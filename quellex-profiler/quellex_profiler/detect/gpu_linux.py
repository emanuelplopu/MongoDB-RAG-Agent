"""Linux GPU detection via lspci (vendor classification) and rocm-smi (AMD VRAM)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from quellex_profiler.schema import GpuInfo


_VGA_LINE_RE = re.compile(r'"([^"]+)"\s+"([^"]+)"\s+"([^"]+)"')


def _run(cmd: list[str], timeout: int = 5) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return (-1, "")
    return (proc.returncode, proc.stdout or "")


def _parse_lspci(stdout: str) -> list[tuple[str, str]]:
    """Return list of ``(vendor_name, device_name)`` tuples for display adapters."""
    out: list[tuple[str, str]] = []
    for line in stdout.splitlines():
        low = line.lower()
        if not ("vga" in low or "3d controller" in low or "display controller" in low):
            continue
        # `lspci -mm` format: slot "Class" "Vendor" "Device" ...
        parts = _VGA_LINE_RE.findall(line)
        if len(parts) >= 3:
            _, vendor, device = parts[0], parts[1], parts[2]
            out.append((vendor, device))
    return out


def _vendor_from_lspci(vendor_str: str, device_str: str) -> str:
    v = vendor_str.lower()
    d = device_str.lower()
    if "nvidia" in v:
        return "nvidia"
    if "advanced micro devices" in v or "amd" in v or "ati" in v or "radeon" in d:
        return "amd"
    if "intel" in v:
        return "intel"
    return "unknown"


def _is_integrated(vendor: str, device: str) -> bool:
    d = device.lower()
    if vendor == "intel":
        # All Intel consumer GPUs except discrete "Arc" are integrated.
        return "arc " not in d
    if vendor == "amd":
        return "radeon graphics" in d or "vega" in d and "rx" not in d
    return False


def _rocm_vram_gb() -> float:
    if not shutil.which("rocm-smi"):
        return 0.0
    rc, out = _run(["rocm-smi", "--showmeminfo", "vram", "--json"], timeout=5)
    if rc != 0 or not out.strip():
        return 0.0
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return 0.0
    # rocm-smi returns something like {"card0": {"VRAM Total Memory (B)": "..."}}
    for _card, fields in data.items() if isinstance(data, dict) else []:
        if not isinstance(fields, dict):
            continue
        for k, v in fields.items():
            if "vram total memory" in k.lower() and "(b)" in k.lower():
                try:
                    return round(int(v) / (1024 ** 3), 1)
                except (TypeError, ValueError):
                    continue
    return 0.0


def detect_linux_gpus() -> list[GpuInfo]:
    """Return non-NVIDIA Linux GPUs (NVIDIA handled via nvidia-smi)."""
    if not shutil.which("lspci"):
        return []
    rc, out = _run(["lspci", "-mm"], timeout=5)
    if rc != 0 or not out:
        return []

    entries = _parse_lspci(out)
    gpus: list[GpuInfo] = []
    amd_vram_cache: float | None = None

    for vendor_str, device_str in entries:
        vendor = _vendor_from_lspci(vendor_str, device_str)
        if vendor == "nvidia":
            continue  # nvidia-smi handles it
        integrated = _is_integrated(vendor, device_str)

        vram_gb = 0.0
        if vendor == "amd" and not integrated:
            if amd_vram_cache is None:
                amd_vram_cache = _rocm_vram_gb()
            vram_gb = amd_vram_cache or 0.0
        # Intel Arc VRAM is not easily discoverable from CLI; leave 0 and let
        # the recommender treat it as "unknown dGPU" via model-name hints.

        gpus.append(
            GpuInfo(
                vendor=vendor,  # type: ignore[arg-type]
                model=device_str,
                vram_gb=vram_gb,
                is_integrated=integrated,
            )
        )
    return gpus
