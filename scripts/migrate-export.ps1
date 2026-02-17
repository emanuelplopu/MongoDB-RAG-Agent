#Requires -Version 5.1
<#
.SYNOPSIS
    RecallHub Migration Export Script
    Creates a complete migration package including MongoDB data, configuration, and documents.

.DESCRIPTION
    This script:
    1. Validates the environment
    2. Stops all Docker containers gracefully
    3. Exports MongoDB database to a compressed archive
    4. Packages all project files
    5. Creates a single ZIP file for migration

.PARAMETER OutputPath
    Directory where the migration ZIP will be created. Defaults to user's Desktop.

.PARAMETER SkipDockerStop
    Skip stopping Docker containers (use if already stopped).

.PARAMETER IncludeGit
    Include .git folder in migration (default: excluded).

.EXAMPLE
    .\migrate-export.ps1
    .\migrate-export.ps1 -OutputPath "E:\Backups"
    .\migrate-export.ps1 -SkipDockerStop -IncludeGit
#>

param(
    [string]$OutputPath = [Environment]::GetFolderPath("Desktop"),
    [switch]$SkipDockerStop,
    [switch]$IncludeGit
)

# Configuration
$ErrorActionPreference = "Stop"
$ProgressPreference = "Continue"
$script:ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $script:ProjectRoot) {
    $script:ProjectRoot = (Get-Location).Path
}

# Colors and formatting
function Write-Step { param([string]$Message) Write-Host "`n[$script:CurrentStep/$script:TotalSteps] $Message" -ForegroundColor Cyan }
function Write-Success { param([string]$Message) Write-Host "  [OK] $Message" -ForegroundColor Green }
function Write-Warning { param([string]$Message) Write-Host "  [WARN] $Message" -ForegroundColor Yellow }
function Write-Error { param([string]$Message) Write-Host "  [ERROR] $Message" -ForegroundColor Red }
function Write-Info { param([string]$Message) Write-Host "  $Message" -ForegroundColor Gray }

$script:CurrentStep = 0
$script:TotalSteps = 7

# Banner
Write-Host @"

===============================================
   RecallHub Migration Export Tool
   Version 1.0
===============================================

"@ -ForegroundColor Magenta

Write-Host "Project Root: $script:ProjectRoot" -ForegroundColor Gray
Write-Host "Output Path:  $OutputPath" -ForegroundColor Gray
Write-Host ""

# Step 1: Validate Environment
$script:CurrentStep++
Write-Step "Validating environment..."

# Check Docker
try {
    $dockerVersion = docker --version 2>&1
    Write-Success "Docker found: $dockerVersion"
} catch {
    Write-Error "Docker is not installed or not in PATH"
    exit 1
}

# Check project structure
$requiredPaths = @(
    "docker-compose.yml",
    "data\mongoDB",
    ".env"
)

$missingPaths = @()
foreach ($path in $requiredPaths) {
    $fullPath = Join-Path $script:ProjectRoot $path
    if (-not (Test-Path $fullPath)) {
        $missingPaths += $path
    }
}

if ($missingPaths.Count -gt 0) {
    Write-Warning "Missing paths (may be optional):"
    foreach ($path in $missingPaths) {
        Write-Info "  - $path"
    }
}

# Check if .env exists, create from example if not
$envPath = Join-Path $script:ProjectRoot ".env"
$envExamplePath = Join-Path $script:ProjectRoot ".env.example"
if (-not (Test-Path $envPath) -and (Test-Path $envExamplePath)) {
    Write-Warning ".env not found, will include .env.example instead"
}

Write-Success "Environment validation complete"

# Step 2: Check Docker containers status
$script:CurrentStep++
Write-Step "Checking Docker containers..."

Set-Location $script:ProjectRoot

$runningContainers = docker compose ps --format json 2>&1 | ConvertFrom-Json -ErrorAction SilentlyContinue
$mongoRunning = $false

