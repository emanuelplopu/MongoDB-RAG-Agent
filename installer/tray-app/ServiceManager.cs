// RecallHub Service Manager
// Handles starting, stopping, and monitoring RecallHub services

using System.Diagnostics;
using System.Net.Http;

namespace RecallHub.TrayApp;

/// <summary>
/// Manages RecallHub services running in WSL2.
/// </summary>
public class ServiceManager
{
    private readonly string _installPath;
    private readonly string _scriptsPath;
    private readonly HttpClient _httpClient;

    public ServiceManager()
    {
        _installPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "RecallHub"
        );
        _scriptsPath = Path.Combine(_installPath, "scripts");
        
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
            var response = await _httpClient.GetAsync("http://localhost:11080/");
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
            var response = await _httpClient.GetAsync("http://localhost:11080/api/v1/status");
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    /// <summary>
    /// Start RecallHub services.
    /// </summary>
    public async Task<bool> StartServicesAsync()
    {
        var scriptPath = Path.Combine(_scriptsPath, "Start-Services.ps1");
        
        if (!File.Exists(scriptPath))
        {
            throw new FileNotFoundException($"Start script not found: {scriptPath}");
        }
        
        var success = await RunPowerShellScriptAsync(scriptPath, "-Wait");
        
        if (success)
        {
            // Wait for services to be ready
            for (int i = 0; i < 30; i++)
            {
                await Task.Delay(1000);
                if (await CheckServicesRunningAsync())
                {
                    return true;
                }
            }
        }
        
        return false;
    }

    /// <summary>
    /// Stop RecallHub services.
    /// </summary>
    public async Task<bool> StopServicesAsync()
    {
        var scriptPath = Path.Combine(_scriptsPath, "Stop-Services.ps1");
        
        if (!File.Exists(scriptPath))
        {
            throw new FileNotFoundException($"Stop script not found: {scriptPath}");
        }
        
        return await RunPowerShellScriptAsync(scriptPath, "-Force -StopWSL");
    }

    /// <summary>
    /// Get service status information.
    /// </summary>
    public async Task<ServiceStatus> GetServiceStatusAsync()
    {
        var status = new ServiceStatus
        {
            IsRunning = await CheckServicesRunningAsync(),
            CheckedAt = DateTime.Now
        };
        
        if (status.IsRunning)
        {
            status.BackendHealthy = await CheckBackendHealthyAsync();
        }
        
        return status;
    }

    /// <summary>
    /// Run a PowerShell script asynchronously.
    /// </summary>
    private async Task<bool> RunPowerShellScriptAsync(string scriptPath, string arguments = "")
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = "powershell.exe",
            Arguments = $"-ExecutionPolicy Bypass -File \"{scriptPath}\" {arguments}",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        
        try
        {
            using var process = Process.Start(startInfo);
            
            if (process == null)
            {
                return false;
            }
            
            // Wait for completion with timeout
            var completed = await Task.Run(() => process.WaitForExit(120000)); // 2 minute timeout
            
            if (!completed)
            {
                process.Kill();
                return false;
            }
            
            return process.ExitCode == 0;
        }
        catch
        {
            return false;
        }
    }

    /// <summary>
    /// Check if WSL is running the RecallHub distribution.
    /// </summary>
    public async Task<bool> IsWslDistroRunningAsync()
    {
        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = "wsl.exe",
                Arguments = "--list --running --quiet",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true
            };
            
            using var process = Process.Start(startInfo);
            
            if (process == null)
            {
                return false;
            }
            
            var output = await process.StandardOutput.ReadToEndAsync();
            await Task.Run(() => process.WaitForExit(5000));
            
            return output.Contains("RecallHub", StringComparison.OrdinalIgnoreCase);
        }
        catch
        {
            return false;
        }
    }
}

/// <summary>
/// Represents the status of RecallHub services.
/// </summary>
public class ServiceStatus
{
    public bool IsRunning { get; set; }
    public bool BackendHealthy { get; set; }
    public DateTime CheckedAt { get; set; }
}
