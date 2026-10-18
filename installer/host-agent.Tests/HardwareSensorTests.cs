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
        Assert.Equal(52.0, temp["storage_max_c"].AsDouble); // hottest sensor of the drive
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
