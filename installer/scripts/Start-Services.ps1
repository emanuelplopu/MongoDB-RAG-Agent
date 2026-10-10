<#
.SYNOPSIS
    Starts RecallHub / Quellex services via Docker Compose.
.DESCRIPTION
    Works for both deployment layouts:
      * Backup/restore install: these scripts live in %LocalAppData%\RecallHub\scripts
        and config.json (written by Write-TrayConfig.ps1 during INSTALL.ps1 /
        full-restore.ps1) points docker.installPath at the source tree
        (e.g. C:\RecallHub) and docker.composeProfile at the tenant profile.
      * Hardened installer: scripts live in <app>\scripts next to docker-compose.yml;
        config.json carries no installPath, so the parent folder is used.

    Docker writes its progress to stderr. Windows PowerShell 5.1 turns that into a
    terminating error when $ErrorActionPreference is "Stop", which is why earlier
    versions of this script died silently right after "Running docker compose up".
    Native commands are therefore run with ErrorActionPreference = Continue and
    judged by $LASTEXITCODE only.
.PARAMETER Wait
    Block until the frontend and the backend health endpoint answer, or Timeout elapses.
.PARAMETER Timeout
    Seconds to wait for health when -Wait is given (config docker.startupTimeout wins).
.OUTPUTS
    Exit 0 = started (and healthy when -Wait), 1 = Docker unavailable,
    2 = compose file missing, 3 = docker compose up failed, 4 = health timeout.
#>
param(
    [switch]$Detached = $true,
    [switch]$Wait,
    [int]$Timeout = 180
)

$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Logging.psm1") -Force

$logFile = Initialize-InstallLog -LogName "start-services"
Write-InstallLog "Starting services (Docker Desktop mode)" -Source "Start-Services"
Write-InstallLog "Parameters: Wait=$Wait, Timeout=$Timeout" -Level DEBUG -Source "Start-Services"

# --- Configuration -----------------------------------------------------------
function Get-ConfigValue {
    param($Object, [string]$Name, $Default)
    if ($null -ne $Object) {
        $prop = $Object.PSObject.Properties[$Name]
        if ($prop -and $null -ne $prop.Value -and "$($prop.Value)" -ne "") { return $prop.Value }
    }
    return $Default
}

$configPath = Join-Path (Split-Path $ScriptDir -Parent) "config.json"
$Config = $null
if (Test-Path $configPath) {
    try {
        $Config = Get-Content $configPath -Raw | ConvertFrom-Json
        Write-InstallLog "Loaded config from $configPath" -Level DEBUG -Source "Start-Services"
    } catch {
        Write-InstallLog "config.json at $configPath is not valid JSON: $_" -Level WARN -Source "Start-Services"
    }
} else {
    Write-InstallLog "Config not found at $configPath, using defaults" -Level WARN -Source "Start-Services"
}

$dockerCfg = Get-ConfigValue $Config "docker" $null
$portsCfg  = Get-ConfigValue $Config "ports" $null

$dockerDesktopPath = Get-ConfigValue $dockerCfg "desktopPath" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$installPath       = Get-ConfigValue $dockerCfg "installPath" "auto"
$composeFile       = Get-ConfigValue $dockerCfg "composeFile" "docker-compose.yml"
$composeProfile    = Get-ConfigValue $dockerCfg "composeProfile" ""
$startupTimeout    = [int](Get-ConfigValue $dockerCfg "startupTimeout" $Timeout)
$healthInterval    = [int](Get-ConfigValue $dockerCfg "healthCheckInterval" 5)
$frontendPort      = [int](Get-ConfigValue $portsCfg "frontend" 11080)
$backendPort       = [int](Get-ConfigValue $portsCfg "backend" 11000)

# "auto" (the hardened installer's config.json) or no config at all: derive the app
# root from the script location - scripts\ is one level below the compose file.
if ($installPath -eq "auto") { $installPath = Split-Path $ScriptDir -Parent }

Write-InstallLog "installPath=$installPath composeFile=$composeFile profile='$composeProfile' frontendPort=$frontendPort backendPort=$backendPort" -Source "Start-Services"

# --- Step 1: Docker daemon ---------------------------------------------------
function Test-DockerDaemon {
    $null = docker info 2>&1
    return ($LASTEXITCODE -eq 0)
}

