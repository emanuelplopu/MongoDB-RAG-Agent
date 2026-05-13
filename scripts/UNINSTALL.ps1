#Requires -Version 5.1
<#
.SYNOPSIS
    RecallHub Complete Uninstallation
.DESCRIPTION
    Removes RecallHub and restores the system to its previous state.
    Only removes components that were installed by INSTALL.ps1.

    Steps (in reverse order of installation):
    1. Stop and remove all Docker containers and volumes
    2. Remove Docker images (RecallHub-specific)
    3. Remove source code (InstallPath)
    4. Remove Test_Data
    5. Remove Ollama models (only RecallHub ones)
    6. Uninstall Ollama (if installed by us)
    7. Uninstall Docker Desktop (if installed by us)
    8. Remove WSL2 configuration (if created by us)
    9. Disable WSL2/VirtualMachinePlatform features (if enabled by us)
    10. Clean up manifest and leftover files
.PARAMETER InstallPath
    Where RecallHub was installed. Defaults to C:\RecallHub.
.PARAMETER KeepData
    Keep Test_Data and MongoDB data (useful if reinstalling).
.PARAMETER KeepDocker
    Don't uninstall Docker Desktop even if we installed it.
.PARAMETER KeepOllama
    Don't uninstall Ollama even if we installed it.
.PARAMETER KeepWSL
    Don't disable WSL2 features even if we enabled them.
.PARAMETER Force
    Skip all confirmation prompts.
.PARAMETER DryRun
    Show what would be removed without actually removing anything.
.EXAMPLE
    .\UNINSTALL.ps1
    .\UNINSTALL.ps1 -KeepDocker -KeepOllama
    .\UNINSTALL.ps1 -DryRun
    .\UNINSTALL.ps1 -Force
