using System.Diagnostics;
using System.Text.RegularExpressions;
using MongoDB.Bson;
using MongoDB.Driver;
using RecallHub.HostAgent.Storage;

namespace RecallHub.HostAgent;

/// <summary>
/// Applies the CPU clock cap the backends ask for. Every tenant backend publishes its desire into
/// <c>host_control</c> (<c>_id: "power_plan:&lt;tenant&gt;"</c>, <c>desired: capped|normal</c>,
/// <c>updated_at</c>); the agent caps the active Windows power plan (maximum processor state 99 %,
/// boost mode disabled) when any fresh desire says <c>capped</c> and restores it (100 %, boost
/// aggressive) otherwise, writing what it did to <c>_id: "power_plan"</c>. A desire older than
/// <see cref="PowerPlanOptions.DesiredFreshSeconds"/> counts as normal, so a stopped backend never
/// leaves the machine capped. The service runs as LocalSystem, so no UAC prompt is involved.
/// </summary>
public static class PowerPlanDecision
{
    public const string Capped = "capped";
    public const string Normal = "normal";
    public const string DesiredIdPrefix = "power_plan:";
    public const string StatusId = "power_plan";

    /// <summary>Capped when any fresh tenant desire says so; the reasons name the deciding documents.</summary>
    public static (string Desired, List<string> Reasons) Decide(IEnumerable<BsonDocument> desires, DateTime nowUtc, int freshSeconds)
    {
        var reasons = new List<string>();
        var capped = false;
        foreach (var doc in desires)
        {
            var id = doc.GetValue("_id", BsonNull.Value).ToString() ?? "";
            if (!id.StartsWith(DesiredIdPrefix, StringComparison.Ordinal)) continue;
            var tenant = doc.GetValue("tenant", BsonNull.Value).IsBsonNull ? id[DesiredIdPrefix.Length..] : doc["tenant"].AsString;
            var desired = doc.GetValue("desired", BsonNull.Value).IsBsonNull ? Normal : doc["desired"].AsString;
            var updated = doc.GetValue("updated_at", BsonNull.Value);
            var age = updated.IsValidDateTime ? (nowUtc - updated.ToUniversalTime()).TotalSeconds : double.PositiveInfinity;
            var fresh = age <= freshSeconds;
            if (desired == Capped && fresh)
            {
                capped = true;
                reasons.Add($"{tenant}: capped ({doc.GetValue("reason", "").ToString()})");
            }
            else if (desired == Capped)
            {
                reasons.Add($"{tenant}: capped but stale ({age:F0} s), ignored");
            }
        }
        return (capped ? Capped : Normal, reasons);
    }
}

/// <summary>The processor settings of the active plan as powercfg reports them.</summary>
public sealed record PowercfgState(int? MaxStateAc, int? MaxStateDc, int? BoostModeAc, int? BoostModeDc)
{
    /// <summary>Capped = maximum processor state below 100 % and the boost mode disabled (AC).</summary>
    public bool IsCapped => MaxStateAc is <= 99 && BoostModeAc == 0;

    public string State => IsCapped ? PowerPlanDecision.Capped : MaxStateAc is null ? "unknown" : PowerPlanDecision.Normal;

    private static readonly Regex AcIndex = new(@"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", RegexOptions.Compiled);
    private static readonly Regex DcIndex = new(@"Current DC Power Setting Index:\s*0x([0-9a-fA-F]+)", RegexOptions.Compiled);

    /// <summary>Parses one <c>powercfg /query</c> output for a single setting into (AC, DC) indexes.</summary>
    public static (int? Ac, int? Dc) ParseIndexes(string queryOutput)
    {
        var ac = AcIndex.Match(queryOutput);
        var dc = DcIndex.Match(queryOutput);
        return (ac.Success ? Convert.ToInt32(ac.Groups[1].Value, 16) : null,
                dc.Success ? Convert.ToInt32(dc.Groups[1].Value, 16) : null);
    }
}

/// <summary>Reads and changes the active power plan (abstracted for tests).</summary>
public interface IPowerPlanApplier
{
    Task<PowercfgState> ReadAsync(CancellationToken ct);

