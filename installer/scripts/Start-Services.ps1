<#
.SYNOPSIS
    Starts RecallHub services inside WSL2 with retry logic and diagnostics.

.DESCRIPTION
    This script starts all RecallHub services with structured logging,
    exponential-backoff retry, timeout enforcement, and diagnostic capture:
    - Docker daemon
    - MongoDB database
    - Ollama LLM service
    - Backend API
    - Frontend web server

    Uses RecallHub-Logging module for consistent logging, retry with backoff,
    and diagnostic capture. Generates a JSON health report on completion.

.PARAMETER Detached
    Run services in the background (default: true)

.PARAMETER Wait
    Wait for services to be healthy before returning

.PARAMETER Timeout
    Maximum seconds to wait for all services to become healthy (default: 180)
#>

param(
    [switch]$Detached = $true,
    [switch]$Wait,
    [int]$Timeout = 180,
    [string]$ConfigPath
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Import logging module
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Logging.psm1") -Force

$logFile = Initialize-InstallLog -LogName "start-services"
Write-InstallLog "Starting RecallHub services" -Source "Start-Services"
Write-InstallLog "Parameters: Detached=$Detached, Wait=$Wait, Timeout=$Timeout" -Source "Start-Services" -Level DEBUG

# ---------------------------------------------------------------------------
# Load configuration
# ---------------------------------------------------------------------------
$InstallerDir = Split-Path $ScriptDir -Parent
if (-not $ConfigPath) { $ConfigPath = Join-Path $InstallerDir "config.json" }
$Config = if (Test-Path $ConfigPath) { Get-Content $ConfigPath -Raw | ConvertFrom-Json } else { $null }

# Derive service configuration from config
$ContainerNames = if ($Config) { 
    @{
        MongoDB = $Config.containers.mongodb
        Backend = $Config.containers.backend
        Frontend = $Config.containers.frontend
        Ollama = $Config.containers.ollama
    }
} else {
    @{
        MongoDB = "recallhub-mongodb"
        Backend = "recallhub-backend"
        Frontend = "recallhub-frontend"
        Ollama = "recallhub-ollama"
    }
}

$ServicePorts = if ($Config) {
    @{
        MongoDB = $Config.ports.mongodb
        Backend = $Config.ports.backend
        Frontend = $Config.ports.frontend
        Ollama = $Config.ports.ollama
    }
} else {
    @{ MongoDB = 27017; Backend = 11000; Frontend = 11080; Ollama = 11434 }
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
$DISTRO_NAME = if ($Config) { $Config.wsl.distroName } else { "RecallHub" }
$CONFIG_PATH = "/opt/recallhub/config"

$CONTAINER_MAP = @{
    mongodb  = $ContainerNames.MongoDB
    ollama   = $ContainerNames.Ollama
    backend  = $ContainerNames.Backend
    frontend = $ContainerNames.Frontend
}

$SERVICE_PORTS = @{
    mongodb  = $ServicePorts.MongoDB
    ollama   = $ServicePorts.Ollama
    backend  = $ServicePorts.Backend
    frontend = $ServicePorts.Frontend
}

# Per-service health tracking
$script:serviceHealth = [ordered]@{}

# Overall script start time for timeout enforcement
$script:scriptStartTime = Get-Date

# ---------------------------------------------------------------------------
# Helpers: Timeout tracking
# ---------------------------------------------------------------------------
function Test-TimeoutExceeded {
    $elapsed = ((Get-Date) - $script:scriptStartTime).TotalSeconds
    return ($elapsed -ge $Timeout)
}

function Get-ElapsedSeconds {
    return [math]::Round(((Get-Date) - $script:scriptStartTime).TotalSeconds, 1)
}

# ---------------------------------------------------------------------------
# Get-ServiceDiagnostics: Dump container logs and status on failure
# ---------------------------------------------------------------------------
function Get-ServiceDiagnostics {
    param(
        [string]$ServiceName,
        [string]$ContainerName
    )

    Write-InstallLog "Collecting diagnostics for $ServiceName" -Level WARN -Source "Diagnostics"

    # Get last 50 lines of container logs
    $logs = @()
    try {
        $logs = wsl -d $DISTRO_NAME -- bash -c "docker logs --tail 50 $ContainerName 2>&1"
        Write-InstallLog "--- $ServiceName container logs (last 50 lines) ---" -Level DEBUG -Source "Diagnostics"
        if ($logs) {
            foreach ($line in $logs) {
                Write-InstallLog "  $line" -Level DEBUG -Source "Diagnostics"
            }
        }
    }
    catch {
        Write-InstallLog "Could not retrieve logs for ${ServiceName}: $_" -Level WARN -Source "Diagnostics"
        $logs = @("(unable to retrieve logs)")
    }

    # Check if container is running
    $status = "unknown"
    try {
        $status = wsl -d $DISTRO_NAME -- bash -c "docker inspect --format='{{.State.Status}}' $ContainerName 2>&1"
        Write-InstallLog "$ServiceName container status: $status" -Level WARN -Source "Diagnostics"
    }
    catch {
        Write-InstallLog "Could not inspect $ServiceName container: $_" -Level WARN -Source "Diagnostics"
    }

    # Write diagnostic entry
    Write-InstallDiagnostic -ErrorCode "SVC-$($ServiceName.ToUpper())-001" -Component $ServiceName -Message "$ServiceName health check failed" -Context @{
        containerName   = $ContainerName
        containerStatus = $status
        lastLogs        = if ($logs) { ($logs | Select-Object -Last 10) -join "`n" } else { "(none)" }
    }
}

# ---------------------------------------------------------------------------
# Test-DistroExists
# ---------------------------------------------------------------------------
function Test-DistroExists {
    param([string]$Name)

    try {
        $distros = wsl --list --quiet 2>&1
        $found = ($distros -contains $Name)
        Write-InstallLog "WSL distro check for '$Name': found=$found" -Level DEBUG -Source "Start-Services"
        return $found
    }
    catch {
        Write-InstallLog "WSL distro check failed: $_" -Level ERROR -Source "Start-Services"
        return $false
    }
}

# ---------------------------------------------------------------------------
# Start-WSLDistro
# ---------------------------------------------------------------------------
function Start-WSLDistro {
    Start-InstallSection -Name "WSL Distribution"

    try {
        Write-InstallLog "Starting WSL distribution '$DISTRO_NAME'..." -Source "Start-Services"
        $null = wsl -d $DISTRO_NAME echo "started" 2>&1
        Write-InstallLog "WSL distribution is running" -Level INFO -Source "Start-Services"
        Complete-InstallSection -Name "WSL Distribution" -Status Success
        return $true
    }
    catch {
        Write-InstallLog "Failed to start WSL distribution: $_" -Level ERROR -Source "Start-Services"
        Write-InstallDiagnostic -ErrorCode "WSL-START-001" -Component "WSL" -Message "Failed to start WSL distribution '$DISTRO_NAME'" -Context @{
            distroName = $DISTRO_NAME
            error      = $_.Exception.Message
        }
        Complete-InstallSection -Name "WSL Distribution" -Status Failed
        return $false
    }
}

# ---------------------------------------------------------------------------
# Start-DockerDaemon (with retry)
# ---------------------------------------------------------------------------
function Start-DockerDaemon {
    Start-InstallSection -Name "Docker Daemon"

    try {
        # Check if Docker is already running
        Write-InstallLog "Checking if Docker daemon is already running..." -Source "Start-Services"
        $dockerCheck = wsl -d $DISTRO_NAME bash -c "docker info > /dev/null 2>&1 && echo 'RUNNING' || echo 'STOPPED'" 2>&1

        if ($dockerCheck -match "RUNNING") {
            Write-InstallLog "Docker daemon is already running" -Level INFO -Source "Start-Services"
            $script:serviceHealth["docker"] = @{
                status    = "healthy"
                startTime = (Get-Date).ToString("o")
                note      = "Already running"
            }
            Complete-InstallSection -Name "Docker Daemon" -Status Success
            return $true
        }

        Write-InstallLog "Docker daemon not running, starting with retry..." -Source "Start-Services"

        Invoke-WithRetry -OperationName "Docker Daemon Start" -RetryCount 3 -InitialDelay 5 -ScriptBlock {
            $startScript = @'
#!/bin/bash
if ! pgrep -x dockerd > /dev/null; then
    sudo dockerd --host=unix:///var/run/docker.sock > /var/log/docker.log 2>&1 &
    sleep 5
fi
docker info > /dev/null 2>&1
'@
            $result = wsl -d $DISTRO_NAME bash -c $startScript
            if ($LASTEXITCODE -ne 0) { throw "Docker daemon failed to start" }
        }

        $script:serviceHealth["docker"] = @{
            status    = "healthy"
            startTime = (Get-Date).ToString("o")
        }
        Write-InstallLog "Docker daemon started successfully" -Level INFO -Source "Start-Services"
        Complete-InstallSection -Name "Docker Daemon" -Status Success
        return $true
    }
    catch {
        Write-InstallLog "Failed to start Docker daemon after retries: $_" -Level ERROR -Source "Start-Services"
        $script:serviceHealth["docker"] = @{
            status = "failed"
            error  = $_.Exception.Message
        }
        Get-ServiceDiagnostics -ServiceName "docker" -ContainerName "dockerd"
        Complete-InstallSection -Name "Docker Daemon" -Status Failed
        return $false
    }
}

# ---------------------------------------------------------------------------
# Start-RecallHubServices (docker compose up)
# ---------------------------------------------------------------------------
function Start-RecallHubServices {
    Start-InstallSection -Name "Docker Compose"

    $composeCommand = if ($Detached) { "docker compose up -d" } else { "docker compose up" }

    $startScript = @"
#!/bin/bash
set -e
cd $CONFIG_PATH
$composeCommand
echo "COMPOSE_STARTED"
"@

    try {
        Write-InstallLog "Running: $composeCommand in $CONFIG_PATH" -Source "Start-Services"
        $output = wsl -d $DISTRO_NAME bash -c $startScript 2>&1

        if ($output -match "COMPOSE_STARTED") {
            Write-InstallLog "Docker Compose services starting" -Level INFO -Source "Start-Services"
            Complete-InstallSection -Name "Docker Compose" -Status Success
            return $true
        }
        else {
            Write-InstallLog "Docker Compose output: $output" -Level WARN -Source "Start-Services"
            Write-InstallLog "Services may not have started correctly" -Level WARN -Source "Start-Services"
            Complete-InstallSection -Name "Docker Compose" -Status Success
            return $true
        }
    }
    catch {
        Write-InstallLog "Failed to start services: $_" -Level ERROR -Source "Start-Services"
        Write-InstallDiagnostic -ErrorCode "COMPOSE-001" -Component "DockerCompose" -Message "docker compose up failed" -Context @{
            command = $composeCommand
            error   = $_.Exception.Message
        }
        Complete-InstallSection -Name "Docker Compose" -Status Failed
        return $false
    }
}

# ---------------------------------------------------------------------------
# Test-ServiceHealthWithRetry: Individual service health check
# ---------------------------------------------------------------------------
function Test-ServiceHealthWithRetry {
    param(
        [string]$ServiceName,
        [string]$ContainerName,
        [scriptblock]$HealthCheck,
        [int]$RetryCount = 10,
        [double]$InitialDelay = 3,
        [double]$BackoffMultiplier = 1.5,
        [int]$PerServiceTimeout = 60
    )

    # Check overall timeout first
    if (Test-TimeoutExceeded) {
        $elapsed = Get-ElapsedSeconds
        Write-InstallLog "Overall timeout (${Timeout}s) exceeded at ${elapsed}s - skipping $ServiceName health check" -Level WARN -Source "Start-Services"
        $script:serviceHealth[$ServiceName] = @{
            status = "timeout"
            port   = $SERVICE_PORTS[$ServiceName]
            error  = "Overall timeout exceeded before check started"
        }
        return $false
    }

    Start-InstallSection -Name "$ServiceName Health"

    try {
        $result = Invoke-WithRetry -OperationName "$ServiceName Health Check" `
            -RetryCount $RetryCount `
            -InitialDelay $InitialDelay `
            -BackoffMultiplier $BackoffMultiplier `
            -TimeoutSeconds $PerServiceTimeout `
            -ScriptBlock $HealthCheck

        Write-InstallLog "$ServiceName is healthy" -Level INFO -Source "Start-Services"
        $script:serviceHealth[$ServiceName] = @{
            status = "healthy"
            port   = $SERVICE_PORTS[$ServiceName]
        }

        # For Ollama, also capture loaded models
        if ($ServiceName -eq "ollama") {
            try {
                $modelOutput = wsl -d $DISTRO_NAME -- bash -c "curl -sf http://localhost:$($SERVICE_PORTS['ollama'])/api/tags 2>/dev/null"
                if ($modelOutput) {
                    $script:serviceHealth[$ServiceName]["modelsRaw"] = $modelOutput
                }
            }
            catch {
                Write-InstallLog "Could not retrieve Ollama models list" -Level DEBUG -Source "Start-Services"
            }
        }

        Complete-InstallSection -Name "$ServiceName Health" -Status Success
        return $true
    }
    catch {
        Write-InstallLog "$ServiceName health check failed: $_" -Level ERROR -Source "Start-Services"
        $script:serviceHealth[$ServiceName] = @{
            status = "unhealthy"
            port   = $SERVICE_PORTS[$ServiceName]
            error  = $_.Exception.Message
        }
        Get-ServiceDiagnostics -ServiceName $ServiceName -ContainerName $ContainerName
        Complete-InstallSection -Name "$ServiceName Health" -Status Failed
        return $false
    }
}

# ---------------------------------------------------------------------------
# Wait-ForServicesHealthy: Orchestrates per-service health checks
# ---------------------------------------------------------------------------
function Wait-ForServicesHealthy {
    Start-InstallSection -Name "Service Health Checks"

    Write-InstallLog "Running individual service health checks (timeout: ${Timeout}s)..." -Source "Start-Services"

    # MongoDB health check
    $mongoHealthy = Test-ServiceHealthWithRetry `
        -ServiceName "mongodb" `
        -ContainerName $CONTAINER_MAP["mongodb"] `
        -RetryCount 10 `
        -InitialDelay 3 `
        -BackoffMultiplier 1.5 `
        -PerServiceTimeout 60 `
        -HealthCheck {
            $output = wsl -d $DISTRO_NAME -- bash -c "docker exec $($CONTAINER_MAP['mongodb']) mongosh --eval 'db.adminCommand({ping:1})' --quiet 2>&1"
            if ($LASTEXITCODE -ne 0) { throw "MongoDB not ready: $output" }
            return $output
        }

    # Ollama health check
    $ollamaHealthy = Test-ServiceHealthWithRetry `
        -ServiceName "ollama" `
        -ContainerName $CONTAINER_MAP["ollama"] `
        -RetryCount 10 `
        -InitialDelay 3 `
        -BackoffMultiplier 1.5 `
        -PerServiceTimeout 60 `
        -HealthCheck {
            $output = wsl -d $DISTRO_NAME -- bash -c "curl -sf http://localhost:$($SERVICE_PORTS['ollama'])/api/tags 2>&1"
            if ($LASTEXITCODE -ne 0) { throw "Ollama not ready: $output" }
            return $output
        }

    # Backend health check
    $backendHealthy = Test-ServiceHealthWithRetry `
        -ServiceName "backend" `
        -ContainerName $CONTAINER_MAP["backend"] `
        -RetryCount 10 `
        -InitialDelay 3 `
        -BackoffMultiplier 1.5 `
        -PerServiceTimeout 60 `
        -HealthCheck {
            $output = wsl -d $DISTRO_NAME -- bash -c "curl -sf http://localhost:$($SERVICE_PORTS['backend'])/api/v1/status 2>&1"
            if ($LASTEXITCODE -ne 0) { throw "Backend not ready: $output" }
            return $output
        }

    # Frontend health check
    $frontendHealthy = Test-ServiceHealthWithRetry `
        -ServiceName "frontend" `
        -ContainerName $CONTAINER_MAP["frontend"] `
        -RetryCount 8 `
        -InitialDelay 2 `
        -BackoffMultiplier 1.5 `
        -PerServiceTimeout 45 `
        -HealthCheck {
            $output = wsl -d $DISTRO_NAME -- bash -c "curl -sf http://localhost:$($SERVICE_PORTS['frontend'])/ 2>&1"
            if ($LASTEXITCODE -ne 0) { throw "Frontend not ready: $output" }
            return $output
        }

    $allHealthy = $mongoHealthy -and $ollamaHealthy -and $backendHealthy -and $frontendHealthy

    if ($allHealthy) {
        Write-InstallLog "All services are healthy" -Level INFO -Source "Start-Services"
        Complete-InstallSection -Name "Service Health Checks" -Status Success
    }
    elseif (Test-TimeoutExceeded) {
        $elapsed = Get-ElapsedSeconds
        $failedServices = ($script:serviceHealth.GetEnumerator() |
            Where-Object { $_.Value.status -ne "healthy" } |
            ForEach-Object { $_.Key }) -join ", "
        Write-InstallLog "Overall timeout (${Timeout}s) reached after ${elapsed}s - unhealthy services: $failedServices" -Level WARN -Source "Start-Services"
        Complete-InstallSection -Name "Service Health Checks" -Status Failed
    }
    else {
        $failedServices = ($script:serviceHealth.GetEnumerator() |
            Where-Object { $_.Value.status -ne "healthy" } |
            ForEach-Object { $_.Key }) -join ", "
        Write-InstallLog "Some services failed health checks: $failedServices" -Level WARN -Source "Start-Services"
        Complete-InstallSection -Name "Service Health Checks" -Status Failed
    }

    return $allHealthy
}

# ---------------------------------------------------------------------------
# Get-ServiceStatus: Show docker compose ps output
# ---------------------------------------------------------------------------
function Get-ServiceStatus {
    Start-InstallSection -Name "Service Status"

    try {
        $statusScript = @"
#!/bin/bash
cd $CONFIG_PATH
docker compose ps --format "table {{.Service}}\t{{.Status}}\t{{.Ports}}"
"@
        $output = wsl -d $DISTRO_NAME bash -c $statusScript 2>&1
        Write-InstallLog "Docker Compose service status:" -Level INFO -Source "Start-Services"
        foreach ($line in $output) {
            Write-InstallLog "  $line" -Level INFO -Source "Start-Services"
        }
        Complete-InstallSection -Name "Service Status" -Status Success
    }
    catch {
        Write-InstallLog "Could not get service status: $_" -Level WARN -Source "Start-Services"
        Complete-InstallSection -Name "Service Status" -Status Skipped
    }
}

# ---------------------------------------------------------------------------
# Write-HealthReport: Generate JSON health report
# ---------------------------------------------------------------------------
function Write-HealthReport {
    $elapsed = Get-ElapsedSeconds

    # Determine overall status from individual service health
    $statuses = @()
    foreach ($key in $script:serviceHealth.Keys) {
        $statuses += $script:serviceHealth[$key].status
    }

    $hasUnhealthy = $statuses | Where-Object { $_ -eq "unhealthy" -or $_ -eq "failed" -or $_ -eq "timeout" }

    if ($hasUnhealthy) {
        $overallStatus = "unhealthy"
    }
    elseif ($statuses -contains "degraded") {
        $overallStatus = "degraded"
    }
    elseif ($statuses.Count -eq 0) {
        $overallStatus = "unknown"
    }
    else {
        $overallStatus = "healthy"
    }

    # Build service entries for report
    $servicesReport = [ordered]@{}
    foreach ($key in $script:serviceHealth.Keys) {
        $servicesReport[$key] = $script:serviceHealth[$key]
    }

    $healthReport = [ordered]@{
        timestamp      = (Get-Date).ToString("o")
        elapsedSeconds = $elapsed
        services       = $servicesReport
        overallStatus  = $overallStatus
        logFile        = $logFile
    }

    # Write to file
    $reportDir = Join-Path $env:LOCALAPPDATA "RecallHub\logs"
    if (-not (Test-Path $reportDir)) {
        try {
            New-Item -Path $reportDir -ItemType Directory -Force | Out-Null
        }
        catch {
            Write-InstallLog "Could not create report directory: $_" -Level WARN -Source "Start-Services"
            $reportDir = $env:TEMP
        }
    }

    $reportPath = Join-Path $reportDir "service-health.json"
    try {
        $json = $healthReport | ConvertTo-Json -Depth 5
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($reportPath, $json, $utf8NoBom)
        Write-InstallLog "Health report written to: $reportPath" -Level INFO -Source "Start-Services"
    }
    catch {
        Write-InstallLog "Failed to write health report: $_" -Level WARN -Source "Start-Services"
    }

    return $overallStatus
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
function Main {
    Write-InstallLog "============================================================" -Source "Start-Services"
    Write-InstallLog "  RecallHub Service Manager - START" -Source "Start-Services"
    Write-InstallLog "============================================================" -Source "Start-Services"

    # Check if distribution exists
    if (-not (Test-DistroExists -Name $DISTRO_NAME)) {
        Write-InstallLog "RecallHub distribution not found. Please run the installer first." -Level ERROR -Source "Start-Services"
        Write-InstallDiagnostic -ErrorCode "DISTRO-001" -Component "WSL" -Message "RecallHub WSL distribution not found"
        Write-HealthReport
        Export-InstallSummary
        exit 1
    }

    # Start WSL distribution
    if (-not (Start-WSLDistro)) {
        Write-HealthReport
        Export-InstallSummary
        exit 1
    }

    # Start Docker daemon (with retry)
    if (-not (Start-DockerDaemon)) {
        Write-HealthReport
        Export-InstallSummary
        exit 1
    }

    # Start docker compose services
    if (-not (Start-RecallHubServices)) {
        Write-HealthReport
        Export-InstallSummary
        exit 1
    }

    # Wait for healthy if requested, otherwise brief pause
    if ($Wait) {
        $allHealthy = Wait-ForServicesHealthy
    }
    else {
        Write-InstallLog "Waiting 5 seconds for services to initialize..." -Source "Start-Services"
        Start-Sleep -Seconds 5
    }

    # Show docker compose ps output
    Get-ServiceStatus

    # Generate health report
    $overallStatus = Write-HealthReport

    # Final summary
    $elapsed = Get-ElapsedSeconds

    if ($overallStatus -eq "healthy" -or (-not $Wait -and $overallStatus -eq "unknown")) {
        Write-InstallLog "============================================================" -Level INFO -Source "Start-Services"
        Write-InstallLog "  SERVICES STARTED SUCCESSFULLY (${elapsed}s)" -Level INFO -Source "Start-Services"
        Write-InstallLog "============================================================" -Level INFO -Source "Start-Services"
        Write-InstallLog "RecallHub is now running!" -Source "Start-Services"
        Write-InstallLog "Open your browser to: http://localhost:$($SERVICE_PORTS['frontend'])" -Source "Start-Services"
    }
    else {
        Write-InstallLog "============================================================" -Level WARN -Source "Start-Services"
        Write-InstallLog "  SERVICES STARTED WITH ISSUES ($overallStatus) (${elapsed}s)" -Level WARN -Source "Start-Services"
        Write-InstallLog "============================================================" -Level WARN -Source "Start-Services"
        $failedList = ($script:serviceHealth.GetEnumerator() |
            Where-Object { $_.Value.status -ne "healthy" } |
            ForEach-Object { "$($_.Key)=$($_.Value.status)" }) -join ", "
        if ($failedList) {
            Write-InstallLog "Problem services: $failedList" -Level WARN -Source "Start-Services"
        }
        Write-InstallLog "Check logs for details: $logFile" -Level WARN -Source "Start-Services"
    }

    # Export install summary
    Write-InstallLog "Log file: $logFile" -Source "Start-Services"
    Export-InstallSummary
}

# Run main
Main
