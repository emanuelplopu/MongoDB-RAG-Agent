# Cloudflare Tunnel - Scheduled Task Setup (replaces broken Windows service)
# Run as Administrator in PowerShell
#
# The cloudflared Windows service handler has a known issue causing error 1067.
# This script replaces it with a scheduled task that runs on startup as SYSTEM.

$ErrorActionPreference = "Stop"
$taskName = "CloudflareTunnel"
$configPath = "C:\Windows\System32\config\systemprofile\.cloudflared\config.yml"
$exePath = "C:\Program Files (x86)\cloudflared\cloudflared.exe"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Cloudflare Tunnel - Scheduled Task Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# --- Step 1: Uninstall broken service ---
Write-Host "`n[1/6] Removing cloudflared Windows service..." -ForegroundColor Yellow
try {
    $svc = Get-Service cloudflared -ErrorAction SilentlyContinue
    if ($svc) {
        if ($svc.Status -ne 'Stopped') {
            net stop cloudflared 2>$null | Out-Null
            Start-Sleep -Seconds 2
        }
        & $exePath service uninstall 2>$null | Out-Null
        Start-Sleep -Seconds 2
        Write-Host "  Service uninstalled." -ForegroundColor Green
    } else {
        Write-Host "  Service not found (already removed)." -ForegroundColor DarkGray
    }
} catch {
    Write-Host "  Warning: $($_.Exception.Message)" -ForegroundColor DarkYellow
}

# --- Step 2: Kill any lingering cloudflared processes ---
Write-Host "`n[2/6] Killing lingering cloudflared processes..." -ForegroundColor Yellow
$procs = Get-Process cloudflared -ErrorAction SilentlyContinue
if ($procs) {
    $procs | Stop-Process -Force
    Start-Sleep -Seconds 2
    Write-Host "  Killed $($procs.Count) process(es)." -ForegroundColor Green
} else {
    Write-Host "  No processes running." -ForegroundColor DarkGray
}

# --- Step 3: Validate config and credentials ---
Write-Host "`n[3/6] Validating config and credentials..." -ForegroundColor Yellow
if (-not (Test-Path $configPath)) {
    Write-Host "  ERROR: Config not found at $configPath" -ForegroundColor Red
    exit 1
}
# Strip BOM if present
$bytes = [System.IO.File]::ReadAllBytes($configPath)
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    $content = [System.IO.File]::ReadAllText($configPath)
    [System.IO.File]::WriteAllText($configPath, $content, [System.Text.UTF8Encoding]::new($false))
    Write-Host "  Stripped UTF-8 BOM from config." -ForegroundColor DarkYellow
}
# Also strip BOM from user config
$userConfig = "$env:USERPROFILE\.cloudflared\config.yml"
if (Test-Path $userConfig) {
    $ub = [System.IO.File]::ReadAllBytes($userConfig)
    if ($ub.Length -ge 3 -and $ub[0] -eq 0xEF -and $ub[1] -eq 0xBB -and $ub[2] -eq 0xBF) {
        $uc = [System.IO.File]::ReadAllText($userConfig)
        [System.IO.File]::WriteAllText($userConfig, $uc, [System.Text.UTF8Encoding]::new($false))
        Write-Host "  Stripped UTF-8 BOM from user config." -ForegroundColor DarkYellow
    }
}
Write-Host "  Config: OK ($($bytes.Length) bytes)" -ForegroundColor Green

# Extract and check credentials-file from config
$credLine = Select-String -Path $configPath -Pattern "^credentials-file:" | Select-Object -First 1
if ($credLine) {
    $credPath = ($credLine.Line -replace "^credentials-file:\s*", "").Trim()
    if (Test-Path $credPath) {
        Write-Host "  Credentials: OK ($(((Get-Item $credPath).Length)) bytes)" -ForegroundColor Green
    } else {
        Write-Host "  ERROR: Credentials file not found: $credPath" -ForegroundColor Red
        exit 1
    }
}

# --- Step 4: Remove existing scheduled task if present ---
Write-Host "`n[4/6] Registering scheduled task '$taskName'..." -ForegroundColor Yellow
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "  Removed existing task." -ForegroundColor DarkGray
}

# --- Step 5: Create and register the scheduled task ---
$action = New-ScheduledTaskAction `
    -Execute $exePath `
    -Argument "tunnel --config `"$configPath`" run"

$trigger = New-ScheduledTaskTrigger -AtStartup

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Cloudflare Tunnel for recallhub.app and quellex.at (replaces broken Windows service)" | Out-Null

Write-Host "  Task registered." -ForegroundColor Green

# --- Step 6: Start the task and validate ---
Write-Host "`n[5/6] Starting tunnel..." -ForegroundColor Yellow
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 8

Write-Host "`n[6/6] Validating tunnel is running..." -ForegroundColor Yellow

# Check process
$proc = Get-Process cloudflared -ErrorAction SilentlyContinue
if (-not $proc) {
    Write-Host "  FAIL: cloudflared process not running!" -ForegroundColor Red
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    Write-Host "  Task last result: $($info.LastTaskResult)" -ForegroundColor Red
    exit 1
}
Write-Host "  Process: PID $($proc.Id), running since $($proc.StartTime)" -ForegroundColor Green

# Check metrics port (proves tunnel initialized)
$listening = netstat -ano | Select-String ":60124.*LISTENING"
if ($listening) {
    Write-Host "  Metrics: listening on port 60124" -ForegroundColor Green
} else {
    Write-Host "  Warning: metrics port 60124 not yet listening" -ForegroundColor DarkYellow
}

# Check task status
$taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
$taskState = (Get-ScheduledTask -TaskName $taskName).State
Write-Host "  Task state: $taskState" -ForegroundColor Green

# Quick connectivity test
Write-Host "`n--- Connectivity Test ---" -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri "https://recallhub.app" -UseBasicParsing -TimeoutSec 10
    Write-Host "  recallhub.app: $($r.StatusCode) OK" -ForegroundColor Green
} catch {
    Write-Host "  recallhub.app: FAILED ($($_.Exception.Message))" -ForegroundColor Red
}
try {
    $r = Invoke-WebRequest -Uri "https://test-0-8-7.quellex.at" -UseBasicParsing -TimeoutSec 10
    Write-Host "  test-0-8-7.quellex.at: $($r.StatusCode) OK" -ForegroundColor Green
} catch {
    Write-Host "  test-0-8-7.quellex.at: FAILED ($($_.Exception.Message))" -ForegroundColor Red
}

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host " Setup complete! Tunnel will auto-start on boot." -ForegroundColor Cyan
Write-Host " Task name: $taskName" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
