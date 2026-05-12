"""Apple GPU / Apple Silicon detection via system_profiler + sysctl."""

from __future__ import annotations

import json
import shutil
import subprocess

from quellex_profiler.schema import GpuInfo


def _run(cmd: list[str], timeout: int = 5) -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""


def _unified_memory_gb() -> float:
    out = _run(["sysctl", "-n", "hw.memsize"])
    try:
        return round(int(out.strip()) / (1024 ** 3), 1)
    except (ValueError, AttributeError):
        return 0.0


def detect_apple() -> list[GpuInfo]:
    """Return Apple/macOS GPUs via ``system_profiler``.

    On Apple Silicon we mark the GPU with ``unified_memory=True`` and set
    ``vram_gb`` to a conservative fraction of total unified memory (75%).
    """
    if not shutil.which("system_profiler"):
        return []

    stdout = _run(["system_profiler", "SPDisplaysDataType", "-json"], timeout=10)
    if not stdout:
        return []
    return parse_system_profiler_json(stdout, unified_memory_gb=_unified_memory_gb())


def parse_system_profiler_json(stdout: str, unified_memory_gb: float = 0.0) -> list[GpuInfo]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []

    entries = data.get("SPDisplaysDataType") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []

    gpus: list[GpuInfo] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        model = (
            item.get("sppci_model")
            or item.get("_name")
            or "Apple GPU"
        )
        vendor_raw = str(item.get("spdisplays_vendor") or item.get("sppci_vendor") or "").lower()
        is_apple = "apple" in vendor_raw or "apple" in model.lower()
        vendor = (
            "apple" if is_apple
            else ("amd" if "amd" in vendor_raw or "amd" in model.lower() else
                  ("intel" if "intel" in vendor_raw or "intel" in model.lower() else
                   ("nvidia" if "nvidia" in vendor_raw else "unknown")))
        )

        vram_gb = 0.0
        if is_apple and unified_memory_gb > 0:
            vram_gb = round(unified_memory_gb * 0.75, 1)
        else:
            for key in ("spdisplays_vram", "spdisplays_vram_shared", "_spdisplays_vram"):
                raw = item.get(key)
                if isinstance(raw, str) and raw:
                    vram_gb = _parse_vram_string(raw)
                    if vram_gb > 0:
                        break

        gpus.append(
            GpuInfo(
                vendor=vendor,  # type: ignore[arg-type]
                model=str(model),
                vram_gb=vram_gb,
                is_integrated=is_apple,   # Apple Silicon GPU is integrated/unified
                unified_memory=is_apple,
            )
        )
    return gpus


def _parse_vram_string(raw: str) -> float:
    """Parse strings like "8 GB", "1536 MB", "8192" (assumed MB)."""
    s = raw.strip().lower()
    try:
        if s.endswith("gb"):
            return round(float(s[:-2].strip()), 1)
        if s.endswith("mb"):
            return round(float(s[:-2].strip()) / 1024.0, 1)
        return round(float(s) / 1024.0, 1)
    except ValueError:
        return 0.0
