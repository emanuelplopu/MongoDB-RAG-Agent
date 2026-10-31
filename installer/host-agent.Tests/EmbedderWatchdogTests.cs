using System.Text.Json;
using Xunit;

namespace RecallHub.HostAgent.Tests;

public class EmbedderWatchdogDecisionTests
{
    private static readonly DateTime Now = new(2026, 10, 30, 14, 0, 0, DateTimeKind.Utc);

    /// <summary>The defaults: 5 slow probes over 240 s, CPU >= 0.5 cores, 300 s warm-up, one restart per 30 min.</summary>
    private static EmbedderWatchdogOptions Options() => new EmbedderWatchdogOptions().Normalize();

    /// <summary>A probe every minute ending now; <paramref name="latencies"/> oldest first, null = timeout.</summary>
    private static List<ProbeCheck> Minutely(double? cpu, params double?[] latencies)
    {
        var checks = new List<ProbeCheck>();
        for (var i = 0; i < latencies.Length; i++)
        {
            var at = Now.AddMinutes(-(latencies.Length - 1 - i));
            checks.Add(latencies[i] is { } ms ? ProbeCheck.Ok(at, ms, 150, cpu) : ProbeCheck.Timeout(at, 30000, cpu));
        }
        return checks;
    }

    private static readonly DateTime StartedLongAgo = Now.AddDays(-2);

    [Fact]
    public void NoProbe_IsUnknown()
    {
        var v = EmbedderWatchdogDecision.Decide(Array.Empty<ProbeCheck>(), Now, StartedLongAgo, null, Options());
        Assert.Equal("unknown", v.State);
        Assert.False(v.Restart);
    }

    [Fact]
    public void FastProbes_AreHealthy()
    {
        var v = EmbedderWatchdogDecision.Decide(Minutely(0.9, 25, 31, 18, 40, 22), Now, StartedLongAgo, null, Options());
        Assert.Equal("healthy", v.State);
        Assert.False(v.Restart);
        Assert.Equal(0, v.SlowChecks);
    }

    [Fact]
    public void FiveSlowProbesOverFourMinutes_WithTheCpuPinned_Restart()
    {
        var v = EmbedderWatchdogDecision.Decide(Minutely(0.98, 20, 8000, 12000, null, null, 25000), Now, StartedLongAgo, null, Options());
        Assert.Equal("degraded", v.State);
        Assert.True(v.Restart);
        Assert.Equal(5, v.SlowChecks);
        Assert.Equal(0.98, v.MeanCpuCores!.Value, 3);
        Assert.Contains("5 slow probe(s) over 4 min", v.Reason);
    }

    [Fact]
    public void TimeoutsAlone_CountAsSlow()
    {
        var v = EmbedderWatchdogDecision.Decide(Minutely(1.0, null, null, null, null, null), Now, StartedLongAgo, null, Options());
        Assert.True(v.Restart);
        Assert.Contains("timeout after 30000 ms", v.Reason);
    }

    [Fact]
    public void FourSlowProbes_AreNotEnough()
    {
        var v = EmbedderWatchdogDecision.Decide(Minutely(1.0, 20, 9000, 9000, 9000, 9000), Now, StartedLongAgo, null, Options());
        Assert.Equal("slow", v.State);
        Assert.False(v.Restart);
        Assert.Equal(4, v.SlowChecks);
        Assert.Contains("restart after 5 over 240 s", v.Reason);
    }

    [Fact]
    public void AFastProbeInBetween_BreaksTheRun()
    {
        // One answer within the threshold resets the count: a queue spike, not the degradation.
        var v = EmbedderWatchdogDecision.Decide(Minutely(1.0, 9000, 9000, 9000, 120, 9000, 9000), Now, StartedLongAgo, null, Options());
        Assert.Equal("slow", v.State);
        Assert.False(v.Restart);
        Assert.Equal(2, v.SlowChecks);
    }

    [Fact]
    public void FiveSlowProbesInUnderTheWindow_AreNotEnough()
    {
        // Five probes 30 s apart span 2 min; the rule wants 4 min, so a burst cannot trigger it.
        var checks = Enumerable.Range(0, 5).Select(i => ProbeCheck.Ok(Now.AddSeconds(-30 * (4 - i)), 9000, 150, 1.0)).ToList();
        var v = EmbedderWatchdogDecision.Decide(checks, Now, StartedLongAgo, null, Options());
        Assert.Equal("slow", v.State);
        Assert.False(v.Restart);
    }

