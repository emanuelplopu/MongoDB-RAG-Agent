#Requires -Version 5.1
<#
.SYNOPSIS
    RecallHub diagnostic report generator.
.DESCRIPTION
    Collects comprehensive system state, service status, container logs, and configuration
    for troubleshooting installation or runtime issues. Can be run independently from
    command line or invoked from the tray app.
.PARAMETER OutputPath
    Path for the JSON diagnostics file. Defaults to LOCALAPPDATA\RecallHub\logs\diagnostics-TIMESTAMP.json
.PARAMETER Brief
    Only output console summary, skip JSON file generation
#>
param(
    [string]$OutputPath,
    [switch]$Brief,
    [string]$ConfigPath
)

$ErrorActionPreference = 'Continue'

# ---------------------------------------------------------------------------
# Import logging module and initialize
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Logging.psm1") -Force

$logFile = Initialize-InstallLog -LogName "diagnostics"
Write-InstallLog "Starting diagnostic collection" -Source "Export-Diagnostics"

# ---------------------------------------------------------------------------
# Load configuration
# ---------------------------------------------------------------------------
$InstallerDir = Split-Path $ScriptDir -Parent
if (-not $ConfigPath) { $ConfigPath = Join-Path $InstallerDir "config.json" }
$Config = if (Test-Path $ConfigPath) { Get-Content $ConfigPath -Raw | ConvertFrom-Json } else { $null }

# Derive container/port names from config
$DiagContainerNames = if ($Config) { 
    @($Config.containers.mongodb, $Config.containers.backend, $Config.containers.frontend, 
      $Config.containers.ollama, $Config.containers.worker)
} else {
    @("recallhub-mongodb", "recallhub-backend", "recallhub-frontend", "recallhub-ollama", "recallhub-worker")
}

$DiagExpectedPorts = if ($Config) {
    @(
        @{ port = $Config.ports.backend; service = "Backend (RecallHub)" }
        @{ port = $Config.ports.frontend; service = "Frontend (RecallHub)" }
        @{ port = $Config.ports.ollama; service = "Ollama" }
    )
} else {
    @(
        @{ port = 11000; service = "Backend" }
        @{ port = 11080; service = "Frontend" }
        @{ port = 11434; service = "Ollama" }
    )
}

$DiagRequiredModels = if ($Config) {
    @($Config.models.ollama | Where-Object { $_.required } | ForEach-Object { $_.name })
} else {
    @("llama3.2:3b", "nomic-embed-text")
}

$WslDistroName = if ($Config) { $Config.wsl.distroName } else { "RecallHub" }

# ---------------------------------------------------------------------------
# Diagnostic collection functions
# ---------------------------------------------------------------------------

function Get-SystemInfo {
    Start-InstallSection -Name "System Info"

    $info = @{
        computerName      = $env:COMPUTERNAME
        windowsVersion    = [System.Environment]::OSVersion.VersionString
        windowsBuild      = [System.Environment]::OSVersion.Version.Build
        powerShellVersion = $PSVersionTable.PSVersion.ToString()
        timestamp         = (Get-Date).ToString("o")
    }

    try {
        $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
        if ($cs) {
            $info.totalRAM_GB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 2)
        }
    } catch {
        $info.totalRAM_GB = -1
    }

    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
        if ($os) {
            $info.availableRAM_GB = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
        }
    } catch {
        $info.availableRAM_GB = -1
    }

    try {
        $info.processors = @(Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue |
            Select-Object Name, NumberOfCores, NumberOfLogicalProcessors)
    } catch {
        $info.processors = @()
    }

    # Disk space for all fixed drives
    $info.drives = @()
    try {
        Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" -ErrorAction SilentlyContinue | ForEach-Object {
            $info.drives += @{
                letter      = $_.DeviceID
                totalGB     = [math]::Round($_.Size / 1GB, 2)
                freeGB      = [math]::Round($_.FreeSpace / 1GB, 2)
                percentFree = [math]::Round(($_.FreeSpace / $_.Size) * 100, 1)
            }
        }
    } catch {
        $info.drives = @(@{ error = $_.Exception.Message })
    }

    Complete-InstallSection -Name "System Info" -Status Success
    return $info
}

