using System.Diagnostics;
using System.Globalization;
using MongoDB.Bson;

namespace RecallHub.HostAgent.Probes;

/// <summary>
/// GPU utilisation, VRAM, power, temperatures, clocks and throttle reasons via nvidia-smi on the
/// host (ported from backend/evaluation/synthetic_corpus/resources.py GpuProbe). The richest field
/// set the driver understands is detected once; drivers before 2023 fall back to the basic set.
/// </summary>
public sealed class NvidiaSmiProbe
{
    // "name" is last so a GPU name containing a comma cannot shift the numeric fields.
    internal static readonly string[] FullFields =
    {
        "utilization.gpu", "utilization.memory", "memory.used", "memory.total", "power.draw", "power.limit",
        "temperature.gpu", "temperature.memory", "temperature.gpu.tlimit", "clocks.sm", "clocks.mem",
        "fan.speed", "pstate", "clocks_event_reasons.active", "name",
    };

    internal static readonly string[] BasicFields =
    {
        "utilization.gpu", "utilization.memory", "memory.used", "memory.total", "power.draw", "power.limit",
        "temperature.gpu", "clocks.sm", "clocks.mem", "fan.speed", "pstate", "clocks_throttle_reasons.active", "name",
    };

    /// <summary>NVML clocks-event reason bits (bit 0x1, GPU idle, is not a throttle reason).</summary>
    internal static readonly (int Bit, string Name)[] ReasonBits =
    {
        (0x2, "app_clocks"), (0x4, "power_cap"), (0x8, "hw_slowdown"), (0x10, "sync_boost"),
        (0x20, "sw_thermal"), (0x40, "hw_thermal"), (0x80, "hw_power_brake"), (0x100, "display_clocks"),
    };

    private static readonly HashSet<string> NotAvailable = new(StringComparer.OrdinalIgnoreCase)
    {
        "", "N/A", "[N/A]", "[NOT SUPPORTED]", "[INSUFFICIENT PERMISSIONS]", "NOT SUPPORTED", "-",
    };

    private const double MiB = 1024.0 * 1024.0;

    private readonly ILogger _logger;
    private readonly NvidiaOptions _options;
    private string[]? _fields;
    private bool _disabled;

    public NvidiaSmiProbe(ILogger logger, NvidiaOptions options)
    {
        _logger = logger;
        _options = options;
        _disabled = !options.Enabled;
    }

    /// <summary>Fills the gpu sub-document and the GPU parts of temp/power. Returns false when no GPU was read.</summary>
    public async Task<bool> SampleAsync(BsonDocument gpu, BsonDocument temp, BsonDocument power, CancellationToken ct)
    {
        if (_disabled) return false;
        if (_fields is null)
        {
            foreach (var candidate in new[] { FullFields, BasicFields })
            {
                var probe = await QueryAsync(candidate, ct);
                if (probe is not null && Parse(candidate, probe) is not null)
                {
                    _fields = candidate;
                    _logger.LogInformation("nvidia-smi detected ({Set} field set)", candidate == FullFields ? "full" : "basic");
                    break;
                }
            }
            if (_fields is null)
            {
                _disabled = true;
                _logger.LogInformation("nvidia-smi not available: GPU telemetry disabled");
                return false;
            }
        }
        var line = await QueryAsync(_fields, ct);
        var parsed = line is null ? null : Parse(_fields, line);
        if (parsed is null) return false;
        Apply(parsed, gpu, temp, power);
        return true;
    }

    /// <summary>Parses one "--format=csv,noheader,nounits" line into field -> raw string (null on shape mismatch).</summary>
    internal static Dictionary<string, string>? Parse(string[] fields, string line)
    {
        var parts = line.Trim().Split(',');
        if (parts.Length < fields.Length) return null;
        var result = new Dictionary<string, string>(StringComparer.Ordinal);
        for (var i = 0; i < fields.Length - 1; i++) result[fields[i]] = parts[i].Trim();
        // The last field (name) takes the rest of the line, commas included.
        result[fields[^1]] = string.Join(",", parts.Skip(fields.Length - 1)).Trim();
        return result;
    }

    /// <summary>Number from an nvidia-smi value, or null for N/A markers.</summary>
    internal static double? Number(string? value)
    {
        if (value is null) return null;
        var v = value.Trim().Trim('[', ']').Trim();
        if (NotAvailable.Contains(v)) return null;
        return double.TryParse(v, NumberStyles.Float, CultureInfo.InvariantCulture, out var d) ? d : null;
    }

