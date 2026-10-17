"""Host / GPU / container machine stats for the superadmin stats widget.

This service backs ``GET /api/v1/system/host-stats`` (see
:mod:`backend.routers.system`). It composes the existing benchmark probes with
a handful of standard ``psutil`` reads, caches the result for a short TTL, and
shapes it into a :class:`~backend.models.schemas.HostStatsResponse`.

Architectural note — where each figure comes from
-------------------------------------------------
The API runs inside a **Linux container**, which bounds what is readable:

* **CPU / RAM / disk / uptime** are read by ``psutil`` inside the container. On
  Docker Desktop (Windows) these reflect the Docker Desktop / WSL2 VM, not the
  Windows host directly — they are the closest-to-host numbers the container can
  see. CPU utilisation reuses :class:`HostProbe` (which primes
  ``psutil.cpu_percent`` so readings are meaningful over the poll interval).
* **GPU temperature, power, utilisation and VRAM** come from ``nvidia-smi`` via
  :class:`GpuProbe`. These are *true GPU figures* because the GPU is passed
  through to the container.
* **CPU package temperature and power** genuinely require the Windows host. The
  benchmark sampler reads them with :class:`ThermalProbe` (Windows ``typeperf``)
  and would need host RAPL/vendor tooling for power — neither is reachable from
  the Linux container. They are therefore reported as ``None`` and the widget
  marks them unavailable. A best-effort ``psutil.sensors_temperatures`` read is
  attempted for CPU temperature so the field is populated on platforms (bare
  Linux hosts) where it *is* exposed.

Sampling is kept off the heavy path: it runs in a worker thread
(:func:`asyncio.to_thread`) and is cached for a few seconds, so rapid polling by
the widget does not spawn an ``nvidia-smi`` process per request.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import psutil

from backend.core.config import settings
from backend.evaluation.synthetic_corpus.resources import GpuProbe, HostProbe
from backend.models.schemas import HostStatsResponse, HostStatsSources

logger = logging.getLogger(__name__)

__all__ = ["HostStatsSampler", "get_host_stats_sampler", "collect_host_stats"]

#: MiB -> bytes (nvidia-smi reports GPU memory in MiB).
_MIB = 1024 * 1024

#: Default seconds a sampled snapshot is reused before re-sampling.
DEFAULT_TTL_SECONDS = 3.0

#: Fixed provenance of each metric group (see module docstring).
_SOURCES = HostStatsSources(
    cpu="container",
    ram="container",
    disk="container",
    cpu_temp="host",
    gpu_temp="gpu",
    cpu_power="host",
    gpu_power="gpu",
    uptime="container",
    note=(
        "CPU/RAM/disk/uptime are read inside the Linux container (the Docker "
        "Desktop VM on Windows); GPU temperature/power/VRAM come from nvidia-smi "
        "via GPU passthrough; CPU package temperature and power require the "
        "Windows host and are unavailable from the container."
    ),
)


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


class HostStatsSampler:
    """Samples host/GPU/container stats and caches the result for a short TTL.

    The probes are created once and reused across requests: :class:`GpuProbe`
    detects the richest ``nvidia-smi`` field set at construction, and
    :class:`HostProbe` primes ``psutil.cpu_percent`` so CPU utilisation is
    averaged over the time between samples rather than always reading zero.

    Args:
        ttl_seconds: How long a sampled snapshot is reused before re-sampling.
        disk_path: Explicit path to measure. ``None`` resolves it from settings
            (``host_stats_disk_path`` / ``document_roots``) at sample time.
        gpu_probe: Injected GPU probe (tests). Defaults to a real
            :class:`GpuProbe`.
        host_probe: Injected host probe (tests). Defaults to a real
            :class:`HostProbe`.
        disk_usage: Injected ``psutil.disk_usage`` replacement (tests).
        boot_time: Injected ``psutil.boot_time`` replacement (tests).
        virtual_memory: Injected ``psutil.virtual_memory`` replacement (tests).
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        disk_path: Optional[str] = None,
        gpu_probe: Optional[GpuProbe] = None,
        host_probe: Optional[HostProbe] = None,
        disk_usage: Optional[Callable[[str], Any]] = None,
        boot_time: Optional[Callable[[], float]] = None,
        virtual_memory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._ttl_seconds = float(ttl_seconds)
        self._disk_path_override = disk_path
        self._gpu_probe = gpu_probe if gpu_probe is not None else GpuProbe()
        self._host_probe = host_probe if host_probe is not None else HostProbe()
        self._disk_usage = disk_usage or psutil.disk_usage
        self._boot_time = boot_time or psutil.boot_time
        self._virtual_memory = virtual_memory or psutil.virtual_memory
        self._cache: Optional[HostStatsResponse] = None
        self._cache_ts: float = 0.0
        self._lock = asyncio.Lock()

    async def get_stats(self) -> HostStatsResponse:
        """Return a fresh or cached snapshot of host stats.

        Sampling runs in a worker thread so the event loop is never blocked by
        the ``nvidia-smi`` / ``psutil`` calls. Concurrent callers share one
        in-flight sample via an :class:`asyncio.Lock`.
        """
        async with self._lock:
            now = time.monotonic()
            if self._cache is not None and (now - self._cache_ts) < self._ttl_seconds:
                return self._cache.model_copy(update={"cached": True})
            raw = await asyncio.to_thread(self._collect_raw)
            response = self._build_response(raw)
            self._cache = response
            self._cache_ts = now
            return response

    # ── internals ────────────────────────────────────────────────────────

    def _resolve_disk_path(self) -> Optional[str]:
        """Pick the path to report disk usage for.

        An explicit constructor ``disk_path`` is trusted as-is. Otherwise the
        first existing candidate is chosen: the configured
        ``host_stats_disk_path`` setting, then each ``document_roots`` entry,
        then the container/filesystem root — so a typo'd setting degrades
        gracefully instead of breaking the endpoint.
        """
        if self._disk_path_override:
            return self._disk_path_override
        candidates: list[str] = []
        configured = (getattr(settings, "host_stats_disk_path", "") or "").strip()
        if configured:
            candidates.append(configured)
        roots = getattr(settings, "document_roots", "") or ""
        candidates.extend(r.strip() for r in roots.split(",") if r.strip())
        candidates.extend(["/app", "/", os.path.abspath(os.sep)])
        for path in candidates:
            try:
                if path and os.path.exists(path):
                    return path
            except OSError:
                continue
        return None

    def _collect_raw(self) -> dict[str, Any]:
        """Collect one raw sample (sync; safe to run in a thread; never raises)."""
        raw: dict[str, Any] = {}

        # CPU utilisation via the reused, primed HostProbe (container/VM view).
        try:
            host = self._host_probe.sample()
            raw["cpu_percent"] = host.get("cpu_pct")
        except Exception as exc:  # noqa: BLE001 - telemetry is best-effort
            logger.debug("host cpu sample failed: %s", exc)

        # RAM in exact bytes (container/VM view).
        try:
            vm = self._virtual_memory()
            raw["ram_used_bytes"] = int(vm.used)
            raw["ram_total_bytes"] = int(vm.total)
            raw["ram_percent"] = float(vm.percent)
        except Exception as exc:  # noqa: BLE001
            logger.debug("virtual_memory read failed: %s", exc)

        # Disk usage of the data volume as the container sees it.
        disk_path = self._resolve_disk_path()
        raw["disk_path"] = disk_path
        if disk_path:
            try:
                usage = self._disk_usage(disk_path)
                raw["disk_used_bytes"] = int(usage.used)
                raw["disk_total_bytes"] = int(usage.total)
                raw["disk_percent"] = float(usage.percent)
            except Exception as exc:  # noqa: BLE001
                logger.debug("disk_usage(%s) failed: %s", disk_path, exc)

        # Kernel uptime (the Docker Desktop VM on Windows).
        try:
            raw["uptime_seconds"] = max(0.0, time.time() - float(self._boot_time()))
        except Exception as exc:  # noqa: BLE001
            logger.debug("boot_time read failed: %s", exc)

        # GPU via the reused GpuProbe (true GPU figures through passthrough).
        gpu: Optional[dict[str, Any]] = None
        try:
            gpu = self._gpu_probe.read()
        except Exception as exc:  # noqa: BLE001
            logger.debug("gpu read failed: %s", exc)
        if gpu:
            raw["gpu_temp_c"] = gpu.get("temp_c")
            raw["gpu_power_watts"] = gpu.get("power_w")
            raw["gpu_percent"] = gpu.get("util_pct")
            if gpu.get("mem_mb") is not None:
                raw["vram_used_bytes"] = int(float(gpu["mem_mb"]) * _MIB)
            if gpu.get("mem_total_mb") is not None:
                raw["vram_total_bytes"] = int(float(gpu["mem_total_mb"]) * _MIB)
        gpu_name = (getattr(self._gpu_probe, "static", None) or {}).get("name")
        if gpu_name:
            raw["gpu_name"] = gpu_name

        # CPU package temperature: host-only on Windows. Attempt a cheap psutil
        # sensor read so bare-Linux hosts are covered; it returns nothing inside
        # the Windows-hosted container, which is the honest "unavailable".
        raw["cpu_temp_c"] = _read_cpu_temp_c()

        # CPU package power needs host RAPL / vendor tooling — not in-container.
        raw["cpu_power_watts"] = None
        return raw

    def _build_response(self, raw: dict[str, Any]) -> HostStatsResponse:
        """Shape a raw sample into the typed response model."""
        return HostStatsResponse(
            sampled_at=_utcnow(),
            cached=False,
            cpu_percent=raw.get("cpu_percent"),
            ram_used_bytes=raw.get("ram_used_bytes"),
            ram_total_bytes=raw.get("ram_total_bytes"),
            ram_percent=raw.get("ram_percent"),
            disk_path=raw.get("disk_path"),
            disk_used_bytes=raw.get("disk_used_bytes"),
            disk_total_bytes=raw.get("disk_total_bytes"),
            disk_percent=raw.get("disk_percent"),
            cpu_temp_c=raw.get("cpu_temp_c"),
            gpu_temp_c=raw.get("gpu_temp_c"),
            gpu_power_watts=raw.get("gpu_power_watts"),
            cpu_power_watts=raw.get("cpu_power_watts"),
            uptime_seconds=raw.get("uptime_seconds"),
            gpu_name=raw.get("gpu_name"),
            gpu_percent=raw.get("gpu_percent"),
            vram_used_bytes=raw.get("vram_used_bytes"),
            vram_total_bytes=raw.get("vram_total_bytes"),
            sources=_SOURCES,
        )


