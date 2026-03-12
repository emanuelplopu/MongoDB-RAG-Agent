// RecallHub Tray Application
// Provides system tray integration for RecallHub services

namespace RecallHub.TrayApp;

static class Program
{
    /// <summary>
    /// The main entry point for the application.
    /// </summary>
    [STAThread]
    static void Main()
    {
        // Ensure single instance
        using var mutex = new Mutex(true, "RecallHub.TrayApp.SingleInstance", out bool createdNew);
        
        if (!createdNew)
        {
            // Another instance is already running
            MessageBox.Show(
                "RecallHub is already running in the system tray.",
                "RecallHub",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information
            );
            return;
        }

        // To customize application configuration such as set high DPI settings or default font,
        // see https://aka.ms/applicationconfiguration.
        ApplicationConfiguration.Initialize();
        
        // Run the tray application
        Application.Run(new TrayApplicationContext());
    }
}
