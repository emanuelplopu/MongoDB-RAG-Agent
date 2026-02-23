# Direct Task Creation using schtasks.exe (works without elevation in some cases)
# This uses the built-in schtasks command instead of PowerShell cmdlets

$ErrorActionPreference = "Continue"

$taskName = "CloudflareTunnel-RecallHub"
$projectRoot = "d:\dev\repos\MongoDB-RAG-Agent"
$setupScript = "$projectRoot\setup-cloudflare-tunnel.ps1"

Write-Host "Creating scheduled task: $taskName" -ForegroundColor Cyan
Write-Host "This requires Administrator privileges..." -ForegroundColor Yellow
Write-Host ""

# Check if running as admin
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: This script must be run as Administrator!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please:" -ForegroundColor Yellow
    Write-Host "1. Right-click PowerShell" -ForegroundColor White
    Write-Host "2. Select 'Run as Administrator'" -ForegroundColor White
    Write-Host "3. Run this script again" -ForegroundColor White
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "[OK] Running with Administrator privileges" -ForegroundColor Green
Write-Host ""

# Delete existing task if it exists
Write-Host "Removing existing task (if any)..." -ForegroundColor Yellow
schtasks /Delete /TN $taskName /F 2>$null | Out-Null

# Create XML for the task
$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Cloudflare Tunnel for RecallHub - Auto-starts at boot</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Delay>PT30S</Delay>
      <Enabled>true</Enabled>
    </BootTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$env:USERNAME</UserId>
      <LogonType>S4U</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>P3D</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$setupScript" -Run</Arguments>
      <WorkingDirectory>$projectRoot</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

# Save XML to temp file
$tempXmlPath = "$env:TEMP\cloudflare-tunnel-task.xml"
$taskXml | Out-File -FilePath $tempXmlPath -Encoding Unicode -Force

Write-Host "Creating task with schtasks..." -ForegroundColor Cyan

# Create the task using schtasks
$result = schtasks /Create /TN $taskName /XML $tempXmlPath /F 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Task created successfully!" -ForegroundColor Green
    
    # Clean up temp file
    Remove-Item $tempXmlPath -Force -ErrorAction SilentlyContinue
    
    Write-Host ""
    Write-Host "Starting task..." -ForegroundColor Cyan
    schtasks /Run /TN $taskName | Out-Null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] Task started" -ForegroundColor Green
        Write-Host ""
        Write-Host "Waiting for tunnel to establish connection..." -ForegroundColor Yellow
        Start-Sleep -Seconds 15
        
        # Test connectivity
        Write-Host ""
        Write-Host "Testing connectivity..." -ForegroundColor Cyan
        try {
            $response = Invoke-WebRequest -Uri "https://recallhub.app" -TimeoutSec 15 -UseBasicParsing
            Write-Host ""
            Write-Host "SUCCESS!" -ForegroundColor Green -BackgroundColor Black
            Write-Host "Tunnel is working! HTTP $($response.StatusCode)" -ForegroundColor Green
            Write-Host ""
            Write-Host "The tunnel will now auto-start after Windows restarts." -ForegroundColor Cyan
            Write-Host ""
            Write-Host "Endpoints:" -ForegroundColor Cyan
            Write-Host "  https://recallhub.app" -ForegroundColor White
            Write-Host "  https://www.recallhub.app" -ForegroundColor White
            Write-Host "  https://api.recallhub.app" -ForegroundColor White
        } catch {
            Write-Host ""
            Write-Host "WARNING: Could not reach tunnel yet" -ForegroundColor Yellow
            Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Gray
            Write-Host ""
            Write-Host "Wait a few more seconds and try:" -ForegroundColor Yellow
            Write-Host "  Invoke-WebRequest -Uri 'https://recallhub.app'" -ForegroundColor White
        }
        
        Write-Host ""
        Write-Host "Management Commands:" -ForegroundColor Cyan
        Write-Host "  View task:   " -NoNewline
        Write-Host "schtasks /Query /TN $taskName /V /FO LIST" -ForegroundColor White
        Write-Host "  Start task:  " -NoNewline
        Write-Host "schtasks /Run /TN $taskName" -ForegroundColor White
        Write-Host "  Stop task:   " -NoNewline
        Write-Host "schtasks /End /TN $taskName" -ForegroundColor White
        Write-Host "  Delete task: " -NoNewline
        Write-Host "schtasks /Delete /TN $taskName" -ForegroundColor White
    } else {
        Write-Host "[ERROR] Failed to start task" -ForegroundColor Red
        Write-Host "Result: $result" -ForegroundColor Gray
    }
} else {
    Write-Host "[ERROR] Failed to create task" -ForegroundColor Red
    Write-Host "Result: $result" -ForegroundColor Gray
    Remove-Item $tempXmlPath -Force -ErrorAction SilentlyContinue
}

Write-Host ""
