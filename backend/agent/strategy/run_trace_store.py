"""Strategy run-trace persistence layer (Phase 5 / Task 68).

Defines a :class:`RunTraceStore` abstract interface plus two concrete
implementations:

* :class:`InMemoryRunTraceStore` — process-local list-backed store, used
  for tests and as a graceful fallback when no Mongo handle is available.
* :class:`MongoRunTraceStore` — backed by an async MongoDB collection
  (default: ``strategy_runs``, the placeholder name expected by
  :mod:`backend.agent.strategy.report_generator`).

Document schema is captured by the :class:`RunTraceDoc` Pydantic v2 model
and is intentionally aligned with
:data:`backend.agent.strategy.report_generator.EXPECTED_RUN_DOC` so the
nightly :class:`NightlyReportGenerator` can consume traces without any
changes. See the ``EXPECTED_RUN_DOC`` reference in that module for the
canonical contract:

    {
        "run_id": str,
        "strategy_id": str,
        "capability_id": str | None,
        "tenant_id": str | None,
        "status": "success | failed | timed_out | cancelled",
        "started_at": datetime (UTC),
        "completed_at": datetime (UTC),
        "duration_ms": float,
        "halt_reason": str | None,
        "node_outputs": list[dict],
    }

This module additionally persists a unique ``trace_id`` (for the unique
secondary index), an optional ``strategy_version``, and a
``created_at`` audit timestamp. Those fields are additive — they do not
break Felix's contract.

Repo rule #3: this module uses ``pymongo.AsyncMongoClient`` collection
semantics only — no Motor APIs are introduced.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

try:  # pragma: no cover - import-time guard, exercised in production runs
    from pymongo.errors import OperationFailure
except Exception:  # pragma: no cover - fall back when pymongo not available
    class OperationFailure(Exception):  # type: ignore[no-redef]
        """Fallback when ``pymongo`` is unavailable in the test env."""

logger = logging.getLogger(__name__)

__all__ = [
    "RUN_TRACE_COLLECTION_NAME",
    "RunTraceDoc",
    "RunTraceStore",
    "InMemoryRunTraceStore",
    "MongoRunTraceStore",
]


#: Canonical Mongo collection name for per-run trace documents. Mirrors
#: :data:`backend.agent.strategy.report_generator.STRATEGY_RUN_COLLECTION_NAME`
#: so the nightly aggregator reads what the runner writes.
RUN_TRACE_COLLECTION_NAME: str = "strategy_runs"


#: Allowed status values. Aligned with
#: :data:`backend.agent.strategy.report_generator.EXPECTED_RUN_DOC` —
#: ``success`` is counted as a success by the aggregator while
#: ``failed`` / ``timed_out`` are counted as failures. ``cancelled`` is
#: tracked but excluded from the success/failure ratios.
RunTraceStatus = Literal["success", "failed", "timed_out", "cancelled"]


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic schema
# ═══════════════════════════════════════════════════════════════════════════════


class RunTraceNodeOutput(BaseModel):
    """Per-node summary embedded in :class:`RunTraceDoc`.

    Fields are kept narrow on purpose: only what the nightly aggregator
    needs to rank the top-failing nodes plus enough context to
    diagnose the run after the fact.
    """

    model_config = ConfigDict(extra="allow")

    node_id: str = Field(description="Stable node identifier from the StrategySpec.")
    node_type: str = Field(description="Node type token (e.g. ``retrieve``).")
    status: str = Field(
        description=(
            "Node-level status. Mirrors ``NodeOutput.status``: "
            "``success | skipped | empty | error | timed_out``."
        ),
    )
    duration_ms: float = Field(default=0.0, ge=0.0)
    error: Optional[str] = Field(
        default=None,
        description="Error message when ``status`` is ``error`` or ``timed_out``.",
    )


class RunTraceDoc(BaseModel):
    """Per-strategy-run trace document persisted into ``strategy_runs``.

    Field set deliberately matches
    :data:`backend.agent.strategy.report_generator.EXPECTED_RUN_DOC`.
    Extra fields (``trace_id``, ``strategy_version``, ``created_at``)
    are additive; the nightly aggregator ignores them.
    """

    model_config = ConfigDict(extra="allow")

    trace_id: str = Field(description="Unique trace identifier (per run).")
    run_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional run identifier. Distinct from ``trace_id`` so a "
            "resumed run can keep the original ``run_id`` while emitting "
            "a fresh ``trace_id`` per attempt."
        ),
    )
    strategy_id: str = Field(description="Strategy spec identifier.")
    strategy_version: Optional[str] = Field(
        default=None, description="Semantic strategy version, when available."
    )
    capability_id: Optional[str] = Field(default=None)
    tenant_id: Optional[str] = Field(default=None)
    status: RunTraceStatus = Field(description="Final run status.")
    started_at: datetime = Field(description="Run start (UTC).")
    completed_at: datetime = Field(description="Run completion (UTC).")
    duration_ms: float = Field(default=0.0, ge=0.0)
    node_outputs: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Flat per-node summary list. Each entry should contain at "
            "least ``node_id``, ``node_type``, ``status``, ``duration_ms``, "
            "and optionally ``error``."
        ),
    )
    halt_reason: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# Abstract interface
# ═══════════════════════════════════════════════════════════════════════════════


class RunTraceStore(ABC):
    """Abstract base for run-trace persistence backends."""

    @abstractmethod
    async def save(self, trace_doc: RunTraceDoc | dict[str, Any]) -> RunTraceDoc:
        """Persist ``trace_doc`` and return the validated model.

        Args:
            trace_doc: Either a :class:`RunTraceDoc` or a dict that
                validates against it.

        Returns:
            The persisted :class:`RunTraceDoc` (validated).
        """

    @abstractmethod
    async def get(self, trace_id: str) -> Optional[RunTraceDoc]:
        """Return the trace identified by ``trace_id`` or ``None``."""

    @abstractmethod
    async def list_recent(
        self,
        *,
        since: datetime,
        until: datetime,
        strategy_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 1000,
    ) -> list[RunTraceDoc]:
        """List traces whose ``started_at`` falls in ``[since, until]``.

        Args:
            since: Inclusive lower bound on ``started_at``.
            until: Inclusive upper bound on ``started_at``.
            strategy_id: Optional ``strategy_id`` filter.
            status: Optional ``status`` filter.
            limit: Hard cap on returned rows (most recent first).

        Returns:
            A list of :class:`RunTraceDoc` ordered by ``started_at`` desc.
        """

    @abstractmethod
    async def ensure_indexes(self) -> None:
        """Create the indexes required for efficient lookups.

        Must be idempotent so that repeated calls during app lifespan
        startup do not fail.
        """


# ═══════════════════════════════════════════════════════════════════════════════
# In-memory implementation
# ═══════════════════════════════════════════════════════════════════════════════


class InMemoryRunTraceStore(RunTraceStore):
    """Process-local :class:`RunTraceStore` used for tests and fallback.

    Stores trace docs in a list. ``trace_id`` uniqueness is enforced on
    :meth:`save`. Not safe for cross-process use.
    """

    def __init__(self) -> None:
        self._docs: list[RunTraceDoc] = []
        self._lock = asyncio.Lock()

    async def save(self, trace_doc: RunTraceDoc | dict[str, Any]) -> RunTraceDoc:
        doc = (
            trace_doc
            if isinstance(trace_doc, RunTraceDoc)
            else RunTraceDoc.model_validate(trace_doc)
        )
        async with self._lock:
            # Replace existing entry on trace_id collision so the contract
            # mirrors Mongo's unique-index upsert behaviour without raising.
            for i, existing in enumerate(self._docs):
                if existing.trace_id == doc.trace_id:
                    self._docs[i] = doc
                    return doc
            self._docs.append(doc)
        return doc

    async def get(self, trace_id: str) -> Optional[RunTraceDoc]:
        for doc in self._docs:
            if doc.trace_id == trace_id:
                return doc
        return None

    async def list_recent(
        self,
        *,
        since: datetime,
        until: datetime,
        strategy_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 1000,
    ) -> list[RunTraceDoc]:
        results = [
            d
            for d in self._docs
            if since <= d.started_at <= until
            and (strategy_id is None or d.strategy_id == strategy_id)
            and (status is None or d.status == status)
        ]
        results.sort(key=lambda d: d.started_at, reverse=True)
        return results[: int(limit)]

    async def ensure_indexes(self) -> None:
        # No-op for in-memory backend; uniqueness is enforced in ``save``.
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Mongo implementation
# ═══════════════════════════════════════════════════════════════════════════════


class MongoRunTraceStore(RunTraceStore):
    """MongoDB-backed :class:`RunTraceStore`.

    Persists documents into a collection (default name
    :data:`RUN_TRACE_COLLECTION_NAME` = ``strategy_runs``) shaped exactly
    like Felix's :data:`EXPECTED_RUN_DOC` placeholder so the nightly
    report generator can read traces without any extra translation.

    Indexes (created idempotently by :meth:`ensure_indexes`):

    * ``(strategy_id, started_at)`` non-unique — supports the
      per-strategy time-window queries the aggregator and admin views
      need.
    * ``(trace_id)`` unique — guarantees ``save`` is idempotent on
      retry by the runner.
    * ``created_at`` TTL — bounds collection growth by expiring trace
      docs older than ``ttl_seconds``. Configurable via
      ``Settings.strategy_runs_ttl_days`` (see
      :mod:`backend.core.config`).

    Args:
        db: An async MongoDB database handle (``pymongo.AsyncMongoClient``
            database). PyMongo Async only — no Motor APIs are used.
        collection_name: Override the collection name. Defaults to
            :data:`RUN_TRACE_COLLECTION_NAME`.
        ttl_seconds: Retention period (seconds) for the TTL index on
            ``created_at``. Defaults to ``7 * 24 * 3600`` (7 days).
    """

    #: Stable name for the TTL index so collisions are detectable.
    _TTL_INDEX_NAME: str = "strategy_runs_ttl"

    def __init__(
        self,
        db: Any,
        *,
        collection_name: str = RUN_TRACE_COLLECTION_NAME,
        ttl_seconds: int = 7 * 24 * 3600,
    ) -> None:
        if db is None:
            raise ValueError("MongoRunTraceStore requires a non-None database handle")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a positive integer")
        self._db = db
        self._collection_name = collection_name
        self._ttl_seconds: int = int(ttl_seconds)
        self._indexes_ready = False

    @property
    def collection(self) -> Any:
        """Return the underlying async Mongo collection handle."""
        return self._db[self._collection_name]

    async def ensure_indexes(self) -> None:
        """Create required indexes idempotently.

        Errors are logged but never raised — index creation must not
        prevent the lifespan from completing.
        """
        if self._indexes_ready:
            return
        try:
            await _maybe_await(
                self.collection.create_index(
                    [("strategy_id", 1), ("started_at", -1)]
                )
            )
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning(
                "create_index(strategy_id, started_at) on %s failed (non-fatal): %s",
                self._collection_name,
                exc,
            )
        try:
            await _maybe_await(
                self.collection.create_index("trace_id", unique=True)
            )
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning(
                "create_index(trace_id, unique=True) on %s failed (non-fatal): %s",
                self._collection_name,
                exc,
            )
        await self._ensure_ttl_index()
        self._indexes_ready = True

    async def _ensure_ttl_index(self) -> None:
        """Create or reconcile the TTL index on ``created_at``.

        MongoDB raises :class:`pymongo.errors.OperationFailure` (code 85,
        ``IndexOptionsConflict``) when an index with the same name already
        exists with a different ``expireAfterSeconds``. When that happens
        we drop the stale index and recreate it with the configured
        retention period so the new value takes effect without crashing
        the lifespan.
        """
        try:
            await _maybe_await(
                self.collection.create_index(
                    "created_at",
                    expireAfterSeconds=self._ttl_seconds,
                    name=self._TTL_INDEX_NAME,
                )
            )
            return
        except OperationFailure as exc:
            logger.warning(
                "TTL index on %s.created_at conflicts with existing definition "
                "(retention period changed?); dropping and recreating: %s",
                self._collection_name,
                exc,
            )
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning(
                "create_index(created_at, ttl=%ss) on %s failed (non-fatal): %s",
                self._ttl_seconds,
                self._collection_name,
                exc,
            )
            return
        # Collision recovery path: drop the stale TTL index and recreate.
        try:
            drop = getattr(self.collection, "drop_index", None)
            if drop is not None:
                await _maybe_await(drop(self._TTL_INDEX_NAME))
            await _maybe_await(
                self.collection.create_index(
                    "created_at",
                    expireAfterSeconds=self._ttl_seconds,
                    name=self._TTL_INDEX_NAME,
                )
            )
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning(
                "Failed to recreate TTL index on %s after collision (non-fatal): %s",
                self._collection_name,
                exc,
            )

    async def save(self, trace_doc: RunTraceDoc | dict[str, Any]) -> RunTraceDoc:
        doc = (
            trace_doc
            if isinstance(trace_doc, RunTraceDoc)
            else RunTraceDoc.model_validate(trace_doc)
        )
        payload = doc.model_dump(mode="python")
        try:
            await self.collection.replace_one(
                {"trace_id": doc.trace_id},
                payload,
                upsert=True,
            )
        except Exception as exc:  # noqa: BLE001 - propagate as RuntimeError
            # Tests assert that callers (the runner) swallow exceptions
            # from save(); we do raise here so the in-memory contract and
            # the Mongo contract stay symmetric and the caller decides.
            logger.warning(
                "Failed to persist run trace %s into %s: %s",
                doc.trace_id,
                self._collection_name,
                exc,
            )
            raise
        return doc

    async def get(self, trace_id: str) -> Optional[RunTraceDoc]:
        try:
            raw = await self.collection.find_one({"trace_id": trace_id})
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "find_one(trace_id=%s) on %s failed: %s",
                trace_id,
                self._collection_name,
                exc,
            )
            return None
        if raw is None:
            return None
        raw.pop("_id", None)
        return RunTraceDoc.model_validate(raw)

    async def list_recent(
        self,
        *,
        since: datetime,
        until: datetime,
        strategy_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 1000,
    ) -> list[RunTraceDoc]:
        match: dict[str, Any] = {
            "started_at": {"$gte": since, "$lte": until},
        }
        if strategy_id is not None:
            match["strategy_id"] = strategy_id
        if status is not None:
            match["status"] = status

        results: list[RunTraceDoc] = []
        try:
            cursor = self.collection.find(match).sort("started_at", -1)
            count = 0
            async for raw in cursor:
                raw.pop("_id", None)
                try:
                    results.append(RunTraceDoc.model_validate(raw))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Skipping malformed trace doc in %s: %s",
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


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` if it is a coroutine, otherwise return it.

    PyMongo Async returns coroutines, but some test fakes may return a
    string directly. Keeping this branch makes the index-creation path
    work uniformly across both.
    """
    if asyncio.iscoroutine(value):
        return await value
    return value
