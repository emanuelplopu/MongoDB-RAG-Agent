"""Persistent store for :class:`AdaptiveSelector` decisions (F8 / Task 85).

Mirror of :mod:`backend.agent.strategy.run_trace_store` and
:mod:`backend.agent.strategy.bandit_state_store`: ABC + in-memory +
Mongo (PyMongo Async, repo rule #3) implementations writing to the
``adaptive_selection_decisions`` collection so operators can analyze
adaptive routing patterns post-hoc.

The selector continues to emit the existing
``adaptive_selection_decision`` operational-trace event — this store is
*additive*. A persistence failure must never break selection (the store
is best-effort; callers wrap :meth:`AdaptiveDecisionStore.record` in
``try/except`` and log on failure).

Document shape mirrors the per-candidate breakdown produced by
:class:`~backend.agent.strategy.spec_selector.AdaptiveSelector` in P9:

    {
        "decision_id": str (UUID4),
        "capability_id": str,
        "tenant_id": str | None,
        "privacy_mode": str | None,
        "agent_mode": str | None,
        "winner_strategy_id": str,
        "candidate_scores": [
            {
                "strategy_id": str,
                "latency_fit": float | None,
                "quality": float | None,
                "resource_fit": float | None,
                "residency": float | None,
                "fast_path_bias": float | None,
                "total": float,
                "disqualified": bool,
                "disqualified_reason": str | None,
            },
            ...
        ],
        "fast_path_eligible": bool,
        "chosen_via_cache": bool,
        "created_at": datetime,
    }

Forward compatibility: the model uses ``ConfigDict(extra="allow")`` so
new selector signals can be added later without a migration.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

__all__ = [
    "ADAPTIVE_DECISION_COLLECTION_NAME",
    "DEFAULT_ADAPTIVE_DECISION_TTL_DAYS",
    "AdaptiveDecisionDoc",
    "AdaptiveDecisionStore",
    "InMemoryAdaptiveDecisionStore",
    "MongoAdaptiveDecisionStore",
]


#: Canonical Mongo collection name for persisted adaptive selections.
ADAPTIVE_DECISION_COLLECTION_NAME: str = "adaptive_selection_decisions"

#: Default retention window applied to the TTL index on ``created_at``.
DEFAULT_ADAPTIVE_DECISION_TTL_DAYS: int = 30


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schema
# ─────────────────────────────────────────────────────────────────────────────


class AdaptiveDecisionDoc(BaseModel):
    """Single :class:`AdaptiveSelector` decision persisted into Mongo.

    Attributes:
        decision_id: Stable identifier for this decision (UUID4).
        capability_id: Capability the decision was made for.
        tenant_id: Tenant scope used for cache keying / tie-breaks.
        privacy_mode: ``standard`` / ``strict`` / ``local_only``.
        agent_mode: ``auto`` / ``fast`` / ``deep`` execution preference.
        winner_strategy_id: ``strategy_id`` chosen by the selector.
        candidate_scores: Flat list of per-candidate score breakdowns;
            one entry per candidate considered (including disqualified
            ones).
        fast_path_eligible: Whether :class:`FastPathRules` evaluated
            this request as fast-path eligible.
        chosen_via_cache: ``True`` when the decision was served from
            the adaptive cache rather than freshly computed.
        created_at: UTC creation timestamp (also the TTL anchor).
    """

    model_config = ConfigDict(extra="allow")

    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    capability_id: str
    tenant_id: Optional[str] = None
    privacy_mode: Optional[str] = None
    agent_mode: Optional[str] = None
    winner_strategy_id: str
    candidate_scores: list[dict[str, Any]] = Field(default_factory=list)
    fast_path_eligible: bool = False
    chosen_via_cache: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────────────────────────────────────


class AdaptiveDecisionStore(ABC):
    """Abstract base class for adaptive-decision persistence backends."""

    @abstractmethod
    async def record(self, doc: AdaptiveDecisionDoc) -> AdaptiveDecisionDoc:
        """Persist ``doc`` and return the stored model.

        Args:
            doc: Validated :class:`AdaptiveDecisionDoc` to write.

        Returns:
            The persisted :class:`AdaptiveDecisionDoc`.
        """

    @abstractmethod
    async def list_recent(
        self,
        *,
        capability_id: Optional[str] = None,
        winner_strategy_id: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AdaptiveDecisionDoc]:
        """Return recent decisions, ordered by ``created_at`` descending.

        Args:
            capability_id: Optional filter on ``capability_id``.
            winner_strategy_id: Optional filter on ``winner_strategy_id``.
            since: Inclusive lower bound on ``created_at``.
            limit: Max number of rows to return.

        Returns:
            Matching decisions, newest first.
        """

    async def ensure_indexes(self) -> None:
        """Create idempotent indexes used by the store. Default no-op."""
        return None


# ─────────────────────────────────────────────────────────────────────────────
# In-memory implementation
# ─────────────────────────────────────────────────────────────────────────────


class InMemoryAdaptiveDecisionStore(AdaptiveDecisionStore):
    """Process-local :class:`AdaptiveDecisionStore` used for tests."""

    def __init__(self) -> None:
        self._docs: list[AdaptiveDecisionDoc] = []
        self._lock = asyncio.Lock()

    async def record(self, doc: AdaptiveDecisionDoc) -> AdaptiveDecisionDoc:
        async with self._lock:
            stored = doc.model_copy(deep=True)
            self._docs.append(stored)
        return stored.model_copy(deep=True)

    async def list_recent(
        self,
        *,
        capability_id: Optional[str] = None,
        winner_strategy_id: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AdaptiveDecisionDoc]:
        async with self._lock:
            results = list(self._docs)
        if capability_id is not None:
            results = [d for d in results if d.capability_id == capability_id]
        if winner_strategy_id is not None:
            results = [
                d for d in results if d.winner_strategy_id == winner_strategy_id
            ]
        if since is not None:
            results = [d for d in results if d.created_at >= since]
        results.sort(key=lambda d: d.created_at, reverse=True)
        return [d.model_copy(deep=True) for d in results[: int(limit)]]


# ─────────────────────────────────────────────────────────────────────────────
# Mongo implementation
# ─────────────────────────────────────────────────────────────────────────────


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` if it is awaitable; otherwise return it unchanged.

    PyMongo Async returns coroutines for ``create_index`` while some
    test fakes return plain values; this helper bridges both shapes.
    """
    if asyncio.iscoroutine(value):
        return await value
    return value


