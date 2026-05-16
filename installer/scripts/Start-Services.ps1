<#
.SYNOPSIS
    Starts RecallHub services via Docker Compose.
.DESCRIPTION
    Ensures Docker Desktop is running, then starts all services
    defined in docker-compose.yml with health verification.
.PARAMETER Detached
    Run services in background (default: true)
.PARAMETER Wait
    Wait for services to become healthy before returning
.PARAMETER Timeout
    Maximum seconds to wait for services (default: 180)
#>
param(
    [switch]$Detached = $true,
    [switch]$Wait,
    [int]$Timeout = 180
)

$ErrorActionPreference = "Stop"

# Import logging module
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Logging.psm1") -Force

$logFile = Initialize-InstallLog -LogName "start-services"
Write-InstallLog "Starting RecallHub services (Docker Desktop mode)" -Source "Start-Services"

# Load configuration
$configPath = Join-Path (Split-Path $ScriptDir -Parent) "config.json"
if (Test-Path $configPath) {
    $Config = Get-Content $configPath -Raw | ConvertFrom-Json
    Write-InstallLog "Loaded config from $configPath" -Level DEBUG -Source "Start-Services"
} else {
    Write-InstallLog "Config not found at $configPath, using defaults" -Level WARN -Source "Start-Services"
    $Config = $null
}

# Resolve settings from config
$dockerDesktopPath = if ($Config.docker.desktopPath) { $Config.docker.desktopPath } else { "C:\Program Files\Docker\Docker\Docker Desktop.exe" }
$installPath = if ($Config.docker.installPath) { $Config.docker.installPath } else { "C:\RecallHub" }
$composeFile = if ($Config.docker.composeFile) { $Config.docker.composeFile } else { "docker-compose.yml" }
$startupTimeout = if ($Config.docker.startupTimeout) { $Config.docker.startupTimeout } else { $Timeout }
$healthInterval = if ($Config.docker.healthCheckInterval) { $Config.docker.healthCheckInterval } else { 5 }
$frontendPort = if ($Config.ports.frontend) { $Config.ports.frontend } else { 11080 }
$backendPort = if ($Config.ports.backend) { $Config.ports.backend } else { 11000 }

# Step 1: Ensure Docker Desktop is running
Write-InstallLog "Checking Docker daemon..." -Source "Start-Services"
$dockerReady = $false
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -eq 0) {
        $dockerReady = $true
        Write-InstallLog "Docker daemon is running" -Source "Start-Services"
    }
} catch {}

if (-not $dockerReady) {
    if (Test-Path $dockerDesktopPath) {
        Write-InstallLog "Starting Docker Desktop..." -Source "Start-Services"
        Start-Process $dockerDesktopPath
        $waited = 0
        while ($waited -lt 60) {
            Start-Sleep -Seconds 2
            $waited += 2
            try {
                $null = docker info 2>&1
                if ($LASTEXITCODE -eq 0) {
                    $dockerReady = $true
                    Write-InstallLog "Docker Desktop started after ${waited}s" -Source "Start-Services"
                    break
                }
            } catch {}
        }
    }
    if (-not $dockerReady) {
        Write-InstallLog "Docker Desktop is not running and could not be started" -Level ERROR -Source "Start-Services"
        Write-InstallDiagnostic -ErrorCode "DOCKER-001" -Component "Docker" -Message "Docker daemon not available"
        exit 1
    }
}

# Step 2: Start services via docker compose
$composePath = Join-Path $installPath $composeFile
if (-not (Test-Path $composePath)) {
    Write-InstallLog "Compose file not found: $composePath" -Level ERROR -Source "Start-Services"
    Write-InstallDiagnostic -ErrorCode "COMPOSE-001" -Component "Docker" -Message "docker-compose.yml not found at $composePath"
    exit 1
}

Write-InstallLog "Running docker compose up from $installPath" -Source "Start-Services"
Push-Location $installPath
try {
    $output = docker compose up -d 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-InstallLog "docker compose up failed: $output" -Level ERROR -Source "Start-Services"
        Write-InstallDiagnostic -ErrorCode "COMPOSE-002" -Component "Docker" -Message "docker compose up failed" -Context @{ output = "$output" }
        exit 1
    }
    Write-InstallLog "docker compose up completed successfully" -Source "Start-Services"
} finally {
    Pop-Location
}

# Step 3: Wait for health (if -Wait)
if ($Wait) {
    Write-InstallLog "Waiting for services to become healthy (timeout: ${startupTimeout}s)..." -Source "Start-Services"
    $elapsed = 0
    $healthy = $false
    while ($elapsed -lt $startupTimeout) {
        Start-Sleep -Seconds $healthInterval
        $elapsed += $healthInterval
        try {
            $frontendOk = (Invoke-WebRequest -Uri "http://localhost:$frontendPort/" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200
            $backendOk = (Invoke-WebRequest -Uri "http://localhost:${backendPort}/api/v1/status" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200
            if ($frontendOk -and $backendOk) {
                $healthy = $true
                break
            }
        } catch {
            # Services not ready yet
        }
        Write-InstallLog "Health check: waiting... (${elapsed}s / ${startupTimeout}s)" -Level DEBUG -Source "Start-Services"
    }
    if ($healthy) {
        Write-InstallLog "All services are healthy after ${elapsed}s" -Source "Start-Services"
    } else {
        Write-InstallLog "Services did not become healthy within ${startupTimeout}s" -Level WARN -Source "Start-Services"
    }
}

Write-InstallLog "Start-Services complete" -Source "Start-Services"
