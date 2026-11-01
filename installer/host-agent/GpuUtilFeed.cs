namespace RecallHub.HostAgent;

/// <summary>
/// The last few minutes of GPU utilisation readings, shared in-process between the sample
/// collector (which reads nvidia-smi every tick for <c>host_samples.gpu.util_pct</c>) and the
/// embedder watchdog (which wants the mean utilisation between two probes for its sustained-CPU
/// rule). No second nvidia-smi process and no MongoDB round trip for a number the agent already has.
/// </summary>
public sealed class GpuUtilFeed
{
    private readonly object _lock = new();
    private readonly List<(DateTime At, double UtilPct)> _readings = new();
    private readonly TimeSpan _keep;

    public GpuUtilFeed(TimeSpan? keep = null) => _keep = keep ?? TimeSpan.FromMinutes(20);

    /// <summary>Adds one reading (UTC) and drops the ones older than the retention.</summary>
    public void Record(DateTime atUtc, double utilPct)
    {
        lock (_lock)
        {
            _readings.Add((atUtc, utilPct));
            var cutoff = atUtc - _keep;
            var drop = 0;
            while (drop < _readings.Count && _readings[drop].At < cutoff) drop++;
            if (drop > 0) _readings.RemoveRange(0, drop);
        }
    }

    /// <summary>Mean utilisation of the readings in (<paramref name="sinceUtc"/>, <paramref name="nowUtc"/>], null when there is none.</summary>
    public double? MeanSince(DateTime sinceUtc, DateTime nowUtc)
    {
        lock (_lock)
        {
            double sum = 0;
            var n = 0;
            foreach (var (at, util) in _readings)
            {
                if (at <= sinceUtc || at > nowUtc) continue;
                sum += util;
                n++;
            }
            return n == 0 ? null : Math.Round(sum / n, 1);
        }
    }

    /// <summary>The most recent reading, null when the GPU was never read.</summary>
    public double? Latest
    {
        get
        {
            lock (_lock) return _readings.Count == 0 ? null : _readings[^1].UtilPct;
        }
    }
}
