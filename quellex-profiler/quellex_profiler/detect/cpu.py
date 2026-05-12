"""CPU brand, vendor, core counts, and instruction-set flags."""

from __future__ import annotations

import platform
from dataclasses import dataclass, field


@dataclass
class CpuInfo:
    brand: str = "Unknown CPU"
    vendor: str = "unknown"  # "intel", "amd", "apple", "arm", "unknown"
    cores_physical: int = 0
    cores_logical: int = 0
    max_ghz: float = 0.0
    flags: list[str] = field(default_factory=list)


def _vendor_from_brand(brand: str, raw_vendor: str) -> str:
    b = (brand or "").lower()
    v = (raw_vendor or "").lower()
    if "apple" in b or v == "apple":
        return "apple"
    if "intel" in b or "genuineintel" in v:
        return "intel"
    if "amd" in b or "authenticamd" in v:
        return "amd"
    if "arm" in b or platform.machine().lower() in ("arm64", "aarch64", "armv7l"):
        return "arm"
    return "unknown"


def detect_cpu() -> CpuInfo:
    info = CpuInfo()

    # Brand / flags via py-cpuinfo (best effort).
    try:
        import cpuinfo  # type: ignore

        raw = cpuinfo.get_cpu_info() or {}
        info.brand = raw.get("brand_raw") or raw.get("brand") or info.brand
        info.vendor = _vendor_from_brand(info.brand, raw.get("vendor_id_raw", ""))
        flags = raw.get("flags") or []
        # Keep only the flags we care about, to stay compact.
        keep = {
            "avx", "avx2", "avx512f", "avx512_bf16", "avx_vnni",
            "sse4_1", "sse4_2", "fma", "f16c",
            "neon", "asimd", "sve", "sve2",
            "amx_bf16", "amx_int8", "amx_tile",
        }
        info.flags = sorted({f for f in flags if f in keep})
        hz_advertised = raw.get("hz_advertised", [0, 0])
        if isinstance(hz_advertised, (list, tuple)) and hz_advertised:
            info.max_ghz = round(float(hz_advertised[0]) / 1e9, 2)
    except Exception:
        info.brand = platform.processor() or info.brand
        info.vendor = _vendor_from_brand(info.brand, "")

    # Core counts / frequency via psutil (best effort).
    try:
        import psutil  # type: ignore

        physical = psutil.cpu_count(logical=False) or 0
        logical = psutil.cpu_count(logical=True) or 0
        info.cores_physical = int(physical)
        info.cores_logical = int(logical)
        freq = psutil.cpu_freq()
        if freq and freq.max:
            # psutil returns MHz
            info.max_ghz = max(info.max_ghz, round(float(freq.max) / 1000.0, 2))
    except Exception:
        pass

    # Apple Silicon sometimes reports empty brand from platform.processor(); fix.
    if info.vendor == "apple" and (not info.brand or info.brand.lower() == "arm"):
        info.brand = "Apple Silicon"

    return info
