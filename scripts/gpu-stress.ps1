<#
.SYNOPSIS
    Run the GPU thermal / mixed-placement stress suite (llama.cpp in Docker).

.DESCRIPTION
    Wrapper around `quellexctl bench gpu-stress`. Starts the models of each
    scenario as llama.cpp containers (CUDA or CPU image), drives them with
    concurrent synthetic load, samples nvidia-smi (temperature, power, clocks,
    throttle reasons, VRAM) and host CPU/RAM every second, then writes a JSON
    result that can be compared across machines.

    Requires Docker with the NVIDIA Container Toolkit and nvidia-smi on the
    host. GGUFs are downloaded on first use into the `recallhub_embedder_models`
    volume (about 26 GB for the default Gemma 4 suite).

.EXAMPLE
    .\scripts\gpu-stress.ps1
    .\scripts\gpu-stress.ps1 -Only gemma4-26b-split,mixed-gpu-and-ram -Duration 180 -Output stress-gpu-box.json
    .\scripts\gpu-stress.ps1 -DryRun
    .\scripts\gpu-stress.ps1 -Cleanup
#>
param(
    [string]$Suite = "backend/config/gpu_stress/gemma4.yaml",
    [string[]]$Only = @(),
    [int]$Duration = 0,
    [int]$Cooldown = -1,
    [string]$Output = "",
    [switch]$Json,
    [switch]$DryRun,
    [switch]$Cleanup
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $env:PYTHONUTF8 = "1"
    $args = @()
    if ($Json) { $args += @("--format", "json") }
    $args += @("bench", "gpu-stress", "--suite", $Suite)
    foreach ($name in $Only) { $args += @("--only", $name) }
    if ($Duration -gt 0) { $args += @("--duration", $Duration) }
    if ($Cooldown -ge 0) { $args += @("--cooldown", $Cooldown) }
    if ($Output) { $args += @("--output", $Output) }
    if ($DryRun) { $args += "--dry-run" }
    if ($Cleanup) { $args += "--cleanup" }

    & uv run python -m backend.cli @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
