<#
.SYNOPSIS
    Restore Ollama models from backup to local Ollama storage.
.DESCRIPTION
    Copies model blobs and manifests from the backup's models/ directory
    into Ollama's local storage, so models are immediately available
    without needing to pull them from the internet.
.PARAMETER ModelsPath
    Path to the models directory in the backup. Defaults to .\models
.PARAMETER OllamaDir
    Ollama storage directory. Defaults to $env:USERPROFILE\.ollama
.EXAMPLE
    .\restore-ollama-models.ps1
    .\restore-ollama-models.ps1 -ModelsPath "E:\RecallHub_Backups\RecallHub_v0.8.8_20260512\models"
    .\restore-ollama-models.ps1 -ModelsPath .\models -OllamaDir "D:\ollama"
#>
param(
    [string]$ModelsPath = ".\models",
    [string]$OllamaDir = "$env:USERPROFILE\.ollama"
)

$ErrorActionPreference = "Continue"

# ─── Helpers ────────────────────────────────────────────────────────────────────
function Write-Step  { param([string]$Msg) Write-Host "`n[*] $Msg" -ForegroundColor Cyan }
function Write-OK    { param([string]$Msg) Write-Host "  [OK] $Msg" -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Err   { param([string]$Msg) Write-Host "  [ERROR] $Msg" -ForegroundColor Red }
function Write-Info  { param([string]$Msg) Write-Host "  $Msg" -ForegroundColor Gray }

Write-Host @"

=============================================
   RecallHub - Restore Ollama Models
=============================================

"@ -ForegroundColor Magenta

# ─── Resolve paths ──────────────────────────────────────────────────────────────
if (-not [System.IO.Path]::IsPathRooted($ModelsPath)) {
    $ModelsPath = Join-Path (Get-Location).Path $ModelsPath
}

Write-Info "Models source:  $ModelsPath"
Write-Info "Ollama storage: $OllamaDir"
Write-Host ""

# ─── Validate Ollama is installed ───────────────────────────────────────────────
Write-Step "Validating Ollama installation"

$ollamaInstalled = $false
try {
    $ov = ollama --version 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0 -and $ov -match "ollama") {
        Write-OK "Ollama installed: $($ov.Trim())"
        $ollamaInstalled = $true
    }
} catch { }

if (-not $ollamaInstalled) {
    Write-Err "Ollama is not installed or not in PATH."
    Write-Err "Run install-prerequisites.ps1 first."
    exit 1
}

# ─── Validate models source ────────────────────────────────────────────────────
Write-Step "Validating models source directory"

if (-not (Test-Path $ModelsPath)) {
    Write-Err "Models directory not found: $ModelsPath"
    Write-Err "Ensure the backup contains a 'models' folder with blobs/ and manifests/ subdirectories."
    exit 1
}

$blobsSrc = Join-Path $ModelsPath "blobs"
$manifestsSrc = Join-Path $ModelsPath "manifests"

$hasBlobsDir = Test-Path $blobsSrc
$hasManifestsDir = Test-Path $manifestsSrc

if (-not $hasBlobsDir -and -not $hasManifestsDir) {
    Write-Err "Models directory is empty or has incorrect structure."
    Write-Err "Expected subdirectories: blobs/ and manifests/"
    exit 1
}

