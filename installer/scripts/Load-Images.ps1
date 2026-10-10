#Requires -Version 5.1
<#
.SYNOPSIS
    Loads bundled Docker images and imports Ollama models for offline RecallHub installation.
.DESCRIPTION
    Loads pre-exported Docker image tars into the local Docker daemon and imports
    the Ollama model archive into the ollama_models named volume.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# Resolve install root from script location (scripts/ is one level below app root)
$installPath = Split-Path -Parent $PSScriptRoot

# Import logging module
$loggingModule = Join-Path $PSScriptRoot "RecallHub-Logging.psm1"
if (Test-Path $loggingModule) {
    Import-Module $loggingModule -Force
}

function Write-Status {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp] [$Level] $Message"
    Write-Host $line
    if (Get-Command Write-RecallHubLog -ErrorAction SilentlyContinue) {
        Write-RecallHubLog -Message $Message -Level $Level
    }
}

# --- Load Docker Images ---
Write-Status "Starting Docker image loading from $installPath\images"

$imagesPath = Join-Path $installPath "images"
if (-not (Test-Path $imagesPath)) {
    Write-Status "Images directory not found: $imagesPath" -Level "ERROR"
    exit 1
}

$imageFiles = @("mongodb.tar", "ollama.tar", "backend.tar", "frontend.tar")
$loadedCount = 0

foreach ($imageFile in $imageFiles) {
    $tarPath = Join-Path $imagesPath $imageFile
    if (Test-Path $tarPath) {
        Write-Status "Loading image: $imageFile"
        $ErrorActionPreference = 'Continue'  # docker may write progress to stderr
        $output = docker load -i $tarPath 2>&1 | Out-String
        $loadExitCode = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($loadExitCode -ne 0) {
            Write-Status "  Failed to load $imageFile (exit code $loadExitCode): $($output.Trim())" -Level "ERROR"
            exit 2
        }
        Write-Status "  Loaded: $($output.Trim())"
        $loadedCount++
    } else {
        Write-Status "  Image file not found (skipping): $tarPath" -Level "WARN"
    }
}

Write-Status "Docker images loaded: $loadedCount of $($imageFiles.Count)"

if ($loadedCount -eq 0) {
    Write-Status "No images were loaded. Installation cannot proceed." -Level "ERROR"
    exit 3
}

# --- Import Ollama Models into Named Volume ---
# Must match the explicit volume name in docker-compose.hardened.yml
$ollamaVolume = "recallhub_ollama_models"
Write-Status "Importing Ollama models into $ollamaVolume volume"

$modelsPath = Join-Path $installPath "models"
$modelsTar = Join-Path $modelsPath "ollama-models.tar"

if (Test-Path $modelsTar) {
    try {
        $ErrorActionPreference = 'Continue'  # docker may write progress to stderr

        # Create the volume if it doesn't exist
        docker volume create $ollamaVolume 2>&1 | Out-Null
        Write-Status "  Volume $ollamaVolume ensured"

        # Use the just-loaded Ollama image (bundled, so no network pull) as a
        # one-shot container to extract models into the volume
        $helperImage = "ollama/ollama:latest"
        $null = docker image inspect $helperImage 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Status "  Helper image '$helperImage' not loaded; cannot import models" -Level "ERROR"
            exit 4
        }
        Write-Status "  Using image '$helperImage' for volume import"

        # Docker Desktop accepts native Windows paths for bind mounts
        $importOutput = docker run --rm --entrypoint tar `
            -v "${ollamaVolume}:/dest" `
            -v "${modelsPath}:/src:ro" `
            $helperImage -xf /src/ollama-models.tar -C /dest 2>&1 | Out-String
        $importExitCode = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'

        if ($importExitCode -ne 0) {
            Write-Status "  Model import container exited with code ${importExitCode}: $($importOutput.Trim())" -Level "ERROR"
            exit 4
        }

        Write-Status "  Ollama models imported successfully"
    } catch {
        Write-Status "  Failed to import Ollama models: $_" -Level "ERROR"
        exit 4
    }
} else {
    Write-Status "  Ollama models tar not found at $modelsTar (skipping)" -Level "WARN"
}

Write-Status "Image loading and model import complete"
exit 0
