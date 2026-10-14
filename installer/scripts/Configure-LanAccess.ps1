<#
.SYNOPSIS
    Windows Firewall rules for reaching the install from other computers (https://quellex.local).
.DESCRIPTION
    Opens exactly what the LAN access design needs (docs/21-LAN_ACCESS.md):
      * TCP  443 / 80   -> the Caddy gateway published by Docker Desktop (HTTPS + redirect)
      * UDP 5353 / 5355 -> the tray app's mDNS / LLMNR name announcer (quellex.local / quellex)
    Nothing else is opened: the raw frontend/API/MongoDB ports bind to 127.0.0.1
    in docker-compose.yml. By default the rules only accept traffic from the
    local subnet; pass -AnyRemote for routed office networks.

    Ports, tenant and host name are read from the tray config
    (%LocalAppData%\RecallHub\config.json, written by Write-TrayConfig.ps1) unless
    given explicitly. Requires administrator privileges (self-elevates).
.PARAMETER Remove
    Delete the rules created by this script instead of creating them.
.PARAMETER AnyRemote
    Accept connections from any address instead of LocalSubnet only.
.PARAMETER AllowTailscale
    Also accept connections from the Tailscale range (100.64.0.0/10). Tailnet
    peers cannot use mDNS, so add https://<tailscale-ip> (or the MagicDNS name)
    to QUELLEX_GATEWAY_HOSTS in .env and open that address.
.EXAMPLE
    .\Configure-LanAccess.ps1
.EXAMPLE
    .\Configure-LanAccess.ps1 -Remove
#>
param(
    [ValidateSet("", "recallhub", "quellex")][string]$Tenant = "",
    [int]$HttpsPort = 0,
    [int]$HttpPort = 0,
    [string]$TrayExe = "",
    [switch]$AnyRemote,
    [switch]$AllowTailscale,
    [switch]$Remove,
    [switch]$NoElevate
)

$ErrorActionPreference = "Stop"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
    if ($NoElevate) {
        Write-Host "  [ERROR] Administrator privileges are required to change firewall rules." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Administrator privileges are required - requesting elevation..." -ForegroundColor Yellow
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$($MyInvocation.MyCommand.Path)`"", "-NoElevate")
    foreach ($kv in $PSBoundParameters.GetEnumerator()) {
        if ($kv.Value -is [switch]) { if ($kv.Value) { $argList += "-$($kv.Key)" } }
        else { $argList += @("-$($kv.Key)", "`"$($kv.Value)`"") }
    }
    $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs -Wait -PassThru
    exit $proc.ExitCode
}

# --- Resolve settings from the tray config ----------------------------------
$trayDir = Join-Path $env:LOCALAPPDATA "RecallHub"
$configPath = Join-Path $trayDir "config.json"
$config = $null
if (Test-Path $configPath) {
    try { $config = Get-Content $configPath -Raw | ConvertFrom-Json } catch { $config = $null }
}
if (-not $Tenant) {
    $Tenant = if ($config -and $config.app -and $config.app.tenant) { [string]$config.app.tenant } else { "recallhub" }
}
$defaults = @{ recallhub = @{ https = 11443; http = 11480 }; quellex = @{ https = 443; http = 80 } }
if ($HttpsPort -le 0) { $HttpsPort = if ($config -and $config.network -and $config.network.httpsPort) { [int]$config.network.httpsPort } else { $defaults[$Tenant].https } }
if ($HttpPort -le 0)  { $HttpPort  = if ($config -and $config.network -and $config.network.httpPort)  { [int]$config.network.httpPort }  else { $defaults[$Tenant].http } }
if (-not $TrayExe)    { $TrayExe   = Join-Path $trayDir "RecallHubTray.exe" }
$hostname = if ($config -and $config.network -and $config.network.hostname) { [string]$config.network.hostname } else { $Tenant }
$appName  = if ($Tenant -eq "quellex") { "Quellex" } else { "RecallHub" }
$prefix   = "$appName LAN"
$remote   = if ($AnyRemote) { @("Any") } elseif ($AllowTailscale) { @("LocalSubnet", "100.64.0.0/10") } else { @("LocalSubnet") }
$remoteLabel = $remote -join ","

Write-Host ""
Write-Host "  $appName LAN access firewall rules" -ForegroundColor Cyan
Write-Host "  tenant=$Tenant  https=$HttpsPort  http=$HttpPort  name=$hostname.local  remote=$remoteLabel" -ForegroundColor Gray

# --- Remove previous rules (idempotent) --------------------------------------
$existing = @(Get-NetFirewallRule -DisplayName "$prefix - *" -ErrorAction SilentlyContinue)
if ($existing.Count -gt 0) {
    $existing | Remove-NetFirewallRule
    Write-Host "  [OK] Removed $($existing.Count) existing rule(s)" -ForegroundColor Green
}
if ($Remove) {
    Write-Host "  [OK] LAN access rules removed" -ForegroundColor Green
    exit 0
}

# --- Gateway (Docker Desktop publishes the ports on the host) ----------------
New-NetFirewallRule -DisplayName "$prefix - Gateway HTTPS (TCP $HttpsPort)" `
    -Description "$appName TLS gateway (Caddy container) - the address other computers use" `
    -Direction Inbound -Protocol TCP -LocalPort $HttpsPort -RemoteAddress $remote `
    -Action Allow -Profile Any -Enabled True | Out-Null
Write-Host "  [OK] Allow TCP $HttpsPort from $remoteLabel (HTTPS)" -ForegroundColor Green

New-NetFirewallRule -DisplayName "$prefix - Gateway HTTP redirect (TCP $HttpPort)" `
    -Description "$appName gateway: redirects http:// to https://" `
    -Direction Inbound -Protocol TCP -LocalPort $HttpPort -RemoteAddress $remote `
    -Action Allow -Profile Any -Enabled True | Out-Null
Write-Host "  [OK] Allow TCP $HttpPort from $remoteLabel (HTTP -> HTTPS redirect)" -ForegroundColor Green

# --- Name announcer (tray app process) --------------------------------------
if (Test-Path $TrayExe) {
    New-NetFirewallRule -DisplayName "$prefix - Name announcer mDNS (UDP 5353)" `
        -Description "$appName tray app answers '$hostname.local' (multicast DNS)" `
        -Direction Inbound -Protocol UDP -LocalPort 5353 -Program $TrayExe -RemoteAddress $remote `
        -Action Allow -Profile Any -Enabled True | Out-Null
    New-NetFirewallRule -DisplayName "$prefix - Name announcer LLMNR (UDP 5355)" `
        -Description "$appName tray app answers '$hostname' (LLMNR, Windows clients)" `
        -Direction Inbound -Protocol UDP -LocalPort 5355 -Program $TrayExe -RemoteAddress $remote `
        -Action Allow -Profile Any -Enabled True | Out-Null
    Write-Host "  [OK] Allow UDP 5353/5355 to $TrayExe (name announcer)" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Tray app not found at $TrayExe - name announcer rules skipped; re-run after INSTALL.ps1 deployed it" -ForegroundColor Yellow
}

$url = if ($HttpsPort -eq 443) { "https://$hostname.local" } else { "https://$hostname.local:$HttpsPort" }
Write-Host ""
Write-Host "  LAN access configured. Other computers open: $url" -ForegroundColor Green
Write-Host "  To avoid browser certificate warnings, distribute the gateway's root certificate:" -ForegroundColor Gray
Write-Host "    installer\scripts\Export-LanCertificate.ps1" -ForegroundColor Gray
exit 0
