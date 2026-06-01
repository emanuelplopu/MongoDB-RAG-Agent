"""Strategy LLM-call telemetry persistence layer (Task 89 / T1).

Captures every LLM invocation made by strategy node executors with FULL
prompt and response text plus timing/cost metrics. The schema is the
data foundation for the admin-panel and telemetry-viewer surfaces that
expose end-to-end strategy traces, prompts, and model output.

Three concrete artifacts are exported:

* :class:`LLMCallDoc` — Pydantic v2 schema for one LLM invocation.
* :class:`InMemoryLLMCallStore` — process-local store for tests / fallback.
* :class:`MongoLLMCallStore` — async MongoDB-backed store using the
  ``strategy_llm_calls`` collection.

Repo rule #3: this module uses ``pymongo.AsyncMongoClient`` collection
semantics only — no Motor APIs are introduced.

The store is a *pure data layer*. It deliberately does **not** import or
depend on :mod:`backend.services.llm_helper`; T2 wires invocation
capture into the node executors.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

try:  # pragma: no cover - import-time guard, exercised in production runs
    from pymongo.errors import OperationFailure
except Exception:  # pragma: no cover - fallback when pymongo unavailable
    class OperationFailure(Exception):  # type: ignore[no-redef]
        """Fallback when ``pymongo`` is unavailable in the test env."""

logger = logging.getLogger(__name__)

__all__ = [
    "LLM_CALL_COLLECTION_NAME",
    "LLMCallDoc",
    "LLMCallStore",
    "InMemoryLLMCallStore",
    "MongoLLMCallStore",
]


#: Canonical Mongo collection name for per-LLM-call telemetry documents.
LLM_CALL_COLLECTION_NAME: str = "strategy_llm_calls"

#: Default retention for the TTL index — 30 days. Production callers
#: should override via ``Settings.strategy_llm_calls_ttl_days``.
_DEFAULT_TTL_SECONDS: int = 30 * 24 * 3600


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic schema
# ═══════════════════════════════════════════════════════════════════════════════


class LLMCallDoc(BaseModel):
    """Full-fidelity record of one LLM invocation by a strategy node.

    Stores the complete system + user prompt and full assistant
    response so that the admin panel and telemetry viewer can render
    end-to-end traces without re-running the call. ``call_id`` is a
    UUID assigned automatically; multiple calls per ``(trace_id,
    node_id)`` pair are expected and supported.

    ``model_config`` enables ``extra="allow"`` for forward
    compatibility (so newer fields written by future executors are
    preserved on read) and disables Pydantic's protected
    ``model_*`` namespace warning so ``model`` and ``model_role`` can
    be plain field names.
    """

    model_config = ConfigDict(extra="allow", protected_namespaces=())

    # ── Identity ─────────────────────────────────────────────────────
    call_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this LLM call (UUID4).",
    )
    trace_id: str = Field(
        description=(
            "Strategy run trace identifier. Joins back to "
            "``strategy_runs.trace_id`` for full trace reconstruction."
        ),
    )
    node_id: str = Field(
        description="Stable DAG node identifier (e.g. ``n_synthesize``).",
    )
    node_type: str = Field(
        description="Node type token (e.g. ``synthesize``, ``retrieve``).",
    )

    # ── Model + provider ─────────────────────────────────────────────
    model: str = Field(description="Model identifier (e.g. ``gemma3:4b``).")
    provider: str = Field(description="Provider identifier (e.g. ``ollama``, ``litellm``).")
    model_role: Optional[str] = Field(
        default=None,
        description=(
            "Role from the model registry that selected this model "
            "(e.g. ``synthesizer_fast``). ``None`` when no role mapping was used."
        ),
    )

    # ── Prompts + response (FULL text, no truncation) ────────────────
    system_prompt: Optional[str] = Field(
        default=None,
        description="Full system-prompt text sent to the LLM, if any.",
    )
    user_prompt: str = Field(
        description="Full user-prompt text sent to the LLM (constructed prompt).",
    )
    assistant_response: str = Field(
        default="",
        description="Full assistant response text from the LLM.",
    )

    # ── Token accounting ─────────────────────────────────────────────
    prompt_tokens: Optional[int] = Field(default=None, ge=0)
    completion_tokens: Optional[int] = Field(default=None, ge=0)
    total_tokens: Optional[int] = Field(default=None, ge=0)

    # ── Timing + sampling parameters ─────────────────────────────────
    latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Wall-clock latency for the LLM call in milliseconds.",
    )
    temperature: Optional[float] = Field(default=None)
    max_tokens: Optional[int] = Field(default=None, ge=0)

    # ── Cost ─────────────────────────────────────────────────────────
    cost_eur: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "Cost in EUR for this call when available — typically "
            "populated from :class:`RuntimeProfiler` cost estimation."
        ),
    )

    # ── Outcome ──────────────────────────────────────────────────────
    success: bool = Field(
        default=True,
        description="Whether the LLM call returned a usable response.",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message when ``success`` is False; ``None`` otherwise.",
    )

    created_at: datetime = Field(
        default_factory=_utcnow,
        description="Server-side timestamp when this record was created (UTC).",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Abstract interface
# ═══════════════════════════════════════════════════════════════════════════════


class LLMCallStore(ABC):
    """Abstract base for LLM-call persistence backends.

    The contract is intentionally append-only: ``call_id`` is a UUID4
    so collisions are not modelled, and there is no ``update`` /
    ``delete`` surface. Writes are best-effort — concrete
    implementations log and swallow transient backend errors so that
    failures in telemetry never break the actual strategy run.
    """

    @abstractmethod
    async def save(self, doc: LLMCallDoc) -> None:
        """Persist ``doc``.

        Args:
            doc: A validated :class:`LLMCallDoc` instance.

        Notes:
            Best-effort: implementations log warnings on transient
            backend failures and return gracefully rather than raising.
            Validation errors raised by Pydantic at construction time
            do propagate to the caller.
        """

    @abstractmethod
    async def get(self, call_id: str) -> Optional[LLMCallDoc]:
        """Return the call identified by ``call_id`` or ``None``."""

    @abstractmethod
    async def list_by_trace(
        self,
        trace_id: str,
        *,
        limit: int = 100,
    ) -> list[LLMCallDoc]:
        """List all calls associated with ``trace_id``.

        Args:
            trace_id: Strategy run trace identifier.
            limit: Hard cap on returned rows (oldest first by
                ``created_at`` so the trace reads as a timeline).

        Returns:
            A list of :class:`LLMCallDoc` ordered by ``created_at`` asc.
        """

    @abstractmethod
    async def list_by_node(
        self,
        trace_id: str,
        node_id: str,
    ) -> list[LLMCallDoc]:
        """List calls for one ``(trace_id, node_id)`` pair, oldest first."""

    @abstractmethod
    async def ensure_indexes(self) -> None:
        """Create required indexes idempotently."""


# ═══════════════════════════════════════════════════════════════════════════════
# In-memory implementation
# ═══════════════════════════════════════════════════════════════════════════════


class InMemoryLLMCallStore(LLMCallStore):
    """Process-local :class:`LLMCallStore` for tests and fallback.

    Stores documents in a list. Append-only — duplicate ``call_id``
    values would only occur on UUID collision, which is treated as
    impossible. Not safe for cross-process use.
    """

    def __init__(self) -> None:
        self._docs: list[LLMCallDoc] = []
        self._lock = asyncio.Lock()

    async def save(self, doc: LLMCallDoc) -> None:
        if not isinstance(doc, LLMCallDoc):
            doc = LLMCallDoc.model_validate(doc)
        async with self._lock:
            self._docs.append(doc)

    async def get(self, call_id: str) -> Optional[LLMCallDoc]:
        for doc in self._docs:
            if doc.call_id == call_id:
                return doc
        return None

    async def list_by_trace(
        self,
        trace_id: str,
        *,
        limit: int = 100,
    ) -> list[LLMCallDoc]:
        results = [d for d in self._docs if d.trace_id == trace_id]
        results.sort(key=lambda d: d.created_at)
        return results[: int(limit)]

    async def list_by_node(
        self,
        trace_id: str,
        node_id: str,
    ) -> list[LLMCallDoc]:
        results = [
            d
            for d in self._docs
            if d.trace_id == trace_id and d.node_id == node_id
        ]
        results.sort(key=lambda d: d.created_at)
        return results

    async def ensure_indexes(self) -> None:
        # No-op for the in-memory backend.
        return None

    # ── Test introspection helper ───────────────────────────────────
    @property
    def all_docs(self) -> list[LLMCallDoc]:
        """Return a shallow copy of stored docs (test-only convenience)."""
        return list(self._docs)


# ═══════════════════════════════════════════════════════════════════════════════
# Mongo implementation
# ═══════════════════════════════════════════════════════════════════════════════


class MongoLLMCallStore(LLMCallStore):
    """MongoDB-backed :class:`LLMCallStore` (PyMongo Async only).

    Persists documents into ``strategy_llm_calls`` (or override). The
    write path uses ``insert_one`` because ``call_id`` is a UUID4 and
    no upsert semantics are needed.

    Indexes (created idempotently by :meth:`ensure_indexes`):

    * ``(trace_id, node_id)`` non-unique — supports per-run /
      per-node lookups used by the trace UI.
    * ``created_at`` TTL — bounds collection growth by expiring
      records older than ``ttl_seconds``. The TTL index doubles as
      the time-range index for ``created_at`` queries; Mongo refuses
      duplicate index specs on the same key, so we deliberately do
      not create a second non-TTL index on ``created_at``.

    On a TTL ``IndexOptionsConflict`` (existing index with a
    different ``expireAfterSeconds``) the collision-recovery path
    drops and recreates the index with the new retention period and
    logs a warning instead of crashing the lifespan startup.

    Args:
        db: Async MongoDB database handle (``pymongo.AsyncMongoClient``
            database). PyMongo Async only — no Motor APIs are used.
        collection_name: Override the collection name. Defaults to
            :data:`LLM_CALL_COLLECTION_NAME`.
        ttl_seconds: Retention period (seconds) for the TTL index on
            ``created_at``. Defaults to 30 days.
    """

    #: Stable name for the TTL index so collisions are detectable.
    _TTL_INDEX_NAME: str = "strategy_llm_calls_ttl"

    def __init__(
        self,
        db: Any,
        *,
        collection_name: str = LLM_CALL_COLLECTION_NAME,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        if db is None:
            raise ValueError(
                "MongoLLMCallStore requires a non-None database handle"
            )
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

    # ── Index management ────────────────────────────────────────────

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
                    [("trace_id", 1), ("node_id", 1)]
                )
            )
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning(
                "create_index(trace_id, node_id) on %s failed (non-fatal): %s",
                self._collection_name,
                exc,
            )
        await self._ensure_ttl_index()
        self._indexes_ready = True

    async def _ensure_ttl_index(self) -> None:
        """Create or reconcile the TTL index on ``created_at``.

        MongoDB raises :class:`pymongo.errors.OperationFailure` (code 85,
        ``IndexOptionsConflict``) when an index with the same name
        already exists with a different ``expireAfterSeconds``. When
        that happens we drop the stale index and recreate it with the
        configured retention period so the new value takes effect
        without crashing the lifespan.
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

    # ── Read / write surface ────────────────────────────────────────

    async def save(self, doc: LLMCallDoc) -> None:
        """Append-only insert; transient Mongo errors are logged + swallowed.

        The store is a telemetry sink — it must never break the
        actual strategy run. Pydantic validation errors raised at
        construction time still propagate; only post-construction
        backend failures are absorbed here.
        """
        if not isinstance(doc, LLMCallDoc):
            doc = LLMCallDoc.model_validate(doc)
        payload = doc.model_dump(mode="python")
        try:
            await self.collection.insert_one(payload)
        except Exception as exc:  # noqa: BLE001 - best effort sink
            logger.warning(
                "Failed to persist LLM call %s into %s (non-fatal): %s",
                doc.call_id,
                self._collection_name,
                exc,
            )

    async def get(self, call_id: str) -> Optional[LLMCallDoc]:
        try:
            raw = await self.collection.find_one({"call_id": call_id})
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "find_one(call_id=%s) on %s failed: %s",
                call_id,
                self._collection_name,
                exc,
            )
            return None
        if raw is None:
            return None
        raw.pop("_id", None)
        try:
            return LLMCallDoc.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Skipping malformed LLM call doc in %s: %s",
                self._collection_name,
                exc,
            )
            return None

    async def list_by_trace(
        self,
        trace_id: str,
        *,
        limit: int = 100,
    ) -> list[LLMCallDoc]:
        return await self._cursor_to_list(
            {"trace_id": trace_id}, limit=int(limit)
        )

    async def list_by_node(
        self,
        trace_id: str,
        node_id: str,
    ) -> list[LLMCallDoc]:
        # No explicit limit on per-node listing; node-scoped traces are
        # already small. Cap at 1000 to avoid pathological reads.
        return await self._cursor_to_list(
            {"trace_id": trace_id, "node_id": node_id}, limit=1000
        )

    async def _cursor_to_list(
        self, match: dict[str, Any], *, limit: int
    ) -> list[LLMCallDoc]:
        """Stream ``match`` results sorted by ``created_at`` ascending.

        Uses iterate-and-break for the ``limit`` cap so the test fakes
        in :mod:`backend.tests.strategy._fakes` (which expose ``sort``
        but not ``limit`` on the cursor) continue to work uniformly
        with real PyMongo Async cursors.
        """
        results: list[LLMCallDoc] = []
        try:
            cursor = self.collection.find(match).sort("created_at", 1)
            count = 0
            async for raw in cursor:
                raw.pop("_id", None)
                try:
                    results.append(LLMCallDoc.model_validate(raw))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Skipping malformed LLM call doc in %s: %s",
                        self._collection_name,
                        exc,
                    )
                    continue
                count += 1
                if count >= int(limit):
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "list query failed on %s (treating as empty): %s",
                self._collection_name,
                exc,
            )
            return []
        return results


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` if it is a coroutine, otherwise return it.

    PyMongo Async returns coroutines, but some test fakes return
    strings directly; this branch keeps the index-creation path
    uniform across both.
    """
    if asyncio.iscoroutine(value):
        return await value
    return value
