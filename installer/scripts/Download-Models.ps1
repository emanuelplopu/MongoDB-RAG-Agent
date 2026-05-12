<#
.SYNOPSIS
    Downloads and prepares Ollama models for bundling into the RecallHub installer.

.DESCRIPTION
    This script downloads the required LLM and embedding models from Ollama
    and prepares them for inclusion in the hardened Windows installer.

.NOTES
    Models are downloaded to installer/models/ and will be bundled into the
    WSL2 distribution during the build process.
#>

param(
    [string]$OutputDir = "$PSScriptRoot\..\models",
    [switch]$Force,
    [string]$ConfigPath
)

$ErrorActionPreference = "Stop"

# Load configuration
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallerDir = Split-Path $ScriptDir -Parent
if (-not $ConfigPath) { $ConfigPath = Join-Path $InstallerDir "config.json" }
$Config = if (Test-Path $ConfigPath) { Get-Content $ConfigPath -Raw | ConvertFrom-Json } else { $null }

# Get models from config or fall back to defaults
if ($Config -and $Config.models.ollama) {
    $OLLAMA_MODELS = @()
    foreach ($m in $Config.models.ollama) {
        $OLLAMA_MODELS += @{ Name = $m.name; Description = $m.purpose; RequiredSize = "$($m.expectedSizeGB)GB" }
    }
} else {
    # Fallback defaults
    $OLLAMA_MODELS = @(
        @{ Name = "llama3.2:3b"; Description = "Primary LLM for chat and reasoning"; RequiredSize = "2GB" }
        @{ Name = "nomic-embed-text"; Description = "Text embedding model for RAG"; RequiredSize = "300MB" }
    )
}

# Ollama Docker image reference
$ollamaImage = if ($Config) { "$($Config.docker.images.ollama.name):$($Config.docker.images.ollama.tag)" } else { "ollama/ollama:latest" }

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

function Test-OllamaInstalled {
    try {
        $null = Get-Command ollama -ErrorAction Stop
        return $true
    }
    catch {
        return $false
    }
}

function Test-OllamaRunning {
    try {
        $response = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method Get -TimeoutSec 5
        return $true
    }
    catch {
        return $false
    }
}

function Start-OllamaService {
    Write-Step "Starting Ollama service..."
    
    # Start Ollama in the background
    $process = Start-Process -FilePath "ollama" -ArgumentList "serve" -PassThru -WindowStyle Hidden
    
    # Wait for it to be ready
    $maxAttempts = 30
    $attempt = 0
    while (-not (Test-OllamaRunning) -and $attempt -lt $maxAttempts) {
        Start-Sleep -Seconds 1
        $attempt++
    }
    
    if (Test-OllamaRunning) {
        Write-Success "Ollama service is running"
        return $process
    }
    else {
        throw "Failed to start Ollama service after $maxAttempts seconds"
    }
}

function Get-OllamaModel {
    param(
        [string]$ModelName
    )
    
    Write-Step "Downloading model: $ModelName"
    
    try {
        # Pull the model using ollama CLI
        $output = & ollama pull $ModelName 2>&1
        
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Downloaded $ModelName"
            return $true
        }
        else {
            Write-Error "Failed to download $ModelName : $output"
            return $false
        }
    }
    catch {
        Write-Error "Exception downloading $ModelName : $_"
        return $false
    }
}

function Export-OllamaModels {
    param(
        [string]$OutputPath
    )
    
    Write-Step "Exporting Ollama models to: $OutputPath"
    
    # Ensure output directory exists
    if (-not (Test-Path $OutputPath)) {
        New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null
    }
    
    # Get the Ollama models directory
    $ollamaHome = $env:OLLAMA_MODELS
    if (-not $ollamaHome) {
        $ollamaHome = "$env:USERPROFILE\.ollama\models"
    }
    
    if (-not (Test-Path $ollamaHome)) {
        throw "Ollama models directory not found: $ollamaHome"
    }
    
    # Copy the models directory structure
    Write-Step "Copying models from $ollamaHome"
    
    # Create a tar archive of the models
    $tarPath = Join-Path $OutputPath "ollama-models.tar"
    
    # Use tar to create archive (available in Windows 10+)
    Push-Location $ollamaHome
    try {
        & tar -cvf $tarPath .
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Created models archive: $tarPath"
            
            # Get the size
            $size = (Get-Item $tarPath).Length / 1GB
            Write-Host "    Archive size: $([math]::Round($size, 2)) GB"
        }
        else {
            throw "Failed to create tar archive"
        }
    }
    finally {
        Pop-Location
    }
    
    # Create a manifest file
    $manifest = @{
        version = "1.0.0"
        created = (Get-Date).ToString("o")
        models = @()
    }
    
    foreach ($model in $OLLAMA_MODELS) {
        $manifest.models += @{
            name = $model.Name
            description = $model.Description
        }
    }
    
    $manifestPath = Join-Path $OutputPath "models-manifest.json"
    $manifest | ConvertTo-Json -Depth 3 | Set-Content -Path $manifestPath
    Write-Success "Created manifest: $manifestPath"
    
    return $tarPath
}

function Main {
    Write-Host @"
╔═══════════════════════════════════════════════════════════════════╗
║         RecallHub Ollama Model Bundler                            ║
║         Downloads and packages models for offline installer       ║
╚═══════════════════════════════════════════════════════════════════╝
"@ -ForegroundColor Magenta

    # Check if Ollama is installed
    if (-not (Test-OllamaInstalled)) {
        Write-Error "Ollama is not installed. Please install from https://ollama.ai"
        Write-Host "`nInstallation instructions:"
        Write-Host "  1. Visit https://ollama.ai"
        Write-Host "  2. Download and run the Windows installer"
        Write-Host "  3. Re-run this script"
        exit 1
    }
    
    Write-Success "Ollama is installed"
    
    # Check if already running, start if not
    $ollamaProcess = $null
    if (-not (Test-OllamaRunning)) {
        $ollamaProcess = Start-OllamaService
    }
    else {
        Write-Success "Ollama is already running"
    }
    
    try {
        # Download each model
        $allSuccess = $true
        foreach ($model in $OLLAMA_MODELS) {
            Write-Host "`n----------------------------------------"
            Write-Host "Model: $($model.Name)"
            Write-Host "Purpose: $($model.Description)"
            Write-Host "Expected size: ~$($model.RequiredSize)"
            Write-Host "----------------------------------------"
            
            if (-not (Get-OllamaModel -ModelName $model.Name)) {
                $allSuccess = $false
            }
        }
        
        if (-not $allSuccess) {
            throw "Some models failed to download"
        }
        
        # Export models
        $archivePath = Export-OllamaModels -OutputPath $OutputDir
        
        Write-Host "`n" + ("=" * 60) -ForegroundColor Green
        Write-Host "SUCCESS! Models have been downloaded and packaged." -ForegroundColor Green
        Write-Host ("=" * 60) -ForegroundColor Green
        Write-Host "`nOutput directory: $OutputDir"
        Write-Host "Archive: $archivePath"
        Write-Host "`nThese files will be included in the hardened installer."
    }
    finally {
        # Stop Ollama if we started it
        if ($ollamaProcess) {
            Write-Step "Stopping Ollama service..."
            Stop-Process -Id $ollamaProcess.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

# Run main
Main
