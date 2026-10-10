<#
.SYNOPSIS
    Loads bundled Docker image tar files into the local Docker daemon.
.DESCRIPTION
    Hardened-installer post-install step. Walks <app>\images and runs
    `docker load -i` on every *.tar file (backend, frontend and - when the build
    bundled them - the mongodb/ollama base images). Afterwards the
    recallhub-hardened-* images are retagged to :latest when the tar only
    carried the version tag, so the `${VERSION:-latest}` references in
    docker-compose.yml resolve even without a VERSION in .env.

    Ollama models are not Docker images; Import-OllamaModels.ps1 handles them.

    Docker writes progress to stderr. Windows PowerShell 5.1 turns that into a
    terminating error when $ErrorActionPreference is "Stop", so native commands
    run with Continue and are judged by $LASTEXITCODE only.
.OUTPUTS
    Exit 0 = all tars loaded (or nothing bundled), 1 = Docker unavailable,
    2 = a tar failed to load.
#>
param()

$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Logging.psm1") -Force

$null = Initialize-InstallLog -LogName "load-images"
Write-InstallLog "Loading bundled Docker images" -Source "Load-Images"

$installPath = Split-Path -Parent $ScriptDir
$imagesDir = Join-Path $installPath "images"

if (-not (Test-Path $imagesDir)) {
    Write-InstallLog "No images directory at $imagesDir - nothing to load (this build was not bundled with images; compose will pull from a registry)" -Level WARN -Source "Load-Images"
    exit 0
}

$null = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-InstallLog "Docker daemon is not running. Start Docker Desktop and re-run." -Level ERROR -Source "Load-Images"
    Write-InstallDiagnostic -ErrorCode "DOCKER-001" -Component "Docker" -Message "Docker daemon not available"
    exit 1
}

$tars = @(Get-ChildItem -Path $imagesDir -Filter "*.tar" -File -ErrorAction SilentlyContinue)
if ($tars.Count -eq 0) {
    Write-InstallLog "No *.tar files found under $imagesDir" -Level WARN -Source "Load-Images"
    exit 0
}

$loaded = 0
foreach ($tar in $tars) {
    Write-InstallLog "docker load -i $($tar.FullName)" -Source "Load-Images"
    $output = docker load -i $tar.FullName 2>&1 | ForEach-Object { "$_" }
    $exit = $LASTEXITCODE
    foreach ($line in $output) { Write-InstallLog "  $line" -Level DEBUG -Source "docker" }
    if ($exit -ne 0) {
        Write-InstallLog "Failed to load $($tar.Name) (exit code $exit)" -Level ERROR -Source "Load-Images"
        Write-InstallDiagnostic -ErrorCode "IMAGE-001" -Component "Docker" -Message "docker load failed for $($tar.Name)" -Context @{ output = ($output -join "`n") }
        exit 2
    }
    $loaded++
}
Write-InstallLog "Loaded $loaded image tar(s)" -Source "Load-Images"

# build.ps1 exports both :<version> and :latest, but older packages only carried
# the version tag. Retag so `${VERSION:-latest}` resolves either way.
foreach ($repo in @("recallhub-hardened-backend", "recallhub-hardened-frontend")) {
    $existing = @(docker images --format "{{.Repository}}:{{.Tag}}" 2>$null | Where-Object { $_ -like "$repo`:*" })
    if ($existing.Count -eq 0) { continue }
    if ($existing -contains "$repo`:latest") {
        Write-InstallLog "$repo`:latest already present" -Level DEBUG -Source "Load-Images"
        continue
    }
    $candidate = $existing | Where-Object { $_ -notmatch ":<none>$" } | Select-Object -First 1
    if ($candidate) {
        Write-InstallLog "Tagging $candidate as $repo`:latest" -Source "Load-Images"
        $null = docker tag $candidate "$repo`:latest" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-InstallLog "docker tag failed for $candidate" -Level WARN -Source "Load-Images"
        }
    }
}

Write-InstallLog "Load-Images complete" -Source "Load-Images"
exit 0
