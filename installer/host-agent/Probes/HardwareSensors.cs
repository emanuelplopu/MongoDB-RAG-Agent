using System.Security.Principal;
using LibreHardwareMonitor.Hardware;
using MongoDB.Bson;

namespace RecallHub.HostAgent.Probes;

/// <summary>
/// Driver-level hardware sensors (CPU package/core temperatures, fans, board and storage
/// temperatures). Implementations add temperature fields to <c>temp</c> and return the
/// <c>sensors</c> sub-document, whose <c>status</c> is ok, driver_missing, not_elevated, disabled or error.
/// </summary>
public interface IHardwareSensorProbe
{
    Task<BsonDocument?> SampleAsync(BsonDocument temp, BsonDocument power, BsonDocument errors, CancellationToken ct);
}

/// <summary>Used when HardwareSensors.Enabled is false.</summary>
public sealed class DisabledSensorProbe : IHardwareSensorProbe
{
    public Task<BsonDocument?> SampleAsync(BsonDocument temp, BsonDocument power, BsonDocument errors, CancellationToken ct) =>
        Task.FromResult<BsonDocument?>(new BsonDocument("status", "disabled"));
}

/// <summary>Chooses the sensor implementation from configuration.</summary>
public static class HardwareSensorFactory
{
    public static IHardwareSensorProbe Create(AgentOptions options, ILoggerFactory loggers) =>
        options.HardwareSensors.Enabled
            ? new LhmSensorProbe(loggers.CreateLogger<LhmSensorProbe>())
            : new DisabledSensorProbe();
}

/// <summary>One sensor value as read from LibreHardwareMonitor (decoupled from its live objects for testing).</summary>
public sealed record SensorReading(HardwareType Hardware, string HardwareName, SensorType Type, string Name, float? Value);

/// <summary>
/// Reads CPU, motherboard (Super I/O) and storage sensors with LibreHardwareMonitorLib 0.9.6. CPU
/// temperatures come from model-specific registers, which need the signed PawnIO driver (installed
/// separately, e.g. <c>winget install namazso.PawnIO</c>) and an elevated process (the Windows
/// service runs as LocalSystem). Without them the library keeps running and CPU temperatures are
/// simply absent; the status says why. The GPU is read by nvidia-smi instead.
/// </summary>
public sealed class LhmSensorProbe : IHardwareSensorProbe, IDisposable
{
    private static readonly TimeSpan RetryOpenAfter = TimeSpan.FromMinutes(5);

    private readonly ILogger _logger;
    private readonly bool _elevated;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private Computer? _computer;
    private DateTime _nextOpenAttempt = DateTime.MinValue;
    private string? _lastStatus;

    public LhmSensorProbe(ILogger logger)
    {
        _logger = logger;
        using var identity = WindowsIdentity.GetCurrent();
        _elevated = new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
    }

    public async Task<BsonDocument?> SampleAsync(BsonDocument temp, BsonDocument power, BsonDocument errors, CancellationToken ct)
    {
        if (!await _gate.WaitAsync(TimeSpan.FromSeconds(3), ct)) return new BsonDocument("status", "busy");
        try
        {
            var readings = await Task.Run(ReadAll, ct);
            if (readings is null)
            {
                errors["sensors"] = "LibreHardwareMonitor could not be opened";
                return new BsonDocument("status", "error");
            }
            var (fields, sensors) = Map(readings, _elevated);
            foreach (var element in fields) temp[element.Name] = element.Value;
            var status = sensors["status"].AsString;
            if (status != _lastStatus)
            {
                _lastStatus = status;
                if (status == "ok") _logger.LogInformation("Hardware sensors: CPU temperature available");
                else _logger.LogWarning("Hardware sensors status {Status}: {Hint}", status, Hint(status));
            }
            return sensors;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            errors["sensors"] = $"{ex.GetType().Name}: {ex.Message}";
            return new BsonDocument("status", "error");
        }
        finally
        {
            _gate.Release();
        }
    }

    private static string Hint(string status) => status switch
    {
        "not_elevated" => "run the agent as the Windows service (LocalSystem) or as administrator",
        "driver_missing" => "install the PawnIO driver (winget install namazso.PawnIO) and restart the agent",
        _ => "see the agent log",
    };

    private List<SensorReading>? ReadAll()
    {
        if (_computer is null)
        {
            if (DateTime.UtcNow < _nextOpenAttempt) return null;
            try
            {
                var computer = new Computer
                {
                    IsCpuEnabled = true,
                    IsMotherboardEnabled = true,
                    IsStorageEnabled = true,
                    IsGpuEnabled = false, // nvidia-smi reads the GPU
                    IsMemoryEnabled = false,
                    IsNetworkEnabled = false,
                    IsControllerEnabled = false,
                    IsPsuEnabled = false,
                    IsBatteryEnabled = false,
                };
                computer.Open();
                _computer = computer;
                _logger.LogInformation("LibreHardwareMonitor opened ({Count} devices, elevated={Elevated})",
                    computer.Hardware.Count, _elevated);
            }
            catch (Exception ex)
            {
                _nextOpenAttempt = DateTime.UtcNow + RetryOpenAfter;
                _logger.LogWarning("LibreHardwareMonitor could not be opened: {Message}", ex.Message);
                return null;
            }
        }

        var readings = new List<SensorReading>();
        foreach (var hardware in _computer.Hardware)
        {
            Collect(hardware, readings);
        }
        return readings;
    }

