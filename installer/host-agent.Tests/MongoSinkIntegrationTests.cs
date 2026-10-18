using Microsoft.Extensions.Logging.Abstractions;
using MongoDB.Bson;
using MongoDB.Driver;
using RecallHub.HostAgent.Storage;
using Xunit;

namespace RecallHub.HostAgent.Tests;

/// <summary>
/// Round trip against a real MongoDB. Runs only when RECALLHUB_TEST_MONGO_URI points at a
/// throwaway server (never the stack's database); otherwise it passes without doing anything.
/// </summary>
public class MongoSinkIntegrationTests
{
    [Fact]
    public async Task WriteAsync_CreatesTimeSeriesCollectionAndStoresSamples()
    {
        var uri = Environment.GetEnvironmentVariable("RECALLHUB_TEST_MONGO_URI");
        if (string.IsNullOrWhiteSpace(uri)) return;

        var database = "agent_it_" + Guid.NewGuid().ToString("N")[..8];
        var options = new AgentOptions { MongoUri = uri, Database = database, HostSamplesTtlDays = 2 }.Normalize();
        var mongo = new MongoContext(options);
        try
        {
            var sink = new MongoSampleSink(NullLogger.Instance, options, mongo);
            var sample = new BsonDocument
            {
                { "ts", new BsonDateTime(DateTime.UtcNow) },
                { "meta", new BsonDocument { { "host", "TEST" }, { "source", "host-agent" }, { "schema", 1 } } },
                { "cpu", new BsonDocument("pct", 12.5) },
            };
            await sink.WriteAsync(sample, CancellationToken.None);

            using var cursor = await mongo.Database.ListCollectionsAsync(
                new ListCollectionsOptions { Filter = new BsonDocument("name", MongoContext.HostSamples) });
            var info = await cursor.FirstAsync();
            Assert.Equal("timeseries", info["type"].AsString);
            Assert.Equal(2 * 86400, info["options"]["expireAfterSeconds"].ToInt64());

            var stored = await mongo.Database.GetCollection<BsonDocument>(MongoContext.HostSamples)
                .Find(FilterDefinition<BsonDocument>.Empty).FirstAsync();
            Assert.Equal(12.5, stored["cpu"]["pct"].AsDouble);
            Assert.Equal(0, sink.Buffered);
        }
        finally
        {
            await mongo.Client.DropDatabaseAsync(database);
        }
    }

    [Fact]
    public async Task WriteAsync_BuffersWhileMongoIsUnreachable()
    {
        // Port 1 is never a MongoDB server: every write fails fast and lands in the buffer.
        var options = new AgentOptions
        {
            MongoUri = "mongodb://127.0.0.1:1/?directConnection=true&serverSelectionTimeoutMS=200",
            BufferMaxSamples = 2,
        }.Normalize();
        var sink = new MongoSampleSink(NullLogger.Instance, options, new MongoContext(options));

        for (var i = 0; i < 3; i++)
        {
            await sink.WriteAsync(new BsonDocument { { "ts", new BsonDateTime(DateTime.UtcNow) }, { "i", i } }, CancellationToken.None);
        }

        Assert.Equal(2, sink.Buffered); // oldest dropped beyond BufferMaxSamples
    }
}
