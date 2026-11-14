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

/// <summary>
/// The sustained-CPU rule of agent 1.3.0: CPU >= 0.85 cores over 10 min with the GPU below 40 % and
/// probes that are not all fast. Signatures measured on the client's machine 2026-11-01: healthy busy
/// 0.34-0.71 cores at 15-30 % GPU with probes of 0.2-6 s; degraded 0.95-1.05 cores at 6-16 % GPU with
/// probes of 0.8-20 s, only now and then above the 5 s threshold.
/// </summary>
public class EmbedderWatchdogSustainedRuleTests
{
    private static readonly DateTime Now = new(2026, 11, 1, 23, 30, 0, DateTimeKind.Utc);
    private static readonly DateTime StartedLongAgo = Now.AddHours(-3);

    private static EmbedderWatchdogOptions Options() => new EmbedderWatchdogOptions().Normalize();

    /// <summary>Eleven probes a minute apart ending now (the 10 min window covered), latencies cycled over the list.</summary>
    private static List<ProbeCheck> Window(double? cpu, double? gpu, params double[] latencies) => WindowOf(11, cpu, gpu, latencies);

    private static List<ProbeCheck> WindowOf(int count, double? cpu, double? gpu, params double[] latencies)
    {
        var checks = new List<ProbeCheck>();
        for (var i = 0; i < count; i++)
        {
            checks.Add(ProbeCheck.Ok(Now.AddMinutes(-(count - 1 - i)), latencies[i % latencies.Length], 150, cpu, gpu));
        }
        return checks;
    }

    /// <summary>The degraded signature: a core pinned, GPU 13 %, probes 0.8-8.9 s, slow only now and then.</summary>
    private static readonly double[] DegradedProbes = { 800, 2930, 1500, 8900, 1200, 900, 3400, 1100, 6200, 2100, 1800 };

    [Fact]
    public void DegradedSignature_RestartsWithoutFiveSlowProbesInARow()
    {
        var v = EmbedderWatchdogDecision.Decide(Window(0.96, 13, DegradedProbes), Now, StartedLongAgo, null, Options());
        Assert.Equal("degraded", v.State);
        Assert.True(v.Restart);
        Assert.Equal(EmbedderWatchdogDecision.RuleSustainedCpu, v.Rule);
        Assert.Equal(0.96, v.MeanCpuCores!.Value, 3);
        Assert.Equal(13, v.MeanGpuUtilPct!.Value, 3);
        Assert.Equal("sustained: 0.96 cores over 10 min, GPU 13 %, probes 0.8-8.9 s", v.Reason);
        Assert.Equal(0, v.SlowChecks); // the last probe was fast; the consecutive rule saw nothing
    }

    [Fact]
    public void WhatTheAgentReportedAsHealthy_IsNowDegraded()
    {
        // 2026-11-01 23:30: "healthy, 2930 ms, cpu 0.942" after 20 min at 0.9-1.0 cores; no probe over 5 s, mean 2.9 s.
        var v = EmbedderWatchdogDecision.Decide(Window(0.942, 12, 2930, 3100, 2700, 2850), Now, StartedLongAgo, null, Options());
        Assert.True(v.Restart);
        Assert.Equal(EmbedderWatchdogDecision.RuleSustainedCpu, v.Rule);
    }

    [Fact]
    public void HealthyBusy_DoesNotFire()
    {
        // 0.6 cores at 25 % GPU with one 6 s probe: ingestion batches ahead of the probe, not the degradation.
        var v = EmbedderWatchdogDecision.Decide(Window(0.6, 25, 200, 6000, 400, 1500, 300), Now, StartedLongAgo, null, Options());
        Assert.False(v.Restart);
        Assert.Null(v.Rule);
        Assert.Equal("healthy", v.State);
    }

    [Fact]
    public void ABusyGpu_MeansRealWork_NoRestart()
    {
        // The core is up but so is the GPU: the server is embedding, not spinning.
        var v = EmbedderWatchdogDecision.Decide(Window(0.96, 55, DegradedProbes), Now, StartedLongAgo, null, Options());
        Assert.False(v.Restart);
        Assert.Null(v.Rule);
    }

    [Fact]
    public void GpuUnknown_CountsAsNotBusy()
    {
        var v = EmbedderWatchdogDecision.Decide(Window(0.96, null, DegradedProbes), Now, StartedLongAgo, null, Options());
        Assert.True(v.Restart);
        Assert.Equal(EmbedderWatchdogDecision.RuleSustainedCpu, v.Rule);
        Assert.Null(v.MeanGpuUtilPct);
        Assert.Contains("GPU unknown", v.Reason);
    }

