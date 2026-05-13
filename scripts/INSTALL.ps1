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
.EXAMPLE
    .\INSTALL.ps1
    .\INSTALL.ps1 -TestDataPath "D:\Test_Data" -InstallPath "D:\RecallHub"
    .\INSTALL.ps1 -SkipPrerequisites
#>
param(
    [string]$TestDataPath = "C:\Test_Data",
    [string]$InstallPath = "C:\RecallHub",
    [switch]$SkipPrerequisites,
    [switch]$SkipModels,
    [switch]$SkipRestore
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
    Start-Process powershell.exe -ArgumentList $arguments -Verb RunAs -Wait
    exit
}

# --- Resolve backup directory (where this script lives) ---
$BackupDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ScriptsDir = Join-Path (Join-Path $BackupDir "source") "scripts"

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "   RecallHub v0.8.8 - Complete System Installation" -ForegroundColor Cyan
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

# --- Step 1: Prerequisites ---
if (-not $SkipPrerequisites) {
    Write-Host "" -NoNewline
    Write-Host "===================================================" -ForegroundColor Magenta
    Write-Host "  STEP 1/3: Installing Prerequisites" -ForegroundColor Magenta
    Write-Host "===================================================" -ForegroundColor Magenta

    $prereqScript = Join-Path $ScriptsDir "install-prerequisites.ps1"
    $appsDir = Join-Path $BackupDir "apps"

    if (Test-Path $prereqScript) {
        & $prereqScript -AppsPath $appsDir

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

# --- Write install manifest for uninstaller ---
$ollamaModelsPath = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { "$env:USERPROFILE\.ollama\models" }
$manifestData = @{
    installed_at = (Get-Date -Format "o")
    version = "0.8.8"
    install_path = $InstallPath
    test_data_path = $TestDataPath
    docker_installed_by_us = (-not ([bool]$SkipPrerequisites))
    ollama_installed_by_us = (-not ([bool]$SkipPrerequisites))
    wsl_enabled_by_us = (-not ([bool]$SkipPrerequisites))
    wsl_config_created_by_us = (-not ([bool]$SkipPrerequisites))
    docker_settings_modified_by_us = (-not ([bool]$SkipPrerequisites))
    ollama_models_path = $ollamaModelsPath
}
$manifestDir = Join-Path $env:ProgramData "RecallHub"
if (-not (Test-Path $manifestDir)) { New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null }
$manifestData | ConvertTo-Json | Set-Content (Join-Path $manifestDir "install-manifest.json") -Encoding UTF8
Write-Host "  [OK] Install manifest written to: $manifestDir\install-manifest.json" -ForegroundColor Green

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
