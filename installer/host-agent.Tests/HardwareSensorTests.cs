using LibreHardwareMonitor.Hardware;
using RecallHub.HostAgent.Probes;
using Xunit;

namespace RecallHub.HostAgent.Tests;

public class HardwareSensorMappingTests
{
    private static SensorReading Cpu(string name, float? value) =>
        new(HardwareType.Cpu, "Intel Core Ultra 9 285HX", SensorType.Temperature, name, value);

    [Fact]
    public void Map_IntelPackageCoreMaxBoardFansAndStorage()
    {
        var readings = new[]
        {
            Cpu("CPU Package", 61.4f),
            Cpu("Core Max", 66f),
            Cpu("Core Average", 52.25f),
            Cpu("CPU Core #1", 55f),
            new SensorReading(HardwareType.Cpu, "CPU", SensorType.Power, "CPU Package", 22f),
            new SensorReading(HardwareType.SuperIO, "Nuvoton NCT6799D", SensorType.Temperature, "System", 34f),
            new SensorReading(HardwareType.SuperIO, "Nuvoton NCT6799D", SensorType.Temperature, "PCH", 48f),
            new SensorReading(HardwareType.SuperIO, "Nuvoton NCT6799D", SensorType.Fan, "Fan #1", 1250f),
            new SensorReading(HardwareType.Storage, "Samsung SSD 990 PRO 4TB", SensorType.Temperature, "Composite Temperature", 41f),
            new SensorReading(HardwareType.Storage, "Samsung SSD 990 PRO 4TB", SensorType.Temperature, "Temperature #2", 52f),
        };

        var (temp, sensors) = LhmSensorProbe.Map(readings, elevated: true);

        Assert.Equal(61.4, temp["cpu_pkg_c"].AsDouble);
        Assert.Equal(66.0, temp["cpu_core_max_c"].AsDouble);
        Assert.Equal(52.2, temp["cpu_core_avg_c"].AsDouble, 1); // banker's rounding of 52.25, as in Python
        Assert.Equal(48.0, temp["board_max_c"].AsDouble);
        Assert.Equal(41.0, temp["storage_max_c"].AsDouble); // composite, not the hotter per-sensor value
        Assert.Equal("ok", sensors["status"].AsString);
        Assert.Equal(1250.0, sensors["fans"][0]["rpm"].AsDouble);
        Assert.Equal("Samsung SSD 990 PRO 4TB", sensors["storage"][0]["name"].AsString);
    }

    [Fact]
    public void Map_AmdTctlCountsAsPackage()
    {
        var (temp, _) = LhmSensorProbe.Map(new[] { Cpu("Core (Tctl/Tdie)", 70f) }, elevated: true);
        Assert.Equal(70.0, temp["cpu_pkg_c"].AsDouble);
    }

    [Fact]
    public void Map_PerCoreOnlyFallsBackToHottestCore()
    {
        var (temp, _) = LhmSensorProbe.Map(new[] { Cpu("CPU Core #1", 50f), Cpu("CPU Core #2", 58f) }, elevated: true);
        Assert.Equal(58.0, temp["cpu_pkg_c"].AsDouble);
        Assert.Equal(58.0, temp["cpu_core_max_c"].AsDouble);
    }

    [Theory]
    [InlineData(true, "driver_missing")]
    [InlineData(false, "not_elevated")]
    public void Map_NoCpuTemperatureExplainsWhy(bool elevated, string status)
    {
        // Without PawnIO the CPU sensors exist but read null/0.
        var (temp, sensors) = LhmSensorProbe.Map(new[] { Cpu("CPU Package", null), Cpu("Core Max", 0f) }, elevated);
        Assert.False(temp.Contains("cpu_pkg_c"));
        Assert.Equal(status, sensors["status"].AsString);
        Assert.Equal(elevated, sensors["elevated"].AsBoolean);
    }

