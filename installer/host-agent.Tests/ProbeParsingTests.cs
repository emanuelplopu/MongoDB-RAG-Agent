using System.Text.Json;
using MongoDB.Bson;
using RecallHub.HostAgent.Probes;
using RecallHub.HostAgent.Storage;
using Xunit;

namespace RecallHub.HostAgent.Tests;

public class NvidiaSmiProbeTests
{
    [Fact]
    public void Parse_KeepsCommasInTheTrailingName()
    {
        var line = "15, 5, 4081, 16311, 10.20, 70.00, 47, N/A, 49, 472, 810, 30, P5, 0x0000000000000004, NVIDIA RTX, Pro Edition";
        var parsed = NvidiaSmiProbe.Parse(NvidiaSmiProbe.FullFields, line);

        Assert.NotNull(parsed);
        Assert.Equal("NVIDIA RTX, Pro Edition", parsed!["name"]);
        Assert.Equal("47", parsed["temperature.gpu"]);
    }

    [Fact]
    public void Parse_RejectsShortLines()
    {
        Assert.Null(NvidiaSmiProbe.Parse(NvidiaSmiProbe.FullFields, "15, 5"));
    }

    [Theory]
    [InlineData("N/A", null)]
    [InlineData("[N/A]", null)]
    [InlineData("[Not Supported]", null)]
    [InlineData(" 12.5 ", 12.5)]
    [InlineData("abc", null)]
    public void Number_HandlesNotAvailableMarkers(string input, double? expected)
    {
        Assert.Equal(expected, NvidiaSmiProbe.Number(input));
    }

    [Fact]
    public void ReasonsFromMask_DecodesThrottleBits()
    {
        Assert.Equal(new[] { "power_cap" }, NvidiaSmiProbe.ReasonsFromMask("0x0000000000000004"));
        Assert.Equal(new[] { "sw_thermal", "hw_thermal" }, NvidiaSmiProbe.ReasonsFromMask("0x60"));
        Assert.Empty(NvidiaSmiProbe.ReasonsFromMask("0x0000000000000001")); // idle is not a reason
        Assert.Empty(NvidiaSmiProbe.ReasonsFromMask("[N/A]"));
    }

    [Fact]
    public void Apply_MapsUnitsAndHeadroom()
    {
        var raw = NvidiaSmiProbe.Parse(NvidiaSmiProbe.FullFields,
            "15, 5, 4096, 16384, 10.20, 70.00, 47, N/A, 49, 472, 810, 30, P5, 0x4, NVIDIA RTX PRO 2000")!;
        var gpu = new BsonDocument();
        var temp = new BsonDocument();
        var power = new BsonDocument();
        NvidiaSmiProbe.Apply(raw, gpu, temp, power);

        Assert.Equal(4096L * 1024 * 1024, gpu["vram_used_bytes"].AsInt64);
        Assert.Equal(16384L * 1024 * 1024, gpu["vram_total_bytes"].AsInt64);
        Assert.Equal(47.0, temp["gpu_c"].AsDouble);
        Assert.Equal(49.0, temp["gpu_headroom_c"].AsDouble);
        Assert.False(temp.Contains("gpu_mem_c")); // N/A left out
        Assert.Equal(10.2, power["gpu_w"].AsDouble);
        Assert.Equal("P5", gpu["pstate"].AsString);
        Assert.Equal("power_cap", gpu["reasons"].AsBsonArray[0].AsString);
    }
}

public class DockerProbeTests
{
    private const string CgroupV2Stats = """
        {
          "cpu_stats": {"cpu_usage": {"total_usage": 12000000000}, "system_cpu_usage": 50000000000, "online_cpus": 24},
          "memory_stats": {"usage": 700000000, "limit": 25197576192, "stats": {"inactive_file": 100000000}},
          "blkio_stats": {"io_service_bytes_recursive": [{"op": "read", "value": 10}, {"op": "write", "value": 3}, {"op": "Read", "value": 5}]},
          "networks": {"eth0": {"rx_bytes": 100, "tx_bytes": 40}, "eth1": {"rx_bytes": 1, "tx_bytes": 2}}
        }
        """;

    [Fact]
    public void ParseStats_CgroupV2_ExcludesInactiveFileAndSumsIo()
    {
        using var json = JsonDocument.Parse(CgroupV2Stats);
        var c = DockerProbe.ParseStats(json.RootElement)!;

        Assert.Equal(12_000_000_000UL, c.CpuTotalNs);
        Assert.Equal(24u, c.OnlineCpus);
        Assert.Equal(600_000_000L, c.MemBytes);
        Assert.Equal(25_197_576_192L, c.MemLimitBytes);
        Assert.Equal(15L, c.BlkReadBytes);
        Assert.Equal(3L, c.BlkWriteBytes);
        Assert.Equal(101L, c.NetRxBytes);
        Assert.Equal(42L, c.NetTxBytes);
    }

    [Fact]
    public void ParseStats_CgroupV1_UsesTotalInactiveFile()
    {
        using var json = JsonDocument.Parse("""
            {"cpu_stats": {"cpu_usage": {"total_usage": 1, "percpu_usage": [1, 1, 1, 1]}, "system_cpu_usage": 2},
             "memory_stats": {"usage": 500, "limit": 1000, "stats": {"total_inactive_file": 200}}}
            """);
        var c = DockerProbe.ParseStats(json.RootElement)!;

        Assert.Equal(4u, c.OnlineCpus); // derived from percpu_usage
        Assert.Equal(300L, c.MemBytes);
    }

