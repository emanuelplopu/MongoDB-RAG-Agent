using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using MongoDB.Bson;
using MongoDB.Driver;
using RecallHub.HostAgent.Probes;
using RecallHub.HostAgent.Storage;

namespace RecallHub.HostAgent;

/// <summary>
/// Watchdog for the llama.cpp embedding servers of the compose stack (<c>rag-embedder-gpu</c> /
/// <c>rag-embedder</c> on host port 11090, <c>rag-embedder-query</c> on 11091).
///
/// Seen three times on the client's machine (2026-10-26, -28, -30): after a day or two of uptime
/// the GPU embedder degrades — one CPU core pinned, 8-10 GB RSS, a few hundred tokens/s instead
/// of 13-16k — and every file's embedding step takes an hour instead of seconds while the
/// conversion slots wait on it. <c>docker restart</c> restores full speed within a minute.
///
/// Detection is a direct probe: every <see cref="EmbedderWatchdogOptions.CheckSeconds"/> the
/// agent embeds a fixed ~150-token text through the server's published port and times it. A
/// healthy server answers in tens of milliseconds, at most a few seconds when ingestion batches are
/// queued ahead of the probe; a degraded one needs tens of seconds to minutes (the probe then times
/// out, which counts as slow). The container is restarted through the Docker Engine API when
/// <see cref="EmbedderWatchdogOptions.ConsecutiveChecks"/> consecutive probes were slow over at
/// least <see cref="EmbedderWatchdogOptions.WindowSeconds"/> and the container's mean CPU use over
/// those probes reached <see cref="EmbedderWatchdogOptions.MinCpuCores"/> (the degraded server
/// spins on one core; a server that is merely waiting on something else does not). Nothing is
/// judged during <see cref="EmbedderWatchdogOptions.WarmupSeconds"/> after a container start
/// (model load, CUDA warm-up), a server that reports "Loading model" is left alone, a connection
/// error is "unreachable", not slow (Docker's own restart policy covers crashes), and restarts are
/// rate-limited per container. Every check is reported to <c>host_control</c>
/// (<c>_id: "embedder_watchdog"</c>) for <c>GET /api/v1/system/embedder-watchdog</c>.
///
/// Agent 1.3.0 adds a second, earlier rule. Under the firm's ingestion load the degradation now
/// comes every 1-2 hours, and the probe latency lags the CPU signal: single probes slip between
/// the backend's batches, so a degraded server answers them in 0.8-20 s and only now and then slower
/// than 5 s, while its CPU has been pinned for 20 minutes and the conversion throughput has already
/// dropped by a quarter to a half (2026-11-01 23:30: "healthy, 2930 ms, cpu 0.942" with the embedder
/// at 0.9-1.0 cores for 20 min). Measured signatures: healthy busy 0.34-0.71 cores at 15-30 % GPU,
/// degraded 0.95-1.05 cores at 6-16 % GPU with probes of 0.8-20 s; memory 5-10 GB in both states.
/// The sustained rule (<see cref="RuleSustainedCpu"/>) therefore fires when, over the last
/// <see cref="EmbedderWatchdogOptions.SustainedWindowSeconds"/>, the container's mean CPU reached
/// <see cref="EmbedderWatchdogOptions.SustainedCpuCores"/>, the mean GPU utilisation stayed below
/// <see cref="EmbedderWatchdogOptions.SustainedMaxGpuUtilPct"/> (unknown counts as not busy) and the
/// probes were not all fast (one slower than <see cref="EmbedderWatchdogOptions.SlowMs"/>, or a mean
/// above <see cref="EmbedderWatchdogOptions.SustainedSlowMs"/>). The probes must cover the window, so
/// the rule cannot fire right after a warm-up or a restart; the GPU figure is the mean of the sample
/// collector's nvidia-smi readings between two probes (<see cref="GpuUtilFeed"/>).
/// </summary>
public static class EmbedderWatchdogDecision
{
    public const string StatusId = "embedder_watchdog";

    public const string Healthy = "healthy";
    public const string Slow = "slow";
    public const string Degraded = "degraded";
    public const string Cooldown = "cooldown";
    public const string WarmingUp = "warming_up";
    public const string Unreachable = "unreachable";
    public const string Absent = "absent";
    public const string Unknown = "unknown";

    /// <summary>Rule name: consecutive slow probes with the CPU up (the original rule).</summary>
    public const string RuleSlowProbes = "slow_probes";

    /// <summary>Rule name: sustained CPU with an idle GPU and probes that are not all fast.</summary>
    public const string RuleSustainedCpu = "sustained_cpu";

