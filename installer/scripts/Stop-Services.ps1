<#
.SYNOPSIS
    Stops RecallHub / Quellex services via Docker Compose.
.DESCRIPTION
    Reads the same config.json as Start-Services.ps1 (docker.installPath,
    docker.composeFile, docker.composeProfile). Containers are removed, data
    stays in the bind-mounted data folders / named volumes. Docker Desktop
    itself is left running.
.PARAMETER Force
    Use a short 10 second container shutdown timeout.
.PARAMETER Timeout
    Maximum seconds to wait for containers to stop (default: 60).
.PARAMETER DryRun
    Print the docker command that would run and exit without stopping anything.
.OUTPUTS
    Exit 0 = stopped or nothing to stop, 3 = docker compose down failed.
#>
param(
    [switch]$Force,
    [int]$Timeout = 60,
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Logging.psm1") -Force

$logFile = Initialize-InstallLog -LogName "stop-services"
Write-InstallLog "Stopping services" -Source "Stop-Services"

function Get-ConfigValue {
    param($Object, [string]$Name, $Default)
    if ($null -ne $Object) {
        $prop = $Object.PSObject.Properties[$Name]
        if ($prop -and $null -ne $prop.Value -and "$($prop.Value)" -ne "") { return $prop.Value }
    }
    return $Default
}

function Get-ComposeProfiles {
    # Tenant profile from config.json (may be comma separated) plus COMPOSE_PROFILES
    # from <InstallPath>\.env, de-duplicated. Must match Start-Services.ps1.
    param([string]$ConfigProfile, [string]$InstallPath)
    $list = New-Object System.Collections.Generic.List[string]
    foreach ($p in ("$ConfigProfile" -split ",")) { $t = $p.Trim(); if ($t -and -not $list.Contains($t)) { $list.Add($t) } }
    $envFile = Join-Path $InstallPath ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^\s*COMPOSE_PROFILES\s*=\s*(.*)$') {
                $value = $Matches[1].Trim().Trim('"').Trim("'")
                foreach ($p in ($value -split ",")) { $t = $p.Trim(); if ($t -and -not $list.Contains($t)) { $list.Add($t) } }
            }
        }
    }
    return @($list)
}

$configPath = Join-Path (Split-Path $ScriptDir -Parent) "config.json"
$Config = $null
if (Test-Path $configPath) {
    try { $Config = Get-Content $configPath -Raw | ConvertFrom-Json } catch { $Config = $null }
}
$dockerCfg      = Get-ConfigValue $Config "docker" $null
$installPath    = Get-ConfigValue $dockerCfg "installPath" "auto"
$composeFile    = Get-ConfigValue $dockerCfg "composeFile" "docker-compose.yml"
$composeProfile = Get-ConfigValue $dockerCfg "composeProfile" ""
# "auto" (the hardened installer's config.json) or no config at all: derive the app
# root from the script location - scripts\ is one level below the compose file.
if ($installPath -eq "auto") { $installPath = Split-Path $ScriptDir -Parent }

$null = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-InstallLog "Docker daemon not running - services are already stopped" -Level WARN -Source "Stop-Services"
    exit 0
}

$composePath = Join-Path $installPath $composeFile
if (-not (Test-Path $composePath)) {
    Write-InstallLog "Compose file not found: $composePath - nothing to stop" -Level WARN -Source "Stop-Services"
    exit 0
}

$stopTimeout = if ($Force) { 10 } else { $Timeout }
$downArgs = @("compose", "-f", $composePath)
if ($composeFile -eq "docker-compose.yml") {
    # Same file set as Start-Services.ps1 and the tray app: override + network shares.
    foreach ($extra in @("docker-compose.override.yml", "docker-compose.shares.yml")) {
        $extraPath = Join-Path $installPath $extra
        if (Test-Path $extraPath) { $downArgs += @("-f", $extraPath) }
    }
}
# Same profile set as Start-Services.ps1 (config.json tenant + .env COMPOSE_PROFILES),
# otherwise e.g. the embedder container would survive "Stop Services".
$profiles = Get-ComposeProfiles -ConfigProfile $composeProfile -InstallPath $installPath
foreach ($p in $profiles) { $downArgs += @("--profile", $p) }
$downArgs += @("down", "--timeout", "$stopTimeout")

Write-InstallLog "Compose profiles: $(if ($profiles) { $profiles -join ', ' } else { '(none)' })" -Source "Stop-Services"
Write-InstallLog "Running: docker $($downArgs -join ' ')  (cwd: $installPath)" -Source "Stop-Services"
if ($DryRun) {
    Write-Host "DRY RUN: docker $($downArgs -join ' ')  (cwd: $installPath)"
    exit 0
}
Push-Location $installPath
try {
    $output = & docker @downArgs 2>&1 | ForEach-Object { "$_" }
    $exit = $LASTEXITCODE
} finally {
    Pop-Location
}
foreach ($line in $output) { Write-InstallLog "  $line" -Level DEBUG -Source "compose" }
if ($exit -ne 0) {
    Write-InstallLog "docker compose down failed with exit code $exit" -Level ERROR -Source "Stop-Services"
    exit 3
}

Write-InstallLog "Services stopped" -Source "Stop-Services"
exit 0
