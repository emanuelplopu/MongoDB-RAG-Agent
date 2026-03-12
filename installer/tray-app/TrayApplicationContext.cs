// RecallHub Tray Application Context
// Manages the system tray icon and menu

using System.Diagnostics;
using System.Text.Json;

namespace RecallHub.TrayApp;

/// <summary>
/// Application context that manages the system tray icon and menu.
/// </summary>
public class TrayApplicationContext : ApplicationContext
{
    private readonly NotifyIcon _trayIcon;
    private readonly ServiceManager _serviceManager;
    private readonly ContextMenuStrip _contextMenu;
    private readonly System.Windows.Forms.Timer _statusTimer;
    
    private ToolStripMenuItem _statusMenuItem = null!;
    private ToolStripMenuItem _startMenuItem = null!;
    private ToolStripMenuItem _stopMenuItem = null!;
    
    private bool _servicesRunning = false;

    public TrayApplicationContext()
    {
        _serviceManager = new ServiceManager();
        
        // Create context menu
        _contextMenu = CreateContextMenu();
        
        // Create tray icon
        _trayIcon = new NotifyIcon
        {
            Icon = LoadIcon(),
            ContextMenuStrip = _contextMenu,
            Visible = true,
            Text = "RecallHub"
        };
        
        // Handle double-click to open browser
        _trayIcon.DoubleClick += OnTrayIconDoubleClick;
        
        // Start status timer
        _statusTimer = new System.Windows.Forms.Timer
        {
            Interval = 5000 // Check every 5 seconds
        };
        _statusTimer.Tick += OnStatusTimerTick;
        _statusTimer.Start();
        
        // Initial status check
        _ = UpdateStatusAsync();
    }

    private Icon LoadIcon()
    {
        try
        {
            var iconPath = Path.Combine(AppContext.BaseDirectory, "icon.ico");
            if (File.Exists(iconPath))
            {
                return new Icon(iconPath);
            }
            
            // Fallback to embedded resource or system icon
            return SystemIcons.Application;
        }
        catch
        {
            return SystemIcons.Application;
        }
    }

    private ContextMenuStrip CreateContextMenu()
    {
        var menu = new ContextMenuStrip();
        
        // Status indicator (not clickable)
        _statusMenuItem = new ToolStripMenuItem("Status: Checking...")
        {
            Enabled = false,
            Image = null
        };
        menu.Items.Add(_statusMenuItem);
        
        menu.Items.Add(new ToolStripSeparator());
        
        // Open RecallHub
        var openItem = new ToolStripMenuItem("Open RecallHub", null, OnOpenClick);
        openItem.Font = new Font(openItem.Font, FontStyle.Bold);
        menu.Items.Add(openItem);
        
        // Open Admin Panel
        menu.Items.Add(new ToolStripMenuItem("Open Admin Panel", null, OnOpenAdminClick));
        
        menu.Items.Add(new ToolStripSeparator());
        
        // Start Services
        _startMenuItem = new ToolStripMenuItem("Start Services", null, OnStartClick);
        menu.Items.Add(_startMenuItem);
        
        // Stop Services
        _stopMenuItem = new ToolStripMenuItem("Stop Services", null, OnStopClick);
        menu.Items.Add(_stopMenuItem);
        
        // Restart Services
        menu.Items.Add(new ToolStripMenuItem("Restart Services", null, OnRestartClick));
        
        menu.Items.Add(new ToolStripSeparator());
        
        // View Logs
        menu.Items.Add(new ToolStripMenuItem("View Logs", null, OnViewLogsClick));
        
        // Check for Updates
        menu.Items.Add(new ToolStripMenuItem("Check for Updates", null, OnCheckUpdatesClick));
        
        menu.Items.Add(new ToolStripSeparator());
        
        // About
        menu.Items.Add(new ToolStripMenuItem("About RecallHub", null, OnAboutClick));
        
        // Exit
        menu.Items.Add(new ToolStripMenuItem("Exit", null, OnExitClick));
        
        return menu;
    }

    private async void OnStatusTimerTick(object? sender, EventArgs e)
    {
        await UpdateStatusAsync();
    }

    private async Task UpdateStatusAsync()
    {
        try
        {
            _servicesRunning = await _serviceManager.CheckServicesRunningAsync();
            
            if (_servicesRunning)
            {
                _statusMenuItem.Text = "Status: Running";
                _statusMenuItem.ForeColor = Color.Green;
                _trayIcon.Text = "RecallHub - Running";
                _startMenuItem.Enabled = false;
                _stopMenuItem.Enabled = true;
            }
            else
            {
                _statusMenuItem.Text = "Status: Stopped";
                _statusMenuItem.ForeColor = Color.Red;
                _trayIcon.Text = "RecallHub - Stopped";
                _startMenuItem.Enabled = true;
                _stopMenuItem.Enabled = false;
            }
        }
        catch
        {
            _statusMenuItem.Text = "Status: Unknown";
            _statusMenuItem.ForeColor = Color.Orange;
            _trayIcon.Text = "RecallHub - Status Unknown";
            _startMenuItem.Enabled = true;
            _stopMenuItem.Enabled = true;
        }
    }

    private void OnTrayIconDoubleClick(object? sender, EventArgs e)
    {
        OnOpenClick(sender, e);
    }