if ($hasBlobsDir) {
    $blobCount = (Get-ChildItem $blobsSrc -File -ErrorAction SilentlyContinue | Measure-Object).Count
    $blobSizeMB = [math]::Round(((Get-ChildItem $blobsSrc -File -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
    Write-Info "Blobs: $blobCount files ($blobSizeMB MB)"
}
if ($hasManifestsDir) {
    $manifestCount = (Get-ChildItem $manifestsSrc -File -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Info "Manifests: $manifestCount files"
}

Write-OK "Models source validated"

# ─── Stop Ollama service ────────────────────────────────────────────────────────
Write-Step "Stopping Ollama service"

$ollamaWasRunning = $false
$ollamaProcesses = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if ($ollamaProcesses) {
    $ollamaWasRunning = $true
    Write-Info "Stopping Ollama processes..."

    $stopAttempts = 0
    $maxAttempts = 3
    while ((Get-Process -Name "ollama" -ErrorAction SilentlyContinue) -and $stopAttempts -lt $maxAttempts) {
        Stop-Process -Name "ollama" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        $stopAttempts++
    }

    if (Get-Process -Name "ollama" -ErrorAction SilentlyContinue) {
        Write-Err "Could not stop Ollama processes after $maxAttempts attempts"
        Write-Err "Please close Ollama manually and re-run this script"
        exit 1
    }
    Write-OK "Ollama service stopped"
} else {
    Write-Info "Ollama is not currently running"
}

# Also stop ollama_llama_server if present
Stop-Process -Name "ollama_llama_server" -Force -ErrorAction SilentlyContinue

# ─── Copy models ────────────────────────────────────────────────────────────────
Write-Step "Copying model files to Ollama storage"

$ollamaModelsDir = Join-Path $OllamaDir "models"
if (-not (Test-Path $ollamaModelsDir)) {
    New-Item -ItemType Directory -Path $ollamaModelsDir -Force | Out-Null
    Write-Info "Created Ollama models directory: $ollamaModelsDir"
}

$copyErrors = 0

# Copy blobs
if ($hasBlobsDir) {
    $blobsDest = Join-Path $ollamaModelsDir "blobs"
    if (-not (Test-Path $blobsDest)) {
        New-Item -ItemType Directory -Path $blobsDest -Force | Out-Null
    }
    Write-Info "Copying blobs (this may take several minutes for large models)..."
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $null = robocopy $blobsSrc $blobsDest /E /MT:8 /NFL /NDL /NJH /NJS /NC /NS /R:2 /W:3
    $roboExit = $LASTEXITCODE
    $elapsed = $sw.Elapsed
    if ($roboExit -ge 4) {
        Write-Err "File copy failed (robocopy exit: $roboExit)"
        exit 1
    } elseif ($roboExit -eq 0) {
        # 0 means no files copied - check if dest already has them
        $destCount = (Get-ChildItem $blobsDest -File -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count
        if ($destCount -eq 0) {
            Write-Warn "No files copied and destination is empty - source may be inaccessible"
        } else {
            Write-Info "Files already exist at destination (no changes needed)"
        }
    } else {
        $copiedSize = [math]::Round(((Get-ChildItem $blobsDest -File -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1GB), 2)
        Write-OK "Blobs copied: ${copiedSize} GB in $([math]::Round($elapsed.TotalSeconds, 1))s"
    }
}

# Copy manifests
if ($hasManifestsDir) {
    $manifestsDest = Join-Path $ollamaModelsDir "manifests"
    if (-not (Test-Path $manifestsDest)) {
        New-Item -ItemType Directory -Path $manifestsDest -Force | Out-Null
    }
    Write-Info "Copying manifests..."
    $null = robocopy $manifestsSrc $manifestsDest /E /MT:8 /NFL /NDL /NJH /NJS /NC /NS /R:2 /W:3
    $roboExit = $LASTEXITCODE
    if ($roboExit -ge 4) {
        Write-Err "Manifest copy failed (robocopy exit: $roboExit)"
        exit 1
    } else {
        Write-OK "Manifests copied"
    }
}

# ─── Restart Ollama ─────────────────────────────────────────────────────────────
Write-Step "Restarting Ollama"

Write-Info "Starting Ollama serve..."
Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden

# Wait for Ollama API to be responsive
$maxWait = 60
$elapsed = 0
$ollamaReady = $false
while ($elapsed -lt $maxWait) {
    Start-Sleep -Seconds 3
    $elapsed += 3
    try {
        $listOutput = ollama list 2>&1
        if ($LASTEXITCODE -eq 0) { $ollamaReady = $true; break }
    } catch { }
    Write-Host "." -NoNewline -ForegroundColor Gray
}
Write-Host ""

if (-not $ollamaReady) {
    Write-Warn "Ollama service did not respond within ${maxWait}s"
    Write-Info "Models were copied successfully - try 'ollama list' manually after Ollama starts"
} else {
    Write-OK "Ollama service ready"
}

# ─── Verify models ─────────────────────────────────────────────────────────────
Write-Step "Verifying restored models"

$verified = $false
$retries = 0
while ($retries -lt 5) {
    try {
        $modelList = ollama list 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) {
            $verified = $true
            break
        }
    } catch { }
    Start-Sleep -Seconds 3
    $retries++
}

if ($verified) {
    Write-Host ""
    Write-Host "  Available models:" -ForegroundColor White
    Write-Host "  $('-' * 60)" -ForegroundColor DarkGray
    $lines = ($modelList.Trim() -split "`n")
    foreach ($line in $lines) {
        if ($line.Trim()) {
            Write-Host "  $($line.Trim())" -ForegroundColor Gray
        }
    }
    Write-Host ""
    Write-OK "Models verified via 'ollama list'"
} else {
    Write-Warn "Could not verify models - Ollama may still be starting."
    Write-Warn "Try running 'ollama list' manually in a few seconds."
}

# ─── Summary ────────────────────────────────────────────────────────────────────
Write-Host @"

=============================================
   MODEL RESTORE COMPLETE
=============================================

"@ -ForegroundColor Green

if ($copyErrors -eq 0) {
    Write-Host "  All model files restored successfully!" -ForegroundColor Green
} else {
    Write-Host "  Restore completed with $copyErrors warning(s). Check above for details." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  Models location: $ollamaModelsDir" -ForegroundColor White
Write-Host ""
Write-Host "  Next step:" -ForegroundColor White
Write-Host "    Run .\scripts\full-restore.ps1 -BackupPath <backup_folder>" -ForegroundColor Gray
Write-Host ""
