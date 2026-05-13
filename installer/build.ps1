<#
.SYNOPSIS
    RecallHub Hardened Installer Build Script

.DESCRIPTION
    One-click build script that creates the hardened Windows installer.
    
    This script:
    1. Copies source to a temporary build directory (doesn't touch your code)
    2. Applies hardening patches to the COPY
    3. Builds hardened Docker images with separate tags
    4. Exports images for bundling
    5. Builds the tray application
    6. Downloads Ollama models (optional)
    7. Creates the WSL2 distribution
    8. Packages everything into RecallHubSetup.exe
    9. Cleans up build artifacts
    
    Your development environment remains UNTOUCHED.

.PARAMETER Version
    Version number for the build (e.g., "1.0.0")

.PARAMETER SkipModels
    Skip downloading Ollama models (use existing if available)

.PARAMETER SkipDistro
    Skip creating WSL2 distribution (use existing if available)

.PARAMETER Clean
    Clean build artifacts before building

.PARAMETER Debug
    Keep intermediate build artifacts for debugging and enable verbose logging

.NOTES
    Requirements:
    - Docker Desktop with WSL2 backend
    - .NET 8 SDK (for tray app)
    - Inno Setup 6+ (for installer)
    - PowerShell 5.1+
#>

param(
    [string]$Version = "1.0.0",
    [switch]$SkipModels,
    [switch]$SkipDistro,
    [switch]$Clean,
    [switch]$Debug
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ---------------------------------------------------------------------------
# Import logging module
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptsDir = Join-Path $ScriptDir "scripts"
Import-Module (Join-Path $ScriptsDir "RecallHub-Logging.psm1") -Force

# Initialize with build-specific log name
$logFile = Initialize-InstallLog -LogName "build"
Write-InstallLog "Starting RecallHub installer build" -Source "Build"
Write-InstallLog "Build parameters: Version=$Version, SkipModels=$SkipModels, SkipDistro=$SkipDistro, Clean=$Clean, Debug=$Debug" -Level DEBUG -Source "Build"

if ($Debug) {
    $env:RECALLHUB_DEBUG = "1"
    Write-InstallLog "Debug mode enabled - verbose logging active" -Level DEBUG -Source "Build"
}

# ---------------------------------------------------------------------------
# Load build configuration
# ---------------------------------------------------------------------------
$ConfigPath = Join-Path $ScriptDir "config.json"
if (-not (Test-Path $ConfigPath)) {
    throw "Build configuration not found: $ConfigPath. Please create config.json in the installer directory."
}
$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
Write-InstallLog "Loaded configuration: $($Config.app.name) v$($Config.app.version)" -Source "Build"

# Override version from config if not explicitly passed
if ($Version -eq "1.0.0" -and $Config.app.version) {
    $Version = $Config.app.version
    Write-InstallLog "Using version from config.json: $Version" -Level DEBUG -Source "Build"
}

# Paths
$SCRIPT_DIR = $PSScriptRoot
$PROJECT_ROOT = Split-Path $SCRIPT_DIR -Parent
$BUILD_DIR = Join-Path $SCRIPT_DIR "build-temp"
$OUTPUT_DIR = Join-Path $SCRIPT_DIR "output"
$DISTRO_DIR = Join-Path $SCRIPT_DIR "distro"
$MODELS_DIR = Join-Path $SCRIPT_DIR "models"
$ASSETS_DIR = Join-Path $SCRIPT_DIR "assets"

# Image tags (from config)
$BACKEND_IMAGE = $Config.docker.images.backend.name
$FRONTEND_IMAGE = $Config.docker.images.frontend.name

# =============================================================================
# Helper Functions
# =============================================================================

function Write-Banner {
    param([string]$Message)
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor Magenta
    Write-Host "  $Message" -ForegroundColor Magenta
    Write-Host ("=" * 70) -ForegroundColor Magenta
    Write-Host ""
}

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
    Write-InstallLog $Message -Source "Build"
}

function Write-Success {
    param([string]$Message)
    Write-Host "    [OK] $Message" -ForegroundColor Green
    Write-InstallLog $Message -Source "Build"
}

function Write-BuildWarning {
    param([string]$Message)
    Write-Host "    [WARN] $Message" -ForegroundColor Yellow
    Write-InstallLog $Message -Level WARN -Source "Build"
}

function Write-BuildError {
    param([string]$Message)
    Write-Host "    [ERROR] $Message" -ForegroundColor Red
    Write-InstallLog $Message -Level ERROR -Source "Build"
}

function Test-BuildArtifact {
    param(
        [string]$Path,
        [string]$Name,
        [long]$MinimumSize = 1MB
    )
    
    if (-not (Test-Path $Path)) {
        Write-InstallDiagnostic -ErrorCode "BUILD-002" -Component "Artifacts" -Message "Build artifact missing: $Name" -Context @{ path = $Path }
        return $false
    }
    
    $size = (Get-Item $Path).Length
    Write-InstallLog "Artifact: $Name = $([math]::Round($size/1MB, 1)) MB" -Source "Build"
    
    if ($size -lt $MinimumSize) {
        Write-InstallDiagnostic -ErrorCode "BUILD-003" -Component "Artifacts" -Message "Artifact too small: $Name" -Context @{
            path = $Path
            actualSize = $size
            minimumSize = $MinimumSize
        }
        return $false
    }
    return $true
}

function Test-Requirement {
    param(
        [string]$Command,
        [string]$Name,
        [string]$InstallUrl
    )
    
    try {
        $null = Get-Command $Command -ErrorAction Stop
        Write-Success "$Name is installed"
        return $true
    }
    catch {
        Write-BuildError "$Name is not installed"
        Write-Host "    Please install from: $InstallUrl"
        return $false
    }
}

# =============================================================================
# Build Steps
# =============================================================================

function Test-Requirements {
    Write-Step "Checking build requirements..."
    
    $allMet = $true
    
    # Check Docker
    if (-not (Test-Requirement -Command "docker" -Name "Docker" -InstallUrl "https://docker.com")) {
        $allMet = $false
    }
    else {
        # Check Docker is running
        try {
            docker info 2>&1 | Out-Null
            Write-Success "Docker is running"
        }
        catch {
            Write-BuildError "Docker is not running"
            $allMet = $false
        }
    }
    
    # Check .NET SDK
    if (-not (Test-Requirement -Command "dotnet" -Name ".NET SDK" -InstallUrl "https://dotnet.microsoft.com")) {
        $allMet = $false
    }
    else {
        # Verify .NET 8 SDK is available (required for WebView2 and tray app)
        $dotnetSdks = dotnet --list-sdks 2>&1
        $hasNet8 = $dotnetSdks | Where-Object { $_ -match '^8\.' }
        if ($hasNet8) {
            Write-Success ".NET 8 SDK is available"
        }
        else {
            Write-BuildError ".NET 8 SDK is required for tray app (WebView2 dependency)"
            Write-Host "    Installed SDKs: $dotnetSdks"
            Write-Host "    Please install .NET 8 SDK from: https://dotnet.microsoft.com/download/dotnet/8.0"
            $allMet = $false
        }
    }
    
    # Check Inno Setup
    $isccPath = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (Test-Path $isccPath) {
        Write-Success "Inno Setup is installed"
    }
    else {
        Write-BuildError "Inno Setup is not installed"
        Write-Host "    Please install from: https://jrsoftware.org/isinfo.php"
        $allMet = $false
    }
    
    if (-not $allMet) {
        throw "Missing required dependencies. Please install them and try again."
    }
}

function Initialize-Build {
    Write-Step "Initializing build environment..."
    
    # Clean if requested
    if ($Clean) {
        if (Test-Path $BUILD_DIR) {
            Remove-Item $BUILD_DIR -Recurse -Force
            Write-Success "Cleaned build directory"
        }
        if (Test-Path $OUTPUT_DIR) {
            Remove-Item $OUTPUT_DIR -Recurse -Force
            Write-Success "Cleaned output directory"
        }
    }
    
    # Create directories
    New-Item -ItemType Directory -Path $BUILD_DIR -Force | Out-Null
    New-Item -ItemType Directory -Path $OUTPUT_DIR -Force | Out-Null
    New-Item -ItemType Directory -Path $DISTRO_DIR -Force | Out-Null
    New-Item -ItemType Directory -Path $ASSETS_DIR -Force | Out-Null
    
    Write-Success "Build directories created"
}

function Copy-Source {
    Write-Step "Copying source code to build directory..."
    
    # Copy backend
    Copy-Item -Path "$PROJECT_ROOT\backend" -Destination "$BUILD_DIR\backend" -Recurse
    Write-Success "Copied backend source"
    
    # Copy frontend
    Copy-Item -Path "$PROJECT_ROOT\frontend" -Destination "$BUILD_DIR\frontend" -Recurse
    Write-Success "Copied frontend source"
    
    # Copy root files
    $rootFiles = @("requirements.txt", "profiles.yaml", "pyproject.toml")
    foreach ($file in $rootFiles) {
        $sourcePath = Join-Path $PROJECT_ROOT $file
        if (Test-Path $sourcePath) {
            Copy-Item $sourcePath -Destination $BUILD_DIR
        }
    }
    Write-Success "Copied root configuration files"
}

function Apply-HardeningPatches {
    Write-Step "Applying hardening patches..."
    
    # Copy secure config module
    $secureConfigSource = Join-Path $SCRIPT_DIR "patches\secure_config.py"
    $secureConfigDest = Join-Path $BUILD_DIR "backend\core\secure_config.py"
    
    if (Test-Path $secureConfigSource) {
        Copy-Item $secureConfigSource -Destination $secureConfigDest
        Write-Success "Applied secure config patch"
    }
    
    # Copy hardened environment config
    Copy-Item "$SCRIPT_DIR\config\hardened.env" -Destination "$BUILD_DIR\.env"
    Write-Success "Applied hardened environment config"
    
    # Modify main.py to disable API docs in production
    $mainPyPath = Join-Path $BUILD_DIR "backend\main.py"
    if (Test-Path $mainPyPath) {
        $content = Get-Content $mainPyPath -Raw
        
        # Add check for EXPOSE_API_DOCS
        if ($content -notmatch "EXPOSE_API_DOCS") {
            $patchCode = @"

# Hardening: Disable API docs in production
import os
if os.getenv('EXPOSE_API_DOCS', 'true').lower() != 'true':
    app.openapi_url = None
    app.docs_url = None
    app.redoc_url = None
"@
            # Append after app creation
            $content = $content + $patchCode
            Set-Content -Path $mainPyPath -Value $content
            Write-Success "Applied API docs disable patch"
        }
    }
    
    Write-Success "All hardening patches applied"
}

function Build-HardenedImages {
    Write-Step "Building hardened Docker images..."
    
    Push-Location $BUILD_DIR
    
    try {
        # Build backend image
        Write-InstallLog "Building backend image..." -Source "Build"
        $backendDockerfile = Join-Path $SCRIPT_DIR "dockerfiles\Dockerfile.backend.hardened"
        
        docker build `
            -f $backendDockerfile `
            -t "${BACKEND_IMAGE}:${Version}" `
            -t "${BACKEND_IMAGE}:latest" `
            . 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
        
        if ($LASTEXITCODE -ne 0) { throw "Backend image build failed" }
        Write-Success "Built backend image: ${BACKEND_IMAGE}:${Version}"
        
        # Build frontend image
        Write-InstallLog "Building frontend image..." -Source "Build"
        $frontendDockerfile = Join-Path $SCRIPT_DIR "dockerfiles\Dockerfile.frontend.hardened"
        
        docker build `
            -f $frontendDockerfile `
            -t "${FRONTEND_IMAGE}:${Version}" `
            -t "${FRONTEND_IMAGE}:latest" `
            . 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
        
        if ($LASTEXITCODE -ne 0) { throw "Frontend image build failed" }
        Write-Success "Built frontend image: ${FRONTEND_IMAGE}:${Version}"
    }
    finally {
        Pop-Location
    }
}

function Export-DockerImages {
    Write-Step "Exporting Docker images..."
    
    $imagesDir = Join-Path $OUTPUT_DIR "images"
    New-Item -ItemType Directory -Path $imagesDir -Force | Out-Null
    
    # Export backend image
    $backendTar = Join-Path $imagesDir "backend.tar"
    docker save -o $backendTar "${BACKEND_IMAGE}:${Version}"
    Write-Success "Exported backend image to: $backendTar"
    
    # Export frontend image
    $frontendTar = Join-Path $imagesDir "frontend.tar"
    docker save -o $frontendTar "${FRONTEND_IMAGE}:${Version}"
    Write-Success "Exported frontend image to: $frontendTar"
    
    # Get sizes
    $backendSize = (Get-Item $backendTar).Length / 1MB
    $frontendSize = (Get-Item $frontendTar).Length / 1MB
    Write-InstallLog "Backend image size: $([math]::Round($backendSize, 2)) MB" -Source "Build"
    Write-InstallLog "Frontend image size: $([math]::Round($frontendSize, 2)) MB" -Source "Build"
    
    # Validate exported artifacts
    Test-BuildArtifact -Path $backendTar -Name "Backend Image" -MinimumSize ($Config.docker.images.backend.minimumSizeMB * 1MB) | Out-Null
    Test-BuildArtifact -Path $frontendTar -Name "Frontend Image" -MinimumSize ($Config.docker.images.frontend.minimumSizeMB * 1MB) | Out-Null
}

function Build-TrayApp {
    Write-Step "Building tray application..."
    
    $trayAppDir = Join-Path $SCRIPT_DIR "tray-app"
    
    Push-Location $trayAppDir
    
    try {
        # Restore NuGet packages (ensures WebView2 and other dependencies are available)
        Write-InstallLog "Restoring NuGet packages (including WebView2)..." -Source "Build"
        dotnet restore 2>&1 | ForEach-Object { if ($Debug) { Write-Host "    $_" -ForegroundColor Gray } }
        if ($LASTEXITCODE -ne 0) { throw "NuGet restore failed" }
        Write-Success "NuGet packages restored"
        
        # Build the tray app
        dotnet build -c Release --no-restore 2>&1 | ForEach-Object { if ($Debug) { Write-Host "    $_" -ForegroundColor Gray } }
        if ($LASTEXITCODE -ne 0) { throw "Tray app build failed" }
        
        # Publish with WebView2 native library extraction support
        # IncludeAllContentForSelfExtract ensures WebView2 loader DLL extracts correctly at runtime
        dotnet publish -c Release -r win-x64 --self-contained true --no-restore `
            -p:IncludeAllContentForSelfExtract=true `
            -p:IncludeNativeLibrariesForSelfExtract=true 2>&1 | ForEach-Object { if ($Debug) { Write-Host "    $_" -ForegroundColor Gray } }
        if ($LASTEXITCODE -ne 0) { throw "Tray app publish failed" }
        
        Write-Success "Built tray application (with WebView2 support)"
        
        # Copy to output
        $publishDir = Join-Path $trayAppDir "bin\Release\net8.0-windows\win-x64\publish"
        if (Test-Path $publishDir) {
            Copy-Item "$publishDir\*" -Destination $OUTPUT_DIR -Recurse -Force
            Write-Success "Copied tray application to output"
            
            # Validate tray app binary size (WebView2 increases size to ~5-8 MB)
            $trayExePath = Join-Path $OUTPUT_DIR "RecallHubTray.exe"
            if (Test-Path $trayExePath) {
                Test-BuildArtifact -Path $trayExePath -Name "Tray Application" -MinimumSize 5MB | Out-Null
            }
        }
        else {
            throw "Publish output directory not found: $publishDir"
        }
    }
    catch {
        Write-BuildWarning "Tray application build failed: $_"
        Write-InstallLog "The installer will be created without the tray application" -Level WARN -Source "Build"
    }
    finally {
        Pop-Location
    }
}

function Download-Models {
    Write-Step "Downloading Ollama models (via Docker)..."
    
    Write-InstallLog "Downloading models using Docker containers..." -Source "Build"
    
    # Create a volume to store models
    docker volume create recallhub-build-models 2>&1 | Out-Null
    
    try {
        # Pull and run Ollama to download models
        $ollamaImage = "$($Config.docker.images.ollama.name):$($Config.docker.images.ollama.tag)"
        Write-InstallLog "Pulling Ollama image: $ollamaImage" -Source "Build"
        docker pull $ollamaImage 2>&1 | Out-Null
        
        # Start Ollama server in background
        Write-InstallLog "Starting Ollama container..." -Source "Build"
        $containerId = docker run -d --name recallhub-ollama-download -v "recallhub-build-models:/root/.ollama" $ollamaImage
        Start-Sleep -Seconds 5
        
        # Download models from config
        $models = @()
        foreach ($m in $Config.models.ollama) {
            $models += @{ Name = $m.name; Purpose = $m.purpose }
        }
        
        foreach ($model in $models) {
            Write-InstallLog "Downloading model: $($model.Name) ($($model.Purpose))" -Source "Build"
            
            $modelName = $model.Name
            Invoke-WithRetry -OperationName "Download $modelName" -RetryCount 3 -InitialDelay 10 -BackoffMultiplier 2.0 -TimeoutSeconds 600 -ScriptBlock {
                $output = docker exec recallhub-ollama-download ollama pull $using:modelName 2>&1
                if ($LASTEXITCODE -ne 0) {
                    throw "Model download failed: $output"
                }
            }
            Write-Success "Model $($model.Name) downloaded successfully"
        }
        
        # Export models to tar file
        Write-InstallLog "Exporting models archive..." -Source "Build"
        New-Item -ItemType Directory -Path $MODELS_DIR -Force | Out-Null
        
        $ubuntuImage = "$($Config.docker.images.ubuntu.name):$($Config.docker.images.ubuntu.tag)"
        docker run --rm -v "recallhub-build-models:/source:ro" -v "${MODELS_DIR}:/dest" $ubuntuImage bash -c "cd /source && tar -cvf /dest/ollama-models.tar models/"
        
        if (Test-Path "$MODELS_DIR\ollama-models.tar") {
            $size = (Get-Item "$MODELS_DIR\ollama-models.tar").Length / 1GB
            Write-Success "Models downloaded and exported ($([math]::Round($size, 2)) GB)"
            Test-BuildArtifact -Path "$MODELS_DIR\ollama-models.tar" -Name "Ollama Models" -MinimumSize 100MB | Out-Null
        }
        else {
            Write-BuildWarning "Models archive may not have been created correctly"
        }
    }
    finally {
        # Cleanup
        docker stop recallhub-ollama-download 2>&1 | Out-Null
        docker rm recallhub-ollama-download 2>&1 | Out-Null
        docker volume rm recallhub-build-models 2>&1 | Out-Null
    }
}

function Create-WSLDistro {
    Write-Step "Creating WSL2 distribution..."
    
    Write-InstallLog "Building complete WSL2 distribution with all components..." -Source "Build"
    Write-InstallLog "Includes: Ubuntu 22.04, Docker, pre-loaded images, Ollama models" -Level DEBUG -Source "Build"
    
    # Run the distro build script
    $distroScript = Join-Path $SCRIPT_DIR "scripts\Build-Distro.ps1"
    
    if (Test-Path $distroScript) {
        & $distroScript `
            -OutputPath "$DISTRO_DIR\recallhub.tar.gz" `
            -BackendImage "${BACKEND_IMAGE}:${Version}" `
            -FrontendImage "${FRONTEND_IMAGE}:${Version}" `
            -IncludeModels
        
        if (Test-Path "$DISTRO_DIR\recallhub.tar.gz") {
            $size = (Get-Item "$DISTRO_DIR\recallhub.tar.gz").Length / 1GB
            Write-Success "WSL2 distribution created ($([math]::Round($size, 2)) GB)"
            Test-BuildArtifact -Path "$DISTRO_DIR\recallhub.tar.gz" -Name "WSL Distribution" -MinimumSize ($Config.wsl.minimumSizeMB * 1MB) | Out-Null
        }
        else {
            throw "WSL2 distribution was not created"
        }
    }
    else {
        Write-BuildError "Build-Distro.ps1 script not found"
        throw "Cannot create WSL2 distribution - missing build script"
    }
}

function Create-Assets {
    Write-Step "Creating installer assets..."
    
    # Create a simple icon if not exists (placeholder)
    $iconPath = Join-Path $ASSETS_DIR "icon.ico"
    if (-not (Test-Path $iconPath)) {
        Write-BuildWarning "Icon file not found at $iconPath"
        Write-InstallLog "Please add a proper icon.ico file to the assets directory" -Level WARN -Source "Build"
    }
}

function Build-Installer {
    Write-Step "Building Windows installer..."
    
    $isccPath = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    $setupScript = Join-Path $SCRIPT_DIR "setup.iss"
    
    if (-not (Test-Path $isccPath)) {
        Write-BuildWarning "Inno Setup not found - skipping installer creation"
        return
    }
    
    # Use version from config - pass as preprocessor define to ISCC
    # This avoids modifying the source setup.iss file
    
    # Build installer
    Push-Location $SCRIPT_DIR
    try {
        & $isccPath "/DMyAppVersion=$Version" $setupScript
        
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Installer created successfully"
            
            # Show output
            $installerPath = Join-Path $OUTPUT_DIR "RecallHubSetup-${Version}.exe"
            if (Test-Path $installerPath) {
                $size = (Get-Item $installerPath).Length / 1GB
                Write-InstallLog "Installer output: $installerPath ($([math]::Round($size, 2)) GB)" -Source "Build"
            }
        }
        else {
            Write-BuildError "Installer build failed"
        }
    }
    finally {
        Pop-Location
    }
}

