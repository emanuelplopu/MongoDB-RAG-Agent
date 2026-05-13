#Requires -Version 5.1
<#
.SYNOPSIS
    RecallHub Complete Installation from Backup
.DESCRIPTION
    Single entry point to restore a complete RecallHub system from this backup.
    Run this script from the backup folder (e.g., from a USB drive).

    Steps performed:
    1. Install Docker Desktop + Ollama (from apps/ folder or internet)
    2. Restore Ollama models (gemma4:26b, gemma4:e4b)
    3. Full system restore (source code, MongoDB, Test_Data, Docker build, services)
.PARAMETER TestDataPath
    Where to restore Test_Data. Defaults to C:\Test_Data.
.PARAMETER InstallPath
    Where to install the RecallHub source code. Defaults to C:\RecallHub.
.PARAMETER SkipPrerequisites
    Skip Docker/Ollama installation (if already installed).
.PARAMETER SkipModels
    Skip Ollama model restore.
.PARAMETER SkipRestore
    Skip the full system restore (only install prerequisites and models).
.PARAMETER Repair
    Repair mode: skip prerequisites and models, clean up existing deployment
    (containers, images, source, test data), then re-run full restore.
    Does NOT touch Docker Desktop, Ollama, WSL, or model files.
.PARAMETER RepairFull
    Like -Repair but also re-restores Ollama models (Phase 2 + Phase 3).
.EXAMPLE
    .\INSTALL.ps1
    .\INSTALL.ps1 -TestDataPath "D:\Test_Data" -InstallPath "D:\RecallHub"
    .\INSTALL.ps1 -SkipPrerequisites
    .\INSTALL.ps1 -Repair
    .\INSTALL.ps1 -RepairFull
#>
param(
    [string]$TestDataPath = "C:\Test_Data",
    [string]$InstallPath = "C:\RecallHub",
    [switch]$SkipPrerequisites,
    [switch]$SkipModels,
    [switch]$SkipRestore,
    [switch]$Repair,
    [switch]$RepairFull
)

$ErrorActionPreference = "Stop"

# --- Self-elevation ---
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "This script requires Administrator privileges. Restarting elevated..." -ForegroundColor Yellow
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($TestDataPath -ne "C:\Test_Data") { $arguments += " -TestDataPath `"$TestDataPath`"" }
    if ($InstallPath -ne "C:\RecallHub") { $arguments += " -InstallPath `"$InstallPath`"" }
    if ($SkipPrerequisites) { $arguments += " -SkipPrerequisites" }
    if ($SkipModels) { $arguments += " -SkipModels" }
    if ($SkipRestore) { $arguments += " -SkipRestore" }
    if ($Repair) { $arguments += " -Repair" }
    if ($RepairFull) { $arguments += " -RepairFull" }
    Start-Process powershell.exe -ArgumentList $arguments -Verb RunAs -Wait
    exit
}

# --- Helper: Incremental manifest writing ---
function Update-Manifest {
    param([hashtable]$Updates)
    $manifestDir = Join-Path $env:ProgramData "RecallHub"
    $manifestFile = Join-Path $manifestDir "install-manifest.json"
    if (-not (Test-Path $manifestDir)) { New-Item $manifestDir -ItemType Directory -Force | Out-Null }

    $manifest = @{}
    if (Test-Path $manifestFile) {
        $existing = Get-Content $manifestFile -Raw | ConvertFrom-Json
        $existing.PSObject.Properties | ForEach-Object { $manifest[$_.Name] = $_.Value }
    }
    foreach ($key in $Updates.Keys) { $manifest[$key] = $Updates[$key] }
    $manifest | ConvertTo-Json -Depth 3 | Set-Content $manifestFile -Encoding UTF8
}

# --- Resolve backup directory (where this script lives) ---
$BackupDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ScriptsDir = Join-Path (Join-Path $BackupDir "source") "scripts"

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
if ($Repair -or $RepairFull) {
    $modeLabel = if ($RepairFull) { "Full Repair" } else { "Repair" }
    Write-Host "   RecallHub v0.8.8 - $modeLabel Mode" -ForegroundColor Yellow
} else {
    Write-Host "   RecallHub v0.8.8 - Complete System Installation" -ForegroundColor Cyan
}
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Backup Source:  $BackupDir" -ForegroundColor Gray
Write-Host "  Install Path:   $InstallPath" -ForegroundColor Gray
Write-Host "  Test Data Path: $TestDataPath" -ForegroundColor Gray
Write-Host ""
Write-Host "  Steps:" -ForegroundColor Gray
Write-Host "    1. Install Docker Desktop + Ollama" -ForegroundColor Gray
Write-Host "    2. Restore Ollama AI Models (~26 GB)" -ForegroundColor Gray
Write-Host "    3. Full System Restore (DB, code, data, services)" -ForegroundColor Gray
Write-Host ""

