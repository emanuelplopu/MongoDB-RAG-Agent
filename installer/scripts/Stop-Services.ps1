<#
.SYNOPSIS
    Stops RecallHub services inside WSL2.

.DESCRIPTION
    This script gracefully stops all RecallHub services.

.PARAMETER Force
    Force stop services without waiting for graceful shutdown
#>

param(
    [switch]$Force,
    [switch]$StopWSL
)

$ErrorActionPreference = "Stop"

$DISTRO_NAME = "RecallHub"
$CONFIG_PATH = "/opt/recallhub/config"

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "    [OK] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "    [WARN] $Message" -ForegroundColor Yellow
}

function Test-DistroRunning {
    param([string]$Name)
    
    try {
        $running = wsl --list --running --quiet 2>&1
        return ($running -contains $Name)
    }
    catch {
        return $false
    }
}

function Stop-RecallHubServices {
    Write-Step "Stopping RecallHub services..."
    
    $timeout = if ($Force) { 5 } else { 30 }
    
    $stopScript = @"
#!/bin/bash
cd $CONFIG_PATH
docker compose down --timeout $timeout 2>/dev/null || true
echo "COMPOSE_STOPPED"
"@
    
    try {
        $output = wsl -d $DISTRO_NAME bash -c $stopScript 2>&1
        
        if ($output -match "COMPOSE_STOPPED") {
            Write-Success "Services stopped"
            return $true
        }
        else {
            Write-Host $output
            return $true
        }
    }
    catch {
        Write-Warning "Could not stop services: $_"
        return $false
    }
}

function Stop-DockerDaemon {
    Write-Step "Stopping Docker daemon..."
    
    try {
        wsl -d $DISTRO_NAME bash -c "sudo pkill dockerd 2>/dev/null || true"
        Write-Success "Docker daemon stopped"
        return $true
    }
    catch {
        Write-Warning "Could not stop Docker daemon"
        return $false
    }
}

function Stop-WSLDistro {
    Write-Step "Terminating WSL distribution..."
    
    try {
        wsl --terminate $DISTRO_NAME 2>&1 | Out-Null
        Write-Success "WSL distribution terminated"
        return $true
    }
    catch {
        Write-Warning "Could not terminate WSL distribution"
        return $false
    }
}

function Main {
    Write-Host @"
╔═══════════════════════════════════════════════════════════════════╗
║         RecallHub Service Manager - STOP                          ║
╚═══════════════════════════════════════════════════════════════════╝
"@ -ForegroundColor Magenta

    # Check if distribution is running
    if (-not (Test-DistroRunning -Name $DISTRO_NAME)) {
        Write-Success "RecallHub is not running"
        exit 0
    }
    
    # Stop services
    Stop-RecallHubServices
    
    # Stop Docker daemon
    Stop-DockerDaemon
    
    # Optionally stop WSL
    if ($StopWSL) {
        Stop-WSLDistro
    }
    
    Write-Host "`n" + ("=" * 60) -ForegroundColor Green
    Write-Host "SERVICES STOPPED" -ForegroundColor Green
    Write-Host ("=" * 60) -ForegroundColor Green
}

# Run main
Main