    /// <summary>
    /// Judges one target from its probe history (oldest first).
    /// </summary>
    /// <param name="checks">Probe results, oldest first; checks before the container start + warm-up and before the last restart are ignored.</param>
    /// <param name="nowUtc">The clock.</param>
    /// <param name="containerStartedAt">When the running container started (null = unknown, no warm-up applied).</param>
    /// <param name="lastRestartUtc">When the watchdog last restarted this container (null = never).</param>
    /// <param name="o">Thresholds.</param>
    public static WatchdogVerdict Decide(
        IReadOnlyList<ProbeCheck> checks,
        DateTime nowUtc,
        DateTime? containerStartedAt,
        DateTime? lastRestartUtc,
        EmbedderWatchdogOptions o)
    {
        if (containerStartedAt is not null && (nowUtc - containerStartedAt.Value).TotalSeconds < o.WarmupSeconds)
        {
            var left = o.WarmupSeconds - (nowUtc - containerStartedAt.Value).TotalSeconds;
            return new WatchdogVerdict(WarmingUp, false, Inv($"container started {FormatAge(nowUtc - containerStartedAt.Value)} ago, judging in {left:F0} s"), 0);
        }

        var judgedFrom = DateTime.MinValue;
        if (containerStartedAt is not null) judgedFrom = containerStartedAt.Value.AddSeconds(o.WarmupSeconds);
        if (lastRestartUtc is not null && lastRestartUtc.Value > judgedFrom) judgedFrom = lastRestartUtc.Value;
        var judged = checks.Where(c => c.At >= judgedFrom).ToList();
        if (judged.Count == 0) return new WatchdogVerdict(Unknown, false, "no probe yet", 0);

        var last = judged[^1];
        if (last.Loading) return new WatchdogVerdict(WarmingUp, false, "server is loading its model", 0);
        if (last.Unreachable) return new WatchdogVerdict(Unreachable, false, last.Error ?? "connection failed", 0);

        // Rule 1: trailing run of consecutive slow probes.
        var run = new List<ProbeCheck>();
        for (var i = judged.Count - 1; i >= 0 && judged[i].IsSlow(o.SlowMs); i--) run.Insert(0, judged[i]);
        var sustained = EvaluateSustained(judged, nowUtc, o);

        if (run.Count == 0)
        {
            if (sustained is { Fires: true })
            {
                return Fire(RuleSustainedCpu, sustained.Summary, 0, sustained.MeanCpuCores, sustained.MeanGpuUtilPct, nowUtc, lastRestartUtc, o);
            }
            return new WatchdogVerdict(Healthy, false, Inv($"{last.LatencyMs:F0} ms"), 0, null, null, sustained?.MeanGpuUtilPct);
        }

        var span = run[^1].At - run[0].At;
        var cpuValues = run.Where(c => c.CpuCores is not null).Select(c => c.CpuCores!.Value).ToList();
        double? meanCpu = cpuValues.Count > 0 ? cpuValues.Average() : null;
        var enough = run.Count >= o.ConsecutiveChecks && span.TotalSeconds >= o.WindowSeconds;
        var cpuOk = o.MinCpuCores <= 0 || (meanCpu is not null && meanCpu.Value >= o.MinCpuCores);
        var summary = Inv($"{run.Count} slow probe(s) over {FormatAge(span)} (last {DescribeLatency(last)}, mean CPU {(meanCpu is null ? "unknown" : meanCpu.Value.ToString("F2", CultureInfo.InvariantCulture))} cores)");

        if (enough && cpuOk)
        {
            return Fire(RuleSlowProbes, summary, run.Count, meanCpu, sustained?.MeanGpuUtilPct, nowUtc, lastRestartUtc, o);
        }
        // Rule 2: sustained CPU with an idle GPU, judged over the longer window regardless of the run.
        if (sustained is { Fires: true })
        {
            return Fire(RuleSustainedCpu, sustained.Summary, run.Count, sustained.MeanCpuCores, sustained.MeanGpuUtilPct, nowUtc, lastRestartUtc, o);
        }
        if (!enough)
        {
            return new WatchdogVerdict(Slow, false, Inv($"{summary}; restart after {o.ConsecutiveChecks} over {o.WindowSeconds} s"), run.Count, meanCpu, null, sustained?.MeanGpuUtilPct);
        }
        return new WatchdogVerdict(Slow, false, Inv($"{summary}; CPU below {o.MinCpuCores:F2} cores, not the known degradation, no restart"), run.Count, meanCpu, null, sustained?.MeanGpuUtilPct);
    }

