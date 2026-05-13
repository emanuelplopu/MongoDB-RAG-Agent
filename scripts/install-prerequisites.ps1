#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install Docker Desktop and Ollama for RecallHub deployment.
.DESCRIPTION
    Installs Docker Desktop and Ollama on a fresh Windows PC with fully silent,
    headless deployment. Auto-detects system resources, configures WSL2, installs
    Docker Desktop in background mode, and sets up Ollama.

    Looks for bundled installers in the apps/ folder first (offline install).
    Falls back to downloading from the internet if bundled installers are not found.
    Run this script BEFORE full-restore.ps1.

    Exit codes:
      0    = Success, no restart needed
      3010 = Success, but a system restart is required (WSL features were enabled)
      1    = Error
.PARAMETER AppsPath
    Path to the apps directory containing bundled installers. Defaults to .\apps
.PARAMETER SkipDocker
    Skip Docker Desktop installation (if already installed).
.PARAMETER SkipOllama
    Skip Ollama installation (if already installed).
.PARAMETER ShowUI
    Revert to interactive mode (show installer UIs, welcome screens). Default is
    fully silent/headless.
.EXAMPLE
    .\install-prerequisites.ps1
    .\install-prerequisites.ps1 -AppsPath "E:\RecallHub_Backups\RecallHub_v0.8.8_20260512\apps"
    .\install-prerequisites.ps1 -SkipDocker
    .\install-prerequisites.ps1 -ShowUI
#>
param(
    [string]$AppsPath = ".\apps",
    [switch]$SkipDocker,
    [switch]$SkipOllama,
    [switch]$ShowUI
)

$ErrorActionPreference = "Stop"
$script:RestartRequired = $false

# --- Installation tracking (reported to caller) ---
$script:DockerWasInstalled = $false
$script:OllamaWasInstalled = $false
$script:WslWasEnabled = $false
$script:WslConfigCreated = $false

