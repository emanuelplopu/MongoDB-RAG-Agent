// RecallHub Desktop Application
// Provides system tray integration and embedded WebView2 browser for RecallHub

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
            // Another instance is already running. Branding is not configured
            // yet at this point, so resolve the name from config.json directly.
            Branding.Configure(TrayApplicationContext.LoadConfig());
            MessageBox.Show(
                $"{Branding.AppName} is already running in the system tray.",
                Branding.AppName,
                MessageBoxButtons.OK,
                MessageBoxIcon.Information
            );
            return;
        }

        // High DPI support
        Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        // Run the tray application
        Application.Run(new TrayApplicationContext());
    }
}
