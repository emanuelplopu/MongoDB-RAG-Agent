#Requires -Version 5.1
<#
.SYNOPSIS
    RecallHub/Quellex Complete Installation from Backup (Tenant-Aware)
.DESCRIPTION
    Single entry point to restore a complete system from this backup.
    Automatically detects the tenant from manifest.json (recallhub or quellex).
    Run this script from the backup folder (e.g., from a USB drive).

    Steps performed:
    1. Install Docker Desktop + Ollama (from apps/ folder or internet)
    2. Restore Ollama models (gemma4:26b, gemma4:e4b)
    3. Full system restore (source code, MongoDB, Test_Data, Docker build, services)
    4. Deploy Desktop App (WebView2 tray app, shortcuts, protocol handler)
.PARAMETER TestDataPath
    Where to restore Test_Data. Defaults to C:\Test_Data.
.PARAMETER InstallPath
    Where to install the source code. Defaults to C:\<TenantName> based on manifest.
.PARAMETER SkipPrerequisites
    Skip Docker/Ollama installation (if already installed).
.PARAMETER SkipModels
    Skip Ollama model restore.
.PARAMETER SkipRestore
    Skip the full system restore (only install prerequisites and models).
.PARAMETER SkipDesktopApp
    Skip desktop app deployment (Phase 4).
.PARAMETER Repair
    Repair mode: skip prerequisites and models, clean up existing deployment
    (containers, images, source, test data), then re-run full restore.
    Does NOT touch Docker Desktop, Ollama, WSL, or model files.
.PARAMETER RepairFull
    Like -Repair but also re-restores Ollama models (Phase 2 + Phase 3).
.PARAMETER PurgeTestData
    In repair mode also delete the Test_Data folder (it is kept by default).
.PARAMETER TakeOverStack
    Allow this install to take over a stack that was started from a different
    install folder (the compose project name is pinned to "recallhub", so only
    one install folder can own the containers on a machine).
.PARAMETER NoLaunch
    Do not start the desktop app at the end (automation / tests).
.PARAMETER NonInteractive
    Never wait for a key press or an Enter (automation / tests).
