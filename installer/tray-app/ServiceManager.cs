// RecallHub Service Manager
// Manages RecallHub services via Docker Desktop and Docker Compose

using System.Diagnostics;
using System.Net.Http;
using System.Text.Json;

namespace RecallHub.TrayApp;

/// <summary>
/// Manages RecallHub services via Docker Desktop and Docker Compose.
/// </summary>
public class ServiceManager
{
    private readonly HttpClient _httpClient;
    private readonly AppConfig _config;
    private readonly string _installPath;
    private readonly string _scriptsPath;
    private readonly string[] _scriptCandidates;

    public ServiceManager(AppConfig config)
    {
        _config = config;
        _installPath = config.Docker?.InstallPath
            ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RecallHub");
        // Service scripts: deployed next to the tray app by Write-TrayConfig.ps1, or
        // (dev checkout / hardened layout) under the install path.
        _scriptCandidates = new[]
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RecallHub", "scripts"),
            Path.Combine(_installPath, "installer", "scripts"),
            Path.Combine(_installPath, "scripts"),
        };
        _scriptsPath = _scriptCandidates.FirstOrDefault(d => File.Exists(Path.Combine(d, "Start-Services.ps1")))
            ?? _scriptCandidates[0];
        TrayLog.Info($"ServiceManager: installPath={_installPath} scripts={_scriptsPath} "
            + $"profile={config.Docker?.ComposeProfile ?? "(none)"} frontendPort={config.Ports?.Frontend ?? 11080} backendPort={config.Ports?.Backend ?? 11000}");

