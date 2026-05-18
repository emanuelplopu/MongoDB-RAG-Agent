"""Chat activity tracker (Task 86 / F9).

This module provides :class:`ChatActivityTracker`, a tiny shared service
used by chat-producing routers to stamp the wall-clock time of the most
recent user-visible chat request. The
:class:`backend.services.resource_snapshot.ResourceSnapshotCollector`
reads back this timestamp to populate ``interactive_users_detected``
without taking a request-path dependency or relying on heuristic
process / kernel signals.

Two storage modes are supported:

* ``"in_process"`` – stores the last-active timestamp in a
  ``time.monotonic()`` slot on the instance. Cheap, accurate within a
  single process, and the right default for tests / single-worker
  deployments.
* ``"mongo"`` – upserts a small document into the ``runtime_signals``
  collection keyed by ``chat_activity:<host_id>``. This is the mode used
  by the production API so that multiple Uvicorn workers (and the
  separate ingestion worker) observe the same chat-activity signal.

Repo rules honoured:

* Rule #3 — PyMongo Async semantics only; no Motor APIs are introduced.
* Pydantic v2 idioms (no ``class Config``; the tracker itself is a
  plain dataclass-style service so no model is needed).
* Every Mongo call is best-effort: if the database is unreachable the
  tracker silently keeps an in-process timestamp so the chat request
  never fails and the collector still gets a usable signal in the
  current worker.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
from datetime import datetime, timezone
from typing import Any, Literal, Optional

__all__ = [
    "CHAT_ACTIVITY_COLLECTION_NAME",
    "ChatActivityTracker",
]


#: Canonical Mongo collection name for shared runtime signals.
CHAT_ACTIVITY_COLLECTION_NAME: str = "runtime_signals"

#: Default retention for the chat-activity document. Mongo's TTL monitor
#: runs roughly every 60s; 24h is plenty to cover restart-recovery while
#: keeping the collection self-cleaning.
_DEFAULT_TTL_SECONDS: int = 24 * 3600

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` when it is a coroutine; otherwise return it.

    PyMongo Async returns coroutines, but in-process fakes used by tests
    may return plain values. Mirrors the helper in
    :mod:`backend.services.resource_snapshot`.
    """
    if asyncio.iscoroutine(value):
        return await value
    return value