function Get-WSLStatus {
    Start-InstallSection -Name "WSL Status"

    $wsl = @{ installed = $false; distros = $null; version = $null; recallhubExists = $false; recallhubRunning = $false }

    try {
        $wslVersion = wsl --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $wsl.installed = $true
            $wsl.version = ($wslVersion | Out-String).Trim()
        }

        $distros = wsl -l -v 2>&1
        if ($LASTEXITCODE -eq 0) {
            $wsl.distros = ($distros | Out-String).Trim()
        }

        # Check if RecallHub distro exists
        $wsl.recallhubExists = ($null -ne ($distros | Where-Object { $_ -match $WslDistroName }))

        # Get RecallHub distro status if exists
        if ($wsl.recallhubExists) {
            $status = wsl -d $WslDistroName -- echo "alive" 2>&1
            $wsl.recallhubRunning = ($LASTEXITCODE -eq 0)
        }
    } catch {
        $wsl.error = $_.Exception.Message
    }

    Complete-InstallSection -Name "WSL Status" -Status $(if ($wsl.installed) { "Success" } else { "Failed" })
    return $wsl
}

function Get-DockerStatus {
    Start-InstallSection -Name "Docker Status"

    $docker = @{ running = $false; containers = @(); images = @(); version = $null }

    try {
        # Check Docker via WSL
        $dockerInfo = wsl -d $WslDistroName -- bash -c "docker info --format '{{json .}}'" 2>&1
        if ($LASTEXITCODE -eq 0) {
            $docker.running = $true
            try { $docker.info = $dockerInfo | ConvertFrom-Json } catch { $docker.info = ($dockerInfo | Out-String).Trim() }
        }

        # Get Docker version
        $versionOutput = wsl -d $WslDistroName -- bash -c "docker version --format '{{.Server.Version}}'" 2>&1
        if ($LASTEXITCODE -eq 0) {
            $docker.version = ($versionOutput | Out-String).Trim()
        }

        # List all containers with status
        $containers = wsl -d $WslDistroName -- bash -c "docker ps -a --format '{{json .}}'" 2>&1
        if ($LASTEXITCODE -eq 0) {
            $docker.containers = @()
            foreach ($line in $containers) {
                if ($line -and $line.Trim()) {
                    try { $docker.containers += ($line | ConvertFrom-Json) } catch { $docker.containers += $line }
                }
            }
        }

        # List images
        $images = wsl -d $WslDistroName -- bash -c "docker images --format '{{json .}}'" 2>&1
        if ($LASTEXITCODE -eq 0) {
            $docker.images = @()
            foreach ($line in $images) {
                if ($line -and $line.Trim()) {
                    try { $docker.images += ($line | ConvertFrom-Json) } catch { $docker.images += $line }
                }
            }
        }
    } catch {
        $docker.error = $_.Exception.Message
    }

    Complete-InstallSection -Name "Docker Status" -Status $(if ($docker.running) { "Success" } else { "Failed" })
    return $docker
}

function Get-ContainerLogs {
    Start-InstallSection -Name "Container Logs"

    $logs = @{}
    $containerNames = $DiagContainerNames

    foreach ($name in $containerNames) {
        try {
            $containerLogs = wsl -d $WslDistroName -- bash -c "docker logs --tail 100 $name 2>&1" 2>&1
            if ($LASTEXITCODE -eq 0) {
                $logLines = @($containerLogs | Select-Object -Last 100)
                $logs[$name] = @{
                    status    = "collected"
                    lines     = $logLines
                    lineCount = ($containerLogs | Measure-Object).Count
                }
            } else {
                $logs[$name] = @{ status = "not_found"; error = "Container not running or doesn't exist" }
            }
        } catch {
            $logs[$name] = @{ status = "error"; error = $_.Exception.Message }
        }
    }

    Complete-InstallSection -Name "Container Logs" -Status Success
    return $logs
}