    [Fact]
    public void SlowWithALowCpu_IsNotTheKnownDegradation()
    {
        var v = EmbedderWatchdogDecision.Decide(Minutely(0.1, 9000, 9000, 9000, 9000, 9000), Now, StartedLongAgo, null, Options());
        Assert.Equal("slow", v.State);
        Assert.False(v.Restart);
        Assert.Contains("CPU below 0.50 cores", v.Reason);
    }

    [Fact]
    public void SlowWithUnknownCpu_DoesNotRestart_UnlessCpuIsIgnored()
    {
        var checks = Minutely(null, 9000, 9000, 9000, 9000, 9000);
        Assert.False(EmbedderWatchdogDecision.Decide(checks, Now, StartedLongAgo, null, Options()).Restart);
        var o = Options();
        o.MinCpuCores = 0;
        Assert.True(EmbedderWatchdogDecision.Decide(checks, Now, StartedLongAgo, null, o).Restart);
    }

    [Fact]
    public void MeanCpuOverTheRun_IsWhatCounts()
    {
        // Three of five probes saw the core pinned, two saw it idle: the mean 0.6 still passes 0.5.
        var checks = new List<ProbeCheck>();
        var cpus = new double?[] { 1.0, 1.0, 1.0, 0.0, 0.0 };
        for (var i = 0; i < 5; i++) checks.Add(ProbeCheck.Ok(Now.AddMinutes(-(4 - i)), 9000, 150, cpus[i]));
        var v = EmbedderWatchdogDecision.Decide(checks, Now, StartedLongAgo, null, Options());
        Assert.True(v.Restart);
        Assert.Equal(0.6, v.MeanCpuCores!.Value, 3);
    }

    [Fact]
    public void DuringWarmup_NothingIsJudged()
    {
        var v = EmbedderWatchdogDecision.Decide(Minutely(1.0, null, null, null, null, null), Now, Now.AddSeconds(-200), null, Options());
        Assert.Equal("warming_up", v.State);
        Assert.False(v.Restart);
        Assert.Contains("judging in 100 s", v.Reason);
    }

    [Fact]
    public void ProbesFromBeforeTheWarmupEnded_AreIgnored()
    {
        // Started 6 min ago, warm-up 5 min: only the probes of the last minute count, so two slow probes are not five.
        var v = EmbedderWatchdogDecision.Decide(Minutely(1.0, null, null, null, null, null, null, null), Now, Now.AddMinutes(-6), null, Options());
        Assert.Equal("slow", v.State);
        Assert.False(v.Restart);
        Assert.Equal(2, v.SlowChecks);
    }

    [Fact]
    public void ServerLoadingItsModel_IsWarmingUp()
    {
        var checks = Minutely(1.0, 9000, 9000, 9000, 9000);
        checks.Add(ProbeCheck.LoadingModel(Now, 12, 1.0));
        var v = EmbedderWatchdogDecision.Decide(checks, Now, StartedLongAgo, null, Options());
        Assert.Equal("warming_up", v.State);
        Assert.False(v.Restart);
    }

    [Fact]
    public void ConnectionFailure_IsUnreachable_NotSlow()
    {
        var checks = Minutely(1.0, 9000, 9000, 9000, 9000);
        checks.Add(ProbeCheck.Failed(Now, 3, 1.0, "Connection refused"));
        var v = EmbedderWatchdogDecision.Decide(checks, Now, StartedLongAgo, null, Options());
        Assert.Equal("unreachable", v.State);
        Assert.False(v.Restart);
        Assert.Equal("Connection refused", v.Reason);
    }

    [Fact]
    public void ARecentRestart_RateLimitsTheNextOne()
    {
        var checks = Minutely(1.0, null, null, null, null, null);
        var v = EmbedderWatchdogDecision.Decide(checks, Now, StartedLongAgo, Now.AddMinutes(-20), Options());
        Assert.Equal("cooldown", v.State);
        Assert.False(v.Restart);
        Assert.Contains("next restart allowed in 600 s", v.Reason);
        Assert.Equal(5, v.SlowChecks);
    }

    [Fact]
    public void AfterTheRateLimit_ARestartIsAllowedAgain()
    {
        var checks = Minutely(1.0, null, null, null, null, null);
        var v = EmbedderWatchdogDecision.Decide(checks, Now, StartedLongAgo, Now.AddMinutes(-31), Options());
        Assert.Equal("degraded", v.State);
        Assert.True(v.Restart);
    }