# ─── Helpers ────────────────────────────────────────────────────────────────────
function Write-Step  { param([string]$Msg) Write-Host "`n[*] $Msg" -ForegroundColor Cyan }
function Write-OK    { param([string]$Msg) Write-Host "  [OK] $Msg" -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Err   { param([string]$Msg) Write-Host "  [ERROR] $Msg" -ForegroundColor Red }
function Write-Info  { param([string]$Msg) Write-Host "  $Msg" -ForegroundColor Gray }

$Headless = -not $ShowUI

# ─── Windows Version Check ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "=============================================" -ForegroundColor Magenta
Write-Host "   RecallHub Prerequisites Installer" -ForegroundColor Magenta
Write-Host "=============================================" -ForegroundColor Magenta
Write-Host ""

if ($Headless) {
    Write-Info "Mode: HEADLESS (silent install, no UI popups)"
} else {
    Write-Info "Mode: INTERACTIVE (-ShowUI enabled)"
}

$osVersion = [System.Environment]::OSVersion.Version
if ($osVersion.Major -lt 10) {
    Write-Err "Windows 10 or later is required for Docker Desktop."
    Write-Err "Current OS: Windows $($osVersion.Major).$($osVersion.Minor)"
    exit 1
}
Write-OK "Windows version: $([System.Environment]::OSVersion.VersionString)"

# Resolve AppsPath to absolute
if (-not [System.IO.Path]::IsPathRooted($AppsPath)) {
    $AppsPath = Join-Path (Get-Location).Path $AppsPath
}
if (Test-Path $AppsPath) {
    Write-Info "Apps folder: $AppsPath"
} else {
    Write-Info "Apps folder not found at: $AppsPath (will download if needed)"
}

# ─── System Resource Detection ──────────────────────────────────────────────────
Write-Step "Detecting System Resources"

# RAM
$totalRamBytes = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
$totalRamGB = [math]::Round($totalRamBytes / 1GB, 1)

# CPU
$cpuCores = (Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
if (-not $cpuCores -or $cpuCores -lt 1) { $cpuCores = [Environment]::ProcessorCount }

# GPU
$gpuName = "None detected"
$hasNvidia = $false
try {
    $nvSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($nvSmi) {
        $gpuOutput = nvidia-smi --query-gpu=name --format=csv,noheader 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0 -and $gpuOutput.Trim()) {
            $gpuName = $gpuOutput.Trim().Split("`n")[0].Trim()
            $hasNvidia = $true
        }
    }
} catch { }
if (-not $hasNvidia) {
    try {
        $gpuInfo = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA|AMD|Intel" } | Select-Object -First 1
        if ($gpuInfo) { $gpuName = $gpuInfo.Name }
    } catch { }
}

# Disk space on system drive
$installDrive = $env:SystemDrive
if (-not $installDrive) { $installDrive = "C:" }
$diskInfo = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$installDrive'"
$diskFreeGB = [math]::Round($diskInfo.FreeSpace / 1GB, 0)

if ($diskFreeGB -lt 50) {
    Write-Err "Insufficient disk space: ${diskFreeGB} GB free on $installDrive (minimum 50 GB required)"
    Write-Err "Docker images + Ollama models + temp space need at least 50 GB"
    exit 1
} elseif ($diskFreeGB -lt 80) {
    Write-Warn "Low disk space: ${diskFreeGB} GB free on $installDrive (recommend 80+ GB)"
}

# Calculate WSL2 allocations - leave at least 4 GB for Windows + Ollama
if ($totalRamGB -lt 16) {
    # Low-RAM machines: cap WSL2 at 50% of total
    $wslMemoryGB = [math]::Floor($totalRamGB * 0.50)
} else {
    # 16+ GB machines: use 60% capped at 24 GB
    $wslMemoryGB = [math]::Floor($totalRamGB * 0.60)
    if ($wslMemoryGB -gt 24) { $wslMemoryGB = 24 }
}
if ($wslMemoryGB -lt 4) { $wslMemoryGB = 4 }

$wslProcessors = $cpuCores - 1
if ($wslProcessors -lt 2) { $wslProcessors = 2 }

$wslSwapGB = [math]::Floor($wslMemoryGB * 0.25)
if ($wslSwapGB -lt 1) { $wslSwapGB = 1 }

Write-OK "RAM: $totalRamGB GB (allocating ${wslMemoryGB} GB to WSL2)"
Write-OK "CPU: $cpuCores cores (allocating $wslProcessors to WSL2)"
Write-OK "GPU: $gpuName"
Write-OK "Disk: ${diskFreeGB} GB free on $installDrive"
Write-OK "WSL2 Swap: ${wslSwapGB} GB"

# ─── WSL2 Configuration ─────────────────────────────────────────────────────────
Write-Step "WSL2 Configuration"

if ($Headless) {
    # Check if WSL feature is enabled
    $wslFeature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue
    $vmFeature = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction SilentlyContinue

    if ($wslFeature.State -ne "Enabled") {
        Write-Info "Enabling Windows Subsystem for Linux..."
        $dismOutput = dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 3010) {
            Write-Err "Failed to enable WSL feature (exit code: $LASTEXITCODE)"
            Write-Info $dismOutput
            exit 1
        }
        $script:RestartRequired = $true
        $script:WslWasEnabled = $true
        Write-OK "WSL feature enabled (restart required)"
    } else {
        Write-OK "WSL feature already enabled"
    }

    if ($vmFeature.State -ne "Enabled") {
        Write-Info "Enabling Virtual Machine Platform..."
        $dismOutput = dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 3010) {
            Write-Err "Failed to enable Virtual Machine Platform (exit code: $LASTEXITCODE)"
            Write-Info $dismOutput
            exit 1
        }
        $script:RestartRequired = $true
        Write-OK "Virtual Machine Platform enabled (restart required)"
    } else {
        Write-OK "Virtual Machine Platform already enabled"
    }

    # Set WSL2 as default version (may fail if restart is pending, that's OK)
    try {
        wsl --set-default-version 2 2>&1 | Out-Null
        Write-OK "WSL2 set as default version"
    } catch {
        Write-Info "WSL2 default version will be set after restart"
    }

    # Create/update .wslconfig
    $wslConfigPath = Join-Path $env:USERPROFILE ".wslconfig"
    $wslConfigContent = @"
[wsl2]
memory=${wslMemoryGB}GB
processors=$wslProcessors
swap=${wslSwapGB}GB
localhostForwarding=true
"@

    Set-Content -Path $wslConfigPath -Value $wslConfigContent -Encoding UTF8 -Force
    $script:WslConfigCreated = $true
    Write-OK ".wslconfig written: ${wslMemoryGB}GB RAM, $wslProcessors cores, ${wslSwapGB}GB swap"
} else {
    Write-Info "Skipping WSL2 auto-configuration (interactive mode)"
}

