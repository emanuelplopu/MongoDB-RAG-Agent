<#
.SYNOPSIS
    Installs and configures WSL2 for RecallHub.

.DESCRIPTION
    This script:
    1. Checks Windows version compatibility
    2. Enables WSL2 feature if not present
    3. Sets WSL2 as the default version
    4. Optionally installs a default Linux distribution

    Supports resume-after-restart: if features require a reboot, the script
    saves state and picks up where it left off on the next run.

.NOTES
    Requires administrator privileges.
    May require a system restart after enabling WSL2.
#>

param(
    [switch]$SkipDistro,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Logging module integration
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Logging.psm1") -Force

$logFile = Initialize-InstallLog -LogName "install-wsl2"
Write-InstallLog "Starting WSL2 installation" -Source "Install-WSL2"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
$MIN_BUILD = 19041
$StateFile = Join-Path $env:LOCALAPPDATA "RecallHub\install-state.json"

# Track current section for error handler
$currentSection = "Initialization"

# ---------------------------------------------------------------------------
# State management functions
# ---------------------------------------------------------------------------
function Save-InstallState {
    param(
        [string]$Phase,
        [string]$Status,
        [hashtable]$Data = @{}
    )

    $state = @{
        phase          = $Phase
        status         = $Status
        timestamp      = (Get-Date).ToString("o")
        windowsVersion = [System.Environment]::OSVersion.Version.ToString()
        data           = $Data
        checksum       = $null
    }
    # Add checksum for integrity verification
    $json = $state | ConvertTo-Json -Depth 5
    $state.checksum = [System.BitConverter]::ToString(
        [System.Security.Cryptography.SHA256]::Create().ComputeHash(
            [System.Text.Encoding]::UTF8.GetBytes($json)
        )
    ).Replace("-", "").Substring(0, 16)

    $stateDir = Split-Path $StateFile -Parent
    if (-not (Test-Path $stateDir)) { New-Item -Path $stateDir -ItemType Directory -Force | Out-Null }

    $state | ConvertTo-Json -Depth 5 | Set-Content -Path $StateFile -Encoding UTF8
    Write-InstallLog "State saved: Phase=$Phase, Status=$Status" -Level DEBUG -Source "Install-WSL2"
}

function Get-InstallState {
    if (-not (Test-Path $StateFile)) { return $null }
    try {
        $state = Get-Content $StateFile -Raw | ConvertFrom-Json
        Write-InstallLog "Resumed from state: Phase=$($state.phase), Status=$($state.status)" -Source "Install-WSL2"
        return $state
    } catch {
        Write-InstallLog "State file corrupted, starting fresh" -Level WARN -Source "Install-WSL2"
        return $null
    }
}

function Clear-InstallState {
    if (Test-Path $StateFile) {
        Remove-Item $StateFile -Force -ErrorAction SilentlyContinue
        Write-InstallLog "Cleared install state file" -Level DEBUG -Source "Install-WSL2"
    }
}

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------
function Test-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-WindowsVersion {
    $osVersion = [System.Environment]::OSVersion.Version
    $build = $osVersion.Build

    Write-InstallLog "Checking Windows version... Current build: $build, Required: $MIN_BUILD+" -Source "Install-WSL2"

    if ($build -lt $MIN_BUILD) {
        Write-InstallLog "Windows build $build is not supported. WSL2 requires build $MIN_BUILD or later." -Level ERROR -Source "Install-WSL2"
        Write-InstallLog "Please update Windows to version 2004 or later." -Level ERROR -Source "Install-WSL2"
        return $false
    }

    Write-InstallLog "Windows version is compatible (build $build)" -Source "Install-WSL2"
    return $true
}

function Test-WSLInstalled {
    try {
        $wslPath = Get-Command wsl.exe -ErrorAction SilentlyContinue
        return ($null -ne $wslPath)
    } catch {
        return $false
    }
}

function Test-WSL2Default {
    try {
        $output = wsl --status 2>&1
        return $output -match "Default Version: 2"
    } catch {
        return $false
    }
}

# ---------------------------------------------------------------------------
# Restart detection
# ---------------------------------------------------------------------------
function Test-RestartRequired {
    $state = Get-InstallState
    if ($state -and $state.status -eq "restart-required") {
        return $true
    }

    # Also check if features require restart
    try {
        $wslFeature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue
        $vmFeature = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction SilentlyContinue

        if (($wslFeature -and $wslFeature.RestartNeeded) -or ($vmFeature -and $vmFeature.RestartNeeded)) {
            return $true
        }
    } catch {
        Write-InstallLog "Could not query feature restart state: $_" -Level WARN -Source "Install-WSL2"
    }
    return $false
}

function Test-IsResumeRun {
    $state = Get-InstallState
    if ($null -eq $state) { return $false }

    # If the last run required restart AND features are now enabled, this is a resume
    if ($state.status -eq "restart-required" -and $state.data.featuresEnabled) {
        # Verify features are actually enabled now
        try {
            $wslFeature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue
            $vmFeature = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction SilentlyContinue

            if ($wslFeature.State -eq "Enabled" -and $vmFeature.State -eq "Enabled") {
                Write-InstallLog "Resume run detected - features now enabled after restart" -Source "Install-WSL2"
                return $true
            }
        } catch {}
    }
    return $false
}

# ---------------------------------------------------------------------------
# Main installation logic
# ---------------------------------------------------------------------------
function Main {
    Write-InstallLog @"
=====================================================================
         RecallHub WSL2 Installation Script
         Configures Windows Subsystem for Linux 2
=====================================================================
"@ -Source "Install-WSL2"

    # ------------------------------------------------------------------
    # Section: Prerequisites
    # ------------------------------------------------------------------
    $script:currentSection = "Prerequisites"
    Start-InstallSection -Name "Prerequisites"

    # Check for admin privileges
    if (-not (Test-Administrator)) {
        Write-InstallLog "This script requires administrator privileges." -Level ERROR -Source "Install-WSL2"
        Write-InstallLog "Please run PowerShell as Administrator and try again." -Level ERROR -Source "Install-WSL2"
        Write-InstallDiagnostic -ErrorCode "WSL-000" -Component "WSL" -Message "Not running as Administrator" -Context @{}
        Complete-InstallSection -Name "Prerequisites" -Status Failed
        Export-InstallSummary
        exit 1
    }
    Write-InstallLog "Running with administrator privileges" -Source "Install-WSL2"

    # Check Windows version
    if (-not (Test-WindowsVersion)) {
        Write-InstallDiagnostic -ErrorCode "WSL-000" -Component "WSL" -Message "Windows version too old for WSL2" -Context @{
            currentBuild = [System.Environment]::OSVersion.Version.Build
            requiredBuild = $MIN_BUILD
        }
        Complete-InstallSection -Name "Prerequisites" -Status Failed
        Export-InstallSummary
        exit 1
    }

    Complete-InstallSection -Name "Prerequisites" -Status Success

    # ------------------------------------------------------------------
    # Check for resume-after-restart
    # ------------------------------------------------------------------
    $isResume = Test-IsResumeRun
    if ($isResume) {
        Write-InstallLog "This is a resume run after restart - skipping feature enablement" -Source "Install-WSL2"
        Clear-InstallState
    }

    # ------------------------------------------------------------------
    # Section: Windows Features
    # ------------------------------------------------------------------
    $needsRestart = $false

    if (-not $isResume) {
        $script:currentSection = "Windows Features"
        Start-InstallSection -Name "Windows Features"

        if (Test-WSLInstalled -and (Test-WSL2Default) -and -not $Force) {
            Write-InstallLog "WSL2 is already installed and set as default" -Source "Install-WSL2"
            Complete-InstallSection -Name "Windows Features" -Status Skipped
        } else {
            # Enable WSL feature
            Write-InstallLog "Enabling Microsoft-Windows-Subsystem-Linux..." -Source "Install-WSL2"
            try {
                $wslFeature = Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart -ErrorAction Stop
            } catch {
                Write-InstallLog "Failed to enable WSL feature: $_" -Level ERROR -Source "Install-WSL2"
                Write-InstallDiagnostic -ErrorCode "WSL-001" -Component "WSL" -Message "Failed to enable WSL feature" -Context @{
                    error = $_.Exception.Message
                }
                Complete-InstallSection -Name "Windows Features" -Status Failed
                throw "WSL feature enablement failed: $_"
            }

            # Verify it's actually enabled
            $verifyWsl = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux
            if ($verifyWsl.State -ne "Enabled" -and -not $wslFeature.RestartNeeded) {
                Write-InstallDiagnostic -ErrorCode "WSL-001" -Component "WSL" -Message "WSL feature failed to enable" -Context @{
                    requestedState = "Enabled"
                    actualState    = $verifyWsl.State
                }
                Complete-InstallSection -Name "Windows Features" -Status Failed
                throw "WSL feature enablement verification failed"
            }

            if ($wslFeature.RestartNeeded) {
                Write-InstallLog "WSL feature enabled (restart required)" -Level WARN -Source "Install-WSL2"
                $needsRestart = $true
            } else {
                Write-InstallLog "WSL feature enabled successfully (verified)" -Source "Install-WSL2"
            }

            # Enable VirtualMachinePlatform
            Write-InstallLog "Enabling VirtualMachinePlatform..." -Source "Install-WSL2"
            try {
                $vmFeature = Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart -ErrorAction Stop
            } catch {
                Write-InstallLog "Failed to enable VirtualMachinePlatform: $_" -Level ERROR -Source "Install-WSL2"
                Write-InstallDiagnostic -ErrorCode "WSL-002" -Component "WSL" -Message "VirtualMachinePlatform failed to enable" -Context @{
                    error = $_.Exception.Message
                }
                Complete-InstallSection -Name "Windows Features" -Status Failed
                throw "VirtualMachinePlatform enablement failed: $_"
            }

            $verifyVm = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform
            if ($verifyVm.State -ne "Enabled" -and -not $vmFeature.RestartNeeded) {
                Write-InstallDiagnostic -ErrorCode "WSL-002" -Component "WSL" -Message "VirtualMachinePlatform failed to enable" -Context @{
                    requestedState = "Enabled"
                    actualState    = $verifyVm.State
                }
                Complete-InstallSection -Name "Windows Features" -Status Failed
                throw "VirtualMachinePlatform enablement verification failed"
            }

            if ($vmFeature.RestartNeeded) {
                Write-InstallLog "VirtualMachinePlatform enabled (restart required)" -Level WARN -Source "Install-WSL2"
                $needsRestart = $true
            } else {
                Write-InstallLog "VirtualMachinePlatform enabled successfully (verified)" -Source "Install-WSL2"
            }

            Complete-InstallSection -Name "Windows Features" -Status Success
        }
    }

    # ------------------------------------------------------------------
    # Handle restart requirement
    # ------------------------------------------------------------------
    if ($needsRestart -or (Test-RestartRequired)) {
        $resumeMsg = @"
+==============================================================+
|  RESTART REQUIRED                                            |
|                                                              |
|  Windows needs to restart to complete WSL2 installation.     |
|                                                              |
|  After restart, run the installer again - it will resume     |
|  from where it left off automatically.                       |
|                                                              |
|  Resume state saved to:                                      |
|  $StateFile
|                                                              |
|  Log file: $logFile
+==============================================================+
"@
        Write-InstallLog $resumeMsg -Level WARN -Source "Install-WSL2"
        Save-InstallState -Phase "wsl-install" -Status "restart-required" -Data @{
            featuresEnabled     = $true
            logFile             = $logFile
            resumeInstructions  = "Run installer again after restart"
        }
        Export-InstallSummary
        exit 3010
    }

    # ------------------------------------------------------------------
    # Section: WSL Kernel
    # ------------------------------------------------------------------
    $script:currentSection = "WSL Kernel"
    Start-InstallSection -Name "WSL Kernel"

    # Check current WSL version
    try {
        $wslVersion = wsl --version 2>&1
        Write-InstallLog "WSL Version info:" -Source "Install-WSL2"
        foreach ($line in $wslVersion) {
            Write-InstallLog "  $line" -Source "Install-WSL2"
        }
    } catch {
        Write-InstallLog "Could not determine WSL version (may need install)" -Level WARN -Source "Install-WSL2"
    }

    Complete-InstallSection -Name "WSL Kernel" -Status Success

    # ------------------------------------------------------------------
    # Section: WSL Install
    # ------------------------------------------------------------------
    $script:currentSection = "WSL Install"
    Start-InstallSection -Name "WSL Install"

    Write-InstallLog "Running wsl --install (timeout: 5 minutes)..." -Source "Install-WSL2"

    $installResult = Invoke-WithRetry -OperationName "WSL Install" -RetryCount 1 -TimeoutSeconds 300 -ScriptBlock {
        $output = wsl --install --no-distribution 2>&1
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 3010) {
            # 3010 = restart required, which is expected
            throw "WSL install failed with exit code $LASTEXITCODE : $output"
        }
        return @{ exitCode = $LASTEXITCODE; output = $output }
    }

    if ($installResult -and $installResult.exitCode -eq 3010) {
        Write-InstallLog "WSL installation requires restart (exit code 3010)" -Level WARN -Source "Install-WSL2"
        Save-InstallState -Phase "wsl-install" -Status "restart-required" -Data @{ reason = "WSL kernel installation" }
        Complete-InstallSection -Name "WSL Install" -Status Success
        Export-InstallSummary
        exit 3010
    }

    Complete-InstallSection -Name "WSL Install" -Status Success

    # ------------------------------------------------------------------
    # Section: WSL2 Default
    # ------------------------------------------------------------------
    $script:currentSection = "WSL2 Default"
    Start-InstallSection -Name "WSL2 Default"

    Write-InstallLog "Setting WSL2 as default version..." -Source "Install-WSL2"
    try {
        wsl --set-default-version 2 2>&1 | Out-Null
        Write-InstallLog "WSL2 set as default version" -Source "Install-WSL2"
    } catch {
        Write-InstallLog "Failed to set WSL2 as default: $_" -Level WARN -Source "Install-WSL2"
    }

    Complete-InstallSection -Name "WSL2 Default" -Status Success

    # ------------------------------------------------------------------
    # Section: Final Status
    # ------------------------------------------------------------------
    $script:currentSection = "Final Status"
    Start-InstallSection -Name "Final Status"

    try {
        $output = wsl --status 2>&1
        Write-InstallLog "WSL Status:" -Source "Install-WSL2"
        foreach ($line in $output) {
            Write-InstallLog "  $line" -Source "Install-WSL2"
        }
    } catch {
        Write-InstallLog "Could not get WSL status" -Level WARN -Source "Install-WSL2"
    }

    Complete-InstallSection -Name "Final Status" -Status Success

    # ------------------------------------------------------------------
    # Cleanup and summary
    # ------------------------------------------------------------------
    Clear-InstallState

    Write-InstallLog @"
=====================================================================
  WSL2 SETUP COMPLETE
  WSL2 is now configured and ready for RecallHub installation.
=====================================================================
"@ -Source "Install-WSL2"

    Export-InstallSummary
}

