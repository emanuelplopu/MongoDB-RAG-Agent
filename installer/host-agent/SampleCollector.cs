using System.Diagnostics;
using System.Reflection;
using MongoDB.Bson;
using RecallHub.HostAgent.Probes;
using RecallHub.HostAgent.Storage;

namespace RecallHub.HostAgent;

/// <summary>
/// Runs every probe for one tick and assembles a host_samples document (schema 1, documented in
/// docs/12-TELEMETRY_SYSTEM.md). Fast counter reads run inline; nvidia-smi, the Docker engine,
/// Ollama, backend health, activity and hardware sensors run concurrently. A failing probe leaves
/// its fields out and records a message under "errors" — it never loses the sample.
/// </summary>
public sealed class SampleCollector : IDisposable
{
    public const int SchemaVersion = 1;

    private readonly AgentOptions _options;
    private readonly SystemProbe _system;
    private readonly RaplPowerProbe _rapl;
    private readonly NvidiaSmiProbe _gpu;
    private readonly DockerProbe _docker;
    private readonly OllamaProbe _ollama;
    private readonly HealthProbe _health;
    private readonly ActivityReader? _activity;
    private readonly IHardwareSensorProbe _sensors;
    private readonly BsonDocument _meta;

    public SampleCollector(ILoggerFactory loggers, AgentOptions options, MongoContext? mongo, IHardwareSensorProbe sensors)
    {
        _options = options;
        _system = new SystemProbe(loggers.CreateLogger<SystemProbe>(), options.PrimaryDrive);
        _rapl = new RaplPowerProbe(loggers.CreateLogger<RaplPowerProbe>());
        _gpu = new NvidiaSmiProbe(loggers.CreateLogger<NvidiaSmiProbe>(), options.Nvidia);
        _docker = new DockerProbe(loggers.CreateLogger<DockerProbe>(), options.Docker);
        _ollama = new OllamaProbe(options.OllamaUrl);
        _health = new HealthProbe(options.Health);
        _activity = mongo is null ? null : new ActivityReader(options, mongo);
        _sensors = sensors;
        _meta = new BsonDocument
        {
            { "host", Environment.MachineName },
            { "source", "host-agent" },
            { "agent_version", AgentVersion },
            { "schema", SchemaVersion },
        };
    }

    public static string AgentVersion =>
        Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "0.0.0";

    public async Task<BsonDocument> CollectAsync(CancellationToken ct)
    {
        var watch = Stopwatch.StartNew();
        var errors = new BsonDocument();
        var doc = new BsonDocument
        {
            { "ts", new BsonDateTime(DateTime.UtcNow) },
            { "meta", _meta.DeepClone() },
            { "interval_s", _options.IntervalSeconds },
        };
        using var tick = CancellationTokenSource.CreateLinkedTokenSource(ct);
        tick.CancelAfter(TimeSpan.FromSeconds(Math.Max(4, _options.IntervalSeconds * 2)));

        // Every concurrent probe writes only into its own fragments (BsonDocument is not
        // thread-safe); the fragments are merged once all probes have finished.
        var gpu = new Fragment();
        var docker = new Fragment();
        var ollama = new Fragment();
        var activity = new Fragment();
        var sensors = new Fragment();

        var gpuTask = Guard("gpu", gpu, async () => (object?)await _gpu.SampleAsync(gpu.Doc, gpu.Temp, gpu.Power, tick.Token));
        var dockerTask = Guard("docker", docker, async () => (object?)await _docker.SampleAsync(docker.Errors, tick.Token));
        var ollamaTask = Guard("ollama", ollama, async () => (object?)await _ollama.SampleAsync(ollama.Errors, tick.Token));
        var healthTask = Guard("health", new Fragment(), async () => (object?)await _health.SampleAsync(tick.Token));
        var activityTask = _activity is null
            ? Task.FromResult<object?>(null)
            : Guard("activity", activity, async () => (object?)await _activity.SampleAsync(activity.Errors, tick.Token));
        var sensorsTask = Guard("sensors", sensors, async () => (object?)await _sensors.SampleAsync(sensors.Temp, sensors.Power, sensors.Errors, tick.Token));

        var power = new BsonDocument();
        _system.Sample(doc, errors);
        _rapl.Sample(power, errors);

        await Task.WhenAll(gpuTask, dockerTask, ollamaTask, healthTask, activityTask, sensorsTask);

        var temp = new BsonDocument();
        foreach (var fragment in new[] { gpu, docker, ollama, activity, sensors })
        {
            Merge(power, fragment.Power);
            Merge(temp, fragment.Temp);
            Merge(errors, fragment.Errors);
        }
        if (power.ElementCount > 0) doc["power"] = power;
        if (temp.ElementCount > 0) doc["temp"] = temp;
        if (gpu.Doc.ElementCount > 0) doc["gpu"] = gpu.Doc;
        if (dockerTask.Result is BsonArray containers) doc["containers"] = containers;
        if (ollamaTask.Result is BsonDocument ollamaDoc) doc["ollama"] = ollamaDoc;
        if (healthTask.Result is BsonArray health) doc["health"] = health;
        if (activityTask.Result is BsonArray activityDoc) doc["activity"] = activityDoc;
        doc["sensors"] = sensorsTask.Result as BsonDocument ?? new BsonDocument("status", "error");
        doc["sample_ms"] = (int)watch.ElapsedMilliseconds;
        if (errors.ElementCount > 0) doc["errors"] = errors;
        return doc;
    }

    /// <summary>Output buffers owned by one concurrent probe.</summary>
    private sealed class Fragment
    {
        public BsonDocument Doc { get; } = new();
        public BsonDocument Temp { get; } = new();
        public BsonDocument Power { get; } = new();
        public BsonDocument Errors { get; } = new();
    }

    private static void Merge(BsonDocument target, BsonDocument source)
    {
        foreach (var element in source) target[element.Name] = element.Value;
    }

    /// <summary>Runs a probe; an unexpected exception becomes an error entry instead of failing the tick.</summary>
    private static async Task<object?> Guard(string name, Fragment fragment, Func<Task<object?>> probe)
    {
        try
        {
            return await probe();
        }
        catch (OperationCanceledException)
        {
            fragment.Errors[name] = "timeout";
            return null;
        }
        catch (Exception ex)
        {
            fragment.Errors[name] = $"{ex.GetType().Name}: {ex.Message}";
            return null;
        }
    }

    public void Dispose()
    {
        _system.Dispose();
        _rapl.Dispose();
        _docker.Dispose();
        _ollama.Dispose();
        _health.Dispose();
        (_sensors as IDisposable)?.Dispose();
    }
}
