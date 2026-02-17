# Build Script for MongoDB-RAG-Agent Docker Images (PowerShell)
# Usage: .\build.ps1 [option]
# Options:
#   light    - Build lightweight images (default) - excludes heavy ML deps
#   heavy    - Build full ML images - includes transformers, whisper, playwright
#   frontend - Build only frontend
#   backend  - Build only backend (lightweight)
#   app      - Build only app (lightweight)
#   all      - Build all images (lightweight versions)

param(
    [string]$Mode = "light"
)

$ErrorActionPreference = "Stop"

# Validate input
$validModes = @("light", "heavy", "frontend", "backend", "app", "all")
if ($validModes -notcontains $Mode) {
    Write-Host "Usage: .\build.ps1 [light|heavy|frontend|backend|app|all]" -ForegroundColor Yellow
    Write-Host "  light    - Lightweight images (default)" -ForegroundColor Yellow
    Write-Host "  heavy    - Full ML images" -ForegroundColor Yellow
    Write-Host "  frontend - Frontend only" -ForegroundColor Yellow
    Write-Host "  backend  - Backend only (light)" -ForegroundColor Yellow
    Write-Host "  app      - App only (light)" -ForegroundColor Yellow
    Write-Host "  all      - All images (light)" -ForegroundColor Yellow
    exit 1
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "MongoDB-RAG-Agent Docker Build Script" -ForegroundColor Cyan
Write-Host "Mode: $Mode" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$startTime = Get-Date

# Configuration
$buildFrontend = $true
$buildBackend = $true
$buildApp = $true
$mlHeavy = $false

switch ($Mode) {
    "light" {
        Write-Host "Building LIGHTWEIGHT images (excluding heavy ML dependencies)..." -ForegroundColor Green
        $mlHeavy = $false
    }
    "heavy" {
        Write-Host "Building HEAVY images (including full ML stack)..." -ForegroundColor Yellow
        $mlHeavy = $true
    }
    "frontend" {
        $buildFrontend = $true
        $buildBackend = $false
        $buildApp = $false
    }
    "backend" {
        $buildFrontend = $false
        $buildBackend = $true
        $buildApp = $false
        $mlHeavy = $false
    }
    "app" {
        $buildFrontend = $false
        $buildBackend = $false
        $buildApp = $true
        $mlHeavy = $false
    }
    "all" {
        Write-Host "Building ALL lightweight images..." -ForegroundColor Green
        $mlHeavy = $false
    }
}

# Function to build image
function Build-Image {
    param(
        [string]$Name,
        [string]$Dockerfile,
        [string]$Context,
        [string]$Tag = "latest"
    )
    
    Write-Host "========================================" -ForegroundColor Blue
    Write-Host "Building $Name..." -ForegroundColor Blue
    Write-Host "Dockerfile: $Dockerfile" -ForegroundColor Gray
    Write-Host "Context: $Context" -ForegroundColor Gray
    Write-Host "========================================" -ForegroundColor Blue
    
    $start = Get-Date
    
    docker build -f $Dockerfile -t "mongodb-rag-agent-$Name`:$Tag" $Context
    
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build $Name"
    }
    
    $end = Get-Date
    $duration = ($end - $start).TotalSeconds
    Write-Host "✅ $Name built in $([math]::Round($duration, 1))s" -ForegroundColor Green
    Write-Host ""
}

try {
    # Build Frontend
    if ($buildFrontend) {
        Build-Image "frontend" "frontend\Dockerfile" "frontend"
    }
    
    # Build Backend
    if ($buildBackend) {
        if ($mlHeavy) {
            Build-Image "backend" "backend\Dockerfile.ml-heavy" "."
        } else {
            Build-Image "backend" "backend\Dockerfile" "."
        }
    }
    
    # Build App (CLI)
    if ($buildApp) {
        Build-Image "app" "Dockerfile" "."
    }
    
    # Show final image sizes
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "FINAL IMAGE SIZES:" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    
    docker images mongodb-rag-agent-* --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}" | Where-Object { $_ -notmatch "<none>" }
    
    $endTime = Get-Date
    $totalDuration = ($endTime - $startTime).TotalSeconds
    
    Write-Host ""
    Write-Host "🎉 All images built successfully in $([math]::Round($totalDuration, 1))s!" -ForegroundColor Green
    Write-Host ""
    
    if (-not $mlHeavy) {
        Write-Host "Lightweight images built. For full ML capabilities, run:" -ForegroundColor Yellow
        Write-Host "  .\build.ps1 heavy" -ForegroundColor Yellow
    } else {
        Write-Host "Heavy ML images built. For lightweight version, run:" -ForegroundColor Yellow
        Write-Host "  .\build.ps1 light" -ForegroundColor Yellow
    }
}
catch {
    Write-Host "❌ Build failed: $_" -ForegroundColor Red
    exit 1
}