    [Fact]
    public void ProbesBeforeTheLastRestart_AreIgnored()
    {
        // Restarted just under 2 min ago (container start unknown): the two probes since then are not five.
        var checks = Minutely(1.0, null, null, null, null, null, null);
        var v = EmbedderWatchdogDecision.Decide(checks, Now, null, Now.AddMinutes(-2).AddSeconds(1), Options());
        Assert.False(v.Restart);
        Assert.Equal(2, v.SlowChecks);
    }

    [Fact]
    public void ThresholdIsInclusive_AndConfigurable()
    {
        var o = Options();
        o.SlowMs = 2000;
        o.ConsecutiveChecks = 2;
        o.WindowSeconds = 60;
        var v = EmbedderWatchdogDecision.Decide(Minutely(1.0, 100, 2000, 2000), Now, StartedLongAgo, null, o);
        Assert.True(v.Restart);
        Assert.False(EmbedderWatchdogDecision.Decide(Minutely(1.0, 100, 1999, 1999), Now, StartedLongAgo, null, o).Restart);
    }
}

public class ProbeCheckTests
{
    [Fact]
    public void TokensPerSecond_FromPromptTokensAndLatency()
    {
        var ok = ProbeCheck.Ok(DateTime.UtcNow, 10, 150, null);
        Assert.Equal(15000, ok.TokensPerSecond!.Value, 1);
        Assert.Null(ProbeCheck.Timeout(DateTime.UtcNow, 30000, null).TokensPerSecond);
        Assert.Null(ProbeCheck.Ok(DateTime.UtcNow, 10, null, null).TokensPerSecond);
    }

    [Fact]
    public void ProbeText_HasTheRequestedNumberOfWords()
    {
        Assert.Equal(120, EmbedderWatchdogDecision.ProbeText(120).Split(' ').Length);
        Assert.Single(EmbedderWatchdogDecision.ProbeText(1).Split(' '));
        Assert.Equal(EmbedderWatchdogDecision.ProbeText(50), EmbedderWatchdogDecision.ProbeText(50)); // deterministic
    }

    [Fact]
    public void ParseEmbeddingsResponse_ReadsUsageAndVectors()
    {
        using var json = JsonDocument.Parse("""{"data":[{"embedding":[0.1,0.2],"index":0}],"usage":{"prompt_tokens":153,"total_tokens":153}}""");
        var (tokens, vectors) = EmbedderWatchdogDecision.ParseEmbeddingsResponse(json.RootElement);
        Assert.Equal(153, tokens);
        Assert.Equal(1, vectors);
    }

    [Fact]
    public void ParseEmbeddingsResponse_WithoutData_HasNoVector()
    {
        using var json = JsonDocument.Parse("""{"error":{"code":500,"message":"x"}}""");
        var (tokens, vectors) = EmbedderWatchdogDecision.ParseEmbeddingsResponse(json.RootElement);
        Assert.Null(tokens);
        Assert.Equal(0, vectors);
    }
}

public class EmbedderWatchdogOptionsTests
{
    [Fact]
    public void Normalize_FillsTheComposeStackTargets()
    {
        var o = new EmbedderWatchdogOptions().Normalize();
        Assert.Collection(o.Targets,
            t =>
            {
                Assert.Equal("embedder", t.Name);
                Assert.Equal("http://127.0.0.1:11090/v1/embeddings", t.Url);
                Assert.Equal(new[] { "rag-embedder-gpu", "rag-embedder" }, t.Containers);
            },
            t =>
            {
                Assert.Equal("embedder-query", t.Name);
                Assert.Equal("http://127.0.0.1:11091/v1/embeddings", t.Url);
                Assert.Equal(new[] { "rag-embedder-query" }, t.Containers);
            });
    }

    [Fact]
    public void Normalize_KeepsConfiguredTargets_AndClampsThresholds()
    {
        var o = new EmbedderWatchdogOptions { CheckSeconds = 1, ConsecutiveChecks = 0, SlowMs = 10 };
        o.Targets.Add(new EmbedderWatchdogTarget { Name = "x", Url = "http://127.0.0.1:1/v1/embeddings", Containers = { "c" } });
        o.Normalize();
        Assert.Single(o.Targets);
        Assert.Equal(10, o.CheckSeconds);
        Assert.Equal(1, o.ConsecutiveChecks);
        Assert.Equal(100, o.SlowMs);
    }
}