#>
param(
    [string]$InstallPath = "C:\RecallHub",
    [switch]$KeepData,
    [switch]$KeepDocker,
    [switch]$KeepOllama,
    [switch]$KeepWSL,
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"

# --- Self-elevation ---
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "This script requires Administrator privileges. Restarting elevated..." -ForegroundColor Yellow
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($InstallPath -ne "C:\RecallHub") { $arguments += " -InstallPath `"$InstallPath`"" }
    if ($KeepData) { $arguments += " -KeepData" }
    if ($KeepDocker) { $arguments += " -KeepDocker" }
    if ($KeepOllama) { $arguments += " -KeepOllama" }
    if ($KeepWSL) { $arguments += " -KeepWSL" }
    if ($Force) { $arguments += " -Force" }
    if ($DryRun) { $arguments += " -DryRun" }
    Start-Process powershell.exe -ArgumentList $arguments -Verb RunAs -Wait
    exit
}

# --- Helper Functions ---
function Write-Step {
    param([int]$Step, [int]$Total, [string]$Message)
    Write-Host ""
    Write-Host "  [$Step/$Total] $Message" -ForegroundColor Cyan
    Write-Host "  $('-' * 50)" -ForegroundColor DarkGray
}

function Write-Done {
    param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-Skipped {
    param([string]$Message)
    Write-Host "  [SKIP] $Message" -ForegroundColor DarkGray
}

function Write-DryRun {
    param([string]$Message)
    Write-Host "  [DRY RUN] Would: $Message" -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Message)
    Write-Host "  [FAIL] $Message" -ForegroundColor Red
}

function Confirm-Action {
    param([string]$Message)
    if ($Force -or $DryRun) { return $true }
    $response = Read-Host "  $Message [Y/N]"
    return ($response -eq 'Y' -or $response -eq 'y')
}

# --- Load manifest ---
$manifest = $null
$manifestPath = $null

# Try primary location (inside install path)
$primaryManifest = Join-Path $InstallPath ".recallhub-install-manifest.json"
# Try fallback location
$fallbackManifest = Join-Path $env:ProgramData "RecallHub\install-manifest.json"

if (Test-Path $fallbackManifest) {
    $manifestPath = $fallbackManifest
    $manifest = Get-Content $fallbackManifest -Raw | ConvertFrom-Json
} elseif (Test-Path $primaryManifest) {
    $manifestPath = $primaryManifest
    $manifest = Get-Content $primaryManifest -Raw | ConvertFrom-Json
}

# --- Header ---
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Red
Write-Host "   RecallHub - Complete System Uninstallation" -ForegroundColor Red
Write-Host "=====================================================" -ForegroundColor Red
Write-Host ""

if ($DryRun) {
    Write-Host "  ** DRY RUN MODE - No changes will be made **" -ForegroundColor Yellow
    Write-Host ""
}

if ($manifest) {
    Write-Host "  Install manifest found: $manifestPath" -ForegroundColor Gray
    Write-Host "  Installed at: $($manifest.installed_at)" -ForegroundColor Gray
    Write-Host "  Version:      $($manifest.version)" -ForegroundColor Gray
    Write-Host "  Install path: $($manifest.install_path)" -ForegroundColor Gray
    Write-Host ""

    # Override InstallPath from manifest if not explicitly changed
    if ($InstallPath -eq "C:\RecallHub" -and $manifest.install_path) {
        $InstallPath = $manifest.install_path
    }
} else {
    Write-Host "  [WARN] No install manifest found." -ForegroundColor Yellow
    Write-Host "  Will use defaults and prompt for confirmation at each step." -ForegroundColor Yellow
    Write-Host ""
    if (-not $Force) {
        $continue = Read-Host "  No manifest found. Continue with uninstall? [Y/N]"
        if ($continue -ne 'Y' -and $continue -ne 'y') {
            Write-Host "  Uninstall cancelled." -ForegroundColor Gray
            exit 0
        }
    }
    # Create a default manifest with safe assumptions (nothing installed by us)
    $manifest = [PSCustomObject]@{
        install_path = $InstallPath
        test_data_path = "C:\Test_Data"
        docker_installed_by_us = $false
        ollama_installed_by_us = $false
        wsl_enabled_by_us = $false
        wsl_config_created_by_us = $false
        docker_settings_modified_by_us = $false
        ollama_models_path = "$env:USERPROFILE\.ollama\models"
    }
}

$TestDataPath = if ($manifest.test_data_path) { $manifest.test_data_path } else { "C:\Test_Data" }
$TotalSteps = 10
$errors = @()
$restartNeeded = $false

# --- Summary of what will be done ---
Write-Host "  Actions planned:" -ForegroundColor White
Write-Host "    - Stop and remove Docker containers/volumes" -ForegroundColor Gray
Write-Host "    - Remove RecallHub Docker images" -ForegroundColor Gray
Write-Host "    - Remove source code at: $InstallPath" -ForegroundColor Gray
if (-not $KeepData) {
    Write-Host "    - Remove Test_Data at: $TestDataPath" -ForegroundColor Gray
}
if (-not $KeepOllama -and $manifest.ollama_installed_by_us) {
    Write-Host "    - Uninstall Ollama (installed by us)" -ForegroundColor Gray
} else {
    Write-Host "    - Remove RecallHub Ollama models only" -ForegroundColor Gray
}
if (-not $KeepDocker -and $manifest.docker_installed_by_us) {
    Write-Host "    - Uninstall Docker Desktop (installed by us)" -ForegroundColor Gray
}
if (-not $KeepWSL -and $manifest.wsl_enabled_by_us) {
    Write-Host "    - Disable WSL2 features (enabled by us)" -ForegroundColor Gray
}
Write-Host ""

if (-not $Force -and -not $DryRun) {
    $confirm = Read-Host "  Proceed with uninstallation? [Y/N]"
    if ($confirm -ne 'Y' -and $confirm -ne 'y') {
        Write-Host "  Uninstall cancelled." -ForegroundColor Gray
        exit 0
    }
}

# ============================================================
# STEP 1: Stop and remove Docker containers, networks, volumes
# ============================================================
Write-Step -Step 1 -Total $TotalSteps -Message "Stopping and removing Docker containers"

$dockerAvailable = $false
try {
    docker info 2>&1 | Out-Null
    $dockerAvailable = $true
} catch {}

if ($dockerAvailable) {
    if (Test-Path $InstallPath) {
        if ($DryRun) {
            Write-DryRun "docker compose down -v --remove-orphans (in $InstallPath)"
        } else {
            try {
                Push-Location $InstallPath
                docker compose --profile recallhub --profile quellex down -v --remove-orphans 2>&1 | Out-Null
                Pop-Location
                Write-Done "Docker compose services stopped and removed"
            } catch {
                Pop-Location
                $errors += "Step 1: Failed to stop compose services: $($_.Exception.Message)"
                Write-Fail "Failed to stop compose services (continuing...)"
            }
        }
    } else {
        Write-Skipped "Install path not found, skipping compose down"
    }

    # Remove any leftover containers
    $filterNames = @("recallhub", "quellex", "rag-")
    foreach ($filterName in $filterNames) {
        $containers = docker ps -a --filter "name=$filterName" -q 2>&1
        if ($containers -and $containers -notlike "*error*") {
            foreach ($cid in $containers) {
                if ($cid.Trim()) {
                    if ($DryRun) {
                        Write-DryRun "Remove container: $($cid.Trim())"
                    } else {
                        docker rm -f $cid.Trim() 2>&1 | Out-Null
                    }
                }
            }
        }
    }

    # Remove networks
    $networks = docker network ls --filter "name=rag-network" -q 2>&1
    if ($networks -and $networks -notlike "*error*") {
        foreach ($netId in $networks) {
            if ($netId.Trim()) {
                if ($DryRun) {
                    Write-DryRun "Remove network: $($netId.Trim())"
                } else {
                    docker network rm $netId.Trim() 2>&1 | Out-Null
                }
            }
        }
    }

    if (-not $DryRun) { Write-Done "Leftover containers and networks cleaned" }
} else {
    Write-Skipped "Docker not available, skipping container cleanup"
}

# ============================================================
# STEP 2: Remove Docker images (RecallHub-specific)
# ============================================================
Write-Step -Step 2 -Total $TotalSteps -Message "Removing RecallHub Docker images"

if ($dockerAvailable) {
    # Patterns for images built via docker-compose (project name prefix)
    $ourImages = @(
        "recallhub-backend*",
        "recallhub-frontend*",
        "recallhub-worker*",
        "recallhub-base*",
        "recallhub-ml-heavy*",
        "quellex-*",
        "rag-*",
        # Images loaded from exported tar files (offline deployment)
        "mongodb-rag-agent-backend*",
        "mongodb-rag-agent-frontend*",
        "mongodb-rag-agent-ingestion-worker*"
    )

    foreach ($pattern in $ourImages) {
        $imageIds = docker images --filter "reference=$pattern" -q 2>&1
        if ($imageIds -and $imageIds -notlike "*error*") {
            foreach ($imgId in $imageIds) {
                if ($imgId.Trim()) {
                    if ($DryRun) {
                        Write-DryRun "Remove image matching '$pattern': $($imgId.Trim())"
                    } else {
                        docker rmi -f $imgId.Trim() 2>&1 | Out-Null
                    }
                }
            }
        }
    }

    # Remove base/pulled images
    $pulledImages = @(
        "mongodb/mongodb-atlas-local:8.0",
        "mongo:8.0",
        "recallhub-backend-base:latest"
    )
    foreach ($img in $pulledImages) {
        $exists = docker images -q $img 2>&1
        if ($exists -and $exists.Trim()) {
            if ($DryRun) {
                Write-DryRun "Remove pulled image: $img"
            } else {
                docker rmi $img 2>&1 | Out-Null
            }
        }
    }

    # Remove dangling images left from our builds
    $danglingIds = docker images -f "dangling=true" -q 2>&1
    if ($danglingIds -and $danglingIds -notlike "*error*") {
        foreach ($dId in $danglingIds) {
            if ($dId.Trim()) {
                if ($DryRun) {
                    Write-DryRun "Remove dangling image: $($dId.Trim())"
                } else {
                    docker rmi -f $dId.Trim() 2>&1 | Out-Null
                }
            }
        }
    }

    # Docker system prune to reclaim layer cache and build cache
    if ($DryRun) {
        Write-DryRun "Docker system prune (volumes excluded)"
    } else {
        $doPrune = $true
        if (-not $Force) {
            $pruneConfirm = Read-Host "  Reclaim disk space with docker system prune? [Y/N]"
            $doPrune = ($pruneConfirm -eq 'Y' -or $pruneConfirm -eq 'y')
        }
        if ($doPrune) {
            docker system prune -af 2>&1 | Out-Null
            Write-Host "    Docker system prune completed (build cache + unused layers removed)" -ForegroundColor Gray
        } else {
            Write-Host "    Skipped docker system prune" -ForegroundColor DarkGray
        }
    }

    if (-not $DryRun) { Write-Done "Docker images removed" }
} else {
    Write-Skipped "Docker not available, skipping image cleanup"
}

# ============================================================
# STEP 3: Remove source code
# ============================================================
Write-Step -Step 3 -Total $TotalSteps -Message "Removing source code ($InstallPath)"

if (Test-Path $InstallPath) {
    if (Confirm-Action "Remove $InstallPath and all contents?") {
        if ($DryRun) {
            Write-DryRun "Remove directory: $InstallPath"
        } else {
            try {
                Remove-Item $InstallPath -Recurse -Force
                Write-Done "Source code removed: $InstallPath"
            } catch {
                $errors += "Step 3: Failed to remove $InstallPath`: $($_.Exception.Message)"
                Write-Fail "Failed to remove $InstallPath (may be in use)"
            }
        }
    } else {
        Write-Skipped "Source code removal declined"
    }
} else {
    Write-Skipped "Install path not found: $InstallPath"
}

