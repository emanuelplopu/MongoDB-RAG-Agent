#Requires -Version 5.1
<#
.SYNOPSIS
    RecallHub Migration Import Script
    Restores a complete RecallHub installation from a migration package.

.DESCRIPTION
    This script:
    1. Validates prerequisites (Docker, disk space)
    2. Extracts the migration ZIP (if not already extracted)
    3. Validates the migration manifest
    4. Starts MongoDB container
    5. Restores the database from mongodump archive
    6. Starts all services
    7. Verifies the installation

.PARAMETER ZipPath
    Path to the migration ZIP file. If not specified, assumes current directory is extracted migration.

.PARAMETER InstallPath
    Where to extract/install RecallHub. Defaults to current directory.

.PARAMETER SkipDatabaseRestore
    Skip MongoDB restore (use existing data files).

.PARAMETER SkipVerification
    Skip post-installation verification.

.EXAMPLE
    .\migrate-import.ps1 -ZipPath "E:\RecallHub_Migration_20240115_120000.zip"
    .\migrate-import.ps1 -ZipPath "E:\backup.zip" -InstallPath "D:\RecallHub"
    .\migrate-import.ps1  # Run from extracted migration folder
#>

param(
    [string]$ZipPath,
    [string]$InstallPath,
    [switch]$SkipDatabaseRestore,
    [switch]$SkipVerification
)

# Configuration
$ErrorActionPreference = "Stop"
$ProgressPreference = "Continue"

# Colors and formatting
function Write-Step { param([string]$Message) Write-Host "`n[$script:CurrentStep/$script:TotalSteps] $Message" -ForegroundColor Cyan }
function Write-Success { param([string]$Message) Write-Host "  [OK] $Message" -ForegroundColor Green }
function Write-Warning { param([string]$Message) Write-Host "  [WARN] $Message" -ForegroundColor Yellow }
function Write-ErrorMsg { param([string]$Message) Write-Host "  [ERROR] $Message" -ForegroundColor Red }
function Write-Info { param([string]$Message) Write-Host "  $Message" -ForegroundColor Gray }

$script:CurrentStep = 0
$script:TotalSteps = 8

# Banner
Write-Host @"

===============================================
   RecallHub Migration Import Tool
   Version 1.0
===============================================

"@ -ForegroundColor Magenta

# Determine project root
if ($ZipPath -and (Test-Path $ZipPath)) {
    # ZIP file provided - need to extract
    $needsExtraction = $true
    if (-not $InstallPath) {
        $InstallPath = Join-Path (Split-Path $ZipPath -Parent) "RecallHub"
    }
    $script:ProjectRoot = $InstallPath
} elseif ($InstallPath) {
    # Install path provided, check if it exists
    $script:ProjectRoot = $InstallPath
    $needsExtraction = -not (Test-Path (Join-Path $InstallPath "docker-compose.yml"))
} else {
    # Assume current directory is the extracted migration
    $script:ProjectRoot = (Get-Location).Path
    $needsExtraction = $false
}

Write-Host "Project Root: $script:ProjectRoot" -ForegroundColor Gray
if ($ZipPath) {
    Write-Host "ZIP Path:     $ZipPath" -ForegroundColor Gray
}
Write-Host ""

# Step 1: Validate Prerequisites
$script:CurrentStep++
Write-Step "Validating prerequisites..."

# Check Docker
try {
    $dockerVersion = docker --version 2>&1
    Write-Success "Docker found: $dockerVersion"
} catch {
    Write-ErrorMsg "Docker is not installed or not in PATH"
    Write-Host ""
    Write-Host "Please install Docker Desktop for Windows:" -ForegroundColor Yellow
    Write-Host "  https://www.docker.com/products/docker-desktop" -ForegroundColor Gray
    exit 1
}

# Check if Docker daemon is running
try {
    docker info 2>&1 | Out-Null
    Write-Success "Docker daemon is running"
} catch {
    Write-ErrorMsg "Docker daemon is not running"
    Write-Host ""
    Write-Host "Please start Docker Desktop and try again." -ForegroundColor Yellow
    exit 1
}