# --- Validate backup contents ---
Write-Host "[Validation] Checking backup integrity..." -ForegroundColor Yellow
$requiredDirs = @("source", "mongodb", "models", "config")
$missing = @()
foreach ($dir in $requiredDirs) {
    $path = Join-Path $BackupDir $dir
    if (-not (Test-Path $path)) { $missing += $dir }
}
if ($missing.Count -gt 0) {
    Write-Host "  ERROR: Missing required directories: $($missing -join ', ')" -ForegroundColor Red
    Write-Host "  This backup may be incomplete." -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] Backup structure validated" -ForegroundColor Green

# --- Repair mode: clean up existing deployment before restoring ---
if ($Repair -or $RepairFull) {
    Write-Host ""
    Write-Host "===================================================" -ForegroundColor Yellow
    Write-Host "  REPAIR MODE: Cleaning existing deployment" -ForegroundColor Yellow
    Write-Host "===================================================" -ForegroundColor Yellow

    # Mark install as incomplete during repair
    Update-Manifest @{ install_complete = $false }

    # Validate Docker is available (required for cleanup)
    $dockerOk = $false
    try {
        docker info 2>&1 | Out-Null
        $dockerOk = $true
    } catch {}

    if ($dockerOk) {
        # Stop and remove compose services
        if (Test-Path $InstallPath) {
            Write-Host "  Stopping Docker Compose services..." -ForegroundColor Gray
            try {
                Push-Location $InstallPath
                docker compose --profile recallhub --profile quellex down -v --remove-orphans 2>&1 | Out-Null
                Pop-Location
                Write-Host "  [OK] Compose services stopped" -ForegroundColor Green
            } catch {
                Pop-Location
                Write-Host "  [WARN] Failed to stop compose services (continuing)" -ForegroundColor Yellow
            }
        }

        # Remove leftover containers
        Write-Host "  Removing leftover containers..." -ForegroundColor Gray
        $filterNames = @("recallhub", "quellex", "rag-")
        foreach ($filterName in $filterNames) {
            $containers = docker ps -a --filter "name=$filterName" -q 2>&1
            if ($containers -and $containers -notlike "*error*") {
                foreach ($cid in $containers) {
                    if ($cid.Trim()) { docker rm -f $cid.Trim() 2>&1 | Out-Null }
                }
            }
        }

        # Remove our Docker images
        Write-Host "  Removing RecallHub Docker images..." -ForegroundColor Gray
        $ourImages = @(
            "recallhub-backend*",
            "recallhub-frontend*",
            "recallhub-worker*",
            "recallhub-base*",
            "recallhub-ml-heavy*",
            "quellex-*",
            "rag-*",
            "mongodb-rag-agent-backend*",
            "mongodb-rag-agent-frontend*",
            "mongodb-rag-agent-ingestion-worker*"
        )
        foreach ($pattern in $ourImages) {
            $imageIds = docker images --filter "reference=$pattern" -q 2>&1
            if ($imageIds -and $imageIds -notlike "*error*") {
                foreach ($imgId in $imageIds) {
                    if ($imgId.Trim()) { docker rmi -f $imgId.Trim() 2>&1 | Out-Null }
                }
            }
        }
        $pulledImages = @("mongodb/mongodb-atlas-local:8.0", "mongo:8.0", "recallhub-backend-base:latest")
        foreach ($img in $pulledImages) {
            $exists = docker images -q $img 2>&1
            if ($exists -and $exists.Trim()) { docker rmi $img 2>&1 | Out-Null }
        }
        Write-Host "  [OK] Docker images removed" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] Docker not available, skipping container/image cleanup" -ForegroundColor Yellow
    }

    # Remove source code directory
    if (Test-Path $InstallPath) {
        Write-Host "  Removing source code: $InstallPath" -ForegroundColor Gray
        try {
            Remove-Item $InstallPath -Recurse -Force
            Write-Host "  [OK] Source code removed" -ForegroundColor Green
        } catch {
            Write-Host "  [WARN] Failed to remove $InstallPath`: $_" -ForegroundColor Yellow
        }
    }

    # Remove Test_Data directory
    if (Test-Path $TestDataPath) {
        Write-Host "  Removing test data: $TestDataPath" -ForegroundColor Gray
        try {
            Remove-Item $TestDataPath -Recurse -Force
            Write-Host "  [OK] Test data removed" -ForegroundColor Green
        } catch {
            Write-Host "  [WARN] Failed to remove $TestDataPath`: $_" -ForegroundColor Yellow
        }
    }

    Write-Host "  [OK] Cleanup complete" -ForegroundColor Green
    Write-Host ""

    # In repair mode, skip Phase 1 (prerequisites) and conditionally Phase 2
    $SkipPrerequisites = $true
    if (-not $RepairFull) { $SkipModels = $true }
    $SkipRestore = $false
}