# ============================================================
# STEP 4: Remove Test_Data
# ============================================================
Write-Step -Step 4 -Total $TotalSteps -Message "Removing Test_Data ($TestDataPath)"

if ($KeepData) {
    Write-Skipped "Keeping Test_Data (-KeepData specified)"
} elseif (Test-Path $TestDataPath) {
    if (Confirm-Action "Remove $TestDataPath and all contents?") {
        if ($DryRun) {
            Write-DryRun "Remove directory: $TestDataPath"
        } else {
            try {
                Remove-Item $TestDataPath -Recurse -Force
                Write-Done "Test_Data removed: $TestDataPath"
            } catch {
                $errors += "Step 4: Failed to remove $TestDataPath`: $($_.Exception.Message)"
                Write-Fail "Failed to remove $TestDataPath"
            }
        }
    } else {
        Write-Skipped "Test_Data removal declined"
    }
} else {
    Write-Skipped "Test_Data path not found: $TestDataPath"
}

# ============================================================
# STEP 5: Remove Ollama models (RecallHub-specific)
# ============================================================
Write-Step -Step 5 -Total $TotalSteps -Message "Removing RecallHub Ollama models"

$ollamaAvailable = $false
try {
    ollama --version 2>&1 | Out-Null
    $ollamaAvailable = $true
} catch {}