        _httpClient = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(5)
        };
    }

    /// <summary>
    /// Check if RecallHub services are running by testing the frontend endpoint.
    /// </summary>
    public async Task<bool> CheckServicesRunningAsync()
    {
        try
        {
            var port = _config.Ports?.Frontend ?? 11080;
            var response = await _httpClient.GetAsync($"http://localhost:{port}/");
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    /// <summary>
    /// Check if the backend API is responding.
    /// </summary>
    public async Task<bool> CheckBackendHealthyAsync()
    {
        try
        {
            var port = _config.Ports?.Backend ?? 11000;
            // /api/v1/status does not exist on the backend; this is the real health route.
            var response = await _httpClient.GetAsync($"http://localhost:{port}/api/v1/system/health");
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    private const string DefaultDesktopPath = @"C:\Program Files\Docker\Docker\Docker Desktop.exe";

    /// <summary>
    /// Make sure the Docker engine is reachable.
    /// <para>
    /// Docker Desktop is single-instance: starting "Docker Desktop.exe" while it is
    /// already running - or still booting after login - starts nothing, it only tells
    /// the running instance to show its Dashboard window. This app and Docker Desktop
    /// both autostart at login and the engine needs 20-60 s before <c>docker info</c>
    /// answers, so the old "no answer, so relaunch" logic opened the Dashboard in
    /// front of every user at every login. Therefore: while a Docker Desktop process
    /// exists we only wait (up to <see cref="DockerConfig.StartupTimeout"/>); only
    /// when no process exists and <see cref="DockerConfig.AutoStart"/> is true do we
    /// start Docker Desktop, and then quietly.
    /// </para>
    /// </summary>
    public async Task<bool> EnsureDockerRunningAsync()
    {
        if (await IsDockerRunningAsync())
            return true;

        var timeout = TimeSpan.FromSeconds(Math.Max(30, _config.Docker?.StartupTimeout ?? 120));

        if (IsDockerDesktopProcessRunning())
        {
            TrayLog.Info($"Docker Desktop is running but its engine is not ready yet - waiting up to {timeout.TotalSeconds:0}s (not relaunching it: that would only open the Dashboard window)");
            return await WaitForDockerEngineAsync(timeout);
        }

        if (_config.Docker?.AutoStart != true)
        {
            TrayLog.Warn("Docker engine is not reachable and docker.autoStart is false");
            return false;
        }

        if (!await StartDockerDesktopQuietlyAsync())
            return false;

        return await WaitForDockerEngineAsync(timeout);
    }

    /// <summary>True when a "Docker Desktop" process exists (running or still booting).</summary>
    public static bool IsDockerDesktopProcessRunning()
    {
        var processes = Process.GetProcessesByName("Docker Desktop");
        try
        {
            return processes.Length > 0;
        }
        finally
        {
            foreach (var process in processes)
                process.Dispose();
        }
    }

    /// <summary>
    /// Launch Docker Desktop without bringing its Dashboard to the front: through the
    /// Docker Desktop CLI (<c>docker desktop start</c>, bundled since 4.37) when it is
    /// available, otherwise the executable from config, minimized. Only call this when
    /// <see cref="IsDockerDesktopProcessRunning"/> is false.
    /// </summary>
    private async Task<bool> StartDockerDesktopQuietlyAsync()
    {
        var cli = await RunProcessAsync("docker", "desktop version", timeoutMs: 10000);
        if (cli.ExitCode == 0)
        {
            var start = await RunProcessAsync("docker", "desktop start --detach", timeoutMs: 30000);
            if (start.ExitCode == 0)
            {
                TrayLog.Info("Docker Desktop start requested via 'docker desktop start'");
                return true;
            }
            TrayLog.Warn($"'docker desktop start' failed (exit {start.ExitCode}): {start.StdErr.Trim()} - falling back to the executable");
        }

        var desktopPath = _config.Docker?.DesktopPath ?? DefaultDesktopPath;
        if (!File.Exists(desktopPath))
        {
            TrayLog.Error($"Docker Desktop not found at {desktopPath}");
            return false;
        }

        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = desktopPath,
                UseShellExecute = true,
                WindowStyle = ProcessWindowStyle.Minimized
            });
            TrayLog.Info($"Docker Desktop started from {desktopPath}");
            return true;
        }
        catch (Exception ex)
        {
            TrayLog.Error($"Failed to start Docker Desktop: {ex.Message}");
            return false;
        }
    }

    /// <summary>Poll the engine every 3s until it answers or <paramref name="timeout"/> elapses.</summary>
    private async Task<bool> WaitForDockerEngineAsync(TimeSpan timeout)
    {
        var started = DateTime.UtcNow;
        while (DateTime.UtcNow - started < timeout)
        {
            await Task.Delay(3000);
            if (await IsDockerRunningAsync())
            {
                TrayLog.Info($"Docker engine ready after {(DateTime.UtcNow - started).TotalSeconds:0}s");
                return true;
            }
        }

        TrayLog.Error($"Docker engine did not answer within {timeout.TotalSeconds:0}s - Docker Desktop may be showing a dialog; open it from its tray icon");
        return false;
    }

    /// <summary>
    /// Check if the Docker engine is responsive: a cheap named-pipe check first (no
    /// process spawn every 5s while Docker is down), then <c>docker info</c> with a
    /// timeout long enough for a busy machine - the previous 5s made a slow but
    /// healthy engine look stopped.
    /// </summary>
    public async Task<bool> IsDockerRunningAsync()
    {
        if (!DockerEnginePipeExists())
            return false;

        try
        {
            var result = await RunProcessAsync("docker", "info", timeoutMs: 15000);
            return result.ExitCode == 0;
        }
        catch
        {
            return false;
        }
    }

    /// <summary>
    /// Docker Desktop exposes the engine as named pipes (docker_engine,
    /// dockerDesktopLinuxEngine, ...). None present means the engine is certainly
    /// down. When the enumeration itself fails, docker info decides.
    /// </summary>
    private static bool DockerEnginePipeExists()
    {
        try
        {
            return Directory.GetFiles(@"\\.\pipe\").Any(pipe =>
                pipe.Contains("docker", StringComparison.OrdinalIgnoreCase)
                && pipe.Contains("engine", StringComparison.OrdinalIgnoreCase));
        }
        catch
        {
            return true;
        }
    }

    /// <summary>
    /// Start RecallHub services via docker compose.
    /// </summary>
    public async Task<bool> StartServicesAsync()
    {
        // Ensure Docker Desktop is running first
        if (!await EnsureDockerRunningAsync())
            return false;

        var startupTimeout = _config.Docker?.StartupTimeout ?? 120;

        // Preferred path: Start-Services.ps1 knows the install path, the compose
        // profile (tenant) and the health endpoints, and writes a log under
        // %LocalAppData%\RecallHub\logs. INSTALL.ps1 deploys it next to this app.
        var scriptPath = Path.Combine(_scriptsPath, "Start-Services.ps1");
        if (File.Exists(scriptPath))
        {
            TrayLog.Info($"Starting services via {scriptPath}");
            if (await RunPowerShellScriptAsync(scriptPath, "-Wait", timeoutMs: (startupTimeout + 90) * 1000))
                return true;
            TrayLog.Warn("Start-Services.ps1 reported failure - trying docker compose directly");
        }

        var composePath = Path.Combine(_installPath, _config.Docker?.ComposeFile ?? "docker-compose.yml");
        if (!File.Exists(composePath))
        {
            TrayLog.Error($"Neither a usable Start-Services.ps1 nor {composePath} found");
            return false;
        }

        var args = BuildComposeArgs(composePath) + " up -d --remove-orphans";
        TrayLog.Info($"docker {args}");
        var result = await RunProcessAsync("docker", args, workingDirectory: _installPath, timeoutMs: startupTimeout * 1000);
        if (result.ExitCode != 0)
        {
            TrayLog.Error($"docker compose up failed (exit {result.ExitCode}): {result.StdErr}");
            return false;
        }

        // Wait for services to become healthy
        var healthTimeout = _config.Docker?.StartupTimeout ?? 120;
        var interval = _config.Docker?.HealthCheckInterval ?? 5;
        var elapsed = 0;
        while (elapsed < healthTimeout)
        {
            await Task.Delay(interval * 1000);
            elapsed += interval;
            if (await CheckServicesRunningAsync())
                return true;
        }

        return await CheckServicesRunningAsync();
    }

    /// <summary>
    /// Stop RecallHub services via docker compose.
    /// </summary>
    public async Task<bool> StopServicesAsync()
    {
        var scriptPath = Path.Combine(_scriptsPath, "Stop-Services.ps1");
        if (File.Exists(scriptPath))
        {
            TrayLog.Info($"Stopping services via {scriptPath}");
            if (await RunPowerShellScriptAsync(scriptPath, "", timeoutMs: 120000))
                return true;
            TrayLog.Warn("Stop-Services.ps1 reported failure - trying docker compose directly");
        }

        var composePath = Path.Combine(_installPath, _config.Docker?.ComposeFile ?? "docker-compose.yml");
        if (!File.Exists(composePath))
        {
            TrayLog.Error($"Compose file not found: {composePath}");
            return false;
        }

        var args = BuildComposeArgs(composePath) + " down";
        TrayLog.Info($"docker {args}");
        var result = await RunProcessAsync("docker", args, workingDirectory: _installPath, timeoutMs: 120000);
        if (result.ExitCode != 0)
            TrayLog.Error($"docker compose down failed (exit {result.ExitCode}): {result.StdErr}");
        return result.ExitCode == 0;
    }

    /// <summary>
    /// Compose arguments shared by start/stop: compose file, the override file
    /// (external document mounts) and the tenant profile from config.json.
    /// </summary>
    private string BuildComposeArgs(string composePath)
    {
        var args = $"compose -f \"{composePath}\"";
        var overridePath = Path.Combine(_installPath, "docker-compose.override.yml");
        if (File.Exists(overridePath)
            && Path.GetFileName(composePath).Equals("docker-compose.yml", StringComparison.OrdinalIgnoreCase))
        {
            args += $" -f \"{overridePath}\"";
        }
        var profile = _config.Docker?.ComposeProfile;
        if (!string.IsNullOrWhiteSpace(profile))
            args += $" --profile {profile}";
        return args;
    }

    /// <summary>
    /// Restart services (stop then start).
    /// </summary>
    public async Task<bool> RestartServicesAsync()
    {
        await StopServicesAsync();
        await Task.Delay(2000);
        return await StartServicesAsync();
    }

    /// <summary>
    /// Get comprehensive service status.
    /// </summary>
    public async Task<ServiceStatus> GetServiceStatusAsync()
    {
        var status = new ServiceStatus
        {
            DockerRunning = await IsDockerRunningAsync(),
            CheckedAt = DateTime.Now
        };

        if (status.DockerRunning)
        {
            status.IsRunning = await CheckServicesRunningAsync();
            if (status.IsRunning)
            {
                status.BackendHealthy = await CheckBackendHealthyAsync();
            }
        }

        return status;
    }

    /// <summary>
    /// Run a process and capture output.
    /// </summary>
    private async Task<ProcessResult> RunProcessAsync(string fileName, string arguments,
        string? workingDirectory = null, int timeoutMs = 30000)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = fileName,
            Arguments = arguments,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };

        if (workingDirectory != null)
            startInfo.WorkingDirectory = workingDirectory;

        try
        {
            using var process = Process.Start(startInfo);
            if (process == null)
                return new ProcessResult { ExitCode = -1, StdErr = "Failed to start process" };

            var stdOut = await process.StandardOutput.ReadToEndAsync();
            var stdErr = await process.StandardError.ReadToEndAsync();
            var completed = await Task.Run(() => process.WaitForExit(timeoutMs));

            if (!completed)
            {
                process.Kill();
                return new ProcessResult { ExitCode = -1, StdErr = "Process timed out" };
            }

            return new ProcessResult
            {
                ExitCode = process.ExitCode,
                StdOut = stdOut,
                StdErr = stdErr
            };
        }
        catch (Exception ex)
        {
            return new ProcessResult { ExitCode = -1, StdErr = ex.Message };
        }
    }

    /// <summary>
    /// Run a PowerShell script (fallback for complex operations).
    /// </summary>
    private async Task<bool> RunPowerShellScriptAsync(string scriptPath, string arguments = "", int timeoutMs = 120000)
    {
        var result = await RunProcessAsync(
            "powershell.exe",
            $"-NoProfile -ExecutionPolicy Bypass -File \"{scriptPath}\" {arguments}",
            timeoutMs: timeoutMs
        );
        TrayLog.Info($"{Path.GetFileName(scriptPath)} {arguments} -> exit {result.ExitCode}");
        if (result.ExitCode != 0 && !string.IsNullOrWhiteSpace(result.StdErr))
            TrayLog.Warn(result.StdErr.Trim());
        return result.ExitCode == 0;
    }
}