# Check disk space (need at least 25GB free for safety)
if ($InstallPath) {
    $drive = Split-Path $InstallPath -Qualifier
} else {
    $drive = Split-Path $script:ProjectRoot -Qualifier
}
$disk = Get-PSDrive -Name ($drive -replace ":", "")
$freeSpaceGB = [math]::Round($disk.Free / 1GB, 2)
Write-Info "Available disk space on ${drive}: $freeSpaceGB GB"

if ($freeSpaceGB -lt 25) {
    Write-Warning "Less than 25GB free space. Migration may fail for large databases."
}

Write-Success "Prerequisites validated"

# Step 2: Extract ZIP (if needed)
$script:CurrentStep++
Write-Step "Preparing migration files..."

if ($needsExtraction -and $ZipPath) {
    if (-not (Test-Path $ZipPath)) {
        Write-ErrorMsg "ZIP file not found: $ZipPath"
        exit 1
    }
    
    Write-Info "Extracting migration archive..."
    Write-Info "This may take several minutes for large databases..."
    
    # Create install directory
    if (-not (Test-Path $script:ProjectRoot)) {
        New-Item -ItemType Directory -Path $script:ProjectRoot -Force | Out-Null
    }
    
    # Extract using .NET
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    try {
        [System.IO.Compression.ZipFile]::ExtractToDirectory($ZipPath, $script:ProjectRoot)
        Write-Success "Extraction complete"
    } catch {
        Write-ErrorMsg "Extraction failed: $_"
        Write-Info "Trying fallback extraction method..."
        Expand-Archive -Path $ZipPath -DestinationPath $script:ProjectRoot -Force
    }
} else {
    Write-Info "Using existing files at: $script:ProjectRoot"
}

# Verify essential files exist
$essentialFiles = @(
    "docker-compose.yml"
)

foreach ($file in $essentialFiles) {
    $filePath = Join-Path $script:ProjectRoot $file
    if (-not (Test-Path $filePath)) {
        Write-ErrorMsg "Essential file missing: $file"
        Write-Host "Please ensure you're running this from a valid RecallHub migration." -ForegroundColor Yellow
        exit 1
    }
}

Write-Success "Migration files ready"

# Step 3: Read migration manifest
$script:CurrentStep++
Write-Step "Reading migration manifest..."

$manifestPath = Join-Path $script:ProjectRoot "MIGRATION_MANIFEST.json"
$manifest = $null

if (Test-Path $manifestPath) {
    try {
        $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
        Write-Success "Manifest loaded"
        Write-Info "Source machine: $($manifest.ExportMachine)"
        Write-Info "Export date: $($manifest.ExportDate)"
    } catch {
        Write-Warning "Could not parse manifest, using defaults"
    }
} else {
    Write-Warning "No manifest found, using default configuration"
}

# Step 4: Configure environment
$script:CurrentStep++
Write-Step "Configuring environment..."

Set-Location $script:ProjectRoot

$envPath = Join-Path $script:ProjectRoot ".env"
$envExamplePath = Join-Path $script:ProjectRoot ".env.example"

if (-not (Test-Path $envPath)) {
    if (Test-Path $envExamplePath) {
        Write-Info "Creating .env from .env.example..."
        Copy-Item $envExamplePath $envPath
        Write-Warning ".env created from example - you may need to update API keys!"
    } else {
        Write-ErrorMsg "No .env or .env.example found"
        exit 1
    }
} else {
    Write-Success ".env file exists"
}

# Update MongoDB URI for local Docker
$envContent = Get-Content $envPath -Raw
if ($envContent -match 'mongodb\+srv://') {
    Write-Warning "Detected Atlas connection string in .env"
    Write-Info "For local Docker deployment, update MONGODB_URI to:"
    Write-Info "  mongodb://mongodb:27017/?directConnection=true"
}