function Get-OllamaStatus {
    Start-InstallSection -Name "Ollama Status"

    $ollama = @{
        reachable      = $false
        models         = @()
        requiredModels = $DiagRequiredModels
        missingModels  = @()
        allModelsPresent = $false
    }

    try {
        # Try via container
        $ollamaPort = if ($Config) { $Config.ports.ollama } else { 11434 }
        $response = wsl -d $WslDistroName -- bash -c "curl -s http://localhost:${ollamaPort}/api/tags 2>&1" 2>&1
        if ($LASTEXITCODE -eq 0 -and $response) {
            $responseStr = ($response | Out-String).Trim()
            if ($responseStr) {
                $ollama.reachable = $true
                try {
                    $data = $responseStr | ConvertFrom-Json
                    $ollama.models = @($data.models | ForEach-Object {
                        @{ name = $_.name; size = $_.size; modified = $_.modified_at }
                    })
                } catch {
                    $ollama.models = @()
                    $ollama.parseError = $_.Exception.Message
                }
            }
        }

        # Check which required models are present
        $modelNames = @($ollama.models | ForEach-Object { $_.name })
        $ollama.missingModels = @()
        foreach ($req in $ollama.requiredModels) {
            $found = $modelNames | Where-Object { $_ -like "$req*" }
            if (-not $found) { $ollama.missingModels += $req }
        }
        $ollama.allModelsPresent = ($ollama.missingModels.Count -eq 0)
    } catch {
        $ollama.error = $_.Exception.Message
    }

    $ollamaOk = $ollama.reachable -and $ollama.allModelsPresent
    Complete-InstallSection -Name "Ollama Status" -Status $(if ($ollamaOk) { "Success" } else { "Failed" })
    return $ollama
}

function Get-MongoDBStatus {
    Start-InstallSection -Name "MongoDB Status"

    $mongo = @{ reachable = $false }

    try {
        $mongoContainer = if ($Config) { $Config.containers.mongodb } else { "recallhub-mongodb" }
        $pingResult = wsl -d $WslDistroName -- bash -c "docker exec $mongoContainer mongosh --eval 'db.adminCommand({ping:1})' --quiet 2>&1" 2>&1
        if ($LASTEXITCODE -eq 0) {
            $mongo.reachable = $true

            # Get database list
            $dbs = wsl -d $WslDistroName -- bash -c "docker exec $mongoContainer mongosh --eval 'JSON.stringify(db.adminCommand({listDatabases:1}))' --quiet 2>&1" 2>&1
            if ($LASTEXITCODE -eq 0) {
                $dbsStr = ($dbs | Out-String).Trim()
                try { $mongo.databases = $dbsStr | ConvertFrom-Json } catch { $mongo.databases = $dbsStr }
            }

            # Get collection names for main DB
            $collections = wsl -d $WslDistroName -- bash -c "docker exec $mongoContainer mongosh recallhub --eval 'JSON.stringify(db.getCollectionNames())' --quiet 2>&1" 2>&1
            if ($LASTEXITCODE -eq 0) {
                $colStr = ($collections | Out-String).Trim()
                try { $mongo.collections = $colStr | ConvertFrom-Json } catch { $mongo.collections = $colStr }
            }
        }
    } catch {
        $mongo.error = $_.Exception.Message
    }

    Complete-InstallSection -Name "MongoDB Status" -Status $(if ($mongo.reachable) { "Success" } else { "Failed" })
    return $mongo
}

