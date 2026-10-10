<#
.SYNOPSIS
    Imports the bundled Ollama model archive into the Ollama models Docker volume.
.DESCRIPTION
    Hardened-installer post-install step. build.ps1 ships ollama-models.tar as a
    tar of the contents of an Ollama volume (models/ at the top level) - it is NOT
    a Docker image, so `docker load` cannot import it. This script ensures the
    named volume exists, then runs a one-shot container that mounts the volume at
    /dest and the bundle directory at /src and extracts the archive in place.

    The helper container uses the bundled ollama/ollama image - the same image the
    compose stack runs - so no extra image has to be pulled on an offline machine.
    If that image is not present yet (older bundle without ollama.tar), a pull is
    attempted, which only works online.

    The volume name must match the explicit `name:` of the ollama_models volume in
    docker-compose.hardened.yml, so compose attaches to the pre-populated volume
    regardless of the compose project name.

    Idempotent: if /dest already contains models/, the import is skipped.

    Docker writes progress to stderr; native commands therefore run with
    $ErrorActionPreference = Continue and are judged by $LASTEXITCODE only.
.OUTPUTS
    Exit 0 = imported, already present or nothing bundled; 1 = Docker or helper
    image unavailable; 2 = extraction failed.
#>
param()

$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Logging.psm1") -Force

$null = Initialize-InstallLog -LogName "import-models"
Write-InstallLog "Importing bundled Ollama models" -Source "Import-Models"

$installPath = Split-Path -Parent $ScriptDir
$modelsDir = Join-Path $installPath "models"
$tarPath = Join-Path $modelsDir "ollama-models.tar"

# Must match `volumes: ollama_models: name:` in docker-compose.hardened.yml
$volumeName = "recallhub_ollama_models"
# Same image as the compose ollama service; loaded by Load-Images.ps1 when bundled
$helperImage = "ollama/ollama:latest"

if (-not (Test-Path $tarPath)) {
    Write-InstallLog "No ollama-models.tar at $tarPath - skipping import (Ollama will pull models on first use)" -Level WARN -Source "Import-Models"
    exit 0
}

$null = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-InstallLog "Docker daemon is not running. Start Docker Desktop and re-run." -Level ERROR -Source "Import-Models"
    Write-InstallDiagnostic -ErrorCode "DOCKER-001" -Component "Docker" -Message "Docker daemon not available"
    exit 1
}

$null = docker image inspect $helperImage 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-InstallLog "$helperImage is not loaded locally - attempting a pull (requires network)" -Level WARN -Source "Import-Models"
    $pullOutput = docker pull $helperImage 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) {
        Write-InstallLog "Helper image $helperImage unavailable; cannot import models. Bundle ollama.tar under images\ for offline installs." -Level ERROR -Source "Import-Models"
        Write-InstallDiagnostic -ErrorCode "IMAGE-002" -Component "Docker" -Message "helper image $helperImage unavailable" -Context @{ output = ($pullOutput -join "`n") }
        exit 1
    }
}

$existing = @(docker volume ls --format "{{.Name}}" 2>$null | Where-Object { $_ -eq $volumeName })
if ($existing.Count -eq 0) {
    Write-InstallLog "Creating Docker volume: $volumeName" -Source "Import-Models"
    $null = docker volume create $volumeName 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-InstallLog "docker volume create failed for $volumeName" -Level ERROR -Source "Import-Models"
        exit 1
    }
}

# Idempotency check: if the volume already has models/, skip extraction
$probe = docker run --rm --entrypoint sh -v "${volumeName}:/dest" $helperImage -c "test -d /dest/models && echo present || echo absent" 2>&1 | Out-String
if ($probe -match "present") {
    Write-InstallLog "Models already present in volume $volumeName - skipping extraction" -Source "Import-Models"
    exit 0
}

# Forward slashes keep Docker Desktop's bind-mount parsing happy
$srcMount = $modelsDir.Replace('\', '/')

Write-InstallLog "Extracting $tarPath into volume $volumeName ..." -Source "Import-Models"
$output = docker run --rm --entrypoint tar -v "${volumeName}:/dest" -v "${srcMount}:/src:ro" $helperImage -xf /src/ollama-models.tar -C /dest 2>&1 | ForEach-Object { "$_" }
$exit = $LASTEXITCODE
foreach ($line in $output) { Write-InstallLog "  $line" -Level DEBUG -Source "docker" }
if ($exit -ne 0) {
    Write-InstallLog "Failed to extract Ollama models (exit code $exit)" -Level ERROR -Source "Import-Models"
    Write-InstallDiagnostic -ErrorCode "MODELS-001" -Component "Ollama" -Message "model archive extraction failed" -Context @{ output = ($output -join "`n") }
    exit 2
}

Write-InstallLog "Ollama models imported into $volumeName" -Source "Import-Models"
exit 0
