"""Telemetry hooks for the ingestion pipeline (checkpoint events and per-file step timings).

``src/`` must not depend on ``backend/``, so the pipeline only calls these functions; they do
nothing until a host process registers a sink with :func:`set_event_sink` (the API registers
:class:`backend.services.activity_events.ActivityEventRecorder`, so does the ingestion worker).
Every call is cheap, synchronous and never raises into the pipeline.

Events emitted here (persisted by the recorder into ``activity_events``):

* ``file.start`` — ``file: {path, name, ext, size_bytes}``
* ``file.end`` — the same ``file`` plus ``outcome`` (ok | empty | error | cancelled),
  ``duration_ms``, ``steps: {<step>_ms}``, ``chunks``, ``pages``, ``chars`` and on failure
  ``error_type`` / ``error``.

``state`` signals (live "what is happening now", never persisted) carry the current step so the
host agent can stamp it onto its samples.

The current ingestion job is bound with :func:`bind_job` (a ``ContextVar``), so tasks created
inside ``ingest_documents`` inherit it and events carry ``job_id`` without extra plumbing.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "EventSink",
    "FileTrace",
    "set_event_sink",
    "emit",
    "signal_state",
    "bind_job",
    "reset_job",
    "current_job_id",
    "file_scope",
    "step",
    "note",
]

#: ``sink(kind, fields)`` — must be non-blocking and thread-safe.
EventSink = Callable[[str, dict[str, Any]], None]

_sink: Optional[EventSink] = None
_job_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("ingest_job_id", default=None)
_trace: contextvars.ContextVar[Optional["FileTrace"]] = contextvars.ContextVar("ingest_file_trace", default=None)


def set_event_sink(sink: Optional[EventSink]) -> None:
    """Register (or clear with ``None``) the process-wide event sink."""
    global _sink
    _sink = sink


def emit(kind: str, **fields: Any) -> None:
    """Send one event to the sink; ``job_id`` defaults to the bound job. Never raises."""
    sink = _sink
    if sink is None:
        return
    fields.setdefault("job_id", _job_id.get())
    try:
        sink(kind, fields)
    except Exception as exc:  # noqa: BLE001 - telemetry must never break ingestion
        logger.debug("telemetry sink failed for %s: %s", kind, exc)


def signal_state(**fields: Any) -> None:
    """Update the live activity state (current step, progress); not stored as an event."""
    emit("state", **fields)


def bind_job(job_id: Optional[str]) -> contextvars.Token:
    """Bind the ingestion job for the current context; returns a token for :func:`reset_job`."""
    return _job_id.set(job_id)


def reset_job(token: contextvars.Token) -> None:
    """Undo :func:`bind_job`."""
    try:
        _job_id.reset(token)
    except ValueError:  # token from another context
        _job_id.set(None)


def current_job_id() -> Optional[str]:
    """The job bound to the current context, if any."""
    return _job_id.get()


@dataclass
class FileTrace:
    """Timings and counts collected while one file is ingested."""

    path: str
    size_bytes: Optional[int] = None
    started: float = field(default_factory=time.perf_counter)
    steps: dict[str, float] = field(default_factory=dict)
    counts: dict[str, Any] = field(default_factory=dict)
    outcome: Optional[str] = None

    @property
    def file_info(self) -> dict[str, Any]:
        """The ``file`` sub-document shared by file.start and file.end."""
        name = os.path.basename(self.path)
        info: dict[str, Any] = {"path": self.path, "name": name, "ext": os.path.splitext(name)[1].lower().lstrip(".")}
        if self.size_bytes is not None:
            info["size_bytes"] = self.size_bytes
        return info


def _file_size(path: str) -> Optional[int]:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


@contextmanager
def file_scope(file_path: str) -> Iterator[FileTrace]:
    """Emit ``file.start`` / ``file.end`` around one file and collect its step timings.

    The outcome is ``ok`` unless set otherwise via ``trace.outcome``; an exception sets
    ``error`` (or ``cancelled`` for task cancellation, e.g. a per-file timeout) and re-raises.
    """
    if _sink is None:
        yield FileTrace(path=file_path)
        return
    trace = FileTrace(path=file_path, size_bytes=_file_size(file_path))
    token = _trace.set(trace)
    emit("file.start", file=trace.file_info)
    signal_state(current_file=file_path, step=None)
    error: Optional[BaseException] = None
    try:
        yield trace
    except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
        error = exc
        raise
    finally:
        _trace.reset(token)
        fields: dict[str, Any] = {
            "file": trace.file_info,
            "duration_ms": round((time.perf_counter() - trace.started) * 1000, 1),
            "steps": {f"{name}_ms": round(ms, 1) for name, ms in trace.steps.items()},
            **trace.counts,
        }
        if error is None:
            fields["outcome"] = trace.outcome or "ok"
        else:
            cancelled = isinstance(error, asyncio.CancelledError)
            fields["outcome"] = "cancelled" if cancelled else "error"
            fields["error_type"] = type(error).__name__
            if not cancelled:
                fields["error"] = str(error)[:300]
        emit("file.end", **fields)
        signal_state(step=None)


@contextmanager
def step(name: str) -> Iterator[None]:
    """Time one step of the current file (adds ``<name>_ms`` to its ``file.end`` event)."""
    trace = _trace.get()
    if trace is None:
        yield
        return
    signal_state(step=name)
    started = time.perf_counter()
    try:
        yield
    finally:
        trace.steps[name] = trace.steps.get(name, 0.0) + (time.perf_counter() - started) * 1000


def note(**counts: Any) -> None:
    """Attach counts (chunks, pages, chars, ...) to the current file's ``file.end`` event."""
    trace = _trace.get()
    if trace is not None:
        trace.counts.update({k: v for k, v in counts.items() if v is not None})
