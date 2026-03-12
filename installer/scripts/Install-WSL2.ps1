<#
.SYNOPSIS
    Installs and configures WSL2 for RecallHub.

.DESCRIPTION
    This script:
    1. Checks Windows version compatibility
    2. Enables WSL2 feature if not present
    3. Sets WSL2 as the default version
    4. Optionally installs a default Linux distribution

.NOTES
    Requires administrator privileges.
    May require a system restart after enabling WSL2.
#>

param(
    [switch]$SkipDistro,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# Minimum Windows build for WSL2
$MIN_BUILD = 19041

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "    [OK] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "    [WARN] $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "    [ERROR] $Message" -ForegroundColor Red
}

function Test-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-WindowsVersion {
    $osVersion = [System.Environment]::OSVersion.Version
    $build = $osVersion.Build
    
    Write-Step "Checking Windows version..."
    Write-Host "    Current build: $build"
    Write-Host "    Required build: $MIN_BUILD+"
    
    if ($build -lt $MIN_BUILD) {
        Write-Error "Windows build $build is not supported. WSL2 requires build $MIN_BUILD or later."
        Write-Host "`n    Please update Windows to version 2004 or later."
        return $false
    }
    
    Write-Success "Windows version is compatible"
    return $true
}

function Test-WSLInstalled {
    try {
        $wslPath = Get-Command wsl.exe -ErrorAction SilentlyContinue
        return ($null -ne $wslPath)
    }
    catch {
        return $false
    }
}

function Test-WSL2Default {
    try {
        $output = wsl --status 2>&1
        return $output -match "Default Version: 2"
    }
    catch {
        return $false
    }
}

function Enable-WSLFeature {
    Write-Step "Enabling Windows Subsystem for Linux feature..."
    
    try {
        $result = Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart
        
        if ($result.RestartNeeded) {
            return "restart"
        }
        
        Write-Success "WSL feature enabled"
        return "success"
    }
    catch {
        Write-Error "Failed to enable WSL feature: $_"
        return "error"
    }
}

function Enable-VirtualMachinePlatform {
    Write-Step "Enabling Virtual Machine Platform feature..."
    
    try {
        $result = Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart
        
        if ($result.RestartNeeded) {
            return "restart"
        }
        
        Write-Success "Virtual Machine Platform enabled"
        return "success"
    }
    catch {
        Write-Error "Failed to enable Virtual Machine Platform: $_"
        return "error"
    }
}

function Install-WSL2Kernel {
    Write-Step "Checking WSL2 kernel..."
    
    # Try to set WSL2 as default - this will indicate if kernel is installed
    try {
        wsl --set-default-version 2 2>&1 | Out-Null
        Write-Success "WSL2 kernel is installed"
        return $true
    }
    catch {
        Write-Warning "WSL2 kernel may need to be installed"
    }
    
    # Try to install kernel update via wsl --install
    Write-Step "Installing WSL2 kernel update..."
    
    try {
        wsl --install --no-distribution 2>&1 | Out-Null
        Write-Success "WSL2 kernel installed"
        return $true
    }
    catch {
        Write-Warning "Could not install WSL2 kernel automatically"
        Write-Host "`n    Please download and install the WSL2 kernel update manually:"
        Write-Host "    https://wslstorestorage.blob.core.windows.net/wslblob/wsl_update_x64.msi"
        return $false
    }
}

function Set-WSL2Default {
    Write-Step "Setting WSL2 as default version..."
    
    try {
        wsl --set-default-version 2 2>&1 | Out-Null
        Write-Success "WSL2 set as default"
        return $true
    }
    catch {
        Write-Error "Failed to set WSL2 as default: $_"
        return $false
    }
}

function Get-WSLStatus {
    Write-Step "Getting WSL status..."
    
    try {
        $output = wsl --status 2>&1
        Write-Host $output
    }
    catch {
        Write-Warning "Could not get WSL status"
    }
}

function Main {
    Write-Host @"
╔═══════════════════════════════════════════════════════════════════╗
║         RecallHub WSL2 Installation Script                        ║
║         Configures Windows Subsystem for Linux 2                  ║
╚═══════════════════════════════════════════════════════════════════╝
"@ -ForegroundColor Magenta

    # Check for admin privileges
    if (-not (Test-Administrator)) {
        Write-Error "This script requires administrator privileges."
        Write-Host "`n    Please run PowerShell as Administrator and try again."
        exit 1
    }
    
    Write-Success "Running with administrator privileges"
    
    # Check Windows version
    if (-not (Test-WindowsVersion)) {
        exit 1
    }
    
    $needsRestart = $false
    
    # Check if WSL is already installed
    if (Test-WSLInstalled) {
        Write-Success "WSL is already installed"
        
        # Check if WSL2 is default
        if (Test-WSL2Default) {
            Write-Success "WSL2 is already the default version"
        }
        else {
            # Try to set WSL2 as default
            if (-not (Set-WSL2Default)) {
                # May need kernel update
                Install-WSL2Kernel
                Set-WSL2Default
            }
        }
    }
    else {
        Write-Warning "WSL is not installed"
        
        # Enable WSL feature
        $result = Enable-WSLFeature
        if ($result -eq "restart") {
            $needsRestart = $true
        }
        elseif ($result -eq "error") {
            exit 1
        }
        
        # Enable Virtual Machine Platform
        $result = Enable-VirtualMachinePlatform
        if ($result -eq "restart") {
            $needsRestart = $true
        }
        elseif ($result -eq "error") {
            exit 1
        }
    }
    
    # Handle restart requirement
    if ($needsRestart) {
        Write-Host "`n" + ("=" * 60) -ForegroundColor Yellow
        Write-Host "RESTART REQUIRED" -ForegroundColor Yellow
        Write-Host ("=" * 60) -ForegroundColor Yellow
        Write-Host "`nWindows features have been enabled that require a restart."
        Write-Host "Please restart your computer and run this installer again."
        Write-Host "`nAfter restarting, the installation will continue automatically."
        
        # Create a marker file to indicate WSL was being installed
        $markerPath = Join-Path $env:LOCALAPPDATA "RecallHub\install-state.json"
        $markerDir = Split-Path $markerPath -Parent
        if (-not (Test-Path $markerDir)) {
            New-Item -ItemType Directory -Path $markerDir -Force | Out-Null
        }
        
        @{
            stage = "wsl-restart-pending"
            timestamp = (Get-Date).ToString("o")
        } | ConvertTo-Json | Set-Content $markerPath
        
        # Prompt for restart
        $restart = Read-Host "`nWould you like to restart now? (y/n)"
        if ($restart -eq 'y' -or $restart -eq 'Y') {
            Restart-Computer -Force
        }
        
        exit 0
    }
    
    # Install WSL2 kernel if needed
    if (-not (Install-WSL2Kernel)) {
        Write-Warning "WSL2 kernel installation may have failed"
    }
    
    # Set WSL2 as default
    Set-WSL2Default
    
    # Show final status
    Get-WSLStatus
    
    Write-Host "`n" + ("=" * 60) -ForegroundColor Green
    Write-Host "WSL2 SETUP COMPLETE" -ForegroundColor Green
    Write-Host ("=" * 60) -ForegroundColor Green
    Write-Host "`nWSL2 is now configured and ready for RecallHub installation."
}

# Run main
Main
