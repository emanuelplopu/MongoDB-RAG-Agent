"""Host / GPU / container machine stats for the superadmin stats widget.

This service backs ``GET /api/v1/system/host-stats`` (see
:mod:`backend.routers.system`) and returns a
:class:`~backend.models.schemas.HostStatsResponse` from one of two sources:

1. **The Windows host agent** (``installer/host-agent``, preferred). It samples
   the real host every few seconds into the ``host_samples`` time-series
   collection: true host CPU/RAM/disk/uptime, CPU package power from the Windows
   RAPL "Energy Meter" counters, GPU temperature/power/VRAM from ``nvidia-smi``
   on the host, per-container CPU/memory from the Docker Engine API and — with
   the optional PawnIO sensor driver — CPU temperatures. :func:`collect_host_stats`
   maps the newest sample (no older than ``host_agent_stale_seconds``) with
   :func:`response_from_agent_sample`.
2. **The in-container fallback** (:class:`HostStatsSampler`), used while the
   agent is offline. The API runs inside a Linux container on Docker Desktop,
   so ``psutil`` there sees the Docker Desktop / WSL2 VM: its CPU, its RAM (not
   the host's), its kernel uptime. No GPU is attached to the API container in
   the compose files, so GPU figures are normally ``null``; CPU temperature and
   power are never readable there. Disk usage of a bind-mounted host folder is
   accurate. Sources are labelled per sample so the UI can mark each figure.

The fallback reuses the benchmark probes (:class:`GpuProbe`, :class:`HostProbe`
from ``synthetic_corpus/resources.py``), samples in a worker thread
(:func:`asyncio.to_thread`) and caches for a few seconds, so polling stays cheap.
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
from backend.models.schemas import HostActivityState, HostContainerStat, HostStatsResponse, HostStatsSources
from backend.services.telemetry_store import tenant_visible

logger = logging.getLogger(__name__)

__all__ = ["HostStatsSampler", "get_host_stats_sampler", "collect_host_stats", "response_from_agent_sample"]

#: MiB -> bytes (nvidia-smi reports GPU memory in MiB).
_MIB = 1024 * 1024

#: Default seconds a sampled snapshot is reused before re-sampling.
DEFAULT_TTL_SECONDS = 3.0

#: Note shown while the host agent is offline (in-container fallback view).
_CONTAINER_NOTE = (
    "Host agent offline: showing the API container's own view. On Docker Desktop "
    "that is the WSL2 VM (its CPU, RAM and uptime, not the Windows host's). GPU "
    "figures need nvidia-smi in the container and CPU temperature/power need the "
    "host, so they are usually unavailable here; disk usage of the bind-mounted "
    "data folder is accurate."
)

#: Note shown for host-agent samples.
_AGENT_NOTE = (
    "Read on the Windows host by the RecallHub host agent: host CPU/RAM/disk/uptime, "
    "CPU package power (Windows RAPL counters), GPU via nvidia-smi on the host, "
    "per-container use via the Docker Engine API. CPU temperature needs the "
    "optional PawnIO sensor driver (see sensors_status)."
)


def _container_sources(raw: dict[str, Any]) -> HostStatsSources:
    """Provenance for an in-container sample: GPU/temperature only when actually read."""
    return HostStatsSources(
        cpu="container",
        ram="container",
        disk="container",
        cpu_temp="container" if raw.get("cpu_temp_c") is not None else "unavailable",
        gpu_temp="gpu" if raw.get("gpu_temp_c") is not None else "unavailable",
        cpu_power="unavailable",
        gpu_power="gpu" if raw.get("gpu_power_watts") is not None else "unavailable",
        uptime="container",
        note=_CONTAINER_NOTE,
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

        # GPU via the reused GpuProbe — only when nvidia-smi exists in this container.
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
            source="container",
            agent_online=False,
            sources=_container_sources(raw),
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


def _num(value: Any) -> Optional[float]:
    """Float or None (rejects bools and non-numbers)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    """Int or None."""
    number = _num(value)
    return int(number) if number is not None else None


def _primary_disk(sample: dict[str, Any]) -> dict[str, Any]:
    """The primary drive entry of an agent sample (or the first one)."""
    disks = [d for d in (sample.get("disks") or []) if isinstance(d, dict)]
    for disk in disks:
        if disk.get("primary"):
            return disk
    return disks[0] if disks else {}


