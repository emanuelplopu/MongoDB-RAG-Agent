#Requires -Version 5.1
<#
.SYNOPSIS
    MongoDB RAG Agent - Full Stack Startup Script
    
.DESCRIPTION
    Starts all services (MongoDB, Backend, Frontend) using Docker Compose
    and displays all important URLs at the end.
    
.EXAMPLE
    .\start.ps1
    .\start.ps1 -Build
    .\start.ps1 -Down
#>

param(
    [switch]$Build,      # Force rebuild containers
    [switch]$Down,       # Stop all containers
    [switch]$Logs,       # Show logs after starting
    [switch]$Clean       # Clean volumes and rebuild
)

$ErrorActionPreference = "Stop"

# Colors
function Write-ColorOutput($ForegroundColor) {
    $fc = $host.UI.RawUI.ForegroundColor
    $host.UI.RawUI.ForegroundColor = $ForegroundColor
    if ($args) {
        Write-Output $args
    }
    $host.UI.RawUI.ForegroundColor = $fc
}

function Write-Header($text) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host " $text" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

function Write-Step($text) {
    Write-Host "[*] $text" -ForegroundColor Yellow
}

function Write-Success($text) {
    Write-Host "[OK] $text" -ForegroundColor Green
}

function Write-Error($text) {
    Write-Host "[ERROR] $text" -ForegroundColor Red
}

function Write-Info($text) {
    Write-Host "    $text" -ForegroundColor Gray
}

# Banner
Write-Host ""
Write-Host "  ================================================" -ForegroundColor Magenta
Write-Host "       MongoDB RAG Agent - Full Stack Setup       " -ForegroundColor Magenta
Write-Host "  ================================================" -ForegroundColor Magenta
Write-Host ""

# Change to script directory
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

# Check prerequisites
Write-Header "Checking Prerequisites"

# Check Docker
Write-Step "Checking Docker..."
try {
    $dockerVersion = docker --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Docker not found" }
    Write-Success "Docker: $dockerVersion"
} catch {
    Write-Error "Docker is not installed or not running!"
    Write-Info "Please install Docker Desktop from https://www.docker.com/products/docker-desktop"
    exit 1
}

# Check Docker Compose
Write-Step "Checking Docker Compose..."
try {
    $composeVersion = docker compose version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose not found" }
    Write-Success "Docker Compose: $composeVersion"
} catch {
    Write-Error "Docker Compose is not available!"
    exit 1
}

# Check .env file
Write-Step "Checking environment configuration..."
if (Test-Path ".env") {
    Write-Success ".env file found"
} elseif (Test-Path ".env.example") {
    Write-Info "Creating .env from .env.example..."
    Copy-Item ".env.example" ".env"
    Write-Success ".env file created - please configure your API keys!"
} else {
    Write-Error "No .env or .env.example file found!"
    exit 1
}

# Check data directory
Write-Step "Checking data directories..."
if (-not (Test-Path "data/mongoDB/db")) {
    New-Item -ItemType Directory -Path "data/mongoDB/db" -Force | Out-Null
    Write-Success "Created data/mongoDB/db directory"
}
if (-not (Test-Path "data/mongoDB/configdb")) {
    New-Item -ItemType Directory -Path "data/mongoDB/configdb" -Force | Out-Null
    Write-Success "Created data/mongoDB/configdb directory"
}
Write-Success "MongoDB data directories ready"

# Handle commands
if ($Down) {
    Write-Header "Stopping All Services"
    docker compose down
    Write-Success "All services stopped"
    exit 0
}

if ($Clean) {
    Write-Header "Cleaning and Rebuilding"
    Write-Step "Stopping containers..."
    docker compose down -v 2>$null
    Write-Step "Removing old images..."
    docker compose rm -f 2>$null
    Write-Step "Cleaning data directory..."
    if (Test-Path "data/mongoDB") {
        Remove-Item -Path "data/mongoDB/*" -Recurse -Force -ErrorAction SilentlyContinue
    }
    $Build = $true
}

# Start services
Write-Header "Starting Services"

if ($Build) {
    Write-Step "Building containers (this may take a few minutes)..."
    docker compose build --no-cache
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Build failed!"
        exit 1
    }
    Write-Success "Build complete"
}

Write-Step "Starting MongoDB..."
docker compose up -d mongodb
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to start MongoDB!"
    exit 1
}

# Wait for MongoDB to be healthy
Write-Step "Waiting for MongoDB to be ready..."
$maxAttempts = 30
$attempt = 0
do {
    $attempt++
    Start-Sleep -Seconds 2
    $health = docker inspect --format='{{.State.Health.Status}}' rag-mongodb 2>$null
    Write-Info "Attempt $attempt/$maxAttempts - Status: $health"
} while ($health -ne "healthy" -and $attempt -lt $maxAttempts)

if ($health -eq "healthy") {
    Write-Success "MongoDB is healthy"
} else {
    Write-Error "MongoDB failed to become healthy!"
    docker logs rag-mongodb --tail 20
    exit 1
}

Write-Step "Starting Backend..."
docker compose up -d backend
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to start Backend!"
    exit 1
}

# Wait for backend to be ready
Write-Step "Waiting for Backend to be ready..."
$attempt = 0
$maxAttempts = 30
do {
    $attempt++
    Start-Sleep -Seconds 2
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:11000/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        $backendReady = $response.StatusCode -eq 200
    } catch {
        $backendReady = $false
    }
    Write-Info "Attempt $attempt/$maxAttempts - Backend ready: $backendReady"
} while (-not $backendReady -and $attempt -lt $maxAttempts)

if ($backendReady) {
    Write-Success "Backend is ready"
} else {
    Write-Error "Backend failed to start!"
    docker logs rag-backend --tail 20
    exit 1
}

Write-Step "Starting Frontend..."
docker compose up -d frontend
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to start Frontend!"
    exit 1
}

# Wait for frontend
Start-Sleep -Seconds 3
Write-Success "Frontend started"

# Show logs if requested
if ($Logs) {
    Write-Header "Container Logs"
    docker compose logs -f
}

# Final status
Write-Header "Service Status"
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"

# Display URLs
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "         ALL SERVICES RUNNING          " -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Frontend (Web UI):" -ForegroundColor White
Write-Host "    http://localhost:11080" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Backend API:" -ForegroundColor White
Write-Host "    http://localhost:11000" -ForegroundColor Cyan
Write-Host ""
Write-Host "  API Documentation:" -ForegroundColor White
Write-Host "    Swagger UI:  http://localhost:11000/docs" -ForegroundColor Cyan
Write-Host "    ReDoc:       http://localhost:11000/redoc" -ForegroundColor Cyan
Write-Host ""
Write-Host "  MongoDB:" -ForegroundColor White
Write-Host "    mongodb://localhost:11017" -ForegroundColor Cyan
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Commands:" -ForegroundColor Yellow
Write-Host "    .\start.ps1 -Down     Stop all services" -ForegroundColor Gray
Write-Host "    .\start.ps1 -Build    Rebuild containers" -ForegroundColor Gray
Write-Host "    .\start.ps1 -Logs     Show live logs" -ForegroundColor Gray
Write-Host "    .\start.ps1 -Clean    Clean rebuild" -ForegroundColor Gray
Write-Host ""
Write-Host "  View logs:" -ForegroundColor Yellow
Write-Host "    docker compose logs -f" -ForegroundColor Gray
Write-Host ""