    Task ApplyAsync(bool capped, CancellationToken ct);
}

/// <summary>powercfg.exe on the host: the same four settings as scripts/Set-CpuBoostCap.ps1.</summary>
public sealed class PowercfgApplier : IPowerPlanApplier
{
    public const string MaxProcessorState = "bc5038f7-23e0-4960-96da-33abaf5935ec";
    public const string PerfBoostMode = "be337238-0d82-4146-a960-4f3749d470c7";

    private readonly ILogger _logger;
    private bool _unhidden;

    public PowercfgApplier(ILogger logger)
    {
        _logger = logger;
    }

    public async Task<PowercfgState> ReadAsync(CancellationToken ct)
    {
        await UnhideBoostModeAsync(ct);
        var max = PowercfgState.ParseIndexes(await RunAsync($"/query SCHEME_CURRENT SUB_PROCESSOR {MaxProcessorState}", ct));
        var boost = PowercfgState.ParseIndexes(await RunAsync($"/query SCHEME_CURRENT SUB_PROCESSOR {PerfBoostMode}", ct));
        return new PowercfgState(max.Ac, max.Dc, boost.Ac, boost.Dc);
    }

    public async Task ApplyAsync(bool capped, CancellationToken ct)
    {
        var maxState = capped ? 99 : 100;
        var boostMode = capped ? 0 : 2;
        foreach (var scope in new[] { "/setacvalueindex", "/setdcvalueindex" })
        {
            await RunAsync($"{scope} SCHEME_CURRENT SUB_PROCESSOR {MaxProcessorState} {maxState}", ct);
            await RunAsync($"{scope} SCHEME_CURRENT SUB_PROCESSOR {PerfBoostMode} {boostMode}", ct);
        }
        await RunAsync("/setactive SCHEME_CURRENT", ct);
    }

    private async Task UnhideBoostModeAsync(CancellationToken ct)
    {
        if (_unhidden) return;
        // The boost-mode setting is hidden by default; unhidden, powercfg /query reports it.
        await RunAsync($"-attributes SUB_PROCESSOR {PerfBoostMode} -ATTRIB_HIDE", ct);
        _unhidden = true;
    }

    private async Task<string> RunAsync(string arguments, CancellationToken ct)
    {
        var psi = new ProcessStartInfo("powercfg", arguments)
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        using var process = Process.Start(psi) ?? throw new InvalidOperationException("powercfg did not start");
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(15));
        var stdout = process.StandardOutput.ReadToEndAsync(timeout.Token);
        var stderr = process.StandardError.ReadToEndAsync(timeout.Token);
        await process.WaitForExitAsync(timeout.Token);
        var output = await stdout;
        if (process.ExitCode != 0)
        {
            throw new InvalidOperationException($"powercfg {arguments} failed ({process.ExitCode}): {(await stderr).Trim()} {output.Trim()}".Trim());
        }
        return output;
    }
}

/// <summary>Checks the desired power plan every few seconds and applies it.</summary>
public sealed class PowerPlanWorker : BackgroundService
{
    private static readonly TimeSpan WarnEvery = TimeSpan.FromMinutes(5);

    private readonly ILogger<PowerPlanWorker> _logger;
    private readonly PowerPlanOptions _options;
    private readonly IMongoCollection<BsonDocument> _control;
    private readonly IPowerPlanApplier _applier;
    private string? _lastDesired;
    private string? _lastState;
    private DateTime? _appliedAt;
    private DateTime _lastWarning = DateTime.MinValue;

