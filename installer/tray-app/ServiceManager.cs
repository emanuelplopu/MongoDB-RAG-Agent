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

    /// <summary>Folder the deployed service scripts live in (also holds Export-LanCertificate.ps1).</summary>
    public string ScriptsPath { get; }

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
        ScriptsPath = _scriptsPath;
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

    /// <summary>
    /// Ensure Docker Desktop is running. Auto-starts if configured.
    /// </summary>
    public async Task<bool> EnsureDockerRunningAsync()
    {
        // Check if Docker daemon is already responding
        if (await IsDockerRunningAsync())
            return true;

        if (_config.Docker?.AutoStart != true)
            return false;

        // Try to start Docker Desktop
        var desktopPath = _config.Docker?.DesktopPath
            ?? @"C:\Program Files\Docker\Docker\Docker Desktop.exe";

        if (!File.Exists(desktopPath))
        {
            Debug.WriteLine($"Docker Desktop not found at: {desktopPath}");
            return false;
        }

        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = desktopPath,
                UseShellExecute = true
            });
        }
        catch (Exception ex)
        {
            Debug.WriteLine($"Failed to start Docker Desktop: {ex.Message}");
            return false;
        }

        // Wait for Docker daemon to become ready (up to 60s)
        var timeout = TimeSpan.FromSeconds(60);
        var started = DateTime.Now;
        while (DateTime.Now - started < timeout)
        {
            await Task.Delay(2000);
            if (await IsDockerRunningAsync())
                return true;
        }

        Debug.WriteLine("Docker Desktop did not start within 60 seconds");
        return false;
    }

    /// <summary>
    /// Check if Docker daemon is responsive.
    /// </summary>
    public async Task<bool> IsDockerRunningAsync()
    {
        try
        {
            var result = await RunProcessAsync("docker", "info", timeoutMs: 5000);
            return result.ExitCode == 0;
        }
        catch
        {
            return false;
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
        foreach (var profile in ResolveComposeProfiles())
            args += $" --profile {profile}";
        return args;
    }

    /// <summary>
    /// Profiles to enable: the tenant from config.json plus COMPOSE_PROFILES from
    /// the install's .env (e.g. the local embedding server). Compose treats an
    /// explicit --profile flag as a replacement for the .env list, so the two
    /// have to be merged here (same rule as Start-Services.ps1).
    /// </summary>
    public IReadOnlyList<string> ResolveComposeProfiles()
    {
        var profiles = new List<string>();
        void Add(string? csv)
        {
            foreach (var raw in (csv ?? string.Empty).Split(','))
            {
                var p = raw.Trim().Trim('"', '\'');
                if (p.Length > 0 && !profiles.Contains(p, StringComparer.OrdinalIgnoreCase)) profiles.Add(p);
            }
        }

        Add(_config.Docker?.ComposeProfile);
        var envFile = Path.Combine(_installPath, ".env");
        try
        {
            if (File.Exists(envFile))
            {
                foreach (var line in File.ReadLines(envFile))
                {
                    var trimmed = line.TrimStart();
                    if (trimmed.StartsWith("COMPOSE_PROFILES", StringComparison.OrdinalIgnoreCase))
                    {
                        var eq = trimmed.IndexOf('=');
                        if (eq > 0 && trimmed.Substring(0, eq).Trim().Equals("COMPOSE_PROFILES", StringComparison.OrdinalIgnoreCase))
                            Add(trimmed.Substring(eq + 1));
                    }
                }
            }
        }
        catch (Exception ex)
        {
            TrayLog.Warn($"Could not read COMPOSE_PROFILES from {envFile}: {ex.Message}");
        }
        return profiles;
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
    public AppInfoConfig? App { get; set; }
    public DockerConfig? Docker { get; set; }
    public PortsConfig? Ports { get; set; }
    public NetworkConfig? Network { get; set; }
}

/// <summary>"app" section of config.json: branding written by Write-TrayConfig.ps1.</summary>
public class AppInfoConfig
{
    /// <summary>Display name: "Quellex" or "RecallHub".</summary>
    public string? Name { get; set; }
    /// <summary>Tenant id: recallhub | quellex.</summary>
    public string? Tenant { get; set; }
    public string? Version { get; set; }
}

/// <summary>
/// "network" section of config.json: how the install is reachable from other
/// computers (see docs/21-LAN_ACCESS.md).
/// </summary>
public class NetworkConfig
{
    /// <summary>Single-label name announced via mDNS (name.local) and LLMNR (name).</summary>
    public string? Hostname { get; set; }
    /// <summary>Run the mDNS/LLMNR responder in the tray app.</summary>
    public bool Advertise { get; set; } = false;
    /// <summary>Host port of the TLS gateway (443 = no port in the URL).</summary>
    public int HttpsPort { get; set; } = 443;
    /// <summary>Host port of the gateway's HTTP-to-HTTPS redirect listener.</summary>
    public int HttpPort { get; set; } = 80;

    /// <summary>URL colleagues open, e.g. https://quellex.local or https://recallhub.local:11443.</summary>
    public string? NetworkUrl =>
        string.IsNullOrWhiteSpace(Hostname)
            ? null
            : HttpsPort == 443 ? $"https://{Hostname}.local" : $"https://{Hostname}.local:{HttpsPort}";
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