class ChatActivityTracker:
    """Tracks the wall-clock time of the most recent chat request.

    Args:
        db: Optional async MongoDB database handle
            (``pymongo.AsyncMongoClient`` database). Required when
            ``mode="mongo"``; ignored otherwise. May be ``None`` even in
            mongo mode for tests that wire a fake collection later.
        collection_name: Mongo collection name for the upsert. Defaults
            to :data:`CHAT_ACTIVITY_COLLECTION_NAME`.
        host_id: Stable host identifier. Defaults to
            ``socket.gethostname()`` so multi-worker processes on the
            same host share the same signal key.
        mode: ``"in_process"`` for single-process testing /
            single-worker deployments, ``"mongo"`` for multi-worker
            production deployments.
        ttl_seconds: TTL applied to the ``last_active_at`` field when
            :meth:`ensure_indexes` runs. Defaults to 24 hours.

    Example:
        >>> tracker = ChatActivityTracker(db=db, mode="mongo")
        >>> await tracker.ensure_indexes()
        >>> await tracker.mark_active()
        >>> seconds = await tracker.seconds_since_last_active()
    """

    def __init__(
        self,
        *,
        db: Any = None,
        collection_name: str = CHAT_ACTIVITY_COLLECTION_NAME,
        host_id: Optional[str] = None,
        mode: Literal["in_process", "mongo"] = "in_process",
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        if mode not in ("in_process", "mongo"):
            raise ValueError(
                f"mode must be 'in_process' or 'mongo', got {mode!r}"
            )
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._db = db
        self._collection_name = collection_name
        self._host_id = host_id or socket.gethostname()
        self._mode: Literal["in_process", "mongo"] = mode
        self._ttl_seconds = int(ttl_seconds)
        self._doc_id: str = f"chat_activity:{self._host_id}"
        # In-process slot. Used directly in ``"in_process"`` mode, and as
        # a best-effort fallback when ``"mongo"`` mode encounters a DB
        # outage (so the local worker still reports activity).
        self._last_active_at: Optional[float] = None
        self._indexes_ready: bool = False

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def host_id(self) -> str:
        """Stable host identifier this tracker stamps for."""
        return self._host_id

    @property
    def mode(self) -> str:
        """Active storage mode (``"in_process"`` or ``"mongo"``)."""
        return self._mode

    @property
    def doc_id(self) -> str:
        """Mongo ``_id`` used for the upsert document."""
        return self._doc_id

    @property
    def collection(self) -> Any:
        """Underlying async Mongo collection handle, or ``None``."""
        if self._db is None:
            return None
        return self._db[self._collection_name]

    # ── Public API ──────────────────────────────────────────────────────

    async def ensure_indexes(self) -> None:
        """Create the TTL index on ``last_active_at`` (mongo mode only).

        Idempotent and best-effort — index creation failures are logged
        but never raised, so the API lifespan can never be blocked by a
        transient Mongo error.
        """
        if self._mode != "mongo":
            return
        if self._indexes_ready:
            return
        col = self.collection
        if col is None:
            self._indexes_ready = True
            return
        try:
            await _maybe_await(
                col.create_index(
                    "last_active_at",
                    expireAfterSeconds=self._ttl_seconds,
                )
            )
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning(
                "create_index(last_active_at, TTL) on %s failed (non-fatal): %s",
                self._collection_name,
                exc,
            )
        self._indexes_ready = True

    async def mark_active(self) -> None:
        """Stamp the current time as the last chat-activity instant.

        Always updates the in-process slot so single-worker reads stay
        accurate. In ``"mongo"`` mode it additionally upserts the
        ``chat_activity:<host_id>`` document; Mongo failures degrade to
        the in-process value (logged at debug level) so the chat
        request itself is never broken.
        """
        # Always refresh the in-process slot first — guarantees the
        # signal works even if Mongo is unreachable.
        self._last_active_at = time.monotonic()

        if self._mode != "mongo":
            return
        col = self.collection
        if col is None:
            return
        try:
            await _maybe_await(
                col.update_one(
                    {"_id": self._doc_id},
                    {
                        "$set": {
                            "last_active_at": _utcnow(),
                            "host_id": self._host_id,
                        }
                    },
                    upsert=True,
                )
            )
        except Exception as exc:  # noqa: BLE001 - best-effort persistence
            logger.debug(
                "ChatActivityTracker mongo upsert failed (using in-process "
                "fallback): %s",
                exc,
            )

    async def seconds_since_last_active(self) -> Optional[float]:
        """Return wall-clock seconds since the last :meth:`mark_active`.

        Returns:
            * ``None`` when no activity has ever been recorded.
            * A non-negative ``float`` otherwise. In ``"mongo"`` mode the
              authoritative source is the ``last_active_at`` field on
              the upserted document; if that lookup fails (or returns
              nothing) the tracker falls back to the in-process slot so
              the current worker still contributes a usable signal.
        """
        if self._mode == "mongo":
            col = self.collection
            if col is not None:
                try:
                    doc = await _maybe_await(
                        col.find_one({"_id": self._doc_id})
                    )
                except Exception as exc:  # noqa: BLE001 - best-effort read
                    logger.debug(
                        "ChatActivityTracker mongo read failed (using "
                        "in-process fallback): %s",
                        exc,
                    )
                    doc = None
                if doc is not None:
                    last = doc.get("last_active_at")
                    if isinstance(last, datetime):
                        if last.tzinfo is None:
                            last = last.replace(tzinfo=timezone.utc)
                        delta = _utcnow() - last
                        return max(0.0, delta.total_seconds())
            # Fall through to the in-process fallback when the DB has
            # nothing for us yet (or failed).
        if self._last_active_at is None:
            return None
        return max(0.0, time.monotonic() - self._last_active_at)
