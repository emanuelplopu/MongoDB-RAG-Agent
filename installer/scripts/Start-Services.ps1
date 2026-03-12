<#
.SYNOPSIS
    Starts RecallHub services inside WSL2.

.DESCRIPTION
    This script starts all RecallHub services:
    - MongoDB database
    - Ollama LLM service
    - Backend API
    - Frontend web server

.PARAMETER Detached
    Run services in the background (default: true)

.PARAMETER Wait
    Wait for services to be healthy before returning
#>

param(
    [switch]$Detached = $true,
    [switch]$Wait,
    [int]$Timeout = 120
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

function Write-Error {
    param([string]$Message)
    Write-Host "    [ERROR] $Message" -ForegroundColor Red
}

function Test-DistroExists {
    param([string]$Name)
    
    try {
        $distros = wsl --list --quiet 2>&1
        return ($distros -contains $Name)
    }
    catch {
        return $false
    }
}

function Start-WSLDistro {
    Write-Step "Starting WSL distribution..."
    
    try {
        # This will start the distribution if it's not running
        $null = wsl -d $DISTRO_NAME echo "started" 2>&1
        Write-Success "WSL distribution is running"
        return $true
    }
    catch {
        Write-Error "Failed to start WSL distribution: $_"
        return $false
    }
}

function Start-DockerDaemon {
    Write-Step "Starting Docker daemon..."
    
    try {
        # Check if Docker is already running
        $dockerStatus = wsl -d $DISTRO_NAME bash -c "docker info 2>/dev/null && echo 'RUNNING' || echo 'STOPPED'"
        
        if ($dockerStatus -match "RUNNING") {
            Write-Success "Docker daemon is already running"
            return $true
        }
        
        # Start Docker daemon
        $startScript = @"
#!/bin/bash
if ! pgrep -x dockerd > /dev/null; then
    sudo dockerd > /var/log/docker.log 2>&1 &
    sleep 5
fi
docker info > /dev/null 2>&1 && echo "STARTED" || echo "FAILED"
"@
        
        $result = wsl -d $DISTRO_NAME bash -c $startScript
        
        if ($result -match "STARTED") {
            Write-Success "Docker daemon started"
            return $true
        }
        else {
            Write-Error "Failed to start Docker daemon"
            return $false
        }
    }
    catch {
        Write-Error "Exception starting Docker: $_"
        return $false
    }
}

function Start-RecallHubServices {
    Write-Step "Starting RecallHub services..."
    
    $composeCommand = if ($Detached) { "docker compose up -d" } else { "docker compose up" }
    
    $startScript = @"
#!/bin/bash
set -e
cd $CONFIG_PATH
$composeCommand
echo "COMPOSE_STARTED"
"@
    
    try {
        $output = wsl -d $DISTRO_NAME bash -c $startScript 2>&1
        
        if ($output -match "COMPOSE_STARTED") {
            Write-Success "Docker Compose services starting"
            return $true
        }
        else {
            Write-Host $output
            Write-Warning "Services may not have started correctly"
            return $true
        }
    }
    catch {
        Write-Error "Failed to start services: $_"
        return $false
    }
}

function Wait-ForServicesHealthy {
    param([int]$TimeoutSeconds)
    
    Write-Step "Waiting for services to become healthy..."
    
    $services = @(
        @{ Name = "mongodb"; Check = "curl -sf http://localhost:27017 2>/dev/null || mongosh --eval 'db.runCommand({ping:1})' 2>/dev/null" },
        @{ Name = "ollama"; Check = "curl -sf http://localhost:11434/api/tags" },
        @{ Name = "backend"; Check = "curl -sf http://localhost:11000/api/v1/status" },
        @{ Name = "frontend"; Check = "curl -sf http://localhost:11080/" }
    )
    
    $startTime = Get-Date
    $allHealthy = $false
    
    while (-not $allHealthy -and ((Get-Date) - $startTime).TotalSeconds -lt $TimeoutSeconds) {
        $allHealthy = $true
        
        foreach ($service in $services) {
            $checkScript = "($($service.Check)) && echo 'HEALTHY' || echo 'UNHEALTHY'"
            $status = wsl -d $DISTRO_NAME bash -c $checkScript 2>&1
            
            if ($status -notmatch "HEALTHY") {
                $allHealthy = $false
                Write-Host "    Waiting for $($service.Name)..." -ForegroundColor Gray
            }
        }
        
        if (-not $allHealthy) {
            Start-Sleep -Seconds 5
        }
    }
    
    if ($allHealthy) {
        foreach ($service in $services) {
            Write-Success "$($service.Name) is healthy"
        }
        return $true
    }
    else {
        Write-Warning "Some services may not be fully healthy after $TimeoutSeconds seconds"
        return $false
    }
}

function Get-ServiceStatus {
    Write-Step "Service status:"
    
    try {
        $statusScript = @"
#!/bin/bash
cd $CONFIG_PATH
docker compose ps --format "table {{.Service}}\t{{.Status}}\t{{.Ports}}"
"@
        
        $output = wsl -d $DISTRO_NAME bash -c $statusScript 2>&1
        Write-Host $output
    }
    catch {
        Write-Warning "Could not get service status"
    }
}

function Main {
    Write-Host @"
╔═══════════════════════════════════════════════════════════════════╗
║         RecallHub Service Manager - START                         ║
╚═══════════════════════════════════════════════════════════════════╝
"@ -ForegroundColor Magenta

    # Check if distribution exists
    if (-not (Test-DistroExists -Name $DISTRO_NAME)) {
        Write-Error "RecallHub distribution not found"
        Write-Host "`n    Please run the installer first."
        exit 1
    }
    
    # Start WSL distribution
    if (-not (Start-WSLDistro)) {
        exit 1
    }
    
    # Start Docker daemon
    if (-not (Start-DockerDaemon)) {
        exit 1
    }
    
    # Start services
    if (-not (Start-RecallHubServices)) {
        exit 1
    }
    
    # Wait for healthy if requested
    if ($Wait) {
        Wait-ForServicesHealthy -TimeoutSeconds $Timeout
    }
    else {
        Start-Sleep -Seconds 3
    }
    
    # Show status
    Get-ServiceStatus
    
    Write-Host "`n" + ("=" * 60) -ForegroundColor Green
    Write-Host "SERVICES STARTED" -ForegroundColor Green
    Write-Host ("=" * 60) -ForegroundColor Green
    Write-Host "`nRecallHub is now running!"
    Write-Host "Open your browser to: http://localhost:11080"
}

# Run main
Main