class MongoAdaptiveDecisionStore(AdaptiveDecisionStore):
    """MongoDB-backed :class:`AdaptiveDecisionStore`.

    Documents live in :data:`ADAPTIVE_DECISION_COLLECTION_NAME`. Three
    indexes are created idempotently by :meth:`ensure_indexes`:

    * Non-unique ``(capability_id, created_at desc)`` — capability
      analytics ("which strategy wins for capability X?").
    * Non-unique ``(winner_strategy_id, created_at desc)`` — per-strategy
      timelines.
    * TTL on ``created_at`` (default 30 days) — automatic retention.

    Args:
        db: An async MongoDB database handle (``pymongo.AsyncMongoClient``
            database). PyMongo Async only — repo rule #3.
        collection_name: Override the collection name.
        ttl_days: Retention window applied to the TTL index. Must be
            >= 1; values are clamped to a non-negative ``expireAfterSeconds``.
    """

    def __init__(
        self,
        db: Any,
        *,
        collection_name: str = ADAPTIVE_DECISION_COLLECTION_NAME,
        ttl_days: int = DEFAULT_ADAPTIVE_DECISION_TTL_DAYS,
    ) -> None:
        if db is None:
            raise ValueError(
                "MongoAdaptiveDecisionStore requires a non-None database handle"
            )
        self._db = db
        self._collection_name = collection_name
        self._ttl_days = max(int(ttl_days), 1)
        self._index_lock = asyncio.Lock()
        self._indexes_ready = False

    @property
    def collection(self) -> Any:
        """Return the underlying async Mongo collection handle."""
        return self._db[self._collection_name]

    @property
    def ttl_seconds(self) -> int:
        """Return the configured TTL window in seconds."""
        return self._ttl_days * 24 * 3600

    async def ensure_indexes(self) -> None:
        """Create the (capability), (winner), and TTL indexes idempotently.

        Best-effort: errors are logged and swallowed so callers can
        survive in environments where the user lacks DDL privileges.
        """
        if self._indexes_ready:
            return
        async with self._index_lock:
            if self._indexes_ready:
                return
            collection = self.collection
            try:
                await _maybe_await(
                    collection.create_index(
                        [("capability_id", 1), ("created_at", -1)]
                    )
                )
            except Exception as exc:  # noqa: BLE001 - best effort
                logger.warning(
                    "create_index(capability_id, created_at) on %s failed (non-fatal): %s",
                    self._collection_name,
                    exc,
                )
            try:
                await _maybe_await(
                    collection.create_index(
                        [("winner_strategy_id", 1), ("created_at", -1)]
                    )
                )
            except Exception as exc:  # noqa: BLE001 - best effort
                logger.warning(
                    "create_index(winner_strategy_id, created_at) on %s failed (non-fatal): %s",
                    self._collection_name,
                    exc,
                )
            try:
                await _maybe_await(
                    collection.create_index(
                        "created_at",
                        expireAfterSeconds=self.ttl_seconds,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - best effort
                logger.warning(
                    "create_index(created_at TTL=%ss) on %s failed (non-fatal): %s",
                    self.ttl_seconds,
                    self._collection_name,
                    exc,
                )
            self._indexes_ready = True

    async def record(self, doc: AdaptiveDecisionDoc) -> AdaptiveDecisionDoc:
        payload = doc.model_dump(mode="python")
        await self.collection.insert_one(payload)
        return doc

    async def list_recent(
        self,
        *,
        capability_id: Optional[str] = None,
        winner_strategy_id: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AdaptiveDecisionDoc]:
        query: dict[str, Any] = {}
        if capability_id is not None:
            query["capability_id"] = capability_id
        if winner_strategy_id is not None:
            query["winner_strategy_id"] = winner_strategy_id
        if since is not None:
            query["created_at"] = {"$gte": since}

        results: list[AdaptiveDecisionDoc] = []
        try:
            cursor = self.collection.find(query).sort("created_at", -1)
            count = 0
            async for raw in cursor:
                raw.pop("_id", None)
                try:
                    results.append(AdaptiveDecisionDoc.model_validate(raw))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Skipping malformed adaptive decision doc in %s: %s",
                        self._collection_name,
                        exc,
                    )
                    continue
                count += 1
                if count >= int(limit):
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "list_recent failed on %s (treating as empty): %s",
                self._collection_name,
                exc,
            )
            return []
        return results