Write-Success "Environment configured"

# Step 5: Stop any existing containers
$script:CurrentStep++
Write-Step "Preparing Docker environment..."

Write-Info "Stopping any existing RecallHub containers..."
docker compose down 2>&1 | Out-Null

# Check for port conflicts
$portsToCheck = @(11017, 11000, 11080)
$portConflicts = @()

foreach ($port in $portsToCheck) {
    $inUse = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($inUse) {
        $portConflicts += $port
    }
}

if ($portConflicts.Count -gt 0) {
    Write-Warning "Ports in use: $($portConflicts -join ', ')"
    Write-Info "These ports are needed by RecallHub. Please free them before continuing."
}

Write-Success "Docker environment ready"

# Step 6: Start MongoDB and restore database
$script:CurrentStep++
Write-Step "Starting MongoDB and restoring database..."

# Start only MongoDB first
Write-Info "Starting MongoDB container..."
docker compose up -d mongodb

# Wait for MongoDB to be healthy
Write-Info "Waiting for MongoDB to be healthy..."
$maxWait = 120
$waited = 0
$healthy = $false

while ($waited -lt $maxWait) {
    try {
        $health = docker inspect --format='{{.State.Health.Status}}' rag-mongodb 2>&1
        if ($health -eq "healthy") {
            $healthy = $true
            break
        }
    } catch {
        # Container might not exist yet
    }
    Start-Sleep -Seconds 3
    $waited += 3
    Write-Host "." -NoNewline -ForegroundColor Gray
}
Write-Host ""

if (-not $healthy) {
    Write-Warning "MongoDB health check timeout, attempting to continue..."
}

# Restore database from mongodump archive
if (-not $SkipDatabaseRestore) {
    $mongoArchive = Join-Path $script:ProjectRoot "data\mongoDB\migration_backup\rag_db_export.archive"
    
    if (Test-Path $mongoArchive) {
        Write-Info "Restoring MongoDB database from archive..."
        Write-Info "This may take several minutes for large databases..."
        
        try {
            # Get database name from manifest or default
            $dbName = "rag_db"
            if ($manifest -and $manifest.Components.MongoDB.Database) {
                $dbName = $manifest.Components.MongoDB.Database
            }
            
            docker exec rag-mongodb mongorestore --archive=/data/db/migration_backup/rag_db_export.archive --gzip --drop
            Write-Success "Database restored successfully"
        } catch {
            Write-ErrorMsg "Database restore failed: $_"
            Write-Info "The raw data files may still work. Continuing..."
        }
    } else {
        Write-Info "No mongodump archive found"
        Write-Info "Using raw MongoDB data files from data/mongoDB/"
        
        # Verify raw data exists
        $mongoDataPath = Join-Path $script:ProjectRoot "data\mongoDB\db"
        if (Test-Path $mongoDataPath) {
            $dataFiles = Get-ChildItem $mongoDataPath -Recurse -File
            if ($dataFiles.Count -gt 0) {
                Write-Success "Raw MongoDB data files present ($($dataFiles.Count) files)"
            }
        }
    }
} else {
    Write-Info "Skipping database restore (--SkipDatabaseRestore flag)"
}

# Step 7: Start all services
$script:CurrentStep++
Write-Step "Starting all RecallHub services..."

Write-Info "Building and starting containers..."
docker compose up -d --build

Write-Info "Waiting for services to initialize..."
Start-Sleep -Seconds 10

# Check container status
$containers = docker compose ps --format json 2>&1 | ConvertFrom-Json -ErrorAction SilentlyContinue
if ($containers) {
    Write-Info "Container status:"
    foreach ($container in $containers) {
        $status = if ($container.State -eq "running") { "Running" } else { $container.State }
        $color = if ($container.State -eq "running") { "Green" } else { "Yellow" }
        Write-Host "    $($container.Name): $status" -ForegroundColor $color
    }
}

Write-Success "Services started"