function Cleanup-Build {
    Write-Step "Cleaning up build artifacts..."
    
    if (-not $Debug) {
        if (Test-Path $BUILD_DIR) {
            Remove-Item $BUILD_DIR -Recurse -Force
            Write-Success "Removed build directory"
        }
    }
    else {
        Write-BuildWarning "Debug mode - keeping build artifacts"
    }
}

function New-BuildManifest {
    Write-Step "Generating build manifest..."
    
    $buildManifest = @{
        buildId = [guid]::NewGuid().ToString()
        timestamp = (Get-Date).ToString("o")
        duration = ((Get-Date) - $buildStart).TotalSeconds
        artifacts = @()
        version = $Version
        machine = $env:COMPUTERNAME
        debug = [bool]$Debug
    }
    
    # Collect all artifacts
    $artifactPaths = @(
        (Join-Path $OUTPUT_DIR "images\backend.tar"),
        (Join-Path $OUTPUT_DIR "images\frontend.tar"),
        (Join-Path $DISTRO_DIR "recallhub.tar.gz"),
        (Join-Path $MODELS_DIR "ollama-models.tar")
    )
    
    foreach ($path in $artifactPaths) {
        if (Test-Path $path) {
            $item = Get-Item $path
            $hash = (Get-FileHash $path -Algorithm SHA256).Hash
            $buildManifest.artifacts += @{
                name = $item.Name
                path = $item.FullName
                sizeBytes = $item.Length
                sha256 = $hash
            }
            Write-InstallLog "Manifest artifact: $($item.Name) ($([math]::Round($item.Length/1MB, 1)) MB) SHA256=$($hash.Substring(0,12))..." -Level DEBUG -Source "Build"
        }
    }
    
    $manifestPath = Join-Path $OUTPUT_DIR "build-manifest.json"
    $buildManifest | ConvertTo-Json -Depth 5 | Set-Content -Path $manifestPath -Encoding UTF8
    Write-InstallLog "Build manifest: $manifestPath" -Source "Build"
}