# If we're going to uninstall Ollama entirely, skip model removal
$willUninstallOllama = (-not $KeepOllama -and $manifest.ollama_installed_by_us)

if ($willUninstallOllama) {
    Write-Skipped "Ollama will be uninstalled entirely in Step 6, skipping individual model removal"
} elseif ($ollamaAvailable) {
    $modelsToRemove = @("gemma4:26b", "gemma4:e4b")
    foreach ($model in $modelsToRemove) {
        if ($DryRun) {
            Write-DryRun "Remove Ollama model: $model"
        } else {
            $result = ollama rm $model 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Host "    Removed model: $model" -ForegroundColor Gray
            } else {
                Write-Host "    Model not found or already removed: $model" -ForegroundColor DarkGray
            }
        }
    }
    if (-not $DryRun) { Write-Done "RecallHub Ollama models cleaned" }
} else {
    Write-Skipped "Ollama not available, skipping model removal"
}

# ============================================================
# STEP 6: Uninstall Ollama (if installed by us)
# ============================================================
Write-Step -Step 6 -Total $TotalSteps -Message "Uninstalling Ollama"

if ($KeepOllama) {
    Write-Skipped "Keeping Ollama (-KeepOllama specified)"
} elseif (-not $manifest.ollama_installed_by_us) {
    Write-Skipped "Ollama was not installed by us, leaving it"
} else {
    if (Confirm-Action "Uninstall Ollama completely?") {
        if ($DryRun) {
            Write-DryRun "Stop Ollama processes"
            Write-DryRun "Uninstall Ollama via registry uninstaller"
            Write-DryRun "Remove Ollama data directory"
            Write-DryRun "Remove OLLAMA_MODELS environment variable"
        } else {
            # Stop Ollama processes
            Stop-Process -Name "ollama*" -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2

            # Find and run uninstaller
            $ollamaUninstall = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue |
                Where-Object { $_.DisplayName -like "*Ollama*" }

            if ($ollamaUninstall -and $ollamaUninstall.UninstallString) {
                $uninstallCmd = $ollamaUninstall.UninstallString
                try {
                    # Ollama uses Inno Setup uninstaller
                    if ($uninstallCmd -match "unins\d+\.exe") {
                        Start-Process -FilePath $uninstallCmd -ArgumentList "/VERYSILENT", "/NORESTART" -Wait -ErrorAction Stop
                    } else {
                        Start-Process -FilePath $uninstallCmd -ArgumentList "/S" -Wait -ErrorAction Stop
                    }
                    Write-Host "    Ollama uninstaller completed" -ForegroundColor Gray
                } catch {
                    $errors += "Step 6: Ollama uninstall failed: $($_.Exception.Message)"
                    Write-Fail "Ollama uninstaller failed"
                }
            } else {
                Write-Host "    Ollama uninstaller not found in registry, removing files manually" -ForegroundColor DarkGray
            }

            # Remove Ollama data directory
            $ollamaDir = if ($env:OLLAMA_MODELS) {
                Split-Path $env:OLLAMA_MODELS -Parent
            } else {
                "$env:USERPROFILE\.ollama"
            }
            if (Test-Path $ollamaDir) {
                try {
                    Remove-Item $ollamaDir -Recurse -Force
                    Write-Host "    Removed Ollama data: $ollamaDir" -ForegroundColor Gray
                } catch {
                    $errors += "Step 6: Failed to remove Ollama data: $($_.Exception.Message)"
                    Write-Fail "Failed to remove $ollamaDir"
                }
            }

            # Remove Ollama program files
            $ollamaProgramDir = "$env:LOCALAPPDATA\Programs\Ollama"
            if (Test-Path $ollamaProgramDir) {
                try {
                    Remove-Item $ollamaProgramDir -Recurse -Force
                    Write-Host "    Removed Ollama program files: $ollamaProgramDir" -ForegroundColor Gray
                } catch {
                    $errors += "Step 6: Failed to remove Ollama program files: $($_.Exception.Message)"
                    Write-Fail "Failed to remove $ollamaProgramDir"
                }
            }

            # Remove OLLAMA_MODELS env var
            [Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $null, "User")
            Write-Done "Ollama uninstalled"
        }
    } else {
        Write-Skipped "Ollama uninstall declined"
    }
}