    [Fact]
    public void FastProbesThroughout_DoNotFire_EvenWithTheCpuUp()
    {
        // A core pinned at an idle GPU but every probe answered in under a second: not the known failure.
        var v = EmbedderWatchdogDecision.Decide(Window(0.98, 10, 120, 300, 800, 450), Now, StartedLongAgo, null, Options());
        Assert.False(v.Restart);
        Assert.Equal("healthy", v.State);
    }

    [Fact]
    public void CpuBelowTheThreshold_DoesNotFire()
    {
        var v = EmbedderWatchdogDecision.Decide(Window(0.8, 10, DegradedProbes), Now, StartedLongAgo, null, Options());
        Assert.False(v.Restart);
        Assert.Null(v.Rule);
    }

    [Fact]
    public void CpuUnknownThroughout_IsNotJudged()
    {
        var v = EmbedderWatchdogDecision.Decide(Window(null, 10, DegradedProbes), Now, StartedLongAgo, null, Options());
        Assert.False(v.Restart);
        Assert.Null(EmbedderWatchdogDecision.EvaluateSustained(Window(null, 10, DegradedProbes), Now, Options()));
    }

    [Fact]
    public void DuringWarmup_NothingIsJudged()
    {
        var v = EmbedderWatchdogDecision.Decide(Window(0.96, 13, DegradedProbes), Now, Now.AddSeconds(-120), null, Options());
        Assert.Equal("warming_up", v.State);
        Assert.False(v.Restart);
    }

    [Fact]
    public void RightAfterTheWarmup_TheWindowIsNotCoveredYet()
    {
        // Started 8 min ago, warm-up 5 min: only three probes count, the 10 min window is not covered.
        var v = EmbedderWatchdogDecision.Decide(Window(0.96, 13, DegradedProbes), Now, Now.AddMinutes(-8), null, Options());
        Assert.False(v.Restart);
        Assert.Null(v.Rule);
    }

    [Fact]
    public void DuringCooldown_TheRuleReportsButDoesNotRestart()
    {
        var v = EmbedderWatchdogDecision.Decide(Window(0.96, 13, DegradedProbes), Now, StartedLongAgo, Now.AddMinutes(-20), Options());
        Assert.Equal("cooldown", v.State);
        Assert.False(v.Restart);
        Assert.Equal(EmbedderWatchdogDecision.RuleSustainedCpu, v.Rule);
        Assert.Contains("next restart allowed in 600 s", v.Reason);
    }

    [Fact]
    public void ProbesBeforeTheLastRestart_AreIgnored()
    {
        // Restarted 4 min ago (container start unknown): four probes since, the window is not covered.
        var v = EmbedderWatchdogDecision.Decide(Window(0.96, 13, DegradedProbes), Now, null, Now.AddMinutes(-4), Options());
        Assert.False(v.Restart);
    }

    [Fact]
    public void TheConsecutiveRule_StillFires_AndTakesPrecedence()
    {
        // Six fast minutes, then five slow ones in a row: both rules hold, the original one is named.
        var checks = Window(0.98, 13, 20, 30, 25, 40, 22, 35, 8000, 12000, 9000, 15000, 25000);
        var v = EmbedderWatchdogDecision.Decide(checks, Now, StartedLongAgo, null, Options());
        Assert.True(v.Restart);
        Assert.Equal(EmbedderWatchdogDecision.RuleSlowProbes, v.Rule);
        Assert.Equal(5, v.SlowChecks);
        Assert.Contains("5 slow probe(s) over 4 min", v.Reason);
        Assert.Equal(13, v.MeanGpuUtilPct!.Value, 3);
    }

    [Fact]
    public void FourSlowProbes_WithTheSustainedSignature_FireTheSustainedRule()
    {
        var checks = Window(0.98, 13, 20, 30, 25, 40, 22, 35, 800, 8000, 12000, 9000, 15000);
        var v = EmbedderWatchdogDecision.Decide(checks, Now, StartedLongAgo, null, Options());
        Assert.True(v.Restart);
        Assert.Equal(EmbedderWatchdogDecision.RuleSustainedCpu, v.Rule);
        Assert.Equal(4, v.SlowChecks);
    }

