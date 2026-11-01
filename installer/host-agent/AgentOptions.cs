namespace RecallHub.HostAgent;

/// <summary>Configuration bound from the "Agent" section (appsettings.json, ProgramData override, env vars).</summary>
public sealed class AgentOptions
{
    public const string SectionName = "Agent";

    /// <summary>Seconds between samples.</summary>
    public int IntervalSeconds { get; set; } = 5;

    /// <summary>MongoDB connection string (the stack's MongoDB on the host loopback).</summary>
    public string MongoUri { get; set; } = "mongodb://127.0.0.1:11017/?directConnection=true";

    /// <summary>Telemetry database; must match the backend's TELEMETRY_DATABASE.</summary>
    public string Database { get; set; } = "recallhub_telemetry";

    /// <summary>TTL used only when the agent creates host_samples before the backend did.</summary>
    public int HostSamplesTtlDays { get; set; } = 30;

    /// <summary>activity_current documents older than this are not stamped onto samples.</summary>
    public int ActivityFreshSeconds { get; set; } = 60;

    /// <summary>Samples kept in memory while MongoDB is unreachable (oldest dropped first).</summary>
    public int BufferMaxSamples { get; set; } = 720;

    /// <summary>Drive reported as the primary data volume (null = the Windows system drive).</summary>
    public string? PrimaryDrive { get; set; }

    /// <summary>Ollama base URL on the host (null/empty disables the probe).</summary>
    public string? OllamaUrl { get; set; } = "http://127.0.0.1:11434";

    /// <summary>Backend health endpoints whose latency is recorded (use 127.0.0.1, not localhost).</summary>
    public List<HealthTarget> Health { get; set; } = new();

    public NvidiaOptions Nvidia { get; set; } = new();

    public DockerOptions Docker { get; set; } = new();

    public SensorOptions HardwareSensors { get; set; } = new();

    public PowerPlanOptions PowerPlan { get; set; } = new();

    public EmbedderWatchdogOptions EmbedderWatchdog { get; set; } = new();

    /// <summary>Fills list defaults after binding (the binder appends to pre-filled lists).</summary>
    public AgentOptions Normalize()
    {
        IntervalSeconds = Math.Clamp(IntervalSeconds, 1, 300);
        if (Docker.Pipes.Count == 0)
        {
            Docker.Pipes.AddRange(new[] { "dockerDesktopLinuxEngine", "docker_engine" });
        }
        EmbedderWatchdog.Normalize();
        return this;
    }
}

public sealed class HealthTarget
{
    public string Name { get; set; } = "";
    public string Url { get; set; } = "";
}

public sealed class NvidiaOptions
{
    public bool Enabled { get; set; } = true;
    public string Executable { get; set; } = "nvidia-smi";
    public int GpuIndex { get; set; }
}

public sealed class DockerOptions
{
    public bool Enabled { get; set; } = true;

    /// <summary>Named pipes tried in order (Docker Desktop's Linux engine first).</summary>
    public List<string> Pipes { get; set; } = new();

    /// <summary>Only containers of this compose project (label com.docker.compose.project); empty = all.</summary>
    public string? ProjectLabel { get; set; } = "recallhub";
}

public sealed class SensorOptions
{
    /// <summary>Read CPU/board/storage sensors with LibreHardwareMonitor (needs the PawnIO driver and elevation).</summary>
    public bool Enabled { get; set; }
}

public sealed class PowerPlanOptions
{
    /// <summary>Apply the CPU clock cap the backends publish in host_control (the service runs elevated).</summary>
    public bool Enabled { get; set; } = true;

    /// <summary>Seconds between checks of host_control.</summary>
    public int CheckSeconds { get; set; } = 15;

    /// <summary>A tenant's desire older than this counts as "normal" (a stopped backend never leaves the cap on).</summary>
    public int DesiredFreshSeconds { get; set; } = 600;
}

/// <summary>One llama.cpp embedding server the watchdog probes and may restart.</summary>
public sealed class EmbedderWatchdogTarget
{
    /// <summary>Short name in logs and the status document (e.g. "embedder").</summary>
    public string Name { get; set; } = "";

    /// <summary>OpenAI-compatible embeddings endpoint on the host loopback (the container's published port).</summary>
    public string Url { get; set; } = "";

    /// <summary>Container names behind the URL; the running one is probed and restarted (the CPU and GPU variants share a port).</summary>
    public List<string> Containers { get; set; } = new();
}

/// <summary>
/// Restarts a llama.cpp embedding server that has degraded (seen three times on the client's
/// machine after a day or two of uptime: one CPU core pinned, 8-10 GB RSS, a few hundred tokens/s
/// instead of 13-16k on the GPU, every file's embedding step taking an hour). See EmbedderWatchdog.cs.
/// </summary>
public sealed class EmbedderWatchdogOptions
{
    public bool Enabled { get; set; } = true;

    /// <summary>Seconds between probes.</summary>
    public int CheckSeconds { get; set; } = 60;

    /// <summary>A probe answered later than this counts as slow (a healthy server answers in tens of milliseconds).</summary>
    public int SlowMs { get; set; } = 5000;

    /// <summary>Seconds the probe waits before it gives up; a timeout counts as slow.</summary>
    public int ProbeTimeoutSeconds { get; set; } = 30;

    /// <summary>Restart only after this many consecutive slow probes ...</summary>
    public int ConsecutiveChecks { get; set; } = 5;

    /// <summary>... spanning at least this many seconds.</summary>
    public int WindowSeconds { get; set; } = 240;

    /// <summary>The container's mean CPU use over the slow window must reach this many cores (0 = ignore CPU).</summary>
    public double MinCpuCores { get; set; } = 0.5;

    /// <summary>Seconds after a container start during which nothing is judged (model load, CUDA warm-up).</summary>
    public int WarmupSeconds { get; set; } = 300;

    /// <summary>At most one restart per container in this many seconds.</summary>
    public int MinRestartIntervalSeconds { get; set; } = 1800;

    /// <summary>Approximate size of the probe text in words (~1.3 tokens each).</summary>
    public int ProbeWords { get; set; } = 120;

    /// <summary>Servers to watch; empty = the compose stack's `embedder` (11090) and `embedder-query` (11091).</summary>
    public List<EmbedderWatchdogTarget> Targets { get; set; } = new();

    public EmbedderWatchdogOptions Normalize()
    {
        CheckSeconds = Math.Clamp(CheckSeconds, 10, 3600);
        SlowMs = Math.Max(100, SlowMs);
        ProbeTimeoutSeconds = Math.Clamp(ProbeTimeoutSeconds, 1, 600);
        ConsecutiveChecks = Math.Max(1, ConsecutiveChecks);
        WindowSeconds = Math.Max(0, WindowSeconds);
        MinCpuCores = Math.Max(0, MinCpuCores);
        WarmupSeconds = Math.Max(0, WarmupSeconds);
        MinRestartIntervalSeconds = Math.Max(0, MinRestartIntervalSeconds);
        ProbeWords = Math.Clamp(ProbeWords, 1, 2000);
        if (Targets.Count == 0)
        {
            Targets.Add(new EmbedderWatchdogTarget
            {
                Name = "embedder",
                Url = "http://127.0.0.1:11090/v1/embeddings",
                Containers = { "rag-embedder-gpu", "rag-embedder" },
            });
            Targets.Add(new EmbedderWatchdogTarget
            {
                Name = "embedder-query",
                Url = "http://127.0.0.1:11091/v1/embeddings",
                Containers = { "rag-embedder-query" },
            });
        }
        return this;
    }
}
