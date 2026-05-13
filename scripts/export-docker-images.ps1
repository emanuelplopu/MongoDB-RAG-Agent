#Requires -Version 5.1
<#
.SYNOPSIS
    Export RecallHub Docker images for offline deployment.
.DESCRIPTION
    Saves all production Docker images as tar files for
    inclusion in the backup package. Target machine loads them with docker load.
.PARAMETER OutputPath
    Directory to save image tar files. Defaults to .\images
.PARAMETER IncludeBase
    Also export the backend-base image for future fast builds on target.
.EXAMPLE
    .\export-docker-images.ps1 -OutputPath "E:\RecallHub_Backups\images"
    .\export-docker-images.ps1 -OutputPath ".\docker-images-export" -IncludeBase
#>
param(
    [Parameter(Mandatory=$true)]
    [string]$OutputPath,
    [switch]$IncludeBase
)

$ErrorActionPreference = "Stop"

function Write-OK   { param([string]$Msg) Write-Host "  [OK] $Msg" -ForegroundColor Green }
function Write-Warn { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Err  { param([string]$Msg) Write-Host "  [ERROR] $Msg" -ForegroundColor Red }
function Write-Info { param([string]$Msg) Write-Host "  $Msg" -ForegroundColor Gray }

Write-Host @"

=============================================
   RecallHub Docker Image Export
=============================================

"@ -ForegroundColor Magenta

Write-Host "  Output Path: $OutputPath" -ForegroundColor Gray
Write-Host "  Include Base: $(if ($IncludeBase) { 'YES' } else { 'NO' })" -ForegroundColor Gray
Write-Host ""

# Validate Docker
try {
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker not running" }
} catch {
    Write-Err "Docker daemon is not running. Start Docker Desktop first."
    exit 1
}

# Create output directory
if (-not (Test-Path $OutputPath)) {
    New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null
    Write-OK "Created output directory: $OutputPath"
}

# Define images to export
# These names match what docker-compose.yml generates (project-name + service-name)
$images = @(
    @{ Image = "mongodb-rag-agent-backend:latest";          File = "backend.tar" },
    @{ Image = "mongodb-rag-agent-backend-quellex:latest";  File = "backend-quellex.tar" },
    @{ Image = "mongodb-rag-agent-ingestion-worker:latest"; File = "ingestion-worker.tar" },
    @{ Image = "mongodb-rag-agent-frontend:latest";         File = "frontend.tar" },
    @{ Image = "mongodb-rag-agent-frontend-quellex:latest"; File = "frontend-quellex.tar" },
    @{ Image = "mongodb/mongodb-atlas-local:8.0";           File = "mongodb-atlas-local.tar" }
)

if ($IncludeBase) {
    $images += @{ Image = "recallhub-backend-base:latest"; File = "backend-base.tar" }
}

$exported = 0
$skipped  = 0
$totalSizeBytes = 0
$results = @()

foreach ($img in $images) {
    $imageName = $img.Image
    $fileName  = $img.File
    $filePath  = Join-Path $OutputPath $fileName

    Write-Host ""
    Write-Host "  [$($exported + $skipped + 1)/$($images.Count)] $imageName" -ForegroundColor Cyan

    # Check if image exists locally
    $inspectOutput = docker image inspect $imageName 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Image not found locally - skipping: $imageName"
        $skipped++
        $results += @{ Image = $imageName; Status = "SKIPPED"; Size = "N/A" }
        continue
    }

    # Get image size
    $sizeStr = docker image inspect $imageName --format "{{.Size}}" 2>&1 | Out-String
    $sizeStr = $sizeStr.Trim()
    $sizeBytes = 0
    if ($sizeStr -match '^\d+$') {
        $sizeBytes = [long]$sizeStr
    }
    $sizeMB = [math]::Round($sizeBytes / 1MB, 1)
    Write-Info "Image size: $sizeMB MB"

    # Export with docker save
    Write-Info "Saving to $fileName ..."
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        docker save -o $filePath $imageName
        if ($LASTEXITCODE -ne 0) {
            throw "docker save failed with exit code $LASTEXITCODE"
        }
    } catch {
        Write-Err "Failed to save $imageName - $_"
        $skipped++
        $results += @{ Image = $imageName; Status = "FAILED"; Size = "N/A" }
        continue
    }
    $elapsed = $sw.Elapsed

    # Verify the tar file
    if (-not (Test-Path $filePath)) {
        Write-Err "Tar file was not created: $filePath"
        $skipped++
        $results += @{ Image = $imageName; Status = "FAILED"; Size = "N/A" }
        continue
    }

    $tarSize = (Get-Item $filePath).Length
    $tarSizeMB = [math]::Round($tarSize / 1MB, 1)
    $tarSizeGB = [math]::Round($tarSize / 1GB, 2)

    if ($tarSize -lt 1MB) {
        Write-Warn "Tar file suspiciously small ($tarSizeMB MB) - may be corrupt"
        $results += @{ Image = $imageName; Status = "WARN"; Size = "$tarSizeMB MB" }
    } else {
        $sizeDisplay = if ($tarSize -ge 1GB) { "$tarSizeGB GB" } else { "$tarSizeMB MB" }
        Write-OK "Saved: $sizeDisplay in $([math]::Round($elapsed.TotalSeconds, 1))s"
        $results += @{ Image = $imageName; Status = "OK"; Size = $sizeDisplay }
    }

    $totalSizeBytes += $tarSize
    $exported++
}

# Summary
$totalGB = [math]::Round($totalSizeBytes / 1GB, 2)

Write-Host @"

=============================================
   EXPORT SUMMARY
=============================================

"@ -ForegroundColor Green

Write-Host "  Output Path:    $OutputPath" -ForegroundColor White
Write-Host "  Total Files:    $exported exported, $skipped skipped" -ForegroundColor White
Write-Host "  Total Size:     $totalGB GB" -ForegroundColor White
Write-Host ""

foreach ($r in $results) {
    $icon = switch ($r.Status) {
        "OK"      { "[OK]" }
        "SKIPPED" { "[--]" }
        "WARN"    { "[!!]" }
        default   { "[XX]" }
    }
    $color = switch ($r.Status) {
        "OK"      { "Green" }
        "SKIPPED" { "DarkGray" }
        "WARN"    { "Yellow" }
        default   { "Red" }
    }
    Write-Host "  $icon $($r.Image) ($($r.Size))" -ForegroundColor $color
}

Write-Host ""
if ($exported -gt 0) {
    Write-Host "  RESTORE ON TARGET MACHINE:" -ForegroundColor Yellow
    Write-Host "    docker load -i <file>.tar" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  Or use full-restore.ps1 which loads images automatically" -ForegroundColor Gray
    Write-Host "  when the images/ directory is present in the backup." -ForegroundColor Gray
} else {
    Write-Host "  No images were exported. Build images first:" -ForegroundColor Yellow
    Write-Host "    .\build-backend.ps1 -All" -ForegroundColor Gray
    Write-Host "    docker compose --profile recallhub --profile quellex build frontend frontend-quellex" -ForegroundColor Gray
}
Write-Host ""