def _read_cpu_temp_c() -> Optional[float]:
    """Best-effort CPU temperature via ``psutil.sensors_temperatures``.

    Returns the hottest CPU-like sensor in degrees Celsius, or ``None`` when no
    sensors are exposed (the usual case inside the Linux container on Windows,
    and always on Windows itself where ``sensors_temperatures`` is absent).
    """
    sensors = getattr(psutil, "sensors_temperatures", None)
    if sensors is None:
        return None
    try:
        temps = sensors()
    except (AttributeError, OSError, NotImplementedError) as exc:
        logger.debug("sensors_temperatures failed: %s", exc)
        return None
    if not temps:
        return None
    preferred = ("coretemp", "k10temp", "cpu_thermal", "acpitz", "zenpower")
    values: list[float] = []
    for chip in preferred:
        for entry in temps.get(chip, []):
            if entry.current:
                values.append(float(entry.current))
    if not values:
        for entries in temps.values():
            for entry in entries:
                if entry.current:
                    values.append(float(entry.current))
    return round(max(values), 1) if values else None


_sampler: Optional[HostStatsSampler] = None


def get_host_stats_sampler() -> HostStatsSampler:
    """Return the process-wide :class:`HostStatsSampler`, creating it on first use."""
    global _sampler
    if _sampler is None:
        _sampler = HostStatsSampler()
    return _sampler


async def collect_host_stats() -> HostStatsResponse:
    """Return a fresh-or-cached :class:`HostStatsResponse` from the shared sampler."""
    return await get_host_stats_sampler().get_stats()