    [Fact]
    public void ParseStats_ReturnsNullWithoutCpuStats()
    {
        using var json = JsonDocument.Parse("""{"memory_stats": {}}""");
        Assert.Null(DockerProbe.ParseStats(json.RootElement));
    }

    [Fact]
    public void CpuCores_IsContainerShareOfSystemTimesCpus()
    {
        var current = new ContainerCounters(12_000_000_000, 60_000_000_000, 24, null, null, 0, 0, 0, 0);
        // 2 s of container CPU in 10 s of system CPU (all 24 cores) => 4.8 cores busy.
        var cores = DockerProbe.CpuCores(10_000_000_000, 50_000_000_000, current);
        Assert.Equal(4.8, cores!.Value, 6);
    }

    [Fact]
    public void CpuCores_NullOnCounterResetOrNoProgress()
    {
        var current = new ContainerCounters(5, 100, 24, null, null, 0, 0, 0, 0);
        Assert.Null(DockerProbe.CpuCores(10, 50, current));   // container restarted: counter went down
        Assert.Null(DockerProbe.CpuCores(1, 100, current));   // system counter did not advance
    }

    [Fact]
    public void ContainerName_TrimsLeadingSlash()
    {
        using var json = JsonDocument.Parse("""{"Id": "abcdef1234567890", "Names": ["/rag-backend-quellex"]}""");
        Assert.Equal("rag-backend-quellex", DockerProbe.ContainerName(json.RootElement));
    }
}

public class RaplPowerProbeTests
{
    [Theory]
    [InlineData("RAPL_Package0_PKG", "cpu_pkg_w")]
    [InlineData("rapl_package0_pp0", "cpu_cores_w")]
    [InlineData("RAPL_Package0_PP1", "cpu_gfx_w")]
    [InlineData("RAPL_Package1_DRAM", "dram_w")]
    [InlineData("_Total", null)]
    [InlineData("Battery", null)]
    public void FieldFor_MapsRaplDomains(string instance, string? field)
    {
        Assert.Equal(field, RaplPowerProbe.FieldFor(instance));
    }
}

public class SystemProbeTests
{
    [Theory]
    [InlineData("Intel[R] Ethernet Controller I226-V", true)]
    [InlineData("vEthernet (WSL (Hyper-V firewall))", false)]
    [InlineData("isatap.{1234}", false)]
    [InlineData("Loopback Pseudo-Interface 1", false)]
    public void IsCountedNic_SkipsVirtualAdapters(string instance, bool counted)
    {
        Assert.Equal(counted, SystemProbe.IsCountedNic(instance));
    }

    [Fact]
    public void CoreSortKey_OrdersNumerically()
    {
        var sorted = new[] { "0,10", "0,2", "1,0", "0,1" }.OrderBy(SystemProbe.CoreSortKey).ToArray();
        Assert.Equal(new[] { "0,1", "0,2", "0,10", "1,0" }, sorted);
    }
}

public class ActivityReaderTests
{
    private static readonly DateTime Now = new(2026, 10, 18, 12, 0, 0, DateTimeKind.Utc);

    [Fact]
    public void ToActivity_CopiesRunningJob()
    {
        var current = new BsonDocument
        {
            { "_id", "quellex:api" }, { "tenant", "quellex" }, { "source", "api" }, { "job_id", "job-1" },
            { "job_status", "running" }, { "phase", "processing" }, { "step", "embed" },
            { "current_file", "/app/mounts/a.pdf" }, { "processed_files", 3 }, { "total_files", 10 },
            { "updated_at", Now },
        };
        var activity = ActivityReader.ToActivity(current, Now)!;

        Assert.Equal("job-1", activity["job_id"].AsString);
        Assert.Equal("embed", activity["step"].AsString);
        Assert.False(activity.Contains("_id"));
        Assert.False(activity["chat_active"].AsBoolean);
    }

    [Fact]
    public void ToActivity_ChatOnlyWhenRecent()
    {
        var recent = new BsonDocument { { "tenant", "quellex" }, { "job_id", BsonNull.Value }, { "chat_last_active_at", Now.AddSeconds(-20) } };
        var stale = new BsonDocument { { "tenant", "quellex" }, { "job_id", BsonNull.Value }, { "chat_last_active_at", Now.AddMinutes(-5) } };

        Assert.True(ActivityReader.ToActivity(recent, Now)!["chat_active"].AsBoolean);
        Assert.Null(ActivityReader.ToActivity(stale, Now));
    }
}

public class OllamaProbeTests
{
    [Fact]
    public void ParseModels_MapsSizes()
    {
        using var json = JsonDocument.Parse("""
            {"models": [{"name": "gemma4:12b", "size": 9000, "size_vram": 8000, "context_length": 8192}]}
            """);
        var models = OllamaProbe.ParseModels(json.RootElement);

        Assert.Single(models);
        Assert.Equal("gemma4:12b", models[0]["name"].AsString);
        Assert.Equal(8000L, models[0]["vram_bytes"].AsInt64);
        Assert.Equal(8192L, models[0]["ctx"].AsInt64);
    }
}
