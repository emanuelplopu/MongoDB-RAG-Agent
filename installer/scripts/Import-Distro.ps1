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
    [switch]$Force,
    [string]$ConfigPath
)

$ErrorActionPreference = "Stop"

# --- Import logging module ---
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Logging.psm1") -Force

$logFile = Initialize-InstallLog -LogName "import-distro"
Write-InstallLog "Starting distribution import" -Source "Import-Distro"

# --- Load configuration ---
$InstallerDir = Split-Path $ScriptDir -Parent
if (-not $ConfigPath) { $ConfigPath = Join-Path $InstallerDir "config.json" }
$Config = if (Test-Path $ConfigPath) { Get-Content $ConfigPath -Raw | ConvertFrom-Json } else { $null }

# Derive values from config
$DISTRO_NAME = if ($Config) { $Config.wsl.distroName } else { "RecallHub" }
$MinDistroSize = if ($Config) { $Config.wsl.minimumSizeMB * 1MB } else { 500MB }
$currentSection = ""

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

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
    
    Write-InstallLog "Removing existing $Name distribution..." -Source "Import-Distro"
    
    try {
        wsl --unregister $Name 2>&1 | Out-Null
        Write-InstallLog "Removed existing $Name distribution" -Source "Import-Distro"
        return $true
    }
    catch {
        Write-InstallLog "Failed to remove existing distribution: $_" -Level ERROR -Source "Import-Distro"
        return $false
    }
}

