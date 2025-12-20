# Airbyte Startup Script for RecallHub
# This script starts Airbyte services alongside the main application

param(
    [switch]$Stop,
    [switch]$Logs,
    [switch]$Status,
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
    Write-Host "  -Logs      Follow Airbyte logs"
    Write-Host "  -Status    Show service status"
    Write-Host "  -Help      Show this help"
    Write-Host ""
    Write-Host "Port Assignments:"
    Write-Host "  - Airbyte Webapp: http://localhost:11020"
    Write-Host "  - Airbyte API:    http://localhost:11021"
    Write-Host ""
    exit 0
}

# Check if Docker is running
$dockerRunning = docker info 2>$null
if (-not $dockerRunning) {
    Write-Host "Error: Docker is not running. Please start Docker Desktop." -ForegroundColor Red
    exit 1
}

# Change to project directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

if ($Stop) {
    Write-Host "Stopping Airbyte services..." -ForegroundColor Yellow
    docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml stop `
        airbyte-db airbyte-temporal airbyte-server airbyte-worker `
        airbyte-webapp airbyte-connector-builder-api
    Write-Host "Airbyte services stopped." -ForegroundColor Green
    exit 0
}

if ($Logs) {
    Write-Host "Following Airbyte logs (Ctrl+C to exit)..." -ForegroundColor Cyan
    docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml logs -f `
        airbyte-server airbyte-worker airbyte-webapp
    exit 0
}

if ($Status) {
    Write-Host "Airbyte Service Status:" -ForegroundColor Cyan
    Write-Host ""
    docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml ps `
        airbyte-db airbyte-temporal airbyte-server airbyte-worker `
        airbyte-webapp airbyte-connector-builder-api
    Write-Host ""
    
    # Check Airbyte API health
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:11021/api/v1/health" -TimeoutSec 5 -ErrorAction SilentlyContinue
        Write-Host "Airbyte API: " -NoNewline
        Write-Host "Healthy" -ForegroundColor Green
    }
    catch {
        Write-Host "Airbyte API: " -NoNewline
        Write-Host "Not responding" -ForegroundColor Red
    }
    exit 0
}

# Default: Start services
Write-Host "Starting Airbyte services for RecallHub..." -ForegroundColor Cyan
Write-Host ""
Write-Host "This will start:" -ForegroundColor Yellow
Write-Host "  - Airbyte Database (PostgreSQL)"
Write-Host "  - Airbyte Temporal (Workflow Engine)"
Write-Host "  - Airbyte Server (API)"
Write-Host "  - Airbyte Worker (Sync Executor)"
Write-Host "  - Airbyte Webapp (UI)"
Write-Host "  - Connector Builder Server"
Write-Host ""

# Create network if it doesn't exist
$networkExists = docker network ls --format "{{.Name}}" | Select-String "recallhub_rag-network"
if (-not $networkExists) {
    Write-Host "Creating Docker network..." -ForegroundColor Yellow
    docker-compose up -d mongodb
    Start-Sleep -Seconds 5
}

# Start Airbyte services
Write-Host "Starting Airbyte containers..." -ForegroundColor Yellow
docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml up -d `
    airbyte-db airbyte-temporal airbyte-server airbyte-worker `
    airbyte-webapp airbyte-connector-builder-api

Write-Host ""
Write-Host "Waiting for Airbyte to initialize (this may take 1-2 minutes on first run)..." -ForegroundColor Yellow

# Wait for Airbyte API to be healthy
$maxAttempts = 24
$attempt = 0
$healthy = $false

while ($attempt -lt $maxAttempts -and -not $healthy) {
    $attempt++
    Start-Sleep -Seconds 5
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:11021/api/v1/health" -TimeoutSec 5 -ErrorAction SilentlyContinue
        $healthy = $true
    }
    catch {
        Write-Host "  Waiting... ($attempt/$maxAttempts)" -ForegroundColor Gray
    }
}

Write-Host ""
if ($healthy) {
    Write-Host "Airbyte is ready!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Access Airbyte:" -ForegroundColor Cyan
    Write-Host "  Webapp: http://localhost:11020"
    Write-Host "  API:    http://localhost:11021"
    Write-Host ""
    Write-Host "To enable Airbyte in the backend, set AIRBYTE_ENABLED=true in your .env file"
}
else {
    Write-Host "Warning: Airbyte API not responding yet. It may still be initializing." -ForegroundColor Yellow
    Write-Host "Check logs with: .\start-airbyte.ps1 -Logs"
}
