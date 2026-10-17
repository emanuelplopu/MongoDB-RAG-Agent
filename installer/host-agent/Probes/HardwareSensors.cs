using MongoDB.Bson;

namespace RecallHub.HostAgent.Probes;

/// <summary>
/// Driver-level hardware sensors (CPU package/core temperatures, fans, board and storage
/// temperatures). Implementations add temperature fields to <c>temp</c> and return the
/// <c>sensors</c> sub-document, whose <c>status</c> is ok, driver_missing, disabled or error.
/// </summary>
public interface IHardwareSensorProbe
{
    Task<BsonDocument?> SampleAsync(BsonDocument temp, BsonDocument power, BsonDocument errors, CancellationToken ct);
}

/// <summary>Used when HardwareSensors.Enabled is false.</summary>
public sealed class DisabledSensorProbe : IHardwareSensorProbe
{
    public Task<BsonDocument?> SampleAsync(BsonDocument temp, BsonDocument power, BsonDocument errors, CancellationToken ct) =>
        Task.FromResult<BsonDocument?>(new BsonDocument("status", "disabled"));
}

/// <summary>Chooses the sensor implementation from configuration.</summary>
public static class HardwareSensorFactory
{
    public static IHardwareSensorProbe Create(AgentOptions options, ILoggerFactory loggers) =>
        options.HardwareSensors.Enabled ? new DisabledSensorProbe() : new DisabledSensorProbe();
}
