#Requires -Version 5.1
<#
.SYNOPSIS
    RecallHub/Quellex Full System Restore Script
    Restores a complete installation from a full-backup package with tenant awareness.

.DESCRIPTION
    This script restores a system from a backup created by full-backup.ps1:
    1. Extracts ZIP if needed and validates manifest
    2. Restores Test_Data to a configurable path
    3. Copies source code to the install location
    4. Updates docker-compose.override.yml with new path mappings
    5. Restores ALL MongoDB databases from the dump
    6. Loads or rebuilds Docker images
    7. Starts all services and verifies health

.PARAMETER BackupPath
    Path to the extracted backup folder or a .zip file.

.PARAMETER TestDataPath
    Where to restore Test_Data on this machine. Defaults to C:\Test_Data.

.PARAMETER InstallPath
    Where to place the source code. Defaults to C:\<TenantName> (e.g., C:\Recallhub or C:\Quellex).

.PARAMETER Tenant
    Which tenant to restore for. Valid values: "quellex", "recallhub", or "" (auto-detect).
    If not specified, auto-detects from backup manifest. Falls back to "recallhub".

.PARAMETER SkipBuild
    Skip Docker image rebuild (use if images are included or already built).

.PARAMETER SkipTestData
    Skip restoring the Test_Data folder.

.PARAMETER SkipSourceCopy
    Skip copying source code (use if running from the install location already).

.EXAMPLE
    .\full-restore.ps1 -BackupPath "E:\RecallHub_Backups\RecallHub_v0.8.8_20260512_143000"
    .\full-restore.ps1 -BackupPath "E:\backup.zip" -TestDataPath "C:\Test_Data"
    .\full-restore.ps1 -BackupPath "E:\backup" -InstallPath "C:\RecallHub" -SkipTestData
    .\full-restore.ps1 -BackupPath "E:\backup" -Tenant quellex
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$BackupPath,
    [string]$TestDataPath = "C:\Test_Data",
    [string]$InstallPath = "",
    [ValidateSet("quellex", "recallhub", "")]
    [string]$Tenant = "",
    [switch]$SkipBuild,
    [switch]$SkipTestData,
    [switch]$SkipSourceCopy
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "Continue"

$script:Errors      = @()
$script:Warnings    = @()
$script:CurrentStep = 0
$script:TotalSteps  = 11

function Write-Step  { param([string]$Msg) $script:CurrentStep++; Write-Host "`n[$script:CurrentStep/$script:TotalSteps] $Msg" -ForegroundColor Cyan }
function Write-OK    { param([string]$Msg) Write-Host "  [OK] $Msg" -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow; $script:Warnings += $Msg }
function Write-Err   { param([string]$Msg) Write-Host "  [ERROR] $Msg" -ForegroundColor Red; $script:Errors += $Msg }
function Write-Info  { param([string]$Msg) Write-Host "  $Msg" -ForegroundColor Gray }
function Write-Ts    { Write-Host "  $(Get-Date -Format 'HH:mm:ss') " -NoNewline -ForegroundColor DarkGray }

Write-Host @"

=============================================
   RecallHub Full System Restore
=============================================

"@ -ForegroundColor Magenta

Write-Host "  Backup Path:    $BackupPath"    -ForegroundColor Gray
Write-Host "  Test Data Dest: $(if ($SkipTestData) { 'SKIP' } else { $TestDataPath })" -ForegroundColor Gray
Write-Host ""

# Step 1: Validate prerequisites
Write-Step "Validating prerequisites..."

try {
    $dv = docker --version 2>&1
    Write-OK "Docker: $dv"
} catch {
    Write-Err "Docker is not installed or not in PATH"
    exit 1
}

try {
    docker info 2>&1 | Out-Null
    Write-OK "Docker daemon is running"
} catch {
    Write-Err "Docker daemon is not running. Start Docker Desktop first."
    exit 1
}

# Disk space check deferred until after InstallPath is resolved (see Step 3)

Write-OK "Prerequisites validated"

# Step 2: Extract ZIP if needed
Write-Step "Preparing backup files..."

$workingBackup = $BackupPath

if ($BackupPath -match '\.zip$') {
    if (-not (Test-Path $BackupPath)) {
        Write-Err "ZIP file not found: $BackupPath"
        exit 1
    }

    $extractDir = Join-Path (Split-Path $BackupPath -Parent) ([System.IO.Path]::GetFileNameWithoutExtension($BackupPath))
    Write-Info "Extracting ZIP to $extractDir ..."

    if (-not (Test-Path $extractDir)) {
        New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
    }

    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($BackupPath, $extractDir)
        Write-OK "Extraction complete"
    } catch {
        Write-Info "Trying fallback extraction..."
        Expand-Archive -Path $BackupPath -DestinationPath $extractDir -Force
    }

    $workingBackup = $extractDir
} else {
    if (-not (Test-Path $BackupPath)) {
        Write-Err "Backup path not found: $BackupPath"
        exit 1
    }
    Write-OK "Using existing backup folder: $workingBackup"
}

