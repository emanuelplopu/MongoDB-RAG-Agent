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
.OUTPUTS
    Exit 0 = stopped or nothing to stop, 3 = docker compose down failed.
#>
param(
    [switch]$Force,
    [int]$Timeout = 60
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
$overridePath = Join-Path $installPath "docker-compose.override.yml"
if ($composeFile -eq "docker-compose.yml" -and (Test-Path $overridePath)) { $downArgs += @("-f", $overridePath) }
if ($composeProfile) { $downArgs += @("--profile", $composeProfile) }
$downArgs += @("down", "--timeout", "$stopTimeout")

Write-InstallLog "Running: docker $($downArgs -join ' ')  (cwd: $installPath)" -Source "Stop-Services"
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
