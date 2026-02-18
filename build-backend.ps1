# RecallHub Backend Build Helper
# ===============================
# Manages base image and fast builds
#
# Usage:
#   .\build-backend.ps1 -Base       # Build base image (one-time, ~15 min)
#   .\build-backend.ps1 -Fast       # Fast build using base image (~10 sec)
#   .\build-backend.ps1 -Full       # Full rebuild (default, ~15 min)
#   .\build-backend.ps1 -Check      # Check if base image exists

param(
    [switch]$Base,
    [switch]$Fast,
    [switch]$Full,
    [switch]$Check,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$BaseImageName = "recallhub-backend-base"
$AppImageName = "mongodb-rag-agent-backend"

function Write-Header { param([string]$Msg) Write-Host "`n=== $Msg ===" -ForegroundColor Cyan }
function Write-Success { param([string]$Msg) Write-Host "[OK] $Msg" -ForegroundColor Green }
function Write-Info { param([string]$Msg) Write-Host "[INFO] $Msg" -ForegroundColor Yellow }

function Test-BaseImageExists {
    $image = docker images --format "{{.Repository}}:{{.Tag}}" | Where-Object { $_ -eq "${BaseImageName}:latest" }
    return $null -ne $image
}

function Get-BaseImageInfo {
    if (Test-BaseImageExists) {
        $info = docker inspect ${BaseImageName}:latest --format '{{.Created}} | Size: {{.Size}}' 2>$null
        $sizeBytes = docker inspect ${BaseImageName}:latest --format '{{.Size}}' 2>$null
        $sizeGB = [math]::Round($sizeBytes / 1GB, 2)
        $created = docker inspect ${BaseImageName}:latest --format '{{.Created}}' 2>$null
        return @{
            Exists = $true
            Created = $created
            SizeGB = $sizeGB
        }
    }
    return @{ Exists = $false }
}

function Show-Help {
    Write-Host @"

RecallHub Backend Build Helper
==============================

This script manages a two-tier Docker build system for fast development:

  BASE IMAGE (${BaseImageName}:latest)
    - Contains: PyTorch, Transformers, Whisper, Docling, Playwright + Chromium
    - Size: ~10GB
    - Build time: ~15 minutes
    - Rebuild when: Dependencies change (rare)

  APP IMAGE (${AppImageName}:latest)  
    - Contains: Application code only
    - Build time: ~10-30 seconds
    - Rebuild when: Code changes (frequent)

Commands:
  .\build-backend.ps1 -Base     Build the base image (one-time setup)
  .\build-backend.ps1 -Fast     Fast build using cached base (~10 sec)
  .\build-backend.ps1 -Full     Full rebuild from scratch (~15 min)
  .\build-backend.ps1 -Check    Check base image status

Workflow:
  1. First time:  .\build-backend.ps1 -Base    # Wait ~15 min
  2. Daily dev:   .\build-backend.ps1 -Fast    # ~10 seconds!
  3. Dep change:  .\build-backend.ps1 -Base    # Rebuild base

"@
}

function Build-BaseImage {
    Write-Header "Building Base Image (this takes ~15 minutes)"
    Write-Info "This image contains all ML dependencies and Playwright"
    Write-Info "You only need to rebuild this when dependencies change"
    Write-Host ""
    
    $startTime = Get-Date
    
    docker build `
        -f backend/Dockerfile.base `
        -t ${BaseImageName}:latest `
        --progress=plain `
        .
    
    if ($LASTEXITCODE -eq 0) {
        $elapsed = (Get-Date) - $startTime
        Write-Success "Base image built successfully in $($elapsed.TotalMinutes.ToString('0.0')) minutes"
        
        $info = Get-BaseImageInfo
        Write-Info "Image size: $($info.SizeGB) GB"
        Write-Host ""
        Write-Host "Next step: Use -Fast for quick code rebuilds" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Base image build failed" -ForegroundColor Red
        exit 1
    }
}

function Build-FastImage {
    Write-Header "Fast Build (code only)"
    
    if (-not (Test-BaseImageExists)) {
        Write-Host "[ERROR] Base image not found!" -ForegroundColor Red
        Write-Host "Run '.\build-backend.ps1 -Base' first to create the base image" -ForegroundColor Yellow
        exit 1
    }
    
    $startTime = Get-Date
    
    docker build `
        -f backend/Dockerfile.fast `
        -t ${AppImageName}:latest `
        .
    
    if ($LASTEXITCODE -eq 0) {
        $elapsed = (Get-Date) - $startTime
        Write-Success "Fast build completed in $($elapsed.TotalSeconds.ToString('0.0')) seconds"
        
        Write-Host ""
        Write-Host "Restarting container..." -ForegroundColor Yellow
        docker compose up -d backend
        Write-Success "Backend updated and running!"
    } else {
        Write-Host "[ERROR] Fast build failed" -ForegroundColor Red
        exit 1
    }
}

function Build-FullImage {
    Write-Header "Full Rebuild (all layers)"
    Write-Info "This rebuilds everything from scratch (~15 minutes)"
    
    docker compose build backend
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Full build completed"
        docker compose up -d backend
    }
}

function Show-Status {
    Write-Header "Base Image Status"
    
    $info = Get-BaseImageInfo
    
    if ($info.Exists) {
        Write-Success "Base image exists: ${BaseImageName}:latest"
        Write-Host "  Created: $($info.Created)"
        Write-Host "  Size: $($info.SizeGB) GB"
        Write-Host ""
        Write-Host "You can use -Fast for quick code rebuilds" -ForegroundColor Green
    } else {
        Write-Info "Base image not found"
        Write-Host "Run '.\build-backend.ps1 -Base' to create it (one-time, ~15 min)" -ForegroundColor Yellow
    }
}

# Main
if ($Help) {
    Show-Help
} elseif ($Base) {
    Build-BaseImage
} elseif ($Fast) {
    Build-FastImage
} elseif ($Full) {
    Build-FullImage
} elseif ($Check) {
    Show-Status
} else {
    # Default: show status and help
    Show-Status
    Write-Host ""
    Write-Host "Use -Help for usage information" -ForegroundColor Gray
}
