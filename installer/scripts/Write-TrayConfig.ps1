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

    This script is called by INSTALL.ps1 and full-restore.ps1, and can be re-run
    whenever the install path or tenant changes.
.PARAMETER InstallPath
    Directory that contains docker-compose.yml (e.g. C:\RecallHub).
.PARAMETER Tenant
    recallhub | quellex - selects the compose profile and the ports.
.PARAMETER TrayDir
    Tray app directory. Defaults to %LocalAppData%\RecallHub.
.PARAMETER ScriptsSource
    Where to copy the service scripts from. Defaults to <InstallPath>\installer\scripts.
#>
param(
    [Parameter(Mandatory=$true)][string]$InstallPath,
    [Parameter(Mandatory=$true)][ValidateSet("recallhub", "quellex")][string]$Tenant,
    [string]$TrayDir = "",
    [string]$ScriptsSource = ""
)

$ErrorActionPreference = "Stop"

if (-not $TrayDir) { $TrayDir = Join-Path $env:LOCALAPPDATA "RecallHub" }
if (-not $ScriptsSource) { $ScriptsSource = Join-Path $InstallPath "installer\scripts" }

$tenantPorts = @{
    recallhub = @{ frontend = 11080; backend = 11000 }
    quellex   = @{ frontend = 11081; backend = 11001 }
}
$ports = $tenantPorts[$Tenant]

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
        name    = "RecallHub"
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
}

$configPath = Join-Path $TrayDir "config.json"
$config | ConvertTo-Json -Depth 5 | Set-Content -Path $configPath -Encoding UTF8
Write-Host "  [OK] Tray config written: $configPath (tenant=$Tenant, installPath=$InstallPath, frontend=$($ports.frontend), backend=$($ports.backend))" -ForegroundColor Green

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