function Set-DefaultDistro {
    param([string]$Name)
    
    Write-InstallLog "Setting $Name as default WSL distribution..." -Source "Import-Distro"
    
    try {
        wsl --set-default $Name 2>&1 | Out-Null
        Write-InstallLog "$Name is now the default distribution" -Source "Import-Distro"
        return $true
    }
    catch {
        Write-InstallLog "Could not set as default distribution" -Level WARN -Source "Import-Distro"
        return $false
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

try {
    Write-InstallLog @"
=====================================================================
         RecallHub WSL2 Distribution Importer
         Imports pre-built distribution with all dependencies
=====================================================================
"@ -Source "Import-Distro"

    # ===================================================================
    # Section: Archive Validation
    # ===================================================================
    $currentSection = "Archive Validation"
    Start-InstallSection -Name $currentSection

    # Resolve distro file path
    $distroFile = $DistroPath

    if (-not (Test-Path $distroFile)) {
        Write-InstallDiagnostic -ErrorCode "DIST-001" -Component "Distro" -Message "Distribution archive not found" -Context @{ path = $distroFile }
        throw "Distribution archive not found at: $distroFile"
    }

    $fileSize = (Get-Item $distroFile).Length
    $minSize = $MinDistroSize  # Minimum expected size for valid distro
    Write-InstallLog "Archive size: $([math]::Round($fileSize/1GB, 2)) GB" -Source "Import-Distro"

    if ($fileSize -lt $minSize) {
        Write-InstallDiagnostic -ErrorCode "DIST-002" -Component "Distro" -Message "Archive appears corrupted (too small)" -Context @{ size = $fileSize; minimum = $minSize }
        throw "Archive too small ($fileSize bytes) - likely corrupted"
    }

    # Verify tar header (first 512 bytes should contain tar magic)
    try {
        $header = [System.IO.File]::ReadAllBytes($distroFile)[0..511]
        # tar magic is at offset 257: "ustar"
        $magic = [System.Text.Encoding]::ASCII.GetString($header[257..261])
        if ($magic -ne "ustar") {
            Write-InstallLog "Note: Archive may be gzip compressed (no raw tar header)" -Level WARN -Source "Import-Distro"
        } else {
            Write-InstallLog "Tar archive header validated (ustar magic present)" -Source "Import-Distro"
        }
    } catch {
        Write-InstallLog "Could not read archive header for validation: $_" -Level WARN -Source "Import-Distro"
    }

    Complete-InstallSection -Name $currentSection -Status Success

    # ===================================================================
    # Section: Existing Distro Check
    # ===================================================================
    $currentSection = "Existing Distro Check"
    Start-InstallSection -Name $currentSection

    if (Test-DistroExists -Name $DISTRO_NAME) {
        if ($Force) {
            Write-InstallLog "$DISTRO_NAME distribution already exists - removing due to -Force flag" -Level WARN -Source "Import-Distro"
            if (-not (Remove-ExistingDistro -Name $DISTRO_NAME)) {
                throw "Failed to remove existing distribution"
            }
        }
        else {
            Write-InstallLog "$DISTRO_NAME distribution already exists" -Level WARN -Source "Import-Distro"
            $response = Read-Host "    Would you like to replace it? (y/n)"
            if ($response -eq 'y' -or $response -eq 'Y') {
                if (-not (Remove-ExistingDistro -Name $DISTRO_NAME)) {
                    throw "Failed to remove existing distribution"
                }
            }
            else {
                Write-InstallLog "Skipping import. Use -Force to override." -Source "Import-Distro"
                Complete-InstallSection -Name $currentSection -Status Skipped
                Export-InstallSummary
                exit 0
            }
        }
    } else {
        Write-InstallLog "No existing $DISTRO_NAME distribution found - proceeding" -Source "Import-Distro"
    }

    Complete-InstallSection -Name $currentSection -Status Success

    # ===================================================================
    # Section: WSL Import
    # ===================================================================
    $currentSection = "WSL Import"
    Start-InstallSection -Name $currentSection
    Write-InstallLog "Importing WSL2 distribution (this may take several minutes)..." -Source "Import-Distro"
    Write-InstallLog "  Source: $distroFile" -Source "Import-Distro"
    Write-InstallLog "  Destination: $InstallPath" -Source "Import-Distro"

    # Create install directory
    if (-not (Test-Path $InstallPath)) {
        New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
        Write-InstallLog "Created install directory: $InstallPath" -Source "Import-Distro"
    }

    $importResult = Invoke-WithRetry -OperationName "WSL Import" -RetryCount 1 -TimeoutSeconds 600 -ScriptBlock {
        $output = & wsl --import $using:DISTRO_NAME $using:InstallPath $using:distroFile 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "WSL import failed: $output"
        }
        return $output
    }

    Write-InstallLog "WSL distribution imported successfully" -Source "Import-Distro"
    Complete-InstallSection -Name $currentSection -Status Success

    # ===================================================================
    # Section: Set Default
    # ===================================================================
    $currentSection = "Set Default"
    Start-InstallSection -Name $currentSection
    Set-DefaultDistro -Name $DISTRO_NAME
    Complete-InstallSection -Name $currentSection -Status Success

    # ===================================================================
    # Section: Docker Daemon Init
    # ===================================================================
    $currentSection = "Docker Daemon Init"
    Start-InstallSection -Name $currentSection

    Write-InstallLog "Starting Docker daemon inside WSL..." -Source "Import-Distro"

    $daemonOutput = wsl -d $DISTRO_NAME -- bash -c "sudo dockerd &>/tmp/dockerd.log & echo `$!"
    $dockerPid = ($daemonOutput | Select-Object -Last 1).Trim()
    Write-InstallLog "Docker daemon started with PID: $dockerPid" -Source "Import-Distro"

    # Wait for daemon readiness
    Invoke-WithRetry -OperationName "Docker Daemon Ready" -RetryCount 10 -InitialDelay 2 -TimeoutSeconds 60 -ScriptBlock {
        $info = & wsl -d $DISTRO_NAME -- bash -c "docker info > /dev/null 2>&1"
        if ($LASTEXITCODE -ne 0) { throw "Docker daemon not ready" }
    }
    Write-InstallLog "Docker daemon ready" -Source "Import-Distro"
    Complete-InstallSection -Name $currentSection -Status Success

    # ===================================================================
    # Section: Docker Image Load
    # ===================================================================
    $currentSection = "Docker Image Load"
    Start-InstallSection -Name $currentSection

    # Check for pre-bundled container images
    $imagesExist = wsl -d $DISTRO_NAME -- bash -c "test -d /opt/recallhub/images && ls /opt/recallhub/images/*.tar 2>/dev/null"
    if ($LASTEXITCODE -eq 0 -and $imagesExist) {
        $imageFiles = ($imagesExist -split "`n") | Where-Object { $_.Trim() -ne "" }
        Write-InstallLog "Found $($imageFiles.Count) Docker image(s) to load" -Source "Import-Distro"

        foreach ($imageFile in $imageFiles) {
            $imageName = Split-Path $imageFile -Leaf
            Write-InstallLog "Loading Docker image: $imageName" -Source "Import-Distro"

            Invoke-WithRetry -OperationName "Load $imageName" -RetryCount 2 -InitialDelay 5 -ScriptBlock {
                $output = & wsl -d $DISTRO_NAME -- bash -c "docker load -i '$using:imageFile' 2>&1"
                if ($LASTEXITCODE -ne 0) {
                    throw "Docker image load failed: $output"
                }
                return $output
            }
            Write-InstallLog "  Loaded: $imageName" -Source "Import-Distro"
        }
    } else {
        Write-InstallLog "No pre-bundled Docker images found - skipping" -Level WARN -Source "Import-Distro"
    }

    Complete-InstallSection -Name $currentSection -Status Success

    # ===================================================================
    # Section: Model Restoration
    # ===================================================================
    $currentSection = "Model Restoration"
    Start-InstallSection -Name $currentSection

    $modelsArchiveCheck = wsl -d $DISTRO_NAME -- bash -c "test -f /opt/recallhub/models/ollama-models.tar && echo 'exists'"
    if ($modelsArchiveCheck -match "exists") {
        Write-InstallLog "Restoring Ollama models from archive" -Source "Import-Distro"

        $modelsPath = "/opt/recallhub/models/ollama-models.tar"
        $output = wsl -d $DISTRO_NAME -- bash -c "mkdir -p ~/.ollama/models && tar -xf '$modelsPath' -C ~/.ollama/models/ 2>&1"
        if ($LASTEXITCODE -ne 0) {
            Write-InstallDiagnostic -ErrorCode "DIST-003" -Component "Models" -Message "Model extraction failed" -Context @{ output = "$output" }
            throw "Model extraction failed: $output"
        }

        # Verify models extracted
        $modelCount = wsl -d $DISTRO_NAME -- bash -c "find ~/.ollama/models -type f | wc -l"
        $modelCount = $modelCount.Trim()
        Write-InstallLog "Extracted $modelCount model files" -Source "Import-Distro"

        if ([int]$modelCount -lt 2) {
            Write-InstallDiagnostic -ErrorCode "DIST-004" -Component "Models" -Message "Too few model files after extraction" -Context @{ count = $modelCount }
            Write-InstallLog "WARNING: Expected more model files - models may be incomplete" -Level WARN -Source "Import-Distro"
        }
    } else {
        Write-InstallLog "No Ollama model archive found - skipping model restoration" -Level WARN -Source "Import-Distro"
    }

    Complete-InstallSection -Name $currentSection -Status Success

    # ===================================================================
    # Section: Health Check
    # ===================================================================
    $currentSection = "Health Check"
    Start-InstallSection -Name $currentSection

    Write-InstallLog "Verifying distribution health..." -Source "Import-Distro"

    # Check that we can run commands
    $output = wsl -d $DISTRO_NAME echo "WSL OK" 2>&1
    if ($output -eq "WSL OK") {
        Write-InstallLog "Distribution is responsive" -Source "Import-Distro"
    } else {
        Write-InstallLog "Unexpected response from WSL: $output" -Level WARN -Source "Import-Distro"
    }

    # Check Docker
    $dockerVersion = wsl -d $DISTRO_NAME docker --version 2>&1
    if ($dockerVersion -match "Docker version") {
        Write-InstallLog "Docker is available: $dockerVersion" -Source "Import-Distro"
    } else {
        Write-InstallLog "Docker may not be available" -Level WARN -Source "Import-Distro"
    }

    Complete-InstallSection -Name $currentSection -Status Success

    # ===================================================================
    # Final success
    # ===================================================================
    Write-InstallLog "" -Source "Import-Distro"
    Write-InstallLog "============================================================" -Source "Import-Distro"
    Write-InstallLog "DISTRIBUTION IMPORT COMPLETE" -Source "Import-Distro"
    Write-InstallLog "============================================================" -Source "Import-Distro"
    Write-InstallLog "The RecallHub WSL2 distribution has been imported." -Source "Import-Distro"
    Write-InstallLog "Location: $InstallPath" -Source "Import-Distro"
    Write-InstallLog "You can access it with: wsl -d $DISTRO_NAME" -Source "Import-Distro"

}
catch {
    # Capture failure diagnostics
    if ($currentSection) {
        Complete-InstallSection -Name $currentSection -Status Failed
    }

    $wslStatusInfo = ""
    try { $wslStatusInfo = (wsl --status 2>&1) -join "`n" } catch { $wslStatusInfo = "Unable to retrieve WSL status" }

    $diskFreeGB = 0
    try { $diskFreeGB = [math]::Round((Get-PSDrive C).Free / 1GB, 2) } catch {}

    Write-InstallDiagnostic -ErrorCode "DIST-099" -Component "Import" -Message $_.Exception.Message -Context @{
        wslStatus = $wslStatusInfo
        diskFree  = $diskFreeGB
    } -Exception $_.Exception

    # Try to get Docker logs if daemon was started
    try {
        $dockerLogs = wsl -d $DISTRO_NAME -- bash -c "cat /tmp/dockerd.log 2>&1 | tail -20"
        Write-InstallLog "Docker daemon logs:`n$($dockerLogs -join "`n")" -Level ERROR -Source "Import-Distro"
    } catch {}

    Export-InstallSummary
    throw
}

# Always export summary on success
Export-InstallSummary
Write-InstallLog "Distribution import completed successfully" -Source "Import-Distro"