/// <summary>
/// Service status information.
/// </summary>
public class ServiceStatus
{
    public bool DockerRunning { get; set; }
    public bool IsRunning { get; set; }
    public bool BackendHealthy { get; set; }
    public DateTime CheckedAt { get; set; }
}

/// <summary>
/// Result of a process execution.
/// </summary>
public class ProcessResult
{
    public int ExitCode { get; set; }
    public string StdOut { get; set; } = "";
    public string StdErr { get; set; } = "";
}

/// <summary>
/// Application configuration loaded from config.json.
/// </summary>
public class AppConfig
{
    public DockerConfig? Docker { get; set; }
    public PortsConfig? Ports { get; set; }
}

public class DockerConfig
{
    public string? ComposeFile { get; set; }
    public string? InstallPath { get; set; }
    /// <summary>Compose profile to enable (tenant): recallhub | quellex. Empty = none.</summary>
    public string? ComposeProfile { get; set; }
    public int StartupTimeout { get; set; } = 120;
    public int HealthCheckInterval { get; set; } = 5;
    public bool AutoStart { get; set; } = true;
    public string? DesktopPath { get; set; }
}

public class PortsConfig
{
    public int Frontend { get; set; } = 11080;
    public int Backend { get; set; } = 11000;
    public int MongoDB { get; set; } = 27017;
    public int Ollama { get; set; } = 11434;
}

/// <summary>
/// Minimal file logger: %LocalAppData%\RecallHub\logs\tray-app.log. The tray app used
/// to be a black box when startup failed; this puts its decisions next to the
/// Start-Services logs.
/// </summary>
public static class TrayLog
{
    private static readonly object Gate = new();
    private static readonly string LogPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RecallHub", "logs", "tray-app.log");

    public static void Info(string message) => Write("INFO", message);
    public static void Warn(string message) => Write("WARN", message);
    public static void Error(string message) => Write("ERROR", message);

    private static void Write(string level, string message)
    {
        try
        {
            lock (Gate)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);
                if (File.Exists(LogPath) && new FileInfo(LogPath).Length > 2_000_000)
                    File.Move(LogPath, LogPath + ".1", overwrite: true);
                File.AppendAllText(LogPath, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] [{level}] {message}{Environment.NewLine}");
            }
        }
        catch
        {
            // logging must never break the app
        }
    }
}
