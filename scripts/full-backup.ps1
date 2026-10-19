#Requires -Version 5.1
<#
.SYNOPSIS
    RecallHub/Quellex Per-Tenant Full System Backup Script
    Creates a comprehensive per-tenant backup for migrating to a different Windows PC.

.DESCRIPTION
    This script creates a complete per-tenant backup including:
    1. Tenant-specific MongoDB databases (plus shared databases)
    2. External data folders (e.g. D:\Test_Data) — RecallHub only by default
    3. Full source code (excluding build artifacts)
    4. All configuration files with path mappings for restore
    5. Optional Docker image export (tenant-filtered)
    6. Optional Ollama model export

.PARAMETER Tenant
    The tenant to back up. Determines which databases, images, and config are included.

.PARAMETER OutputPath
    Directory where the backup folder/ZIP will be created.

.PARAMETER TestDataPath
    Path to the external Test_Data folder to include.

.PARAMETER IncludeDockerImages
    Export Docker images (backend, frontend, worker, mongodb) into the backup.

.PARAMETER IncludeModels
    Export Ollama models (gemma4:26b, gemma4:e4b, nomic-embed-text) into the backup.

.PARAMETER All
    Convenience switch that implies -IncludeDockerImages and -IncludeModels.

.PARAMETER SkipTestData
    Skip backing up the Test_Data folder.

.PARAMETER SkipCompress
    Leave backup as a folder instead of compressing to ZIP.

.EXAMPLE
    .\full-backup.ps1 -Tenant recallhub
    .\full-backup.ps1 -Tenant quellex -OutputPath "F:\Backups" -IncludeDockerImages
    .\full-backup.ps1 -Tenant recallhub -All
    .\full-backup.ps1 -Tenant quellex -All -SkipCompress
    .\full-backup.ps1 -Tenant recallhub -SkipTestData -SkipCompress
#>

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("quellex", "recallhub")]
    [string]$Tenant,
    [string]$OutputPath = "",
    [string]$TestDataPath = "",
    [switch]$IncludeDockerImages,
    [switch]$IncludeModels,
    [switch]$All,
    [switch]$SkipTestData,
    [switch]$SkipCompress
)

# -All implies both image and model export
if ($All) {
    $IncludeDockerImages = $true
    $IncludeModels = $true
}

$ErrorActionPreference = "Continue"
$ProgressPreference    = "Continue"

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $script:RepoRoot) { $script:RepoRoot = (Get-Location).Path }

# Defaults that do not assume the dev machine's drive letters
if (-not $OutputPath) { $OutputPath = Join-Path $script:RepoRoot "Hub_Backups" }
if (-not $TestDataPath) {
    foreach ($cand in @("C:\Test_Data", "D:\Test_Data", "D:\Test_data")) { if (Test-Path $cand) { $TestDataPath = $cand; break } }
}

# Early drive validation — must run before Join-Path uses OutputPath
$outputDrive = Split-Path -Qualifier $OutputPath
if (-not (Test-Path $outputDrive)) {
    Write-Host "`n  [ERROR] Output drive $outputDrive does not exist. Please specify a valid -OutputPath." -ForegroundColor Red
    exit 1
}

$script:Version    = "0.8.8"
$pyprojectPath = Join-Path $script:RepoRoot "pyproject.toml"
if (Test-Path $pyprojectPath) {
    $vm = [regex]::Match((Get-Content $pyprojectPath -Raw), '(?m)^version\s*=\s*"([^"]+)"')
    if ($vm.Success) { $script:Version = $vm.Groups[1].Value }
}
$script:Timestamp  = (Get-Date).ToString("yyyyMMdd_HHmmss")
# Capitalize first letter for folder name
$script:TenantLabel = $Tenant.Substring(0,1).ToUpper() + $Tenant.Substring(1)
$script:BackupName = "${script:TenantLabel}_v${script:Version}_${script:Timestamp}"
$script:BackupDir  = Join-Path $OutputPath $script:BackupName
$script:Errors     = @()
$script:CurrentStep = 0
$script:TotalSteps  = 10

# Per-tenant database configuration
$script:SharedDbs = @("admin", "recallhub")
$script:TenantDbs = @{
    "quellex"   = @("rag_diejuristin")
    "recallhub" = @("rag_db", "rag_test_law")
}

# Per-tenant config values (.env keys; docker-compose.yml reads QUELLEX_* for backend-quellex)
$script:TenantConfig = @{
    "quellex" = @{
        ACTIVE_PROFILE           = "diejuristin"
        MONGODB_DATABASE         = "rag_diejuristin"
        QUELLEX_ACTIVE_PROFILE   = "diejuristin"
        QUELLEX_MONGODB_DATABASE = "rag_diejuristin"
    }
    "recallhub" = @{
        ACTIVE_PROFILE   = "default"
        MONGODB_DATABASE = "rag_db"
    }
}