# --- Write initial manifest (tracks partial installs) ---
$ollamaModelsPath = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { "$env:USERPROFILE\.ollama\models" }
Update-Manifest @{
    version = "0.8.8"
    installed_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    install_path = $InstallPath
    test_data_path = $TestDataPath
    ollama_models_path = $ollamaModelsPath
    install_complete = $false
}
Write-Host "  [OK] Install manifest initialized" -ForegroundColor Green

# --- Step 1: Prerequisites ---
if (-not $SkipPrerequisites) {
    Write-Host "" -NoNewline
    Write-Host "===================================================" -ForegroundColor Magenta
    Write-Host "  STEP 1/3: Installing Prerequisites" -ForegroundColor Magenta
    Write-Host "===================================================" -ForegroundColor Magenta

    $prereqScript = Join-Path $ScriptsDir "install-prerequisites.ps1"
    $appsDir = Join-Path $BackupDir "apps"

    if (Test-Path $prereqScript) {
        # Capture output to parse install results
        $prereqOutput = & $prereqScript -AppsPath $appsDir 2>&1 | Out-String
        Write-Host $prereqOutput

        # Parse installation results from output markers
        $dockerInstalledByUs = $false
        $ollamaInstalledByUs = $false
        $wslEnabledByUs = $false
        $wslConfigCreatedByUs = $false

        if ($prereqOutput -match '##INSTALL_RESULTS##(.+?)##END_RESULTS##') {
            $resultsBlock = $Matches[1]
            if ($resultsBlock -match 'DOCKER_INSTALLED=True') { $dockerInstalledByUs = $true }
            if ($resultsBlock -match 'OLLAMA_INSTALLED=True') { $ollamaInstalledByUs = $true }
            if ($resultsBlock -match 'WSL_ENABLED=True') { $wslEnabledByUs = $true }
            if ($resultsBlock -match 'WSL_CONFIG_CREATED=True') { $wslConfigCreatedByUs = $true }
        }

        # Update manifest with Phase 1 results
        Update-Manifest @{
            docker_installed_by_us = $dockerInstalledByUs
            ollama_installed_by_us = $ollamaInstalledByUs
            wsl_enabled_by_us = $wslEnabledByUs
            wsl_config_created_by_us = $wslConfigCreatedByUs
            docker_settings_modified_by_us = $dockerInstalledByUs
        }
        Write-Host "  [OK] Manifest updated with prerequisite results" -ForegroundColor Green

        # Check if restart is needed (Docker first install)
        $dockerOk = $false
        try {
            docker info 2>&1 | Out-Null
            $dockerOk = $true
        } catch {}

        if (-not $dockerOk) {
            Write-Host ""
            Write-Host "  ======================================================" -ForegroundColor Yellow
            Write-Host "  RESTART REQUIRED" -ForegroundColor Yellow
            Write-Host "" -ForegroundColor Yellow
            Write-Host "  Docker Desktop was just installed and needs a" -ForegroundColor Yellow
            Write-Host "  system restart to complete setup." -ForegroundColor Yellow
            Write-Host "" -ForegroundColor Yellow
            Write-Host "  After restart:" -ForegroundColor Yellow
            Write-Host "  1. Wait for Docker Desktop to fully start" -ForegroundColor Yellow
            Write-Host "  2. Run this script again with:" -ForegroundColor Yellow
            Write-Host "     .\INSTALL.ps1 -SkipPrerequisites" -ForegroundColor Yellow
            Write-Host "  ======================================================" -ForegroundColor Yellow
            Write-Host ""
            Read-Host "Press Enter to restart now (or Ctrl+C to cancel)"
            Restart-Computer -Force
            exit
        }
    } else {
        Write-Host "  [WARN] install-prerequisites.ps1 not found at: $prereqScript" -ForegroundColor Yellow
        Write-Host "  Please install Docker Desktop and Ollama manually, then re-run with -SkipPrerequisites" -ForegroundColor Yellow
        exit 1
    }
} else {
    Write-Host ""
    Write-Host "[SKIP] Prerequisites installation skipped" -ForegroundColor DarkGray
    # Still validate they exist
    try { docker info 2>&1 | Out-Null } catch {
        Write-Host "  ERROR: Docker is not running. Install Docker Desktop and restart." -ForegroundColor Red
        exit 1
    }
    try { ollama --version 2>&1 | Out-Null } catch {
        Write-Host "  ERROR: Ollama is not installed." -ForegroundColor Red
        exit 1
    }
    # Mark prerequisites as not installed by us (skipped)
    Update-Manifest @{
        docker_installed_by_us = $false
        ollama_installed_by_us = $false
        wsl_enabled_by_us = $false
        wsl_config_created_by_us = $false
        docker_settings_modified_by_us = $false
    }
}