def response_from_agent_sample(
    sample: dict[str, Any], *, now: Optional[datetime] = None, tenant: Optional[str] = None
) -> HostStatsResponse:
    """Map one ``host_samples`` document (written by the host agent) to the widget response.

    Args:
        sample: A host-agent sample (layout documented in docs/12-TELEMETRY_SYSTEM.md).
        now: Reference time for the sample age (defaults to UTC now).
        tenant: Reader's tenant. The machine figures are host-wide, but the stamped activity
            (job, phase, current file) of other tenants is left out; ``None`` keeps all of it.

    Returns:
        A :class:`HostStatsResponse` with ``source="host-agent"``.
    """
    ts = sample.get("ts")
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    sampled_at = ts if isinstance(ts, datetime) else _utcnow()
    age = max(0.0, ((now or _utcnow()) - sampled_at).total_seconds())

    cpu = sample.get("cpu") or {}
    mem = sample.get("mem") or {}
    gpu = sample.get("gpu") or {}
    temp = sample.get("temp") or {}
    power = sample.get("power") or {}
    disk = _primary_disk(sample)
    sensors = sample.get("sensors") or {}

    containers = [
        HostContainerStat(
            name=str(c.get("name")),
            cpu_cores=_num(c.get("cpu_cores")),
            mem_bytes=_int(c.get("mem_bytes")),
            mem_limit_bytes=_int(c.get("mem_limit_bytes")),
        )
        for c in (sample.get("containers") or [])
        if isinstance(c, dict) and c.get("name")
    ]
    containers.sort(key=lambda c: c.cpu_cores or 0.0, reverse=True)

    activity = [
        HostActivityState(**{k: a.get(k) for k in HostActivityState.model_fields if k in a})
        for a in (sample.get("activity") or [])
        if isinstance(a, dict) and tenant_visible(a.get("tenant"), tenant)
    ]

    cpu_temp = _num(temp.get("cpu_pkg_c"))
    gpu_temp = _num(temp.get("gpu_c"))
    cpu_power = _num(power.get("cpu_pkg_w"))
    gpu_power = _num(power.get("gpu_w"))
    return HostStatsResponse(
        sampled_at=sampled_at,
        cached=False,
        source="host-agent",
        agent_online=True,
        agent_sample_age_seconds=round(age, 1),
        sensors_status=sensors.get("status"),
        cpu_percent=_num(cpu.get("pct")),
        ram_used_bytes=_int(mem.get("used_bytes")),
        ram_total_bytes=_int(mem.get("total_bytes")),
        ram_percent=_num(mem.get("pct")),
        disk_path=disk.get("path"),
        disk_used_bytes=_int(disk.get("used_bytes")),
        disk_total_bytes=_int(disk.get("total_bytes")),
        disk_percent=_num(disk.get("pct")),
        cpu_temp_c=cpu_temp,
        gpu_temp_c=gpu_temp,
        gpu_power_watts=gpu_power,
        cpu_power_watts=cpu_power,
        uptime_seconds=_num(sample.get("uptime_s")),
        gpu_name=gpu.get("name"),
        gpu_percent=_num(gpu.get("util_pct")),
        vram_used_bytes=_int(gpu.get("vram_used_bytes")),
        vram_total_bytes=_int(gpu.get("vram_total_bytes")),
        containers=containers,
        activity=activity,
        sources=HostStatsSources(
            cpu="host",
            ram="host",
            disk="host",
            cpu_temp="host" if cpu_temp is not None else "unavailable",
            gpu_temp="gpu" if gpu_temp is not None else "unavailable",
            cpu_power="host" if cpu_power is not None else "unavailable",
            gpu_power="gpu" if gpu_power is not None else "unavailable",
            uptime="host",
            note=_AGENT_NOTE,
        ),
    )


async def collect_host_stats(store: Optional[Any] = None) -> HostStatsResponse:
    """Return host stats, preferring a fresh host-agent sample over the container view.

    Args:
        store: The :class:`~backend.services.telemetry_store.TelemetryStore`
            (``app.state.telemetry_store``) or ``None`` when telemetry history
            is disabled.

    Returns:
        A host-agent response when a sample younger than
        ``host_agent_stale_seconds`` exists, else the cached in-container view.
    """
    if store is not None:
        try:
            sample = await store.latest_host_sample(max_age_seconds=float(settings.host_agent_stale_seconds))
        except Exception as exc:  # noqa: BLE001 - fall back rather than fail the widget
            logger.debug("host-agent sample read failed, using container view: %s", exc)
            sample = None
        if sample:
            return response_from_agent_sample(sample, tenant=getattr(store, "tenant_scope", None))
    return await get_host_stats_sampler().get_stats()