    /// <summary>A rule has fired: restart now, unless the last restart is too recent (then <see cref="Cooldown"/>).</summary>
    private static WatchdogVerdict Fire(
        string rule, string summary, int slowChecks, double? meanCpu, double? meanGpu,
        DateTime nowUtc, DateTime? lastRestartUtc, EmbedderWatchdogOptions o)
    {
        if (lastRestartUtc is not null && (nowUtc - lastRestartUtc.Value).TotalSeconds < o.MinRestartIntervalSeconds)
        {
            var wait = o.MinRestartIntervalSeconds - (nowUtc - lastRestartUtc.Value).TotalSeconds;
            return new WatchdogVerdict(Cooldown, false, Inv($"{summary}; restarted {FormatAge(nowUtc - lastRestartUtc.Value)} ago, next restart allowed in {wait:F0} s"), slowChecks, meanCpu, rule, meanGpu);
        }
        return new WatchdogVerdict(Degraded, true, summary, slowChecks, meanCpu, rule, meanGpu);
    }

    /// <summary>What the sustained-CPU rule saw over its window; null when it is off or the window is not covered yet.</summary>
    public sealed record SustainedObservation(bool Fires, string Summary, double MeanCpuCores, double? MeanGpuUtilPct, double MeanLatencyMs, bool AnySlow);

    /// <summary>
    /// Evaluates the sustained-CPU rule over the probes of the last <see cref="EmbedderWatchdogOptions.SustainedWindowSeconds"/>.
    /// Only probes that reached the server count (answered or timed out). The probes must span the
    /// window less one check interval, so a history that starts after a warm-up or a restart is not
    /// judged by this rule until the window is full. A window without any CPU figure is not judged.
    /// </summary>
    public static SustainedObservation? EvaluateSustained(IReadOnlyList<ProbeCheck> judged, DateTime nowUtc, EmbedderWatchdogOptions o)
    {
        if (!o.SustainedEnabled || o.SustainedWindowSeconds <= 0) return null;
        var from = nowUtc.AddSeconds(-o.SustainedWindowSeconds);
        var window = judged.Where(c => c.At >= from && (c.Answered || c.TimedOut)).ToList();
        if (window.Count < 2) return null;
        var span = window[^1].At - window[0].At;
        if (span.TotalSeconds < o.SustainedWindowSeconds - o.CheckSeconds) return null;
        var cpuValues = window.Where(c => c.CpuCores is not null).Select(c => c.CpuCores!.Value).ToList();
        if (cpuValues.Count == 0) return null;
        var meanCpu = cpuValues.Average();
        var gpuValues = window.Where(c => c.GpuUtilPct is not null).Select(c => c.GpuUtilPct!.Value).ToList();
        double? meanGpu = gpuValues.Count > 0 ? gpuValues.Average() : null;
        var meanLatency = window.Average(c => c.LatencyMs);
        var anySlow = window.Any(c => c.IsSlow(o.SlowMs));
        var fires = meanCpu >= o.SustainedCpuCores
            && (meanGpu is null || meanGpu.Value < o.SustainedMaxGpuUtilPct)
            && (anySlow || meanLatency > o.SustainedSlowMs);
        var gpuText = meanGpu is null ? "unknown" : meanGpu.Value.ToString("F0", CultureInfo.InvariantCulture) + " %";
        var minS = window.Min(c => c.LatencyMs) / 1000.0;
        var maxS = window.Max(c => c.LatencyMs) / 1000.0;
        var summary = Inv($"sustained: {meanCpu:F2} cores over {FormatAge(span)}, GPU {gpuText}, probes {minS:F1}-{maxS:F1} s");
        return new SustainedObservation(fires, summary, meanCpu, meanGpu, meanLatency, anySlow);
    }

    /// <summary>A deterministic probe text of about <paramref name="words"/> words (one sentence repeated).</summary>
    public static string ProbeText(int words)
    {
        const string sentence =
            "Der Oberste Gerichtshof hat als Revisionsgericht durch den Senatspräsidenten als Vorsitzenden sowie die " +
            "Hofräte als weitere Richter in der Rechtssache der klagenden Partei gegen die beklagte Partei wegen Zahlung " +
            "und Räumung über die Revision der beklagten Partei gegen das Urteil des Landesgerichts als Berufungsgericht " +
            "in nichtöffentlicher Sitzung zu Recht erkannt.";
        var tokens = sentence.Split(' ');
        var parts = new List<string>(Math.Max(1, words));
        while (parts.Count < Math.Max(1, words))
        {
            parts.AddRange(tokens.Take(Math.Max(1, words) - parts.Count));
        }
        return string.Join(' ', parts);
    }

