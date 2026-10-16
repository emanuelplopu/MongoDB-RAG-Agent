<#
.SYNOPSIS
    Writes the desktop tray app's runtime config.json and deploys the service scripts.
.DESCRIPTION
    RecallHubTray.exe, Start-Services.ps1 and Stop-Services.ps1 all read
    %LocalAppData%\RecallHub\config.json. Installs before this script existed never
    wrote that file, so the tray app fell back to ports 11080/11000 and used
    %LocalAppData%\RecallHub itself as the compose directory - which only matches
    the hardened-installer layout and silently broke every backup/restore install
    (and every Quellex install, which listens on 11081/11001).

    The file also carries the branding (app.name: "Quellex" for the quellex tenant,
    shown as window title, tray text and splash title) and the "network" section
    that makes the tray app announce the install on the LAN (mDNS quellex.local /
    LLMNR quellex) and shows the gateway URL in its menu - see docs/21-LAN_ACCESS.md.

    This script is called by INSTALL.ps1 and full-restore.ps1, and can be re-run
    whenever the install path, tenant or network name changes.
.PARAMETER InstallPath
    Directory that contains docker-compose.yml (e.g. C:\RecallHub).
.PARAMETER Tenant
    recallhub | quellex - selects the compose profile, the ports and the branding.
.PARAMETER TrayDir
    Tray app directory. Defaults to %LocalAppData%\RecallHub.
.PARAMETER ScriptsSource
    Where to copy the service scripts from. Defaults to <InstallPath>\installer\scripts.
.PARAMETER LanHostname
    Single-label name announced on the LAN (default: the tenant id, i.e. "quellex").
    Must match the names the gateway serves (QUELLEX_GATEWAY_HOSTS in .env).
.PARAMETER NoLanAdvertise
    Write network.advertise = false: the tray app does not answer mDNS/LLMNR queries.
.PARAMETER HttpsPort
    Host port of the TLS gateway (default 443 for quellex, 11443 for recallhub;
    must match QUELLEX_/RECALLHUB_GATEWAY_HTTPS_PORT in .env).
.PARAMETER HttpPort
    Host port of the gateway's HTTP redirect (default 80 / 11480).
#>
param(
    [Parameter(Mandatory=$true)][string]$InstallPath,
    [Parameter(Mandatory=$true)][ValidateSet("recallhub", "quellex")][string]$Tenant,
    [string]$TrayDir = "",
    [string]$ScriptsSource = "",
    [string]$LanHostname = "",
    [switch]$NoLanAdvertise,
    [int]$HttpsPort = 0,
    [int]$HttpPort = 0
)

$ErrorActionPreference = "Stop"

if (-not $TrayDir) { $TrayDir = Join-Path $env:LOCALAPPDATA "RecallHub" }
if (-not $ScriptsSource) { $ScriptsSource = Join-Path $InstallPath "installer\scripts" }

$tenantPorts = @{
    recallhub = @{ frontend = 11080; backend = 11000; https = 11443; http = 11480 }
    quellex   = @{ frontend = 11081; backend = 11001; https = 443;   http = 80 }
}
$tenantNames = @{
    recallhub = "RecallHub"
    quellex   = "Quellex"
}
$ports = $tenantPorts[$Tenant]
if ($HttpsPort -le 0) { $HttpsPort = $ports.https }
if ($HttpPort -le 0)  { $HttpPort  = $ports.http }

if (-not $LanHostname) { $LanHostname = $Tenant }
$LanHostname = $LanHostname.Trim().ToLowerInvariant()
if ($LanHostname -notmatch '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$') {
    throw "LanHostname '$LanHostname' is not a valid single-label host name (letters, digits, hyphens; no dots)"
}

# Version from pyproject.toml when available
$version = "0.8.8"
$pyproject = Join-Path $InstallPath "pyproject.toml"
if (Test-Path $pyproject) {
    $m = [regex]::Match((Get-Content $pyproject -Raw), '(?m)^version\s*=\s*"([^"]+)"')
    if ($m.Success) { $version = $m.Groups[1].Value }
}

if (-not (Test-Path $TrayDir)) { New-Item -ItemType Directory -Path $TrayDir -Force | Out-Null }

$config = [ordered]@{
    app = [ordered]@{
        name    = $tenantNames[$Tenant]
        tenant  = $Tenant
        version = $version
    }
    docker = [ordered]@{
        installPath         = $InstallPath
        composeFile         = "docker-compose.yml"
        composeProfile      = $Tenant
        startupTimeout      = 180
        healthCheckInterval = 5
        autoStart           = $true
        desktopPath         = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    }
    ports = [ordered]@{
        frontend = $ports.frontend
        backend  = $ports.backend
        mongodb  = 11017
        ollama   = 11434
    }
    network = [ordered]@{
        hostname  = $LanHostname
        advertise = (-not $NoLanAdvertise)
        httpsPort = $HttpsPort
        httpPort  = $HttpPort
    }
}

$configPath = Join-Path $TrayDir "config.json"
$config | ConvertTo-Json -Depth 5 | Set-Content -Path $configPath -Encoding UTF8
$networkUrl = if ($HttpsPort -eq 443) { "https://$LanHostname.local" } else { "https://$LanHostname.local:$HttpsPort" }
Write-Host "  [OK] Tray config written: $configPath (app=$($tenantNames[$Tenant]), tenant=$Tenant, installPath=$InstallPath, frontend=$($ports.frontend), backend=$($ports.backend))" -ForegroundColor Green
if ($NoLanAdvertise) {
    Write-Host "  [OK] LAN name announcement disabled (network URL would be $networkUrl)" -ForegroundColor Green
} else {
    Write-Host "  [OK] LAN name: $LanHostname -> $networkUrl (announced by the tray app via mDNS/LLMNR)" -ForegroundColor Green
}

# Service scripts used by the tray app
$scriptsDest = Join-Path $TrayDir "scripts"
if (Test-Path $ScriptsSource) {
    if (-not (Test-Path $scriptsDest)) { New-Item -ItemType Directory -Path $scriptsDest -Force | Out-Null }
    Get-ChildItem $ScriptsSource -File | Where-Object { $_.Extension -in @(".ps1", ".psm1") } | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $scriptsDest $_.Name) -Force
    }
    Write-Host "  [OK] Service scripts deployed to $scriptsDest" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Scripts source not found: $ScriptsSource (tray app will fall back to docker compose directly)" -ForegroundColor Yellow
}

# Leftovers from the hardened installer make the tray dir look like a compose project
# with the same project name ("recallhub") as the real stack. Quarantine them.
foreach ($stale in @("docker-compose.yml", ".env")) {
    $stalePath = Join-Path $TrayDir $stale
    if (Test-Path $stalePath) {
        $backupName = "$stale.hardened.bak"
        $backupPath = Join-Path $TrayDir $backupName
        if (Test-Path $backupPath) { Remove-Item $backupPath -Force }
        Rename-Item -Path $stalePath -NewName $backupName -Force
        Write-Host "  [OK] Moved stale $stale out of the way -> $backupName" -ForegroundColor Green
    }
}

Write-Host "  [OK] Tray app configured for tenant '$Tenant' at $InstallPath" -ForegroundColor Green
exit 0
