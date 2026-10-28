using MongoDB.Bson;
using MongoDB.Driver;

namespace RecallHub.HostAgent.Storage;

/// <summary>Shared MongoDB client for the telemetry database (short timeouts: the stack may be down).</summary>
public sealed class MongoContext
{
    public const string HostSamples = "host_samples";
    public const string ActivityCurrent = "activity_current";
    public const string HostControl = "host_control";

    public MongoContext(AgentOptions options)
    {
        var settings = MongoClientSettings.FromConnectionString(options.MongoUri);
        // Short defaults (the stack may be down); explicit values in the URI win.
        if (!options.MongoUri.Contains("serverSelectionTimeoutMS", StringComparison.OrdinalIgnoreCase))
        {
            settings.ServerSelectionTimeout = TimeSpan.FromSeconds(3);
        }
        if (!options.MongoUri.Contains("connectTimeoutMS", StringComparison.OrdinalIgnoreCase))
        {
            settings.ConnectTimeout = TimeSpan.FromSeconds(3);
        }
        settings.ApplicationName = "recallhub-host-agent";
        Client = new MongoClient(settings);
        Database = Client.GetDatabase(options.Database);
    }

    public IMongoClient Client { get; }

    public IMongoDatabase Database { get; }
}

/// <summary>
/// Writes samples to the host_samples time-series collection. Creates the collection when the
/// backend has not done so yet (the backend owns the TTL afterwards). While MongoDB is unreachable,
/// samples are buffered in memory (oldest dropped first) and flushed on the next successful write.
/// </summary>
public sealed class MongoSampleSink
{
    private const int NamespaceExists = 48;
    private static readonly TimeSpan WarnEvery = TimeSpan.FromMinutes(5);

    private readonly ILogger _logger;
    private readonly AgentOptions _options;
    private readonly IMongoCollection<BsonDocument> _samples;
    private readonly IMongoDatabase _db;
    private readonly Queue<BsonDocument> _buffer = new();
    private bool _ready;
    private DateTime _lastWarning = DateTime.MinValue;

    public MongoSampleSink(ILogger logger, AgentOptions options, MongoContext mongo)
    {
        _logger = logger;
        _options = options;
        _db = mongo.Database;
        _samples = _db.GetCollection<BsonDocument>(MongoContext.HostSamples);
    }

    /// <summary>Samples waiting for MongoDB.</summary>
    public int Buffered => _buffer.Count;

    public async Task WriteAsync(BsonDocument sample, CancellationToken ct)
    {
        try
        {
            if (!_ready) _ready = await EnsureCollectionAsync(ct);
            if (_buffer.Count > 0)
            {
                var pending = _buffer.ToList();
                await _samples.InsertManyAsync(pending, new InsertManyOptions { IsOrdered = false }, ct);
                _buffer.Clear();
                _logger.LogInformation("Flushed {Count} buffered samples to MongoDB", pending.Count);
            }
            await _samples.InsertOneAsync(sample, cancellationToken: ct);
        }
        catch (Exception ex) when (ex is MongoException or TimeoutException)
        {
            Buffer(sample);
            if (DateTime.UtcNow - _lastWarning > WarnEvery)
            {
                _lastWarning = DateTime.UtcNow;
                _logger.LogWarning("MongoDB write failed ({Message}); {Count} samples buffered", ex.Message, _buffer.Count);
            }
        }
    }

    private void Buffer(BsonDocument sample)
    {
        _buffer.Enqueue(sample);
        while (_buffer.Count > Math.Max(1, _options.BufferMaxSamples)) _buffer.Dequeue();
    }

    private async Task<bool> EnsureCollectionAsync(CancellationToken ct)
    {
        var filter = new BsonDocument("name", MongoContext.HostSamples);
        using var cursor = await _db.ListCollectionsAsync(new ListCollectionsOptions { Filter = filter }, ct);
        var existing = await cursor.FirstOrDefaultAsync(ct);
        if (existing is not null)
        {
            if (existing.GetValue("type", "collection").AsString != "timeseries")
            {
                _logger.LogWarning("{Collection} exists but is not a time-series collection; writing anyway", MongoContext.HostSamples);
            }
            return true;
        }
        try
        {
            await _db.CreateCollectionAsync(MongoContext.HostSamples, new CreateCollectionOptions
            {
                TimeSeriesOptions = new TimeSeriesOptions("ts", "meta", TimeSeriesGranularity.Seconds),
                ExpireAfter = TimeSpan.FromDays(Math.Max(1, _options.HostSamplesTtlDays)),
            }, ct);
            _logger.LogInformation("Created time-series collection {Database}.{Collection}", _options.Database, MongoContext.HostSamples);
        }
        catch (MongoCommandException ex) when (ex.Code == NamespaceExists)
        {
            // Created concurrently by the backend.
        }
        return true;
    }
}

/// <summary>
/// Reads activity_current (maintained by the backend's activity recorder) so every sample says what
/// was running: ingestion job, phase, step, file, and whether chat requests were active.
/// </summary>
public sealed class ActivityReader
{
    private static readonly string[] CopiedFields =
    {
        "tenant", "source", "job_id", "job_status", "phase", "step", "current_file", "processed_files", "total_files",
    };

    private readonly IMongoCollection<BsonDocument> _current;
    private readonly int _freshSeconds;

    public ActivityReader(AgentOptions options, MongoContext mongo)
    {
        _current = mongo.Database.GetCollection<BsonDocument>(MongoContext.ActivityCurrent);
        _freshSeconds = Math.Max(5, options.ActivityFreshSeconds);
    }

    public async Task<BsonArray?> SampleAsync(BsonDocument errors, CancellationToken ct)
    {
        try
        {
            var now = DateTime.UtcNow;
            var filter = Builders<BsonDocument>.Filter.Gte("updated_at", now.AddSeconds(-_freshSeconds));
            var docs = await _current.Find(filter).ToListAsync(ct);
            var activity = new BsonArray(docs.Select(d => ToActivity(d, now)).Where(a => a is not null));
            return activity.Count > 0 ? activity : null;
        }
        catch (Exception ex) when (ex is MongoException or TimeoutException)
        {
            errors["activity"] = ex.Message;
            return null;
        }
    }

    /// <summary>Copies the reportable fields; null when nothing is running (no job, no recent chat).</summary>
    internal static BsonDocument? ToActivity(BsonDocument current, DateTime nowUtc)
    {
        var chatActive = current.TryGetValue("chat_last_active_at", out var chat)
            && chat.IsValidDateTime
            && nowUtc - chat.ToUniversalTime() < TimeSpan.FromSeconds(60);
        var hasJob = current.TryGetValue("job_id", out var job) && !job.IsBsonNull;
        if (!hasJob && !chatActive) return null;
        var doc = new BsonDocument();
        foreach (var field in CopiedFields)
        {
            if (current.TryGetValue(field, out var value) && !value.IsBsonNull) doc[field] = value;
        }
        doc["chat_active"] = chatActive;
        return doc;
    }
}
