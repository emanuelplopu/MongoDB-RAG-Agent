using System.IO.Pipes;
using System.Text.Json;

namespace RecallHub.HostAgent.Probes;

/// <summary>
/// HTTP access to the Docker Engine API over Docker Desktop's named pipe, shared by the container
/// probe and the embedder watchdog. One instance keeps one pooled pipe connection; a failed call
/// drops it and the next call reconnects (after a short backoff when no pipe answers).
/// </summary>
public sealed class DockerEngine : IDisposable
{
    private static readonly TimeSpan ReconnectBackoff = TimeSpan.FromSeconds(30);

    private readonly ILogger _logger;
    private readonly IReadOnlyList<string> _pipes;
    private readonly TimeSpan _timeout;
    private HttpClient? _client;
    private DateTime _nextConnectAttempt = DateTime.MinValue;

    public DockerEngine(ILogger logger, IReadOnlyList<string> pipes, TimeSpan? timeout = null)
    {
        _logger = logger;
        _pipes = pipes;
        _timeout = timeout ?? TimeSpan.FromSeconds(4);
    }

    /// <summary>The connected client, or null while no engine pipe answers (retried every 30 s).</summary>
    public async Task<HttpClient?> EnsureClientAsync(CancellationToken ct)
    {
        if (_client is not null) return _client;
        if (DateTime.UtcNow < _nextConnectAttempt) return null;
        _nextConnectAttempt = DateTime.UtcNow + ReconnectBackoff;
        foreach (var pipe in _pipes)
        {
            var client = CreatePipeClient(pipe, _timeout);
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
            string.Join(", ", _pipes), ReconnectBackoff.TotalSeconds);
        return null;
    }

    /// <summary>Drops the connection; the next call reconnects at once.</summary>
    public void Reset()
    {
        _client?.Dispose();
        _client = null;
        _nextConnectAttempt = DateTime.UtcNow;
    }

    /// <summary>GET a JSON document; the caller disposes it.</summary>
    public static async Task<JsonDocument> GetJsonAsync(HttpClient client, string path, CancellationToken ct)
    {
        using var response = await client.GetAsync(path, ct);
        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync(ct);
        return await JsonDocument.ParseAsync(stream, cancellationToken: ct);
    }

    public static HttpClient CreatePipeClient(string pipe, TimeSpan timeout)
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
        return new HttpClient(handler) { BaseAddress = new Uri("http://docker/"), Timeout = timeout };
    }

    public void Dispose() => _client?.Dispose();
}