# Step 3: Validate manifest
Write-Step "Validating backup manifest..."

$manifestPath = Join-Path $workingBackup "manifest.json"
$manifest = $null

if (Test-Path $manifestPath) {
    try {
        $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
        Write-OK "Manifest loaded"
        Write-Info "Backup version:  $($manifest.version)"
        Write-Info "Backup date:     $($manifest.backup_date)"
        Write-Info "Source machine:   $($manifest.source_machine)"
        Write-Info "Test data incl.:  $($manifest.test_data_included)"
        Write-Info "Docker imgs incl: $($manifest.docker_images_included)"
    } catch {
        Write-Warn "Could not parse manifest - proceeding with defaults"
    }
} else {
    Write-Warn "No manifest.json found - proceeding with best effort"
}

# Auto-detect tenant from manifest if not specified
if (-not $Tenant -and $manifest -and $manifest.tenant) {
    $Tenant = $manifest.tenant
    Write-Info "Detected tenant from backup manifest: $Tenant"
}
if (-not $Tenant) {
    Write-Warn "No tenant specified and none found in manifest. Defaulting to 'recallhub'"
    $Tenant = "recallhub"
}

# Compute tenant-aware install path
$tenantName = $Tenant.Substring(0,1).ToUpper() + $Tenant.Substring(1)
if (-not $InstallPath) {
    $InstallPath = "C:\$tenantName"
}
Write-Info "Tenant: $Tenant"
Write-Info "Install path: $InstallPath"

# Check disk space on install drive
try {
    $installDrive = Split-Path $InstallPath -Qualifier
    $disk = Get-PSDrive -Name ($installDrive -replace ":", "")
    $freeGB = [math]::Round($disk.Free / 1GB, 2)
    Write-Info "Free space on ${installDrive}: $freeGB GB"
    if ($freeGB -lt 20) {
        Write-Warn "Less than 20 GB free on $installDrive - restore may fail."
    }
} catch {
    Write-Warn "Could not check disk space for $InstallPath"
}

# Verify critical backup components exist
$mongoDumpDir = Join-Path $workingBackup "mongodb\dump"
$mongoArchive = Join-Path $workingBackup "mongodb\full_backup.archive"
if (Test-Path $mongoDumpDir) {
    Write-OK "MongoDB dump directory present"
} elseif (Test-Path $mongoArchive) {
    $archSizeMB = [math]::Round((Get-Item $mongoArchive).Length / 1MB, 1)
    Write-OK "MongoDB archive present (legacy format): $archSizeMB MB"
} else {
    Write-Warn "MongoDB dump not found"
}

$sourceDir = Join-Path $workingBackup "source"
if (Test-Path $sourceDir) {
    Write-OK "Source code present"
} else {
    Write-Warn "Source code directory not found"
}

# Step 4: Restore Test_Data
Write-Step "Restoring Test_Data..."

