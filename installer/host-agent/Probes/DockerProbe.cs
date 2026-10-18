using System.IO.Pipes;
using System.Text.Json;
using MongoDB.Bson;

namespace RecallHub.HostAgent.Probes;

/// <summary>Raw counters of one container from a single Engine API stats read.</summary>
internal sealed record ContainerCounters(
    ulong CpuTotalNs,
    ulong SystemCpuNs,
    uint OnlineCpus,
    long? MemBytes,
    long? MemLimitBytes,
    long BlkReadBytes,
    long BlkWriteBytes,
    long NetRxBytes,
    long NetTxBytes);

/// <summary>
/// Per-container CPU, memory, block and network I/O plus restart count and health, read from the
/// Docker Engine API over Docker Desktop's named pipe (no docker CLI process per sample).
/// Stats are read with one-shot=true; CPU is computed against this probe's previous tick and
/// reported as cores in use (1.0 = one full core), which docker stats shows as 100 %.
/// </summary>
public sealed class DockerProbe : IDisposable
{
    private static readonly TimeSpan ReconnectBackoff = TimeSpan.FromSeconds(30);

    private readonly ILogger _logger;
    private readonly DockerOptions _options;
    private readonly System.Collections.Concurrent.ConcurrentDictionary<string, (ulong Total, ulong System)> _previous = new();
    private HttpClient? _client;
    private DateTime _nextConnectAttempt = DateTime.MinValue;

    public DockerProbe(ILogger logger, DockerOptions options)
    {
        _logger = logger;
        _options = options;
    }

    /// <summary>One entry per running container (null when Docker is disabled or unreachable).</summary>
    public async Task<BsonArray?> SampleAsync(BsonDocument errors, CancellationToken ct)
    {
        if (!_options.Enabled) return null;
        var client = await EnsureClientAsync(ct);
        if (client is null)
        {
            errors["docker"] = "Docker engine pipe unreachable";
            return null;
        }
        try
        {
            using var list = await GetJsonAsync(client, ContainersPath(), ct);
            var containers = list.RootElement.EnumerateArray()
                .Select(c => (Id: c.GetProperty("Id").GetString() ?? "", Name: ContainerName(c)))
                .Where(c => c.Id.Length > 0)
                .ToList();
            var results = await Task.WhenAll(containers.Select(c => SampleContainerAsync(client, c.Id, c.Name, ct)));
            foreach (var gone in _previous.Keys.Except(containers.Select(c => c.Id)).ToList()) _previous.TryRemove(gone, out _);
            return new BsonArray(results.Where(r => r is not null));
        }
        catch (Exception ex) when (ex is HttpRequestException or IOException or TaskCanceledException or JsonException)
        {
            if (ct.IsCancellationRequested) throw;
            errors["docker"] = ex.Message;
            ResetClient();
            return null;
        }
    }

    private async Task<BsonDocument?> SampleContainerAsync(HttpClient client, string id, string name, CancellationToken ct)
    {
        try
        {
            var statsTask = GetJsonAsync(client, $"containers/{id}/stats?stream=false&one-shot=true", ct);
            var inspectTask = GetJsonAsync(client, $"containers/{id}/json", ct);
            using var stats = await statsTask;
            using var inspect = await inspectTask;

            var doc = new BsonDocument { { "name", name } };
            var counters = ParseStats(stats.RootElement);
            if (counters is not null)
            {
                if (_previous.TryGetValue(id, out var prev))
                {
                    var cores = CpuCores(prev.Total, prev.System, counters);
                    if (cores is not null) doc["cpu_cores"] = Math.Round(cores.Value, 3);
                }
                _previous[id] = (counters.CpuTotalNs, counters.SystemCpuNs);
                if (counters.MemBytes is not null) doc["mem_bytes"] = counters.MemBytes.Value;
                if (counters.MemLimitBytes is not null) doc["mem_limit_bytes"] = counters.MemLimitBytes.Value;
                doc["blk_read_bytes"] = counters.BlkReadBytes;
                doc["blk_write_bytes"] = counters.BlkWriteBytes;
                doc["net_rx_bytes"] = counters.NetRxBytes;
                doc["net_tx_bytes"] = counters.NetTxBytes;
            }

            var root = inspect.RootElement;
            if (root.TryGetProperty("RestartCount", out var restarts) && restarts.ValueKind == JsonValueKind.Number)
            {
                doc["restart_count"] = restarts.GetInt32();
            }
            if (root.TryGetProperty("State", out var state))
            {
                if (state.TryGetProperty("Status", out var status)) doc["state"] = status.GetString() ?? "";
                if (state.TryGetProperty("StartedAt", out var started) && DateTime.TryParse(started.GetString(),
                        null, System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal, out var startedAt))
                {
                    doc["started_at"] = new BsonDateTime(DateTime.SpecifyKind(startedAt, DateTimeKind.Utc));
                }
                if (state.TryGetProperty("Health", out var health) && health.TryGetProperty("Status", out var hs))
                {
                    doc["health"] = hs.GetString() ?? "";
                }
            }
            return doc;
        }
        catch (Exception ex) when (ex is HttpRequestException or IOException or TaskCanceledException or JsonException or KeyNotFoundException or InvalidOperationException)
        {
            if (ct.IsCancellationRequested) throw;
            _logger.LogDebug("stats for {Container} failed: {Message}", name, ex.Message);
            return new BsonDocument { { "name", name }, { "error", ex.Message } };
        }
    }

