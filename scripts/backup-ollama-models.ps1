<#
.SYNOPSIS
    Export Ollama models for inclusion in a RecallHub backup.
.DESCRIPTION
    Copies the Ollama model blobs and manifests for specified models
    into a target directory (typically the backup's models/ folder).
    Only copies blobs referenced by the requested models, not all blobs.
.PARAMETER OutputPath
    Where to copy the models. Should be the backup's models/ directory.
.PARAMETER Models
    Comma-separated list of model names to export.
    Defaults to gemma4:26b,gemma4:e4b,nomic-embed-text
.PARAMETER OllamaDir
    Source Ollama directory. Defaults to $env:USERPROFILE\.ollama
.EXAMPLE
    .\backup-ollama-models.ps1 -OutputPath "E:\RecallHub_Backups\RecallHub_v0.8.8_20260512\models"
    .\backup-ollama-models.ps1 -OutputPath .\models -Models "gemma4:26b,nomic-embed-text"
#>
param(
    [Parameter(Mandatory=$true)]
    [string]$OutputPath,
    [string]$Models = "gemma4:26b,gemma4:e4b,nomic-embed-text",
    [string]$OllamaDir = ""
)

$ErrorActionPreference = "Continue"

# Auto-detect Ollama directory if not specified
if (-not $OllamaDir) {
    if ($env:OLLAMA_MODELS -and (Test-Path $env:OLLAMA_MODELS)) {
        # OLLAMA_MODELS points directly to the models dir, go up one level
        $OllamaDir = Split-Path $env:OLLAMA_MODELS -Parent
    } elseif (Test-Path "$env:USERPROFILE\.ollama") {
        $OllamaDir = "$env:USERPROFILE\.ollama"
    } else {
        Write-Host "  [ERROR] Cannot auto-detect Ollama directory. Use -OllamaDir parameter." -ForegroundColor Red
        exit 1
    }
}

# ─── Helpers ────────────────────────────────────────────────────────────────────
function Write-Step  { param([string]$Msg) Write-Host "`n[*] $Msg" -ForegroundColor Cyan }
function Write-OK    { param([string]$Msg) Write-Host "  [OK] $Msg" -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Err   { param([string]$Msg) Write-Host "  [ERROR] $Msg" -ForegroundColor Red }
function Write-Info  { param([string]$Msg) Write-Host "  $Msg" -ForegroundColor Gray }

Write-Host @"

=============================================
   RecallHub - Backup Ollama Models
=============================================

"@ -ForegroundColor Magenta

# ─── Parse model list ───────────────────────────────────────────────────────────
$modelList = $Models -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ }
Write-Info "Models to export: $($modelList -join ', ')"
Write-Info "Ollama directory: $OllamaDir"
Write-Info "Output path:      $OutputPath"
Write-Host ""

# ─── Validate Ollama directory ──────────────────────────────────────────────────
Write-Step "Validating Ollama storage"

$ollamaModelsDir = Join-Path $OllamaDir "models"
$blobsDir = Join-Path $ollamaModelsDir "blobs"
$manifestsDir = Join-Path $ollamaModelsDir "manifests"

if (-not (Test-Path $ollamaModelsDir)) {
    Write-Err "Ollama models directory not found: $ollamaModelsDir"
    Write-Err "Is Ollama installed and have you pulled models?"
    exit 1
}

if (-not (Test-Path $blobsDir)) {
    Write-Err "Ollama blobs directory not found: $blobsDir"
    exit 1
}

if (-not (Test-Path $manifestsDir)) {
    Write-Err "Ollama manifests directory not found: $manifestsDir"
    exit 1
}

Write-OK "Ollama storage validated"

# ─── Create output directories ──────────────────────────────────────────────────
Write-Step "Preparing output directory"

$outputBlobs = Join-Path $OutputPath "blobs"
$outputManifests = Join-Path $OutputPath "manifests"

New-Item -ItemType Directory -Path $outputBlobs -Force | Out-Null
New-Item -ItemType Directory -Path $outputManifests -Force | Out-Null
Write-OK "Output directory ready: $OutputPath"

# ─── Process each model ─────────────────────────────────────────────────────────
Write-Step "Exporting models"

$totalBlobsCopied = 0
$totalBlobSize = [long]0
$modelsExported = @()
$modelsSkipped = @()
$allDigests = [System.Collections.Generic.HashSet[string]]::new()

foreach ($model in $modelList) {
    Write-Host ""
    Write-Info "Processing: $model"

    # Parse model name and tag
    if ($model -match "^([^:]+):(.+)$") {
        $modelName = $Matches[1]
        $modelTag = $Matches[2]
    } else {
        $modelName = $model
        $modelTag = "latest"
    }

    # Find manifest file
    # Ollama stores manifests at: manifests/registry.ollama.ai/library/<name>/<tag>
    $manifestPath = Join-Path (Join-Path (Join-Path (Join-Path $manifestsDir "registry.ollama.ai") "library") $modelName) $modelTag

    if (-not (Test-Path $manifestPath)) {
        Write-Warn "  Manifest not found for ${modelName}:${modelTag}"
        Write-Warn "  Expected: $manifestPath"
        Write-Warn "  Skipping this model."
        $modelsSkipped += $model
        continue
    }

    # Copy manifest
    $destManifestDir = Join-Path (Join-Path (Join-Path $outputManifests "registry.ollama.ai") "library") $modelName
    New-Item -ItemType Directory -Path $destManifestDir -Force | Out-Null
    Copy-Item $manifestPath (Join-Path $destManifestDir $modelTag) -Force
    Write-Info "  Manifest copied"

    # Parse manifest JSON to find referenced blobs
    try {
        $manifestContent = Get-Content $manifestPath -Raw | ConvertFrom-Json
    } catch {
        Write-Err "  Failed to parse manifest JSON: $_"
        $modelsSkipped += $model
        continue
    }

    # Collect all digest references
    $digests = @()

    # From layers
    if ($manifestContent.layers) {
        foreach ($layer in $manifestContent.layers) {
            if ($layer.digest) {
                $digests += $layer.digest
            }
        }
    }

    # From config
    if ($manifestContent.config -and $manifestContent.config.digest) {
        $digests += $manifestContent.config.digest
    }

    Write-Info "  Found $($digests.Count) blob references"

    # Copy each referenced blob (skip if already copied for another model)
    $modelBlobCount = 0
    foreach ($digest in $digests) {
        if ($allDigests.Contains($digest)) {
            # Already copied by a previous model (shared blob)
            continue
        }
        $null = $allDigests.Add($digest)

        # Convert digest format: "sha256:abc123" -> filename "sha256-abc123"
        $blobFilename = $digest -replace ":", "-"
        $blobSourcePath = Join-Path $blobsDir $blobFilename

        if (-not (Test-Path $blobSourcePath)) {
            Write-Warn "  Blob not found: $blobFilename"
            continue
        }

        $destBlobPath = Join-Path $outputBlobs $blobFilename
        if (-not (Test-Path $destBlobPath)) {
            Copy-Item $blobSourcePath $destBlobPath -Force
            $blobSize = (Get-Item $blobSourcePath).Length
            $totalBlobSize += $blobSize
            $modelBlobCount++
            $totalBlobsCopied++
        }
    }

    $modelSizeMB = 0
    foreach ($digest in $digests) {
        $blobFilename = $digest -replace ":", "-"
        $blobPath = Join-Path $outputBlobs $blobFilename
        if (Test-Path $blobPath) {
            $modelSizeMB += (Get-Item $blobPath).Length
        }
    }
    $modelSizeGB = [math]::Round($modelSizeMB / 1GB, 2)
    Write-OK "  ${modelName}:${modelTag} exported ($modelBlobCount new blobs, ~${modelSizeGB} GB)"
    $modelsExported += $model
}

# ─── Summary ────────────────────────────────────────────────────────────────────
Write-Host @"

=============================================
   MODEL BACKUP COMPLETE
=============================================

"@ -ForegroundColor Green

$totalSizeGB = [math]::Round($totalBlobSize / 1GB, 2)

Write-Host "  Output:        $OutputPath" -ForegroundColor White
Write-Host "  Total size:    $totalSizeGB GB" -ForegroundColor White
Write-Host "  Blobs copied:  $totalBlobsCopied unique files" -ForegroundColor White
Write-Host ""

if ($modelsExported.Count -gt 0) {
    Write-Host "  Exported models:" -ForegroundColor Green
    foreach ($m in $modelsExported) {
        Write-Host "    [OK] $m" -ForegroundColor Green
    }
}

if ($modelsSkipped.Count -gt 0) {
    Write-Host ""
    Write-Host "  Skipped models:" -ForegroundColor Yellow
    foreach ($m in $modelsSkipped) {
        Write-Host "    [!!] $m (not found locally)" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "  To pull missing models: ollama pull <model_name>" -ForegroundColor Gray
}

Write-Host ""
Write-Host "  To restore on target machine:" -ForegroundColor White
Write-Host "    .\scripts\restore-ollama-models.ps1 -ModelsPath `"$OutputPath`"" -ForegroundColor Gray
Write-Host ""
