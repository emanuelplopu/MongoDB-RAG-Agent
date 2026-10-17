using MongoDB.Bson;
using MongoDB.Bson.IO;
using RecallHub.HostAgent.Storage;

namespace RecallHub.HostAgent;

/// <summary>How samples leave the agent.</summary>
public enum OutputMode
{
    /// <summary>Write to MongoDB (normal service operation).</summary>
    Mongo,

    /// <summary>Print every sample as JSON, never write (verification).</summary>
    DryRun,

    /// <summary>Print one sample as JSON and exit.</summary>
    Once,
}

/// <summary>Samples on a fixed cadence (PeriodicTimer: an overrunning tick skips, never piles up).</summary>
public sealed class AgentWorker : BackgroundService
{
    private static readonly JsonWriterSettings JsonSettings = new() { OutputMode = JsonOutputMode.RelaxedExtendedJson, Indent = true };

    private readonly ILogger<AgentWorker> _logger;
    private readonly AgentOptions _options;
    private readonly SampleCollector _collector;
    private readonly MongoSampleSink? _sink;
    private readonly OutputMode _mode;
    private readonly IHostApplicationLifetime _lifetime;

    public AgentWorker(
        ILogger<AgentWorker> logger,
        AgentOptions options,
        SampleCollector collector,
        IHostApplicationLifetime lifetime,
        OutputMode mode,
        MongoSampleSink? sink = null)
    {
        _logger = logger;
        _options = options;
        _collector = collector;
        _lifetime = lifetime;
        _mode = mode;
        _sink = sink;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _logger.LogInformation(
            "RecallHub host agent {Version} sampling every {Interval}s ({Mode}) into {Database}",
            SampleCollector.AgentVersion, _options.IntervalSeconds, _mode, _options.Database);
        try
        {
            using var timer = new PeriodicTimer(TimeSpan.FromSeconds(_options.IntervalSeconds));
            do
            {
                var sample = await _collector.CollectAsync(stoppingToken);
                if (_mode == OutputMode.Mongo && _sink is not null)
                {
                    await _sink.WriteAsync(sample, stoppingToken);
                }
                else
                {
                    Console.WriteLine(sample.ToJson(JsonSettings));
                }
                if (_mode == OutputMode.Once) break;
            }
            while (await timer.WaitForNextTickAsync(stoppingToken));
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
        {
            // normal shutdown
        }
        finally
        {
            if (_mode == OutputMode.Once) _lifetime.StopApplication();
        }
    }
}