# ─── Docker Desktop ────────────────────────────────────────────────────────────
Write-Step "Docker Desktop"

$dockerInstalled = $false
try {
    $dv = docker --version 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0 -and $dv -match "Docker version") {
        Write-OK "Docker is already installed: $($dv.Trim())"
        $dockerInstalled = $true
    }
} catch { }

if (-not $dockerInstalled -and -not $SkipDocker) {
    $dockerInstaller = $null
    $bundledDocker = Join-Path $AppsPath "DockerDesktopInstaller.exe"

    if (Test-Path $bundledDocker) {
        Write-Info "Installing Docker Desktop from bundled installer..."
        $dockerInstaller = $bundledDocker
    } else {
        Write-Info "No bundled installer found. Downloading Docker Desktop..."
        $dockerUrl = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
        $dockerInstaller = Join-Path $env:TEMP "DockerDesktopInstaller.exe"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $ProgressPreference = 'SilentlyContinue'
            Invoke-WebRequest -Uri $dockerUrl -OutFile $dockerInstaller -UseBasicParsing
            $ProgressPreference = 'Continue'
            Write-OK "Downloaded Docker Desktop installer"
        } catch {
            Write-Err "Failed to download Docker Desktop: $_"
            Write-Err "Please manually download from: $dockerUrl"
            Write-Err "Place as: $bundledDocker"
            exit 1
        }
    }

    if ($Headless) {
        Write-Info "Running Docker Desktop installer (fully silent, WSL2 backend)..."
        $dockerArgs = @("install", "--quiet", "--accept-license", "--backend=wsl-2")
    } else {
        Write-Info "Running Docker Desktop installer (interactive mode)..."
        $dockerArgs = @("install", "--accept-license")
    }

    Write-Warn "A system restart may be required after Docker Desktop installation."
    try {
        $proc = Start-Process -FilePath $dockerInstaller -ArgumentList $dockerArgs -Wait -PassThru
        if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
            Write-Err "Docker Desktop installer exited with code $($proc.ExitCode)"
            Write-Warn "You may need to restart and re-run this script."
            exit 1
        }
        if ($proc.ExitCode -eq 3010) {
            $script:RestartRequired = $true
            Write-OK "Docker Desktop installer completed (restart required)"
        } else {
            Write-OK "Docker Desktop installer completed"
        }
        $script:DockerWasInstalled = $true
    } catch {
        Write-Err "Failed to run Docker Desktop installer: $_"
        exit 1
    }

    # Wait for Docker daemon to start (only if no restart pending)
    if (-not $script:RestartRequired) {
        Write-Info "Waiting for Docker daemon to start (up to 300s - first startup is slow)..."
        $elapsed = 0
        $dockerReady = $false
        while ($elapsed -lt 300) {
            Start-Sleep -Seconds 5
            $elapsed += 5
            try {
                $info = docker info 2>&1 | Out-String
                if ($info -match "Server Version") {
                    $dockerReady = $true
                    break
                }
            } catch { }
            Write-Host "." -NoNewline -ForegroundColor Gray
        }
        Write-Host ""

        if ($dockerReady) {
            Write-OK "Docker daemon is running"
        } else {
            Write-Warn "Docker daemon did not start within 300s."
            Write-Warn "Docker Desktop may require a system restart on first install."
            Write-Warn "After restart, start Docker Desktop and re-run this script to verify."
        }
    } else {
        Write-Info "Skipping Docker daemon wait (restart pending)"
    }
} elseif ($SkipDocker -and -not $dockerInstalled) {
    Write-Info "Skipped (-SkipDocker flag)"
}