    /// <summary>Reads <c>usage.prompt_tokens</c> and the number of vectors from an embeddings response.</summary>
    public static (int? PromptTokens, int Vectors) ParseEmbeddingsResponse(JsonElement root)
    {
        int? tokens = null;
        if (root.TryGetProperty("usage", out var usage) && usage.ValueKind == JsonValueKind.Object
            && usage.TryGetProperty("prompt_tokens", out var pt) && pt.ValueKind == JsonValueKind.Number)
        {
            tokens = pt.GetInt32();
        }
        var vectors = 0;
        if (root.TryGetProperty("data", out var data) && data.ValueKind == JsonValueKind.Array)
        {
            vectors = data.EnumerateArray().Count(d => d.TryGetProperty("embedding", out var e) && e.ValueKind == JsonValueKind.Array && e.GetArrayLength() > 0);
        }
        return (tokens, vectors);
    }

    private static string DescribeLatency(ProbeCheck c) => c.TimedOut ? Inv($"timeout after {c.LatencyMs:F0} ms") : Inv($"{c.LatencyMs:F0} ms");

    private static string FormatAge(TimeSpan age) =>
        age.TotalSeconds < 90 ? Inv($"{age.TotalSeconds:F0} s") : age.TotalMinutes < 120 ? Inv($"{age.TotalMinutes:F0} min") : Inv($"{age.TotalHours:F1} h");

    /// <summary>Invariant formatting: the strings go to MongoDB and the UI, not to a local console.</summary>
    private static string Inv(FormattableString s) => FormattableString.Invariant(s);
}

/// <summary>
/// One probe of one target, with the container's CPU use since the previous probe and the mean GPU
/// utilisation of the host over the same interval (null when nvidia-smi is not available).
/// </summary>
public sealed record ProbeCheck(
    DateTime At,
    bool Answered,
    double LatencyMs,
    bool TimedOut,
    bool Loading,
    bool Unreachable,
    int? PromptTokens,
    double? CpuCores,
    string? Error,
    double? GpuUtilPct = null)
{
    /// <summary>Slow = answered later than the threshold, or not at all within the probe timeout.</summary>
    public bool IsSlow(int slowMs) => TimedOut || (Answered && LatencyMs >= slowMs);

    /// <summary>Tokens per second of this probe, including any queue wait (null when unknown).</summary>
    public double? TokensPerSecond => Answered && PromptTokens is > 0 && LatencyMs > 0 ? PromptTokens.Value / (LatencyMs / 1000.0) : null;

    public static ProbeCheck Ok(DateTime at, double ms, int? tokens, double? cpu, double? gpu = null) => new(at, true, ms, false, false, false, tokens, cpu, null, gpu);

    public static ProbeCheck Timeout(DateTime at, double ms, double? cpu, double? gpu = null) => new(at, false, ms, true, false, false, null, cpu, "timeout", gpu);

    public static ProbeCheck LoadingModel(DateTime at, double ms, double? cpu, double? gpu = null) => new(at, false, ms, false, true, false, null, cpu, "loading model", gpu);

    public static ProbeCheck Failed(DateTime at, double ms, double? cpu, string error, double? gpu = null) => new(at, false, ms, false, false, true, null, cpu, error, gpu);
}

/// <summary>
/// What the watchdog concluded for one target. <paramref name="Rule"/> names the rule that fired
/// (<see cref="EmbedderWatchdogDecision.RuleSlowProbes"/> or <see cref="EmbedderWatchdogDecision.RuleSustainedCpu"/>)
/// for a <c>degraded</c> or <c>cooldown</c> verdict, null otherwise.
/// </summary>
public sealed record WatchdogVerdict(
    string State, bool Restart, string Reason, int SlowChecks, double? MeanCpuCores = null, string? Rule = null, double? MeanGpuUtilPct = null);

/// <summary>What the Docker engine says about the container behind a target.</summary>
public sealed record ContainerObservation(string Name, string State, DateTime? StartedAt, string? Health, double? CpuCores, long? MemBytes, int? RestartCount);

/// <summary>Probes the embedding servers every minute and restarts a degraded one.</summary>
public sealed class EmbedderWatchdogWorker : BackgroundService, IDisposable
{
    private const int HistoryKeep = 120;
    private static readonly TimeSpan WarnEvery = TimeSpan.FromMinutes(5);

