#Requires -Version 5.1
<#
.SYNOPSIS
    RecallHub pre-flight validation - checks system requirements before installation.
.DESCRIPTION
    Validates Windows version, disk space, RAM, WSL2, network, ports, GPU, and existing installations.
    Produces a structured report with PASS/WARN/FAIL for each check.
.PARAMETER OutputPath
    Optional path for the JSON results file. Defaults to LOCALAPPDATA\RecallHub\logs\preflight-TIMESTAMP.json
.PARAMETER SkipNetwork
    Skip network connectivity checks (for offline installs)
#>
param(
    [string]$OutputPath,
    [switch]$SkipNetwork,
    [string]$ConfigPath
)

# ---------------------------------------------------------------------------
# Module import and log initialization
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Logging.psm1") -Force

$logFile = Initialize-InstallLog -LogName "preflight"
Write-InstallLog "Starting pre-flight validation" -Source "Test-Prerequisites"

# ---------------------------------------------------------------------------
# Load configuration
# ---------------------------------------------------------------------------
$InstallerDir = Split-Path $ScriptDir -Parent
if (-not $ConfigPath) { $ConfigPath = Join-Path $InstallerDir "config.json" }
$Config = if (Test-Path $ConfigPath) { Get-Content $ConfigPath -Raw | ConvertFrom-Json } else { $null }

# Derive requirements from config
$RequiredDiskGB = if ($Config) { $Config.installer.requiredDiskSpaceGB } else { 10 }
$RecommendedDiskGB = if ($Config) { $Config.installer.recommendedDiskSpaceGB } else { 15 }
$RequiredRAMGB = if ($Config) { $Config.installer.requiredRAMGB } else { 8 }
$RecommendedRAMGB = if ($Config) { $Config.installer.recommendedRAMGB } else { 16 }
$CheckPorts = if ($Config) { 
    @($Config.ports.backend, $Config.ports.frontend, $Config.ports.ollama, $Config.ports.mongodb)
} else { 
    @(11000, 11080, 11434, 27017) 
}

# ---------------------------------------------------------------------------
# Results collection
# ---------------------------------------------------------------------------
$results = [System.Collections.ArrayList]::new()

# ---------------------------------------------------------------------------
# 1. Windows Version
# ---------------------------------------------------------------------------
Start-InstallSection -Name "Windows Version Check"
$winResult = Test-Prerequisite -Name "Windows Version" -Required $true -Description "Windows build must be >= 19041 (Windows 10 2004+)" -ScriptBlock {
    $build = [System.Environment]::OSVersion.Version.Build
    if ($build -ge 19041) {
        return "Build $build (>= 19041)"
    } else {
        throw "Build $build is below minimum 19041"
    }
}
$results.Add($winResult) | Out-Null
Complete-InstallSection -Name "Windows Version Check" -Status $(if ($winResult.Status -eq 'Fail') { 'Failed' } else { 'Success' })