# ─── Docker Desktop Headless Configuration ──────────────────────────────────────
if ($Headless) {
    Write-Step "Docker Desktop Headless Configuration"

    $dockerSettingsDir = Join-Path $env:APPDATA "Docker"
    $dockerSettingsPath = Join-Path $dockerSettingsDir "settings.json"

    # Desired headless settings
    $headlessSettings = @{
        "autoStart"                = $true
        "displayedWelcomeMessage"  = $true
        "displayedTutorialMessage" = $true
        "analyticsEnabled"         = $false
        "openUIOnStartupDisabled"  = $true
        "disableUpdate"            = $true
        "disableTips"              = $true
        "disableHardwareInfo"      = $true
        "wslEngineEnabled"         = $true
        "useResourceSaver"         = $true
    }

    # Ensure directory exists
    if (-not (Test-Path $dockerSettingsDir)) {
        New-Item -ItemType Directory -Path $dockerSettingsDir -Force | Out-Null
    }

    # Merge with existing settings if file exists
    $existingSettings = @{}
    if (Test-Path $dockerSettingsPath) {
        try {
            $rawJson = Get-Content -Path $dockerSettingsPath -Raw -ErrorAction Stop
            if ($rawJson -and $rawJson.Trim().Length -gt 0) {
                $existingSettings = $rawJson | ConvertFrom-Json
                # Convert PSCustomObject to hashtable for merging
                $mergedHash = @{}
                $existingSettings.PSObject.Properties | ForEach-Object { $mergedHash[$_.Name] = $_.Value }
                $existingSettings = $mergedHash
            }
        } catch {
            Write-Info "Could not parse existing settings.json, creating fresh"
            $existingSettings = @{}
        }
    }

    # Merge: add/update headless keys without removing existing ones
    foreach ($key in $headlessSettings.Keys) {
        $existingSettings[$key] = $headlessSettings[$key]
    }

    # Write merged settings
    $jsonOutput = $existingSettings | ConvertTo-Json -Depth 10
    Set-Content -Path $dockerSettingsPath -Value $jsonOutput -Encoding UTF8 -Force
    Write-OK "Docker Desktop settings.json configured for headless operation"
    Write-Info "  Path: $dockerSettingsPath"
}

# ─── Ollama ─────────────────────────────────────────────────────────────────────
Write-Step "Ollama"

$ollamaInstalled = $false
try {
    $ov = ollama --version 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0 -and $ov -match "ollama") {
        Write-OK "Ollama is already installed: $($ov.Trim())"
        $ollamaInstalled = $true
    }
} catch { }

if (-not $ollamaInstalled -and -not $SkipOllama) {
    $ollamaInstaller = $null
    $bundledOllama = Join-Path $AppsPath "OllamaSetup.exe"

    if (Test-Path $bundledOllama) {
        Write-Info "Installing Ollama from bundled installer..."
        $ollamaInstaller = $bundledOllama
    } else {
        Write-Info "No bundled installer found. Downloading Ollama..."
        $ollamaUrl = "https://ollama.com/download/OllamaSetup.exe"
        $ollamaInstaller = Join-Path $env:TEMP "OllamaSetup.exe"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $ProgressPreference = 'SilentlyContinue'
            Invoke-WebRequest -Uri $ollamaUrl -OutFile $ollamaInstaller -UseBasicParsing
            $ProgressPreference = 'Continue'
            Write-OK "Downloaded Ollama installer"
        } catch {
            Write-Err "Failed to download Ollama: $_"
            Write-Err "Please manually download from: $ollamaUrl"
            Write-Err "Place as: $bundledOllama"
            exit 1
        }
    }

    if ($Headless) {
        Write-Info "Running Ollama installer (fully silent)..."
        $ollamaArgs = @("/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES")
    } else {
        Write-Info "Running Ollama installer..."
        $ollamaArgs = @("/SILENT")
    }

    try {
        $proc = Start-Process -FilePath $ollamaInstaller -ArgumentList $ollamaArgs -Wait -PassThru
        if ($proc.ExitCode -ne 0) {
            Write-Err "Ollama installer exited with code $($proc.ExitCode)"
            exit 1
        }
        Write-OK "Ollama installer completed"
        $script:OllamaWasInstalled = $true
    } catch {
        Write-Err "Failed to run Ollama installer: $_"
        exit 1
    }

    # Refresh PATH so ollama is available
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

    # Wait for Ollama service to start
    Write-Info "Waiting for Ollama service to start (up to 60s)..."
    $elapsed = 0
    $ollamaReady = $false
    while ($elapsed -lt 60) {
        Start-Sleep -Seconds 3
        $elapsed += 3
        try {
            $list = ollama list 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0) {
                $ollamaReady = $true
                break
            }
        } catch { }
        Write-Host "." -NoNewline -ForegroundColor Gray
    }
    Write-Host ""

    if ($ollamaReady) {
        Write-OK "Ollama service is running"
    } else {
        Write-Warn "Ollama service did not start within 60s."
        Write-Warn "Try running 'ollama serve' manually or restart the machine."
    }
} elseif ($SkipOllama -and -not $ollamaInstalled) {
    Write-Info "Skipped (-SkipOllama flag)"
}