    private readonly ILogger<EmbedderWatchdogWorker> _logger;
    private readonly EmbedderWatchdogOptions _options;
    private readonly IMongoCollection<BsonDocument> _control;
    private readonly DockerEngine _engine;
    private readonly HttpClient _http;
    private readonly string _probeText;
    private readonly GpuUtilFeed? _gpuFeed;
    private readonly Dictionary<string, TargetState> _targets = new();
    private DateTime _lastWarning = DateTime.MinValue;

    public EmbedderWatchdogWorker(ILogger<EmbedderWatchdogWorker> logger, EmbedderWatchdogOptions options, DockerOptions docker, MongoContext mongo, GpuUtilFeed? gpuFeed = null)
    {
        _logger = logger;
        _options = options;
        _gpuFeed = gpuFeed;
        _control = mongo.Database.GetCollection<BsonDocument>(MongoContext.HostControl);
        // A restart blocks until the container is up again; give the engine calls their own, longer budget.
        _engine = new DockerEngine(logger, docker.Pipes, TimeSpan.FromSeconds(120));
        _http = new HttpClient(new SocketsHttpHandler { PooledConnectionLifetime = TimeSpan.FromMinutes(10) })
        {
            Timeout = TimeSpan.FromSeconds(options.ProbeTimeoutSeconds),
        };
        _probeText = EmbedderWatchdogDecision.ProbeText(options.ProbeWords);
        foreach (var t in options.Targets) _targets[t.Name] = new TargetState(t);
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _logger.LogInformation(
            "Embedder watchdog on: probing {Targets} every {Check}s; restart after {N} slow probes (>= {Slow} ms) over {Window}s with CPU >= {Cpu} cores, warm-up {Warmup}s, at most one restart per {Rate}s",
            string.Join(", ", _options.Targets.Select(t => $"{t.Name} ({t.Url} -> {string.Join("|", t.Containers)})")),
            _options.CheckSeconds, _options.ConsecutiveChecks, _options.SlowMs, _options.WindowSeconds, _options.MinCpuCores,
            _options.WarmupSeconds, _options.MinRestartIntervalSeconds);
        if (_options.SustainedEnabled)
        {
            _logger.LogInformation(
                "Embedder watchdog sustained rule on: restart when CPU >= {Cpu} cores over {Window}s with GPU < {Gpu} % and a probe >= {Slow} ms or a mean probe latency > {Mean} ms (GPU readings {Gpu2})",
                _options.SustainedCpuCores, _options.SustainedWindowSeconds, _options.SustainedMaxGpuUtilPct, _options.SlowMs, _options.SustainedSlowMs,
                _gpuFeed is null ? "unavailable, GPU counts as not busy" : "from the sample collector");
        }
        try
        {
            using var timer = new PeriodicTimer(TimeSpan.FromSeconds(_options.CheckSeconds));
            do
            {
                await TickAsync(stoppingToken);
            }
            while (await timer.WaitForNextTickAsync(stoppingToken));
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
        {
            // normal shutdown
        }
    }

    /// <summary>One round: observe, probe and judge every target, restart where due, report.</summary>
    public async Task TickAsync(CancellationToken ct)
    {
        foreach (var state in _targets.Values)
        {
            try
            {
                await CheckTargetAsync(state, ct);
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception ex)
            {
                state.LastError = ex.Message;
                Warn("Embedder watchdog check of {Target} failed: {Message}", state.Target.Name, ex.Message);
            }
        }
        await WriteStatusAsync(ct);
    }

    private async Task CheckTargetAsync(TargetState state, CancellationToken ct)
    {
        var now = DateTime.UtcNow;
        state.LastCheck = now;
        state.LastError = null;
        var container = await ObserveContainerAsync(state, ct);
        state.Container = container;
        if (container is null || container.State != "running")
        {
            state.Verdict = new WatchdogVerdict(EmbedderWatchdogDecision.Absent, false,
                container is null ? "no configured container is running" : $"container {container.Name} is {container.State}", 0);
            state.History.Clear();
            return;
        }

        // Mean GPU utilisation since the previous probe (the same interval as the container's CPU figure).
        var previousProbeAt = state.History.Count > 0 ? state.History[^1].At : now.AddSeconds(-_options.CheckSeconds);
        var gpu = _gpuFeed?.MeanSince(previousProbeAt, now);
        var check = await ProbeAsync(state, now, container.CpuCores, gpu, ct);
        state.History.Add(check);
        if (state.History.Count > HistoryKeep) state.History.RemoveRange(0, state.History.Count - HistoryKeep);

        var verdict = EmbedderWatchdogDecision.Decide(state.History, now, container.StartedAt, state.LastRestart, _options);
        if (verdict.State != state.Verdict?.State)
        {
            _logger.LogInformation("Embedder watchdog: {Target} ({Container}) is {State}: {Reason}", state.Target.Name, container.Name, verdict.State, verdict.Reason);
        }
        state.Verdict = verdict;
        if (!verdict.Restart) return;

        _logger.LogWarning("Embedder watchdog: restarting {Container} ({Target}) by rule {Rule}: {Reason}", container.Name, state.Target.Name, verdict.Rule, verdict.Reason);
        var restartedAt = DateTime.UtcNow;
        try
        {
            await RestartContainerAsync(container.Name, ct);
            state.LastRestart = restartedAt;
            state.LastRestartReason = verdict.Reason;
            state.LastRestartRule = verdict.Rule;
            state.RestartCount++;
            state.History.Clear();
            state.PreviousCounters = null;
            state.Verdict = new WatchdogVerdict(EmbedderWatchdogDecision.WarmingUp, false, FormattableString.Invariant($"restarted at {restartedAt:HH:mm:ss} UTC: {verdict.Reason}"), 0, verdict.MeanCpuCores);
            _logger.LogWarning("Embedder watchdog: {Container} restarted in {Seconds:F1} s", container.Name, (DateTime.UtcNow - restartedAt).TotalSeconds);
        }
        catch (Exception ex) when (ex is HttpRequestException or IOException or TaskCanceledException)
        {
            if (ct.IsCancellationRequested) throw;
            // Count the attempt against the rate limit even when the engine call failed, so a broken
            // pipe cannot turn into a restart storm once it works again.
            state.LastRestart = restartedAt;
            state.LastRestartReason = $"restart failed: {ex.Message}";
            state.LastRestartRule = verdict.Rule;
            state.LastError = $"restart failed: {ex.Message}";
            _engine.Reset();
            _logger.LogError("Embedder watchdog: restart of {Container} failed: {Message}", container.Name, ex.Message);
        }
    }

    /// <summary>Inspects the first running container of the target and reads its CPU use since the previous tick.</summary>
    private async Task<ContainerObservation?> ObserveContainerAsync(TargetState state, CancellationToken ct)
    {
        var client = await _engine.EnsureClientAsync(ct);
        if (client is null)
        {
            state.LastError = "Docker engine pipe unreachable";
            return null;
        }
        using var budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
        budget.CancelAfter(TimeSpan.FromSeconds(15));
        ContainerObservation? firstSeen = null;
        foreach (var name in state.Target.Containers)
        {
            JsonDocument inspect;
            try
            {
                using var response = await client.GetAsync($"containers/{Uri.EscapeDataString(name)}/json", budget.Token);
                if (response.StatusCode == HttpStatusCode.NotFound) continue;
                response.EnsureSuccessStatusCode();
                await using var stream = await response.Content.ReadAsStreamAsync(budget.Token);
                inspect = await JsonDocument.ParseAsync(stream, cancellationToken: budget.Token);
            }
            catch (Exception ex) when (ex is HttpRequestException or IOException or TaskCanceledException or JsonException)
            {
                if (ct.IsCancellationRequested) throw;
                state.LastError = $"inspect {name}: {ex.Message}";
                _engine.Reset();
                return null;
            }
            using (inspect)
            {
                var root = inspect.RootElement;
                var status = root.TryGetProperty("State", out var st) && st.TryGetProperty("Status", out var s) ? s.GetString() ?? "" : "";
                DateTime? startedAt = null;
                string? health = null;
                if (st.ValueKind == JsonValueKind.Object)
                {
                    if (st.TryGetProperty("StartedAt", out var started) && DateTime.TryParse(started.GetString(), null,
                            System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal, out var startedParsed))
                    {
                        startedAt = DateTime.SpecifyKind(startedParsed, DateTimeKind.Utc);
                    }
                    if (st.TryGetProperty("Health", out var h) && h.TryGetProperty("Status", out var hs)) health = hs.GetString();
                }
                int? restarts = root.TryGetProperty("RestartCount", out var rc) && rc.ValueKind == JsonValueKind.Number ? rc.GetInt32() : null;
                var observation = new ContainerObservation(name, status, startedAt, health, null, null, restarts);
                firstSeen ??= observation;
                if (status != "running") continue;

                double? cpu = null;
                long? mem = null;
                try
                {
                    using var stats = await DockerEngine.GetJsonAsync(client, $"containers/{Uri.EscapeDataString(name)}/stats?stream=false&one-shot=true", budget.Token);
                    var counters = DockerProbe.ParseStats(stats.RootElement);
                    if (counters is not null)
                    {
                        if (state.PreviousCounters is { } prev && prev.Name == name)
                        {
                            cpu = DockerProbe.CpuCores(prev.Total, prev.System, counters);
                            if (cpu is not null) cpu = Math.Round(cpu.Value, 3);
                        }
                        state.PreviousCounters = (name, counters.CpuTotalNs, counters.SystemCpuNs);
                        mem = counters.MemBytes;
                    }
                }
                catch (Exception ex) when (ex is HttpRequestException or IOException or TaskCanceledException or JsonException)
                {
                    if (ct.IsCancellationRequested) throw;
                    state.LastError = $"stats {name}: {ex.Message}";
                }
                return observation with { CpuCores = cpu, MemBytes = mem };
            }
        }
        return firstSeen;
    }

    /// <summary>Times one embeddings request to the target's URL.</summary>
    private async Task<ProbeCheck> ProbeAsync(TargetState state, DateTime at, double? cpu, double? gpu, CancellationToken ct)
    {
        var watch = Stopwatch.StartNew();
        try
        {
            using var response = await _http.PostAsJsonAsync(state.Target.Url, new { input = _probeText, model = "embedding" }, ct);
            var ms = watch.Elapsed.TotalMilliseconds;
            if (response.StatusCode == HttpStatusCode.ServiceUnavailable)
            {
                return ProbeCheck.LoadingModel(at, ms, cpu, gpu);
            }
            if (!response.IsSuccessStatusCode)
            {
                var body = await response.Content.ReadAsStringAsync(ct);
                return ProbeCheck.Failed(at, ms, cpu, $"HTTP {(int)response.StatusCode}: {Trim(body)}", gpu);
            }
            await using var stream = await response.Content.ReadAsStreamAsync(ct);
            using var json = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
            var (tokens, vectors) = EmbedderWatchdogDecision.ParseEmbeddingsResponse(json.RootElement);
            if (vectors == 0) return ProbeCheck.Failed(at, ms, cpu, "no embedding in the answer", gpu);
            return ProbeCheck.Ok(at, watch.Elapsed.TotalMilliseconds, tokens, cpu, gpu);
        }
        catch (TaskCanceledException) when (!ct.IsCancellationRequested)
        {
            return ProbeCheck.Timeout(at, watch.Elapsed.TotalMilliseconds, cpu, gpu);
        }
        catch (Exception ex) when (ex is HttpRequestException or IOException or JsonException)
        {
            if (ct.IsCancellationRequested) throw;
            return ProbeCheck.Failed(at, watch.Elapsed.TotalMilliseconds, cpu, ex.Message, gpu);
        }
    }

    private async Task RestartContainerAsync(string name, CancellationToken ct)
    {
        var client = await _engine.EnsureClientAsync(ct) ?? throw new HttpRequestException("Docker engine pipe unreachable");
        using var budget = CancellationTokenSource.CreateLinkedTokenSource(ct);
        budget.CancelAfter(TimeSpan.FromSeconds(120));
        using var response = await client.PostAsync($"containers/{Uri.EscapeDataString(name)}/restart?t=10", content: null, budget.Token);
        if (!response.IsSuccessStatusCode)
        {
            var body = await response.Content.ReadAsStringAsync(budget.Token);
            throw new HttpRequestException($"restart answered HTTP {(int)response.StatusCode}: {Trim(body)}");
        }
    }

    private async Task WriteStatusAsync(CancellationToken ct)
    {
        var targets = new BsonArray();
        foreach (var s in _targets.Values)
        {
            var last = s.History.Count > 0 ? s.History[^1] : null;
            var allowedAt = s.LastRestart?.AddSeconds(_options.MinRestartIntervalSeconds);
            targets.Add(new BsonDocument
            {
                { "name", s.Target.Name },
                { "url", s.Target.Url },
                { "containers", new BsonArray(s.Target.Containers) },
                { "container", (BsonValue?)s.Container?.Name ?? BsonNull.Value },
                { "container_state", (BsonValue?)s.Container?.State ?? BsonNull.Value },
                { "container_health", (BsonValue?)s.Container?.Health ?? BsonNull.Value },
                { "started_at", (BsonValue?)s.Container?.StartedAt ?? BsonNull.Value },
                { "state", s.Verdict?.State ?? EmbedderWatchdogDecision.Unknown },
                { "reason", s.Verdict?.Reason ?? "" },
                { "rule", (BsonValue?)s.Verdict?.Rule ?? BsonNull.Value },
                { "slow_checks", s.Verdict?.SlowChecks ?? 0 },
                { "latency_ms", last is null ? BsonNull.Value : Math.Round(last.LatencyMs, 1) },
                { "prompt_tokens", (BsonValue?)last?.PromptTokens ?? BsonNull.Value },
                { "tokens_per_second", last?.TokensPerSecond is { } tps ? Math.Round(tps, 1) : BsonNull.Value },
                { "cpu_cores", (BsonValue?)s.Container?.CpuCores ?? BsonNull.Value },
                { "gpu_util_pct", (BsonValue?)last?.GpuUtilPct ?? BsonNull.Value },
                { "sustained_gpu_util_pct", (BsonValue?)s.Verdict?.MeanGpuUtilPct ?? BsonNull.Value },
                { "mem_bytes", (BsonValue?)s.Container?.MemBytes ?? BsonNull.Value },
                { "last_check", (BsonValue?)s.LastCheck ?? BsonNull.Value },
                { "last_error", (BsonValue?)(s.LastError ?? last?.Error) ?? BsonNull.Value },
                { "last_restart", (BsonValue?)s.LastRestart ?? BsonNull.Value },
                { "last_restart_reason", (BsonValue?)s.LastRestartReason ?? BsonNull.Value },
                { "last_restart_rule", (BsonValue?)s.LastRestartRule ?? BsonNull.Value },
                { "restart_count", s.RestartCount },
                { "next_restart_allowed_at", (BsonValue?)allowedAt ?? BsonNull.Value },
            });
        }
        var status = new BsonDocument
        {
            { "enabled", _options.Enabled },
            { "check_seconds", _options.CheckSeconds },
            { "thresholds", new BsonDocument
                {
                    { "slow_ms", _options.SlowMs },
                    { "probe_timeout_seconds", _options.ProbeTimeoutSeconds },
                    { "consecutive_checks", _options.ConsecutiveChecks },
                    { "window_seconds", _options.WindowSeconds },
                    { "min_cpu_cores", _options.MinCpuCores },
                    { "warmup_seconds", _options.WarmupSeconds },
                    { "min_restart_interval_seconds", _options.MinRestartIntervalSeconds },
                    { "probe_words", _options.ProbeWords },
                    { "sustained_enabled", _options.SustainedEnabled },
                    { "sustained_window_seconds", _options.SustainedWindowSeconds },
                    { "sustained_cpu_cores", _options.SustainedCpuCores },
                    { "sustained_max_gpu_util_pct", _options.SustainedMaxGpuUtilPct },
                    { "sustained_slow_ms", _options.SustainedSlowMs },
                } },
            { "targets", targets },
            { "agent_version", SampleCollector.AgentVersion },
            { "host", Environment.MachineName },
            { "updated_at", DateTime.UtcNow },
        };
        try
        {
            await _control.UpdateOneAsync(
                Builders<BsonDocument>.Filter.Eq("_id", EmbedderWatchdogDecision.StatusId),
                new BsonDocument("$set", status),
                new UpdateOptions { IsUpsert = true },
                ct);
        }
        catch (Exception ex) when (ex is MongoException or TimeoutException)
        {
            Warn("Embedder watchdog status write failed: {Message}", ex.Message);
        }
    }

    private void Warn(string message, params object[] args)
    {
        if (DateTime.UtcNow - _lastWarning <= WarnEvery) return;
        _lastWarning = DateTime.UtcNow;
#pragma warning disable CA2254 // template comes from the two call sites above
        _logger.LogWarning(message, args);
#pragma warning restore CA2254
    }

    private static string Trim(string body) => body.Length > 200 ? body[..200] + "…" : body;

    public override void Dispose()
    {
        _http.Dispose();
        _engine.Dispose();
        base.Dispose();
    }

    private sealed class TargetState
    {
        public TargetState(EmbedderWatchdogTarget target) => Target = target;

        public EmbedderWatchdogTarget Target { get; }
        public List<ProbeCheck> History { get; } = new();
        public ContainerObservation? Container { get; set; }
        public WatchdogVerdict? Verdict { get; set; }
        public (string Name, ulong Total, ulong System)? PreviousCounters { get; set; }
        public DateTime? LastCheck { get; set; }
        public string? LastError { get; set; }
        public DateTime? LastRestart { get; set; }
        public string? LastRestartReason { get; set; }
        public string? LastRestartRule { get; set; }
        public int RestartCount { get; set; }
    }
}
