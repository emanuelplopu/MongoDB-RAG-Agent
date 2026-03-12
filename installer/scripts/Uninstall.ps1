<#
.SYNOPSIS
    Uninstalls RecallHub completely.

.DESCRIPTION
    This script removes all RecallHub components:
    - Stops all services
    - Removes WSL2 distribution
    - Removes application files
    - Removes Start Menu shortcuts
    - Optionally removes user data

.PARAMETER KeepData
    Keep user data (documents, backups) after uninstallation

.PARAMETER Force
    Skip confirmation prompts
#>

param(
    [switch]$KeepData,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$DISTRO_NAME = "RecallHub"
$APP_NAME = "RecallHub"
$INSTALL_PATH = "$env:LOCALAPPDATA\RecallHub"

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

function Stop-TrayApp {
    Write-Step "Stopping RecallHub tray application..."
    
    try {
        $process = Get-Process -Name "RecallHubTray" -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Name "RecallHubTray" -Force
            Write-Success "Tray application stopped"
        }
        else {
            Write-Success "Tray application not running"
        }
    }
    catch {
        Write-Warning "Could not stop tray application"
    }
}

function Stop-AllServices {
    Write-Step "Stopping all services..."
    
    # Run stop script if it exists
    $stopScript = "$INSTALL_PATH\scripts\Stop-Services.ps1"
    if (Test-Path $stopScript) {
        try {
            & $stopScript -Force -StopWSL
            Write-Success "Services stopped"
        }
        catch {
            Write-Warning "Could not stop services gracefully"
        }
    }
    
    # Force terminate WSL if still running
    try {
        wsl --terminate $DISTRO_NAME 2>&1 | Out-Null
    }
    catch {
        # Ignore errors
    }
}

function Remove-WSLDistro {
    Write-Step "Removing WSL2 distribution..."
    
    try {
        $distros = wsl --list --quiet 2>&1
        if ($distros -contains $DISTRO_NAME) {
            wsl --unregister $DISTRO_NAME 2>&1 | Out-Null
            Write-Success "WSL2 distribution removed"
        }
        else {
            Write-Success "WSL2 distribution not found"
        }
    }
    catch {
        Write-Warning "Could not remove WSL2 distribution: $_"
    }
}

function Remove-StartMenuShortcuts {
    Write-Step "Removing Start Menu shortcuts..."
    
    $startMenuPath = [Environment]::GetFolderPath("CommonStartMenu")
    $programsPath = Join-Path $startMenuPath "Programs\$APP_NAME"
    
    try {
        if (Test-Path $programsPath) {
            Remove-Item -Path $programsPath -Recurse -Force
            Write-Success "Start Menu shortcuts removed"
        }
        else {
            Write-Success "No Start Menu shortcuts found"
        }
    }
    catch {
        Write-Warning "Could not remove Start Menu shortcuts"
    }
    
    # Also check user's Start Menu
    $userStartMenu = [Environment]::GetFolderPath("StartMenu")
    $userProgramsPath = Join-Path $userStartMenu "Programs\$APP_NAME"
    
    try {
        if (Test-Path $userProgramsPath) {
            Remove-Item -Path $userProgramsPath -Recurse -Force
            Write-Success "User Start Menu shortcuts removed"
        }
    }
    catch {
        Write-Warning "Could not remove user Start Menu shortcuts"
    }
}

function Remove-DesktopShortcuts {
    Write-Step "Removing desktop shortcuts..."
    
    $desktops = @(
        [Environment]::GetFolderPath("Desktop"),
        [Environment]::GetFolderPath("CommonDesktopDirectory")
    )
    
    foreach ($desktop in $desktops) {
        $shortcut = Join-Path $desktop "$APP_NAME.lnk"
        try {
            if (Test-Path $shortcut) {
                Remove-Item -Path $shortcut -Force
                Write-Success "Removed: $shortcut"
            }
        }
        catch {
            Write-Warning "Could not remove: $shortcut"
        }
    }
}

function Remove-FirewallRules {
    Write-Step "Removing firewall rules..."
    
    try {
        $rules = Get-NetFirewallRule -DisplayName "RecallHub*" -ErrorAction SilentlyContinue
        if ($rules) {
            $rules | Remove-NetFirewallRule
            Write-Success "Firewall rules removed"
        }
        else {
            Write-Success "No firewall rules found"
        }
    }
    catch {
        Write-Warning "Could not remove firewall rules (may require admin)"
    }
}