# ---------------------------------------------------------------------------
# 2. Available Disk Space
# ---------------------------------------------------------------------------
Start-InstallSection -Name "Disk Space Check"
$diskResult = Test-Prerequisite -Name "Disk Space" -Required $true -Description "At least $RequiredDiskGB GB free disk space required, $RecommendedDiskGB GB recommended" -ScriptBlock {
    $targetDrive = $env:SystemDrive
    if (-not $targetDrive) { $targetDrive = "C:" }
    $driveLetter = $targetDrive.TrimEnd(':')
    $drive = Get-PSDrive -Name $driveLetter -ErrorAction Stop
    $freeGB = [math]::Round($drive.Free / 1GB, 1)

    if ($freeGB -lt $RequiredDiskGB) {
        throw "$freeGB GB free (minimum $RequiredDiskGB GB required)"
    } elseif ($freeGB -lt $RecommendedDiskGB) {
        # Return a message but mark as warn via the Test-Prerequisite mechanism
        # We need to signal warn - use a custom approach
        throw "$freeGB GB free ($RecommendedDiskGB GB recommended)"
    } else {
        return "$freeGB GB free (>= $RecommendedDiskGB GB)"
    }
}
# Re-check disk space with a warn-tolerant approach
if ($diskResult.Status -eq 'Fail') {
    # Determine if it's a hard fail (<RequiredDiskGB) or soft warn (<RecommendedDiskGB)
    $targetDrive = $env:SystemDrive
    if (-not $targetDrive) { $targetDrive = "C:" }
    $driveLetter = $targetDrive.TrimEnd(':')
    try {
        $drive = Get-PSDrive -Name $driveLetter -ErrorAction Stop
        $freeGB = [math]::Round($drive.Free / 1GB, 1)
        if ($freeGB -ge $RequiredDiskGB -and $freeGB -lt $RecommendedDiskGB) {
            # Override to Warn - space is sufficient but not recommended
            $diskResult = [PSCustomObject]@{
                Name     = "Disk Space"
                Status   = "Warn"
                Required = $true
                Message  = "$freeGB GB free ($RecommendedDiskGB GB recommended)"
                Duration = $diskResult.Duration
            }
            Write-InstallLog "Disk space $freeGB GB - above minimum but below recommended $RecommendedDiskGB GB" -Level 'WARN' -Source "Test-Prerequisites"
        }
    } catch { }
}
$results.Add($diskResult) | Out-Null
Complete-InstallSection -Name "Disk Space Check" -Status $(if ($diskResult.Status -eq 'Fail') { 'Failed' } elseif ($diskResult.Status -eq 'Warn') { 'Success' } else { 'Success' })

# ---------------------------------------------------------------------------
# 3. System RAM
# ---------------------------------------------------------------------------
Start-InstallSection -Name "System RAM Check"
$ramResult = Test-Prerequisite -Name "System RAM" -Required $true -Description "At least $RequiredRAMGB GB RAM required, $RecommendedRAMGB GB recommended for local LLM" -ScriptBlock {
    $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
    $ramGB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)

    if ($ramGB -lt $RequiredRAMGB) {
        throw "$ramGB GB RAM (minimum $RequiredRAMGB GB required)"
    } elseif ($ramGB -lt $RecommendedRAMGB) {
        throw "$ramGB GB RAM ($RecommendedRAMGB GB recommended for local LLM)"
    } else {
        return "$ramGB GB RAM (>= $RecommendedRAMGB GB)"
    }
}
# Re-check RAM for warn vs fail
if ($ramResult.Status -eq 'Fail') {
    try {
        $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
        $ramGB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
        if ($ramGB -ge $RequiredRAMGB -and $ramGB -lt $RecommendedRAMGB) {
            $ramResult = [PSCustomObject]@{
                Name     = "System RAM"
                Status   = "Warn"
                Required = $true
                Message  = "$ramGB GB RAM ($RecommendedRAMGB GB recommended for local LLM)"
                Duration = $ramResult.Duration
            }
            Write-InstallLog "System RAM $ramGB GB - above minimum but below recommended $RecommendedRAMGB GB" -Level 'WARN' -Source "Test-Prerequisites"
        }
    } catch { }
}
$results.Add($ramResult) | Out-Null
Complete-InstallSection -Name "System RAM Check" -Status $(if ($ramResult.Status -eq 'Fail') { 'Failed' } elseif ($ramResult.Status -eq 'Warn') { 'Success' } else { 'Success' })