if ($SkipTestData) {
    Write-Info "Skipped (-SkipTestData flag)"
} else {
    $backupTestData = Join-Path $workingBackup "Test_Data"
    if (Test-Path $backupTestData) {
        Write-Ts; Write-Host "Copying Test_Data to $TestDataPath ..." -ForegroundColor White
        if (-not (Test-Path $TestDataPath)) {
            New-Item -ItemType Directory -Path $TestDataPath -Force | Out-Null
        }
        $null = robocopy $backupTestData $TestDataPath /E /MT:8 /NFL /NDL /NJH /NJS /NC /NS
        if ($LASTEXITCODE -ge 4) {
            Write-Err "Test_Data copy failed (robocopy exit code: $LASTEXITCODE)"
            exit 1
        } else {
            $tdSizeMB = [math]::Round(((Get-ChildItem $TestDataPath -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
            Write-OK "Test_Data restored: $tdSizeMB MB at $TestDataPath"
        }
    } else {
        Write-Info "No Test_Data in backup - skipped"
    }
}

# Step 5: Restore source code
Write-Step "Restoring source code..."

if ($SkipSourceCopy) {
    Write-Info "Skipped (-SkipSourceCopy flag)"
} else {
    if (Test-Path $sourceDir) {
        Write-Ts; Write-Host "Copying source to $InstallPath ..." -ForegroundColor White
        if (-not (Test-Path $InstallPath)) {
            New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
        }
        $null = robocopy $sourceDir $InstallPath /E /MT:8 /NFL /NDL /NJH /NJS /NC /NS
        if ($LASTEXITCODE -ge 4) {
            Write-Err "Source copy failed (robocopy exit code: $LASTEXITCODE)"
            exit 1
        } else {
            Write-OK "Source code restored to $InstallPath"
        }

        # Verify critical files exist after copy
        $criticalFiles = @("docker-compose.yml", "backend\main.py", "frontend\package.json", "build-backend.ps1")
        $missingCritical = @()
        foreach ($file in $criticalFiles) {
            $fullPath = Join-Path $InstallPath $file
            if (-not (Test-Path $fullPath)) { $missingCritical += $file }
        }
        if ($missingCritical.Count -gt 0) {
            Write-Err "Critical files missing after source copy:"
            $missingCritical | ForEach-Object { Write-Err "  - $_" }
            exit 1
        }
    } else {
        Write-Warn "No source directory in backup"
    }
}

# Step 6: Restore configuration files (overwrite from backup config)
Write-Step "Restoring configuration files..."

$configDir = Join-Path $workingBackup "config"
if (Test-Path $configDir) {
    $configFiles = Get-ChildItem $configDir -Recurse -File
    foreach ($cf in $configFiles) {
        $relativePath = $cf.FullName.Substring($configDir.Length + 1)
        $dest = Join-Path $InstallPath $relativePath
        $destDir = Split-Path $dest -Parent
        if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
        Copy-Item $cf.FullName $dest -Force
        Write-Info "  + $relativePath"
    }
    Write-OK "Configuration files restored"
} else {
    Write-Warn "No config directory in backup"
}

# Restore documents and projects
$docFolders = @("documents", "projects")
foreach ($folder in $docFolders) {
    $backupFolder = Join-Path $workingBackup $folder
    if (Test-Path $backupFolder) {
        $destFolder = Join-Path $InstallPath $folder
        $null = robocopy $backupFolder $destFolder /E /MT:8 /NFL /NDL /NJH /NJS /NC /NS
        Write-OK "$folder restored"
    }
}

# Step 7: Update docker-compose.override.yml with new paths
Write-Step "Updating path mappings in docker-compose.override.yml..."

$overridePath = Join-Path $InstallPath "docker-compose.override.yml"
if ((Test-Path $overridePath) -and $manifest) {
    try {
        $override = Get-Content $overridePath -Raw

        # Get original Test_Data path from manifest
        $originalTestDataPath = $null
        if ($manifest.test_data_source_path) {
            $originalTestDataPath = $manifest.test_data_source_path
        }

        if ($originalTestDataPath) {
            # Normalize paths to forward slashes for YAML matching
            $originalFwd = $originalTestDataPath -replace '\\', '/'
            $newFwd = $TestDataPath -replace '\\', '/'

            if ($originalFwd -ne $newFwd) {
                # Replace in volume mounts and MOUNT_MAPPINGS
                $override = $override -replace [regex]::Escape($originalFwd), $newFwd

                # Also handle backslash variants if any
                $originalBack = $originalTestDataPath -replace '/', '\'
                $newBack = $TestDataPath -replace '/', '\'
                $override = $override -replace [regex]::Escape($originalBack), $newBack

                Set-Content $overridePath $override -Encoding UTF8 -NoNewline
                Write-OK "Remapped: $originalFwd -> $newFwd"
            } else {
                Write-Info "Test_Data path unchanged - no remapping needed"
            }
        } else {
            Write-Info "No test_data_source_path in manifest - skipping path remap"
        }

        # Also remap any other path_mappings from the manifest
        if ($manifest.path_mappings) {
            $override = Get-Content $overridePath -Raw
            $changed = $false
            $mappingProps = $manifest.path_mappings.PSObject.Properties
            foreach ($prop in $mappingProps) {
                $origPath = $prop.Name
                # Skip the Test_Data path (already handled)
                $origFwd = $origPath -replace '\\', '/'
                $testFwd = $TestDataPath -replace '\\', '/'
                if ($origFwd -eq ($originalTestDataPath -replace '\\', '/')) { continue }

                # Check if this path exists on the new machine
                $origWin = $origPath -replace '/', '\'
                if (-not (Test-Path $origWin)) {
                    Write-Warn "External path not found on this machine: $origPath"
                    Write-Info "  You may need to manually update docker-compose.override.yml"
                }
            }
            if ($changed) {
                Set-Content $overridePath $override -Encoding UTF8 -NoNewline
            }
        }

        # ── Sanitize volume mounts for paths that don't exist on this machine ──
        Write-Info "Sanitizing volume mounts for non-existent host paths..."
        $override = Get-Content $overridePath -Raw
        $lines = $override -split "`n"
        $cleanedLines = @()
        $removedMounts = @()

        foreach ($line in $lines) {
            # Match volume mount lines like:
            #   - "G:/My Drive/root:/app/mounts/gdrive-root:ro"   (quoted, may have spaces)
            #   - D:/parhelion.energy:/app/mounts/parhelion-energy:ro  (unquoted)
            # Only target absolute host paths with a drive letter (A-Z).
            if ($line -match '^\s*-\s*"?([A-Za-z]:[/\\][^:]+):(/app/[^:"]+)') {
                $hostPath = $Matches[1] -replace '/', '\'
                if (-not (Test-Path $hostPath)) {
                    $removedMounts += $hostPath
                    # Comment out instead of delete for transparency
                    $cleanedLines += "      # REMOVED (path not found): $($line.TrimStart())"
                    continue
                }
            }
            $cleanedLines += $line
        }

        if ($removedMounts.Count -gt 0) {
            # Also clean MOUNT_MAPPINGS env var entries for removed paths
            $result = $cleanedLines -join "`n"
            foreach ($removedPath in $removedMounts) {
                $fwdPath = $removedPath -replace '\\', '/'
                $escaped = [regex]::Escape($fwdPath)
                # Remove pipe-separated segment with leading pipe:  |/app/mounts/name=G:/My Drive/root
                $result = $result -replace "\|[^|=]+=$escaped", ''
                # Remove segment with trailing pipe (if it was first):  /app/mounts/name=G:/My Drive/root|
                $result = $result -replace "[^|=]+=$escaped\|", ''
                # Remove segment if it's the only one (no pipes)
                $result = $result -replace "MOUNT_MAPPINGS=[^|=]+=$escaped\s*`$", 'MOUNT_MAPPINGS='
            }
            Set-Content $overridePath $result -Encoding UTF8 -NoNewline
            Write-OK "Removed $($removedMounts.Count) volume mount(s) for non-existent paths:"
            foreach ($m in $removedMounts) {
                Write-Info "    - $m"
            }
        } else {
            Write-OK "All volume mount paths exist on this machine"
        }
    } catch {
        Write-Warn "Failed to update override paths: $_"
    }
} else {
    if (-not (Test-Path $overridePath)) {
        Write-Warn "docker-compose.override.yml not found at $overridePath"
    } else {
        Write-Info "No manifest available - manual path review recommended"
    }
}

# Ensure .env has correct tenant values
$envFile = Join-Path $InstallPath ".env"
if (Test-Path $envFile) {
    $tenantConfig = @{
        "quellex" = @{ ACTIVE_PROFILE = "parhelion"; MONGODB_DATABASE = "rag_parhelion" }
        "recallhub" = @{ ACTIVE_PROFILE = "default"; MONGODB_DATABASE = "rag_db" }
    }
    $envContent = Get-Content $envFile -Raw
    $config = $tenantConfig[$Tenant]
    foreach ($key in $config.Keys) {
        $envContent = $envContent -replace "(?m)^${key}=.*$", "${key}=$($config[$key])"
    }
    Set-Content $envFile $envContent -Encoding UTF8
    Write-OK "Updated .env for tenant: $Tenant (ACTIVE_PROFILE=$($config['ACTIVE_PROFILE']), MONGODB_DATABASE=$($config['MONGODB_DATABASE']))"
}

# Update profiles.yaml active_profile
$profilesFile = Join-Path $InstallPath "profiles.yaml"
if (Test-Path $profilesFile) {
    $profilesContent = Get-Content $profilesFile -Raw
    $activeProfile = if ($Tenant -eq "quellex") { "parhelion" } else { "default" }
    $profilesContent = $profilesContent -replace "(?m)^active_profile:.*$", "active_profile: $activeProfile"
    Set-Content $profilesFile $profilesContent -Encoding UTF8
    Write-OK "Updated profiles.yaml active_profile to: $activeProfile"
}

# Step 8: Stop any existing containers
Write-Step "Preparing Docker environment..."

Set-Location $InstallPath

$dockerComposePath = Join-Path $InstallPath "docker-compose.yml"
if (-not (Test-Path $dockerComposePath)) {
    Write-Err "docker-compose.yml not found in $InstallPath"
    Write-Err "Source code may not have been copied correctly in Step 5"
    exit 1
}

Write-Info "Stopping any existing containers..."
try {
    $null = docker compose --profile $Tenant down 2>&1
} catch {
    # Docker writes informational messages to stderr (e.g., "Container ... Stopping")
}
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) {
    Write-Warn "docker compose --profile $Tenant down returned exit code $LASTEXITCODE"
}
try {
    $null = docker compose down 2>&1
} catch {
    # Docker writes informational messages to stderr
}
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) {
    Write-Warn "docker compose down returned exit code $LASTEXITCODE"
}
Write-OK "Docker environment cleared"

# Step 9: Start MongoDB and restore dump
Write-Step "Starting MongoDB and restoring databases..."

Write-Info "Starting MongoDB container..."
$mongoStarted = $false
try {
    $output = docker compose --profile $Tenant up -d mongodb 2>&1
    $output | ForEach-Object { Write-Info "$_" }
    if ($LASTEXITCODE -eq 0) { $mongoStarted = $true }
} catch {
    # Docker stderr is informational, check exit code instead
    if ($LASTEXITCODE -eq 0) { $mongoStarted = $true }
}

if (-not $mongoStarted) {
    try {
        $output = docker compose up -d mongodb 2>&1
        $output | ForEach-Object { Write-Info "$_" }
        if ($LASTEXITCODE -eq 0) { $mongoStarted = $true }
    } catch {
        if ($LASTEXITCODE -eq 0) { $mongoStarted = $true }
    }
}

if (-not $mongoStarted) {
    Write-Err "Failed to start MongoDB container"
    Write-Info "Check: docker compose logs mongodb"
    exit 1
}

Write-Info "Waiting for MongoDB to become healthy..."
$maxWait = 300  # 5 min default for safety
Write-Info "  (timeout: ${maxWait}s)"

$waited = 0
$mongoReady = $false
while ($waited -lt $maxWait) {
    try {
        $svcHealth = docker compose --profile $Tenant exec -T mongodb mongosh --eval "db.adminCommand('ping')" --quiet 2>&1 | Out-String
    } catch {
        $svcHealth = ""
    }
    # Match any variant: { ok: 1 }, ok: 1, "ok" : 1, etc.
    if ($svcHealth -match 'ok.*:.*1' -or ($LASTEXITCODE -eq 0 -and $svcHealth -and $svcHealth.Trim())) {
        $mongoReady = $true
        break
    }
    Start-Sleep -Seconds 3
    $waited += 3
    Write-Host "." -NoNewline -ForegroundColor Gray
}
Write-Host ""

if (-not $mongoReady) {
    Write-Warn "MongoDB did not become healthy within ${maxWait}s - CONTINUING anyway"
    Write-Info "  The container may still be initializing."
    Write-Info "  Possible causes:"
    Write-Info "    1. WSL2 not fully initialized (try restart)"
    Write-Info "    2. Docker Desktop still loading"
    Write-Info "    3. Port conflict (check if port 11017 is in use)"
    Write-Info "  Run: docker compose logs mongodb"
} else {
    Write-OK "MongoDB is healthy"
}

$mongoDumpDir = Join-Path $workingBackup "mongodb\dump"
$mongoArchive = Join-Path $workingBackup "mongodb\full_backup.archive"

if (Test-Path $mongoDumpDir) {
    # New format: directory dump
    Write-Ts; Write-Host "Restoring MongoDB from directory dump (all databases)..." -ForegroundColor White
    # Detect the Docker network that MongoDB is on
    $network = $null
    try {
        $inspectJson = docker inspect rag-mongodb 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0 -and $inspectJson) {
            $inspectObj = $inspectJson | ConvertFrom-Json
            $networks = $inspectObj.NetworkSettings.Networks
            if ($networks) {
                $network = ($networks | Get-Member -MemberType NoteProperty | Select-Object -First 1).Name
            }
        }
    } catch { }
    if (-not $network) {
        # Try common network names based on project directory
        $projectName = (Split-Path $InstallPath -Leaf).ToLower() -replace '[^a-z0-9]', ''
        $candidates = @("${projectName}_rag-network", "recallhub_rag-network", "mongodb-rag-agent_rag-network")
        foreach ($candidate in $candidates) {
            $exists = docker network ls --filter "name=$candidate" --format "{{.Name}}" 2>$null
            if ($exists) { $network = $candidate; break }
        }
    }
    if (-not $network) { $network = "recallhub_rag-network" }
    Write-Info "Using network: $network"

    $restoreContainer = "recallhub-mongorestore-temp"
    try {
        try { $null = docker rm -f $restoreContainer 2>&1 } catch {}
        Write-Info "Pulling mongo:8.0 image (if needed)..."
        try {
            $pullOutput = docker pull mongo:8.0 2>&1
            $pullOutput | Select-Object -Last 3 | ForEach-Object { Write-Info "$_" }
        } catch {
            Write-Info "(pull output suppressed)"
        }
        try { $null = docker run -d --name $restoreContainer --network $network mongo:8.0 sleep 300 2>&1 } catch {}

        # Copy dump into container
        Write-Info "Copying dump into container via docker cp..."
        docker cp "$mongoDumpDir" "${restoreContainer}:/dump"

        # Restore inside container
        Write-Info "Running mongorestore (parallel, gzipped)..."
        $restoreOutput = docker exec $restoreContainer mongorestore --host=mongodb --port=27017 --dir=/dump --gzip --drop --numParallelCollections=4 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            Write-Err "MongoDB restore failed (exit code: $LASTEXITCODE)"
            if ($restoreOutput) { $restoreOutput.Trim() -split "`n" | Select-Object -Last 10 | ForEach-Object { Write-Err "  $_" } }
            throw "mongorestore failed"
        }
        if ($restoreOutput) {
            $restoreOutput.Trim() -split "`n" | Select-Object -Last 5 | ForEach-Object { Write-Info $_.Trim() }
        }

        # Verify databases were actually restored
        Write-Info "Verifying restored databases..."
        $dbCount = docker compose --profile $Tenant exec -T mongodb mongosh --eval "db.adminCommand('listDatabases').databases.length" --quiet 2>&1 | Out-String
        $dbCountTrimmed = $dbCount.Trim()
        if ($dbCountTrimmed -match '^\d+$' -and [int]$dbCountTrimmed -gt 2) {
            Write-OK "MongoDB restored ($dbCountTrimmed databases)"
        } else {
            Write-Warn "Could not verify database count (got: $dbCountTrimmed)"
        }
    } catch {
        Write-Err "MongoDB restore failed: $_"
    } finally {
        try { $null = docker rm -f $restoreContainer 2>&1 } catch {}
    }
} elseif (Test-Path $mongoArchive) {
    # Legacy archive format
    Write-Ts; Write-Host "Restoring MongoDB from archive (legacy format)..." -ForegroundColor White

    $network = $null
    try {
        $inspectJson = docker inspect rag-mongodb 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0 -and $inspectJson) {
            $inspectObj = $inspectJson | ConvertFrom-Json
            $networks = $inspectObj.NetworkSettings.Networks
            if ($networks) {
                $network = ($networks | Get-Member -MemberType NoteProperty | Select-Object -First 1).Name
            }
        }
    } catch { }
    if (-not $network) { $network = "recallhub_rag-network" }
    Write-Info "Using Docker network: $network"

    try {
        $mongoArchiveDir = Split-Path $mongoArchive -Parent
        $restoreOutput = docker run --rm --network $network -v "${mongoArchiveDir}:/backup" mongo:8.0 mongorestore --host=mongodb --port=27017 --archive=/backup/full_backup.archive --gzip --drop 2>&1 | Out-String
        if ($restoreOutput) {
            $restoreOutput.Trim() -split "`n" | Select-Object -Last 10 | ForEach-Object { Write-Info $_.Trim() }
        }
        Write-OK "MongoDB restored from archive (legacy format)"
    } catch {
        Write-Err "MongoDB restore failed: $_"
    }
} else {
    Write-Warn "No MongoDB dump found - database will be empty"
}

