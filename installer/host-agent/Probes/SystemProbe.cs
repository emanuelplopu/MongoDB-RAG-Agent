using System.Diagnostics;
using System.Runtime.InteropServices;
using MongoDB.Bson;

namespace RecallHub.HostAgent.Probes;

/// <summary>
/// Host CPU, memory, drives, disk I/O, network and uptime from Windows performance counters
/// and the Win32 API. CPU uses "% Processor Utility" (what Task Manager shows), clamped to 0-100.
/// Not thread-safe: the collector calls <see cref="Sample"/> from one tick at a time.
/// </summary>
public sealed class SystemProbe : IDisposable
{
    private const string ProcessorCategory = "Processor Information";
    private static readonly TimeSpan InstanceRefresh = TimeSpan.FromSeconds(60);

    private readonly ILogger _logger;
    private readonly string _primaryDrive;
    private readonly PerformanceCounter? _cpuTotal;
    private readonly PerformanceCounter? _cpuPerf;
    private readonly PerformanceCounter? _cpuBaseFreq;
    private readonly List<PerformanceCounter> _cores = new();
    private readonly PerformanceCounter? _diskRead;
    private readonly PerformanceCounter? _diskWrite;
    private readonly PerformanceCounter? _diskIdle;
    private readonly List<(PerformanceCounter Rx, PerformanceCounter Tx)> _nics = new();
    private DateTime _nicsRefreshedAt = DateTime.MinValue;

