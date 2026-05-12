# PyInstaller build helper for Windows.
# Produces: dist\quellex-profiler.exe (x86_64) or dist\quellex-profiler-arm64.exe
# Usage:
#   .\build.ps1              # build onefile for this host arch
#   .\build.ps1 -Clean       # wipe build/dist first
#   .\build.ps1 -Console     # keep the console window (default: windowed)

param(
    [switch]$Clean,
    [switch]$Console
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtualenv..."
    python -m venv .venv
}

Write-Host "Installing dependencies..."
.\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
.\.venv\Scripts\python.exe -m pip install -e ".[build]" | Out-Null

if ($Clean) {
    Write-Host "Cleaning build artifacts..."
    Remove-Item -Recurse -Force build, dist, *.spec -ErrorAction SilentlyContinue
}

$arch = (Get-CimInstance Win32_Processor).Architecture
# 5 = ARM, 9 = x64, 12 = ARM64
$archSuffix = if ($arch -eq 12 -or $arch -eq 5) { "arm64" } else { "x64" }
$exeName = "quellex-profiler-win-$archSuffix"

$pyiArgs = @(
    "--onefile",
    "--name", $exeName,
    "--add-data", "quellex_profiler/ui;quellex_profiler/ui",
    "--hidden-import", "quellex_profiler.detect.gpu_windows",
    "--hidden-import", "quellex_profiler.detect.gpu_apple",
    "--hidden-import", "quellex_profiler.detect.gpu_linux"
)
if (-not $Console) {
    # On Windows we keep console because the tool prints the local URL.
    # Use -Console to explicitly request the console build (same effect here).
}

Write-Host "Running PyInstaller -> $exeName.exe"
.\.venv\Scripts\pyinstaller.exe @pyiArgs quellex_profiler/__main__.py

Write-Host ""
Write-Host "Build complete:"
Get-ChildItem dist | Format-Table -AutoSize
