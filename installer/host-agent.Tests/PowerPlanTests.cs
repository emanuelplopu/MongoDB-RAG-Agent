using MongoDB.Bson;
using Xunit;

namespace RecallHub.HostAgent.Tests;

public class PowerPlanDecisionTests
{
    private static readonly DateTime Now = new(2026, 10, 28, 9, 0, 0, DateTimeKind.Utc);

    private static BsonDocument Desire(string tenant, string desired, int ageSeconds, string reason = "office hours")
        => new()
        {
            { "_id", "power_plan:" + tenant },
            { "tenant", tenant },
            { "desired", desired },
            { "reason", reason },
            { "updated_at", Now.AddSeconds(-ageSeconds) },
        };

    [Fact]
    public void NoDesires_MeansNormal()
    {
        var (desired, reasons) = PowerPlanDecision.Decide(Array.Empty<BsonDocument>(), Now, 600);
        Assert.Equal("normal", desired);
        Assert.Empty(reasons);
    }

    [Fact]
    public void FreshCappedDesire_Caps()
    {
        var (desired, reasons) = PowerPlanDecision.Decide(new[] { Desire("quellex", "capped", 30) }, Now, 600);
        Assert.Equal("capped", desired);
        Assert.Single(reasons);
        Assert.Contains("quellex: capped (office hours)", reasons[0]);
    }

    [Fact]
    public void StaleCappedDesire_IsIgnored()
    {
        var (desired, reasons) = PowerPlanDecision.Decide(new[] { Desire("quellex", "capped", 900) }, Now, 600);
        Assert.Equal("normal", desired);
        Assert.Contains("stale", reasons[0]);
    }

    [Fact]
    public void AnyFreshTenantAskingForTheCap_Wins()
    {
        var docs = new[] { Desire("recallhub", "normal", 10), Desire("quellex", "capped", 10) };
        Assert.Equal("capped", PowerPlanDecision.Decide(docs, Now, 600).Desired);
    }

    [Fact]
    public void DesireWithoutTimestamp_IsStale()
    {
        var doc = Desire("quellex", "capped", 0);
        doc.Remove("updated_at");
        Assert.Equal("normal", PowerPlanDecision.Decide(new[] { doc }, Now, 600).Desired);
    }

    [Fact]
    public void StatusDocumentAndForeignIds_AreIgnored()
    {
        var docs = new[]
        {
            new BsonDocument { { "_id", "power_plan" }, { "desired", "capped" }, { "updated_at", Now } },
            new BsonDocument { { "_id", "something_else" }, { "desired", "capped" }, { "updated_at", Now } },
        };
        Assert.Equal("normal", PowerPlanDecision.Decide(docs, Now, 600).Desired);
    }
}

public class PowercfgStateTests
{
    private const string Query =
        "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)\n" +
        "  Subgroup GUID: 54533251-82be-4824-96c1-47b60b740d00  (Processor power management)\n" +
        "    Power Setting GUID: bc5038f7-23e0-4960-96da-33abaf5935ec  (Maximum processor state)\n" +
        "      Minimum Possible Setting: 0x00000000\n" +
        "      Maximum Possible Setting: 0x00000064\n" +
        "    Current AC Power Setting Index: 0x00000063\n" +
        "    Current DC Power Setting Index: 0x00000064\n";

    [Fact]
    public void ParseIndexes_ReadsHexValues()
    {
        var (ac, dc) = PowercfgState.ParseIndexes(Query);
        Assert.Equal(99, ac);
        Assert.Equal(100, dc);
    }

    [Fact]
    public void ParseIndexes_MissingSettingGivesNulls()
    {
        Assert.Equal((null, null), PowercfgState.ParseIndexes("Power Scheme GUID: x\n"));
    }

    [Theory]
    [InlineData(99, 0, true, "capped")]
    [InlineData(100, 2, false, "normal")]
    [InlineData(99, 2, false, "normal")]   // boost still on: not what the agent calls capped
    [InlineData(100, 0, false, "normal")]
    public void IsCapped_NeedsBothSettings(int maxState, int boost, bool capped, string state)
    {
        var s = new PowercfgState(maxState, maxState, boost, boost);
        Assert.Equal(capped, s.IsCapped);
        Assert.Equal(state, s.State);
    }

    [Fact]
    public void UnknownSettings_AreUnknown()
    {
        Assert.Equal("unknown", new PowercfgState(null, null, null, null).State);
    }
}