    public PowerPlanWorker(ILogger<PowerPlanWorker> logger, PowerPlanOptions options, MongoContext mongo, IPowerPlanApplier applier)
    {
        _logger = logger;
        _options = options;
        _control = mongo.Database.GetCollection<BsonDocument>(MongoContext.HostControl);
        _applier = applier;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _logger.LogInformation("Power plan control on: checking host_control every {Seconds}s (desires older than {Fresh}s count as normal)",
            _options.CheckSeconds, _options.DesiredFreshSeconds);
        try
        {
            using var timer = new PeriodicTimer(TimeSpan.FromSeconds(Math.Clamp(_options.CheckSeconds, 5, 300)));
            do
            {
                await TickAsync(stoppingToken);
            }
            while (await timer.WaitForNextTickAsync(stoppingToken));
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
        {
            // normal shutdown; the plan stays as it is (the next agent start re-evaluates)
        }
    }

    /// <summary>One check: read the desires, decide, apply when the plan differs, report.</summary>
    public async Task TickAsync(CancellationToken ct)
    {
        string? error = null;
        string desired = PowerPlanDecision.Normal;
        List<string> reasons = new();
        PowercfgState? state = null;
        try
        {
            var filter = Builders<BsonDocument>.Filter.Regex("_id", new BsonRegularExpression("^" + Regex.Escape(PowerPlanDecision.DesiredIdPrefix)));
            var desires = await _control.Find(filter).ToListAsync(ct);
            (desired, reasons) = PowerPlanDecision.Decide(desires, DateTime.UtcNow, _options.DesiredFreshSeconds);
            state = await _applier.ReadAsync(ct);
            var wantCapped = desired == PowerPlanDecision.Capped;
            if (state.MaxStateAc is not null && state.IsCapped != wantCapped)
            {
                await _applier.ApplyAsync(wantCapped, ct);
                state = await _applier.ReadAsync(ct);
                _appliedAt = DateTime.UtcNow;
                _logger.LogInformation("Power plan set to {State} (max processor state {Max}%, boost mode {Boost}); {Reasons}",
                    state.State, state.MaxStateAc, state.BoostModeAc, reasons.Count > 0 ? string.Join("; ", reasons) : "no tenant asks for a cap");
            }
            else if (desired != _lastDesired || state.State != _lastState)
            {
                _logger.LogInformation("Power plan desired {Desired}, plan is {State}; {Reasons}",
                    desired, state.State, reasons.Count > 0 ? string.Join("; ", reasons) : "no tenant asks for a cap");
            }
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex)
        {
            error = ex.Message;
            if (DateTime.UtcNow - _lastWarning > WarnEvery)
            {
                _lastWarning = DateTime.UtcNow;
                _logger.LogWarning("Power plan check failed: {Message}", ex.Message);
            }
        }
        _lastDesired = desired;
        _lastState = state?.State;
        await WriteStatusAsync(desired, reasons, state, error, ct);
    }

    private async Task WriteStatusAsync(string desired, List<string> reasons, PowercfgState? state, string? error, CancellationToken ct)
    {
        var status = new BsonDocument
        {
            { "state", state?.State ?? "unknown" },
            { "desired", desired },
            { "reasons", new BsonArray(reasons) },
            { "max_state_ac", (BsonValue?)state?.MaxStateAc ?? BsonNull.Value },
            { "max_state_dc", (BsonValue?)state?.MaxStateDc ?? BsonNull.Value },
            { "boost_mode_ac", (BsonValue?)state?.BoostModeAc ?? BsonNull.Value },
            { "boost_mode_dc", (BsonValue?)state?.BoostModeDc ?? BsonNull.Value },
            { "applied_at", (BsonValue?)_appliedAt ?? BsonNull.Value },
            { "error", (BsonValue?)error ?? BsonNull.Value },
            { "agent_version", SampleCollector.AgentVersion },
            { "host", Environment.MachineName },
            { "updated_at", DateTime.UtcNow },
        };
        try
        {
            await _control.UpdateOneAsync(
                Builders<BsonDocument>.Filter.Eq("_id", PowerPlanDecision.StatusId),
                new BsonDocument("$set", status),
                new UpdateOptions { IsUpsert = true },
                ct);
        }
        catch (Exception ex) when (ex is MongoException or TimeoutException)
        {
            if (DateTime.UtcNow - _lastWarning > WarnEvery)
            {
                _lastWarning = DateTime.UtcNow;
                _logger.LogWarning("Power plan status write failed: {Message}", ex.Message);
            }
        }
    }
}