# ============================================================
# STEP 7: Uninstall Docker Desktop (if installed by us)
# ============================================================
Write-Step -Step 7 -Total $TotalSteps -Message "Uninstalling Docker Desktop"

if ($KeepDocker) {
    Write-Skipped "Keeping Docker Desktop (-KeepDocker specified)"
} elseif (-not $manifest.docker_installed_by_us) {
    Write-Skipped "Docker Desktop was not installed by us, leaving it"
} else {
    if (Confirm-Action "Uninstall Docker Desktop completely?") {
        if ($DryRun) {
            Write-DryRun "Uninstall Docker Desktop via installer"
            Write-DryRun "Remove Docker data directories"
        } else {
            # Find Docker Desktop installer for uninstall
            $dockerInstallerPath = "$env:ProgramFiles\Docker\Docker\Docker Desktop Installer.exe"
            if (Test-Path $dockerInstallerPath) {
                try {
                    Start-Process -FilePath $dockerInstallerPath -ArgumentList "uninstall", "--quiet" -Wait -ErrorAction Stop
                    Write-Host "    Docker Desktop uninstaller completed" -ForegroundColor Gray
                } catch {
                    $errors += "Step 7: Docker Desktop uninstall failed: $($_.Exception.Message)"
                    Write-Fail "Docker Desktop uninstaller failed"
                }
            } else {
                # Try via registry
                $dockerUninstall = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
                    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue |
                    Where-Object { $_.DisplayName -like "*Docker Desktop*" }

                if ($dockerUninstall -and $dockerUninstall.UninstallString) {
                    try {
                        Start-Process -FilePath $dockerUninstall.UninstallString -ArgumentList "--quiet" -Wait -ErrorAction Stop
                        Write-Host "    Docker Desktop uninstaller completed via registry" -ForegroundColor Gray
                    } catch {
                        $errors += "Step 7: Docker Desktop uninstall via registry failed: $($_.Exception.Message)"
                        Write-Fail "Docker Desktop uninstaller failed"
                    }
                } else {
                    Write-Host "    Docker Desktop uninstaller not found" -ForegroundColor DarkGray
                }
            }

            # Remove Docker data directories
            $dockerDirs = @(
                "$env:APPDATA\Docker",
                "$env:LOCALAPPDATA\Docker",
                "$env:ProgramData\Docker",
                "$env:ProgramData\DockerDesktop",
                "$env:USERPROFILE\.docker"
            )
            foreach ($dir in $dockerDirs) {
                if (Test-Path $dir) {
                    try {
                        Remove-Item $dir -Recurse -Force
                        Write-Host "    Removed: $dir" -ForegroundColor Gray
                    } catch {
                        Write-Host "    Could not remove: $dir (may be in use)" -ForegroundColor DarkGray
                    }
                }
            }

            Write-Done "Docker Desktop uninstalled"
        }
    } else {
        Write-Skipped "Docker Desktop uninstall declined"
    }
}

