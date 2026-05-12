"""Hardware and OS detection package.

Public entry point: :func:`collect_profile` which returns a populated
:class:`~quellex_profiler.schema.SystemProfile`. All detection is best-effort:
each sub-detector is wrapped to fail soft and append a note rather than raise.
"""

from __future__ import annotations

from quellex_profiler.schema import SystemProfile
from quellex_profiler.detect.os_info import detect_os
from quellex_profiler.detect.cpu import detect_cpu
from quellex_profiler.detect.memory import detect_ram_gb
from quellex_profiler.detect.gpu import detect_gpus


def collect_profile() -> SystemProfile:
    """Collect OS/CPU/RAM/GPU info into a single SystemProfile."""
    notes: list[str] = []

    os_name, os_version, arch = detect_os()
    cpu = detect_cpu()
    ram = detect_ram_gb()
    gpus, gpu_notes = detect_gpus()
    notes.extend(gpu_notes)

    is_apple_silicon = (cpu.vendor == "apple") or (
        os_name.lower().startswith("macos") and arch in ("arm64", "aarch64")
    )

    # Total "usable" accel VRAM: sum of discrete GPUs; for Apple, use unified
    # memory (a conservative ~75% of RAM is usable for models, capped later).
    if is_apple_silicon and ram > 0:
        total_accel = round(ram * 0.75, 1)
    else:
        total_accel = round(
            sum(g.vram_gb for g in gpus if not g.is_integrated), 1
        )

    return SystemProfile(
        os_name=os_name,
        os_version=os_version,
        arch=arch,
        cpu_brand=cpu.brand,
        cpu_vendor=cpu.vendor,
        cpu_cores_physical=cpu.cores_physical,
        cpu_cores_logical=cpu.cores_logical,
        cpu_max_ghz=cpu.max_ghz,
        cpu_flags=cpu.flags,
        ram_gb=ram,
        gpus=gpus,
        total_accel_vram_gb=total_accel,
        is_apple_silicon=is_apple_silicon,
        detection_notes=notes,
    )


__all__ = ["collect_profile"]
