# Airbyte Startup Script for RecallHub
# Simplified version with core functionality

param(
    [switch]$Stop,
    [switch]$Logs,
    [switch]$Status,
    [switch]$Restart,
    [switch]$HealthCheck,
    [switch]$Cleanup,
    [switch]$Help
)

if ($Help) {
    Write-Host "Airbyte Management Script for RecallHub"
    Write-Host ""
    Write-Host "Usage: .\start-airbyte.ps1 [options]"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  (no args)  Start Airbyte services"
    Write-Host "  -Stop      Stop Airbyte services"
    Write-Host "  -Restart   Restart Airbyte services"
    Write-Host "  -Logs      Follow Airbyte logs"
    Write-Host "  -Status    Show service status"
    Write-Host "  -HealthCheck Perform comprehensive health check"
    Write-Host "  -Cleanup   Remove stopped containers and clean data"
    Write-Host "  -Help      Show this help"
    Write-Host ""
    Write-Host "Port Assignments:"
    Write-Host "  - Airbyte Webapp: http://localhost:11020"
    Write-Host "  - Airbyte API:    http://localhost:11021"
    Write-Host ""
    exit 0
}

# Check if Docker is running
function Test-DockerAvailability {
    Write-Host "Checking Docker availability..." -ForegroundColor Yellow
    try {
        $dockerInfo = docker info 2>$null
        if (-not $dockerInfo) {
            throw "Docker command failed"
        }
        $version = docker version --format "{{.Server.Version}}" 2>$null
        if (-not $version) {
            throw "Cannot get Docker version"
        }
        Write-Host "✓ Docker is running (version: $version)" -ForegroundColor Green
        return $true
    }
    catch {
        Write-Host "✗ Error: Docker is not running or not accessible" -ForegroundColor Red
        Write-Host "  Please start Docker Desktop and try again" -ForegroundColor Yellow
        return $false
    }
}

if (-not (Test-DockerAvailability)) {
    exit 1
}

# Change to project directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

if ($Stop) {
    Write-Host "Stopping Airbyte services..." -ForegroundColor Yellow
    try {
        docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml stop `
            airbyte-db airbyte-temporal airbyte-server airbyte-worker `
            airbyte-webapp airbyte-connector-builder-server 2>$null
        Write-Host "✓ Airbyte services stopped successfully" -ForegroundColor Green
    }
    catch {
        Write-Host "⚠ Warning: Some services may have already been stopped" -ForegroundColor Yellow
    }
    exit 0
}

if ($Logs) {
    Write-Host "Following Airbyte logs (Ctrl+C to exit)..." -ForegroundColor Cyan
    try {
        docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml logs -f `
            airbyte-db airbyte-temporal airbyte-server airbyte-worker airbyte-webapp
    }
    catch {
        Write-Host "Error following logs: $_" -ForegroundColor Red
        exit 1
    }
    exit 0
}

if ($Status) {
    Write-Host "Airbyte Service Status:" -ForegroundColor Cyan
    Write-Host ""
    
    try {
        docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml ps | Select-String "rag-airbyte"
    }
    catch {
        Write-Host "Error getting container status" -ForegroundColor Red
    }
    
    Write-Host ""
    
    # Check service health
    $services = @{
        "API Server" = "http://localhost:11021/api/v1/health"
        "Webapp" = "http://localhost:11020"
    }
    
    foreach ($serviceName in $services.Keys) {
        $url = $services[$serviceName]
        Write-Host "$serviceName`: " -NoNewline
        try {
            $response = Invoke-WebRequest -Uri $url -TimeoutSec 3 -ErrorAction SilentlyContinue
            if ($response.StatusCode -eq 200) {
                Write-Host "Healthy" -ForegroundColor Green
            } else {
                Write-Host "Unhealthy (Status: $($response.StatusCode))" -ForegroundColor Red
            }
        }
        catch {
            Write-Host "Not responding" -ForegroundColor Red
        }
    }
    exit 0
}

if ($Restart) {
    Write-Host "Restarting Airbyte services..." -ForegroundColor Yellow
    & $MyInvocation.MyCommand.Path -Stop > $null
    Start-Sleep -Seconds 3
    & $MyInvocation.MyCommand.Path > $null
    exit 0
}

if ($HealthCheck) {
    Write-Host "Performing comprehensive Airbyte health check..." -ForegroundColor Cyan
    Write-Host ""
    
    # Check Docker availability
    if (-not (Test-DockerAvailability)) {
        exit 1
    }
    
    # Check container status
    Write-Host "Container Status:" -ForegroundColor Yellow
    & $MyInvocation.MyCommand.Path -Status
    
    # Check data directories
    Write-Host ""
    Write-Host "Data Directories:" -ForegroundColor Yellow
    $dataDirs = @("data/airbyte/db", "data/airbyte/config", "data/airbyte/workspace", "data/airbyte/local")
    
    foreach ($dir in $dataDirs) {
        if (Test-Path $dir) {
            $itemCount = (Get-ChildItem $dir -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object).Count
            Write-Host "  $dir`: $itemCount items" -ForegroundColor Green
        } else {
            Write-Host "  $dir`: Missing" -ForegroundColor Red
        }
    }
    
    # Check connectivity
    Write-Host ""
    Write-Host "Network Connectivity:" -ForegroundColor Yellow
    $testPorts = @(11020, 11021, 5432, 7233)
    foreach ($port in $testPorts) {
        $result = Test-NetConnection -ComputerName localhost -Port $port -WarningAction SilentlyContinue -ErrorAction SilentlyContinue
        Write-Host "  Port $port`: " -NoNewline
        if ($result.TcpTestSucceeded) {
            Write-Host "Open" -ForegroundColor Green
        } else {
            Write-Host "Closed" -ForegroundColor Red
        }
    }
    
    exit 0
}

