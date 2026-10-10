<#
.SYNOPSIS
    Imports the bundled Ollama model archive into the ollama_models Docker volume.
.DESCRIPTION
    The build script ships ollama-models.tar as a tar of the contents of an
    Ollama volume - it is NOT a Docker image, so `docker load` cannot import
    it. This script ensures the named volume exists, then runs a one-shot
    Ubuntu container that mounts the named volume at /dest and the bundle
    directory at /src and extracts the archive in place.

    Idempotent: if /dest already contains models/, the import is skipped.
#>
param()

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Logging.psm1") -Force

$null = Initialize-InstallLog -LogName "import-models"
Write-InstallLog "Importing bundled Ollama models" -Source "Import-Models"

$installPath = Split-Path -Parent $ScriptDir
$modelsDir = Join-Path $installPath "models"
$tarPath = Join-Path $modelsDir "ollama-models.tar"

if (-not (Test-Path $tarPath)) {
    Write-InstallLog "No ollama-models.tar at $tarPath - skipping import (Ollama will pull on first use)" -Level WARN -Source "Import-Models"
    exit 0
}

# Ensure Docker is reachable
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-InstallLog "Docker daemon is not running. Start Docker Desktop and re-run." -Level ERROR -Source "Import-Models"
        exit 1
    }
} catch {
    Write-InstallLog "Docker CLI not available: $_" -Level ERROR -Source "Import-Models"
    exit 1
}

$volumeName = "ollama_models"

# Create the named volume if it doesn't exist
$existing = docker volume ls --format "{{.Name}}" | Where-Object { $_ -eq $volumeName }
if (-not $existing) {
    Write-InstallLog "Creating Docker volume: $volumeName" -Source "Import-Models"
    docker volume create $volumeName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-InstallLog "docker volume create failed for $volumeName" -Level ERROR -Source "Import-Models"
        exit 1
    }
}

# Idempotency check: if the volume already has models/, skip extraction
$probe = docker run --rm `
    -v "${volumeName}:/dest" `
    ubuntu:22.04 `
    bash -c "test -d /dest/models && echo present || echo absent" 2>&1
$probeText = ($probe | Out-String).Trim()
if ($probeText -match "present") {
    Write-InstallLog "Models already present in volume $volumeName - skipping extraction" -Source "Import-Models"
    exit 0
}

# Use forward slashes for the host path so Docker Desktop mounts it cleanly
$srcMount = ($modelsDir -replace '\\', '/')

Write-InstallLog "Extracting $tarPath into volume $volumeName ..." -Source "Import-Models"
$output = docker run --rm `
    -v "${volumeName}:/dest" `
    -v "${srcMount}:/src:ro" `
    ubuntu:22.04 `
    bash -c "cd /dest && tar -xf /src/ollama-models.tar" 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-InstallLog "Failed to extract Ollama models: $output" -Level ERROR -Source "Import-Models"
    exit 1
}

Write-InstallLog "Ollama models imported into $volumeName" -Source "Import-Models"
exit 0