# ---------------------------------------------------------------------------
# 4. WSL2 Feature Status
# ---------------------------------------------------------------------------
Start-InstallSection -Name "WSL2 Features Check"
$wslResult = Test-Prerequisite -Name "WSL2 Features" -Required $false -Description "WSL and VirtualMachinePlatform features needed for Docker/WSL2" -ScriptBlock {
    $wslFeature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction Stop
    $vmFeature = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction Stop

    $wslEnabled = $wslFeature.State -eq 'Enabled'
    $vmEnabled = $vmFeature.State -eq 'Enabled'

    if ($wslEnabled -and $vmEnabled) {
        return "Both WSL and VirtualMachinePlatform enabled"
    } elseif (-not $wslEnabled -and -not $vmEnabled) {
        throw "WSL and VirtualMachinePlatform both disabled (will be enabled during install)"
    } elseif (-not $wslEnabled) {
        throw "WSL disabled (will be enabled during install)"
    } else {
        throw "VirtualMachinePlatform disabled (will be enabled during install)"
    }
}
$results.Add($wslResult) | Out-Null
Complete-InstallSection -Name "WSL2 Features Check" -Status $(if ($wslResult.Status -eq 'Fail') { 'Failed' } else { 'Success' })

# ---------------------------------------------------------------------------
# 5. Network Connectivity
# ---------------------------------------------------------------------------
Start-InstallSection -Name "Network Connectivity Check"
if ($SkipNetwork) {
    $netResult = [PSCustomObject]@{
        Name     = "Network Connectivity"
        Status   = "Pass"
        Required = $false
        Message  = "Skipped (-SkipNetwork specified)"
        Duration = 0
    }
    Write-InstallLog "Network check skipped per -SkipNetwork flag" -Level 'INFO' -Source "Test-Prerequisites"
    Complete-InstallSection -Name "Network Connectivity Check" -Status 'Skipped'
} else {
    $netResult = Test-Prerequisite -Name "Network Connectivity" -Required $false -Description "Connectivity to Docker Hub and Ollama registry" -ScriptBlock {
        $endpoints = @(
            @{ Host = "hub.docker.com"; Port = 443 },
            @{ Host = "registry.ollama.ai"; Port = 443 }
        )
        $unreachable = @()

        foreach ($ep in $endpoints) {
            try {
                $tcp = Test-NetConnection -ComputerName $ep.Host -Port $ep.Port -WarningAction SilentlyContinue -ErrorAction Stop
                if (-not $tcp.TcpTestSucceeded) {
                    $unreachable += "$($ep.Host):$($ep.Port)"
                }
            } catch {
                $unreachable += "$($ep.Host):$($ep.Port)"
            }
        }

        if ($unreachable.Count -gt 0) {
            throw "Unreachable: $($unreachable -join ', ') (offline mode may be required)"
        }
        return "hub.docker.com and registry.ollama.ai reachable"
    }
    Complete-InstallSection -Name "Network Connectivity Check" -Status $(if ($netResult.Status -eq 'Fail') { 'Failed' } else { 'Success' })
}
$results.Add($netResult) | Out-Null