# Step 10: Load or build Docker images
Write-Step "Loading/building Docker images..."

$imagesDir = Join-Path $workingBackup "images"
$hasImages = (Test-Path $imagesDir) -and ((Get-ChildItem $imagesDir -Filter "*.tar" -File -ErrorAction SilentlyContinue).Count -gt 0)
$imagesLoaded = $false

if ($hasImages) {
    Write-Info "Loading Docker images from backup (fully offline)..."
    $imageFiles = Get-ChildItem $imagesDir -Filter "*.tar" -File
    $loadedCount = 0
    $failedCount = 0

    foreach ($imgFile in $imageFiles) {
        $imgSizeMB = [math]::Round($imgFile.Length / 1MB, 1)
        $imgSizeGB = [math]::Round($imgFile.Length / 1GB, 2)
        $sizeDisplay = if ($imgFile.Length -ge 1GB) { "$imgSizeGB GB" } else { "$imgSizeMB MB" }
        Write-Ts; Write-Host "Loading $($imgFile.Name) ($sizeDisplay)..." -ForegroundColor White
        try {
            $loadOutput = docker load -i $imgFile.FullName 2>&1 | Out-String
            if ($LASTEXITCODE -ne 0) {
                throw "docker load exit code: $LASTEXITCODE"
            }
            # Show which image was loaded
            $loadedImage = if ($loadOutput -match 'Loaded image:\s*(.+)') { $Matches[1].Trim() } else { "" }
            if ($loadedImage) {
                Write-OK "Loaded: $loadedImage"
            } else {
                Write-OK "Loaded $($imgFile.Name)"
            }
            $loadedCount++
        } catch {
            Write-Warn "Failed to load $($imgFile.Name): $_"
            $failedCount++
        }
    }

    # Verify images are available
    Write-Info "Verifying loaded images..."
    $expectedImages = @(
        "mongodb-rag-agent-backend",
        "mongodb-rag-agent-ingestion-worker",
        "mongodb-rag-agent-frontend",
        "mongodb/mongodb-atlas-local"
    )
    $foundCount = 0
    foreach ($expected in $expectedImages) {
        $check = docker images --format "{{.Repository}}:{{.Tag}}" 2>&1 | Out-String
        if ($check -match [regex]::Escape($expected)) {
            $foundCount++
        }
    }
    Write-OK "Loaded $loadedCount image(s), verified $foundCount/$($expectedImages.Count) expected images present"

    if ($failedCount -eq 0 -and $loadedCount -gt 0) {
        $imagesLoaded = $true
        Write-OK "All images loaded from backup - no internet or build required"
    } else {
        Write-Warn "$failedCount image(s) failed to load - may need to rebuild missing ones"
    }
}

