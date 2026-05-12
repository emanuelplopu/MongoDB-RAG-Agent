"""Cross-platform GPU dispatcher.

Tries ``nvidia-smi`` first on every OS, then falls back to the OS-native
enumeration (WMI on Windows, ``system_profiler`` on macOS, ``lspci``/``rocm-smi``
on Linux). Every step is fail-soft; errors are accumulated as notes.
"""

from __future__ import annotations

import platform

from quellex_profiler.schema import GpuInfo
from quellex_profiler.detect.gpu_nvidia import detect_nvidia


def detect_gpus() -> tuple[list[GpuInfo], list[str]]:
    notes: list[str] = []
    gpus: list[GpuInfo] = []

    # 1) NVIDIA (works on Windows/Linux/WSL).
    try:
        nvidia = detect_nvidia()
        if nvidia:
            gpus.extend(nvidia)
    except Exception as exc:  # pragma: no cover - defensive
        notes.append(f"nvidia-smi failed: {exc!r}")

    # 2) OS-native fallback.
    system = platform.system()
    try:
        if system == "Windows":
            from quellex_profiler.detect.gpu_windows import detect_windows_gpus

            gpus.extend(detect_windows_gpus())
        elif system == "Darwin":
            from quellex_profiler.detect.gpu_apple import detect_apple

            gpus.extend(detect_apple())
        elif system == "Linux":
            from quellex_profiler.detect.gpu_linux import detect_linux_gpus

            gpus.extend(detect_linux_gpus())
        else:
            notes.append(f"Unsupported OS for GPU detection: {system!r}")
    except Exception as exc:  # pragma: no cover - defensive
        notes.append(f"OS GPU detection failed: {exc!r}")

    # Deduplicate by (vendor, model) - nvidia-smi + WMI may both report the same card.
    seen: set[tuple[str, str]] = set()
    unique: list[GpuInfo] = []
    for g in gpus:
        key = (g.vendor, g.model.lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(g)

    if not unique:
        notes.append("No GPUs detected; system will be classified as CPU-only.")

    return unique, notes