function Write-Step  { param([string]$Msg) $script:CurrentStep++; Write-Host "`n[$script:CurrentStep/$script:TotalSteps] $Msg" -ForegroundColor Cyan }
function Write-OK    { param([string]$Msg) Write-Host "  [OK] $Msg" -ForegroundColor Green }
function Write-Warn  { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Err   { param([string]$Msg) Write-Host "  [ERROR] $Msg" -ForegroundColor Red; $script:Errors += $Msg }
function Write-Info  { param([string]$Msg) Write-Host "  $Msg" -ForegroundColor Gray }
function Write-Ts    { Write-Host "  $(Get-Date -Format 'HH:mm:ss') " -NoNewline -ForegroundColor DarkGray }

# Profiles in profiles.yaml with their database and owning tenant: the profile's
# "tenant:" field, else the legacy owner of a known key, else "" (unassigned, shared).
# Same rules as src/profile.py (LEGACY_PROFILE_TENANTS); the copy in
# full-restore.ps1 must stay identical (docs/27-TENANT_ISOLATION.md).
function Get-ProfileTenants {
    param([string]$Path)
    $legacy = @{ "diejuristin" = "quellex"; "parhelion" = "recallhub"; "parhelion-full" = "recallhub" }
    $result = @()
    if (-not $Path -or -not (Test-Path $Path)) { return $result }
    $inProfiles = $false
    $current = $null
    foreach ($line in (Get-Content $Path -Encoding UTF8)) {
        if ($line -match '^profiles:\s*$') { $inProfiles = $true; continue }
        if ($line -match '^\S') { $inProfiles = $false; continue }
        if (-not $inProfiles) { continue }
        if ($line -match '^  ([^\s#][^:]*):\s*$') {
            $current = [pscustomobject]@{ Key = $Matches[1].Trim().Trim("'", '"'); Database = ""; Tenant = "" }
            $result += $current
            continue
        }
        if (-not $current) { continue }
        if ($line -match '^    database:\s*(.+?)\s*$') { $current.Database = $Matches[1].Trim("'", '"') }
        elseif ($line -match '^    tenant:\s*(.+?)\s*$') { $current.Tenant = $Matches[1].Trim("'", '"').ToLower() }
    }
    foreach ($p in $result) {
        if (-not $p.Tenant -and $legacy.ContainsKey($p.Key)) { $p.Tenant = $legacy[$p.Key] }
        if (-not $p.Database) { $p.Database = "rag_db" }
    }
    return $result
}

Write-Host @"

=============================================
   $script:TenantLabel Full System Backup
   Version $script:Version
=============================================

"@ -ForegroundColor Magenta

Write-Host "  Tenant:       $script:TenantLabel" -ForegroundColor Gray
Write-Host "  Repo Root:    $script:RepoRoot"    -ForegroundColor Gray
Write-Host "  Output Path:  $OutputPath"          -ForegroundColor Gray
Write-Host "  Backup Name:  $script:BackupName"   -ForegroundColor Gray
Write-Host "  Test Data:    $(if ($SkipTestData) { 'SKIP' } elseif ($Tenant -eq 'quellex' -and -not $TestDataPath) { 'SKIP (quellex)' } else { $TestDataPath })" -ForegroundColor Gray
Write-Host "  Docker Imgs:  $(if ($IncludeDockerImages) { 'YES' } else { 'NO' })"   -ForegroundColor Gray
Write-Host "  Ollama Models:$(if ($IncludeModels) { ' YES' } else { ' NO' })"        -ForegroundColor Gray
Write-Host ""

# Step 1: Validate environment
Write-Step "Validating environment..."

try {
    $dv = docker --version 2>&1
    Write-OK "Docker: $dv"
} catch {
    Write-Err "Docker is not installed or not in PATH"
    exit 1
}

try {
    $dockerInfoOutput = docker info 2>&1 | Out-String
    if ($dockerInfoOutput -match "Server Version") {
        Write-OK "Docker daemon is running"
    } else {
        throw "Docker daemon not responding"
    }
} catch {
    Write-Err "Docker daemon is not running. Start Docker Desktop first."
    exit 1
}

if (-not (Test-Path (Join-Path $script:RepoRoot "docker-compose.yml"))) {
    Write-Err "docker-compose.yml not found at $script:RepoRoot"
    exit 1
}

if (-not (Test-Path $OutputPath)) {
    New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null
    Write-OK "Created output directory: $OutputPath"
}

$outDrive = Split-Path $OutputPath -Qualifier
$disk = Get-PSDrive -Name ($outDrive -replace ":", "")
$freeGB = [math]::Round($disk.Free / 1GB, 2)
Write-Info "Free space on ${outDrive}: $freeGB GB"
if ($freeGB -lt 10) {
    Write-Warn "Less than 10 GB free on $outDrive - backup may fail."
}
Write-OK "Environment validated"

# Step 2: Create backup directory structure
Write-Step "Creating backup directory structure..."

New-Item -ItemType Directory -Path $script:BackupDir -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $script:BackupDir "mongodb") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $script:BackupDir "config")  -Force | Out-Null
Write-OK "Backup directory: $script:BackupDir"
@{ tenant = $Tenant; version = $script:Version; status = "in-progress"; backup_date = (Get-Date).ToString("o"); source_machine = $env:COMPUTERNAME } |
    ConvertTo-Json | Set-Content -Path (Join-Path $script:BackupDir "manifest.json") -Encoding UTF8