    private static void Collect(IHardware hardware, List<SensorReading> readings)
    {
        hardware.Update();
        foreach (var sensor in hardware.Sensors)
        {
            readings.Add(new SensorReading(hardware.HardwareType, hardware.Name, sensor.SensorType, sensor.Name, sensor.Value));
        }
        foreach (var sub in hardware.SubHardware)
        {
            Collect(sub, readings);
        }
    }

    /// <summary>A plausible temperature (drivers report 0 or garbage when a sensor is unreadable).</summary>
    internal static bool ValidTemperature(float? value) => value is > 0f and < 150f && !float.IsNaN(value.Value);

    /// <summary>Maps readings to the sample's temp fields and the sensors sub-document.</summary>
    internal static (BsonDocument Temp, BsonDocument Sensors) Map(IEnumerable<SensorReading> readings, bool elevated)
    {
        var temp = new BsonDocument();
        var fans = new BsonArray();
        var board = new BsonArray();
        var storage = new Dictionary<string, double>(StringComparer.Ordinal);
        double? cpuPackage = null, coreMax = null, coreAvg = null, coreFallback = null;

        foreach (var r in readings)
        {
            switch (r.Hardware)
            {
                case HardwareType.Cpu when r.Type == SensorType.Temperature && ValidTemperature(r.Value):
                    var v = Math.Round((double)r.Value!.Value, 1);
                    if (r.Name.Equals("CPU Package", StringComparison.OrdinalIgnoreCase)
                        || r.Name.Contains("Tctl", StringComparison.OrdinalIgnoreCase)
                        || r.Name.Contains("Tdie", StringComparison.OrdinalIgnoreCase))
                        cpuPackage = Math.Max(cpuPackage ?? double.MinValue, v);
                    else if (r.Name.Equals("Core Max", StringComparison.OrdinalIgnoreCase)) coreMax = v;
                    else if (r.Name.Equals("Core Average", StringComparison.OrdinalIgnoreCase)) coreAvg = v;
                    else if (r.Name.StartsWith("CPU Core", StringComparison.OrdinalIgnoreCase) || r.Name.StartsWith("Core #", StringComparison.OrdinalIgnoreCase))
                        coreFallback = Math.Max(coreFallback ?? double.MinValue, v);
                    break;
                case HardwareType.Motherboard or HardwareType.SuperIO when r.Type == SensorType.Fan && r.Value is { } rpm && !float.IsNaN(rpm):
                    fans.Add(new BsonDocument { { "name", r.Name }, { "rpm", Math.Round((double)rpm) } });
                    break;
                case HardwareType.Motherboard or HardwareType.SuperIO when r.Type == SensorType.Temperature && ValidTemperature(r.Value):
                    board.Add(new BsonDocument { { "name", r.Name }, { "c", Math.Round((double)r.Value!.Value, 1) } });
                    break;
                case HardwareType.Storage when r.Type == SensorType.Temperature && ValidTemperature(r.Value):
                    storage[r.HardwareName] = Math.Max(storage.GetValueOrDefault(r.HardwareName, double.MinValue), Math.Round((double)r.Value!.Value, 1));
                    break;
            }
        }

        coreMax ??= coreFallback;
        cpuPackage ??= coreMax; // some CPUs expose only per-core sensors
        if (cpuPackage is not null) temp["cpu_pkg_c"] = cpuPackage.Value;
        if (coreMax is not null) temp["cpu_core_max_c"] = coreMax.Value;
        if (coreAvg is not null) temp["cpu_core_avg_c"] = coreAvg.Value;
        if (board.Count > 0) temp["board_max_c"] = board.Max(b => b["c"].AsDouble);
        if (storage.Count > 0) temp["storage_max_c"] = storage.Values.Max();

        var status = cpuPackage is not null ? "ok" : elevated ? "driver_missing" : "not_elevated";
        var sensors = new BsonDocument { { "status", status }, { "source", "lhm" }, { "elevated", elevated } };
        if (fans.Count > 0) sensors["fans"] = fans;
        if (board.Count > 0) sensors["board"] = board;
        if (storage.Count > 0)
        {
            sensors["storage"] = new BsonArray(storage.Select(kv => new BsonDocument { { "name", kv.Key }, { "c", kv.Value } }));
        }
        return (temp, sensors);
    }

    public void Dispose()
    {
        try { _computer?.Close(); } catch (Exception) { /* closing on shutdown */ }
        _gate.Dispose();
    }
}
