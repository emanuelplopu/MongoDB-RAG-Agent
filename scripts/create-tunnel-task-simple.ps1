# Simple Task Creation Script (Run in current admin PowerShell)
# This version creates the task without #Requires to avoid execution issues

$ErrorActionPreference = "Stop"

$taskName = "CloudflareTunnel-RecallHub"
$projectRoot = "d:\dev\repos\MongoDB-RAG-Agent"
$setupScript = "$projectRoot\setup-cloudflare-tunnel.ps1"

Write-Host "Creating scheduled task: $taskName" -ForegroundColor Cyan

# Remove existing task if exists
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# Create action
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$setupScript`" -Run" `
    -WorkingDirectory $projectRoot

# Create trigger (startup with 30 sec delay)
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT30S"

# Create settings
$settings = New-ScheduledTaskSettings `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 3 `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

# Create principal
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType S4U `
    -RunLevel Highest

# Register task
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Cloudflare Tunnel for RecallHub - Auto-starts at boot" | Out-Null

Write-Host "Task created successfully!" -ForegroundColor Green

# Start it now
Write-Host "Starting task..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 5

# Check status
$task = Get-ScheduledTask -TaskName $taskName
Write-Host "Task state: $($task.State)" -ForegroundColor $(if ($task.State -eq 'Running') { 'Green' } else { 'Yellow' })

# Test connectivity
Write-Host "`nTesting connectivity..." -ForegroundColor Cyan
Start-Sleep -Seconds 10

try {
    $response = Invoke-WebRequest -Uri "https://recallhub.app" -TimeoutSec 15 -UseBasicParsing
    Write-Host "SUCCESS: Tunnel is working! (HTTP $($response.StatusCode))" -ForegroundColor Green
    Write-Host "`nThe tunnel will now auto-start after Windows restarts." -ForegroundColor Cyan
} catch {
    Write-Host "WARNING: Could not reach tunnel yet: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "Wait a few more seconds and try: Invoke-WebRequest -Uri 'https://recallhub.app'" -ForegroundColor Gray
}
