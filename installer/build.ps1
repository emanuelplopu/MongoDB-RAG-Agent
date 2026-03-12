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
    Keep intermediate build artifacts for debugging

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

# Paths
$SCRIPT_DIR = $PSScriptRoot
$PROJECT_ROOT = Split-Path $SCRIPT_DIR -Parent
$BUILD_DIR = Join-Path $SCRIPT_DIR "build-temp"
$OUTPUT_DIR = Join-Path $SCRIPT_DIR "output"
$DISTRO_DIR = Join-Path $SCRIPT_DIR "distro"
$MODELS_DIR = Join-Path $SCRIPT_DIR "models"
$ASSETS_DIR = Join-Path $SCRIPT_DIR "assets"

# Image tags
$BACKEND_IMAGE = "recallhub-hardened-backend"
$FRONTEND_IMAGE = "recallhub-hardened-frontend"

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
}

function Write-Success {
    param([string]$Message)
    Write-Host "    [OK] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "    [WARN] $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "    [ERROR] $Message" -ForegroundColor Red
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
        Write-Error "$Name is not installed"
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
            Write-Error "Docker is not running"
            $allMet = $false
        }
    }
    
    # Check .NET SDK
    if (-not (Test-Requirement -Command "dotnet" -Name ".NET SDK" -InstallUrl "https://dotnet.microsoft.com")) {
        $allMet = $false
    }
    
    # Check Inno Setup
    $isccPath = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (Test-Path $isccPath) {
        Write-Success "Inno Setup is installed"
    }
    else {
        Write-Error "Inno Setup is not installed"
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
        Write-Host "    Building backend image..."
        $backendDockerfile = Join-Path $SCRIPT_DIR "dockerfiles\Dockerfile.backend.hardened"
        
        docker build `
            -f $backendDockerfile `
            -t "${BACKEND_IMAGE}:${Version}" `
            -t "${BACKEND_IMAGE}:latest" `
            . 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
        
        if ($LASTEXITCODE -ne 0) { throw "Backend image build failed" }
        Write-Success "Built backend image: ${BACKEND_IMAGE}:${Version}"
        
        # Build frontend image
        Write-Host "    Building frontend image..."
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
    Write-Host "    Backend size: $([math]::Round($backendSize, 2)) MB"
    Write-Host "    Frontend size: $([math]::Round($frontendSize, 2)) MB"
}

function Build-TrayApp {
    Write-Step "Building tray application..."
    
    $trayAppDir = Join-Path $SCRIPT_DIR "tray-app"
    
    Push-Location $trayAppDir
    
    try {
        # Restore and build
        dotnet restore 2>&1 | Out-Null
        dotnet build -c Release 2>&1 | Out-Null
        dotnet publish -c Release -r win-x64 --self-contained true 2>&1 | Out-Null
        
        Write-Success "Built tray application"
        
        # Copy to output
        $publishDir = Join-Path $trayAppDir "bin\Release\net8.0-windows\win-x64\publish"
        if (Test-Path $publishDir) {
            Copy-Item "$publishDir\*" -Destination $OUTPUT_DIR -Recurse -Force
            Write-Success "Copied tray application to output"
        }
    }
    catch {
        Write-Warning "Tray application build failed: $_"
        Write-Host "    The installer will be created without the tray application"
    }
    finally {
        Pop-Location
    }
}

function Download-Models {
    Write-Step "Downloading Ollama models (via Docker)..."
    
    # Use Docker to download models - no local Ollama required
    Write-Host "    This downloads models using Docker containers..."
    
    # Create a volume to store models
    docker volume create recallhub-build-models 2>&1 | Out-Null
    
    try {
        # Pull and run Ollama to download models
        Write-Host "    Pulling Ollama image..."
        docker pull ollama/ollama:latest 2>&1 | Out-Null
        
        # Start Ollama server in background
        Write-Host "    Starting Ollama container..."
        $containerId = docker run -d --name recallhub-ollama-download -v "recallhub-build-models:/root/.ollama" ollama/ollama:latest
        Start-Sleep -Seconds 5
        
        # Download models
        Write-Host "    Downloading llama3.2:3b (this may take several minutes)..."
        docker exec recallhub-ollama-download ollama pull llama3.2:3b 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
        
        Write-Host "    Downloading nomic-embed-text..."
        docker exec recallhub-ollama-download ollama pull nomic-embed-text 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
        
        # Export models to tar file
        Write-Host "    Exporting models archive..."
        New-Item -ItemType Directory -Path $MODELS_DIR -Force | Out-Null
        
        docker run --rm -v "recallhub-build-models:/source:ro" -v "${MODELS_DIR}:/dest" ubuntu:22.04 bash -c "cd /source && tar -cvf /dest/ollama-models.tar models/"
        
        if (Test-Path "$MODELS_DIR\ollama-models.tar") {
            $size = (Get-Item "$MODELS_DIR\ollama-models.tar").Length / 1GB
            Write-Success "Models downloaded and exported ($([math]::Round($size, 2)) GB)"
        }
        else {
            Write-Warning "Models archive may not have been created correctly"
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
    
    Write-Host "    Building complete WSL2 distribution with all components..."
    Write-Host "    This includes: Ubuntu 22.04, Docker, pre-loaded images, Ollama models"
    
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
        }
        else {
            throw "WSL2 distribution was not created"
        }
    }
    else {
        Write-Error "Build-Distro.ps1 script not found"
        throw "Cannot create WSL2 distribution - missing build script"
    }
}

