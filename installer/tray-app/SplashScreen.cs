// RecallHub Splash Screen
// Shows branding and startup progress while services initialize

namespace RecallHub.TrayApp;

/// <summary>
/// Branded splash/loading screen shown during service startup.
/// </summary>
public class SplashScreen : Form
{
    private readonly Label _titleLabel;
    private readonly Label _subtitleLabel;
    private readonly Label _statusLabel;
    private readonly ProgressBar _progressBar;
    private readonly Button _cancelButton;
    private readonly Button _viewLogsButton;

    private bool _cancelled = false;

    private const int SplashWidth = 450;
    private const int SplashHeight = 300;

    /// <summary>
    /// Raised when the user clicks Cancel.
    /// </summary>
    public event EventHandler? Cancelled;

    /// <summary>
    /// Raised when the user clicks View Logs.
    /// </summary>
    public event EventHandler? ViewLogsRequested;

    /// <summary>
    /// Whether the user has cancelled startup.
    /// </summary>
    public bool IsCancelled => _cancelled;

    public SplashScreen()
    {
        // Form settings
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.CenterScreen;
        Size = new Size(SplashWidth, SplashHeight);
        BackColor = Color.FromArgb(24, 24, 32);
        TopMost = true;
        ShowInTaskbar = true;
        DoubleBuffered = true;

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
            // Use default
        }

        // Title
        _titleLabel = new Label
        {
            Text = "RecallHub",
            Font = new Font("Segoe UI", 28f, FontStyle.Bold),
            ForeColor = Color.White,
            AutoSize = false,
            TextAlign = ContentAlignment.MiddleCenter,
            Bounds = new Rectangle(0, 40, SplashWidth, 55)
        };
        Controls.Add(_titleLabel);

        // Subtitle
        _subtitleLabel = new Label
        {
            Text = "Local-First AI Knowledge Base",
            Font = new Font("Segoe UI", 10f, FontStyle.Regular),
            ForeColor = Color.FromArgb(160, 160, 180),
            AutoSize = false,
            TextAlign = ContentAlignment.MiddleCenter,
            Bounds = new Rectangle(0, 95, SplashWidth, 25)
        };
        Controls.Add(_subtitleLabel);

        // Progress bar
        _progressBar = new ProgressBar
        {
            Style = ProgressBarStyle.Marquee,
            MarqueeAnimationSpeed = 30,
            Bounds = new Rectangle(50, 155, SplashWidth - 100, 22)
        };
        Controls.Add(_progressBar);

        // Status label
        _statusLabel = new Label
        {
            Text = "Checking services...",
            Font = new Font("Segoe UI", 9f, FontStyle.Regular),
            ForeColor = Color.FromArgb(200, 200, 220),
            AutoSize = false,
            TextAlign = ContentAlignment.MiddleCenter,
            Bounds = new Rectangle(0, 185, SplashWidth, 25)
        };
        Controls.Add(_statusLabel);

        // Cancel button
        _cancelButton = new Button
        {
            Text = "Cancel",
            FlatStyle = FlatStyle.Flat,
            ForeColor = Color.FromArgb(200, 200, 220),
            BackColor = Color.FromArgb(50, 50, 65),
            Font = new Font("Segoe UI", 9f),
            Size = new Size(90, 30),
            Location = new Point(SplashWidth / 2 - 100, 235)
        };
        _cancelButton.FlatAppearance.BorderColor = Color.FromArgb(80, 80, 100);
        _cancelButton.Click += OnCancelClick;
        Controls.Add(_cancelButton);

        // View Logs button
        _viewLogsButton = new Button
        {
            Text = "View Logs",
            FlatStyle = FlatStyle.Flat,
            ForeColor = Color.FromArgb(200, 200, 220),
            BackColor = Color.FromArgb(50, 50, 65),
            Font = new Font("Segoe UI", 9f),
            Size = new Size(90, 30),
            Location = new Point(SplashWidth / 2 + 10, 235)
        };
        _viewLogsButton.FlatAppearance.BorderColor = Color.FromArgb(80, 80, 100);
        _viewLogsButton.Click += OnViewLogsClick;
        Controls.Add(_viewLogsButton);

        // Paint border
        Paint += OnPaint;
    }

    private void OnPaint(object? sender, PaintEventArgs e)
    {
        // Draw a subtle border around the splash
        using var pen = new Pen(Color.FromArgb(60, 60, 80), 1);
        e.Graphics.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
    }

    /// <summary>
    /// Update the status text shown on the splash screen.
    /// </summary>
    public void UpdateStatus(string message)
    {
        if (InvokeRequired)
        {
            BeginInvoke(() => UpdateStatus(message));
            return;
        }

        _statusLabel.Text = message;
    }

    /// <summary>
    /// Set the progress bar to determinate mode with a specific value.
    /// </summary>
    public void SetProgress(int value, int maximum = 100)
    {
        if (InvokeRequired)
        {
            BeginInvoke(() => SetProgress(value, maximum));
            return;
        }

        if (_progressBar.Style != ProgressBarStyle.Continuous)
        {
            _progressBar.Style = ProgressBarStyle.Continuous;
        }

        _progressBar.Maximum = maximum;
        _progressBar.Value = Math.Min(value, maximum);
    }

    /// <summary>
    /// Show a "Ready!" state and close after a brief delay.
    /// </summary>
    public async Task ShowReadyAndCloseAsync(int delayMs = 600)
    {
        if (InvokeRequired)
        {
            Invoke(() => ShowReadyAndCloseInternal());
        }
        else
        {
            ShowReadyAndCloseInternal();
        }

        await Task.Delay(delayMs);

        if (InvokeRequired)
        {
            BeginInvoke(Close);
        }
        else
        {
            Close();
        }
    }

    private void ShowReadyAndCloseInternal()
    {
        _statusLabel.Text = "Ready!";
        _statusLabel.ForeColor = Color.FromArgb(100, 220, 140);
        _progressBar.Style = ProgressBarStyle.Continuous;
        _progressBar.Value = _progressBar.Maximum;
        _cancelButton.Enabled = false;
    }

    private void OnCancelClick(object? sender, EventArgs e)
    {
        _cancelled = true;
        _cancelButton.Enabled = false;
        _statusLabel.Text = "Cancelling...";
        Cancelled?.Invoke(this, EventArgs.Empty);
    }

    private void OnViewLogsClick(object? sender, EventArgs e)
    {
        ViewLogsRequested?.Invoke(this, EventArgs.Empty);
    }

    /// <summary>
    /// Close the splash screen safely from any thread.
    /// </summary>
    public void CloseFromAnyThread()
    {
        if (IsDisposed || !IsHandleCreated)
        {
            return;
        }

        if (InvokeRequired)
        {
            BeginInvoke(Close);
        }
        else
        {
            Close();
        }
    }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        base.OnFormClosed(e);
    }

    protected override CreateParams CreateParams
    {
        get
        {
            // Add drop shadow effect
            var cp = base.CreateParams;
            cp.ClassStyle |= 0x00020000; // CS_DROPSHADOW
            return cp;
        }
    }
}