function Show-Summary {
    Write-Banner "BUILD COMPLETE"
    
    Write-Host "Version: $Version"
    Write-Host "Output Directory: $OUTPUT_DIR"
    Write-Host ""
    
    # List output files
    Write-Host "Output Files:"
    Get-ChildItem $OUTPUT_DIR -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
        $size = $_.Length / 1MB
        Write-Host "  - $($_.Name): $([math]::Round($size, 2)) MB"
    }
    
    if (Test-Path "$DISTRO_DIR\recallhub.tar.gz") {
        $size = (Get-Item "$DISTRO_DIR\recallhub.tar.gz").Length / 1GB
        Write-Host "  - recallhub.tar.gz (WSL distro): $([math]::Round($size, 2)) GB"
    }
    
    if (Test-Path "$MODELS_DIR\ollama-models.tar") {
        $size = (Get-Item "$MODELS_DIR\ollama-models.tar").Length / 1GB
        Write-Host "  - ollama-models.tar: $([math]::Round($size, 2)) GB"
    }
    
    Write-Host ""
    Write-Host "Included Components:" -ForegroundColor Yellow
    Write-Host "  - Hardened Docker images (backend + frontend)"
    Write-Host "  - WSL2 distribution with Docker pre-installed"
    $modelNames = ($Config.models.ollama | ForEach-Object { $_.name }) -join ", "
    Write-Host "  - Ollama models ($modelNames)"
    Write-Host "  - MongoDB Atlas Local image"
    Write-Host "  - System tray application"
    Write-Host "  - Installation and management scripts"
    Write-Host ""
    Write-Host "Your development environment was NOT modified." -ForegroundColor Green
}