# --- Step 2: Restore Ollama Models ---
if (-not $SkipModels) {
    Write-Host ""
    Write-Host "===================================================" -ForegroundColor Magenta
    Write-Host "  STEP 2/3: Restoring Ollama AI Models" -ForegroundColor Magenta
    Write-Host "===================================================" -ForegroundColor Magenta

    $modelsScript = Join-Path $ScriptsDir "restore-ollama-models.ps1"
    $modelsDir = Join-Path $BackupDir "models"

    if (Test-Path $modelsScript) {
        & $modelsScript -ModelsPath $modelsDir
    } else {
        Write-Host "  [WARN] restore-ollama-models.ps1 not found" -ForegroundColor Yellow
        Write-Host "  Attempting manual model copy..." -ForegroundColor Yellow

        $ollamaHome = if ($env:OLLAMA_MODELS) { Split-Path $env:OLLAMA_MODELS -Parent } else { "$env:USERPROFILE\.ollama" }
        $destModels = Join-Path $ollamaHome "models"

        if (Test-Path (Join-Path $modelsDir "blobs")) {
            Write-Host "  Copying blobs..." -ForegroundColor Gray
            robocopy (Join-Path $modelsDir "blobs") (Join-Path $destModels "blobs") /E /MT:8 /NJH /NJS | Out-Null
        }
        if (Test-Path (Join-Path $modelsDir "manifests")) {
            Write-Host "  Copying manifests..." -ForegroundColor Gray
            robocopy (Join-Path $modelsDir "manifests") (Join-Path $destModels "manifests") /E /MT:8 /NJH /NJS | Out-Null
        }
        Write-Host "  [OK] Models copied" -ForegroundColor Green
    }
} else {
    Write-Host ""
    Write-Host "[SKIP] Model restore skipped" -ForegroundColor DarkGray
}

# Update manifest after models phase
if (-not $SkipModels) {
    Update-Manifest @{ ollama_models_restored = $true }
    Write-Host "  [OK] Manifest updated: models restored" -ForegroundColor Green
}

# --- Step 3: Full System Restore ---
if (-not $SkipRestore) {
    Write-Host ""
    Write-Host "===================================================" -ForegroundColor Magenta
    Write-Host "  STEP 3/3: Full System Restore" -ForegroundColor Magenta
    Write-Host "===================================================" -ForegroundColor Magenta

    $restoreScript = Join-Path $ScriptsDir "full-restore.ps1"

    if (Test-Path $restoreScript) {
        & $restoreScript -BackupPath $BackupDir -TestDataPath $TestDataPath -InstallPath $InstallPath
    } else {
        Write-Host "  [ERROR] full-restore.ps1 not found at: $restoreScript" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host ""
    Write-Host "[SKIP] Full system restore skipped" -ForegroundColor DarkGray
}

# --- Finalize manifest ---
Update-Manifest @{ docker_images_loaded = $true; install_complete = $true }
$manifestDir = Join-Path $env:ProgramData "RecallHub"
Write-Host "  [OK] Install manifest finalized: $manifestDir\install-manifest.json" -ForegroundColor Green

# --- Final Summary ---
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host "   INSTALLATION COMPLETE" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  RecallHub is now running at:" -ForegroundColor White
Write-Host "    RecallHub:  http://localhost:11000" -ForegroundColor White
Write-Host "    Quellex:    http://localhost:11001" -ForegroundColor White
Write-Host ""
Write-Host "  Source code:    $InstallPath" -ForegroundColor Gray
Write-Host "  Test data:      $TestDataPath" -ForegroundColor Gray
Write-Host ""
Write-Host "  To manage services:" -ForegroundColor Gray
Write-Host "    cd $InstallPath" -ForegroundColor Gray
Write-Host "    docker compose --profile recallhub up -d" -ForegroundColor Gray
Write-Host "    docker compose --profile quellex up -d" -ForegroundColor Gray
Write-Host "    docker compose down" -ForegroundColor Gray
Write-Host ""
Write-Host "  Ollama models loaded:" -ForegroundColor Gray
Write-Host "    gemma4:26b (orchestrator)" -ForegroundColor Gray
Write-Host "    gemma4:e4b (worker)" -ForegroundColor Gray
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Press any key to exit..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
