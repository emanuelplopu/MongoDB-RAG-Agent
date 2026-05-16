<#
.SYNOPSIS
    One-time development environment setup for RecallHub/Quellex.
.DESCRIPTION
    Sets up prerequisites, builds images, and starts services for local development.
    Run this once after cloning the repo. Daily development only needs:
      docker compose --profile recallhub up -d
      cd installer/tray-app; dotnet run   (optional - desktop app in debug mode)
.PARAMETER Tenant
    Which tenant profile to set up: recallhub (default) or quellex
.PARAMETER SkipModels
    Skip Ollama model pulling (useful for fast iteration without LLM)
.PARAMETER SkipBuild
    Skip Docker image building (use if images already exist)
#>
param(
    [ValidateSet("recallhub", "quellex")]
    [string]$Tenant = "recallhub",
    [switch]$SkipModels,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  RecallHub Development Setup" -ForegroundColor Cyan
Write-Host "  Tenant: $Tenant" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ------------------------------------------------------------------
# Step 1: Check prerequisites
# ------------------------------------------------------------------
Write-Host "[1/6] Checking prerequisites..." -ForegroundColor Yellow

# Docker Desktop
$dockerOk = $false
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
} catch {}

if (-not $dockerOk) {
    Write-Host "  [!] Docker Desktop is not running." -ForegroundColor Red
    $dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerExe) {
        Write-Host "  Starting Docker Desktop..." -ForegroundColor Yellow
        Start-Process $dockerExe
        Write-Host "  Waiting for Docker daemon (up to 60s)..." -ForegroundColor Yellow
        $waited = 0
        while ($waited -lt 60) {
            Start-Sleep -Seconds 3
            $waited += 3
            try {
                $null = docker info 2>&1
                if ($LASTEXITCODE -eq 0) { $dockerOk = $true; break }
            } catch {}
        }
    }
    if (-not $dockerOk) {
        Write-Host "  [X] Docker Desktop required. Install from https://www.docker.com/products/docker-desktop/" -ForegroundColor Red
        exit 1
    }
}
Write-Host "  [OK] Docker Desktop running" -ForegroundColor Green

# Ollama
$ollamaOk = $false
try {
    $null = ollama --version 2>&1
    if ($LASTEXITCODE -eq 0) { $ollamaOk = $true }
} catch {}

if (-not $ollamaOk) {
    Write-Host "  [!] Ollama not found. Install from https://ollama.com/download" -ForegroundColor Red
    Write-Host "  (You can continue without it if using --SkipModels)" -ForegroundColor Yellow
    if (-not $SkipModels) { exit 1 }
} else {
    Write-Host "  [OK] Ollama installed" -ForegroundColor Green
}

# .NET SDK (for tray app development)
$dotnetOk = $false
try {
    $null = dotnet --version 2>&1
    if ($LASTEXITCODE -eq 0) { $dotnetOk = $true }
} catch {}
if ($dotnetOk) {
    Write-Host "  [OK] .NET SDK available (tray app development)" -ForegroundColor Green
} else {
    Write-Host "  [i] .NET SDK not found - tray app dev unavailable (optional)" -ForegroundColor DarkGray
}

# ------------------------------------------------------------------
# Step 2: Create .env if missing
# ------------------------------------------------------------------
Write-Host ""
Write-Host "[2/6] Checking environment configuration..." -ForegroundColor Yellow

$envFile = Join-Path $RepoRoot ".env"
$envExample = Join-Path $RepoRoot ".env.example"
if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Host "  [OK] Created .env from .env.example" -ForegroundColor Green
        Write-Host "  [!] Review .env and update secrets before production use" -ForegroundColor Yellow
    } else {
        Write-Host "  [!] No .env.example found - create .env manually" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [OK] .env already exists" -ForegroundColor Green
}

# ------------------------------------------------------------------
# Step 3: Build Docker images
# ------------------------------------------------------------------
Write-Host ""
Write-Host "[3/6] Building Docker images..." -ForegroundColor Yellow