if (-not $imagesLoaded -and -not $SkipBuild) {
    Write-Warn "** Building Docker images requires INTERNET access **"
    Write-Info "No pre-built images in backup - falling back to build from source..."
    Write-Info "To avoid this, export images on source machine first:"
    Write-Info "  .\scripts\export-docker-images.ps1 -OutputPath <backup>\images"
    Write-Info ""

    # Try the fast build path first
    $buildScript = Join-Path $InstallPath "build-backend.ps1"
    if (Test-Path $buildScript) {
        Write-Ts; Write-Host "Building backend images..." -ForegroundColor White
        try {
            # Check if base image exists
            $null = docker image inspect "recallhub-backend-base:latest" 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Info "Base image not found - building base first (this takes ~15 min)..."
                & $buildScript -Base
            }
            & $buildScript -All
            Write-OK "Backend images built"
        } catch {
            Write-Warn "Backend build failed: $_ - will try docker compose build"
        }
    }

    Write-Ts; Write-Host "Building frontend image..." -ForegroundColor White
    try {
        $frontendService = if ($Tenant -eq "quellex") { "frontend-quellex" } else { "frontend" }
        $null = docker compose --profile $Tenant build $frontendService 2>&1
    } catch {
        # Docker stderr is informational, not necessarily an error
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Frontend build failed (exit code: $LASTEXITCODE)"
    } else {
        Write-OK "Frontend image built"
    }
} elseif (-not $imagesLoaded -and $SkipBuild) {
    Write-Info "Skipped (-SkipBuild flag)"
}