function Create-Assets {
    Write-Step "Creating installer assets..."
    
    # Create a simple icon if not exists (placeholder)
    $iconPath = Join-Path $ASSETS_DIR "icon.ico"
    if (-not (Test-Path $iconPath)) {
        Write-Warning "Icon file not found at $iconPath"
        Write-Host "    Please add a proper icon.ico file to the assets directory"
    }
}

function Build-Installer {
    Write-Step "Building Windows installer..."
    
    $isccPath = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    $setupScript = Join-Path $SCRIPT_DIR "setup.iss"
    
    if (-not (Test-Path $isccPath)) {
        Write-Warning "Inno Setup not found - skipping installer creation"
        return
    }
    
    # Update version in setup.iss
    $setupContent = Get-Content $setupScript -Raw
    $setupContent = $setupContent -replace '#define MyAppVersion ".*"', "#define MyAppVersion `"$Version`""
    Set-Content -Path $setupScript -Value $setupContent
    
    # Build installer
    Push-Location $SCRIPT_DIR
    try {
        & $isccPath $setupScript
        
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Installer created successfully"
            
            # Show output
            $installerPath = Join-Path $OUTPUT_DIR "RecallHubSetup-${Version}.exe"
            if (Test-Path $installerPath) {
                $size = (Get-Item $installerPath).Length / 1GB
                Write-Host "    Output: $installerPath"
                Write-Host "    Size: $([math]::Round($size, 2)) GB"
            }
        }
        else {
            Write-Error "Installer build failed"
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
        Write-Warning "Debug mode - keeping build artifacts"
    }
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
    Write-Host "  - Ollama models (llama3.2:3b, nomic-embed-text)"
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
    Write-Host ""
    Write-Host "This build will create a COMPLETE installer including:"
    Write-Host "  - Hardened Docker images"
    Write-Host "  - WSL2 distribution with all dependencies"
    Write-Host "  - Ollama LLM models (~2.3GB)"
    Write-Host ""
    
    $startTime = Get-Date
    
    try {
        Test-Requirements
        Initialize-Build
        Copy-Source
        Apply-HardeningPatches
        Build-HardenedImages
        Export-DockerImages
        Build-TrayApp
        
        # Download models (now uses Docker - no local Ollama needed)
        if (-not $SkipModels) {
            Download-Models
        }
        else {
            if (Test-Path "$MODELS_DIR\ollama-models.tar") {
                Write-Step "Using existing Ollama models (skipped download)"
            }
            else {
                Write-Warning "SkipModels specified but no existing models found - downloading anyway"
                Download-Models
            }
        }
        
        # Build WSL2 distribution (now fully automated)
        if (-not $SkipDistro) {
            Create-WSLDistro
        }
        else {
            if (Test-Path "$DISTRO_DIR\recallhub.tar.gz") {
                Write-Step "Using existing WSL2 distribution (skipped creation)"
            }
            else {
                Write-Warning "SkipDistro specified but no existing distro found - building anyway"
                Create-WSLDistro
            }
        }
        
        Create-Assets
        Build-Installer
        Cleanup-Build
        
        $duration = (Get-Date) - $startTime
        Write-Host "`nBuild completed in $([math]::Round($duration.TotalMinutes, 1)) minutes"
        
        Show-Summary
    }
    catch {
        Write-Error "Build failed: $_"
        Write-Host $_.ScriptStackTrace
        exit 1
    }
}

# Run main
Main