# ---------------------------------------------------------------------------
# 6. Port Availability
# ---------------------------------------------------------------------------
Start-InstallSection -Name "Port Availability Check"
$portResult = Test-Prerequisite -Name "Port Availability" -Required $true -Description "Required ports must be free: $($CheckPorts -join ', ')" -ScriptBlock {
    $requiredPorts = $CheckPorts
    $criticalPorts = @($CheckPorts | Select-Object -Last 2)  # MongoDB and Ollama

    try {
        $listeners = Get-NetTCPConnection -State Listen -ErrorAction Stop | Select-Object -ExpandProperty LocalPort -Unique
    } catch {
        $listeners = @()
    }

    $occupied = @()
    foreach ($port in $requiredPorts) {
        if ($listeners -contains $port) {
            $occupied += $port
        }
    }

    if ($occupied.Count -eq 0) {
        return "All ports free ($($CheckPorts -join ', '))"
    }

    $criticalConflicts = $occupied | Where-Object { $criticalPorts -contains $_ }
    if ($criticalConflicts.Count -gt 0) {
        throw "Critical ports occupied: $($criticalConflicts -join ', '). All conflicts: $($occupied -join ', ')"
    }

    # Non-critical ports occupied - warn
    throw "Ports occupied: $($occupied -join ', ') (non-critical, may cause conflicts)"
}
# Override to warn if only non-critical ports are occupied
if ($portResult.Status -eq 'Fail') {
    $criticalPorts = @($CheckPorts | Select-Object -Last 2)
    try {
        $listeners = Get-NetTCPConnection -State Listen -ErrorAction Stop | Select-Object -ExpandProperty LocalPort -Unique
    } catch {
        $listeners = @()
    }
    $criticalConflicts = $criticalPorts | Where-Object { $listeners -contains $_ }
    if ($criticalConflicts.Count -eq 0) {
        # No critical port conflicts, downgrade to warn
        $occupied = $CheckPorts | Where-Object { $listeners -contains $_ }
        $portResult = [PSCustomObject]@{
            Name     = "Port Availability"
            Status   = "Warn"
            Required = $true
            Message  = "Ports occupied: $($occupied -join ', ') (non-critical)"
            Duration = $portResult.Duration
        }
        Write-InstallLog "Non-critical port conflicts detected: $($occupied -join ', ')" -Level 'WARN' -Source "Test-Prerequisites"
    }
}
$results.Add($portResult) | Out-Null
Complete-InstallSection -Name "Port Availability Check" -Status $(if ($portResult.Status -eq 'Fail') { 'Failed' } elseif ($portResult.Status -eq 'Warn') { 'Success' } else { 'Success' })

# ---------------------------------------------------------------------------
# 7. Existing Installation Detection
# ---------------------------------------------------------------------------
Start-InstallSection -Name "Existing Installation Check"
$existResult = Test-Prerequisite -Name "Existing Installation" -Required $false -Description "Check for previous RecallHub installation" -ScriptBlock {
    $findings = @()

    # Check WSL distro
    try {
        $wslList = wsl -l -q 2>$null
        if ($wslList -match 'RecallHub') {
            $findings += "WSL distro 'RecallHub' found"
        }
    } catch { }

    # Check Docker images
    try {
        $images = docker images --format "{{.Repository}}" 2>$null
        $rhImages = $images | Where-Object { $_ -match 'recallhub' }
        if ($rhImages) {
            $findings += "Docker images: $($rhImages -join ', ')"
        }
    } catch { }

    # Check registry
    try {
        if (Test-Path "HKLM:\SOFTWARE\RecallHub") {
            $findings += "Registry key HKLM:\SOFTWARE\RecallHub exists"
        }
    } catch { }

    if ($findings.Count -gt 0) {
        throw "Existing installation detected: $($findings -join '; ')"
    }
    return "No existing installation found"
}
$results.Add($existResult) | Out-Null
Complete-InstallSection -Name "Existing Installation Check" -Status $(if ($existResult.Status -eq 'Fail') { 'Failed' } else { 'Success' })

# ---------------------------------------------------------------------------
# 8. Antivirus Detection
# ---------------------------------------------------------------------------
Start-InstallSection -Name "Antivirus Detection"
$avResult = Test-Prerequisite -Name "Antivirus Detection" -Required $false -Description "Detect active AV that may interfere with installation" -ScriptBlock {
    $warnings = @()

    # Check Windows Defender real-time protection
    try {
        $mpPref = Get-MpPreference -ErrorAction Stop
        if ($mpPref.DisableRealtimeMonitoring -eq $false) {
            $warnings += "Windows Defender real-time protection is ON"
        }
    } catch {
        # Get-MpPreference may not be available on all editions
    }

    # Check common AV processes
    $avProcesses = @('avast', 'avgui', 'avguard', 'norton', 'ns', 'kaspersky', 'avp', 'bitdefender', 'bdagent', 'mcafee', 'mcshield')
    try {
        $running = Get-Process -ErrorAction SilentlyContinue | Where-Object {
            $name = $_.ProcessName.ToLower()
            $avProcesses | Where-Object { $name -match $_ }
        }
        if ($running) {
            $avNames = ($running | Select-Object -ExpandProperty ProcessName -Unique) -join ', '
            $warnings += "AV processes detected: $avNames"
        }
    } catch { }

    if ($warnings.Count -gt 0) {
        throw "$($warnings -join '; '). Consider excluding the install directory from real-time scanning."
    }
    return "No AV interference detected"
}
# AV detection always passes but may warn - it's never a fail condition
if ($avResult.Status -eq 'Fail') {
    $avResult = [PSCustomObject]@{
        Name     = "Antivirus Detection"
        Status   = "Warn"
        Required = $false
        Message  = $avResult.Message
        Duration = $avResult.Duration
    }
}
$results.Add($avResult) | Out-Null
Complete-InstallSection -Name "Antivirus Detection" -Status 'Success'