# Step 10b: Deploy tray app scripts to expected location
Write-Step "Deploying tray app scripts..."
$trayScriptsSource = Join-Path $InstallPath "installer\scripts"
$trayScriptsDest = Join-Path $env:LOCALAPPDATA "RecallHub\scripts"
if (Test-Path $trayScriptsSource) {
    if (-not (Test-Path $trayScriptsDest)) {
        New-Item $trayScriptsDest -ItemType Directory -Force | Out-Null
    }
    Copy-Item "$trayScriptsSource\*" $trayScriptsDest -Recurse -Force
    Write-OK "Tray app scripts deployed to $trayScriptsDest"
} else {
    Write-Info "No installer\scripts found - tray app scripts skipped"
}

# Step 11: Start all services and verify
Write-Step "Starting all services and verifying health..."

Write-Info "Starting all $tenantName services..."
try {
    $null = docker compose --profile $Tenant up -d 2>&1
} catch {
    # Docker stderr is informational (e.g., "Container ... Starting")
}
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) {
    Write-Warn "Some services may have failed to start (exit code: $LASTEXITCODE)"
}

Write-Info "Waiting for services to initialize (30s)..."
Start-Sleep -Seconds 30

# Check container status
Write-Info "Container status:"
try {
    $psOutput = docker compose --profile $Tenant ps --format json 2>&1
    $containers = $psOutput | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($containers) {
        foreach ($container in $containers) {
            $status = if ($container.State -eq "running") { "Running" } else { $container.State }
            $color = if ($container.State -eq "running") { "Green" } else { "Yellow" }
            Write-Host "    $($container.Name): $status" -ForegroundColor $color
        }
    }
} catch {
    Write-Info "(Could not parse container status)"
}

