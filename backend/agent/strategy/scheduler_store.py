"""Strategy scheduler persistence layer.

Phase 5 / Task 61 deliverable. Mirrors the shape of
:mod:`backend.agent.strategy.spec_store` so that the scheduler daemon and
its HTTP router can be wired against either an in-memory backend
(used by tests and local-dev when ``app.state.db`` is unavailable) or a
MongoDB-backed implementation that survives process restarts.

Persisted shape: a ``strategy_schedules`` collection where each document
carries every field of :class:`backend.scheduler.models.StrategySchedule`
plus the runtime fields the daemon mutates between ticks
(``paused``, ``paused_reason``, ``last_run_at``, ``last_run_status``,
``next_run_at``, ``consecutive_failures``, ``circuit_breaker_state``).

Repo rule #3: this module uses ``pymongo.AsyncMongoClient`` collection
semantics only \u2014 no Motor APIs are introduced. The fake collection used
by the unit tests mirrors the small slice of the API actually invoked
here (``find_one``, ``find``, ``replace_one``, ``delete_one``,
``delete_many``, ``create_index``).
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

from backend.scheduler.models import StrategySchedule

logger = logging.getLogger(__name__)

__all__ = [
    "SCHEDULE_COLLECTION_NAME",
    "SchedulerStore",
    "InMemorySchedulerStore",
    "MongoSchedulerStore",
]


#: Canonical Mongo collection name for persisted schedules.
SCHEDULE_COLLECTION_NAME: str = "strategy_schedules"


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


def _is_due(schedule: StrategySchedule, now: datetime) -> bool:
    """Return ``True`` when ``schedule`` is unpaused and its ``next_run_at`` has elapsed."""
    if schedule.paused:
        return False
    if schedule.status != "active":
        return False
    if schedule.next_run_at is None:
        return False
    next_run = schedule.next_run_at
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return next_run <= now


class SchedulerStore(ABC):
    """Abstract base for scheduler persistence backends.

    All implementations expose async CRUD plus a partial
    ``update_runtime_state`` accessor for the daemon's tick-time mutations
    and a ``list_due`` query for the dispatch loop.
    """

    @abstractmethod
    async def get(self, schedule_id: str) -> Optional[StrategySchedule]:
        """Return the schedule identified by ``schedule_id`` (or ``None``)."""

    @abstractmethod
    async def list(
        self,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[StrategySchedule]:
        """Return every schedule matching ``filters`` (empty dict \u2192 all)."""

    @abstractmethod
    async def upsert(self, schedule: StrategySchedule) -> StrategySchedule:
        """Insert or replace ``schedule`` keyed by ``schedule.id``.

        Args:
            schedule: The schedule to persist. ``updated_at`` is refreshed
                automatically.

        Returns:
            The persisted schedule.
        """

    @abstractmethod
    async def delete(self, schedule_id: str) -> bool:
        """Remove the schedule keyed by ``schedule_id``.

        Returns:
            ``True`` when a schedule was removed, ``False`` otherwise.
        """

    @abstractmethod
    async def update_runtime_state(
        self,
        schedule_id: str,
        *,
        circuit_breaker: Optional[str] = None,
        paused: Optional[bool] = None,
        paused_reason: Optional[str] = None,
        last_run_at: Optional[datetime] = None,
        last_run_status: Optional[str] = None,
        next_run_at: Optional[datetime] = None,
        consecutive_failures: Optional[int] = None,
    ) -> Optional[StrategySchedule]:
        """Apply a partial runtime-field update to a schedule.

        Only fields explicitly passed (i.e. not ``None``) are written.
        Use sentinel ``""`` (empty string) on ``paused_reason`` to clear
        a previously-set reason without disturbing other fields.

        Returns:
            The updated schedule, or ``None`` when the id is unknown.
        """

    @abstractmethod
    async def list_due(self, now: datetime) -> list[StrategySchedule]:
        """Return active, unpaused schedules whose ``next_run_at`` \u2264 ``now``."""

    async def ensure_indexes(self) -> None:
        """Create idempotent indexes used by the store. Default no-op."""
        return None


# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
# In-memory implementation
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550


class InMemorySchedulerStore(SchedulerStore):
    """Process-local, dict-backed :class:`SchedulerStore` implementation.

    Used by tests and as a fallback when no MongoDB handle is available.
    All values are stored as :class:`StrategySchedule` clones to avoid
    accidental aliasing between caller-held references and the store.
    """

    def __init__(
        self,
        schedules: Optional[dict[str, StrategySchedule]] = None,
    ) -> None:
        self._schedules: dict[str, StrategySchedule] = dict(schedules or {})

    async def get(self, schedule_id: str) -> Optional[StrategySchedule]:
        existing = self._schedules.get(schedule_id)
        return existing.model_copy(deep=True) if existing is not None else None

    async def list(
        self,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[StrategySchedule]:
        result: list[StrategySchedule] = []
        for schedule in self._schedules.values():
            if filters and not _matches(schedule, filters):
                continue
            result.append(schedule.model_copy(deep=True))
        return result

    async def upsert(self, schedule: StrategySchedule) -> StrategySchedule:
        copy = schedule.model_copy(deep=True)
        copy.updated_at = datetime.utcnow()
        self._schedules[copy.id] = copy
        return copy.model_copy(deep=True)

    async def delete(self, schedule_id: str) -> bool:
        return self._schedules.pop(schedule_id, None) is not None

    async def update_runtime_state(
        self,
        schedule_id: str,
        *,
        circuit_breaker: Optional[str] = None,
        paused: Optional[bool] = None,
        paused_reason: Optional[str] = None,
        last_run_at: Optional[datetime] = None,
        last_run_status: Optional[str] = None,
        next_run_at: Optional[datetime] = None,
        consecutive_failures: Optional[int] = None,
    ) -> Optional[StrategySchedule]:
        existing = self._schedules.get(schedule_id)
        if existing is None:
            return None
        updates: dict[str, Any] = {}
        if circuit_breaker is not None:
            updates["circuit_breaker_state"] = circuit_breaker
        if paused is not None:
            updates["paused"] = paused
        if paused_reason is not None:
            # Empty string sentinel clears the field.
            updates["paused_reason"] = paused_reason or None
        if last_run_at is not None:
            updates["last_run_at"] = last_run_at
        if last_run_status is not None:
            updates["last_run_status"] = last_run_status
        if next_run_at is not None:
            updates["next_run_at"] = next_run_at
        if consecutive_failures is not None:
            updates["consecutive_failures"] = consecutive_failures
        if not updates:
            return existing.model_copy(deep=True)
        updated = existing.model_copy(update=updates)
        updated.updated_at = datetime.utcnow()
        self._schedules[schedule_id] = updated
        return updated.model_copy(deep=True)

    async def list_due(self, now: datetime) -> list[StrategySchedule]:
        return [
            s.model_copy(deep=True)
            for s in self._schedules.values()
            if _is_due(s, now)
        ]


def _matches(schedule: StrategySchedule, filters: dict[str, Any]) -> bool:
    """Return ``True`` when every ``key=value`` in ``filters`` matches ``schedule``."""
    data = schedule.model_dump()
    for key, expected in filters.items():
        if data.get(key) != expected:
            return False
    return True


# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
# Mongo implementation
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550


class MongoSchedulerStore(SchedulerStore):
    """MongoDB-backed :class:`SchedulerStore` implementation.

    Documents are stored in :data:`SCHEDULE_COLLECTION_NAME` keyed by the
    ``schedule_id`` field (mirroring :class:`StrategySchedule.id`). The
    store is compatible with both ``pymongo.AsyncMongoClient`` databases
    and the in-process fake collection used in tests; only a small,
    explicitly enumerated subset of the collection API is exercised.

    Args:
        db: An async MongoDB database handle.
        collection_name: Override the collection name. Defaults to
            :data:`SCHEDULE_COLLECTION_NAME`.
    """

    def __init__(
        self,
        db: Any,
        *,
        collection_name: str = SCHEDULE_COLLECTION_NAME,
    ) -> None:
        if db is None:
            raise ValueError("MongoSchedulerStore requires a non-None database handle")
        self._db = db
        self._collection_name = collection_name
        self._index_lock = asyncio.Lock()
        self._indexes_ready = False

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def collection(self) -> Any:
        """Return the underlying async Mongo collection handle."""
        return self._db[self._collection_name]

    # ── Index management ────────────────────────────────────────────────

    async def ensure_indexes(self) -> None:
        """Create the unique ``schedule_id`` and the dispatch lookup index.

        Idempotent: re-issuing ``create_index`` on the same key spec is a
        no-op according to the MongoDB driver. Best-effort; logs and
        swallows driver failures so callers can survive in environments
        where the user lacks DDL privileges.
        """
        if self._indexes_ready:
            return
        async with self._index_lock:
            if self._indexes_ready:
                return
            collection = self.collection
            try:
                await _maybe_await(
                    collection.create_index("schedule_id", unique=True)
                )
                await _maybe_await(
                    collection.create_index([("paused", 1), ("next_run_at", 1)])
                )
            except Exception as exc:  # noqa: BLE001 - best effort
                logger.warning(
                    "MongoSchedulerStore.ensure_indexes failed (non-fatal): %s",
                    exc,
                )
            self._indexes_ready = True

    # ── SchedulerStore methods ──────────────────────────────────────────

    async def get(self, schedule_id: str) -> Optional[StrategySchedule]:
        doc = await self.collection.find_one({"schedule_id": schedule_id})
        return _doc_to_schedule(doc)

    async def list(
        self,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[StrategySchedule]:
        query = dict(filters or {})
        cursor = self.collection.find(query)
        out: list[StrategySchedule] = []
        async for doc in cursor:
            schedule = _doc_to_schedule(doc)
            if schedule is not None:
                out.append(schedule)
        return out

    async def upsert(self, schedule: StrategySchedule) -> StrategySchedule:
        schedule.updated_at = datetime.utcnow()
        doc = _schedule_to_doc(schedule)
        await self.collection.replace_one(
            {"schedule_id": schedule.id}, doc, upsert=True
        )
        return schedule.model_copy(deep=True)

    async def delete(self, schedule_id: str) -> bool:
        result = await self.collection.delete_one({"schedule_id": schedule_id})
        return getattr(result, "deleted_count", 0) > 0

    async def update_runtime_state(
        self,
        schedule_id: str,
        *,
        circuit_breaker: Optional[str] = None,
        paused: Optional[bool] = None,
        paused_reason: Optional[str] = None,
        last_run_at: Optional[datetime] = None,
        last_run_status: Optional[str] = None,
        next_run_at: Optional[datetime] = None,
        consecutive_failures: Optional[int] = None,
    ) -> Optional[StrategySchedule]:
        existing = await self.get(schedule_id)
        if existing is None:
            return None
        updates: dict[str, Any] = {}
        if circuit_breaker is not None:
            updates["circuit_breaker_state"] = circuit_breaker
        if paused is not None:
            updates["paused"] = paused
        if paused_reason is not None:
            updates["paused_reason"] = paused_reason or None
        if last_run_at is not None:
            updates["last_run_at"] = last_run_at
        if last_run_status is not None:
            updates["last_run_status"] = last_run_status
        if next_run_at is not None:
            updates["next_run_at"] = next_run_at
        if consecutive_failures is not None:
            updates["consecutive_failures"] = consecutive_failures
        if not updates:
            return existing
        updated = existing.model_copy(update=updates)
        updated.updated_at = datetime.utcnow()
        await self.collection.replace_one(
            {"schedule_id": schedule_id},
            _schedule_to_doc(updated),
            upsert=True,
        )
        return updated

    async def list_due(self, now: datetime) -> list[StrategySchedule]:
        # Pre-filter at the Mongo layer; final due-check repeats in Python so
        # in-memory parity is exact across both stores.
        cursor = self.collection.find(
            {"paused": {"$ne": True}, "status": "active"}
        )
        out: list[StrategySchedule] = []
        async for doc in cursor:
            schedule = _doc_to_schedule(doc)
            if schedule is None:
                continue
            if _is_due(schedule, now):
                out.append(schedule)
        return out


# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
# Helpers
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` when it is awaitable; otherwise return it unchanged.

    PyMongo Async returns coroutines for ``create_index`` while some test
    fakes return plain values; this helper bridges both shapes.
    """
    if asyncio.iscoroutine(value):
        return await value
    return value


def _schedule_to_doc(schedule: StrategySchedule) -> dict[str, Any]:
    """Serialize a :class:`StrategySchedule` into a flat Mongo document.

    The document carries every model field plus a top-level
    ``schedule_id`` mirror of ``schedule.id`` for convenient indexing.
    """
    payload = schedule.model_dump(mode="python")
    payload["schedule_id"] = schedule.id
    return payload


def _doc_to_schedule(doc: Optional[dict[str, Any]]) -> Optional[StrategySchedule]:
    """Deserialize a Mongo document back into :class:`StrategySchedule`."""
    if doc is None:
        return None
    data = {k: v for k, v in doc.items() if k not in {"_id", "schedule_id"}}
    if "schedule_id" in doc and "id" not in data:
        data["id"] = doc["schedule_id"]
    try:
        return StrategySchedule(**data)
    except Exception as exc:  # noqa: BLE001 - tolerate legacy docs
        logger.warning("Failed to parse schedule document: %s", exc)
        return None
