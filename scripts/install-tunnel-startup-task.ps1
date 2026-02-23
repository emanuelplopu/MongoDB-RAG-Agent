#Requires -RunAsAdministrator
# Install Cloudflare Tunnel as Windows Startup Task
# This creates a Task Scheduler task that runs the tunnel at startup
#
# Run as Administrator: Right-click PowerShell -> Run as Administrator
#                       cd d:\dev\repos\MongoDB-RAG-Agent
#                       .\scripts\install-tunnel-startup-task.ps1

$ErrorActionPreference = "Stop"

function Write-Header { param([string]$Msg) Write-Host "`n=== $Msg ===" -ForegroundColor Cyan }
function Write-Success { param([string]$Msg) Write-Host "[OK] $Msg" -ForegroundColor Green }
function Write-Info { param([string]$Msg) Write-Host "[INFO] $Msg" -ForegroundColor Yellow }
function Write-ErrorMsg { param([string]$Msg) Write-Host "[ERROR] $Msg" -ForegroundColor Red }

Write-Header "Cloudflare Tunnel Startup Task Installation"

# Check admin rights
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-ErrorMsg "This script must be run as Administrator!"
    Write-Info "Right-click PowerShell and select 'Run as Administrator'"
    exit 1
}
Write-Success "Running with Administrator privileges"

# Configuration
$taskName = "CloudflareTunnel-RecallHub"
$projectRoot = Split-Path -Parent $PSScriptRoot
$setupScript = Join-Path $projectRoot "setup-cloudflare-tunnel.ps1"
$configDir = "$env:USERPROFILE\.cloudflared"
$configFile = "$configDir\config.yml"
$envFile = Join-Path $projectRoot ".env"

Write-Header "Step 1: Verifying Configuration"

# Check if setup script exists
if (-not (Test-Path $setupScript)) {
    Write-ErrorMsg "Setup script not found: $setupScript"
    exit 1
}
Write-Success "Setup script found"

# Check if config exists
if (-not (Test-Path $configFile)) {
    Write-ErrorMsg "Config file not found: $configFile"
    Write-Info "Run .\setup-cloudflare-tunnel.ps1 first to create the tunnel"
    exit 1
}
Write-Success "Tunnel configuration found"

# Check if .env file exists and has API token
if (Test-Path $envFile) {
    $envContent = Get-Content $envFile -Raw
    if ($envContent -match "CLOUDFLARE_API_TOKEN=(.+)") {
        Write-Success "API token found in .env file"
    } else {
        Write-ErrorMsg "CLOUDFLARE_API_TOKEN not found in .env file"
        Write-Info "Run .\setup-cloudflare-tunnel.ps1 -SetupToken to configure the token"
        exit 1
    }
} else {
    Write-ErrorMsg ".env file not found: $envFile"
    exit 1
}

Write-Header "Step 2: Creating Startup Task"

# Remove existing task if it exists
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Info "Removing existing task..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# Create the task action (run the tunnel)
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$setupScript`" -Run" `
    -WorkingDirectory $projectRoot

# Create the trigger (at startup, with 30 second delay to ensure network is ready)
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT30S"  # 30 second delay

# Create settings
$settings = New-ScheduledTaskSettings `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 3 `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

# Create principal (run as current user to access user profile .env file)
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType S4U `
    -RunLevel Highest

# Register the task
$task = Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Cloudflare Tunnel for RecallHub - Automatically starts at system startup"

Write-Success "Task created: $taskName"

Write-Header "Step 3: Starting Task"

# Start the task immediately to verify it works
Write-Info "Starting task to verify configuration..."
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 5

# Check if task is running
$taskInfo = Get-ScheduledTask -TaskName $taskName
$taskState = $taskInfo.State

if ($taskState -eq "Running") {
    Write-Success "Task is running"
} else {
    Write-Info "Task state: $taskState"
}

Write-Header "Step 4: Verifying Connectivity"

Write-Info "Waiting for tunnel to establish connection..."
Start-Sleep -Seconds 10

# Test frontend
$frontendOk = $false
try {
    $response = Invoke-WebRequest -Uri "https://recallhub.app" -TimeoutSec 15 -UseBasicParsing
    Write-Success "Frontend accessible: HTTP $($response.StatusCode)"
    $frontendOk = $true
} catch {
    Write-Info "Frontend check: $($_.Exception.Message)"
}

# Test API
$apiOk = $false
try {
    $response = Invoke-WebRequest -Uri "https://api.recallhub.app/health" -TimeoutSec 15 -UseBasicParsing
    Write-Success "API accessible: HTTP $($response.StatusCode)"
    $apiOk = $true
} catch {
    Write-Info "API check (may fail if backend not running): $($_.Exception.Message)"
}

Write-Header "Final Status"

Write-Host "Task Name:     $taskName"
Write-Host "Task State:    $taskState" -ForegroundColor $(if ($taskState -eq 'Running') { 'Green' } else { 'Yellow' })
Write-Host "Trigger:       At system startup (30 second delay)"
Write-Host "Run As:        $env:USERNAME (with admin rights)"
Write-Host ""
Write-Host "Configuration:"
Write-Host "  Script:      $setupScript"
Write-Host "  Config:      $configFile"
Write-Host "  Environment: $envFile"
Write-Host ""

if ($frontendOk) {
    Write-Success "Tunnel is working! It will auto-start after Windows restarts."
    Write-Host ""
    Write-Host "Endpoints:" -ForegroundColor Cyan
    Write-Host "  https://recallhub.app     (Frontend)"
    Write-Host "  https://www.recallhub.app (Frontend)"
    Write-Host "  https://api.recallhub.app (Backend API)"
    Write-Host ""
    Write-Host "Management Commands:" -ForegroundColor Cyan
    Write-Host "  View task:       " -NoNewline; Write-Host "Get-ScheduledTask -TaskName '$taskName'" -ForegroundColor White
    Write-Host "  Start manually:  " -NoNewline; Write-Host "Start-ScheduledTask -TaskName '$taskName'" -ForegroundColor White
    Write-Host "  Stop task:       " -NoNewline; Write-Host "Stop-ScheduledTask -TaskName '$taskName'" -ForegroundColor White
    Write-Host "  Remove task:     " -NoNewline; Write-Host "Unregister-ScheduledTask -TaskName '$taskName'" -ForegroundColor White
    Write-Host "  View history:    " -NoNewline; Write-Host "Get-ScheduledTaskInfo -TaskName '$taskName'" -ForegroundColor White
} else {
    Write-ErrorMsg "Tunnel is not working correctly. Check the configuration."
    Write-Info "You can check the task history in Task Scheduler for more details."
    Write-Host ""
    Write-Host "To view task history:" -ForegroundColor Cyan
    Write-Host "  1. Open Task Scheduler (taskschd.msc)"
    Write-Host "  2. Find '$taskName' in Task Scheduler Library"
    Write-Host "  3. Check the 'History' tab for errors"
}

Write-Host ""
