"""Windows GPU enumeration via PowerShell Get-CimInstance Win32_VideoController.

Covers AMD Radeon, Intel Arc/Iris/UHD, and any non-NVIDIA device Windows
exposes through WMI. NVIDIA cards are handled separately via ``nvidia-smi``.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from quellex_profiler.schema import GpuInfo


PS_COMMAND = (
    "Get-CimInstance Win32_VideoController | "
    "Select-Object Name, AdapterRAM, DriverVersion | "
    "ConvertTo-Json -Compress"
)


_INTEGRATED_MARKERS = (
    "uhd graphics", "hd graphics", "iris", "vega graphics",
    "radeon graphics", "radeon(tm) graphics", "apple",
    # Ryzen AI / APU integrated Radeon naming patterns (e.g. "Radeon(TM) 890M Graphics").
    "780m graphics", "880m graphics", "890m graphics", "8050s",
    "680m graphics", "660m graphics", "610m graphics",
)


def _is_amd_apu_igpu(name: str) -> bool:
    """Heuristic: AMD Ryzen APU integrated Radeon graphics include an ``M Graphics`` suffix."""
    n = name.lower()
    return ("radeon" in n) and (" graphics" in n) and (
        "m graphics" in n or "radeon graphics" in n
    )


def _classify_vendor(name: str) -> str:
    n = name.lower()
    if "nvidia" in n or "geforce" in n or "rtx" in n or "quadro" in n or "tesla" in n:
        return "nvidia"
    if "amd" in n or "radeon" in n or "rx " in n:
        return "amd"
    if "intel" in n or "arc " in n or "iris" in n or "uhd" in n or "hd graphics" in n:
        return "intel"
    return "unknown"


def _is_integrated(name: str) -> bool:
    n = name.lower()
    if any(m in n for m in _INTEGRATED_MARKERS):
        return True
    return _is_amd_apu_igpu(n)


def detect_windows_gpus() -> list[GpuInfo]:
    """Return list of GPUs reported by WMI, excluding NVIDIA (handled elsewhere)."""
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return []

    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", PS_COMMAND],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if proc.returncode != 0 or not proc.stdout.strip():
        return []

    return parse_win32_json(proc.stdout)


def parse_win32_json(stdout: str) -> list[GpuInfo]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []

    gpus: list[GpuInfo] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").strip()
        if not name:
            continue
        vendor = _classify_vendor(name)
        # Skip NVIDIA here; nvidia-smi gives us reliable VRAM.
        if vendor == "nvidia":
            continue

        integrated = _is_integrated(name)

        # AdapterRAM is uint32, max 4 GiB, and is unreliable for iGPU/dGPU alike.
        vram_gb = 0.0
        adapter_ram = item.get("AdapterRAM")
        if isinstance(adapter_ram, (int, float)) and adapter_ram > 0:
            vram_gb = round(float(adapter_ram) / (1024 ** 3), 1)
        # Cap at 2 GB "shared" for integrated where value looks bogus.
        if integrated and vram_gb > 2.0:
            vram_gb = 2.0

        gpus.append(
            GpuInfo(
                vendor=vendor if vendor != "unknown" else "unknown",
                model=name,
                vram_gb=vram_gb,
                is_integrated=integrated,
                driver_version=str(item.get("DriverVersion") or "") or None,
            )
        )
    return gpus