if ($Cleanup) {
    Write-Host "Cleaning up Airbyte containers and data..." -ForegroundColor Yellow
    Write-Host "WARNING: This will remove all Airbyte data!" -ForegroundColor Red
    
    $confirmation = Read-Host "Are you sure? Type 'YES' to confirm"
    if ($confirmation -ne "YES") {
        Write-Host "Cleanup cancelled." -ForegroundColor Yellow
        exit 0
    }
    
    # Stop services
    & $MyInvocation.MyCommand.Path -Stop > $null
    
    # Remove containers
    try {
        docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml down --remove-orphans 2>$null
        Write-Host "✓ Containers removed" -ForegroundColor Green
    }
    catch {
        Write-Host "⚠ Warning: Some containers may not have been removed" -ForegroundColor Yellow
    }
    
    # Remove data
    $removeData = Read-Host "Remove persisted data directories? (y/N)"
    if ($removeData -eq "y" -or $removeData -eq "Y") {
        try {
            Remove-Item -Path "data/airbyte" -Recurse -Force -ErrorAction Stop
            Write-Host "✓ Data directories removed" -ForegroundColor Green
        }
        catch {
            Write-Host "⚠ Warning: Could not remove data directories: $_" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Data directories preserved" -ForegroundColor Yellow
    }
    
    Write-Host "Cleanup complete." -ForegroundColor Green
    exit 0
}

# Default: Start services
Write-Host "Starting Airbyte services for RecallHub..." -ForegroundColor Cyan
Write-Host ""

# Pre-flight checks
Write-Host "Performing pre-flight checks..." -ForegroundColor Yellow

# Check required directories
$dataDir = "data/airbyte"
if (-not (Test-Path $dataDir)) {
    Write-Host "Creating data directory: $dataDir" -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $dataDir -Force > $null
    @("db", "config", "workspace", "local") | ForEach-Object {
        New-Item -ItemType Directory -Path "$dataDir/$_" -Force > $null
    }
}

# Check for conflicting containers
Write-Host "Checking for conflicting containers..." -ForegroundColor Yellow
$conflictingContainers = docker ps -a --format "{{.Names}}" | Select-String "rag-airbyte"
if ($conflictingContainers) {
    Write-Host "Found existing Airbyte containers. Stopping them first..." -ForegroundColor Yellow
    docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml down --remove-orphans 2>$null
    Start-Sleep -Seconds 3
}

# Pull latest images
$pullImages = Read-Host "Pull latest Airbyte images? (Recommended for first run) (Y/n)"
if ($pullImages -ne "n" -and $pullImages -ne "N") {
    Write-Host "Pulling Airbyte images..." -ForegroundColor Yellow
    try {
        docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml pull `
            airbyte-db airbyte-temporal airbyte-server airbyte-worker `
            airbyte-webapp airbyte-connector-builder-server 2>$null
        Write-Host "✓ Images pulled successfully" -ForegroundColor Green
    }
    catch {
        Write-Host "⚠ Warning: Could not pull images, using cached versions" -ForegroundColor Yellow
    }
}

# Start Airbyte services
Write-Host "Starting Airbyte containers..." -ForegroundColor Yellow
docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml up -d `
    airbyte-db airbyte-temporal airbyte-server airbyte-worker `
    airbyte-webapp airbyte-connector-builder-server

# Check if containers started successfully
Start-Sleep -Seconds 3
$runningContainers = docker ps --format "{{.Names}}" | Select-String "rag-airbyte"
Write-Host "Started $($runningContainers.Count) containers" -ForegroundColor Green

Write-Host ""
Write-Host "Waiting for Airbyte to initialize (this may take 2-3 minutes on first run)..." -ForegroundColor Yellow

# Wait for Airbyte API to be healthy
$maxAttempts = 36
$attempt = 0
$healthy = $false

while ($attempt -lt $maxAttempts -and -not $healthy) {
    $attempt++
    Start-Sleep -Seconds 5
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:11021/api/v1/health" -TimeoutSec 5 -ErrorAction SilentlyContinue
        $healthy = $true
        Write-Host "✓ Airbyte API is responding" -ForegroundColor Green
    }
    catch {
        $progress = [math]::Round(($attempt / $maxAttempts) * 100, 0)
        Write-Host "  Initializing... ($attempt/$maxAttempts) [$progress%]" -ForegroundColor Gray
    }
}

Write-Host ""
if ($healthy) {
    Write-Host "🎉 Airbyte is ready!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Access Airbyte:" -ForegroundColor Cyan
    Write-Host "  Webapp: http://localhost:11020"
    Write-Host "  API:    http://localhost:11021/api/v1/health"
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "  1. Visit the webapp to configure sources"
    Write-Host "  2. Enable Airbyte in backend: set AIRBYTE_ENABLED=true in .env"
    Write-Host "  3. Test integration with: .\start-airbyte.ps1 -HealthCheck"
    Write-Host ""
    Write-Host "Troubleshooting: .\start-airbyte.ps1 -Logs" -ForegroundColor Gray
}
else {
    Write-Host "❌ Airbyte failed to initialize within timeout period" -ForegroundColor Red
    Write-Host ""
    Write-Host "Troubleshooting steps:" -ForegroundColor Yellow
    Write-Host "  1. Check logs: .\start-airbyte.ps1 -Logs"
    Write-Host "  2. Check status: .\start-airbyte.ps1 -Status"
    Write-Host "  3. Restart: .\start-airbyte.ps1 -Restart"
    Write-Host "  4. If persistent issues: .\start-airbyte.ps1 -Cleanup then retry"
    exit 1
}