# =============================================================================
# Main
# =============================================================================

function Main {
    Write-Banner "RecallHub Hardened Installer Build"
    Write-Host "Version: $Version"
    Write-Host "Output: $OUTPUT_DIR"
    Write-Host "Log: $logFile"
    Write-Host ""
    Write-Host "This build will create a COMPLETE installer including:"
    Write-Host "  - Hardened Docker images"
    Write-Host "  - WSL2 distribution with all dependencies"
    Write-Host "  - Ollama LLM models (~2.3GB)"
    Write-Host ""
    
    $script:buildStart = Get-Date
    $currentSection = "Init"
    
    try {
        # --- Pre-flight Checks ---
        Start-InstallSection -Name "Pre-flight Checks"
        $currentSection = "Pre-flight Checks"
        
        # Check disk space (need sufficient space for full build with models)
        $drive = (Get-Item $SCRIPT_DIR).PSDrive
        $freeGB = [math]::Round($drive.Free / 1GB, 2)
        Write-InstallLog "Available disk space on $($drive.Name): drive: $freeGB GB" -Source "Build"
        
        $requiredSpace = $Config.installer.buildDiskSpaceGB
        if ($freeGB -lt $requiredSpace) {
            Write-InstallDiagnostic -ErrorCode "BUILD-001" -Component "Preflight" -Message "Insufficient disk space for build" -Context @{
                available = $freeGB
                required = $requiredSpace
                drive = $drive.Name
            }
            if ($freeGB -lt ($requiredSpace - 5)) {
                throw "Insufficient disk space: $freeGB GB available, $requiredSpace GB required"
            }
            Write-InstallLog "WARNING: Low disk space ($freeGB GB). Build may fail." -Level WARN -Source "Build"
        }
        
        Test-Requirements
        Complete-InstallSection -Name "Pre-flight Checks" -Status Success
        
        # --- Initialize ---
        Start-InstallSection -Name "Initialize"
        $currentSection = "Initialize"
        Initialize-Build
        Complete-InstallSection -Name "Initialize" -Status Success
        
        # --- Generate Build Configuration ---
        Start-InstallSection -Name "Generate Config"
        $currentSection = "Generate Config"
        
        Write-Step "Generating deployment configuration from config.json..."
        $templateScript = Join-Path $ScriptsDir "New-BuildConfig.ps1"
        $buildConfigDir = Join-Path $BUILD_DIR "config"
        New-Item -ItemType Directory -Path $buildConfigDir -Force | Out-Null
        
        & $templateScript -ConfigPath $ConfigPath -OutputDir $buildConfigDir
        if ($LASTEXITCODE -ne 0) { throw "Configuration generation failed" }
        
        # Copy generated config to where docker-compose expects it
        Copy-Item "$buildConfigDir\docker-compose.hardened.yml" -Destination "$BUILD_DIR\docker-compose.yml" -Force
        Copy-Item "$buildConfigDir\hardened.env" -Destination "$BUILD_DIR\.env" -Force
        Write-Success "Generated deployment configuration from config.json"
        
        Complete-InstallSection -Name "Generate Config" -Status Success
        
        # --- Copy Source ---
        Start-InstallSection -Name "Copy Source"
        $currentSection = "Copy Source"
        Copy-Source
        Complete-InstallSection -Name "Copy Source" -Status Success
        
        # --- Hardening ---
        Start-InstallSection -Name "Hardening Patches"
        $currentSection = "Hardening Patches"
        Apply-HardeningPatches
        Complete-InstallSection -Name "Hardening Patches" -Status Success
        
        # --- Docker Images ---
        Start-InstallSection -Name "Docker Images"
        $currentSection = "Docker Images"
        Build-HardenedImages
        Export-DockerImages
        Complete-InstallSection -Name "Docker Images" -Status Success
        
        # --- Tray App ---
        Start-InstallSection -Name "Tray Application"
        $currentSection = "Tray Application"
        Build-TrayApp
        Complete-InstallSection -Name "Tray Application" -Status Success
        
        # --- Model Downloads ---
        Start-InstallSection -Name "Model Downloads"
        $currentSection = "Model Downloads"
        if (-not $SkipModels) {
            Download-Models
        }
        else {
            if (Test-Path "$MODELS_DIR\ollama-models.tar") {
                Write-InstallLog "Using existing Ollama models (skipped download)" -Source "Build"
            }
            else {
                Write-BuildWarning "SkipModels specified but no existing models found - downloading anyway"
                Download-Models
            }
        }
        Complete-InstallSection -Name "Model Downloads" -Status Success
        
        # --- WSL Distribution ---
        Start-InstallSection -Name "WSL Distribution"
        $currentSection = "WSL Distribution"
        if (-not $SkipDistro) {
            Create-WSLDistro
        }
        else {
            if (Test-Path "$DISTRO_DIR\recallhub.tar.gz") {
                Write-InstallLog "Using existing WSL2 distribution (skipped creation)" -Source "Build"
            }
            else {
                Write-BuildWarning "SkipDistro specified but no existing distro found - building anyway"
                Create-WSLDistro
            }
        }
        Complete-InstallSection -Name "WSL Distribution" -Status Success
        
        # --- Assets & Installer ---
        Start-InstallSection -Name "Installer Packaging"
        $currentSection = "Installer Packaging"
        Create-Assets
        Build-Installer
        Complete-InstallSection -Name "Installer Packaging" -Status Success
        
        # --- Cleanup ---
        Start-InstallSection -Name "Cleanup"
        $currentSection = "Cleanup"
        Cleanup-Build
        Complete-InstallSection -Name "Cleanup" -Status Success
        
        # --- Build Manifest ---
        Start-InstallSection -Name "Build Manifest"
        $currentSection = "Build Manifest"
        New-BuildManifest
        Complete-InstallSection -Name "Build Manifest" -Status Success
        
        # --- Summary ---
        $duration = (Get-Date) - $script:buildStart
        Write-InstallLog "Build completed in $([math]::Round($duration.TotalMinutes, 1)) minutes" -Source "Build"
        
        Export-InstallSummary
        Write-InstallLog "Build completed successfully!" -Source "Build"
        
        Show-Summary
    }
    catch {
        # Mark current section as failed
        if ($currentSection -and $currentSection -ne "Init") {
            try { Complete-InstallSection -Name $currentSection -Status Failed } catch { }
        }
        
        Write-InstallDiagnostic -ErrorCode "BUILD-099" -Component "Build" -Message $_.Exception.Message -Exception $_.Exception -Context @{
            lastSection = $currentSection
        }
        Export-InstallSummary
        Write-InstallLog "BUILD FAILED: $($_.Exception.Message)" -Level ERROR -Source "Build"
        
        Write-BuildError "Build failed: $_"
        Write-Host $_.ScriptStackTrace
        throw
    }
}

# Run main
Main