# ---------------------------------------------------------------------------
# 9. GPU Detection (Ollama acceleration)
# ---------------------------------------------------------------------------
Start-InstallSection -Name "GPU Detection"
$gpuResult = Test-Prerequisite -Name "GPU Detection" -Required $false -Description "NVIDIA GPU for Ollama acceleration" -ScriptBlock {
    $gpuInfo = Get-CimInstance Win32_VideoController -ErrorAction Stop

    $nvidiaGpu = $gpuInfo | Where-Object { $_.Name -match 'NVIDIA' }
    $amdGpu = $gpuInfo | Where-Object { $_.Name -match 'AMD' -and $_.Name -notmatch 'Radeon.*Graphics' -or $_.Name -match 'Radeon RX' }

    if ($nvidiaGpu) {
        # Check nvidia-smi
        $nvidiaSmi = $null
        try {
            $nvidiaSmi = Get-Command nvidia-smi -ErrorAction Stop
        } catch { }

        $gpuName = ($nvidiaGpu | Select-Object -First 1).Name
        if ($nvidiaSmi) {
            return "NVIDIA GPU found: $gpuName (nvidia-smi available)"
        } else {
            throw "NVIDIA GPU found: $gpuName (nvidia-smi NOT found - driver may need update)"
        }
    } elseif ($amdGpu) {
        $gpuName = ($amdGpu | Select-Object -First 1).Name
        throw "AMD GPU found: $gpuName (not supported by Ollama, CPU-only mode will be used)"
    } else {
        throw "No dedicated GPU found (CPU-only mode will be used for Ollama)"
    }
}
# GPU is never a hard fail - override to Warn
if ($gpuResult.Status -eq 'Fail') {
    $gpuResult = [PSCustomObject]@{
        Name     = "GPU Detection"
        Status   = "Warn"
        Required = $false
        Message  = $gpuResult.Message
        Duration = $gpuResult.Duration
    }
}
$results.Add($gpuResult) | Out-Null
Complete-InstallSection -Name "GPU Detection" -Status 'Success'

# ---------------------------------------------------------------------------
# Generate summary report
# ---------------------------------------------------------------------------
Write-InstallLog "All pre-flight checks completed" -Source "Test-Prerequisites"

# Determine overall status
$failCount = ($results | Where-Object { $_.Status -eq 'Fail' }).Count
$warnCount = ($results | Where-Object { $_.Status -eq 'Warn' }).Count
$passCount = ($results | Where-Object { $_.Status -eq 'Pass' }).Count

if ($failCount -gt 0) {
    $overallStatus = "NOT READY ($failCount failures)"
    $overallColor = 'Red'
} elseif ($warnCount -gt 0) {
    $overallStatus = "READY ($warnCount warnings)"
    $overallColor = 'Yellow'
} else {
    $overallStatus = "READY"
    $overallColor = 'Green'
}

# ---------------------------------------------------------------------------
# Console output - formatted table
# ---------------------------------------------------------------------------
$boxWidth = 62

