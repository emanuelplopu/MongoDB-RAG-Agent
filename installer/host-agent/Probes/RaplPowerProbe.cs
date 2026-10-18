using System.Diagnostics;
using MongoDB.Bson;

namespace RecallHub.HostAgent.Probes;

/// <summary>
/// CPU power from the Windows "Energy Meter" performance counters, which expose Intel/AMD RAPL
/// domains without a kernel driver (instances such as RAPL_Package0_PKG). Values are milliwatts.
/// Domains: PKG = whole CPU package, PP0 = cores, PP1 = integrated graphics, DRAM = memory.
/// </summary>
public sealed class RaplPowerProbe : IDisposable
{
    private readonly List<(string Field, PerformanceCounter Counter)> _counters = new();

    public RaplPowerProbe(ILogger logger)
    {
        try
        {
            if (!PerformanceCounterCategory.Exists("Energy Meter"))
            {
                logger.LogInformation("Energy Meter counters not present: CPU power unavailable on this host");
                return;
            }
            foreach (var instance in new PerformanceCounterCategory("Energy Meter").GetInstanceNames())
            {
                var field = FieldFor(instance);
                if (field is null) continue;
                var counter = new PerformanceCounter("Energy Meter", "Power", instance, readOnly: true);
                counter.NextValue();
                _counters.Add((field, counter));
            }
            logger.LogInformation("RAPL power domains: {Domains}", string.Join(", ", _counters.Select(c => c.Field).Distinct()));
        }
        catch (Exception ex)
        {
            logger.LogWarning("Energy Meter counters unavailable: {Message}", ex.Message);
        }
    }

    public bool Available => _counters.Count > 0;

    /// <summary>Maps an Energy Meter instance name to the sample field it feeds (null = ignored).</summary>
    internal static string? FieldFor(string instance)
    {
        var name = instance.ToLowerInvariant();
        if (!name.StartsWith("rapl_", StringComparison.Ordinal)) return null;
        if (name.EndsWith("_pkg", StringComparison.Ordinal)) return "cpu_pkg_w";
        if (name.EndsWith("_pp0", StringComparison.Ordinal)) return "cpu_cores_w";
        if (name.EndsWith("_pp1", StringComparison.Ordinal)) return "cpu_gfx_w";
        if (name.EndsWith("_dram", StringComparison.Ordinal)) return "dram_w";
        return null;
    }

    /// <summary>Adds the power fields (watts, summed over CPU packages) to <paramref name="power"/>.</summary>
    public void Sample(BsonDocument power, BsonDocument errors)
    {
        if (_counters.Count == 0) return;
        try
        {
            var totals = new Dictionary<string, double>();
            foreach (var (field, counter) in _counters)
            {
                totals[field] = totals.GetValueOrDefault(field) + counter.NextValue() / 1000.0;
            }
            foreach (var (field, watts) in totals)
            {
                // Platforms without a DRAM domain report a constant 0: that is "not measured", not 0 W.
                if (field == "dram_w" && watts <= 0) continue;
                power[field] = Math.Round(watts, 1);
            }
        }
        catch (Exception ex) { errors["rapl"] = ex.Message; }
    }

    public void Dispose()
    {
        foreach (var (_, counter) in _counters) counter.Dispose();
    }
}
