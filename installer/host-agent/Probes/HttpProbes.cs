using System.Diagnostics;
using System.Text.Json;
using MongoDB.Bson;

namespace RecallHub.HostAgent.Probes;

/// <summary>Models resident in Ollama on the host (GET /api/ps).</summary>
public sealed class OllamaProbe : IDisposable
{
    private readonly HttpClient? _client;

    public OllamaProbe(string? baseUrl)
    {
        if (string.IsNullOrWhiteSpace(baseUrl)) return;
        _client = new HttpClient { BaseAddress = new Uri(baseUrl.TrimEnd('/') + "/"), Timeout = TimeSpan.FromSeconds(2) };
    }

    public async Task<BsonDocument?> SampleAsync(BsonDocument errors, CancellationToken ct)
    {
        if (_client is null) return null;
        try
        {
            using var response = await _client.GetAsync("api/ps", ct);
            if (!response.IsSuccessStatusCode) return null;
            await using var stream = await response.Content.ReadAsStreamAsync(ct);
            using var json = await JsonDocument.ParseAsync(stream, cancellationToken: ct);
            return new BsonDocument { { "models", ParseModels(json.RootElement) } };
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            if (ct.IsCancellationRequested) throw;
            errors["ollama"] = ex.Message;
            return null;
        }
    }

    internal static BsonArray ParseModels(JsonElement root)
    {
        var models = new BsonArray();
        if (!root.TryGetProperty("models", out var list) || list.ValueKind != JsonValueKind.Array) return models;
        foreach (var m in list.EnumerateArray())
        {
            var doc = new BsonDocument();
            if (m.TryGetProperty("name", out var name)) doc["name"] = name.GetString() ?? "";
            if (m.TryGetProperty("size", out var size) && size.ValueKind == JsonValueKind.Number) doc["size_bytes"] = size.GetInt64();
            if (m.TryGetProperty("size_vram", out var vram) && vram.ValueKind == JsonValueKind.Number) doc["vram_bytes"] = vram.GetInt64();
            if (m.TryGetProperty("context_length", out var ctx) && ctx.ValueKind == JsonValueKind.Number) doc["ctx"] = ctx.GetInt64();
            models.Add(doc);
        }
        return models;
    }

    public void Dispose() => _client?.Dispose();
}

/// <summary>
/// Round-trip latency of the backends' /health endpoints. One keep-alive connection per target, so the
/// figure reflects the backend's event loop rather than connection setup (a blocked loop shows up here).
/// </summary>
public sealed class HealthProbe : IDisposable
{
    private readonly List<HealthTarget> _targets;
    private readonly HttpClient _client = new(new SocketsHttpHandler { PooledConnectionLifetime = TimeSpan.FromMinutes(10) })
    {
        // Below the 5 s default interval so a stalled backend cannot stretch the sampling cadence.
        Timeout = TimeSpan.FromSeconds(4),
    };

    public HealthProbe(IEnumerable<HealthTarget> targets)
    {
        _targets = targets.Where(t => !string.IsNullOrWhiteSpace(t.Url)).ToList();
    }

    public async Task<BsonArray?> SampleAsync(CancellationToken ct)
    {
        if (_targets.Count == 0) return null;
        var results = await Task.WhenAll(_targets.Select(t => ProbeAsync(t, ct)));
        return new BsonArray(results);
    }

    private async Task<BsonDocument> ProbeAsync(HealthTarget target, CancellationToken ct)
    {
        var watch = Stopwatch.StartNew();
        var doc = new BsonDocument { { "name", string.IsNullOrWhiteSpace(target.Name) ? target.Url : target.Name } };
        try
        {
            using var response = await _client.GetAsync(target.Url, HttpCompletionOption.ResponseHeadersRead, ct);
            doc["ok"] = response.IsSuccessStatusCode;
            doc["status"] = (int)response.StatusCode;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            if (ct.IsCancellationRequested) throw;
            doc["ok"] = false;
            doc["error"] = ex is TaskCanceledException ? "timeout" : ex.Message;
        }
        doc["ms"] = Math.Round(watch.Elapsed.TotalMilliseconds, 1);
        return doc;
    }

    public void Dispose() => _client.Dispose();
}
