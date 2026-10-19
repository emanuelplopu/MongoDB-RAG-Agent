using System.Globalization;
using LibreHardwareMonitor.Hardware;
using MongoDB.Bson;
using MongoDB.Bson.IO;

namespace RecallHub.HostAgent.Probes;

/// <summary>
/// <c>--sensors</c>: lists every LibreHardwareMonitor device and sensor the agent reads (type, name,
/// value) and the <c>temp</c> / <c>sensors</c> fields it would derive from them, then exits. Read-only:
/// it neither builds the host nor touches MongoDB. Run it elevated (CPU temperatures and SMART data need
/// administrator rights, CPU temperatures also PawnIO).
/// </summary>
public static class SensorDiagnostics
{
    private static readonly JsonWriterSettings JsonSettings = new() { OutputMode = JsonOutputMode.RelaxedExtendedJson, Indent = true };

    /// <summary>Opens LibreHardwareMonitor once, prints the listing and returns the process exit code.</summary>
    public static int Run(TextWriter output, TextWriter error)
    {
        var elevated = LhmSensorProbe.IsElevated();
        if (!elevated)
        {
            error.WriteLine("Not elevated: CPU temperatures and drive SMART data stay empty. Run from an administrator prompt.");
        }

        var computer = LhmSensorProbe.CreateComputer();
        try
        {
            computer.Open();
        }
        catch (Exception ex)
        {
            error.WriteLine($"LibreHardwareMonitor could not be opened: {ex.GetType().Name}: {ex.Message}");
            return 1;
        }

        try
        {
            var readings = LhmSensorProbe.ReadAll(computer);
            output.WriteLine($"LibreHardwareMonitor {typeof(Computer).Assembly.GetName().Version}, elevated={elevated}, {readings.Count} sensors");
            Write(output, readings);
            var (temp, sensors) = LhmSensorProbe.Map(readings, elevated);
            output.WriteLine();
            output.WriteLine("Agent fields:");
            output.WriteLine(new BsonDocument { { "temp", temp }, { "sensors", sensors } }.ToJson(JsonSettings));
            return 0;
        }
        finally
        {
            try { computer.Close(); } catch (Exception) { /* exiting anyway */ }
        }
    }

    /// <summary>One block per device, one line per sensor; storage temperatures say whether the agent uses them.</summary>
    internal static void Write(TextWriter output, IEnumerable<SensorReading> readings)
    {
        foreach (var device in readings.GroupBy(r => (r.HardwareId, r.Hardware, r.HardwareName)))
        {
            output.WriteLine();
            output.WriteLine($"{device.Key.Hardware} {device.Key.HardwareName} [{device.Key.HardwareId}]");
            foreach (var r in device)
            {
                var value = r.Value is { } v ? v.ToString("0.###", CultureInfo.InvariantCulture) : "-";
                output.WriteLine($"  {r.Type,-12} {r.Name,-36} {value,10}{Note(r)}");
            }
        }
    }

    private static string Note(SensorReading r)
    {
        if (r.Hardware != HardwareType.Storage || r.Type != SensorType.Temperature) return "";
        return LhmSensorProbe.StorageTemperatureRank(r.Name) switch
        {
            null => "  (ignored: threshold or unknown name)",
            _ when !LhmSensorProbe.ValidTemperature(r.Value) => "  (ignored: unreadable)",
            0 => "  (drive temperature)",
            1 => "  (drive temperature unless a composite exists)",
            _ => "  (fallback without composite/plain: hottest per-sensor)",
        };
    }
}
