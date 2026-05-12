"""NVIDIA GPU detection via nvidia-smi (works on Windows/Linux/WSL)."""

from __future__ import annotations

import shutil
import subprocess

from quellex_profiler.schema import GpuInfo


NVIDIA_SMI_ARGS = [
    "--query-gpu=name,memory.total,driver_version,compute_cap",
    "--format=csv,noheader,nounits",
]


def detect_nvidia() -> list[GpuInfo]:
    """Return list of NVIDIA GPUs via ``nvidia-smi`` (empty list if absent)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, *NVIDIA_SMI_ARGS],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if proc.returncode != 0 or not proc.stdout:
        return []

    return parse_nvidia_smi(proc.stdout)


def parse_nvidia_smi(stdout: str) -> list[GpuInfo]:
    gpus: list[GpuInfo] = []
    for line in stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        name = parts[0]
        try:
            vram_mib = float(parts[1])
            vram_gb = round(vram_mib / 1024.0, 1)
        except ValueError:
            vram_gb = 0.0
        driver = parts[2] if len(parts) > 2 else None
        cc = parts[3] if len(parts) > 3 else None
        gpus.append(
            GpuInfo(
                vendor="nvidia",
                model=name,
                vram_gb=vram_gb,
                is_integrated=False,
                compute_capability=cc or None,
                driver_version=driver or None,
            )
        )
    return gpus
