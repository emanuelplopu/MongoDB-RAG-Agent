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

    public ServiceManager(AppConfig config)
    {
        _config = config;
        _installPath = config.Docker?.InstallPath
            ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "RecallHub");
        _scriptsPath = Path.Combine(_installPath, "installer", "scripts");

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
            var response = await _httpClient.GetAsync($"http://localhost:{port}/api/v1/status");
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

        var composePath = Path.Combine(_installPath, _config.Docker?.ComposeFile ?? "docker-compose.yml");
        if (!File.Exists(composePath))
        {
            // Fallback: try Start-Services.ps1 script
            var scriptPath = Path.Combine(_scriptsPath, "Start-Services.ps1");
            if (File.Exists(scriptPath))
            {
                return await RunPowerShellScriptAsync(scriptPath, "-Wait");
            }
            Debug.WriteLine($"Neither compose file nor start script found");
            return false;
        }

        // Run docker compose up
        var result = await RunProcessAsync(
            "docker",
            $"compose -f \"{composePath}\" up -d",
            workingDirectory: _installPath,
            timeoutMs: (_config.Docker?.StartupTimeout ?? 120) * 1000
        );

        if (result.ExitCode != 0)
        {
            Debug.WriteLine($"docker compose up failed: {result.StdErr}");
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
        var composePath = Path.Combine(_installPath, _config.Docker?.ComposeFile ?? "docker-compose.yml");
        if (!File.Exists(composePath))
        {
            // Fallback: try Stop-Services.ps1 script
            var scriptPath = Path.Combine(_scriptsPath, "Stop-Services.ps1");
            if (File.Exists(scriptPath))
            {
                return await RunPowerShellScriptAsync(scriptPath, "-Force");
            }
            return false;
        }

        var result = await RunProcessAsync(
            "docker",
            $"compose -f \"{composePath}\" down",
            workingDirectory: _installPath,
            timeoutMs: 60000
        );

        return result.ExitCode == 0;
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
    private async Task<bool> RunPowerShellScriptAsync(string scriptPath, string arguments = "")
    {
        var result = await RunProcessAsync(
            "powershell.exe",
            $"-ExecutionPolicy Bypass -File \"{scriptPath}\" {arguments}",
            timeoutMs: 120000
        );
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