    [Fact]
    public void SwitchedOff_OnlyTheConsecutiveRuleRemains()
    {
        var o = Options();
        o.SustainedEnabled = false;
        var v = EmbedderWatchdogDecision.Decide(Window(0.96, 13, DegradedProbes), Now, StartedLongAgo, null, o);
        Assert.False(v.Restart);
        Assert.Equal("healthy", v.State);
        Assert.Null(v.MeanGpuUtilPct);
    }

    [Fact]
    public void TimeoutsCountAsSlow_UnreachableProbesDoNotCount()
    {
        var checks = Window(0.96, 13, 1500, 2000);
        checks[3] = ProbeCheck.Timeout(checks[3].At, 30000, 0.96, 13);
        checks[5] = ProbeCheck.Failed(checks[5].At, 5, 0.96, "connection reset", 13);
        var s = EmbedderWatchdogDecision.EvaluateSustained(checks, Now, Options());
        Assert.NotNull(s);
        Assert.True(s!.Fires);
        Assert.True(s.AnySlow);
        Assert.Equal(0.96, s.MeanCpuCores, 3);
    }

    [Fact]
    public void TheWindowIsTheConfiguredLength()
    {
        // 5 min window at the defaults otherwise: probes must span the window less one check (240 s),
        // so six or five one-minute probes cover it and four do not.
        var o = Options();
        o.SustainedWindowSeconds = 300;
        Assert.True(EmbedderWatchdogDecision.Decide(WindowOf(6, 0.96, 13, DegradedProbes), Now, StartedLongAgo, null, o).Restart);
        Assert.True(EmbedderWatchdogDecision.Decide(WindowOf(5, 0.96, 13, DegradedProbes), Now, StartedLongAgo, null, o).Restart);
        Assert.False(EmbedderWatchdogDecision.Decide(WindowOf(4, 0.96, 13, DegradedProbes), Now, StartedLongAgo, null, o).Restart);
    }
}

public class GpuUtilFeedTests
{
    private static readonly DateTime T0 = new(2026, 11, 1, 23, 0, 0, DateTimeKind.Utc);

    [Fact]
    public void MeanSince_AveragesTheReadingsInTheInterval()
    {
        var feed = new GpuUtilFeed();
        for (var i = 0; i < 24; i++) feed.Record(T0.AddSeconds(5 * i), i < 12 ? 30 : 10);
        Assert.Equal(10, feed.MeanSince(T0.AddSeconds(55), T0.AddSeconds(115)));
        Assert.Equal(30, feed.MeanSince(T0.AddSeconds(-5), T0.AddSeconds(55)));
        Assert.Null(feed.MeanSince(T0.AddMinutes(10), T0.AddMinutes(11)));
        Assert.Equal(10, feed.Latest);
    }

    [Fact]
    public void OldReadings_AreDropped()
    {
        var feed = new GpuUtilFeed(TimeSpan.FromMinutes(1));
        feed.Record(T0, 90);
        feed.Record(T0.AddMinutes(2), 10);
        Assert.Null(feed.MeanSince(T0.AddSeconds(-1), T0.AddSeconds(1)));
        Assert.Equal(10, feed.MeanSince(T0, T0.AddMinutes(3)));
    }

    [Fact]
    public void EmptyFeed_HasNoMean()
    {
        Assert.Null(new GpuUtilFeed().MeanSince(T0, T0.AddMinutes(1)));
        Assert.Null(new GpuUtilFeed().Latest);
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

    [Fact]
    public void SustainedRule_IsOnByDefault_WithTheMeasuredThresholds()
    {
        var o = new EmbedderWatchdogOptions().Normalize();
        Assert.True(o.SustainedEnabled);
        Assert.Equal(600, o.SustainedWindowSeconds);
        Assert.Equal(0.85, o.SustainedCpuCores);
        Assert.Equal(40, o.SustainedMaxGpuUtilPct);
        Assert.Equal(2500, o.SustainedSlowMs);
    }

    [Fact]
    public void SustainedWindow_IsAtLeastTwoChecks()
    {
        var o = new EmbedderWatchdogOptions { CheckSeconds = 60, SustainedWindowSeconds = 30, SustainedMaxGpuUtilPct = 150 }.Normalize();
        Assert.Equal(120, o.SustainedWindowSeconds);
        Assert.Equal(100, o.SustainedMaxGpuUtilPct);
    }
}
