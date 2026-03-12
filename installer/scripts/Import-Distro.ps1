<#
.SYNOPSIS
    Imports the RecallHub WSL2 distribution.

.DESCRIPTION
    This script imports the pre-built RecallHub WSL2 distribution that contains:
    - Docker Engine (rootless mode)
    - Pre-pulled application containers
    - Pre-downloaded Ollama models
    - Hardened network configuration

.PARAMETER DistroPath
    Path to the recallhub.tar.gz distribution file.

.PARAMETER InstallPath
    Installation path for the WSL2 distribution.
    Defaults to %LOCALAPPDATA%\RecallHub\wsl

.NOTES
    Requires WSL2 to be installed and configured.
#>

param(
    [string]$DistroPath = "$PSScriptRoot\..\distro\recallhub.tar.gz",
    [string]$InstallPath = "$env:LOCALAPPDATA\RecallHub\wsl",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$DISTRO_NAME = "RecallHub"

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

function Test-DistroExists {
    param([string]$Name)
    
    try {
        $distros = wsl --list --quiet 2>&1
        return ($distros -contains $Name)
    }
    catch {
        return $false
    }
}

function Remove-ExistingDistro {
    param([string]$Name)
    
    Write-Step "Removing existing $Name distribution..."
    
    try {
        wsl --unregister $Name 2>&1 | Out-Null
        Write-Success "Removed existing $Name distribution"
        return $true
    }
    catch {
        Write-Error "Failed to remove existing distribution: $_"
        return $false
    }
}

function Import-WSLDistro {
    param(
        [string]$Name,
        [string]$InstallPath,
        [string]$TarPath
    )
    
    Write-Step "Importing $Name distribution..."
    Write-Host "    Source: $TarPath"
    Write-Host "    Destination: $InstallPath"
    
    # Create install directory
    if (-not (Test-Path $InstallPath)) {
        New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    }
    
    try {
        # Import the distribution
        $output = wsl --import $Name $InstallPath $TarPath 2>&1
        
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Distribution imported successfully"
            return $true
        }
        else {
            Write-Error "Import failed: $output"
            return $false
        }
    }
    catch {
        Write-Error "Exception during import: $_"
        return $false
    }
}

function Set-DefaultDistro {
    param([string]$Name)
    
    Write-Step "Setting $Name as default WSL distribution..."
    
    try {
        wsl --set-default $Name 2>&1 | Out-Null
        Write-Success "$Name is now the default distribution"
        return $true
    }
    catch {
        Write-Warning "Could not set as default distribution"
        return $false
    }
}

function Initialize-RecallHubDistro {
    Write-Step "Initializing RecallHub distribution..."
    
    try {
        # Run initialization script inside WSL
        $initScript = @"
#!/bin/bash
set -e

echo "Starting RecallHub initialization..."

# Ensure Docker is running
if ! systemctl is-active --quiet docker 2>/dev/null; then
    echo "Starting Docker..."
    sudo systemctl start docker || dockerd &
    sleep 5
fi

# Load pre-bundled container images if they exist
IMAGES_DIR="/opt/recallhub/images"
if [ -d "\$IMAGES_DIR" ]; then
    echo "Loading container images..."
    for image in \$IMAGES_DIR/*.tar; do
        if [ -f "\$image" ]; then
            echo "  Loading \$image..."
            docker load < "\$image"
        fi
    done
fi

# Initialize Ollama models if they exist
MODELS_DIR="/opt/recallhub/models"
if [ -d "\$MODELS_DIR" ] && [ -f "\$MODELS_DIR/ollama-models.tar" ]; then
    echo "Restoring Ollama models..."
    mkdir -p ~/.ollama/models
    tar -xf "\$MODELS_DIR/ollama-models.tar" -C ~/.ollama/models/
fi

echo "RecallHub initialization complete!"
"@
        
        # Write and execute initialization script
        $tempScript = [System.IO.Path]::GetTempFileName()
        $initScript | Set-Content -Path $tempScript -Encoding UTF8
        
        # Convert Windows path to WSL path
        $wslTempPath = wsl wslpath -a $tempScript.Replace('\', '/')
        
        # Execute in WSL
        wsl -d $DISTRO_NAME bash $wslTempPath 2>&1
        
        # Cleanup
        Remove-Item $tempScript -Force -ErrorAction SilentlyContinue
        
        Write-Success "Distribution initialized"
        return $true
    }
    catch {
        Write-Warning "Initialization encountered issues: $_"
        return $false
    }
}

function Test-DistroHealth {
    Write-Step "Verifying distribution health..."
    
    try {
        # Check that we can run commands
        $output = wsl -d $DISTRO_NAME echo "WSL OK" 2>&1
        
        if ($output -eq "WSL OK") {
            Write-Success "Distribution is responsive"
        }
        else {
            Write-Warning "Unexpected response: $output"
        }
        
        # Check Docker
        $dockerVersion = wsl -d $DISTRO_NAME docker --version 2>&1
        if ($dockerVersion -match "Docker version") {
            Write-Success "Docker is available: $dockerVersion"
        }
        else {
            Write-Warning "Docker may not be available"
        }
        
        return $true
    }
    catch {
        Write-Error "Health check failed: $_"
        return $false
    }
}

function Main {
    Write-Host @"
╔═══════════════════════════════════════════════════════════════════╗
║         RecallHub WSL2 Distribution Importer                      ║
║         Imports pre-built distribution with all dependencies      ║
╚═══════════════════════════════════════════════════════════════════╝
"@ -ForegroundColor Magenta

    # Check if distribution file exists
    if (-not (Test-Path $DistroPath)) {
        Write-Error "Distribution file not found: $DistroPath"
        Write-Host "`n    Please ensure the distribution file exists or run the build script first."
        exit 1
    }
    
    Write-Success "Found distribution file: $DistroPath"
    $fileSize = (Get-Item $DistroPath).Length / 1GB
    Write-Host "    Size: $([math]::Round($fileSize, 2)) GB"
    
    # Check if distribution already exists
    if (Test-DistroExists -Name $DISTRO_NAME) {
        if ($Force) {
            Write-Warning "$DISTRO_NAME distribution already exists - removing due to -Force flag"
            if (-not (Remove-ExistingDistro -Name $DISTRO_NAME)) {
                exit 1
            }
        }
        else {
            Write-Warning "$DISTRO_NAME distribution already exists"
            $response = Read-Host "    Would you like to replace it? (y/n)"
            if ($response -eq 'y' -or $response -eq 'Y') {
                if (-not (Remove-ExistingDistro -Name $DISTRO_NAME)) {
                    exit 1
                }
            }
            else {
                Write-Host "    Skipping import. Use -Force to override."
                exit 0
            }
        }
    }
    
    # Import the distribution
    if (-not (Import-WSLDistro -Name $DISTRO_NAME -InstallPath $InstallPath -TarPath $DistroPath)) {
        exit 1
    }
    
    # Set as default
    Set-DefaultDistro -Name $DISTRO_NAME
    
    # Initialize the distribution
    Initialize-RecallHubDistro
    
    # Verify health
    Test-DistroHealth
    
    Write-Host "`n" + ("=" * 60) -ForegroundColor Green
    Write-Host "DISTRIBUTION IMPORT COMPLETE" -ForegroundColor Green
    Write-Host ("=" * 60) -ForegroundColor Green
    Write-Host "`nThe RecallHub WSL2 distribution has been imported."
    Write-Host "Location: $InstallPath"
    Write-Host "`nYou can access it with: wsl -d $DISTRO_NAME"
}

# Run main
Main