# ============================================================
# STEP 8: Remove .wslconfig (if created by us)
# ============================================================
Write-Step -Step 8 -Total $TotalSteps -Message "Removing WSL configuration"

$wslConfig = "$env:USERPROFILE\.wslconfig"

if (-not $manifest.wsl_config_created_by_us) {
    Write-Skipped ".wslconfig was not created by us, leaving it"
} elseif (Test-Path $wslConfig) {
    if ($DryRun) {
        Write-DryRun "Remove file: $wslConfig"
    } else {
        try {
            Remove-Item $wslConfig -Force
            Write-Done "Removed .wslconfig"
        } catch {
            $errors += "Step 8: Failed to remove .wslconfig: $($_.Exception.Message)"
            Write-Fail "Failed to remove .wslconfig"
        }
    }
} else {
    Write-Skipped ".wslconfig not found"
}

# ============================================================
# STEP 9: Disable WSL2/VirtualMachinePlatform features
# ============================================================
Write-Step -Step 9 -Total $TotalSteps -Message "Disabling WSL2 features"

if ($KeepWSL) {
    Write-Skipped "Keeping WSL2 features (-KeepWSL specified)"
} elseif (-not $manifest.wsl_enabled_by_us) {
    Write-Skipped "WSL2 features were not enabled by us, leaving them"
} else {
    if (Confirm-Action "Disable WSL2 and VirtualMachinePlatform Windows features?") {
        if ($DryRun) {
            Write-DryRun "Disable Windows feature: Microsoft-Windows-Subsystem-Linux"
            Write-DryRun "Disable Windows feature: VirtualMachinePlatform"
        } else {
            try {
                $wslResult = dism.exe /online /disable-feature /featurename:Microsoft-Windows-Subsystem-Linux /norestart 2>&1
                Write-Host "    Disabled: Microsoft-Windows-Subsystem-Linux" -ForegroundColor Gray
            } catch {
                $errors += "Step 9: Failed to disable WSL: $($_.Exception.Message)"
                Write-Fail "Failed to disable WSL feature"
            }

            try {
                $vmResult = dism.exe /online /disable-feature /featurename:VirtualMachinePlatform /norestart 2>&1
                Write-Host "    Disabled: VirtualMachinePlatform" -ForegroundColor Gray
            } catch {
                $errors += "Step 9: Failed to disable VirtualMachinePlatform: $($_.Exception.Message)"
                Write-Fail "Failed to disable VirtualMachinePlatform"
            }

            $restartNeeded = $true
            Write-Done "WSL2 features disabled (restart required)"
        }
    } else {
        Write-Skipped "WSL2 feature disablement declined"
    }
}

