# Cloudflare Tunnel Health Monitor
# Checks tunnel connectivity and restarts if needed
# 
# Usage:
#   .\scripts\cloudflared-watchdog.ps1              - Check and restart if needed
#   .\scripts\cloudflared-watchdog.ps1 -Status      - Show current status

param(
    [switch]$Status
)

$ErrorActionPreference = "SilentlyContinue"

# Configuration
$logDir = "$env:USERPROFILE\.cloudflared\logs"
$logFile = "$logDir\watchdog.log"
$serviceName = "cloudflared"
$checkUrl = "https://recallhub.app"
$configPath = "$env:USERPROFILE\.cloudflared\config.yml"

# Ensure log directory exists
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logEntry = "[$timestamp] [$Level] $Message"
    Add-Content -Path $logFile -Value $logEntry
    Write-Host $logEntry
}

function Test-TunnelConnectivity {
    try {
        $response = Invoke-WebRequest -Uri $checkUrl -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Test-ProcessRunning {
    $proc = Get-Process cloudflared -ErrorAction SilentlyContinue
    return $null -ne $proc
}

function Start-TunnelProcess {
    Write-Log "Starting cloudflared process..." "WARN"
    
    # Stop any existing processes
    Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    
    if (Test-Path $configPath) {
        Start-Process -FilePath "cloudflared" -ArgumentList "tunnel", "--config", $configPath, "run" -WindowStyle Hidden
        Start-Sleep -Seconds 5
        
        if (Test-ProcessRunning) {
            Write-Log "Tunnel process started successfully" "OK"
            return $true
        }
    }
    
    Write-Log "Failed to start tunnel" "ERROR"
    return $false
}

function Show-Status {
    Write-Host "`n=== Cloudflare Tunnel Status ===" -ForegroundColor Cyan
    
    # Process status
    $proc = Get-Process cloudflared -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "Process: Running (PID: $($proc.Id))" -ForegroundColor Green
    }
    else {
        Write-Host "Process: Not running" -ForegroundColor Red
    }
    
    # Service status
    $service = Get-Service $serviceName -ErrorAction SilentlyContinue
    if ($service) {
        $color = if ($service.Status -eq 'Running') { 'Green' } else { 'Yellow' }
        Write-Host "Service: $($service.Status)" -ForegroundColor $color
    }
    
    # Connectivity
    Write-Host "`nTesting connectivity to $checkUrl..." -NoNewline
    if (Test-TunnelConnectivity) {
        Write-Host " OK" -ForegroundColor Green
    }
    else {
        Write-Host " FAILED" -ForegroundColor Red
    }
    
    # Recent log entries
    Write-Host "`nRecent log entries:" -ForegroundColor Cyan
    if (Test-Path $logFile) {
        Get-Content $logFile -Tail 5
    }
    else {
        Write-Host "  No log file found"
    }
    Write-Host ""
}

# Main execution
if ($Status) {
    Show-Status
    exit 0
}

# Default: Run health check
Write-Log "Starting health check..."

$connected = Test-TunnelConnectivity

if ($connected) {
    Write-Log "Tunnel is healthy" "OK"
    exit 0
}

# Not connected - fix it
Write-Log "Tunnel not reachable - attempting restart" "WARN"

$success = Start-TunnelProcess

if ($success) {
    Start-Sleep -Seconds 3
    if (Test-TunnelConnectivity) {
        Write-Log "Recovery successful" "OK"
        exit 0
    }
}

Write-Log "Recovery failed" "ERROR"
exit 1

