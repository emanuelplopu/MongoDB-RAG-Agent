// RecallHub Main Window
// Embedded WebView2 browser window for the RecallHub application

using System.Diagnostics;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace RecallHub.TrayApp;

/// <summary>
/// Main application window with embedded WebView2 browser control.
/// </summary>
public class MainWindow : Form
{
    private WebView2? _webView;
    private bool _webViewInitialized = false;
    private bool _isFullScreen = false;
    private FormWindowState _previousWindowState;
    private FormBorderStyle _previousBorderStyle;
    private Rectangle _previousBounds;

    private const string BaseUrl = "http://localhost:11080";
    private const int DefaultWidth = 1280;
    private const int DefaultHeight = 800;
    private const int MinWidth = 800;
    private const int MinHeight = 600;

    private static readonly string WindowStateFile = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "RecallHub",
        "window-state.json"
    );

    /// <summary>
    /// Event raised when the user closes the window (to allow tray minimization).
    /// </summary>
    public event EventHandler? WindowHiding;

    /// <summary>
    /// Indicates whether WebView2 initialized successfully.
    /// </summary>
    public bool IsWebViewReady => _webViewInitialized;

    public MainWindow()
    {
        InitializeForm();
        RestoreWindowState();
        InitializeWebView();
    }

    private void InitializeForm()
    {
        Text = "RecallHub";
        Size = new Size(DefaultWidth, DefaultHeight);
        MinimumSize = new Size(MinWidth, MinHeight);
        StartPosition = FormStartPosition.CenterScreen;
        AutoScaleMode = AutoScaleMode.Dpi;
        KeyPreview = true;

        // Load icon
        try
        {
            var iconPath = Path.Combine(AppContext.BaseDirectory, "icon.ico");
            if (File.Exists(iconPath))
            {
                Icon = new Icon(iconPath);
            }
        }
        catch
        {
            // Fall back to default icon
        }

        // Wire events
        FormClosing += OnFormClosing;
        KeyDown += OnKeyDown;
        Resize += OnResize;
    }

    private void InitializeWebView()
    {
        _webView = new WebView2
        {
            Dock = DockStyle.Fill
        };

        Controls.Add(_webView);
        _ = InitializeWebViewAsync();
    }

    private async Task InitializeWebViewAsync()
    {
        try
        {
            // Use a dedicated user data folder to avoid conflicts
            var userDataFolder = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "RecallHub",
                "WebView2Data"
            );

            Directory.CreateDirectory(userDataFolder);

            var env = await CoreWebView2Environment.CreateAsync(
                browserExecutableFolder: null,
                userDataFolder: userDataFolder,
                options: new CoreWebView2EnvironmentOptions
                {
                    AreBrowserExtensionsEnabled = false
                }
            );

            await _webView!.EnsureCoreWebView2Async(env);

            // Configure settings
            var settings = _webView.CoreWebView2.Settings;
            settings.IsStatusBarEnabled = false;
            settings.IsZoomControlEnabled = true;

#if !DEBUG
            settings.AreDefaultContextMenusEnabled = false;
            settings.AreDevToolsEnabled = false;
#endif

            // Block navigation to external URLs
            _webView.CoreWebView2.NavigationStarting += OnNavigationStarting;

            // Handle new window requests (e.g. target="_blank")
            _webView.CoreWebView2.NewWindowRequested += OnNewWindowRequested;

            _webViewInitialized = true;

            // Navigate to the app
            _webView.CoreWebView2.Navigate(BaseUrl);
        }
        catch (WebView2RuntimeNotFoundException)
        {
            HandleWebViewMissing();
        }
        catch (Exception ex)
        {
            HandleWebViewInitError(ex);
        }
    }

    private void OnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs e)
    {
        // Allow navigation within our app
        if (e.Uri.StartsWith(BaseUrl, StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        // Allow about:blank and data URIs
        if (e.Uri.StartsWith("about:", StringComparison.OrdinalIgnoreCase) ||
            e.Uri.StartsWith("data:", StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        // Block external navigation - open in system browser instead
        e.Cancel = true;
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = e.Uri,
                UseShellExecute = true
            });
        }
        catch
        {
            // Ignore failures to open external URLs
        }
    }

    private void OnNewWindowRequested(object? sender, CoreWebView2NewWindowRequestedEventArgs e)
    {
        // Prevent new windows - navigate in same view if internal, or open in browser
        e.Handled = true;

        if (e.Uri.StartsWith(BaseUrl, StringComparison.OrdinalIgnoreCase))
        {
            _webView?.CoreWebView2.Navigate(e.Uri);
        }
        else
        {
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = e.Uri,
                    UseShellExecute = true
                });
            }
            catch
            {
                // Ignore
            }
        }
    }

    private void HandleWebViewMissing()
    {
        _webViewInitialized = false;

        var result = MessageBox.Show(
            "Microsoft Edge WebView2 Runtime is not installed.\n\n" +
            "RecallHub requires WebView2 to display the application.\n" +
            "Would you like to open RecallHub in your default browser instead?",
            "WebView2 Runtime Not Found",
            MessageBoxButtons.YesNo,
            MessageBoxIcon.Warning
        );

        if (result == DialogResult.Yes)
        {
            OpenInBrowser(BaseUrl);
        }
    }

    private void HandleWebViewInitError(Exception ex)
    {
        _webViewInitialized = false;

        var result = MessageBox.Show(
            $"Failed to initialize the embedded browser.\n\n" +
            $"Error: {ex.Message}\n\n" +
            "Would you like to open RecallHub in your default browser instead?",
            "Browser Initialization Error",
            MessageBoxButtons.YesNo,
            MessageBoxIcon.Warning
        );

        if (result == DialogResult.Yes)
        {
            OpenInBrowser(BaseUrl);
        }
    }

    /// <summary>
    /// Navigate the WebView2 to a specific path within RecallHub.
    /// </summary>
    public void NavigateTo(string path)
    {
        if (_webViewInitialized && _webView?.CoreWebView2 != null)
        {
            var url = path.StartsWith("/") ? BaseUrl + path : BaseUrl + "/" + path;
            _webView.CoreWebView2.Navigate(url);
        }
    }

    /// <summary>
    /// Reload the current page.
    /// </summary>
    public void ReloadPage()
    {
        if (_webViewInitialized && _webView?.CoreWebView2 != null)
        {
            _webView.CoreWebView2.Reload();
        }
    }

    /// <summary>
    /// Open a URL in the system default browser.
    /// </summary>
    public static void OpenInBrowser(string url)
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = url,
                UseShellExecute = true
            });
        }
        catch
        {
            // Ignore
        }
    }

    private void OnFormClosing(object? sender, FormClosingEventArgs e)
    {
        if (e.CloseReason == CloseReason.UserClosing)
        {
            // Minimize to tray instead of closing
            e.Cancel = true;
            Hide();
            WindowHiding?.Invoke(this, EventArgs.Empty);
        }
        else
        {
            // Application is actually exiting - save state
            SaveWindowState();
        }
    }

    private void OnKeyDown(object? sender, KeyEventArgs e)
    {
        if (e.KeyCode == Keys.F11)
        {
            ToggleFullScreen();
            e.Handled = true;
        }
        else if (e.Control && e.KeyCode == Keys.R)
        {
            ReloadPage();
            e.Handled = true;
        }
    }

    private void OnResize(object? sender, EventArgs e)
    {
        // No specific action needed - WebView2 auto-resizes with Dock.Fill
    }

    /// <summary>
    /// Toggle fullscreen mode.
    /// </summary>
    public void ToggleFullScreen()
    {
        if (_isFullScreen)
        {
            // Restore
            FormBorderStyle = _previousBorderStyle;
            WindowState = _previousWindowState;
            Bounds = _previousBounds;
            _isFullScreen = false;
        }
        else
        {
            // Save current state
            _previousBorderStyle = FormBorderStyle;
            _previousWindowState = WindowState;
            _previousBounds = Bounds;

            // Go fullscreen
            FormBorderStyle = FormBorderStyle.None;
            WindowState = FormWindowState.Maximized;
            _isFullScreen = true;
        }
    }

    /// <summary>
    /// Show and bring window to front.
    /// </summary>
    public void ShowAndFocus()
    {
        Show();

        if (WindowState == FormWindowState.Minimized)
        {
            WindowState = FormWindowState.Normal;
        }

        Activate();
        BringToFront();
    }

    /// <summary>
    /// Force-close the window (bypass tray minimization).
    /// </summary>
    public void ForceClose()
    {
        SaveWindowState();
        FormClosing -= OnFormClosing;
        Close();
    }

    #region Window State Persistence

    private void SaveWindowState()
    {
        try
        {
            var state = new WindowState
            {
                X = Location.X,
                Y = Location.Y,
                Width = Size.Width,
                Height = Size.Height,
                IsMaximized = WindowState == FormWindowState.Maximized
            };

            var dir = Path.GetDirectoryName(WindowStateFile);
            if (dir != null)
            {
                Directory.CreateDirectory(dir);
            }

            var json = JsonSerializer.Serialize(state, new JsonSerializerOptions
            {
                WriteIndented = true
            });
            File.WriteAllText(WindowStateFile, json);
        }
        catch
        {
            // Non-critical: ignore save failures
        }
    }

    private void RestoreWindowState()
    {
        try
        {
            if (!File.Exists(WindowStateFile))
            {
                return;
            }

            var json = File.ReadAllText(WindowStateFile);
            var state = JsonSerializer.Deserialize<WindowState>(json);

            if (state == null)
            {
                return;
            }

            // Validate the saved position is on a visible screen
            var savedBounds = new Rectangle(state.X, state.Y, state.Width, state.Height);
            var isVisible = Screen.AllScreens.Any(s => s.WorkingArea.IntersectsWith(savedBounds));

            if (isVisible)
            {
                StartPosition = FormStartPosition.Manual;
                Location = new Point(state.X, state.Y);
                Size = new Size(
                    Math.Max(state.Width, MinWidth),
                    Math.Max(state.Height, MinHeight)
                );

                if (state.IsMaximized)
                {
                    WindowState = FormWindowState.Maximized;
                }
            }
        }
        catch
        {
            // Non-critical: ignore restore failures, use defaults
        }
    }

    #endregion

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _webView?.Dispose();
        }

        base.Dispose(disposing);
    }
}

/// <summary>
/// Serializable window state for persistence.
/// </summary>
internal class WindowState
{
    public int X { get; set; }
    public int Y { get; set; }
    public int Width { get; set; }
    public int Height { get; set; }
    public bool IsMaximized { get; set; }
}