function Get-NetworkStatus {
    Start-InstallSection -Name "Network Status"

    $network = @{ expectedPorts = @() }

    $expectedPorts = $DiagExpectedPorts

    try {
        $listening = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue

        foreach ($expected in $expectedPorts) {
            $match = $listening | Where-Object { $_.LocalPort -eq $expected.port }
            $processName = $null
            if ($match) {
                try {
                    $processName = (Get-Process -Id $match[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
                } catch { }
            }
            $network.expectedPorts += @{
                port      = $expected.port
                service   = $expected.service
                listening = ($null -ne $match)
                process   = $processName
            }
        }
    } catch {
        $network.error = $_.Exception.Message
    }

    Complete-InstallSection -Name "Network Status" -Status Success
    return $network
}

function Get-RelevantEventLogs {
    Start-InstallSection -Name "Event Logs"

    $events = @()

    try {
        # Get recent errors/warnings from System log
        $systemEvents = Get-WinEvent -FilterHashtable @{
            LogName   = 'System'
            StartTime = (Get-Date).AddHours(-24)
            Level     = @(1, 2, 3)  # Critical, Error, Warning
        } -MaxEvents 50 -ErrorAction SilentlyContinue

        foreach ($evt in $systemEvents) {
            # Filter for relevant providers
            if ($evt.ProviderName -match "Hyper-V|WSL|Docker|Microsoft-Windows-Subsystem-Linux|VHDMP|storvsp") {
                $msgLen = if ($evt.Message) { [math]::Min($evt.Message.Length, 500) } else { 0 }
                $msgText = if ($evt.Message -and $msgLen -gt 0) { $evt.Message.Substring(0, $msgLen) } else { "" }
                $events += @{
                    time     = $evt.TimeCreated.ToString("o")
                    level    = $evt.LevelDisplayName
                    provider = $evt.ProviderName
                    id       = $evt.Id
                    message  = $msgText
                }
            }
        }
    } catch {
        $events = @(@{ error = $_.Exception.Message })
    }

    Complete-InstallSection -Name "Event Logs" -Status Success
    return $events
}

function Get-InstallLogFiles {
    Start-InstallSection -Name "Log Files"

    $logDir = Join-Path $env:LOCALAPPDATA "RecallHub\logs"
    $logFiles = @()

    try {
        if (Test-Path $logDir) {
            Get-ChildItem $logDir -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 20 |
                ForEach-Object {
                    $logFiles += @{
                        name     = $_.Name
                        size     = $_.Length
                        modified = $_.LastWriteTime.ToString("o")
                        path     = $_.FullName
                    }
                }
        }
    } catch {
        $logFiles = @(@{ error = $_.Exception.Message })
    }

    Complete-InstallSection -Name "Log Files" -Status Success
    return $logFiles
}

# ---------------------------------------------------------------------------
# Main execution - assemble everything and output
# ---------------------------------------------------------------------------

Write-InstallLog "Collecting system information..." -Source "Export-Diagnostics"

$diagnostics = @{
    reportId      = [guid]::NewGuid().ToString()
    generatedAt   = (Get-Date).ToString("o")
    system        = Get-SystemInfo
    wsl           = Get-WSLStatus
    docker        = Get-DockerStatus
    containerLogs = Get-ContainerLogs
    ollama        = Get-OllamaStatus
    mongodb       = Get-MongoDBStatus
    network       = Get-NetworkStatus
    eventLogs     = Get-RelevantEventLogs
    logFiles      = Get-InstallLogFiles
}

# Determine overall health
$healthIssues = @()
if (-not $diagnostics.wsl.installed) { $healthIssues += "WSL not installed" }
if (-not $diagnostics.docker.running) { $healthIssues += "Docker not running" }
if (-not $diagnostics.mongodb.reachable) { $healthIssues += "MongoDB unreachable" }
if (-not $diagnostics.ollama.reachable) { $healthIssues += "Ollama unreachable" }
if ($diagnostics.ollama.missingModels -and $diagnostics.ollama.missingModels.Count -gt 0) {
    $healthIssues += "Missing models: $($diagnostics.ollama.missingModels -join ', ')"
}

$diagnostics.overallHealth = if ($healthIssues.Count -eq 0) { "healthy" }
    elseif ($healthIssues.Count -le 2) { "degraded" }
    else { "unhealthy" }
$diagnostics.issues = $healthIssues

# Save JSON report
if (-not $Brief) {
    if (-not $OutputPath) {
        $OutputPath = Join-Path $env:LOCALAPPDATA "RecallHub\logs\diagnostics-$(Get-Date -Format 'yyyy-MM-dd-HHmmss').json"
    }
    $outputDir = Split-Path $OutputPath -Parent
    if (-not (Test-Path $outputDir)) { New-Item -Path $outputDir -ItemType Directory -Force | Out-Null }

    $diagnostics | ConvertTo-Json -Depth 10 | Set-Content -Path $OutputPath -Encoding UTF8
    Write-InstallLog "Diagnostic report saved: $OutputPath" -Source "Export-Diagnostics"
}

# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host ([char]0x2554 + ([string][char]0x2550) * 62 + [char]0x2557) -ForegroundColor Cyan
Write-Host ("$([char]0x2551)              RecallHub Diagnostic Report                     $([char]0x2551)") -ForegroundColor Cyan
Write-Host ([char]0x2560 + ([string][char]0x2550) * 62 + [char]0x2563) -ForegroundColor Cyan

# Status helper
function Format-StatusLine {
    param([string]$Label, [bool]$Ok, [string]$Detail = "")
    $icon = if ($Ok) { "[OK]" } else { "[!!]" }
    $color = if ($Ok) { "Green" } else { "Red" }
    $text = "$([char]0x2551)  $icon $Label"
    if ($Detail) { $text += " - $Detail" }
    $padded = $text.PadRight(63) + [char]0x2551
    Write-Host $padded -ForegroundColor $color
}

Format-StatusLine -Label "WSL" -Ok $diagnostics.wsl.installed -Detail $(if ($diagnostics.wsl.recallhubExists) { "RecallHub distro found" } else { "RecallHub distro missing" })
Format-StatusLine -Label "Docker" -Ok $diagnostics.docker.running -Detail $(if ($diagnostics.docker.version) { "v$($diagnostics.docker.version)" } else { "not detected" })
Format-StatusLine -Label "MongoDB" -Ok $diagnostics.mongodb.reachable
Format-StatusLine -Label "Ollama" -Ok $diagnostics.ollama.reachable -Detail $(if ($diagnostics.ollama.allModelsPresent) { "all models present" } else { "missing: $($diagnostics.ollama.missingModels -join ', ')" })

# Container count
$runningContainers = @($diagnostics.docker.containers | Where-Object { $_.State -eq "running" }).Count
$totalContainers = $diagnostics.docker.containers.Count
Format-StatusLine -Label "Containers" -Ok ($runningContainers -gt 0) -Detail "$runningContainers/$totalContainers running"

# Network ports
$listeningCount = @($diagnostics.network.expectedPorts | Where-Object { $_.listening }).Count
$totalPorts = $diagnostics.network.expectedPorts.Count
Format-StatusLine -Label "Ports" -Ok ($listeningCount -gt 0) -Detail "$listeningCount/$totalPorts expected ports listening"

Write-Host ([char]0x2560 + ([string][char]0x2550) * 62 + [char]0x2563) -ForegroundColor Cyan

# Overall health
$healthColor = switch ($diagnostics.overallHealth) {
    "healthy"  { "Green" }
    "degraded" { "Yellow" }
    default    { "Red" }
}
$healthLine = "$([char]0x2551)  Overall: $($diagnostics.overallHealth.ToUpper())"
Write-Host ($healthLine.PadRight(63) + [char]0x2551) -ForegroundColor $healthColor

if ($healthIssues.Count -gt 0) {
    $issueHeader = "$([char]0x2551)  Issues:"
    Write-Host ($issueHeader.PadRight(63) + [char]0x2551) -ForegroundColor Yellow
    foreach ($issue in $healthIssues) {
        $issueLine = "$([char]0x2551)    - $issue"
        Write-Host ($issueLine.PadRight(63) + [char]0x2551) -ForegroundColor Yellow
    }
}

Write-Host ([char]0x255A + ([string][char]0x2550) * 62 + [char]0x255D) -ForegroundColor Cyan

if (-not $Brief) {
    Write-Host ""
    Write-Host "Full report: $OutputPath" -ForegroundColor Gray
}

Export-InstallSummary
Write-InstallLog "Diagnostic collection completed" -Source "Export-Diagnostics"