if ($runningContainers) {
    $containerNames = $runningContainers | ForEach-Object { $_.Name }
    Write-Info "Running containers: $($containerNames -join ', ')"
    $mongoRunning = $containerNames -contains "rag-mongodb"
} else {
    Write-Info "No containers currently running"
}

# Step 3: Export MongoDB database
$script:CurrentStep++
Write-Step "Exporting MongoDB database..."

$mongoBackupPath = Join-Path $script:ProjectRoot "data\mongoDB\migration_backup"
$mongoArchive = Join-Path $mongoBackupPath "rag_db_export.archive"

# Create backup directory
if (-not (Test-Path $mongoBackupPath)) {
    New-Item -ItemType Directory -Path $mongoBackupPath -Force | Out-Null
}

if (-not $mongoRunning) {
    Write-Info "Starting MongoDB container for export..."
    docker compose up -d mongodb
    Write-Info "Waiting for MongoDB to be healthy..."
    Start-Sleep -Seconds 10
    
    # Wait for health check
    $maxWait = 60
    $waited = 0
    while ($waited -lt $maxWait) {
        $health = docker inspect --format='{{.State.Health.Status}}' rag-mongodb 2>&1
        if ($health -eq "healthy") {
            Write-Success "MongoDB is healthy"
            break
        }
        Start-Sleep -Seconds 2
        $waited += 2
    }
    if ($waited -ge $maxWait) {
        Write-Warning "MongoDB health check timeout, proceeding anyway..."
    }
}

Write-Info "Running mongodump..."
try {
    # Get database name from .env or use default
    $dbName = "rag_db"
    if (Test-Path $envPath) {
        $envContent = Get-Content $envPath -Raw
        if ($envContent -match 'MONGODB_DATABASE=(\S+)') {
            $dbName = $matches[1]
        }
    }
    
    docker exec rag-mongodb mongodump --db $dbName --archive=/data/db/migration_backup/rag_db_export.archive --gzip
    
    if (Test-Path $mongoArchive) {
        $archiveSize = (Get-Item $mongoArchive).Length / 1GB
        Write-Success "MongoDB exported: $([math]::Round($archiveSize, 2)) GB (compressed)"
    } else {
        Write-Warning "MongoDB export may have failed - archive not found at expected location"
    }
} catch {
    Write-Error "MongoDB export failed: $_"
    Write-Info "Continuing with file-based backup..."
}

# Step 4: Stop Docker containers
$script:CurrentStep++
Write-Step "Stopping Docker containers..."

if (-not $SkipDockerStop) {
    Write-Info "Stopping all containers gracefully..."
    docker compose down
    Write-Success "All containers stopped"
} else {
    Write-Info "Skipping container shutdown (--SkipDockerStop flag)"
}

# Step 5: Prepare migration manifest
$script:CurrentStep++
Write-Step "Creating migration manifest..."

$manifest = @{
    ExportDate = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    ExportMachine = $env:COMPUTERNAME
    ProjectName = "RecallHub"
    Version = "1.0"
    SourcePath = $script:ProjectRoot
    Components = @{
        MongoDB = @{
            BackupMethod = "mongodump"
            BackupFile = "data/mongoDB/migration_backup/rag_db_export.archive"
            DataPath = "data/mongoDB"
        }
        Configuration = @(
            ".env",
            ".env.example",
            "profiles.yaml",
            "docker-compose.yml",
            "docker-compose.local.yml",
            "docker-compose.override.yml"
        )
        Documents = @(
            "documents/",
            "projects/"
        )
    }
    RestoreInstructions = @(
        "1. Extract ZIP to desired location",
        "2. Run: .\scripts\migrate-import.ps1",
        "3. Access app at http://localhost:11080"
    )
}

$manifestPath = Join-Path $script:ProjectRoot "MIGRATION_MANIFEST.json"
$manifest | ConvertTo-Json -Depth 10 | Set-Content $manifestPath -Encoding UTF8
Write-Success "Migration manifest created"

# Step 6: Create ZIP archive
$script:CurrentStep++
Write-Step "Creating migration ZIP archive..."

$timestamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
$zipFileName = "RecallHub_Migration_$timestamp.zip"
$zipPath = Join-Path $OutputPath $zipFileName

# Build exclusion list
$excludePatterns = @(
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "*.pyc",
    "*.pyo",
    "*.log",
    ".pytest_cache",
    ".mypy_cache",
    "*.egg-info",
    "dist",
    "build",
    ".coverage",
    "htmlcov",
    "frontend\node_modules",
    "frontend\dist"
)

if (-not $IncludeGit) {
    $excludePatterns += ".git"
}

Write-Info "Creating ZIP archive (this may take a while for large databases)..."
Write-Info "Excluding: $($excludePatterns -join ', ')"

# Create temporary staging directory
$stagingDir = Join-Path $env:TEMP "RecallHub_Migration_$timestamp"
if (Test-Path $stagingDir) {
    Remove-Item $stagingDir -Recurse -Force
}
New-Item -ItemType Directory -Path $stagingDir | Out-Null

# Copy files with exclusions using robocopy
$excludeDirArgs = $excludePatterns | Where-Object { -not $_.Contains("*") } | ForEach-Object { "/XD", $_ }
$excludeFileArgs = $excludePatterns | Where-Object { $_.Contains("*") } | ForEach-Object { "/XF", $_ }

$robocopyArgs = @($script:ProjectRoot, $stagingDir, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS") + $excludeDirArgs + $excludeFileArgs

Write-Info "Copying files to staging area..."
$robocopyResult = & robocopy @robocopyArgs
# Robocopy returns 0-7 for success, 8+ for errors
if ($LASTEXITCODE -ge 8) {
    Write-Warning "Some files may not have been copied (robocopy exit code: $LASTEXITCODE)"
}

# Calculate staging size
$stagingSize = (Get-ChildItem $stagingDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1GB
Write-Info "Staging directory size: $([math]::Round($stagingSize, 2)) GB"

# Create ZIP using .NET compression
Write-Info "Compressing to ZIP..."
Add-Type -AssemblyName System.IO.Compression.FileSystem

if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

try {
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $stagingDir,
        $zipPath,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false  # Don't include base directory name
    )
    Write-Success "ZIP archive created successfully"
} catch {
    Write-Error "Failed to create ZIP: $_"
    # Fallback to Compress-Archive
    Write-Info "Trying fallback compression method..."
    Compress-Archive -Path "$stagingDir\*" -DestinationPath $zipPath -CompressionLevel Optimal -Force
}

# Cleanup staging
Write-Info "Cleaning up staging directory..."
Remove-Item $stagingDir -Recurse -Force

# Step 7: Final summary
$script:CurrentStep++
Write-Step "Migration export complete!"

$zipSize = (Get-Item $zipPath).Length / 1GB

Write-Host @"

===============================================
   MIGRATION EXPORT SUMMARY
===============================================

"@ -ForegroundColor Green

Write-Host "  Archive:    $zipPath" -ForegroundColor White
Write-Host "  Size:       $([math]::Round($zipSize, 2)) GB" -ForegroundColor White
Write-Host "  Created:    $(Get-Date)" -ForegroundColor White
Write-Host ""
Write-Host "  NEXT STEPS:" -ForegroundColor Yellow
Write-Host "  1. Copy '$zipFileName' to the new machine" -ForegroundColor Gray
Write-Host "  2. Extract the ZIP to your desired location" -ForegroundColor Gray
Write-Host "  3. Run: .\scripts\migrate-import.ps1" -ForegroundColor Gray
Write-Host ""
Write-Host "  The import script will automatically:" -ForegroundColor Gray
Write-Host "    - Restore MongoDB database" -ForegroundColor Gray
Write-Host "    - Configure Docker containers" -ForegroundColor Gray
Write-Host "    - Start all services" -ForegroundColor Gray
Write-Host ""

# Cleanup manifest from source (it's in the ZIP)
if (Test-Path $manifestPath) {
    Remove-Item $manifestPath -Force
}

Write-Host "Export completed successfully!" -ForegroundColor Green