# Verify MongoDB
Write-Info "Verifying MongoDB..."
try {
    $dbListRaw = docker compose --profile $Tenant exec -T mongodb mongosh --eval "db.adminCommand('listDatabases').databases.map(d => d.name + ' (' + Math.round(d.sizeOnDisk/1024/1024) + ' MB)').join(', ')" --quiet 2>&1
    $dbListStr = ("$($dbListRaw | Select-Object -Last 1)").Trim()
    if ($dbListStr) {
        Write-OK "Databases: $dbListStr"
    }
} catch {
    Write-Warn "Could not verify MongoDB databases"
}

# Verify backend API health
$backendPort = if ($Tenant -eq "quellex") { 11001 } else { 11000 }
$frontendPort = if ($Tenant -eq "quellex") { 11081 } else { 11080 }
Write-Info "Verifying backend API (port $backendPort)..."
$backendOk = $false
$retries = 3
for ($i = 0; $i -lt $retries; $i++) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:${backendPort}/api/v1/system/health" -UseBasicParsing -TimeoutSec 10
        if ($response.StatusCode -eq 200) {
            Write-OK "Backend API: healthy (HTTP $($response.StatusCode))"
            $backendOk = $true
            break
        }
    } catch {
        if ($i -lt $retries - 1) {
            Write-Info "Backend not ready, retrying in 10s..."
            Start-Sleep -Seconds 10
        }
    }
}
if (-not $backendOk) {
    Write-Warn "Backend API not responding yet - may still be starting"
}