Write-Host ""
Write-Host ([char]0x2554 + ([string][char]0x2550 * $boxWidth) + [char]0x2557) -ForegroundColor Cyan
Write-Host ([char]0x2551 + "                 RecallHub Pre-Flight Check                   " + [char]0x2551) -ForegroundColor Cyan
Write-Host ([char]0x2560 + ([string][char]0x2550 * $boxWidth) + [char]0x2563) -ForegroundColor Cyan

foreach ($r in $results) {
    $statusTag = switch ($r.Status) {
        'Pass' { 'PASS' }
        'Warn' { 'WARN' }
        'Fail' { 'FAIL' }
    }
    $statusColor = switch ($r.Status) {
        'Pass' { 'Green' }
        'Warn' { 'Yellow' }
        'Fail' { 'Red' }
    }

    # Truncate message to fit the box
    $nameField = $r.Name.PadRight(24)
    $maxMsgLen = $boxWidth - 34  # account for status tag, name, padding
    $msgField = if ($r.Message.Length -gt $maxMsgLen) { $r.Message.Substring(0, $maxMsgLen - 1) + "~" } else { $r.Message }
    $lineContent = "  [$statusTag] $nameField $msgField"

    # Pad or trim to box width
    if ($lineContent.Length -gt $boxWidth) {
        $lineContent = $lineContent.Substring(0, $boxWidth)
    } else {
        $lineContent = $lineContent.PadRight($boxWidth)
    }

    Write-Host -NoNewline ([char]0x2551) -ForegroundColor Cyan
    Write-Host -NoNewline $lineContent -ForegroundColor $statusColor
    Write-Host ([char]0x2551) -ForegroundColor Cyan
}

Write-Host ([char]0x2560 + ([string][char]0x2550 * $boxWidth) + [char]0x2563) -ForegroundColor Cyan

$summaryLine = "  Overall: $overallStatus"
$summaryLine = $summaryLine.PadRight($boxWidth)
Write-Host -NoNewline ([char]0x2551) -ForegroundColor Cyan
Write-Host -NoNewline $summaryLine -ForegroundColor $overallColor
Write-Host ([char]0x2551) -ForegroundColor Cyan

Write-Host ([char]0x255A + ([string][char]0x2550 * $boxWidth) + [char]0x255D) -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# Export JSON results
# ---------------------------------------------------------------------------
if (-not $OutputPath) {
    $logDir = Join-Path $env:LOCALAPPDATA 'RecallHub\logs'
    if (-not (Test-Path $logDir)) {
        New-Item -Path $logDir -ItemType Directory -Force | Out-Null
    }
    $timestamp = (Get-Date).ToString('yyyy-MM-dd-HHmmss')
    $OutputPath = Join-Path $logDir "preflight-$timestamp.json"
}

$jsonResults = $results | ForEach-Object {
    [ordered]@{
        name     = $_.Name
        status   = $_.Status
        required = $_.Required
        message  = $_.Message
        duration = $_.Duration
    }
}

try {
    $jsonOutput = $jsonResults | ConvertTo-Json -Depth 5
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($OutputPath, $jsonOutput, $utf8NoBom)
    Write-InstallLog "Pre-flight results saved to: $OutputPath" -Source "Test-Prerequisites"
} catch {
    Write-InstallLog "Failed to save results JSON: $($_.Exception.Message)" -Level 'WARN' -Source "Test-Prerequisites"
}

# Export the full install summary via the logging module
Export-InstallSummary | Out-Null

# ---------------------------------------------------------------------------
# Exit code
# ---------------------------------------------------------------------------
$requiredFailures = $results | Where-Object { $_.Status -eq 'Fail' -and $_.Required -eq $true }
if ($requiredFailures.Count -gt 0) {
    Write-InstallLog "Pre-flight FAILED - $($requiredFailures.Count) required check(s) did not pass" -Level 'ERROR' -Source "Test-Prerequisites"
    exit 1
} else {
    Write-InstallLog "Pre-flight PASSED - system is ready for installation" -Source "Test-Prerequisites"
    exit 0
}