    private void OnOpenClick(object? sender, EventArgs e)
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = "http://localhost:11080",
                UseShellExecute = true
            });
        }
        catch (Exception ex)
        {
            ShowError("Failed to open browser", ex.Message);
        }
    }

    private void OnOpenAdminClick(object? sender, EventArgs e)
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = "http://localhost:11080/admin",
                UseShellExecute = true
            });
        }
        catch (Exception ex)
        {
            ShowError("Failed to open browser", ex.Message);
        }
    }

    private async void OnStartClick(object? sender, EventArgs e)
    {
        _startMenuItem.Enabled = false;
        _statusMenuItem.Text = "Status: Starting...";
        
        try
        {
            var success = await _serviceManager.StartServicesAsync();
            
            if (success)
            {
                _trayIcon.ShowBalloonTip(
                    3000,
                    "RecallHub",
                    "Services started successfully",
                    ToolTipIcon.Info
                );
            }
            else
            {
                ShowError("Start Failed", "Failed to start services. Check logs for details.");
            }
        }
        catch (Exception ex)
        {
            ShowError("Start Failed", ex.Message);
        }
        
        await UpdateStatusAsync();
    }

    private async void OnStopClick(object? sender, EventArgs e)
    {
        _stopMenuItem.Enabled = false;
        _statusMenuItem.Text = "Status: Stopping...";
        
        try
        {
            var success = await _serviceManager.StopServicesAsync();
            
            if (success)
            {
                _trayIcon.ShowBalloonTip(
                    3000,
                    "RecallHub",
                    "Services stopped",
                    ToolTipIcon.Info
                );
            }
            else
            {
                ShowError("Stop Failed", "Failed to stop services. Check logs for details.");
            }
        }
        catch (Exception ex)
        {
            ShowError("Stop Failed", ex.Message);
        }
        
        await UpdateStatusAsync();
    }

    private async void OnRestartClick(object? sender, EventArgs e)
    {
        _startMenuItem.Enabled = false;
        _stopMenuItem.Enabled = false;
        _statusMenuItem.Text = "Status: Restarting...";
        
        try
        {
            await _serviceManager.StopServicesAsync();
            await Task.Delay(2000);
            await _serviceManager.StartServicesAsync();
            
            _trayIcon.ShowBalloonTip(
                3000,
                "RecallHub",
                "Services restarted successfully",
                ToolTipIcon.Info
            );
        }
        catch (Exception ex)
        {
            ShowError("Restart Failed", ex.Message);
        }
        
        await UpdateStatusAsync();
    }

    private void OnViewLogsClick(object? sender, EventArgs e)
    {
        try
        {
            var logsPath = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "RecallHub",
                "logs"
            );
            
            if (Directory.Exists(logsPath))
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = "explorer.exe",
                    Arguments = logsPath,
                    UseShellExecute = true
                });
            }
            else
            {
                ShowError("Logs Not Found", $"Logs directory not found: {logsPath}");
            }
        }
        catch (Exception ex)
        {
            ShowError("Failed to open logs", ex.Message);
        }
    }

    private void OnCheckUpdatesClick(object? sender, EventArgs e)
    {
        try
        {
            var updatesPath = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "RecallHub",
                "updates"
            );
            
            // Check for .rhu files
            if (Directory.Exists(updatesPath))
            {
                var updateFiles = Directory.GetFiles(updatesPath, "*.rhu");
                
                if (updateFiles.Length > 0)
                {
                    var result = MessageBox.Show(
                        $"Found {updateFiles.Length} update(s) available.\n\nWould you like to open the Admin Panel to install them?",
                        "Updates Available",
                        MessageBoxButtons.YesNo,
                        MessageBoxIcon.Information
                    );
                    
                    if (result == DialogResult.Yes)
                    {
                        OnOpenAdminClick(sender, e);
                    }
                }
                else
                {
                    MessageBox.Show(
                        "No updates available.\n\nTo install updates manually, place .rhu files in:\n" + updatesPath,
                        "No Updates",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Information
                    );
                }
            }
            else
            {
                Directory.CreateDirectory(updatesPath);
                MessageBox.Show(
                    "To install updates, place .rhu files in:\n" + updatesPath,
                    "Updates",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information
                );
            }
        }
        catch (Exception ex)
        {
            ShowError("Check Updates Failed", ex.Message);
        }
    }

    private void OnAboutClick(object? sender, EventArgs e)
    {
        var version = typeof(Program).Assembly.GetName().Version?.ToString() ?? "1.0.0";
        
        MessageBox.Show(
            $"RecallHub\nVersion {version}\n\n" +
            "A local-first RAG (Retrieval-Augmented Generation) application.\n\n" +
            "All data stays on your machine.\nNo cloud, no tracking, no external dependencies.",
            "About RecallHub",
            MessageBoxButtons.OK,
            MessageBoxIcon.Information
        );
    }

    private void OnExitClick(object? sender, EventArgs e)
    {
        var result = MessageBox.Show(
            "Do you want to stop RecallHub services before exiting?\n\n" +
            "Yes - Stop services and exit\n" +
            "No - Exit but keep services running\n" +
            "Cancel - Don't exit",
            "Exit RecallHub",
            MessageBoxButtons.YesNoCancel,
            MessageBoxIcon.Question
        );
        
        if (result == DialogResult.Cancel)
        {
            return;
        }
        
        if (result == DialogResult.Yes)
        {
            _ = _serviceManager.StopServicesAsync();
        }
        
        // Clean up and exit
        _statusTimer.Stop();
        _trayIcon.Visible = false;
        Application.Exit();
    }

    private void ShowError(string title, string message)
    {
        MessageBox.Show(message, title, MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _statusTimer.Dispose();
            _trayIcon.Dispose();
            _contextMenu.Dispose();
        }
        
        base.Dispose(disposing);
    }
}
