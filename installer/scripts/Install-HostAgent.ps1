<#
.SYNOPSIS
    Installs (or updates) the RecallHub host agent that records host telemetry into MongoDB.
.DESCRIPTION
    The host agent (installer/host-agent, docs/12-TELEMETRY_SYSTEM.md) samples the Windows host
    every few seconds - CPU, RAM, drives, disk/network I/O, CPU package power (RAPL), GPU via
    nvidia-smi, per-container use via the Docker Engine API, Ollama models and backend latency -
    and writes it to the time-series collection recallhub_telemetry.host_samples. The machine-stats
    widget and System > Telemetry History read it.

    This script copies the published agent to -InstallDir, writes its settings to
    %ProgramData%\RecallHub\HostAgent\appsettings.json, registers it and starts it:
      * -Mode Service (default): Windows service "RecallHubHostAgent" (LocalSystem, automatic start,
        restarts on failure). Runs without anyone logged in.
      * -Mode Task: a scheduled task at logon of the current user with highest privileges. Use it
        when the service cannot reach Docker Desktop's engine pipe as LocalSystem (the script checks).

    CPU temperatures need -EnableHardwareSensors and the signed PawnIO driver, which -InstallPawnIO
    installs with winget (namazso.PawnIO) or from -PawnIOInstaller (offline). The driver is a kernel
    component: install it deliberately. Requires administrator privileges (self-elevates). Re-running
    the script updates an existing installation in place.
.PARAMETER SourceDir
    Folder with RecallHubHostAgent.exe (installer output\host-agent, or the repo's publish folder).
.PARAMETER Tenant
    recallhub, quellex or both: which backend /health endpoints the agent times (read from the
    tray config when omitted).
.PARAMETER EnableHardwareSensors
    Read CPU/board/storage sensors with LibreHardwareMonitor (CPU temperature needs PawnIO).
.PARAMETER InstallPawnIO
    Install the PawnIO driver (implies -EnableHardwareSensors).
.EXAMPLE
    .\Install-HostAgent.ps1
.EXAMPLE
    .\Install-HostAgent.ps1 -EnableHardwareSensors -InstallPawnIO
.EXAMPLE
    .\Install-HostAgent.ps1 -Mode Task
#>
param(
    [string]$SourceDir = "",
    [string]$InstallDir = (Join-Path $env:ProgramFiles "RecallHub\HostAgent"),
    [string]$ConfigDir = (Join-Path $env:ProgramData "RecallHub\HostAgent"),
    [ValidateSet("Service", "Task")][string]$Mode = "Service",
    [ValidateSet("", "recallhub", "quellex", "both")][string]$Tenant = "",
    [string]$MongoUri = "mongodb://127.0.0.1:11017/?directConnection=true",
    [string]$Database = "recallhub_telemetry",
    [ValidateRange(1, 300)][int]$IntervalSeconds = 5,
    [string]$OllamaUrl = "http://127.0.0.1:11434",
    [switch]$EnableHardwareSensors,
    [switch]$InstallPawnIO,
    [string]$PawnIOInstaller = "",
    [switch]$NoElevate
)

$ErrorActionPreference = "Stop"
$ServiceName = "RecallHubHostAgent"
$TaskName = "RecallHub Host Agent"
$ExeName = "RecallHubHostAgent.exe"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Ok([string]$Message) { Write-Host "  [OK] $Message" -ForegroundColor Green }
function Write-Info([string]$Message) { Write-Host "  [INFO] $Message" -ForegroundColor Yellow }
function Write-Fail([string]$Message) { Write-Host "  [ERROR] $Message" -ForegroundColor Red }

if (-not (Test-Administrator)) {
    if ($NoElevate) {
        Write-Fail "Administrator privileges are required to install a Windows service."
        exit 1
    }
    Write-Host "  Administrator privileges are required - requesting elevation..." -ForegroundColor Yellow
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$($MyInvocation.MyCommand.Path)`"", "-NoElevate")
    foreach ($kv in $PSBoundParameters.GetEnumerator()) {
        if ($kv.Value -is [switch]) { if ($kv.Value) { $argList += "-$($kv.Key)" } }
        else { $argList += @("-$($kv.Key)", "`"$($kv.Value)`"") }
    }
    $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs -Wait -PassThru
    exit $proc.ExitCode
}

Write-Host ""
Write-Host "  RecallHub host agent" -ForegroundColor Cyan

# --- Locate the published agent --------------------------------------------------
if (-not $SourceDir) {
    $candidates = @(
        (Join-Path $PSScriptRoot "..\host-agent"),                                         # installer output layout
        (Join-Path $PSScriptRoot "..\output\host-agent"),                                  # installer\scripts in the repo after build.ps1
        (Join-Path $PSScriptRoot "..\host-agent\bin\Release\net10.0-windows\win-x64\publish") # dotnet publish in the repo
    )
    $SourceDir = $candidates | Where-Object { Test-Path (Join-Path $_ $ExeName) } | Select-Object -First 1
}
if (-not $SourceDir -or -not (Test-Path (Join-Path $SourceDir $ExeName))) {
    Write-Fail "$ExeName not found. Build it (installer\build.ps1, or 'dotnet publish -c Release' in installer\host-agent) or pass -SourceDir."
    exit 1
}
$SourceDir = (Resolve-Path $SourceDir).Path
Write-Ok "Agent found in $SourceDir"

