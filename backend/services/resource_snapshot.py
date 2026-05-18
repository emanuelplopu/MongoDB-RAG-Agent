"""Resource snapshot collector for the strategy experiment runner (Task 71).

This module provides :class:`ResourceSnapshotCollector`, a pure async
service that captures host-level utilization (CPU, RAM, GPU/VRAM), Ollama
model residency, and a conservative parallel-worker process count and
persists each snapshot to MongoDB with a 24h TTL.

Downstream consumers (notably the Phase 5
``StrategyExperimentRunner`` — Task 74 / P5) call :meth:`is_safe_to_run`
to gate the launch / continuation of a strategy experiment against a
:class:`backend.agent.strategy.scheduler_models.ResourceLimits` policy.

Design notes:

* AGENTS.md "ingestion off the request path" rule — the collector is
  cheap (single ``cpu_percent(interval=0.5)`` blocking sample) and is
  intended to be called either from a request handler that explicitly
  wants a snapshot, or from a long-running scheduler/worker loop. It is
  not registered as middleware.
* Repo rule #3: PyMongo Async semantics only. No Motor APIs are
  introduced.
* Pydantic v2 idioms throughout.
* Every external tool invocation is timeout-bounded and degrades to
  ``None`` / empty results on failure — the collector must never crash
  the runner.

Collection: ``resource_snapshots``. TTL is enforced via an
``expireAfterSeconds`` index on ``created_at``.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import socket
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.agent.strategy.scheduler_models import ResourceLimits
from backend.services.activity_tracker import ChatActivityTracker

logger = logging.getLogger(__name__)

__all__ = [
    "RESOURCE_SNAPSHOT_COLLECTION_NAME",
    "DEFAULT_WORKER_PROCESS_SUBSTRINGS",
    "ResourceSnapshot",
    "ResourceSnapshotCollector",
]


#: Canonical Mongo collection name for snapshot documents.
RESOURCE_SNAPSHOT_COLLECTION_NAME: str = "resource_snapshots"

#: Substrings used to identify "parallel worker" processes via ``cmdline``
#: matching. Conservative on purpose — false positives only inflate the
#: gating signal, they do not block legitimate user activity.
DEFAULT_WORKER_PROCESS_SUBSTRINGS: tuple[str, ...] = (
    "worker",
    "profiler",
    "scheduler_daemon",
)


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic schema
# ═══════════════════════════════════════════════════════════════════════════════


class ResourceSnapshot(BaseModel):
    """Point-in-time host resource snapshot.

    All ``*_gb`` fields are expressed in gibibytes (1 GiB = 2**30 bytes).
    All ``*_pct`` fields are 0-100 floats. GPU-related fields and the
    Ollama VRAM total are ``None`` when the corresponding tooling is not
    available on the host.

    The ``created_at`` timestamp is the TTL field — Mongo will purge
    documents ``ttl_hours`` after this value.

    Attributes:
        id: Unique snapshot identifier (UUID4 hex).
        host_id: Stable host identifier (defaults to ``socket.gethostname``).
        captured_at: When the metrics were sampled (UTC).
        cpu_pct: System-wide CPU utilization percentage (0-100).
        ram_used_gb: Used RAM in GiB.
        ram_total_gb: Total RAM in GiB.
        ram_pct: RAM utilization percentage (0-100).
        gpu_pct: GPU utilization percentage (0-100), ``None`` if no GPU
            visibility.
        vram_used_gb: VRAM in use, in GiB. ``None`` if no GPU visibility.
        vram_total_gb: Total VRAM, in GiB. ``None`` if no GPU visibility.
        vram_pct: VRAM utilization percentage. ``None`` if no GPU
            visibility.
        ollama_resident_models: Names of models currently resident in
            Ollama. Empty list when the endpoint is unreachable or the
            host has no Ollama installation.
        ollama_total_vram_gb: Aggregate VRAM (GiB) reported by Ollama
            for resident models, or ``None`` when not available.
        parallel_worker_count: Conservative count of background worker
            processes detected on the host.
        interactive_users_detected: Heuristic flag — see the docstring on
            :meth:`ResourceSnapshotCollector._detect_interactive_users`.
        created_at: TTL anchor timestamp.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    host_id: str = Field(description="Stable host identifier.")
    captured_at: datetime = Field(default_factory=_utcnow)

    cpu_pct: float = Field(ge=0.0, le=100.0)
    ram_used_gb: float = Field(ge=0.0)
    ram_total_gb: float = Field(ge=0.0)
    ram_pct: float = Field(ge=0.0, le=100.0)

    gpu_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    vram_used_gb: Optional[float] = Field(default=None, ge=0.0)
    vram_total_gb: Optional[float] = Field(default=None, ge=0.0)
    vram_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)

    ollama_resident_models: list[str] = Field(default_factory=list)
    ollama_total_vram_gb: Optional[float] = Field(default=None, ge=0.0)

    parallel_worker_count: int = Field(default=0, ge=0)
    interactive_users_detected: bool = Field(default=False)

    created_at: datetime = Field(default_factory=_utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# Collector
# ═══════════════════════════════════════════════════════════════════════════════


class ResourceSnapshotCollector:
    """Captures, persists and gates strategy runs on host resource state.

    Thread/loop safety: All public coroutines are safe to call from
    multiple asyncio tasks; the collector holds no shared mutable state
    other than the ``_indexes_ready`` flag, which is set monotonically.

    Args:
        db: An async MongoDB database handle (``pymongo.AsyncMongoClient``
            database). Repo rule #3: no Motor APIs are used. May be
            ``None`` for tests that only exercise pure capture logic — in
            that case persistence is a no-op.
        host_id: Optional explicit host identifier. Defaults to
            ``socket.gethostname()``.
        ttl_hours: Snapshot retention window. Used to compute the
            ``expireAfterSeconds`` value on the ``created_at`` index.
        ollama_endpoint: Base URL of the local Ollama HTTP API.
        worker_substrings: Iterable of substrings that, when found in a
            process ``cmdline`` (case-insensitive), classify the process
            as a parallel worker.
        cpu_sample_interval: Seconds passed to ``psutil.cpu_percent`` to
            obtain a non-zero sample. ``0.5`` is the default; tests may
            lower this to ``0.0`` to avoid blocking.
        activity_tracker: Optional :class:`ChatActivityTracker` used by
            :meth:`_detect_interactive_users`. When omitted the stub
            fallback behaviour from Task 71 is preserved (always
            returns ``False``) so legacy call sites and tests that
            construct a collector without the new argument keep
            working unchanged.
        activity_threshold_seconds: How recent the most recent chat
            activity must be (in wall-clock seconds) for
            ``interactive_users_detected`` to flip to ``True``. The
            default matches Jimmy's P2 follow-up suggestion of 30s.

    Example:
        >>> collector = ResourceSnapshotCollector(db)
        >>> snapshot = await collector.capture()
        >>> safe, reasons = await collector.is_safe_to_run(limits)

    """

    def __init__(
        self,
        db: Any,
        *,
        host_id: Optional[str] = None,
        ttl_hours: int = 24,
        ollama_endpoint: str = "http://localhost:11434",
        worker_substrings: Optional[tuple[str, ...]] = None,
        cpu_sample_interval: float = 0.5,
        activity_tracker: Optional[ChatActivityTracker] = None,
        activity_threshold_seconds: float = 30.0,
    ) -> None:
        if ttl_hours <= 0:
            raise ValueError("ttl_hours must be positive")
        if activity_threshold_seconds <= 0:
            raise ValueError("activity_threshold_seconds must be positive")
        self._db = db
        self._host_id = host_id or socket.gethostname()
        self._ttl_hours = int(ttl_hours)
        self._ollama_endpoint = ollama_endpoint.rstrip("/")
        self._worker_substrings = tuple(
            s.lower() for s in (worker_substrings or DEFAULT_WORKER_PROCESS_SUBSTRINGS)
        )
        self._cpu_sample_interval = float(cpu_sample_interval)
        self._activity_tracker = activity_tracker
        self._activity_threshold_seconds = float(activity_threshold_seconds)
        self._indexes_ready = False

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def collection(self) -> Any:
        """Return the underlying async Mongo collection handle, or ``None``."""
        if self._db is None:
            return None
        return self._db[RESOURCE_SNAPSHOT_COLLECTION_NAME]

    @property
    def host_id(self) -> str:
        """Stable host identifier this collector is reporting for."""
        return self._host_id

    # ── Public API ──────────────────────────────────────────────────────

    async def ensure_indexes(self) -> None:
        """Create snapshot indexes idempotently.

        Creates a TTL index on ``created_at`` with
        ``expireAfterSeconds = ttl_hours * 3600`` plus a compound
        ``(host_id, captured_at desc)`` index used by :meth:`latest`.

        Errors are logged and swallowed — index creation must not break
        the strategy runner lifecycle.
        """
        if self._indexes_ready:
            return
        col = self.collection
        if col is None:
            self._indexes_ready = True
            return
        try:
            await _maybe_await(
                col.create_index(
                    "created_at",
                    expireAfterSeconds=self._ttl_hours * 3600,
                )
            )
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning(
                "create_index(created_at, TTL) on %s failed (non-fatal): %s",
                RESOURCE_SNAPSHOT_COLLECTION_NAME,
                exc,
            )
        try:
            await _maybe_await(
                col.create_index([("host_id", 1), ("captured_at", -1)])
            )
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning(
                "create_index(host_id, captured_at) on %s failed (non-fatal): %s",
                RESOURCE_SNAPSHOT_COLLECTION_NAME,
                exc,
            )
        self._indexes_ready = True

    async def capture(self) -> ResourceSnapshot:
        """Sample current host state and persist a snapshot document.

        Returns:
            The validated :class:`ResourceSnapshot` that was persisted.
            When the collector was constructed without a database handle
            the snapshot is still returned but no document is written.
        """
        cpu_pct = await self._sample_cpu_percent()
        ram_used_gb, ram_total_gb, ram_pct = self._sample_memory()
        gpu_pct, vram_used_gb, vram_total_gb, vram_pct = self._sample_gpu()
        resident_models, total_vram_gb = await self._sample_ollama()
        worker_count = self._count_worker_processes()
        interactive = await self._detect_interactive_users()

        snapshot = ResourceSnapshot(
            host_id=self._host_id,
            cpu_pct=cpu_pct,
            ram_used_gb=ram_used_gb,
            ram_total_gb=ram_total_gb,
            ram_pct=ram_pct,
            gpu_pct=gpu_pct,
            vram_used_gb=vram_used_gb,
            vram_total_gb=vram_total_gb,
            vram_pct=vram_pct,
            ollama_resident_models=resident_models,
            ollama_total_vram_gb=total_vram_gb,
            parallel_worker_count=worker_count,
            interactive_users_detected=interactive,
        )

        col = self.collection
        if col is not None:
            try:
                await col.insert_one(snapshot.model_dump(mode="python"))
            except Exception as exc:  # noqa: BLE001 - best-effort persistence
                logger.warning(
                    "Failed to persist resource snapshot %s: %s",
                    snapshot.id,
                    exc,
                )
        return snapshot

    async def latest(
        self, *, host_id: Optional[str] = None
    ) -> Optional[ResourceSnapshot]:
        """Return the most recent persisted snapshot for ``host_id``.

        Args:
            host_id: Host filter. Defaults to this collector's host id.

        Returns:
            The most recent :class:`ResourceSnapshot` ordered by
            ``captured_at`` descending, or ``None`` when no snapshot is
            available (or persistence is disabled).
        """
        col = self.collection
        if col is None:
            return None
        target_host = host_id or self._host_id
        try:
            cursor = col.find({"host_id": target_host}).sort("captured_at", -1)
            async for raw in cursor:
                raw.pop("_id", None)
                try:
                    return ResourceSnapshot.model_validate(raw)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Skipping malformed snapshot for host=%s: %s",
                        target_host,
                        exc,
                    )
                    return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "latest() lookup on %s failed (treating as None): %s",
                RESOURCE_SNAPSHOT_COLLECTION_NAME,
                exc,
            )
            return None
        return None

    async def is_safe_to_run(
        self, limits: ResourceLimits
    ) -> tuple[bool, list[str]]:
        """Decide whether it is currently safe to launch experiments.

        Captures a fresh snapshot and compares each populated metric
        against the matching ceiling on ``limits``. Each violation
        produces a human-readable reason string. The decision is
        ``(True, [])`` only when every applicable rule passes.

        Behaviour notes:

        * Missing GPU telemetry is treated as a soft pass — we cannot
          enforce a GPU ceiling we cannot measure. The reason list is
          unaffected.
        * ``pause_if_interactive_users`` and
          ``pause_if_backend_chat_active`` are checked only when both the
          policy enables them and the snapshot heuristic flags activity.

        Args:
            limits: The runner's configured :class:`ResourceLimits`.

        Returns:
            Tuple ``(safe, reasons)`` where ``safe`` is ``True`` only
            when ``reasons`` is empty.
        """
        snapshot = await self.capture()
        reasons: list[str] = []

        if (
            limits.max_cpu_utilization_pct is not None
            and snapshot.cpu_pct > float(limits.max_cpu_utilization_pct)
        ):
            reasons.append(
                f"CPU {snapshot.cpu_pct:.1f}% exceeds limit "
                f"{limits.max_cpu_utilization_pct}%"
            )

        if (
            limits.max_ram_usage_pct is not None
            and snapshot.ram_pct > float(limits.max_ram_usage_pct)
        ):
            reasons.append(
                f"RAM {snapshot.ram_pct:.1f}% exceeds limit "
                f"{limits.max_ram_usage_pct}%"
            )

        if (
            limits.max_gpu_utilization_pct is not None
            and snapshot.gpu_pct is not None
            and snapshot.gpu_pct > float(limits.max_gpu_utilization_pct)
        ):
            reasons.append(
                f"GPU {snapshot.gpu_pct:.1f}% exceeds limit "
                f"{limits.max_gpu_utilization_pct}%"
            )

        if (
            limits.pause_if_interactive_users
            and snapshot.interactive_users_detected
        ):
            reasons.append("Interactive users detected; pausing experiments")

        if (
            limits.pause_if_backend_chat_active
            and snapshot.interactive_users_detected
        ):
            # Currently the same heuristic backs both signals — see
            # ``_detect_interactive_users`` for the follow-up split.
            if "Interactive users detected; pausing experiments" not in reasons:
                reasons.append(
                    "Recent backend /api/chat activity detected; pausing experiments"
                )

        return (len(reasons) == 0, reasons)

    # ── Sampling helpers (kept narrow + monkeypatch-friendly) ────────────

    async def _sample_cpu_percent(self) -> float:
        """Sample system-wide CPU utilization.

        Uses ``psutil.cpu_percent(interval=cpu_sample_interval)`` which
        blocks for ``cpu_sample_interval`` seconds. The call is dispatched
        to a thread to keep the event loop responsive.
        """
        psutil = _import_psutil()
        if psutil is None:
            return 0.0
        try:
            return float(
                await asyncio.to_thread(
                    psutil.cpu_percent, self._cpu_sample_interval
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("psutil.cpu_percent failed: %s", exc)
            return 0.0

    def _sample_memory(self) -> tuple[float, float, float]:
        """Sample system memory.

        Returns:
            Tuple ``(used_gb, total_gb, percent)``. All zeros when
            ``psutil`` is unavailable.
        """
        psutil = _import_psutil()
        if psutil is None:
            return (0.0, 0.0, 0.0)
        try:
            vm = psutil.virtual_memory()
            used_gb = float(vm.used) / (1024**3)
            total_gb = float(vm.total) / (1024**3)
            pct = float(vm.percent)
            return (used_gb, total_gb, pct)
        except Exception as exc:  # noqa: BLE001
            logger.warning("psutil.virtual_memory failed: %s", exc)
            return (0.0, 0.0, 0.0)

    def _sample_gpu(
        self,
    ) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """Sample GPU/VRAM via ``nvidia-smi``.

        Returns:
            Tuple ``(gpu_pct, vram_used_gb, vram_total_gb, vram_pct)``.
            All entries are ``None`` when ``nvidia-smi`` is not on PATH
            or fails.
        """
        binary = shutil.which("nvidia-smi")
        if not binary:
            return (None, None, None, None)
        try:
            completed = subprocess.run(
                [
                    binary,
                    "--query-gpu=utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("nvidia-smi invocation failed: %s", exc)
            return (None, None, None, None)
        if completed.returncode != 0 or not completed.stdout:
            return (None, None, None, None)
        first_line = completed.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in first_line.split(",")]
        if len(parts) < 3:
            return (None, None, None, None)
        try:
            gpu_pct = float(parts[0])
            mem_used_mib = float(parts[1])
            mem_total_mib = float(parts[2])
        except ValueError as exc:
            logger.warning("Could not parse nvidia-smi output %r: %s", first_line, exc)
            return (None, None, None, None)
        # nvidia-smi returns MiB; convert to GiB.
        vram_used_gb = mem_used_mib / 1024.0
        vram_total_gb = mem_total_mib / 1024.0
        vram_pct: Optional[float]
        if mem_total_mib > 0:
            vram_pct = max(0.0, min(100.0, (mem_used_mib / mem_total_mib) * 100.0))
        else:
            vram_pct = None
        return (
            max(0.0, min(100.0, gpu_pct)),
            vram_used_gb,
            vram_total_gb,
            vram_pct,
        )

    async def _sample_ollama(self) -> tuple[list[str], Optional[float]]:
        """Query Ollama's ``/api/ps`` endpoint for resident models.

        Returns:
            Tuple ``(model_names, total_vram_gb)``. The list is empty and
            the total ``None`` when the endpoint is unreachable, returns
            an error, or returns a malformed payload.
        """
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover - httpx is a hard repo dep
            logger.warning("httpx is not available; skipping Ollama probe")
            return ([], None)

        url = f"{self._ollama_endpoint}/api/ps"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001 - any transport / decode error
            logger.debug("Ollama probe failed for %s: %s", url, exc)
            return ([], None)

        models_raw = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models_raw, list):
            return ([], None)
        names: list[str] = []
        total_vram_bytes = 0
        any_size = False
        for entry in models_raw:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str) and name:
                names.append(name)
            size_vram = entry.get("size_vram")
            if isinstance(size_vram, (int, float)):
                total_vram_bytes += int(size_vram)
                any_size = True
        total_vram_gb = (
            float(total_vram_bytes) / (1024**3) if any_size else None
        )
        return (names, total_vram_gb)

    def _count_worker_processes(self) -> int:
        """Count parallel worker processes by ``cmdline`` substring match.

        The check is intentionally conservative — false positives are
        preferable to false negatives because the count is only used to
        widen the gating signal.
        """
        psutil = _import_psutil()
        if psutil is None:
            return 0
        count = 0
        try:
            iterator = psutil.process_iter(["name", "cmdline"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("psutil.process_iter failed: %s", exc)
            return 0
        for proc in iterator:
            try:
                info = proc.info if hasattr(proc, "info") else {}
                cmdline = info.get("cmdline") or []
                if not isinstance(cmdline, (list, tuple)):
                    continue
                joined = " ".join(str(part) for part in cmdline).lower()
                if not joined:
                    continue
                if any(needle in joined for needle in self._worker_substrings):
                    count += 1
            except Exception:  # noqa: BLE001 - per-process access errors
                continue
        return count

    async def _detect_interactive_users(self) -> bool:
        """Detect recent user-visible chat activity.

        Backed by an optional :class:`ChatActivityTracker` (Task 86 /
        F9). When no tracker is wired the method preserves the original
        Task 71 stub behaviour and returns ``False`` — keeping legacy
        call sites and tests that construct a collector without the new
        argument working unchanged.

        With a tracker present, the method reads back the wall-clock
        seconds since the most recent ``mark_active()`` call and flips
        to ``True`` when that delta is strictly less than
        ``activity_threshold_seconds`` (default 30s).

        Returns:
            ``True`` when chat activity has been observed within the
            configured window; ``False`` otherwise.
        """
        if self._activity_tracker is None:
            logger.debug(
                "_detect_interactive_users: no activity tracker configured; "
                "returning False (stub fallback)"
            )
            return False
        try:
            secs = await self._activity_tracker.seconds_since_last_active()
        except Exception as exc:  # noqa: BLE001 - never break capture()
            logger.warning(
                "_detect_interactive_users: tracker read failed (%s); "
                "returning False",
                exc,
            )
            return False
        return secs is not None and secs < self._activity_threshold_seconds


# ═══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _import_psutil() -> Any:
    """Import :mod:`psutil` lazily so missing-tool paths stay testable.

    Returns:
        The :mod:`psutil` module, or ``None`` when import fails. Never
        raises.
    """
    try:
        import psutil  # type: ignore[import-not-found]

        return psutil
    except Exception as exc:  # noqa: BLE001 - any import failure
        logger.warning("psutil import failed: %s", exc)
        return None


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` when it is a coroutine; otherwise return it.

    PyMongo Async returns coroutines, but test fakes may return plain
    strings. Mirrors the helper in
    :mod:`backend.agent.strategy.run_trace_store`.
    """
    if asyncio.iscoroutine(value):
        return await value
    return value
