using RecallHub.HostAgent;
using RecallHub.HostAgent.Probes;
using RecallHub.HostAgent.Storage;

// RecallHub host agent: samples the Windows host every few seconds into MongoDB
// (database recallhub_telemetry, time-series collection host_samples).
//
//   RecallHubHostAgent.exe              run (console, or as the RecallHubHostAgent Windows service)
//   RecallHubHostAgent.exe --once       print one sample as JSON and exit (no write)
//   RecallHubHostAgent.exe --dry-run    print every sample as JSON (no write)
//   RecallHubHostAgent.exe --sensors    list every LibreHardwareMonitor sensor and the derived temperatures, then exit
//                                       (no write, no configuration; run elevated)
//
// As a service it also applies the CPU clock cap the backends ask for (host_control, PowerPlanControl.cs)
// and restarts a degraded llama.cpp embedding server (EmbedderWatchdog.cs).
//
// Configuration precedence: appsettings.json next to the exe < %ProgramData%\RecallHub\HostAgent\appsettings.json
// < RECALLHUB_AGENT_* environment variables (e.g. RECALLHUB_AGENT_Agent__IntervalSeconds=10) < command line.

if (args.Contains("--sensors"))
{
    Environment.ExitCode = SensorDiagnostics.Run(Console.Out, Console.Error);
    return;
}

var mode = args.Contains("--once") ? OutputMode.Once
    : args.Contains("--dry-run") ? OutputMode.DryRun
    : OutputMode.Mongo;
var hostArgs = args.Where(a => a is not "--once" and not "--dry-run").ToArray();

var builder = Host.CreateApplicationBuilder(new HostApplicationBuilderSettings
{
    Args = hostArgs,
    ContentRootPath = AppContext.BaseDirectory,
});

var overridePath = Path.Combine(
    Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "RecallHub", "HostAgent", "appsettings.json");
builder.Configuration.AddJsonFile(overridePath, optional: true, reloadOnChange: false);
builder.Configuration.AddEnvironmentVariables("RECALLHUB_AGENT_");
builder.Configuration.AddCommandLine(hostArgs);

builder.Services.AddWindowsService(o => o.ServiceName = "RecallHubHostAgent");

if (mode != OutputMode.Mongo)
{
    // Keep stdout clean JSON in --once / --dry-run.
    builder.Logging.ClearProviders();
    builder.Logging.AddConsole(o => o.LogToStandardErrorThreshold = LogLevel.Trace);
}

var options = new AgentOptions();
builder.Configuration.GetSection(AgentOptions.SectionName).Bind(options);
options.Normalize();

var mongo = new MongoContext(options);
builder.Services.AddSingleton(options);
builder.Services.AddSingleton(mongo);
builder.Services.AddSingleton<IHardwareSensorProbe>(sp => HardwareSensorFactory.Create(options, sp.GetRequiredService<ILoggerFactory>()));
builder.Services.AddSingleton(sp => new SampleCollector(
    sp.GetRequiredService<ILoggerFactory>(), options, mongo, sp.GetRequiredService<IHardwareSensorProbe>()));
builder.Services.AddHostedService(sp => new AgentWorker(
    sp.GetRequiredService<ILogger<AgentWorker>>(),
    options,
    sp.GetRequiredService<SampleCollector>(),
    sp.GetRequiredService<IHostApplicationLifetime>(),
    mode,
    mode == OutputMode.Mongo ? new MongoSampleSink(sp.GetRequiredService<ILogger<MongoSampleSink>>(), options, mongo) : null));
if (mode == OutputMode.Mongo && options.PowerPlan.Enabled)
{
    // CPU clock cap requested by the backends' load profiles (host_control), applied with powercfg
    builder.Services.AddHostedService(sp => new PowerPlanWorker(
        sp.GetRequiredService<ILogger<PowerPlanWorker>>(),
        options.PowerPlan,
        mongo,
        new PowercfgApplier(sp.GetRequiredService<ILogger<PowercfgApplier>>())));
}
if (mode == OutputMode.Mongo && options.EmbedderWatchdog.Enabled)
{
    // Probes the llama.cpp embedding servers and restarts a degraded one (EmbedderWatchdog.cs)
    builder.Services.AddHostedService(sp => new EmbedderWatchdogWorker(
        sp.GetRequiredService<ILogger<EmbedderWatchdogWorker>>(),
        options.EmbedderWatchdog,
        options.Docker,
        mongo));
}

using var host = builder.Build();
await host.RunAsync();