# ─── Final Summary ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "   PREREQUISITES INSTALLATION SUMMARY" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""

# Status checks
$dockerStatus = "NOT INSTALLED"
try {
    $null = docker --version 2>&1
    if ($LASTEXITCODE -eq 0) { $dockerStatus = "INSTALLED" }
} catch { }

$ollamaStatus = "NOT INSTALLED"
try {
    $null = ollama --version 2>&1
    if ($LASTEXITCODE -eq 0) { $ollamaStatus = "INSTALLED" }
} catch { }

# Resource summary
Write-Host "  System Resources Detected:" -ForegroundColor White
Write-Host "    RAM:        $totalRamGB GB (allocating $wslMemoryGB GB to WSL2)" -ForegroundColor Gray
Write-Host "    CPU:        $cpuCores cores (allocating $wslProcessors to WSL2)" -ForegroundColor Gray
if ($hasNvidia) {
    Write-Host "    GPU:        $gpuName" -ForegroundColor Green
} else {
    Write-Host "    GPU:        $gpuName" -ForegroundColor Gray
}
Write-Host "    Disk:       ${diskFreeGB} GB free on $installDrive" -ForegroundColor Gray
Write-Host ""

# Configuration summary
Write-Host "  Configuration Applied:" -ForegroundColor White
if ($Headless) {
    $wslConfigExists = Test-Path (Join-Path $env:USERPROFILE ".wslconfig")
    if ($wslConfigExists) {
        Write-Host "    WSL2:           .wslconfig written (${wslMemoryGB}GB RAM, $wslProcessors cores)" -ForegroundColor Gray
    }
    $dockerSettingsExists = Test-Path (Join-Path $env:APPDATA "Docker\settings.json")
    if ($dockerSettingsExists) {
        Write-Host "    Docker Desktop: Headless mode, auto-start enabled" -ForegroundColor Gray
    }
    Write-Host "    Ollama:         Service mode, models at default path" -ForegroundColor Gray
} else {
    Write-Host "    (Interactive mode - no headless config applied)" -ForegroundColor Yellow
}
Write-Host ""

# Installation status
$dColor = if ($dockerStatus -eq "INSTALLED") { "Green" } else { "Yellow" }
$oColor = if ($ollamaStatus -eq "INSTALLED") { "Green" } else { "Yellow" }
Write-Host "  Installation Status:" -ForegroundColor White
Write-Host "    Docker Desktop:  $dockerStatus" -ForegroundColor $dColor
Write-Host "    Ollama:          $ollamaStatus" -ForegroundColor $oColor
Write-Host ""

if ($script:RestartRequired) {
    Write-Host "  ** RESTART REQUIRED **" -ForegroundColor Yellow
    Write-Host "  WSL2 features were enabled. Please restart your computer," -ForegroundColor Yellow
    Write-Host "  then re-run this script or proceed with full-restore.ps1." -ForegroundColor Yellow
    Write-Host ""
    exit 3010
}

if ($dockerStatus -eq "INSTALLED" -and $ollamaStatus -eq "INSTALLED") {
    Write-Host "  All prerequisites are ready!" -ForegroundColor Green
    Write-Host "  Next steps:" -ForegroundColor White
    Write-Host "    1. Run .\scripts\restore-ollama-models.ps1 (if models/ folder exists)" -ForegroundColor Gray
    Write-Host "    2. Run .\scripts\full-restore.ps1 -BackupPath <backup_folder>" -ForegroundColor Gray
} else {
    Write-Host "  Some prerequisites are missing. Please resolve above issues." -ForegroundColor Yellow
    if ($dockerStatus -ne "INSTALLED") {
        Write-Host "    - Docker Desktop may need a restart to complete installation." -ForegroundColor Yellow
    }
}
# --- Output installation results for caller ---
Write-Host "##INSTALL_RESULTS##"
Write-Host "DOCKER_INSTALLED=$($script:DockerWasInstalled)"
Write-Host "OLLAMA_INSTALLED=$($script:OllamaWasInstalled)"
Write-Host "WSL_ENABLED=$($script:WslWasEnabled)"
Write-Host "WSL_CONFIG_CREATED=$($script:WslConfigCreated)"
Write-Host "##END_RESULTS##"

Write-Host ""
exit 0