    private static SensorReading Disk(string drive, string name, float? value, string? id = null) =>
        new(HardwareType.Storage, drive, SensorType.Temperature, name, value, id);

    /// <summary>LHM 0.9.6 on this PC (2026-10-18): Windows reported 42 / 43 °C, the agent wrote the thresholds.</summary>
    [Fact]
    public void Map_StorageIgnoresWarningAndCriticalThresholds()
    {
        var readings = new[]
        {
            Disk("CT4000P310SSD8", "Composite Temperature", 42f, "/nvme/0"),
            Disk("CT4000P310SSD8", "Temperature #1", 42f, "/nvme/0"),
            Disk("CT4000P310SSD8", "Warning Temperature", 87f, "/nvme/0"),
            Disk("CT4000P310SSD8", "Critical Temperature", 90f, "/nvme/0"),
            Disk("CT1000P310SSD8", "Composite Temperature", 43f, "/nvme/1"),
            Disk("CT1000P310SSD8", "Warning Temperature", 84f, "/nvme/1"),
            Disk("CT1000P310SSD8", "Critical Temperature", 0f, "/nvme/1"), // unset threshold reads 0
            Disk("CT1000P310SSD8", "Temperature Limit", 80f, "/nvme/1"),
            Disk("CT1000P310SSD8", "Max Temperature Threshold", 85f, "/nvme/1"),
        };

        var (temp, sensors) = LhmSensorProbe.Map(readings, elevated: true);

        Assert.Equal(43.0, temp["storage_max_c"].AsDouble);
        var storage = sensors["storage"].AsBsonArray;
        Assert.Equal(2, storage.Count);
        Assert.Equal("CT4000P310SSD8", storage[0]["name"].AsString);
        Assert.Equal(42.0, storage[0]["c"].AsDouble);
        Assert.Equal("CT1000P310SSD8", storage[1]["name"].AsString);
        Assert.Equal(43.0, storage[1]["c"].AsDouble);
    }

    /// <summary>
    /// Rule: the NVMe composite temperature represents the drive (it is what the controller throttles
    /// on and what Windows / SMART tools show), even when a single sensor such as the controller reads hotter.
    /// </summary>
    [Fact]
    public void Map_StorageCompositeWinsOverHotterPerSensorValues()
    {
        var readings = new[]
        {
            Disk("Samsung SSD 990 PRO 4TB", "Temperature #1", 48f),
            Disk("Samsung SSD 990 PRO 4TB", "Temperature #2", 57f),
            Disk("Samsung SSD 990 PRO 4TB", "Composite Temperature", 45f),
        };

        var (temp, sensors) = LhmSensorProbe.Map(readings, elevated: true);

        Assert.Equal(45.0, temp["storage_max_c"].AsDouble);
        Assert.Equal(45.0, sensors["storage"][0]["c"].AsDouble);
    }

    [Fact]
    public void Map_StoragePlainTemperatureWinsOverPerSensorValues()
    {
        var (temp, _) = LhmSensorProbe.Map(new[]
        {
            Disk("Drive", "Temperature Sensor 1", 51f),
            Disk("Drive", "Temperature", 38f),
        }, elevated: true);

        Assert.Equal(38.0, temp["storage_max_c"].AsDouble);
    }

    [Fact]
    public void Map_StorageFallsBackToHottestPerSensorWhenCompositeUnreadable()
    {
        var (temp, sensors) = LhmSensorProbe.Map(new[]
        {
            Disk("WD_BLACK SN850X 2000GB", "Composite Temperature", 0f), // LHM shows 0 when SMART is null
            Disk("WD_BLACK SN850X 2000GB", "Temperature #1", 39f),
            Disk("WD_BLACK SN850X 2000GB", "Temperature #2", 47.5f),
            Disk("WD_BLACK SN850X 2000GB", "Warning Temperature", 90f),
        }, elevated: true);

        Assert.Equal(47.5, temp["storage_max_c"].AsDouble);
        Assert.Equal(47.5, sensors["storage"][0]["c"].AsDouble);
    }