.PARAMETER NoLanAccess
    Skip the Windows Firewall rules for network access (https://<tenant>.local)
    and tell the tray app not to announce the name on the LAN. The stack still
    starts the TLS gateway; see docs/21-LAN_ACCESS.md.
.PARAMETER LanHostname
    Name other computers use (default: the tenant id, e.g. quellex -> https://quellex.local).
.EXAMPLE
    .\INSTALL.ps1
    .\INSTALL.ps1 -TestDataPath "D:\Test_Data" -InstallPath "D:\Quellex"
    .\INSTALL.ps1 -SkipPrerequisites
    .\INSTALL.ps1 -Repair
    .\INSTALL.ps1 -RepairFull
#>
param(
    [string]$TestDataPath = "C:\Test_Data",
    [string]$InstallPath = "",
    [switch]$SkipPrerequisites,
    [switch]$SkipModels,
    [switch]$SkipRestore,
    [switch]$Repair,
    [switch]$RepairFull,
    [switch]$SkipDesktopApp,
    [switch]$PurgeTestData,
    [switch]$TakeOverStack,
    [switch]$NoLaunch,
    [switch]$NonInteractive,
    [switch]$NoLanAccess,
    [string]$LanHostname = ""
)

$ErrorActionPreference = "Stop"

# --- Self-elevation ---
# Administrator rights are only needed for Phase 1 (Docker Desktop / Ollama /
# Windows features). Everything else runs as the logged-in user, which keeps
# %LocalAppData% paths (tray app, Ollama models) on the right profile.
$needsElevation = -not ($SkipPrerequisites -or $Repair -or $RepairFull)
if ($needsElevation -and -not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "This script requires Administrator privileges. Restarting elevated..." -ForegroundColor Yellow
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($TestDataPath -ne "C:\Test_Data") { $arguments += " -TestDataPath `"$TestDataPath`"" }
    if ($InstallPath) { $arguments += " -InstallPath `"$InstallPath`"" }
    if ($SkipPrerequisites) { $arguments += " -SkipPrerequisites" }
    if ($SkipModels) { $arguments += " -SkipModels" }
    if ($SkipRestore) { $arguments += " -SkipRestore" }
    if ($Repair) { $arguments += " -Repair" }
    if ($RepairFull) { $arguments += " -RepairFull" }
    if ($SkipDesktopApp) { $arguments += " -SkipDesktopApp" }
    if ($PurgeTestData) { $arguments += " -PurgeTestData" }
    if ($TakeOverStack) { $arguments += " -TakeOverStack" }
    if ($NoLaunch) { $arguments += " -NoLaunch" }
    if ($NonInteractive) { $arguments += " -NonInteractive" }
    Start-Process powershell.exe -ArgumentList $arguments -Verb RunAs -Wait
    exit
}

# --- Helper: Incremental manifest writing ---
function Get-ManifestPath {
    # Prefer ProgramData, but only if THIS user can actually write the manifest there.
    # An earlier elevated install leaves a file that an unelevated run cannot modify,
    # so the probe must test the real file, not just the folder.
    $candidates = @((Join-Path $env:ProgramData "RecallHub"), (Join-Path $env:LOCALAPPDATA "RecallHub"))
    foreach ($dir in $candidates) {
        $file = Join-Path $dir "install-manifest.json"
        try {
            if (-not (Test-Path $dir)) { New-Item $dir -ItemType Directory -Force -ErrorAction Stop | Out-Null }
            if (Test-Path $file) {
                $fs = [System.IO.File]::Open($file, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::ReadWrite)
                $fs.Close()
            } else {
                $probe = Join-Path $dir ".write-probe"
                Set-Content -Path $probe -Value "x" -ErrorAction Stop
                Remove-Item $probe -Force -ErrorAction SilentlyContinue
            }
            return $file
        } catch { }
    }
    return (Join-Path (Join-Path $env:LOCALAPPDATA "RecallHub") "install-manifest.json")
}

function Update-Manifest {
    param([hashtable]$Updates)
    $manifestFile = Get-ManifestPath
    $manifestDir = Split-Path $manifestFile -Parent
    # When falling back to %LocalAppData%, start from the (read-only) ProgramData
    # manifest so the install history is carried over instead of lost.
    $programDataManifest = Join-Path (Join-Path $env:ProgramData "RecallHub") "install-manifest.json"
    if (-not (Test-Path $manifestFile) -and ($manifestFile -ne $programDataManifest) -and (Test-Path $programDataManifest)) {
        try { Copy-Item $programDataManifest $manifestFile -Force -ErrorAction Stop } catch { }
    }

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

# --- Read tenant from backup manifest ---
$manifestFile = Join-Path $BackupDir "manifest.json"
$tenant = "recallhub"  # default
if (Test-Path $manifestFile) {
    try {
        $backupManifest = Get-Content $manifestFile -Raw | ConvertFrom-Json
        if ($backupManifest.tenant) {
            $tenant = $backupManifest.tenant
        }
    } catch {
        Write-Host "  [WARN] Could not read manifest.json, defaulting to recallhub" -ForegroundColor Yellow
    }
}
$tenantName = $tenant.Substring(0,1).ToUpper() + $tenant.Substring(1)
Write-Host "  Tenant: $tenantName" -ForegroundColor Cyan

# --- Compute default InstallPath if not provided ---
if (-not $InstallPath) {
    $InstallPath = "C:\$tenantName"
}

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
if ($Repair -or $RepairFull) {
    $modeLabel = if ($RepairFull) { "Full Repair" } else { "Repair" }
    Write-Host "   $tenantName v0.8.8 - $modeLabel Mode" -ForegroundColor Yellow
} else {
    Write-Host "   $tenantName v0.8.8 - Complete System Installation" -ForegroundColor Cyan
}
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Installing:     $tenantName" -ForegroundColor Cyan
Write-Host "  Backup Source:  $BackupDir" -ForegroundColor Gray
Write-Host "  Install Path:   $InstallPath" -ForegroundColor Gray
Write-Host "  Test Data Path: $TestDataPath" -ForegroundColor Gray
Write-Host ""
Write-Host "  Steps:" -ForegroundColor Gray
Write-Host "    1. Install Docker Desktop + Ollama" -ForegroundColor Gray
Write-Host "    2. Restore Ollama AI Models (~26 GB)" -ForegroundColor Gray
Write-Host "    3. Full System Restore (DB, code, data, services)" -ForegroundColor Gray
Write-Host "    4. Deploy Desktop App (WebView2 tray app)" -ForegroundColor Gray
Write-Host ""

# --- Validate backup contents ---
Write-Host "[Validation] Checking backup integrity..." -ForegroundColor Yellow
$requiredDirs = @("source", "mongodb", "config")
if (-not ($SkipModels -or $Repair)) { $requiredDirs += "models" }
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
if (-not (Test-Path (Join-Path $BackupDir "images"))) {
    Write-Host "  [WARN] No images/ folder in this backup - Docker images will have to be BUILT (needs the base image or internet)" -ForegroundColor Yellow
}
if (-not (Test-Path (Join-Path $BackupDir "manifest.json"))) {
    Write-Host "  [WARN] No manifest.json - this package was not finished by full-backup.ps1; tenant defaults to recallhub" -ForegroundColor Yellow
}

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
                try {
                    $null = docker compose --profile $tenant down -v --remove-orphans 2>&1
                } catch {
                    # Docker writes informational messages to stderr (e.g., "Container ... Stopping")
                }
                if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) {
                    Write-Host "  [WARN] docker compose down returned exit code $LASTEXITCODE (continuing)" -ForegroundColor Yellow
                } else {
                    Write-Host "  [OK] Compose services stopped" -ForegroundColor Green
                }
                Pop-Location
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

    # Remove Test_Data directory (opt-in: it is user data, not an install artifact)
    if ($PurgeTestData -and (Test-Path $TestDataPath)) {
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

# --- Guard: stack owned by a different install folder ---
# docker-compose.yml pins the project name to "recallhub", so a stack started from
# another folder (e.g. C:\RecallHub while installing to C:\Quellex) would be taken
# over and its containers recreated against this folder's data directories.
if (-not ($Repair -or $RepairFull)) {
    $ownerDir = $null
    try {
        $lsJson = docker compose ls --all --format json 2>$null | Out-String
        if ($lsJson.Trim()) {
            $projects = $lsJson | ConvertFrom-Json
            foreach ($proj in @($projects)) {
                if ($proj.Name -eq "recallhub" -and $proj.ConfigFiles) {
                    $ownerDir = Split-Path (($proj.ConfigFiles -split ",")[0].Trim()) -Parent
                }
            }
        }
    } catch { }
    if ($ownerDir -and ($ownerDir.TrimEnd('\') -ne $InstallPath.TrimEnd('\'))) {
        Write-Host "" 
        Write-Host "  A $tenantName/RecallHub stack already runs from: $ownerDir" -ForegroundColor Yellow
        Write-Host "  This install targets:                       $InstallPath" -ForegroundColor Yellow
        Write-Host "  Only one install folder can own the containers on a machine." -ForegroundColor Yellow
        if (-not $TakeOverStack) {
            Write-Host "  Re-run with -InstallPath `"$ownerDir`" to update that install, or add -TakeOverStack" -ForegroundColor Yellow
            Write-Host "  to move the stack here (data in $ownerDir\data is left untouched)." -ForegroundColor Yellow
            exit 2
        }
        Write-Host "  -TakeOverStack given: the stack will be recreated from $InstallPath" -ForegroundColor Yellow
    }
}

# --- Write initial manifest (tracks partial installs) ---
$ollamaModelsPath = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { "$env:USERPROFILE\.ollama\models" }
Update-Manifest @{
    version = "0.8.8"
    tenant = $tenant
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
    Write-Host "  STEP 1/4: Installing Prerequisites" -ForegroundColor Magenta
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
            if ($NonInteractive) {
                Write-Host "  Non-interactive mode: restart the machine, then re-run with -SkipPrerequisites" -ForegroundColor Yellow
                exit 3010
            }
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
    Write-Host "  STEP 2/4: Restoring Ollama AI Models" -ForegroundColor Magenta
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
    Write-Host "  STEP 3/4: Full System Restore" -ForegroundColor Magenta
    Write-Host "===================================================" -ForegroundColor Magenta

    $restoreScript = Join-Path $ScriptsDir "full-restore.ps1"

    if (Test-Path $restoreScript) {
        & $restoreScript -BackupPath $BackupDir -TestDataPath $TestDataPath -InstallPath $InstallPath -Tenant $tenant
        $restoreExit = $LASTEXITCODE
        if ($restoreExit -ne 0) {
            Update-Manifest @{ install_complete = $false; last_error = "full-restore.ps1 exited with code $restoreExit" }
            Write-Host "  [ERROR] full-restore.ps1 failed (exit code $restoreExit). Installation is NOT complete." -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "  [ERROR] full-restore.ps1 not found at: $restoreScript" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host ""
    Write-Host "[SKIP] Full system restore skipped" -ForegroundColor DarkGray
}

# --- Step 4: Deploy Desktop App ---
if (-not $SkipDesktopApp) {
    Write-Host ""
    Write-Host "===================================================" -ForegroundColor Magenta
    Write-Host "  STEP 4/4: Deploy Desktop App" -ForegroundColor Magenta
    Write-Host "===================================================" -ForegroundColor Magenta

    # Runtime config for the tray app and the service scripts. Without this the
    # app used %LocalAppData%\RecallHub as compose directory and ports 11080/11000
    # regardless of tenant - the main reason earlier test installs never worked.
    $trayCfgScript = Join-Path $InstallPath "installer\scripts\Write-TrayConfig.ps1"
    if (Test-Path $trayCfgScript) {
        $trayCfgArgs = @{ InstallPath = $InstallPath; Tenant = $tenant }
        if ($LanHostname) { $trayCfgArgs.LanHostname = $LanHostname }
        if ($NoLanAccess) { $trayCfgArgs.NoLanAdvertise = $true }
        & $trayCfgScript @trayCfgArgs
    } else {
        Write-Host "  [WARN] Write-TrayConfig.ps1 not found at $trayCfgScript - tray app will use defaults" -ForegroundColor Yellow
    }

    # Locate the tray app executable (newest of the known publish locations)
    $trayExe = $null
    $primaryPath = Join-Path $InstallPath "installer"
    $primaryPath = Join-Path $primaryPath "tray-app"
    $primaryPath = Join-Path $primaryPath "bin"
    $primaryPath = Join-Path $primaryPath "publish"
    $primaryPath = Join-Path $primaryPath "RecallHubTray.exe"

    $candidates = @()
    if (Test-Path $primaryPath) { $candidates += Get-Item $primaryPath }
    if ($true) {
        # Alternate publish path (dotnet publish -c Release)
        $altPath = Join-Path $InstallPath "installer"
        $altPath = Join-Path $altPath "tray-app"
        $altPath = Join-Path $altPath "bin"
        $altPath = Join-Path $altPath "Release"
        $altPath = Join-Path $altPath "net10.0-windows"
        $altPath = Join-Path $altPath "win-x64"
        $altPath = Join-Path $altPath "publish"
        $altPath = Join-Path $altPath "RecallHubTray.exe"

        if (Test-Path $altPath) { $candidates += Get-Item $altPath }
    }
    if ($candidates.Count -gt 0) {
        $trayExe = ($candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
    }

    if (-not $trayExe) {
        Write-Host "  [WARN] RecallHubTray.exe not found, skipping desktop app deployment" -ForegroundColor Yellow
        Write-Host "  Searched:" -ForegroundColor Gray
        Write-Host "    $primaryPath" -ForegroundColor Gray
        Write-Host "    $altPath" -ForegroundColor Gray
    } else {
        Write-Host "  Found tray app: $trayExe" -ForegroundColor Gray

        # Create the app directory
        $appDir = Join-Path $env:LOCALAPPDATA "RecallHub"
        if (-not (Test-Path $appDir)) {
            New-Item -Path $appDir -ItemType Directory -Force | Out-Null
        }

        # Stop tray app if running (prevents file-in-use error during copy)
        Stop-Process -Name "RecallHubTray" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500

        # Copy the executable
        Write-Host "  Copying RecallHubTray.exe..." -ForegroundColor Gray
        Copy-Item -Path $trayExe -Destination (Join-Path $appDir "RecallHubTray.exe") -Force
        Write-Host "  [OK] Executable copied" -ForegroundColor Green

        # Copy the icons (generic + tenant icon; the tray app picks the tenant one)
        $assetsSource = Join-Path $InstallPath "installer"
        $assetsSource = Join-Path $assetsSource "assets"
        $assetsDir = Join-Path $appDir "assets"
        $tenantIconName = if ($tenant -eq "quellex") { "quellex.ico" } else { "icon.ico" }

        foreach ($iconName in @("icon.ico", $tenantIconName) | Select-Object -Unique) {
            $iconSource = Join-Path $assetsSource $iconName
            if (Test-Path $iconSource) {
                if (-not (Test-Path $assetsDir)) {
                    New-Item -Path $assetsDir -ItemType Directory -Force | Out-Null
                }
                Copy-Item -Path $iconSource -Destination (Join-Path $assetsDir $iconName) -Force
                Write-Host "  [OK] Icon copied: $iconName" -ForegroundColor Green
            }
        }

        $exePath = Join-Path $appDir "RecallHubTray.exe"
        $iconPath = Join-Path $assetsDir $tenantIconName
        if (-not (Test-Path $iconPath)) { $iconPath = Join-Path $assetsDir "icon.ico" }
        $shortcutDescription = if ($tenant -eq "quellex") { "Quellex - Ihr Kanzleiwissen. Schnell gefunden." } else { "RecallHub - AI Knowledge Assistant" }

        # Create Desktop Shortcut (named after the tenant)
        Write-Host "  Creating desktop shortcut..." -ForegroundColor Gray
        try {
            $WshShell = New-Object -ComObject WScript.Shell
            $desktopPath = [Environment]::GetFolderPath('Desktop')
            # Remove the generic shortcut left by earlier installs of the other tenant name
            foreach ($stale in @("RecallHub.lnk", "Quellex.lnk")) {
                if ($stale -ne "$tenantName.lnk" -and (Test-Path "$desktopPath\$stale")) {
                    Remove-Item "$desktopPath\$stale" -Force -ErrorAction SilentlyContinue
                }
            }
            $Shortcut = $WshShell.CreateShortcut("$desktopPath\$tenantName.lnk")
            $Shortcut.TargetPath = $exePath
            if (Test-Path $iconPath) {
                $Shortcut.IconLocation = $iconPath
            }
            $Shortcut.Description = $shortcutDescription
            $Shortcut.WorkingDirectory = $appDir
            $Shortcut.Save()
            Write-Host "  [OK] Desktop shortcut created: $tenantName.lnk" -ForegroundColor Green
        } catch {
            Write-Host "  [WARN] Failed to create desktop shortcut: $_" -ForegroundColor Yellow
        }

        # Create Start Menu Shortcut
        Write-Host "  Creating Start Menu shortcut..." -ForegroundColor Gray
        try {
            $startMenuBase = Join-Path $env:APPDATA "Microsoft"
            $startMenuBase = Join-Path $startMenuBase "Windows"
            $startMenuBase = Join-Path $startMenuBase "Start Menu"
            $startMenuBase = Join-Path $startMenuBase "Programs"
            $startMenuDir = Join-Path $startMenuBase $tenantName
            if (-not (Test-Path $startMenuDir)) {
                New-Item -Path $startMenuDir -ItemType Directory -Force | Out-Null
            }
            $WshShell = New-Object -ComObject WScript.Shell
            $smShortcut = $WshShell.CreateShortcut("$startMenuDir\$tenantName.lnk")
            $smShortcut.TargetPath = $exePath
            if (Test-Path $iconPath) {
                $smShortcut.IconLocation = $iconPath
            }
            $smShortcut.Description = $shortcutDescription
            $smShortcut.WorkingDirectory = $appDir
            $smShortcut.Save()
            Write-Host "  [OK] Start Menu shortcut created" -ForegroundColor Green
        } catch {
            Write-Host "  [WARN] Failed to create Start Menu shortcut: $_" -ForegroundColor Yellow
        }

        # Register recallhub:// Protocol Handler
        Write-Host "  Registering recallhub:// protocol handler..." -ForegroundColor Gray
        try {
            $protocolKey = "HKCU:\Software\Classes\recallhub"
            New-Item -Path $protocolKey -Force | Out-Null
            Set-ItemProperty -Path $protocolKey -Name "(Default)" -Value "URL:RecallHub Protocol"
            Set-ItemProperty -Path $protocolKey -Name "URL Protocol" -Value ""
            New-Item -Path "$protocolKey\DefaultIcon" -Force | Out-Null
            Set-ItemProperty -Path "$protocolKey\DefaultIcon" -Name "(Default)" -Value "`"$exePath`",0"
            $cmdKey = Join-Path $protocolKey "shell"
            $cmdKey = Join-Path $cmdKey "open"
            $cmdKey = Join-Path $cmdKey "command"
            New-Item -Path $cmdKey -Force | Out-Null
            Set-ItemProperty -Path $cmdKey -Name "(Default)" -Value "`"$exePath`" `"%1`""
            Write-Host "  [OK] Protocol handler registered" -ForegroundColor Green
        } catch {
            Write-Host "  [WARN] Failed to register protocol handler: $_" -ForegroundColor Yellow
        }

        # Configure Windows Startup
        Write-Host "  Configuring Windows startup..." -ForegroundColor Gray
        try {
            $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
            Set-ItemProperty -Path $runKey -Name "RecallHub" -Value "`"$exePath`""
            Write-Host "  [OK] Windows startup configured" -ForegroundColor Green
        } catch {
            Write-Host "  [WARN] Failed to configure Windows startup: $_" -ForegroundColor Yellow
        }

        # Update manifest with desktop app info
        Update-Manifest @{
            desktop_app_deployed = $true
            desktop_app_path = $exePath
        }

        # Network access: firewall rules for the TLS gateway (443/80) and the
        # tray app's name announcer (UDP 5353/5355). Needs admin; INSTALL.ps1
        # runs elevated unless -SkipPrerequisites/-Repair, so try here and
        # otherwise leave the command for later.
        $lanScript = Join-Path $InstallPath "installer\scripts\Configure-LanAccess.ps1"
        if ($NoLanAccess) {
            Write-Host "  (network access skipped: -NoLanAccess)" -ForegroundColor DarkGray
        } elseif (-not (Test-Path $lanScript)) {
            Write-Host "  [WARN] Configure-LanAccess.ps1 not found - firewall rules for https://$tenant.local not created" -ForegroundColor Yellow
        } else {
            $isElevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
            if ($isElevated) {
                try {
                    & $lanScript -Tenant $tenant -NoElevate
                } catch {
                    Write-Host "  [WARN] Firewall rules failed: $_" -ForegroundColor Yellow
                }
            } else {
                Write-Host "  [WARN] Not elevated - run this once as Administrator to allow network access:" -ForegroundColor Yellow
                Write-Host "         powershell -ExecutionPolicy Bypass -File `"$lanScript`"" -ForegroundColor Gray
            }
        }

        # Launch the app
        if ($NoLaunch) {
            Write-Host "  (desktop app not launched: -NoLaunch)" -ForegroundColor DarkGray
        } else {
            Write-Host "  Launching $tenantName desktop app..." -ForegroundColor Gray
            try {
                Start-Process -FilePath $exePath
                Write-Host "  [OK] Desktop app launched" -ForegroundColor Green
            } catch {
                Write-Host "  [WARN] Failed to launch desktop app: $_" -ForegroundColor Yellow
            }
        }

        # Phase 4 summary
        $lanName = if ($LanHostname) { $LanHostname } else { $tenant }
        $lanUrl = if ($tenant -eq "quellex") { "https://$lanName.local" } else { "https://$lanName.local:11443" }
        Write-Host ""
        Write-Host "  Desktop App deployed successfully!" -ForegroundColor Green
        Write-Host "    Location:         $exePath" -ForegroundColor White
        Write-Host "    Window title:     $tenantName" -ForegroundColor White
        Write-Host "    Desktop shortcut: $tenantName.lnk" -ForegroundColor White
        Write-Host "    Start Menu:       $tenantName\$tenantName.lnk" -ForegroundColor White
        Write-Host "    Protocol handler: recallhub:// registered" -ForegroundColor White
        Write-Host "    Windows startup:  Configured" -ForegroundColor White
        if ($NoLanAccess) {
            Write-Host "    Network address:  (disabled with -NoLanAccess)" -ForegroundColor White
        } else {
            Write-Host "    Network address:  $lanUrl  (export the certificate: installer\scripts\Export-LanCertificate.ps1)" -ForegroundColor White
        }
    }
} else {
    Write-Host ""
    Write-Host "[SKIP] Desktop app deployment skipped" -ForegroundColor DarkGray
}

# --- Verify the tenant endpoints before calling the install complete ---
$frontendPort = if ($tenant -eq "quellex") { 11081 } else { 11080 }
$backendPort  = if ($tenant -eq "quellex") { 11001 } else { 11000 }
$frontendUrl = "http://localhost:$frontendPort/"
$backendUrl  = "http://localhost:$backendPort/api/v1/system/health"
Write-Host ""
Write-Host "[Verify] Waiting for $frontendUrl and $backendUrl ..." -ForegroundColor Yellow
$healthy = $false
$deadline = (Get-Date).AddSeconds(180)
while ((Get-Date) -lt $deadline) {
    $f = $false; $b = $false
    try { $f = (Invoke-WebRequest -Uri $frontendUrl -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch { }
    try { $b = (Invoke-WebRequest -Uri $backendUrl -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch { }
    if ($f -and $b) { $healthy = $true; break }
    Start-Sleep -Seconds 5
}
if ($healthy) {
    Write-Host "  [OK] Frontend and backend are answering" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Frontend/backend did not answer within 180s - see: docker compose --profile $tenant ps / logs (in $InstallPath)" -ForegroundColor Red
}

# The LAN gateway is informational: it only serves the tenant's host names, so
# probe the TCP port rather than an HTTPS request with a mismatched name.
$gatewayPort = if ($tenant -eq "quellex") { 443 } else { 11443 }
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect("127.0.0.1", $gatewayPort)
    $tcp.Close()
    Write-Host "  [OK] LAN gateway is listening on port $gatewayPort" -ForegroundColor Green
} catch {
    Write-Host "  [WARN] LAN gateway not listening on port $gatewayPort (docker compose --profile $tenant ps; is another program using the port?)" -ForegroundColor Yellow
}

# --- Finalize manifest ---
Update-Manifest @{ docker_images_loaded = $true; tenant = $tenant; install_complete = $healthy; frontend_url = $frontendUrl; backend_url = $backendUrl; verified_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss") }
Write-Host "  [OK] Install manifest finalized: $(Get-ManifestPath) (install_complete=$healthy)" -ForegroundColor Green

# --- Final Summary ---
$tenantPort = $frontendPort
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
if ($healthy) { Write-Host "   $tenantName INSTALLATION COMPLETE" -ForegroundColor Green } else { Write-Host "   $tenantName INSTALLATION FINISHED WITH ERRORS" -ForegroundColor Red }
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  $tenantName is now running at:" -ForegroundColor White
Write-Host "    http://localhost:$tenantPort   (API: http://localhost:$backendPort/docs)" -ForegroundColor White
Write-Host ""
Write-Host "  Source code:    $InstallPath" -ForegroundColor Gray
Write-Host "  Test data:      $TestDataPath" -ForegroundColor Gray
Write-Host ""
Write-Host "  To manage services:" -ForegroundColor Gray
Write-Host "    cd $InstallPath" -ForegroundColor Gray
Write-Host "    docker compose --profile $tenant up -d" -ForegroundColor Gray
Write-Host "    docker compose --profile $tenant down" -ForegroundColor Gray
Write-Host ""
Write-Host "  Ollama models loaded:" -ForegroundColor Gray
Write-Host "    gemma4:26b (orchestrator)" -ForegroundColor Gray
Write-Host "    gemma4:e4b (worker)" -ForegroundColor Gray
Write-Host ""
Write-Host "  Desktop App:" -ForegroundColor Gray
if (-not $SkipDesktopApp) {
    Write-Host "    $env:LOCALAPPDATA\RecallHub\RecallHubTray.exe  (config: $env:LOCALAPPDATA\RecallHub\config.json)" -ForegroundColor Gray
} else {
    Write-Host "    (skipped)" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""
if (-not $NonInteractive) {
    Write-Host "Press any key to exit..." -ForegroundColor Gray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}
if (-not $healthy) { exit 2 }
exit 0