# Step 3: MongoDB per-tenant dump
Write-Step "Exporting MongoDB databases for $script:TenantLabel..."

Set-Location $script:RepoRoot

Write-Info "Ensuring MongoDB container is running..."
$mongoRunning = ((docker inspect -f '{{.State.Running}}' rag-mongodb 2>$null | Out-String).Trim() -eq 'true')
if ($mongoRunning) {
    Write-Info "rag-mongodb is already running - using it as-is"
} else {
    try {
        docker compose --profile $Tenant up -d mongodb 2>&1 | Out-Null
    } catch {
        try { docker compose up -d mongodb 2>&1 | Out-Null } catch { }
    }
}

Write-Info "Waiting for MongoDB to become healthy..."
$maxWait = 90
$waited  = 0
while ($waited -lt $maxWait) {
    $svcHealth = docker compose --profile $Tenant exec -T mongodb mongosh --eval "db.adminCommand('ping')" --quiet 2>&1 | Out-String
    if ($svcHealth -match 'ok\s*:\s*1') { break }
    Start-Sleep -Seconds 3
    $waited += 3
    Write-Host "." -NoNewline -ForegroundColor Gray
}
Write-Host ""
if ($waited -ge $maxWait) {
    Write-Warn "Health-check timed out after ${maxWait}s - attempting dump anyway..."
}

# Build database list: shared DBs + the static tenant table + every database of a
# profile in profiles.yaml that this tenant owns or that is unassigned, and that
# actually exists. docker-compose.yml may pin a tenant to a different profile than
# the table (e.g. backend-quellex -> test-law), and a package that misses the live
# database restores an empty tenant. Profiles owned by the other tenant are left
# out: restoring them with this package would overwrite that tenant's data.
$dbsToDump = $script:SharedDbs + $script:TenantDbs[$Tenant]
$existingRaw = docker exec rag-mongodb mongosh --quiet --eval "db.adminCommand('listDatabases').databases.map(d=>d.name).join(',')" 2>&1 | Out-String
$existing = @((($existingRaw.Trim() -split "`n" | Select-Object -Last 1).Trim()) -split "," | Where-Object { $_ })
$profilesYaml = Join-Path $script:RepoRoot "profiles.yaml"
# The profile this tenant's backend is pinned to in .env is always included
$pinnedProfile = ""
$envFile = Join-Path $script:RepoRoot ".env"
if (Test-Path $envFile) {
    $pinLine = Get-Content $envFile | Where-Object { $_ -match "^$($Tenant.ToUpper())_ACTIVE_PROFILE=" } | Select-Object -Last 1
    if ($pinLine) { $pinnedProfile = ($pinLine -split "=", 2)[1].Trim().Trim("'", '"') }
}
foreach ($p in (Get-ProfileTenants -Path $profilesYaml)) {
    if ($p.Tenant -and ($p.Tenant -ne $Tenant) -and ($p.Key -eq $pinnedProfile)) {
        Write-Warn "Profile '$($p.Key)' is owned by tenant $($p.Tenant) but pinned for $Tenant in .env; including it"
    } elseif ($p.Tenant -and ($p.Tenant -ne $Tenant)) {
        Write-Info "Skipping profile '$($p.Key)' ($($p.Database)): owned by tenant $($p.Tenant)"
        continue
    }
    if (($existing -contains $p.Database) -and ($dbsToDump -notcontains $p.Database)) { $dbsToDump += $p.Database }
}

# Personal databases (backend/agent/federated_search.py): user_rag_<tenant>_<user id>
# and cloud_<tenant>_<user id>; RecallHub also keeps legacy user_rag_<email local part>.
$personalDbs = @($existing | Where-Object {
    ($_ -like "user_rag_${Tenant}_*") -or ($_ -like "cloud_${Tenant}_*") -or
    (($Tenant -eq "recallhub") -and ($_ -like "user_rag_*") -and ($_ -notlike "user_rag_quellex_*"))
})
if ($personalDbs.Count -gt 0) {
    Write-Info "Found personal databases: $($personalDbs -join ', ')"
    foreach ($udb in $personalDbs) { if ($dbsToDump -notcontains $udb) { $dbsToDump += $udb } }
}

if ($existing.Count -gt 0) {
    $dbsToDump = $dbsToDump | Where-Object { ($existing -contains $_) -or ($script:SharedDbs -contains $_) }
} else {
    Write-Warn "Could not list MongoDB databases; dumping the configured list unfiltered"
}

$dbList = $dbsToDump
Write-Info "Databases to dump: $($dbsToDump -join ', ')"

