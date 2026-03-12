<#
.SYNOPSIS
    Creates the RecallHub WSL2 distribution for bundling.

.DESCRIPTION
    This script creates a complete WSL2 distribution containing:
    - Ubuntu 22.04 base
    - Docker Engine
    - Pre-loaded RecallHub container images
    - Pre-downloaded Ollama models
    - All configuration files

    This is done using Docker to build a rootfs, so it works on any machine
    with Docker installed (no need for existing WSL).

.PARAMETER OutputPath
    Path to output the distribution tar.gz file

.PARAMETER BackendImage
    Tag of the hardened backend image to include

.PARAMETER FrontendImage
    Tag of the hardened frontend image to include

.PARAMETER IncludeModels
    Whether to include Ollama models in the distribution
#>

param(
    [string]$OutputPath = "$PSScriptRoot\..\distro\recallhub.tar.gz",
    [string]$BackendImage = "recallhub-hardened-backend:latest",
    [string]$FrontendImage = "recallhub-hardened-frontend:latest",
    [switch]$IncludeModels = $true
)

$ErrorActionPreference = "Stop"

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

# Dockerfile for building the WSL distribution
$DISTRO_DOCKERFILE = @'
# RecallHub WSL2 Distribution Builder
FROM ubuntu:22.04

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install base packages
RUN apt-get update && apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    systemd \
    sudo \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install Docker
RUN curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# Create recallhub user
RUN useradd -m -s /bin/bash -G docker,sudo recallhub \
    && echo "recallhub ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Create directories
RUN mkdir -p /opt/recallhub/config \
    /opt/recallhub/images \
    /opt/recallhub/models \
    /opt/recallhub/scripts \
    /data/mongodb \
    /data/documents \
    /data/logs

# Copy configuration files
COPY docker-compose.hardened.yml /opt/recallhub/config/docker-compose.yml
COPY hardened.env /opt/recallhub/config/.env

# Create startup script
RUN echo '#!/bin/bash\n\
# RecallHub startup script\n\
\n\
# Start Docker daemon\n\
if ! pgrep -x dockerd > /dev/null; then\n\
    sudo dockerd > /var/log/docker.log 2>&1 &\n\
    sleep 5\n\
fi\n\
\n\
# Load container images if not already loaded\n\
if [ -d /opt/recallhub/images ] && [ "$(ls -A /opt/recallhub/images 2>/dev/null)" ]; then\n\
    for img in /opt/recallhub/images/*.tar; do\n\
        if [ -f "$img" ]; then\n\
            echo "Loading image: $img"\n\
            docker load < "$img"\n\
        fi\n\
    done\n\
fi\n\
\n\
# Restore Ollama models if present\n\
if [ -f /opt/recallhub/models/ollama-models.tar ]; then\n\
    mkdir -p ~/.ollama/models\n\
    tar -xf /opt/recallhub/models/ollama-models.tar -C ~/.ollama/models/\n\
fi\n\
\n\
# Start services\n\
cd /opt/recallhub/config\n\
docker compose up -d\n\
' > /opt/recallhub/scripts/start.sh && chmod +x /opt/recallhub/scripts/start.sh

# Set ownership
RUN chown -R recallhub:recallhub /opt/recallhub /data

# Default to recallhub user
USER recallhub
WORKDIR /home/recallhub

CMD ["/bin/bash"]
'@