# ---------------------------------------------------------------------------
# Execute with global error handler
# ---------------------------------------------------------------------------
try {
    Main
} catch {
    Complete-InstallSection -Name $currentSection -Status Failed

    # Capture DISM log
    $dismLog = "$env:WINDIR\Logs\DISM\dism.log"
    $dismTail = ""
    if (Test-Path $dismLog) {
        $dismTail = Get-Content $dismLog -Tail 30 | Out-String
        Write-InstallLog "DISM log (last 30 lines):`n$dismTail" -Level ERROR -Source "Install-WSL2"
    }

    # Capture relevant Windows Event Log entries
    try {
        $events = Get-WinEvent -FilterHashtable @{
            LogName   = 'System'
            StartTime = (Get-Date).AddMinutes(-5)
            Level     = @(1, 2, 3)  # Critical, Error, Warning
        } -MaxEvents 10 -ErrorAction SilentlyContinue

        if ($events) {
            Write-InstallLog "Recent system events:" -Level ERROR -Source "Install-WSL2"
            foreach ($evt in $events) {
                Write-InstallLog "  [$($evt.TimeCreated)] $($evt.ProviderName): $($evt.Message)" -Level ERROR -Source "Install-WSL2"
            }
        }
    } catch {}

    # Capture feature state
    $featureState = @{}
    try {
        $featureState.wsl = (Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue).State
        $featureState.vm = (Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction SilentlyContinue).State
    } catch {}

    Write-InstallDiagnostic -ErrorCode "WSL-099" -Component "WSL" -Message $_.Exception.Message -Context @{
        dismLog      = $dismTail
        featureState = $featureState
        windowsBuild = [System.Environment]::OSVersion.Version.Build
    } -Exception $_.Exception

    Save-InstallState -Phase $currentSection -Status "failed" -Data @{ error = $_.Exception.Message }
    Export-InstallSummary
    throw
}
