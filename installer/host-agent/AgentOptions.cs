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

    /// <summary>Fills list defaults after binding (the binder appends to pre-filled lists).</summary>
    public AgentOptions Normalize()
    {
        IntervalSeconds = Math.Clamp(IntervalSeconds, 1, 300);
        if (Docker.Pipes.Count == 0)
        {
            Docker.Pipes.AddRange(new[] { "dockerDesktopLinuxEngine", "docker_engine" });
        }
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