    [Fact]
    public void Map_SataDriveUsesPlainTemperature()
    {
        var (temp, sensors) = LhmSensorProbe.Map(new[] { Disk("Samsung SSD 870 EVO 1TB", "Temperature", 31f) }, elevated: true);
        Assert.Equal(31.0, temp["storage_max_c"].AsDouble);
        Assert.Equal("Samsung SSD 870 EVO 1TB", sensors["storage"][0]["name"].AsString);
    }

    [Fact]
    public void Map_DriveWithOnlyThresholdSensorsHasNoStorageEntry()
    {
        var (temp, sensors) = LhmSensorProbe.Map(new[]
        {
            Disk("CT4000P310SSD8", "Composite Temperature", null, "/nvme/0"),
            Disk("CT4000P310SSD8", "Warning Temperature", 87f, "/nvme/0"),
            Disk("CT4000P310SSD8", "Critical Temperature", 90f, "/nvme/0"),
        }, elevated: true);

        Assert.False(temp.Contains("storage_max_c"));
        Assert.False(sensors.Contains("storage"));
    }

    [Fact]
    public void Map_IdenticalDriveModelsStaySeparate()
    {
        var (temp, sensors) = LhmSensorProbe.Map(new[]
        {
            Disk("CT1000P310SSD8", "Composite Temperature", 40f, "/nvme/0"),
            Disk("CT1000P310SSD8", "Composite Temperature", 44f, "/nvme/1"),
        }, elevated: true);

        Assert.Equal(44.0, temp["storage_max_c"].AsDouble);
        Assert.Equal(new[] { 40.0, 44.0 }, sensors["storage"].AsBsonArray.Select(d => d["c"].AsDouble));
    }

    [Theory]
    [InlineData("Composite Temperature", 0)]
    [InlineData("Temperature", 1)]
    [InlineData("Temperature #1", 2)]
    [InlineData("Temperature #8", 2)]
    [InlineData("Temperature Sensor 3", 2)]
    [InlineData("Warning Temperature", null)]
    [InlineData("Critical Temperature", null)]
    [InlineData("Warning Composite Temperature", null)]
    [InlineData("Temperature Limit", null)]
    [InlineData("Temperature Threshold", null)]
    [InlineData("Max Temperature", null)]
    [InlineData("Min Temperature", null)]
    [InlineData("Airflow Temperature", null)]
    public void StorageTemperatureRank_PrefersCompositeAndSkipsThresholds(string name, int? rank)
    {
        Assert.Equal(rank, LhmSensorProbe.StorageTemperatureRank(name));
    }

    [Fact]
    public void SensorDiagnostics_ListsDevicesAndMarksIgnoredStorageSensors()
    {
        var output = new StringWriter();
        SensorDiagnostics.Write(output, new[]
        {
            Cpu("CPU Package", 61.4f),
            Disk("CT4000P310SSD8", "Composite Temperature", 42f, "/nvme/0"),
            Disk("CT4000P310SSD8", "Warning Temperature", 87f, "/nvme/0"),
        });

        var text = output.ToString();
        Assert.Contains("Cpu Intel Core Ultra 9 285HX", text);
        Assert.Contains("Storage CT4000P310SSD8 [/nvme/0]", text);
        Assert.Matches(@"Composite Temperature\s+42\s+\(drive temperature\)", text);
        Assert.Matches(@"Warning Temperature\s+87\s+\(ignored: threshold", text);
    }

    [Theory]
    [InlineData(null, false)]
    [InlineData(0f, false)]
    [InlineData(-5f, false)]
    [InlineData(255f, false)]
    [InlineData(float.NaN, false)]
    [InlineData(45f, true)]
    public void ValidTemperature_RejectsUnreadableValues(float? value, bool valid)
    {
        Assert.Equal(valid, LhmSensorProbe.ValidTemperature(value));
    }
}