    public SystemProbe(ILogger logger, string? primaryDrive)
    {
        _logger = logger;
        // A JSON null in appsettings binds as "", so blank means "use the system drive".
        _primaryDrive = NormalizeDrive(string.IsNullOrWhiteSpace(primaryDrive)
            ? Path.GetPathRoot(Environment.SystemDirectory) ?? "C:\\"
            : primaryDrive);
        _cpuTotal = TryCounter(ProcessorCategory, "% Processor Utility", "_Total");
        _cpuPerf = TryCounter(ProcessorCategory, "% Processor Performance", "_Total");
        _cpuBaseFreq = TryCounter(ProcessorCategory, "Processor Frequency", "_Total");
        _diskRead = TryCounter("PhysicalDisk", "Disk Read Bytes/sec", "_Total");
        _diskWrite = TryCounter("PhysicalDisk", "Disk Write Bytes/sec", "_Total");
        _diskIdle = TryCounter("PhysicalDisk", "% Idle Time", "_Total");
        try
        {
            var instances = new PerformanceCounterCategory(ProcessorCategory).GetInstanceNames()
                .Where(n => !n.Contains("_Total", StringComparison.OrdinalIgnoreCase))
                .OrderBy(n => CoreSortKey(n))
                .ToList();
            foreach (var name in instances)
            {
                var counter = TryCounter(ProcessorCategory, "% Processor Utility", name);
                if (counter is not null) _cores.Add(counter);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning("Per-core CPU counters unavailable: {Message}", ex.Message);
        }
        RefreshNics();
    }

    /// <summary>One sample of the host figures; failing parts are recorded in <paramref name="errors"/>.</summary>
    public void Sample(BsonDocument doc, BsonDocument errors)
    {
        try
        {
            var cpu = new BsonDocument { { "logical", Environment.ProcessorCount } };
            if (_cpuTotal is not null) cpu["pct"] = Round(Math.Clamp(_cpuTotal.NextValue(), 0f, 100f));
            if (_cores.Count > 0)
            {
                cpu["per_core"] = new BsonArray(_cores.Select(c => (BsonValue)Round(Math.Clamp(c.NextValue(), 0f, 100f))));
            }
            if (_cpuPerf is not null && _cpuBaseFreq is not null)
            {
                var baseMhz = _cpuBaseFreq.NextValue();
                var perf = _cpuPerf.NextValue();
                if (baseMhz > 0 && perf > 0) cpu["freq_mhz"] = Math.Round(baseMhz * perf / 100.0);
            }
            doc["cpu"] = cpu;
        }
        catch (Exception ex) { errors["cpu"] = ex.Message; }

        try
        {
            var status = new MemoryStatusEx();
            if (GlobalMemoryStatusEx(status))
            {
                var used = status.ullTotalPhys - status.ullAvailPhys;
                doc["mem"] = new BsonDocument
                {
                    { "used_bytes", (long)used },
                    { "total_bytes", (long)status.ullTotalPhys },
                    { "pct", Round(status.ullTotalPhys == 0 ? 0 : used * 100.0 / status.ullTotalPhys) },
                    { "commit_used_bytes", (long)(status.ullTotalPageFile - status.ullAvailPageFile) },
                    { "commit_limit_bytes", (long)status.ullTotalPageFile },
                };
            }
        }
        catch (Exception ex) { errors["mem"] = ex.Message; }

        doc["uptime_s"] = Math.Round(Environment.TickCount64 / 1000.0, 1);

        try
        {
            var disks = new BsonArray();
            foreach (var drive in DriveInfo.GetDrives())
            {
                if (drive.DriveType != DriveType.Fixed || !drive.IsReady) continue;
                var used = drive.TotalSize - drive.TotalFreeSpace;
                disks.Add(new BsonDocument
                {
                    { "path", drive.Name },
                    { "used_bytes", used },
                    { "total_bytes", drive.TotalSize },
                    { "pct", Round(drive.TotalSize == 0 ? 0 : used * 100.0 / drive.TotalSize) },
                    { "primary", string.Equals(NormalizeDrive(drive.Name), _primaryDrive, StringComparison.OrdinalIgnoreCase) },
                });
            }
            doc["disks"] = disks;
        }
        catch (Exception ex) { errors["disks"] = ex.Message; }

        try
        {
            if (_diskRead is not null && _diskWrite is not null)
            {
                var io = new BsonDocument
                {
                    { "read_bps", Math.Round(_diskRead.NextValue()) },
                    { "write_bps", Math.Round(_diskWrite.NextValue()) },
                };
                if (_diskIdle is not null) io["busy_pct"] = Round(Math.Clamp(100f - _diskIdle.NextValue(), 0f, 100f));
                doc["disk_io"] = io;
            }
        }
        catch (Exception ex) { errors["disk_io"] = ex.Message; }

        try
        {
            if (DateTime.UtcNow - _nicsRefreshedAt > InstanceRefresh) RefreshNics();
            double rx = 0, tx = 0;
            foreach (var (rxCounter, txCounter) in _nics)
            {
                rx += rxCounter.NextValue();
                tx += txCounter.NextValue();
            }
            doc["net"] = new BsonDocument { { "rx_bps", Math.Round(rx) }, { "tx_bps", Math.Round(tx) } };
        }
        catch (Exception ex) { errors["net"] = ex.Message; }
    }

    /// <summary>Physical-ish adapters only: virtual WSL/Hyper-V switches would double-count container traffic.</summary>
    internal static bool IsCountedNic(string instance) =>
        !instance.Contains("isatap", StringComparison.OrdinalIgnoreCase)
        && !instance.Contains("Loopback", StringComparison.OrdinalIgnoreCase)
        && !instance.Contains("vEthernet", StringComparison.OrdinalIgnoreCase)
        && !instance.Contains("Teredo", StringComparison.OrdinalIgnoreCase);

    /// <summary>Sort key for "group,index" processor instance names so cores come out in order.</summary>
    internal static (int, int, string) CoreSortKey(string instance)
    {
        var parts = instance.Split(',');
        return parts.Length == 2 && int.TryParse(parts[0], out var group) && int.TryParse(parts[1], out var index)
            ? (group, index, instance)
            : (int.MaxValue, int.MaxValue, instance);
    }

    private void RefreshNics()
    {
        foreach (var (rx, tx) in _nics) { rx.Dispose(); tx.Dispose(); }
        _nics.Clear();
        _nicsRefreshedAt = DateTime.UtcNow;
        try
        {
            foreach (var name in new PerformanceCounterCategory("Network Interface").GetInstanceNames().Where(IsCountedNic))
            {
                var rx = TryCounter("Network Interface", "Bytes Received/sec", name);
                var tx = TryCounter("Network Interface", "Bytes Sent/sec", name);
                if (rx is not null && tx is not null) _nics.Add((rx, tx));
            }
        }
        catch (Exception ex)
        {
            _logger.LogDebug("Network counters unavailable: {Message}", ex.Message);
        }
    }

    private PerformanceCounter? TryCounter(string category, string counter, string instance)
    {
        try
        {
            var pc = new PerformanceCounter(category, counter, instance, readOnly: true);
            pc.NextValue(); // prime: rate counters return 0 on the first read
            return pc;
        }
        catch (Exception ex)
        {
            _logger.LogDebug("Counter \\{Category}({Instance})\\{Counter} unavailable: {Message}", category, instance, counter, ex.Message);
            return null;
        }
    }

    private static string NormalizeDrive(string drive) => drive.TrimEnd('\\', '/').ToUpperInvariant() + "\\";

    private static double Round(double value) => Math.Round(value, 1);

    public void Dispose()
    {
        _cpuTotal?.Dispose();
        _cpuPerf?.Dispose();
        _cpuBaseFreq?.Dispose();
        _diskRead?.Dispose();
        _diskWrite?.Dispose();
        _diskIdle?.Dispose();
        foreach (var c in _cores) c.Dispose();
        foreach (var (rx, tx) in _nics) { rx.Dispose(); tx.Dispose(); }
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
    private sealed class MemoryStatusEx
    {
        public uint dwLength = (uint)Marshal.SizeOf<MemoryStatusEx>();
        public uint dwMemoryLoad;
        public ulong ullTotalPhys;
        public ulong ullAvailPhys;
        public ulong ullTotalPageFile;
        public ulong ullAvailPageFile;
        public ulong ullTotalVirtual;
        public ulong ullAvailVirtual;
        public ulong ullAvailExtendedVirtual;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GlobalMemoryStatusEx([In, Out] MemoryStatusEx buffer);
}