# Step 8: Verify installation
$script:CurrentStep++
Write-Step "Verifying installation..."

if (-not $SkipVerification) {
    $verificationPassed = $true
    
    # Check MongoDB connection
    Write-Info "Testing MongoDB connection..."
    try {
        $mongoTest = docker exec rag-mongodb mongosh --eval "db.adminCommand('ping')" --quiet 2>&1
        if ($mongoTest -match '"ok"\s*:\s*1') {
            Write-Success "MongoDB connection: OK"
        } else {
            Write-Warning "MongoDB connection test inconclusive"
        }
    } catch {
        Write-Warning "Could not verify MongoDB connection"
        $verificationPassed = $false
    }
    
    # Check document count
    Write-Info "Checking database contents..."
    try {
        $docCount = docker exec rag-mongodb mongosh --eval "db.getSiblingDB('rag_db').documents.countDocuments()" --quiet 2>&1
        if ($docCount -match '^\d+$') {
            Write-Success "Documents collection: $docCount documents"
        }
        
        $chunkCount = docker exec rag-mongodb mongosh --eval "db.getSiblingDB('rag_db').chunks.countDocuments()" --quiet 2>&1
        if ($chunkCount -match '^\d+$') {
            Write-Success "Chunks collection: $chunkCount chunks"
        }
    } catch {
        Write-Warning "Could not verify database contents"
    }
    
    # Check backend API
    Write-Info "Testing backend API..."
    Start-Sleep -Seconds 5  # Give backend time to fully start
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:11000/api/v1/system/health" -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -eq 200) {
            Write-Success "Backend API: OK"
        }
    } catch {
        Write-Warning "Backend API not responding yet (may still be starting)"
        $verificationPassed = $false
    }
    
    # Check frontend
    Write-Info "Testing frontend..."
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:11080" -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -eq 200) {
            Write-Success "Frontend: OK"
        }
    } catch {
        Write-Warning "Frontend not responding yet (may still be starting)"
        $verificationPassed = $false
    }
    
} else {
    Write-Info "Skipping verification (--SkipVerification flag)"
}

# Final summary
Write-Host @"

===============================================
   MIGRATION IMPORT COMPLETE!
===============================================

"@ -ForegroundColor Green

Write-Host "  Installation Path: $script:ProjectRoot" -ForegroundColor White
Write-Host ""
Write-Host "  ACCESS YOUR APPLICATION:" -ForegroundColor Yellow
Write-Host "    Frontend:     http://localhost:11080" -ForegroundColor Cyan
Write-Host "    Backend API:  http://localhost:11000/docs" -ForegroundColor Cyan
Write-Host "    MongoDB:      localhost:11017" -ForegroundColor Cyan
Write-Host ""
Write-Host "  USEFUL COMMANDS:" -ForegroundColor Yellow
Write-Host "    View logs:    docker compose logs -f" -ForegroundColor Gray
Write-Host "    Stop:         docker compose down" -ForegroundColor Gray
Write-Host "    Restart:      docker compose restart" -ForegroundColor Gray
Write-Host ""

# Cleanup migration files
$cleanupManifest = Join-Path $script:ProjectRoot "MIGRATION_MANIFEST.json"
$cleanupBackup = Join-Path $script:ProjectRoot "data\mongoDB\migration_backup"

Write-Host "  CLEANUP (optional):" -ForegroundColor Yellow
Write-Host "    Remove manifest:  Remove-Item '$cleanupManifest'" -ForegroundColor Gray
Write-Host "    Remove backup:    Remove-Item '$cleanupBackup' -Recurse" -ForegroundColor Gray
Write-Host ""

if ($verificationPassed) {
    Write-Host "Migration completed successfully!" -ForegroundColor Green
} else {
    Write-Host "Migration completed with warnings. Services may need a moment to fully start." -ForegroundColor Yellow
    Write-Host "Try accessing the application in 30-60 seconds." -ForegroundColor Gray
}

Write-Host ""
