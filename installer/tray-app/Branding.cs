// Tenant branding for the desktop app
// Window title, tray texts, splash texts and icon are derived from config.json
// (app.name / app.tenant written by installer\scripts\Write-TrayConfig.ps1), so
// a Quellex install shows "Quellex" everywhere instead of the RecallHub default.

namespace RecallHub.TrayApp;

/// <summary>
/// Process-wide branding values resolved once from <see cref="AppConfig"/>.
/// </summary>
public static class Branding
{
    public const string DefaultAppName = "RecallHub";
    public const string QuellexTenant = "quellex";

    /// <summary>Display name used for the window title, tray icon and dialogs.</summary>
    public static string AppName { get; private set; } = DefaultAppName;

    /// <summary>Tenant id (compose profile): recallhub | quellex.</summary>
    public static string Tenant { get; private set; } = "recallhub";

    /// <summary>Second line on the splash screen.</summary>
    public static string Tagline { get; private set; } = "Local-First AI Knowledge Base";

    /// <summary>Icon file name looked up next to the exe and in the assets folder.</summary>
    public static string IconFileName => Tenant == QuellexTenant ? "quellex.ico" : "icon.ico";

    /// <summary>Apply the values from config.json. Safe to call with an empty config.</summary>
    public static void Configure(AppConfig config)
    {
        var tenant = (config.App?.Tenant ?? config.Docker?.ComposeProfile ?? "recallhub").Trim().ToLowerInvariant();
        Tenant = string.IsNullOrEmpty(tenant) ? "recallhub" : tenant;
        AppName = ResolveAppName(config.App?.Name, Tenant);
        Tagline = Tenant == QuellexTenant
            ? "Ihr Kanzleiwissen. Schnell gefunden."
            : "Local-First AI Knowledge Base";
    }

    /// <summary>
    /// The configured name wins, except that the legacy default "RecallHub"
    /// (written by older versions of Write-TrayConfig.ps1 for every tenant) is
    /// replaced by the tenant's own name.
    /// </summary>
    public static string ResolveAppName(string? configuredName, string tenant)
    {
        var name = configuredName?.Trim();
        bool isLegacyDefault = string.IsNullOrEmpty(name)
            || string.Equals(name, DefaultAppName, StringComparison.OrdinalIgnoreCase);

        if (!isLegacyDefault) return name!;
        return tenant == QuellexTenant ? "Quellex" : DefaultAppName;
    }

    /// <summary>
    /// Load the tenant icon. INSTALL.ps1 deploys icons to %LocalAppData%\RecallHub\assets,
    /// a dev checkout has them next to the exe; fall back to the generic icon.
    /// </summary>
    public static Icon LoadIcon()
    {
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var candidates = new[]
        {
            Path.Combine(AppContext.BaseDirectory, IconFileName),
            Path.Combine(AppContext.BaseDirectory, "assets", IconFileName),
            Path.Combine(localAppData, "RecallHub", "assets", IconFileName),
            Path.Combine(AppContext.BaseDirectory, "icon.ico"),
            Path.Combine(localAppData, "RecallHub", "assets", "icon.ico"),
        };

        foreach (var path in candidates)
        {
            try
            {
                if (File.Exists(path)) return new Icon(path);
            }
            catch
            {
                // Corrupt or locked icon file - try the next candidate
            }
        }

        try
        {
            var fromExe = Icon.ExtractAssociatedIcon(Environment.ProcessPath ?? string.Empty);
            if (fromExe != null) return fromExe;
        }
        catch
        {
        }
        return SystemIcons.Application;
    }
}
