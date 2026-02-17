# Minimal Airbyte Status Checker
# Demonstrates the core functionality

param(
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [switch]$Health
)

function Test-Docker {
    try {
        docker info > $null 2>&1
        return $true
    }
    catch {
        return $false
    }
}

if (-not (Test-Docker)) {
    Write-Host "Docker is not running!" -ForegroundColor Red
    exit 1
}

if ($Status) {
    Write-Host "=== Airbyte Container Status ===" -ForegroundColor Cyan
    docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml ps 2>$null | Select-String "rag-airbyte"
    
    Write-Host "`n=== Service Health ===" -ForegroundColor Cyan
    try {
        $api = Invoke-WebRequest -Uri "http://localhost:11021/api/v1/health" -TimeoutSec 3 -ErrorAction Stop
        Write-Host "API Server: Healthy (Status $($api.StatusCode))" -ForegroundColor Green
    }
    catch {
        Write-Host "API Server: Unhealthy" -ForegroundColor Red
    }
    
    try {
        $web = Invoke-WebRequest -Uri "http://localhost:11020" -TimeoutSec 3 -ErrorAction Stop
        Write-Host "Webapp: Healthy (Status $($web.StatusCode))" -ForegroundColor Green
    }
    catch {
        Write-Host "Webapp: Unhealthy" -ForegroundColor Red
    }
    
    exit 0
}

if ($Health) {
    Write-Host "=== Health Check ===" -ForegroundColor Cyan
    
    # Check ports
    $ports = @(11020, 11021, 5432, 7233)
    Write-Host "Port Status:" -ForegroundColor Yellow
    foreach ($port in $ports) {
        $test = Test-NetConnection -ComputerName localhost -Port $port -WarningAction SilentlyContinue
        if ($test.TcpTestSucceeded) {
            Write-Host "  Port $port`: Open" -ForegroundColor Green
        } else {
            Write-Host "  Port $port`: Closed" -ForegroundColor Red
        }
    }
    
    # Check data directories
    Write-Host "`nData Directories:" -ForegroundColor Yellow
    $dirs = @("data/airbyte/db", "data/airbyte/config")
    foreach ($dir in $dirs) {
        if (Test-Path $dir) {
            Write-Host "  $dir`: Exists" -ForegroundColor Green
        } else {
            Write-Host "  $dir`: Missing" -ForegroundColor Red
        }
    }
    
    exit 0
}

if ($Stop) {
    Write-Host "Stopping Airbyte services..." -ForegroundColor Yellow
    docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml stop 2>$null
    Write-Host "Services stopped." -ForegroundColor Green
    exit 0
}

if ($Start) {
    Write-Host "Starting Airbyte services..." -ForegroundColor Yellow
    
    # Create data directories if needed
    if (-not (Test-Path "data/airbyte")) {
        mkdir "data/airbyte" > $null
        mkdir "data/airbyte/db" > $null
        mkdir "data/airbyte/config" > $null
    }
    
    # Start services
    docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml up -d 2>$null
    
    Write-Host "Waiting for services to start..." -ForegroundColor Yellow
    Start-Sleep -Seconds 10
    
    # Check if running
    $containers = docker ps --format "{{.Names}}" | Select-String "rag-airbyte"
    Write-Host "Started $($containers.Count) containers" -ForegroundColor Green
    
    Write-Host "`nAirbyte services started!" -ForegroundColor Green
    Write-Host "Webapp: http://localhost:11020" -ForegroundColor Cyan
    Write-Host "API: http://localhost:11021/api/v1/health" -ForegroundColor Cyan
    exit 0
}

# Default action: Show status
& $MyInvocation.MyCommand.Path -Status