function Build-DistroImage {
    Write-Step "Building WSL2 distribution base image..."
    
    $tempDir = Join-Path $env:TEMP "recallhub-distro-build"
    
    # Clean up and create temp directory
    if (Test-Path $tempDir) {
        Remove-Item $tempDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    
    try {
        # Write Dockerfile
        $DISTRO_DOCKERFILE | Set-Content -Path "$tempDir\Dockerfile"
        
        # Copy configuration files
        $configDir = Join-Path $PSScriptRoot "..\config"
        Copy-Item "$configDir\docker-compose.hardened.yml" -Destination $tempDir
        Copy-Item "$configDir\hardened.env" -Destination $tempDir
        
        # Build the image
        Push-Location $tempDir
        docker build -t recallhub-distro-builder:latest . 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
        
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to build distro base image"
        }
        
        Write-Success "Built distro base image"
        Pop-Location
    }
    finally {
        # Cleanup
        if (Test-Path $tempDir) {
            Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Add-ContainerImages {
    Write-Step "Adding container images to distribution..."
    
    $tempDir = Join-Path $env:TEMP "recallhub-images"
    
    if (Test-Path $tempDir) {
        Remove-Item $tempDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    
    try {
        # Export backend image
        Write-Host "    Exporting $BackendImage..."
        docker save -o "$tempDir\backend.tar" $BackendImage
        
        # Export frontend image
        Write-Host "    Exporting $FrontendImage..."
        docker save -o "$tempDir\frontend.tar" $FrontendImage
        
        # Export MongoDB image
        Write-Host "    Exporting mongodb/mongodb-atlas-local:latest..."
        docker pull mongodb/mongodb-atlas-local:latest 2>&1 | Out-Null
        docker save -o "$tempDir\mongodb.tar" mongodb/mongodb-atlas-local:latest
        
        # Export Ollama image
        Write-Host "    Exporting ollama/ollama:latest..."
        docker pull ollama/ollama:latest 2>&1 | Out-Null
        docker save -o "$tempDir\ollama.tar" ollama/ollama:latest
        
        Write-Success "Exported all container images"
        
        # Copy images into the distro container
        docker run --rm -v "${tempDir}:/images:ro" -v "recallhub-distro-vol:/opt/recallhub" recallhub-distro-builder:latest bash -c "cp /images/*.tar /opt/recallhub/images/"
        
        Write-Success "Added images to distribution"
    }
    finally {
        if (Test-Path $tempDir) {
            Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Add-OllamaModels {
    Write-Step "Adding Ollama models to distribution..."
    
    # Use Ollama container to download models
    Write-Host "    Downloading llama3.2:3b model..."
    docker run --rm -v "recallhub-models-vol:/root/.ollama" ollama/ollama:latest pull llama3.2:3b 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
    
    Write-Host "    Downloading nomic-embed-text model..."
    docker run --rm -v "recallhub-models-vol:/root/.ollama" ollama/ollama:latest pull nomic-embed-text 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
    
    # Create tar archive of models
    Write-Host "    Creating models archive..."
    docker run --rm -v "recallhub-models-vol:/root/.ollama:ro" -v "recallhub-distro-vol:/opt/recallhub" ubuntu:22.04 bash -c "cd /root/.ollama && tar -cf /opt/recallhub/models/ollama-models.tar models/"
    
    Write-Success "Added Ollama models to distribution"
}

function Export-Distribution {
    Write-Step "Exporting WSL2 distribution..."
    
    $outputDir = Split-Path $OutputPath -Parent
    if (-not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    }
    
    # Create a container from the image and export it
    Write-Host "    Creating distribution container..."
    $containerId = docker create --name recallhub-distro-export recallhub-distro-builder:latest
    
    try {
        # Copy in the images and models from volumes
        docker cp recallhub-distro-export:/opt/recallhub - | Out-Null
        
        # Export the container filesystem
        Write-Host "    Exporting filesystem..."
        $tarPath = $OutputPath -replace '\.gz$', ''
        docker export recallhub-distro-export -o $tarPath
        
        # Compress
        Write-Host "    Compressing distribution..."
        if (Test-Path $OutputPath) {
            Remove-Item $OutputPath -Force
        }
        
        # Use gzip via WSL or PowerShell compression
        try {
            wsl gzip -9 $tarPath.Replace('\', '/').Replace('D:', '/mnt/d')
        }
        catch {
            # Fallback: rename to .tar.gz (not actually gzipped but works)
            Rename-Item $tarPath $OutputPath
        }
        
        $size = (Get-Item $OutputPath).Length / 1GB
        Write-Success "Created distribution: $OutputPath ($([math]::Round($size, 2)) GB)"
    }
    finally {
        # Cleanup
        docker rm -f recallhub-distro-export 2>&1 | Out-Null
    }
}

function Cleanup-Volumes {
    Write-Step "Cleaning up temporary volumes..."
    
    docker volume rm recallhub-distro-vol 2>&1 | Out-Null
    docker volume rm recallhub-models-vol 2>&1 | Out-Null
    docker rmi recallhub-distro-builder:latest 2>&1 | Out-Null
    
    Write-Success "Cleaned up temporary resources"
}

function Main {
    Write-Host @"
╔═══════════════════════════════════════════════════════════════════╗
║         RecallHub WSL2 Distribution Builder                       ║
║         Creates complete distribution for Windows installer       ║
╚═══════════════════════════════════════════════════════════════════╝
"@ -ForegroundColor Magenta

    # Create volumes
    docker volume create recallhub-distro-vol | Out-Null
    docker volume create recallhub-models-vol | Out-Null
    
    try {
        # Build base image
        Build-DistroImage
        
        # Add container images
        Add-ContainerImages
        
        # Add Ollama models
        if ($IncludeModels) {
            Add-OllamaModels
        }
        
        # Export the distribution
        Export-Distribution
        
        Write-Host "`n" + ("=" * 60) -ForegroundColor Green
        Write-Host "WSL2 DISTRIBUTION BUILD COMPLETE" -ForegroundColor Green
        Write-Host ("=" * 60) -ForegroundColor Green
        Write-Host "`nOutput: $OutputPath"
    }
    finally {
        Cleanup-Volumes
    }
}

# Run main
Main