# ============================================================
# STEP 10: Cleanup manifest and leftover files
# ============================================================
Write-Step -Step 10 -Total $TotalSteps -Message "Cleaning up manifest and leftover files"

# Remove ProgramData\RecallHub directory
$programDataDir = Join-Path $env:ProgramData "RecallHub"
if (Test-Path $programDataDir) {
    if ($DryRun) {
        Write-DryRun "Remove directory: $programDataDir"
    } else {
        try {
            Remove-Item $programDataDir -Recurse -Force
            Write-Host "    Removed: $programDataDir" -ForegroundColor Gray
        } catch {
            $errors += "Step 10: Failed to remove $programDataDir`: $($_.Exception.Message)"
            Write-Fail "Failed to remove $programDataDir"
        }
    }
}

# Remove primary manifest if it still exists (in case InstallPath was already removed)
if ($manifestPath -and (Test-Path $manifestPath)) {
    if ($DryRun) {
        Write-DryRun "Remove manifest: $manifestPath"
    } else {
        Remove-Item $manifestPath -Force -ErrorAction SilentlyContinue
    }
}

if (-not $DryRun) { Write-Done "Cleanup complete" }

# ============================================================
# Final Summary
# ============================================================
Write-Host ""
Write-Host "=====================================================" -ForegroundColor Cyan
if ($DryRun) {
    Write-Host "   DRY RUN COMPLETE - No changes were made" -ForegroundColor Yellow
} else {
    Write-Host "   UNINSTALLATION COMPLETE" -ForegroundColor Green
}
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""

# Report errors
if ($errors.Count -gt 0) {
    Write-Host "  Errors encountered during uninstallation:" -ForegroundColor Yellow
    foreach ($err in $errors) {
        Write-Host "    - $err" -ForegroundColor Yellow
    }
    Write-Host ""
}

# Report what was kept
$kept = @()
if ($KeepData) { $kept += "Test_Data" }
if ($KeepDocker) { $kept += "Docker Desktop" }
if ($KeepOllama) { $kept += "Ollama" }
if ($KeepWSL) { $kept += "WSL2 features" }
if ($kept.Count -gt 0) {
    Write-Host "  Preserved (by request): $($kept -join ', ')" -ForegroundColor Gray
    Write-Host ""
}

# Restart notification
if ($restartNeeded -and -not $DryRun) {
    Write-Host "  ** RESTART REQUIRED **" -ForegroundColor Yellow
    Write-Host "  WSL2 features were disabled. Please restart your" -ForegroundColor Yellow
    Write-Host "  computer to complete the uninstallation." -ForegroundColor Yellow
    Write-Host ""
    if (-not $Force) {
        $restartNow = Read-Host "  Restart now? [Y/N]"
        if ($restartNow -eq 'Y' -or $restartNow -eq 'y') {
            Restart-Computer -Force
        }
    }
}

if ($errors.Count -eq 0 -and -not $DryRun) {
    Write-Host "  RecallHub has been completely removed from this system." -ForegroundColor Green
}

Write-Host ""
Write-Host "Press any key to exit..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