    /// <summary>Active throttle reasons from the hex mask nvidia-smi prints.</summary>
    internal static List<string> ReasonsFromMask(string? mask)
    {
        var reasons = new List<string>();
        if (string.IsNullOrWhiteSpace(mask) || NotAvailable.Contains(mask.Trim())) return reasons;
        var hex = mask.Trim();
        if (hex.StartsWith("0x", StringComparison.OrdinalIgnoreCase)) hex = hex[2..];
        if (!long.TryParse(hex, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var bits)) return reasons;
        foreach (var (bit, name) in ReasonBits)
        {
            if ((bits & bit) != 0) reasons.Add(name);
        }
        return reasons;
    }

    internal static void Apply(Dictionary<string, string> raw, BsonDocument gpu, BsonDocument temp, BsonDocument power)
    {
        void Put(BsonDocument doc, string key, double? value, double scale = 1.0, bool asLong = false)
        {
            if (value is null) return;
            if (asLong) doc[key] = (long)Math.Round(value.Value * scale);
            else doc[key] = Math.Round(value.Value * scale, 1);
        }

        if (raw.TryGetValue("name", out var name) && !NotAvailable.Contains(name)) gpu["name"] = name;
        Put(gpu, "util_pct", Number(raw.GetValueOrDefault("utilization.gpu")));
        Put(gpu, "mem_util_pct", Number(raw.GetValueOrDefault("utilization.memory")));
        Put(gpu, "vram_used_bytes", Number(raw.GetValueOrDefault("memory.used")), MiB, asLong: true);
        Put(gpu, "vram_total_bytes", Number(raw.GetValueOrDefault("memory.total")), MiB, asLong: true);
        Put(gpu, "sm_mhz", Number(raw.GetValueOrDefault("clocks.sm")));
        Put(gpu, "mem_mhz", Number(raw.GetValueOrDefault("clocks.mem")));
        Put(gpu, "fan_pct", Number(raw.GetValueOrDefault("fan.speed")));
        Put(gpu, "power_limit_w", Number(raw.GetValueOrDefault("power.limit")));
        if (raw.TryGetValue("pstate", out var pstate) && !NotAvailable.Contains(pstate)) gpu["pstate"] = pstate;
        var mask = raw.GetValueOrDefault("clocks_event_reasons.active") ?? raw.GetValueOrDefault("clocks_throttle_reasons.active");
        gpu["reasons"] = new BsonArray(ReasonsFromMask(mask));

        Put(temp, "gpu_c", Number(raw.GetValueOrDefault("temperature.gpu")));
        Put(temp, "gpu_mem_c", Number(raw.GetValueOrDefault("temperature.memory")));
        // temperature.gpu.tlimit is the headroom (°C) left before thermal slowdown, not a temperature.
        Put(temp, "gpu_headroom_c", Number(raw.GetValueOrDefault("temperature.gpu.tlimit")));
        Put(power, "gpu_w", Number(raw.GetValueOrDefault("power.draw")));
    }

    private async Task<string?> QueryAsync(string[] fields, CancellationToken ct)
    {
        var psi = new ProcessStartInfo
        {
            FileName = _options.Executable,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        psi.ArgumentList.Add("-i");
        psi.ArgumentList.Add(_options.GpuIndex.ToString(CultureInfo.InvariantCulture));
        psi.ArgumentList.Add($"--query-gpu={string.Join(",", fields)}");
        psi.ArgumentList.Add("--format=csv,noheader,nounits");
        Process? process = null;
        try
        {
            process = Process.Start(psi);
            if (process is null) return null;
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeout.CancelAfter(TimeSpan.FromSeconds(4));
            var output = await process.StandardOutput.ReadToEndAsync(timeout.Token);
            await process.WaitForExitAsync(timeout.Token);
            if (process.ExitCode != 0) return null;
            return output.Split('\n').Select(l => l.Trim()).FirstOrDefault(l => l.Length > 0);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            _logger.LogDebug("nvidia-smi timed out");
            try { process?.Kill(entireProcessTree: true); } catch (InvalidOperationException) { }
            return null;
        }
        catch (System.ComponentModel.Win32Exception)
        {
            return null; // not installed
        }
        finally
        {
            process?.Dispose();
        }
    }
}
