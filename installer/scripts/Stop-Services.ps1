<#
.SYNOPSIS
    Stops RecallHub services via Docker Compose.
.PARAMETER Force
    Use shorter timeout for container shutdown
.PARAMETER Timeout
    Maximum seconds to wait for containers to stop (default: 60)
#>
param(
    [switch]$Force,
    [int]$Timeout = 60
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Logging.psm1") -Force

$logFile = Initialize-InstallLog -LogName "stop-services"
Write-InstallLog "Stopping RecallHub services" -Source "Stop-Services"

# Load configuration
$configPath = Join-Path (Split-Path $ScriptDir -Parent) "config.json"
if (Test-Path $configPath) {
    $Config = Get-Content $configPath -Raw | ConvertFrom-Json
} else {
    $Config = $null
}

$installPath = if ($Config.docker.installPath) { $Config.docker.installPath } else { "C:\RecallHub" }
$composeFile = if ($Config.docker.composeFile) { $Config.docker.composeFile } else { "docker-compose.yml" }

# Check Docker is running
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-InstallLog "Docker daemon not running - services likely already stopped" -Level WARN -Source "Stop-Services"
        exit 0
    }
} catch {
    Write-InstallLog "Docker not available - nothing to stop" -Level WARN -Source "Stop-Services"
    exit 0
}

# Stop services
$composePath = Join-Path $installPath $composeFile
if (-not (Test-Path $composePath)) {
    Write-InstallLog "Compose file not found: $composePath" -Level WARN -Source "Stop-Services"
    exit 0
}

$stopTimeout = if ($Force) { 10 } else { $Timeout }
Write-InstallLog "Running docker compose down (timeout: ${stopTimeout}s)" -Source "Stop-Services"

Push-Location $installPath
try {
    $output = docker compose down --timeout $stopTimeout 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-InstallLog "Services stopped successfully" -Source "Stop-Services"
    } else {
        Write-InstallLog "docker compose down returned non-zero: $output" -Level WARN -Source "Stop-Services"
    }
} finally {
    Pop-Location
}

Write-InstallLog "Stop-Services complete" -Source "Stop-Services"