# --- Tenant -> backend health endpoints ------------------------------------------------
if (-not $Tenant) {
    $trayConfig = Join-Path $env:LOCALAPPDATA "RecallHub\config.json"
    $Tenant = "quellex"
    if (Test-Path $trayConfig) {
        try {
            $cfg = Get-Content $trayConfig -Raw | ConvertFrom-Json
            if ($cfg.app -and $cfg.app.tenant) { $Tenant = [string]$cfg.app.tenant }
        } catch { }
    }
}
$health = @()
if ($Tenant -in @("quellex", "both")) { $health += @{ Name = "backend-quellex"; Url = "http://127.0.0.1:11001/health" } }
if ($Tenant -in @("recallhub", "both")) { $health += @{ Name = "backend-recallhub"; Url = "http://127.0.0.1:11000/health" } }

# --- PawnIO (optional kernel driver for CPU temperatures) -------------------------
if ($InstallPawnIO) {
    $EnableHardwareSensors = $true
    $present = Get-CimInstance Win32_SystemDriver -Filter "Name LIKE 'PawnIO%'" -ErrorAction SilentlyContinue
    if ($present) {
        Write-Ok "PawnIO driver already installed"
    } elseif ($PawnIOInstaller) {
        if (-not (Test-Path $PawnIOInstaller)) { Write-Fail "PawnIO installer not found: $PawnIOInstaller"; exit 1 }
        Write-Info "Running the PawnIO installer (follow its prompts)..."
        Start-Process -FilePath $PawnIOInstaller -Wait
    } elseif (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "Installing PawnIO with winget (namazso.PawnIO)..."
        & winget install --id namazso.PawnIO -e --silent --accept-source-agreements --accept-package-agreements
    } else {
        Write-Fail "winget is not available: download PawnIO from https://pawnio.eu and pass -PawnIOInstaller <path>."
        exit 1
    }
    if (Get-CimInstance Win32_SystemDriver -Filter "Name LIKE 'PawnIO%'" -ErrorAction SilentlyContinue) {
        Write-Ok "PawnIO driver present"
    } else {
        Write-Info "PawnIO driver not detected yet; CPU temperatures stay unavailable until it is installed (a reboot may be needed)."
    }
}

# --- Stop a running installation (update in place) --------------------------------
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service -and $service.Status -ne "Stopped") {
    Stop-Service -Name $ServiceName -Force
    (Get-Service -Name $ServiceName).WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
    Write-Ok "Stopped the running service"
}
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}
Get-Process -Name "RecallHubHostAgent" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# --- Files and settings ---------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Path (Join-Path $SourceDir "*") -Destination $InstallDir -Recurse -Force
Write-Ok "Copied agent to $InstallDir"

New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
$settings = [ordered]@{
    Agent = [ordered]@{
        IntervalSeconds = $IntervalSeconds
        MongoUri = $MongoUri
        Database = $Database
        OllamaUrl = $OllamaUrl
        Health = $health
        Docker = [ordered]@{ Enabled = $true; ProjectLabel = "recallhub" }
        Nvidia = [ordered]@{ Enabled = $true }
        HardwareSensors = [ordered]@{ Enabled = [bool]$EnableHardwareSensors }
    }
}
$configPath = Join-Path $ConfigDir "appsettings.json"
$settings | ConvertTo-Json -Depth 6 | Out-File -FilePath $configPath -Encoding utf8
Write-Ok "Wrote $configPath (tenant=$Tenant, sensors=$([bool]$EnableHardwareSensors))"

$exePath = Join-Path $InstallDir $ExeName
$started = Get-Date

# --- Register ---------------------------------------------------------------------------
if ($Mode -eq "Service") {
    if ($task) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; Write-Ok "Removed the logon task (switching to the service)" }
    if (-not $service) {
        New-Service -Name $ServiceName -BinaryPathName "`"$exePath`"" -DisplayName "RecallHub Host Agent" `
            -Description "Samples host, GPU, container and sensor telemetry into MongoDB for RecallHub/Quellex." `
            -StartupType Automatic | Out-Null
        Write-Ok "Registered service $ServiceName"
    }
    & sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/30000/restart/60000 | Out-Null
    & sc.exe config $ServiceName start= delayed-auto | Out-Null
    Start-Service -Name $ServiceName
    Write-Ok "Service started (delayed automatic start, restarts on failure)"
} else {
    if ($service) { & sc.exe delete $ServiceName | Out-Null; Write-Ok "Removed the service (switching to the logon task)" }
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $action = New-ScheduledTaskAction -Execute $exePath
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    $taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $taskSettings -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Ok "Logon task '$TaskName' registered for $user and started"
}

# --- Health check from the agent's own log --------------------------------------------
if ($Mode -eq "Service") {
    Write-Host "  Waiting for the first samples..." -ForegroundColor Gray
    Start-Sleep -Seconds 15
    $events = @(Get-WinEvent -FilterHashtable @{ LogName = "Application"; ProviderName = $ServiceName; StartTime = $started } -ErrorAction SilentlyContinue)
    $warnings = $events | Where-Object { $_.LevelDisplayName -in @("Warning", "Error") }
    foreach ($e in $events | Sort-Object TimeCreated) {
        $text = ($e.Message -split "`n")[0]
        if ($e.LevelDisplayName -in @("Warning", "Error")) { Write-Info $text } else { Write-Host "    $text" -ForegroundColor Gray }
    }
    if ($warnings | Where-Object { $_.Message -like "*Docker engine pipe*" }) {
        Write-Info "The service cannot reach Docker Desktop as LocalSystem: per-container figures stay empty. Re-run with -Mode Task."
    }
    if ((Get-Service -Name $ServiceName).Status -ne "Running") {
        Write-Fail "The service is not running - check the Application event log (source $ServiceName)."
        exit 1
    }
}
Write-Host ""
Write-Ok "Host agent installed. Check the machine-stats widget (badge 'Host') or System > Telemetry History."
Write-Host "  Print one sample without writing:  & `"$exePath`" --once" -ForegroundColor Gray
exit 0