Write-InstallLog "Checking Docker daemon..." -Source "Start-Services"
$dockerReady = Test-DockerDaemon
if (-not $dockerReady) {
    if (Test-Path $dockerDesktopPath) {
        Write-InstallLog "Starting Docker Desktop..." -Source "Start-Services"
        Start-Process $dockerDesktopPath
        $waited = 0
        while ($waited -lt 120) {
            Start-Sleep -Seconds 3
            $waited += 3
            if (Test-DockerDaemon) {
                $dockerReady = $true
                Write-InstallLog "Docker Desktop started after ${waited}s" -Source "Start-Services"
                break
            }
        }
    } else {
        Write-InstallLog "Docker Desktop not found at $dockerDesktopPath" -Level ERROR -Source "Start-Services"
    }
    if (-not $dockerReady) {
        Write-InstallLog "Docker daemon not available" -Level ERROR -Source "Start-Services"
        Write-InstallDiagnostic -ErrorCode "DOCKER-001" -Component "Docker" -Message "Docker daemon not available"
        exit 1
    }
}

# --- Step 2: docker compose up -----------------------------------------------
$composePath = Join-Path $installPath $composeFile
if (-not (Test-Path $composePath)) {
    Write-InstallLog "Compose file not found: $composePath" -Level ERROR -Source "Start-Services"
    Write-InstallDiagnostic -ErrorCode "COMPOSE-001" -Component "Docker" -Message "docker-compose.yml not found at $composePath"
    exit 2
}

$composeBase = @("compose", "-f", $composePath)
$overridePath = Join-Path $installPath "docker-compose.override.yml"
if ($composeFile -eq "docker-compose.yml" -and (Test-Path $overridePath)) {
    $composeBase += @("-f", $overridePath)
}
if ($composeProfile) { $composeBase += @("--profile", $composeProfile) }

$upArgs = $composeBase + @("up", "-d", "--remove-orphans")
Write-InstallLog "Running: docker $($upArgs -join ' ')  (cwd: $installPath)" -Source "Start-Services"
Push-Location $installPath
try {
    $output = & docker @upArgs 2>&1 | ForEach-Object { "$_" }
    $exit = $LASTEXITCODE
} finally {
    Pop-Location
}
foreach ($line in $output) { Write-InstallLog "  $line" -Level DEBUG -Source "compose" }
if ($exit -ne 0) {
    Write-InstallLog "docker compose up failed with exit code $exit" -Level ERROR -Source "Start-Services"
    Write-InstallDiagnostic -ErrorCode "COMPOSE-002" -Component "Docker" -Message "docker compose up failed (exit $exit)" -Context @{ output = ($output -join "`n") }
    exit 3
}
Write-InstallLog "docker compose up completed" -Source "Start-Services"

# --- Step 3: health ------------------------------------------------------------
function Test-Http200 {
    param([string]$Url)
    try {
        return ((Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200)
    } catch {
        return $false
    }
}

if ($Wait) {
    $frontendUrl = "http://localhost:$frontendPort/"
    $backendUrl  = "http://localhost:$backendPort/api/v1/system/health"
    Write-InstallLog "Waiting up to ${startupTimeout}s for $frontendUrl and $backendUrl" -Source "Start-Services"
    $elapsed = 0
    $healthy = $false
    while ($elapsed -lt $startupTimeout) {
        Start-Sleep -Seconds $healthInterval
        $elapsed += $healthInterval
        $frontendOk = Test-Http200 $frontendUrl
        $backendOk  = Test-Http200 $backendUrl
        if ($frontendOk -and $backendOk) { $healthy = $true; break }
        Write-InstallLog "Health: frontend=$frontendOk backend=$backendOk (${elapsed}s / ${startupTimeout}s)" -Level DEBUG -Source "Start-Services"
    }
    if (-not $healthy) {
        Write-InstallLog "Services did not become healthy within ${startupTimeout}s" -Level ERROR -Source "Start-Services"
        Push-Location $installPath
        try {
            $psArgs = $composeBase + @("ps")
            $ps = & docker @psArgs 2>&1 | ForEach-Object { "$_" }
        } finally { Pop-Location }
        foreach ($line in $ps) { Write-InstallLog "  $line" -Level WARN -Source "compose-ps" }
        Write-InstallDiagnostic -ErrorCode "HEALTH-001" -Component "Services" -Message "frontend/backend not healthy after ${startupTimeout}s" -Context @{ frontend = $frontendUrl; backend = $backendUrl }
        exit 4
    }
    Write-InstallLog "All services healthy after ${elapsed}s" -Source "Start-Services"
}

Write-InstallLog "Start-Services complete" -Source "Start-Services"
exit 0