    /// <summary>Cores in use between two reads: Δcontainer / Δsystem × online CPUs (null when not computable).</summary>
    internal static double? CpuCores(ulong prevTotal, ulong prevSystem, ContainerCounters current)
    {
        if (current.CpuTotalNs < prevTotal || current.SystemCpuNs <= prevSystem || current.OnlineCpus == 0) return null;
        var cpuDelta = (double)(current.CpuTotalNs - prevTotal);
        var systemDelta = (double)(current.SystemCpuNs - prevSystem);
        return Math.Max(0, cpuDelta / systemDelta * current.OnlineCpus);
    }

    /// <summary>Parses an Engine API stats object (cgroup v1 and v2 layouts).</summary>
    internal static ContainerCounters? ParseStats(JsonElement root)
    {
        if (!root.TryGetProperty("cpu_stats", out var cpu) || !cpu.TryGetProperty("cpu_usage", out var usage)) return null;
        if (!usage.TryGetProperty("total_usage", out var totalEl) || totalEl.ValueKind != JsonValueKind.Number) return null;
        var total = totalEl.GetUInt64();
        var system = cpu.TryGetProperty("system_cpu_usage", out var sys) && sys.ValueKind == JsonValueKind.Number ? sys.GetUInt64() : 0UL;
        uint online = 0;
        if (cpu.TryGetProperty("online_cpus", out var onlineEl) && onlineEl.ValueKind == JsonValueKind.Number) online = onlineEl.GetUInt32();
        if (online == 0 && usage.TryGetProperty("percpu_usage", out var perCpu) && perCpu.ValueKind == JsonValueKind.Array) online = (uint)perCpu.GetArrayLength();

        long? mem = null, limit = null;
        if (root.TryGetProperty("memory_stats", out var memory) && memory.ValueKind == JsonValueKind.Object)
        {
            if (memory.TryGetProperty("usage", out var u) && u.ValueKind == JsonValueKind.Number)
            {
                var used = u.GetInt64();
                // docker stats semantics: page cache that can be reclaimed is not "used".
                if (memory.TryGetProperty("stats", out var ms) && ms.ValueKind == JsonValueKind.Object)
                {
                    if (ms.TryGetProperty("inactive_file", out var inactive) && inactive.ValueKind == JsonValueKind.Number) used -= inactive.GetInt64();
                    else if (ms.TryGetProperty("total_inactive_file", out var tInactive) && tInactive.ValueKind == JsonValueKind.Number) used -= tInactive.GetInt64();
                }
                mem = Math.Max(0, used);
            }
            if (memory.TryGetProperty("limit", out var l) && l.ValueKind == JsonValueKind.Number) limit = l.GetInt64();
        }

        long read = 0, write = 0;
        if (root.TryGetProperty("blkio_stats", out var blk) && blk.TryGetProperty("io_service_bytes_recursive", out var io) && io.ValueKind == JsonValueKind.Array)
        {
            foreach (var entry in io.EnumerateArray())
            {
                var op = entry.TryGetProperty("op", out var o) ? o.GetString() : null;
                var value = entry.TryGetProperty("value", out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt64() : 0;
                if (string.Equals(op, "read", StringComparison.OrdinalIgnoreCase)) read += value;
                else if (string.Equals(op, "write", StringComparison.OrdinalIgnoreCase)) write += value;
            }
        }

        long rx = 0, tx = 0;
        if (root.TryGetProperty("networks", out var nets) && nets.ValueKind == JsonValueKind.Object)
        {
            foreach (var nic in nets.EnumerateObject())
            {
                if (nic.Value.TryGetProperty("rx_bytes", out var r) && r.ValueKind == JsonValueKind.Number) rx += r.GetInt64();
                if (nic.Value.TryGetProperty("tx_bytes", out var t) && t.ValueKind == JsonValueKind.Number) tx += t.GetInt64();
            }
        }
        return new ContainerCounters(total, system, online, mem, limit, read, write, rx, tx);
    }

    internal static string ContainerName(JsonElement listEntry)
    {
        if (listEntry.TryGetProperty("Names", out var names) && names.ValueKind == JsonValueKind.Array)
        {
            foreach (var n in names.EnumerateArray())
            {
                var name = n.GetString();
                if (!string.IsNullOrEmpty(name)) return name.TrimStart('/');
            }
        }
        var fallback = listEntry.TryGetProperty("Id", out var id) ? id.GetString() ?? "" : "";
        return fallback.Length > 12 ? fallback[..12] : fallback;
    }

    private string ContainersPath()
    {
        if (string.IsNullOrWhiteSpace(_options.ProjectLabel)) return "containers/json";
        var filter = JsonSerializer.Serialize(new Dictionary<string, string[]>
        {
            ["label"] = new[] { $"com.docker.compose.project={_options.ProjectLabel}" },
        });
        return "containers/json?filters=" + Uri.EscapeDataString(filter);
    }

    private static async Task<JsonDocument> GetJsonAsync(HttpClient client, string path, CancellationToken ct)
    {
        using var response = await client.GetAsync(path, ct);
        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync(ct);
        return await JsonDocument.ParseAsync(stream, cancellationToken: ct);
    }

    private async Task<HttpClient?> EnsureClientAsync(CancellationToken ct)
    {
        if (_client is not null) return _client;
        if (DateTime.UtcNow < _nextConnectAttempt) return null;
        _nextConnectAttempt = DateTime.UtcNow + ReconnectBackoff;
        foreach (var pipe in _options.Pipes)
        {
            var client = CreatePipeClient(pipe);
            try
            {
                using var ping = await client.GetAsync("_ping", ct);
                if (ping.IsSuccessStatusCode)
                {
                    _logger.LogInformation("Docker engine reachable on pipe {Pipe}", pipe);
                    _client = client;
                    return client;
                }
            }
            catch (Exception ex) when (ex is HttpRequestException or IOException or TaskCanceledException or UnauthorizedAccessException or TimeoutException)
            {
                if (ct.IsCancellationRequested) throw;
                _logger.LogDebug("Docker pipe {Pipe} not usable: {Message}", pipe, ex.Message);
            }
            client.Dispose();
        }
        _logger.LogWarning("No Docker engine pipe reachable ({Pipes}); retrying in {Seconds}s",
            string.Join(", ", _options.Pipes), ReconnectBackoff.TotalSeconds);
        return null;
    }

    private static HttpClient CreatePipeClient(string pipe)
    {
        var handler = new SocketsHttpHandler
        {
            ConnectCallback = async (_, token) =>
            {
                var stream = new NamedPipeClientStream(".", pipe, PipeDirection.InOut, PipeOptions.Asynchronous);
                try
                {
                    await stream.ConnectAsync(2000, token);
                    return stream;
                }
                catch
                {
                    await stream.DisposeAsync();
                    throw;
                }
            },
            PooledConnectionLifetime = TimeSpan.FromMinutes(5),
        };
        return new HttpClient(handler) { BaseAddress = new Uri("http://docker/"), Timeout = TimeSpan.FromSeconds(4) };
    }

    private void ResetClient()
    {
        _client?.Dispose();
        _client = null;
        _nextConnectAttempt = DateTime.UtcNow;
    }

    public void Dispose() => _client?.Dispose();
}