Write-Ts; Write-Host "Running mongodump (per-database for $script:TenantLabel)..." -ForegroundColor White
try {
    $network = $null
    try {
        $networkRaw = docker inspect rag-mongodb --format "{{range `$key, `$val := .NetworkSettings.Networks}}{{`$key}}{{end}}" 2>&1
        if ($LASTEXITCODE -eq 0 -and $networkRaw) {
            $network = ($networkRaw | Out-String).Trim()
        }
    } catch { }
    if (-not $network) { $network = "recallhub_rag-network" }
    Write-Info "Using Docker network: $network"

    $dumpContainer = "recallhub-mongodump-temp"
    docker rm -f $dumpContainer 2>$null | Out-Null

    # Start temp container on MongoDB's network
    docker run -d --name $dumpContainer --network $network mongo:8.0 sleep 300 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to start mongodump container"
        throw "Container start failed"
    }

    # Dump each database individually inside container (fast ext4, no VirtioFS)
    Write-Info "Dumping to container filesystem (bypasses VirtioFS)..."
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $dumpErrors = 0
    foreach ($dbName in $dbsToDump) {
        Write-Info "  Dumping database: $dbName"
        $dumpOutput = docker exec $dumpContainer mongodump --host=mongodb --port=27017 --db=$dbName --out=/dump --gzip --numParallelCollections=4 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "  Failed to dump $dbName (may not exist)"
            $dumpErrors++
        }
    }
    $dumpMs = $sw.ElapsedMilliseconds
    Write-Info "Dump completed in $([math]::Round($dumpMs / 1000, 1))s ($($dbsToDump.Count - $dumpErrors)/$($dbsToDump.Count) successful)"

    # docker cp from container to host (tar streaming, 10-50x faster than bind mount)
    $mongoBackupDir = Join-Path $script:BackupDir "mongodb"
    if (-not (Test-Path $mongoBackupDir)) { New-Item -ItemType Directory -Path $mongoBackupDir -Force | Out-Null }
    Write-Info "Copying dump from container to host via docker cp..."
    $sw.Restart()
    docker cp "${dumpContainer}:/dump" "$mongoBackupDir/dump"
    $copyMs = $sw.ElapsedMilliseconds
    Write-Info "Copy completed in $([math]::Round($copyMs / 1000, 1))s"

    # Cleanup
    docker rm -f $dumpContainer 2>$null | Out-Null

    # Report size
    $dumpPath = Join-Path $mongoBackupDir "dump"
    if (Test-Path $dumpPath) {
        $totalSize = (Get-ChildItem $dumpPath -Recurse -File | Measure-Object -Property Length -Sum).Sum
        $sizeMB = [math]::Round($totalSize / 1MB, 1)
        $totalSec = [math]::Round(($dumpMs + $copyMs) / 1000, 1)
        $speedMBs = if ($totalSec -gt 0) { [math]::Round($sizeMB / $totalSec, 1) } else { 0 }
        Write-OK "MongoDB dump: ${sizeMB} MB (gzipped, $($dbsToDump.Count) databases) in ${totalSec}s (${speedMBs} MB/s)"
    } else {
        Write-Err "MongoDB dump directory not found after copy"
    }
} catch {
    Write-Err "MongoDB dump failed: $_"
    docker rm -f $dumpContainer 2>$null | Out-Null
}

# Step 4: Copy Test_Data (RecallHub includes by default; Quellex only if -TestDataPath explicitly provided)
Write-Step "Backing up external Test_Data..."

