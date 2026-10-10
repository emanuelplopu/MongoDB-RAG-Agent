<#
.SYNOPSIS
    Loads bundled Docker image tar files into the local Docker daemon.
.DESCRIPTION
    Walks the {app}\images directory and runs `docker load -i` on every
    *.tar file. Then retags loaded images so the references in
    docker-compose.yml (which use `${VERSION:-latest}`) resolve. Without
    this step, `docker compose up` would try to pull from a registry that
    has no such images.
#>
param()

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Logging.psm1") -Force

$null = Initialize-InstallLog -LogName "load-images"
Write-InstallLog "Loading bundled Docker images" -Source "Load-Images"

$installPath = Split-Path -Parent $ScriptDir
$imagesDir = Join-Path $installPath "images"

if (-not (Test-Path $imagesDir)) {
    Write-InstallLog "No images directory at $imagesDir - nothing to load (will rely on registry pulls)" -Level WARN -Source "Load-Images"
    exit 0
}

# Verify Docker is reachable
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-InstallLog "Docker daemon is not running. Start Docker Desktop and re-run." -Level ERROR -Source "Load-Images"
        exit 1
    }
} catch {
    Write-InstallLog "Docker CLI not available: $_" -Level ERROR -Source "Load-Images"
    exit 1
}

$tars = Get-ChildItem -Path $imagesDir -Filter "*.tar" -File -ErrorAction SilentlyContinue
if (-not $tars) {
    Write-InstallLog "No *.tar files found under $imagesDir" -Level WARN -Source "Load-Images"
    exit 0
}

foreach ($tar in $tars) {
    Write-InstallLog "docker load -i $($tar.FullName)" -Source "Load-Images"
    $output = docker load -i $tar.FullName 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-InstallLog "Failed to load $($tar.Name): $output" -Level ERROR -Source "Load-Images"
        exit 1
    }
    Write-InstallLog "Loaded: $output" -Level DEBUG -Source "Load-Images"
}

# Retag loaded backend/frontend images to :latest so compose finds them even
# if the saved tar only carried the version-specific tag (build.ps1 saves
# "${IMAGE}:${Version}", not :latest).
$retagPairs = @(
    @{ Repo = "recallhub-hardened-backend" },
    @{ Repo = "recallhub-hardened-frontend" }
)

foreach ($pair in $retagPairs) {
    $repo = $pair.Repo
    $existing = docker images --format "{{.Repository}}:{{.Tag}}" | Where-Object { $_ -like "$repo`:*" }
    if (-not $existing) { continue }

    $hasLatest = $existing | Where-Object { $_ -eq "$repo`:latest" }
    if ($hasLatest) {
        Write-InstallLog "$repo`:latest already present" -Level DEBUG -Source "Load-Images"
        continue
    }

    # Pick the newest non-<none> tag and retag it to latest
    $candidate = $existing | Where-Object { $_ -notmatch ":<none>$" } | Select-Object -First 1
    if ($candidate) {
        Write-InstallLog "Tagging $candidate as $repo`:latest" -Source "Load-Images"
        docker tag $candidate "$repo`:latest" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-InstallLog "docker tag failed for $candidate" -Level WARN -Source "Load-Images"
        }
    }
}

Write-InstallLog "Load-Images complete" -Source "Load-Images"
exit 0