if ($SkipBuild) {
    Write-Host "  Skipped (-SkipBuild flag)" -ForegroundColor DarkGray
} else {
    $buildScript = Join-Path $RepoRoot "build-backend.ps1"
    if (Test-Path $buildScript) {
        Write-Host "  Building backend images (this may take a few minutes)..." -ForegroundColor Yellow
        Push-Location $RepoRoot
        try {
            & $buildScript -All
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  [!] Build had warnings - check output above" -ForegroundColor Yellow
            } else {
                Write-Host "  [OK] Backend images built" -ForegroundColor Green
            }
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "  [i] build-backend.ps1 not found - using docker compose build" -ForegroundColor Yellow
        Push-Location $RepoRoot
        docker compose --profile $Tenant build
        Pop-Location
    }
}

# ------------------------------------------------------------------
# Step 4: Pull MongoDB image
# ------------------------------------------------------------------
Write-Host ""
Write-Host "[4/6] Ensuring MongoDB image..." -ForegroundColor Yellow

$mongoImage = docker images "mongodb/mongodb-atlas-local" --format ".Repository" 2>&1
if (-not $mongoImage -or $mongoImage -notlike "*mongodb*") {
    Write-Host "  Pulling mongodb/mongodb-atlas-local:8.0..." -ForegroundColor Yellow
    docker pull "mongodb/mongodb-atlas-local:8.0"
    Write-Host "  [OK] MongoDB image pulled" -ForegroundColor Green
} else {
    Write-Host "  [OK] MongoDB image already available" -ForegroundColor Green
}

# ------------------------------------------------------------------
# Step 5: Pull Ollama models
# ------------------------------------------------------------------
Write-Host ""
Write-Host "[5/6] Setting up Ollama models..." -ForegroundColor Yellow

if ($SkipModels) {
    Write-Host "  Skipped (-SkipModels flag)" -ForegroundColor DarkGray
} else {
    $models = @("nomic-embed-text", "llama3.2:3b")
    foreach ($model in $models) {
        Write-Host "  Pulling $model..." -ForegroundColor Yellow
        ollama pull $model
    }
    Write-Host "  [OK] Required models available" -ForegroundColor Green
    Write-Host "  [i] For full orchestrator mode, also pull: ollama pull gemma4:26b" -ForegroundColor DarkGray
}

# ------------------------------------------------------------------
# Step 6: Start services
# ------------------------------------------------------------------
Write-Host ""
Write-Host "[6/6] Starting services..." -ForegroundColor Yellow

Push-Location $RepoRoot
try {
    docker compose --profile $Tenant up -d
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Services started" -ForegroundColor Green
    } else {
        Write-Host "  [!] docker compose up had issues - check output above" -ForegroundColor Yellow
    }
} finally {
    Pop-Location
}

# ------------------------------------------------------------------
# Health check
# ------------------------------------------------------------------
Write-Host ""
Write-Host "Waiting for services to become healthy..." -ForegroundColor Yellow
$frontendPort = if ($Tenant -eq "quellex") { 11081 } else { 11080 }
$backendPort = if ($Tenant -eq "quellex") { 11001 } else { 11000 }

$healthy = $false
for ($i = 0; $i -lt 12; $i++) {
    Start-Sleep -Seconds 5
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:$frontendPort/" -UseBasicParsing -TimeoutSec 3 -ErrorAction SilentlyContinue
        if ($resp.StatusCode -eq 200) { $healthy = $true; break }
    } catch {}
    Write-Host "  Waiting... ($([math]::Round(($i+1)*5))s)" -ForegroundColor DarkGray
}

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  SETUP COMPLETE" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

if ($healthy) {
    Write-Host "  Frontend: http://localhost:$frontendPort" -ForegroundColor Green
    Write-Host "  Backend:  http://localhost:$backendPort/api/v1/status" -ForegroundColor Green
} else {
    Write-Host "  [!] Services may still be starting - check:" -ForegroundColor Yellow
    Write-Host "      docker compose --profile $Tenant ps" -ForegroundColor Yellow
    Write-Host "      docker compose --profile $Tenant logs --tail 50" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  Daily development:" -ForegroundColor Cyan
Write-Host "    docker compose --profile $Tenant up -d" -ForegroundColor White
Write-Host ""
if ($dotnetOk) {
    Write-Host "  Desktop app (debug mode with F12 dev tools):" -ForegroundColor Cyan
    Write-Host "    cd installer\tray-app; dotnet run" -ForegroundColor White
    Write-Host ""
}
Write-Host "  Stop services:" -ForegroundColor Cyan
Write-Host "    docker compose --profile $Tenant down" -ForegroundColor White
Write-Host ""
