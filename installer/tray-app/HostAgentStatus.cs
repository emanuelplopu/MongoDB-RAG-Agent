using System.Diagnostics;
using System.ServiceProcess;

namespace RecallHub.TrayApp;

/// <summary>
/// State of the telemetry host agent (installer/host-agent) for the tray menu: the Windows service
/// "RecallHubHostAgent", or the agent process started by the logon task (Install-HostAgent.ps1 -Mode Task).
/// </summary>
internal static class HostAgentStatus
{
    private const string ServiceName = "RecallHubHostAgent";

    public static string Describe()
    {
        try
        {
            using var service = ServiceController.GetServices().FirstOrDefault(s => s.ServiceName == ServiceName);
            if (service is not null)
            {
                return service.Status switch
                {
                    ServiceControllerStatus.Running => "Host agent: running",
                    ServiceControllerStatus.StartPending => "Host agent: starting",
                    ServiceControllerStatus.Stopped => "Host agent: stopped",
                    _ => $"Host agent: {service.Status}",
                };
            }
            var processes = Process.GetProcessesByName("RecallHubHostAgent");
            var running = processes.Length > 0;
            foreach (var p in processes) p.Dispose();
            return running ? "Host agent: running (logon task)" : "Host agent: not installed";
        }
        catch (Exception)
        {
            return "Host agent: unknown";
        }
    }
}