$testDataIncluded = $false
$includeTestData = ($Tenant -eq "recallhub") -or ($TestDataPath -and (Test-Path $TestDataPath))
if ($SkipTestData) {
    Write-Info "Skipped (-SkipTestData flag)"
} elseif (-not $includeTestData) {
    Write-Info "Skipped (not included for $script:TenantLabel unless -TestDataPath is provided)"
} elseif (-not (Test-Path $TestDataPath)) {
    Write-Warn "Test_Data path not found: $TestDataPath - skipping"
} else {
    $tdDest = Join-Path $script:BackupDir "Test_Data"
    Write-Ts; Write-Host "Copying $TestDataPath ..." -ForegroundColor White
    $null = robocopy $TestDataPath $tdDest /E /MT:8 /NFL /NDL /NJH /NJS /NC /NS
    if ($LASTEXITCODE -ge 8) {
        Write-Warn "Robocopy returned exit code $LASTEXITCODE - some files may be missing"
    } else {
        $tdSizeMB = [math]::Round(((Get-ChildItem $tdDest -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
        Write-OK "Test_Data: $tdSizeMB MB"
        $testDataIncluded = $true
    }
}

# Step 5: Copy configuration files
Write-Step "Backing up configuration files..."

$configFiles = @(
    ".env",
    ".env.docker",
    ".env.example",
    "docker-compose.yml",
    "docker-compose.override.yml",
    "docker-compose.dev.yml",
    "docker-compose.local.yml",
    "docker-compose.quellex.yml",
    "profiles.yaml",
    "backend\profiles.yaml",
    "backend\requirements.txt",
    "backend\requirements-api.txt",
    "backend\requirements-ml-heavy.txt",
    "pyproject.toml",
    "uv.lock"
)

$configDest = Join-Path $script:BackupDir "config"
foreach ($cf in $configFiles) {
    $src = Join-Path $script:RepoRoot $cf
    if (Test-Path $src) {
        $dest = Join-Path $configDest $cf
        $destDir = Split-Path $dest -Parent
        if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
        Copy-Item $src $dest -Force
        Write-Info "  + $cf"
    }
}
Write-OK "Configuration files copied"

# Update .env in backup with tenant-specific values
$envBackupPath = Join-Path $configDest ".env"
if (Test-Path $envBackupPath) {
    Write-Info "Updating .env with $script:TenantLabel tenant config..."
    $envContent = Get-Content $envBackupPath -Raw
    $tenantCfg = $script:TenantConfig[$Tenant]
    foreach ($key in $tenantCfg.Keys) {
        $value = $tenantCfg[$key]
        if ($envContent -match "(?m)^${key}=.*") {
            $envContent = $envContent -replace "(?m)^${key}=.*", "${key}=${value}"
        } else {
            $envContent = $envContent.TrimEnd() + "`n${key}=${value}`n"
        }
    }
    Set-Content -Path $envBackupPath -Value $envContent -Encoding UTF8 -NoNewline
    Write-OK ".env updated for $script:TenantLabel (ACTIVE_PROFILE=$($tenantCfg['ACTIVE_PROFILE']), MONGODB_DATABASE=$($tenantCfg['MONGODB_DATABASE']))"
}

# Update profiles.yaml active_profile for tenant
$profilesBackupPath = Join-Path $configDest "profiles.yaml"
if (Test-Path $profilesBackupPath) {
    Write-Info "Updating profiles.yaml active_profile for $script:TenantLabel..."
    $profilesContent = Get-Content $profilesBackupPath -Raw
    $targetProfile = $script:TenantConfig[$Tenant]['ACTIVE_PROFILE']
    $profilesContent = $profilesContent -replace "(?m)^active_profile:.*", "active_profile: $targetProfile"
    Set-Content -Path $profilesBackupPath -Value $profilesContent -Encoding UTF8 -NoNewline
    Write-OK "profiles.yaml active_profile set to '$targetProfile'"
}

# Step 6: Copy document folders
Write-Step "Backing up document and project folders..."

$docFolders = @("documents", "projects")
foreach ($folder in $docFolders) {
    $src = Join-Path $script:RepoRoot $folder
    if (Test-Path $src) {
        $dest = Join-Path $script:BackupDir $folder
        $null = robocopy $src $dest /E /MT:8 /NFL /NDL /NJH /NJS /NC /NS
        $fSizeMB = [math]::Round(((Get-ChildItem $dest -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
        Write-OK "${folder}: $fSizeMB MB"
    } else {
        Write-Info "$folder folder not found - skipped"
    }
}

# Step 7: Copy full source code
Write-Step "Backing up source code..."

$srcDest = Join-Path $script:BackupDir "source"
Write-Ts; Write-Host "Copying source tree (excluding build artifacts)..." -ForegroundColor White

$xdArgs = @(
    "/XD", "node_modules",
    "/XD", ".venv",
    "/XD", "venv",
    "/XD", "__pycache__",
    "/XD", ".git",
    "/XD", ".uv-cache",
    "/XD", ".pytest_cache",
    "/XD", ".mypy_cache",
    "/XD", "db",
    "/XD", "configdb",
    "/XD", "dist",
    "/XD", "obj",
    "/XD", ".coverage",
    "/XD", "mounts",
    "/XD", "data",
    "/XD", "$($script:RepoRoot)\ingestion"
)

$null = robocopy $script:RepoRoot $srcDest /E /MT:8 /NFL /NDL /NJH /NJS /NC /NS @xdArgs /XF "*.pyc" "*.pyo"
if ($LASTEXITCODE -ge 8) {
    Write-Warn "Robocopy exit code $LASTEXITCODE - some source files may be missing"
} else {
    $srcSizeMB = [math]::Round(((Get-ChildItem $srcDest -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
    Write-OK "Source code: $srcSizeMB MB"
}
foreach ($critical in @("docker-compose.yml", "backend\main.py", "scripts\full-restore.ps1", "scripts\INSTALL.ps1", "installer\scripts\Start-Services.ps1", "installer\scripts\Write-TrayConfig.ps1")) {
    if (-not (Test-Path (Join-Path $srcDest $critical))) { Write-Err "Source copy is missing $critical" }
}

# Step 8: Optional Docker image export
Write-Step "Docker image export..."

$dockerImagesIncluded = $false
$exportedImageList = @()
if ($IncludeDockerImages) {
    $imgDir = Join-Path $script:BackupDir "images"
    $exportScript = Join-Path $PSScriptRoot "export-docker-images.ps1"

    if (Test-Path $exportScript) {
        Write-Info "Using export-docker-images.ps1 for comprehensive image export (tenant: $Tenant)..."
        try {
            & $exportScript -OutputPath $imgDir -Tenant $Tenant -IncludeBase
            # Check if any tar files were created
            $tarFiles = Get-ChildItem $imgDir -Filter "*.tar" -File -ErrorAction SilentlyContinue
            if ($tarFiles -and $tarFiles.Count -gt 0) {
                $totalImgSize = ($tarFiles | Measure-Object -Property Length -Sum).Sum
                $totalImgGB = [math]::Round($totalImgSize / 1GB, 2)
                Write-OK "Docker images exported: $($tarFiles.Count) files, $totalImgGB GB"
                $dockerImagesIncluded = $true
                $exportedImageList = $tarFiles | ForEach-Object { $_.Name }
            } else {
                Write-Warn "No images were exported - build images first"
            }
        } catch {
            Write-Warn "Image export via export-docker-images.ps1 failed: $_"
        }
    } else {
        # Inline fallback when export-docker-images.ps1 is not available
        Write-Info "export-docker-images.ps1 not found - using inline export..."
        New-Item -Path $imgDir -ItemType Directory -Force | Out-Null

        $requiredImages = @(
            @{ Image = "recallhub-backend-base:latest";       File = "backend-base.tar" },
            @{ Image = "recallhub-backend:latest";            File = "backend.tar" },
            @{ Image = "recallhub-ingestion-worker:latest";   File = "ingestion-worker.tar" },
            @{ Image = "recallhub-frontend:latest";           File = "frontend.tar" },
            @{ Image = "recallhub-frontend-quellex:latest";   File = "frontend-quellex.tar" },
            @{ Image = "mongodb/mongodb-atlas-local:8.0";     File = "mongodb-atlas-local.tar" },
            @{ Image = "caddy:2.10-alpine";                   File = "caddy.tar" }
        )

        foreach ($img in $requiredImages) {
            $imageName = $img.Image
            $outputFile = Join-Path $imgDir $img.File

            # Check if image exists locally
            $inspectOut = docker image inspect $imageName 2>&1 | Out-String
            if ($LASTEXITCODE -ne 0) {
                Write-Info "  [SKIP] $imageName not found locally"
                continue
            }

            Write-Info "  Exporting $imageName ..."
            try {
                docker save -o $outputFile $imageName
                if ($LASTEXITCODE -ne 0) { throw "docker save exit code $LASTEXITCODE" }
                $tarSize = (Get-Item $outputFile).Length
                $sizeDisplay = if ($tarSize -ge 1GB) { "$([math]::Round($tarSize / 1GB, 2)) GB" } else { "$([math]::Round($tarSize / 1MB, 1)) MB" }
                Write-OK "  $($img.File) ($sizeDisplay)"
                $exportedImageList += $img.File
            } catch {
                Write-Warn "  Failed to export ${imageName}: $_"
            }
        }

        # Summary
        $tarFiles = Get-ChildItem $imgDir -Filter "*.tar" -File -ErrorAction SilentlyContinue
        if ($tarFiles -and $tarFiles.Count -gt 0) {
            $totalImgSize = ($tarFiles | Measure-Object -Property Length -Sum).Sum
            $totalImgGB = [math]::Round($totalImgSize / 1GB, 2)
            Write-OK "Exported $($tarFiles.Count) images ($totalImgGB GB total)"
            $dockerImagesIncluded = $true
        } else {
            Write-Warn "No images were exported - build images first"
        }
    }
} else {
    Write-Info "Skipped (use -IncludeDockerImages or -All to include)"
}

# Step 9: Optional Ollama model export
Write-Step "Ollama model export..."

$modelsIncluded = $false
$exportedModelList = @()
if ($IncludeModels) {
    $modelsDir = Join-Path $script:BackupDir "models"
    $modelsScript = Join-Path $PSScriptRoot "backup-ollama-models.ps1"

    if (Test-Path $modelsScript) {
        Write-Info "Using backup-ollama-models.ps1 for model export..."
        try {
            & $modelsScript -OutputPath $modelsDir
            # Verify blobs were copied
            $blobsOut = Join-Path $modelsDir "blobs"
            if ((Test-Path $blobsOut) -and (Get-ChildItem $blobsOut -File -ErrorAction SilentlyContinue)) {
                $blobFiles = Get-ChildItem $blobsOut -File
                $totalModelSize = ($blobFiles | Measure-Object -Property Length -Sum).Sum
                $totalModelGB = [math]::Round($totalModelSize / 1GB, 2)
                Write-OK "Ollama models exported: $($blobFiles.Count) blobs, $totalModelGB GB"
                $modelsIncluded = $true
                # Determine which models were exported from manifests
                $manifestsOut = Join-Path $modelsDir "manifests"
                if (Test-Path $manifestsOut) {
                    $manifestFiles = Get-ChildItem $manifestsOut -Recurse -File -ErrorAction SilentlyContinue
                    foreach ($mf in $manifestFiles) {
                        $relPath = $mf.FullName.Substring($manifestsOut.Length + 1) -replace '\\', '/'
                        # manifests/registry.ollama.ai/library/<name>/<tag>
                        if ($relPath -match 'library/([^/]+)/([^/]+)$') {
                            $exportedModelList += "$($Matches[1]):$($Matches[2])"
                        }
                    }
                }
            } else {
                Write-Warn "No model blobs were exported - check Ollama installation"
            }
        } catch {
            Write-Warn "Model export failed: $_"
        }
    } else {
        # Inline fallback: copy blobs and manifests directly
        Write-Info "backup-ollama-models.ps1 not found - using inline export..."
        $ollamaBase = if ($env:OLLAMA_MODELS -and (Test-Path $env:OLLAMA_MODELS)) {
            Split-Path $env:OLLAMA_MODELS -Parent
        } elseif (Test-Path "$env:USERPROFILE\.ollama") {
            "$env:USERPROFILE\.ollama"
        } else { $null }

        if ($ollamaBase) {
            $srcBlobs = Join-Path (Join-Path $ollamaBase "models") "blobs"
            $srcManifests = Join-Path (Join-Path $ollamaBase "models") "manifests"

            if ((Test-Path $srcBlobs) -and (Test-Path $srcManifests)) {
                New-Item -Path $modelsDir -ItemType Directory -Force | Out-Null
                $destBlobs = Join-Path $modelsDir "blobs"
                $destManifests = Join-Path $modelsDir "manifests"

                Write-Info "  Copying model manifests..."
                $null = robocopy $srcManifests $destManifests /E /MT:4 /NFL /NDL /NJH /NJS /NC /NS

                Write-Info "  Copying model blobs (this may take a while)..."
                $null = robocopy $srcBlobs $destBlobs /E /MT:4 /NFL /NDL /NJH /NJS /NC /NS

                $blobFiles = Get-ChildItem $destBlobs -File -ErrorAction SilentlyContinue
                if ($blobFiles -and $blobFiles.Count -gt 0) {
                    $totalModelSize = ($blobFiles | Measure-Object -Property Length -Sum).Sum
                    $totalModelGB = [math]::Round($totalModelSize / 1GB, 2)
                    Write-OK "Ollama models exported: $($blobFiles.Count) blobs, $totalModelGB GB"
                    $modelsIncluded = $true
                    $exportedModelList = @("all-local-models")
                } else {
                    Write-Warn "No model blobs found in $srcBlobs"
                }
            } else {
                Write-Warn "Ollama model directories not found at $ollamaBase"
            }
        } else {
            Write-Warn "Ollama installation not found - cannot export models"
        }
    }
} else {
    Write-Info "Skipped (use -IncludeModels or -All to include)"
}

# Step 10: Create manifest and compress
Write-Step "Creating manifest and finalizing..."

$pathMappings = @{}
$overridePath = Join-Path $script:RepoRoot "docker-compose.override.yml"
if (Test-Path $overridePath) {
    $overrideContent = Get-Content $overridePath -Raw
    $volumePattern = '^\s*-\s+"?([A-Za-z]:/[^:"]+):/app/'
    $volumeMatches = [regex]::Matches($overrideContent, $volumePattern, [System.Text.RegularExpressions.RegexOptions]::Multiline)
    foreach ($m in $volumeMatches) {
        $hostPath = $m.Groups[1].Value
        if ($pathMappings.Keys -notcontains $hostPath) {
            $pathMappings[$hostPath] = "REQUIRES_REMAPPING"
        }
    }
}

$manifestData = @{
    tenant                 = $Tenant
    version                = $script:Version
    backup_date            = (Get-Date).ToString("o")
    source_machine         = $env:COMPUTERNAME
    source_repo_path       = ($script:RepoRoot -replace '\\', '/')
    databases_included     = if ($dbList.Count -gt 0) { $dbList } else { @("all") }
    test_data_included     = $testDataIncluded
    test_data_source_path  = ($TestDataPath -replace '\\', '/')
    includeDockerImages    = [bool]$IncludeDockerImages
    docker_images_included = $dockerImagesIncluded
    docker_images_list     = $exportedImageList
    includeModels          = [bool]$IncludeModels
    models_included        = $modelsIncluded
    models_list            = $exportedModelList
    path_mappings          = $pathMappings
    errors                 = $script:Errors
    restore_instructions    = "Run INSTALL.ps1 from this folder (tenant is read from this manifest)"
}

# Completeness check - a package that fails this must not be shipped
$missing = @()
if (-not (Test-Path (Join-Path $script:BackupDir "source\docker-compose.yml"))) { $missing += "source/docker-compose.yml" }
if (-not (Test-Path (Join-Path $script:BackupDir "config\.env"))) { $missing += "config/.env" }
$dumpCheck = Join-Path $script:BackupDir "mongodb\dump"
if (-not ((Test-Path $dumpCheck) -and (Get-ChildItem $dumpCheck -Directory -ErrorAction SilentlyContinue))) { $missing += "mongodb/dump" }
if ($IncludeDockerImages -and -not $dockerImagesIncluded) { $missing += "images/*.tar" }
if ($IncludeModels -and -not $modelsIncluded) { $missing += "models/blobs" }
$packageComplete = ($missing.Count -eq 0) -and ($script:Errors.Count -eq 0)
$manifestData.status   = if ($packageComplete) { "complete" } else { "incomplete" }
$manifestData.complete = $packageComplete
$manifestData.missing  = $missing

$manifestJson = $manifestData | ConvertTo-Json -Depth 10
$manifestFile = Join-Path $script:BackupDir "manifest.json"
Set-Content -Path $manifestFile -Value $manifestJson -Encoding UTF8
Write-OK "manifest.json created"

# Copy essential scripts to backup root for easy access on target machine
$rootScripts = @("INSTALL.ps1", "UNINSTALL.ps1", "full-restore.ps1")
foreach ($rs in $rootScripts) {
    $rsSrc = Join-Path $PSScriptRoot $rs
    if (Test-Path $rsSrc) {
        $rsDest = Join-Path $script:BackupDir $rs
        Copy-Item $rsSrc -Destination $rsDest -Force
        Write-Info "  + $rs (backup root)"
    }
}
Write-OK "Root-level scripts copied"

$zipPath = $null
if (-not $SkipCompress) {
    $zipPath = Join-Path $OutputPath "$script:BackupName.zip"
    Write-Ts; Write-Host "Compressing backup to ZIP (this may take a while)..." -ForegroundColor White
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
        [System.IO.Compression.ZipFile]::CreateFromDirectory(
            $script:BackupDir,
            $zipPath,
            [System.IO.Compression.CompressionLevel]::Optimal,
            $false
        )
        $zipSizeGB = [math]::Round((Get-Item $zipPath).Length / 1GB, 2)
        Write-OK "ZIP archive: $zipPath ($zipSizeGB GB)"
    } catch {
        Write-Warn "ZIP compression failed: $_ - backup folder is still available"
        $zipPath = $null
    }
}

# Summary
Write-Host @"

=============================================
   BACKUP COMPLETE
=============================================

"@ -ForegroundColor Green

Write-Host "  Backup Folder:  $script:BackupDir"                             -ForegroundColor White
if ($zipPath -and (Test-Path $zipPath)) {
    Write-Host "  ZIP Archive:    $zipPath"                                   -ForegroundColor White
}
Write-Host "  Timestamp:      $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"     -ForegroundColor White
Write-Host ""

$components = @(
    @{ Label = "MongoDB ($($dbsToDump.Count) databases)"; Status = if ($script:Errors | Where-Object { $_ -match "MongoDB" }) { "WARN" } else { "OK" } },
    @{ Label = "Test_Data";                Status = if ($testDataIncluded) { "OK" } elseif ($SkipTestData) { "SKIP" } else { "WARN" } },
    @{ Label = "Configuration files";      Status = "OK" },
    @{ Label = "Documents and Projects";   Status = "OK" },
    @{ Label = "Source code";              Status = "OK" },
    @{ Label = "Docker images";            Status = if ($dockerImagesIncluded) { "OK" } elseif (-not $IncludeDockerImages) { "SKIP" } else { "WARN" } },
    @{ Label = "Ollama models";            Status = if ($modelsIncluded) { "OK" } elseif (-not $IncludeModels) { "SKIP" } else { "WARN" } }
)

foreach ($c in $components) {
    $icon  = switch ($c.Status) { "OK" { "[OK]" } "SKIP" { "[--]" } default { "[!!]" } }
    $color = switch ($c.Status) { "OK" { "Green" } "SKIP" { "DarkGray" } default { "Yellow" } }
    Write-Host "  $icon $($c.Label)" -ForegroundColor $color
}

Write-Host ""
Write-Host "  RESTORE ON NEW MACHINE:" -ForegroundColor Yellow
Write-Host "    .\scripts\full-restore.ps1 -BackupPath `"$script:BackupDir`" -Tenant $Tenant -TestDataPath C:\Test_Data" -ForegroundColor Gray
Write-Host ""

if (-not $packageComplete) {
    Write-Host "  PACKAGE INCOMPLETE - do not ship this folder." -ForegroundColor Red
    foreach ($m in $missing) { Write-Host "    missing: $m" -ForegroundColor Red }
    foreach ($e in $script:Errors) { Write-Host "    error:   $e" -ForegroundColor Red }
} else {
    Write-Host "  Backup completed successfully - package is complete." -ForegroundColor Green
}

Write-Host ""
if (-not $IncludeModels) {
    Write-Host "  INCLUDE OLLAMA MODELS (optional, for offline restore):" -ForegroundColor Yellow
    Write-Host "    .\full-backup.ps1 -Tenant $Tenant -IncludeModels" -ForegroundColor Gray
    Write-Host "    Or standalone: .\scripts\backup-ollama-models.ps1 -OutputPath `"$script:BackupDir\models`"" -ForegroundColor Gray
    Write-Host ""
}
Write-Host "  INCLUDE INSTALLERS (optional, for fully offline setup):" -ForegroundColor Yellow
Write-Host "    Copy DockerDesktopInstaller.exe and OllamaSetup.exe into:" -ForegroundColor Gray
Write-Host "    $script:BackupDir\apps\" -ForegroundColor Gray
Write-Host ""
if (-not $IncludeDockerImages) {
    Write-Host "  INCLUDE DOCKER IMAGES (optional, for fully offline deployment):" -ForegroundColor Yellow
    Write-Host "    .\full-backup.ps1 -Tenant $Tenant -IncludeDockerImages" -ForegroundColor Gray
    Write-Host "    Or standalone: .\scripts\export-docker-images.ps1 -OutputPath `"$script:BackupDir\images`" -Tenant $Tenant" -ForegroundColor Gray
    Write-Host ""
}
if (-not $All) {
    Write-Host "  FULL OFFLINE BACKUP (include everything):" -ForegroundColor Yellow
    Write-Host "    .\full-backup.ps1 -Tenant $Tenant -All" -ForegroundColor Gray
    Write-Host ""
}
if (-not $packageComplete) { exit 1 }
exit 0