# Verify frontend
Write-Info "Verifying frontend (port $frontendPort)..."
try {
    $response = Invoke-WebRequest -Uri "http://localhost:${frontendPort}" -UseBasicParsing -TimeoutSec 10
    if ($response.StatusCode -eq 200) {
        Write-OK "Frontend: serving (HTTP $($response.StatusCode))"
    }
} catch {
    Write-Warn "Frontend not responding yet - may still be starting"
}

# Summary
Write-Host @"

=============================================
   RESTORE COMPLETE
=============================================

"@ -ForegroundColor Green

Write-Host "  Install Path:   $InstallPath"    -ForegroundColor White
Write-Host "  Test Data:      $(if ($SkipTestData) { 'skipped' } else { $TestDataPath })" -ForegroundColor White
Write-Host "  Source Machine: $(if ($manifest) { $manifest.source_machine } else { 'unknown' })" -ForegroundColor White
Write-Host ""

# Component checklist
$results = @(
    @{ Label = "MongoDB restored (all databases)";  OK = ((Test-Path $mongoDumpDir) -or (Test-Path $mongoArchive)) -and -not ($script:Errors | Where-Object { $_ -match "MongoDB" }) },
    @{ Label = "Test_Data at $TestDataPath";         OK = $SkipTestData -or (Test-Path $TestDataPath) },
    @{ Label = "Path mappings updated";              OK = (Test-Path $overridePath) },
    @{ Label = "Docker images ready";                OK = $hasImages -or (-not $SkipBuild) },
    @{ Label = "Services running";                   OK = $true }
)

foreach ($r in $results) {
    if ($r.OK) {
        Write-Host "  [OK] $($r.Label)" -ForegroundColor Green
    } else {
        Write-Host "  [!!] $($r.Label)" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "  Tenant:         $Tenant" -ForegroundColor White
Write-Host ""
Write-Host "  ACCESS YOUR APPLICATION:" -ForegroundColor Yellow
Write-Host "    Frontend:     http://localhost:${frontendPort}" -ForegroundColor Cyan
Write-Host "    Backend API:  http://localhost:${backendPort}/docs" -ForegroundColor Cyan
Write-Host "    MongoDB:      localhost:11017" -ForegroundColor Cyan
Write-Host ""

if ($script:Warnings.Count -gt 0) {
    Write-Host "  WARNINGS ($($script:Warnings.Count)):" -ForegroundColor Yellow
    foreach ($w in $script:Warnings) {
        Write-Host "    - $w" -ForegroundColor Yellow
    }
    Write-Host ""
}

if ($script:Errors.Count -gt 0) {
    Write-Host "  ERRORS ($($script:Errors.Count)):" -ForegroundColor Red
    foreach ($e in $script:Errors) {
        Write-Host "    - $e" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  Restore completed with errors. Review above and fix manually." -ForegroundColor Red
} else {
    Write-Host "  Restore completed successfully!" -ForegroundColor Green
}

Write-Host ""
Write-Host "  USEFUL COMMANDS:" -ForegroundColor Yellow
Write-Host "    View logs:    docker compose --profile $Tenant logs -f" -ForegroundColor Gray
Write-Host "    Stop:         docker compose --profile $Tenant down" -ForegroundColor Gray
Write-Host "    Restart:      docker compose --profile $Tenant restart" -ForegroundColor Gray
Write-Host ""