function Remove-ApplicationFiles {
    param([bool]$KeepUserData)
    
    Write-Step "Removing application files..."
    
    if (-not (Test-Path $INSTALL_PATH)) {
        Write-Success "Application directory not found"
        return
    }
    
    if ($KeepUserData) {
        # Remove everything except user data directories
        $keepDirs = @("documents", "projects", "backups")
        
        Get-ChildItem -Path $INSTALL_PATH -Force | ForEach-Object {
            if ($_.Name -notin $keepDirs) {
                try {
                    Remove-Item -Path $_.FullName -Recurse -Force
                    Write-Success "Removed: $($_.Name)"
                }
                catch {
                    Write-Warning "Could not remove: $($_.Name)"
                }
            }
            else {
                Write-Host "    [KEPT] $($_.Name)" -ForegroundColor Yellow
            }
        }
    }
    else {
        # Remove everything
        try {
            Remove-Item -Path $INSTALL_PATH -Recurse -Force
            Write-Success "Application directory removed"
        }
        catch {
            Write-Warning "Could not remove application directory"
        }
    }
}

function Remove-RegistryEntries {
    Write-Step "Removing registry entries..."
    
    $registryPaths = @(
        "HKLM:\SOFTWARE\$APP_NAME",
        "HKCU:\SOFTWARE\$APP_NAME",
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$APP_NAME",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$APP_NAME"
    )
    
    foreach ($path in $registryPaths) {
        try {
            if (Test-Path $path) {
                Remove-Item -Path $path -Recurse -Force
                Write-Success "Removed: $path"
            }
        }
        catch {
            Write-Warning "Could not remove: $path"
        }
    }
}

function Remove-StartupEntry {
    Write-Step "Removing startup entry..."
    
    $startupPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\RecallHubTray.lnk"
    
    try {
        if (Test-Path $startupPath) {
            Remove-Item -Path $startupPath -Force
            Write-Success "Startup entry removed"
        }
        else {
            Write-Success "No startup entry found"
        }
    }
    catch {
        Write-Warning "Could not remove startup entry"
    }
}

function Main {
    Write-Host @"
╔═══════════════════════════════════════════════════════════════════╗
║         RecallHub Uninstaller                                     ║
║         Removes all RecallHub components                          ║
╚═══════════════════════════════════════════════════════════════════╝
"@ -ForegroundColor Magenta

    # Confirm uninstallation
    if (-not $Force) {
        Write-Host "`nThis will remove RecallHub and all its components." -ForegroundColor Yellow
        
        if (-not $KeepData) {
            Write-Host "WARNING: All user data (documents, backups) will be deleted!" -ForegroundColor Red
            Write-Host "         Use -KeepData to preserve user data."
        }
        else {
            Write-Host "User data will be preserved." -ForegroundColor Green
        }
        
        $confirm = Read-Host "`nAre you sure you want to continue? (yes/no)"
        if ($confirm -ne "yes") {
            Write-Host "Uninstallation cancelled."
            exit 0
        }
    }
    
    # Stop tray app
    Stop-TrayApp
    
    # Stop all services
    Stop-AllServices
    
    # Remove WSL distribution
    Remove-WSLDistro
    
    # Remove shortcuts
    Remove-StartMenuShortcuts
    Remove-DesktopShortcuts
    
    # Remove startup entry
    Remove-StartupEntry
    
    # Remove firewall rules
    Remove-FirewallRules
    
    # Remove registry entries
    Remove-RegistryEntries
    
    # Remove application files
    Remove-ApplicationFiles -KeepUserData $KeepData
    
    Write-Host "`n" + ("=" * 60) -ForegroundColor Green
    Write-Host "UNINSTALLATION COMPLETE" -ForegroundColor Green
    Write-Host ("=" * 60) -ForegroundColor Green
    
    if ($KeepData) {
        Write-Host "`nUser data has been preserved at: $INSTALL_PATH"
    }
    else {
        Write-Host "`nAll RecallHub components have been removed."
    }
}

# Run main
Main
