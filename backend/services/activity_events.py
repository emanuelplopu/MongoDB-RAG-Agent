"""Activity event recorder: checkpoint events plus the live "what is running now" state.

One :class:`ActivityEventRecorder` runs per process (the API and the ingestion worker each own
one). Producers call :meth:`ActivityEventRecorder.emit` — synchronous, thread-safe and
non-blocking — and a background task flushes events in batches to the ``activity_events``
time-series collection about once a second (see :mod:`backend.services.telemetry_store`).

Event kinds:

* ``job.status`` (``status``: running, paused, completed, failed, stopped, cancelled, interrupted)
  and ``job.phase`` (``phase``: initializing, discovering, filtering, cleaning, processing,
  finalizing, ...), emitted by the API and worker job runners;
* ``file.start`` / ``file.end`` with per-step timings, emitted by the pipeline through
  :mod:`src.ingestion.telemetry_hooks`;
* ``chat.request`` with durations, emitted by the agent coordinator.

``state`` signals are not stored as events: they update the in-memory state that is written to
``activity_current`` (key ``<tenant>:<source>``) at most once a second, refreshed every
``refresh_interval`` seconds while a job or chat is active so the Windows host agent sees it as
fresh and stamps it onto its samples, and rewritten every ``heartbeat_interval`` seconds while
idle so ``updated_at`` tells a quiet process from a dead one (the telemetry overview shows it as
"last seen").

Tenants: an API process records everything under its own tenant. The ingestion worker is shared
by every tenant on the host, so its recorder runs under :data:`~backend.services.telemetry_store.SHARED_TENANT`
(or the worker's ``TENANT_ID``) and :meth:`ActivityEventRecorder.bind_job_tenant` attributes a
job's events, and the live state while it runs, to the tenant recorded on the job.

Events hold internal file paths (same sensitivity as the indexed documents; TTL-limited).
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.ingestion import telemetry_hooks

logger = logging.getLogger(__name__)

__all__ = [
    "ActivityEventRecorder",
    "TERMINAL_JOB_STATUSES",
    "get_activity_recorder",
    "set_activity_recorder",
    "record_event",
]

#: Job statuses after which the live state is cleared.
TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "stopped", "cancelled", "interrupted"})

_JOB_STATE_FIELDS = ("job_id", "job_status", "phase", "step", "current_file", "processed_files", "total_files")
_CHAT_ACTIVE_WINDOW = timedelta(seconds=60)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ActivityEventRecorder:
    """Buffers activity events and the live state of one process, flushing them to MongoDB.

    Args:
        store: A :class:`~backend.services.telemetry_store.TelemetryStore`.
        tenant: Tenant id of this process (``settings.tenant_id``), or ``"shared"`` for the
            ingestion worker; events of a job bound with :meth:`bind_job_tenant` use the job's.
        source: ``"api"`` or ``"worker"``.
        host: Host name recorded in ``activity_current`` (defaults to this machine).
        flush_interval: Seconds between flushes.
        refresh_interval: Seconds between ``activity_current`` refreshes while something runs.
        heartbeat_interval: Seconds between ``activity_current`` rewrites while idle (liveness).
        max_batch: Events per ``insert_many``.
        max_queue: Events kept while MongoDB is unreachable (oldest dropped first).
    """

    def __init__(
        self,
        store: Any,
        *,
        tenant: str,
        source: str,
        host: Optional[str] = None,
        flush_interval: float = 1.0,
        refresh_interval: float = 15.0,
        heartbeat_interval: float = 30.0,
        max_batch: int = 500,
        max_queue: int = 50_000,
    ) -> None:
        self._store = store
        self._tenant = tenant
        self._source = source
        self._key = f"{tenant}:{source}"
        self._flush_interval = flush_interval
        self._refresh_interval = refresh_interval
        self._heartbeat_interval = max(float(heartbeat_interval), flush_interval)
        self._max_batch = max_batch
        self._max_queue = max_queue
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"tenant": tenant, "source": source, "host": host or socket.gethostname(),
                                       **{f: None for f in _JOB_STATE_FIELDS}, "chat_last_active_at": None}
        self._job_tenants: dict[str, str] = {}
        self._dirty = True
        self._last_state_write = 0.0
        self._dropped = 0
        self._task: Optional[asyncio.Task[None]] = None
        self._stopping = False

    @property
    def key(self) -> str:
        """``activity_current`` document id of this process."""
        return self._key

    @property
    def tenant(self) -> str:
        """Default tenant of this process's events."""
        return self._tenant

    def bind_job_tenant(self, job_id: str, tenant: Optional[str]) -> None:
        """Attribute the events of ``job_id`` (and the live state while it runs) to ``tenant``.

        Args:
            job_id: Ingestion job id.
            tenant: The job's tenant; empty keeps this process's default tenant.
        """
        if not job_id or not tenant:
            return
        with self._lock:
            self._job_tenants[job_id] = tenant
        self._update_state({"tenant": tenant})

    def release_job_tenant(self, job_id: str) -> None:
        """Forget the tenant of a finished job and report the process's own tenant again."""
        with self._lock:
            self._job_tenants.pop(job_id, None)
        self._update_state({"tenant": self._tenant})

    def tenant_for(self, job_id: Optional[str]) -> str:
        """Tenant label of an event of ``job_id`` (or of the running job when it has no id)."""
        with self._lock:
            key = job_id or self._state.get("job_id")
            return self._job_tenants.get(key, self._tenant) if key else self._tenant

    @property
    def dropped(self) -> int:
        """Events dropped because the queue was full (MongoDB unreachable for long)."""
        return self._dropped

    def pending(self) -> int:
        """Events waiting to be flushed."""
        with self._lock:
            return len(self._queue)

    def state(self) -> dict[str, Any]:
        """A copy of the live state."""
        with self._lock:
            return dict(self._state)

    # ── producer API (sync, thread-safe, never raises) ───────────────────

    def emit(self, kind: str, fields: dict[str, Any]) -> None:
        """Record one event (or a ``state`` signal). Matches the telemetry_hooks sink signature."""
        try:
            fields = dict(fields)
            if kind == "state":
                self._update_state({k: v for k, v in fields.items() if k != "job_id"})
                return
            job_id = fields.pop("job_id", None)
            now = _utcnow()
            meta = {"kind": kind, "source": self._source, "tenant": self.tenant_for(job_id), "job_id": job_id}
            doc = {"ts": now, "meta": meta, **fields}
            with self._lock:
                if len(self._queue) >= self._max_queue:
                    self._queue.popleft()
                    self._dropped += 1
                self._queue.append(doc)
            self._apply_to_state(kind, job_id, fields, now)
        except Exception as exc:  # noqa: BLE001 - telemetry must never break the caller
            logger.debug("activity event %s dropped: %s", kind, exc)

    def job_status(self, job_id: Optional[str], status: str, **fields: Any) -> None:
        """Record a job status transition (running, paused, completed, ...)."""
        self.emit("job.status", {"job_id": job_id, "status": str(status).lower(), **fields})

    def job_phase(self, job_id: Optional[str], phase: str, **fields: Any) -> None:
        """Record a job phase transition (discovering, processing, ...)."""
        self.emit("job.phase", {"job_id": job_id, "phase": phase, **fields})

    def job_progress(self, job_id: Optional[str], processed: Optional[int], total: Optional[int]) -> None:
        """Update live progress (not stored as an event)."""
        self._update_state({"job_id": job_id, "processed_files": processed, "total_files": total})

    def mark_chat(self) -> None:
        """Note chat activity now (live state only)."""
        self._update_state({"chat_last_active_at": _utcnow()})

    # ── state ────────────────────────────────────────────────────────────

    def _update_state(self, changes: dict[str, Any]) -> None:
        with self._lock:
            for key, value in changes.items():
                if key in self._state and self._state[key] != value:
                    self._state[key] = value
                    self._dirty = True

    def _apply_to_state(self, kind: str, job_id: Optional[str], fields: dict[str, Any], now: datetime) -> None:
        if kind == "job.status":
            status = fields.get("status")
            if status in TERMINAL_JOB_STATUSES:
                self._update_state({f: None for f in _JOB_STATE_FIELDS})
            else:
                self._update_state({"job_id": job_id, "job_status": status})
        elif kind == "job.phase":
            self._update_state({"job_id": job_id, "phase": fields.get("phase")})
        elif kind == "file.start":
            file_info = fields.get("file") or {}
            self._update_state({"job_id": job_id or self._state.get("job_id"), "current_file": file_info.get("path")})
        elif kind == "chat.request":
            self._update_state({"chat_last_active_at": now})

    def _active(self, now: datetime) -> bool:
        chat = self._state.get("chat_last_active_at")
        return self._state.get("job_id") is not None or (chat is not None and now - chat < _CHAT_ACTIVE_WINDOW)

    # ── flushing ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background flush task and register as the pipeline's telemetry sink."""
        telemetry_hooks.set_event_sink(self.emit)
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name=f"activity-recorder-{self._source}")

    async def stop(self) -> None:
        """Stop the flush task and write what is still buffered."""
        self._stopping = True
        telemetry_hooks.set_event_sink(None)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown
                pass
            self._task = None
        await self.flush(force_state=True)

    async def _run(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self._flush_interval)
            try:
                await self.flush()
            except Exception as exc:  # noqa: BLE001 - keep flushing
                logger.debug("activity flush failed: %s", exc)

    async def flush(self, *, force_state: bool = False) -> int:
        """Write buffered events and, when due, the live state. Returns events written."""
        written = 0
        while True:
            with self._lock:
                batch = [self._queue.popleft() for _ in range(min(self._max_batch, len(self._queue)))]
            if not batch:
                break
            try:
                written += await self._store.insert_events(batch)
            except Exception as exc:  # noqa: BLE001 - keep the batch for the next flush
                with self._lock:
                    self._queue.extendleft(reversed(batch))
                    while len(self._queue) > self._max_queue:
                        self._queue.popleft()
                        self._dropped += 1
                logger.debug("activity events not written yet (%d pending): %s", len(batch), exc)
                break
        await self._write_state(force=force_state)
        return written

    async def _write_state(self, *, force: bool) -> None:
        now = _utcnow()
        with self._lock:
            since_write = time.monotonic() - self._last_state_write
            due = (self._dirty
                   or (self._active(now) and since_write >= self._refresh_interval)
                   or since_write >= self._heartbeat_interval)
            if not (due or force):
                return
            snapshot = {**self._state, "updated_at": now}
            self._dirty = False
        try:
            await self._store.upsert_activity_current(self._key, snapshot)
            self._last_state_write = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._dirty = True
            logger.debug("activity_current not written: %s", exc)


_recorder: Optional[ActivityEventRecorder] = None


def get_activity_recorder() -> Optional[ActivityEventRecorder]:
    """The process-wide recorder, or None when telemetry history is disabled."""
    return _recorder


def set_activity_recorder(recorder: Optional[ActivityEventRecorder]) -> None:
    """Install (or clear) the process-wide recorder."""
    global _recorder
    _recorder = recorder


def record_event(kind: str, **fields: Any) -> None:
    """Emit an event through the process recorder; a no-op when none is installed."""
    recorder = _recorder
    if recorder is not None:
        recorder.emit(kind, fields)
