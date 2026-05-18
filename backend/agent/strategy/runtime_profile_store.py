"""Runtime model-profile persistence layer (Phase 6 / Task 70).

Persists empirical LLM benchmark measurements emitted by
:mod:`backend.services.runtime_profiler` into the
``runtime_model_profiles`` collection. The schema mirrors Blueprint 08
§6 verbatim and adds two additive fields (``test_name``,
``model_load_ms``) so the standardized prompt suite can be filtered
by test name and the orchestrator can later attribute a cold-start
penalty to a specific run.

Three concrete stores are provided:

* :class:`InMemoryRuntimeProfileStore` — process-local list-backed
  store, used in tests and as a graceful fallback when no Mongo
  handle is available.
* :class:`MongoRuntimeProfileStore` — backed by an async MongoDB
  collection (default: :data:`RUNTIME_PROFILE_COLLECTION_NAME`,
  i.e. ``runtime_model_profiles``).

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

logger = logging.getLogger(__name__)

__all__ = [
    "RUNTIME_PROFILE_COLLECTION_NAME",
    "STANDARD_TEST_NAMES",
    "RuntimeTestName",
    "RuntimeModelProfile",
    "RuntimeProfileStore",
    "InMemoryRuntimeProfileStore",
    "MongoRuntimeProfileStore",
]


#: Canonical Mongo collection name for runtime model profiles.
RUNTIME_PROFILE_COLLECTION_NAME: str = "runtime_model_profiles"


#: Canonical names for the standardized prompt suite (Blueprint 08 §5).
STANDARD_TEST_NAMES: tuple[str, ...] = (
    "tiny_classification",
    "short_rag",
    "evidence_synthesis",
    "long_context",
    "very_long_context",
    "parallel_workers",
)


#: Pydantic literal alias for the standardized test names.
RuntimeTestName = Literal[
    "tiny_classification",
    "short_rag",
    "evidence_synthesis",
    "long_context",
    "very_long_context",
    "parallel_workers",
]


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic schema (Blueprint 08 §6)
# ═══════════════════════════════════════════════════════════════════════════════


class RuntimeModelProfile(BaseModel):
    """Single empirical measurement of a local LLM under a standardized prompt.

    Field set is aligned with Blueprint 08 §6. Two additive fields
    (``test_name``, ``model_load_ms``) are appended; consumers that
    only know the §6 keys ignore them safely thanks to
    ``extra='allow'``.
    """

    model_config = ConfigDict(extra="allow")

    # ── Identity ────────────────────────────────────────────────────
    id: str = Field(description="Unique profile identifier (one per measurement).")
    host_id: str = Field(description="Host identifier the measurement was taken on.")
    tenant: str = Field(description="Tenant scope (e.g. ``recallhub``, ``default``).")
    model: str = Field(description="Model identifier as registered with the provider.")
    provider: str = Field(description="Provider identifier (``ollama``, ``openai``, ...).")
    quantization: Optional[str] = Field(
        default=None,
        description="Quantization tag if known (e.g. ``Q4_K_M``); ``None`` otherwise.",
    )

    # ── Standardized prompt context ─────────────────────────────────
    test_name: RuntimeTestName = Field(
        description=(
            "Standardized test name. One of: tiny_classification, short_rag, "
            "evidence_synthesis, long_context, very_long_context, parallel_workers."
        ),
    )
    context_tokens: int = Field(ge=0, description="Prompt context length in tokens.")
    output_tokens: int = Field(ge=0, description="Generated output length in tokens.")

    # ── Latency metrics ─────────────────────────────────────────────
    model_load_ms: Optional[int] = Field(
        default=None,
        ge=0,
        description=(
            "Cold-start cost (model load → readiness). ``None`` when the "
            "provider does not expose model-load events."
        ),
    )
    first_token_ms: Optional[int] = Field(
        default=None,
        ge=0,
        description=(
            "Time-to-first-token in ms. ``None`` when the provider does "
            "not stream."
        ),
    )
    total_latency_ms: int = Field(ge=0, description="End-to-end wall-clock latency in ms.")

    # ── Throughput metrics ──────────────────────────────────────────
    tokens_per_second: float = Field(
        ge=0.0,
        description=(
            "Aggregate output throughput (output_tokens / total_latency_ms). "
            "For ``parallel_workers`` records this is the per-worker "
            "throughput (aggregate divided by N)."
        ),
    )
    prompt_tokens_per_second: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Provider-reported prompt-eval throughput, if available.",
    )
    generation_tokens_per_second: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Provider-reported generation throughput, if available.",
    )

    # ── Resource utilisation ────────────────────────────────────────
    ram_peak_gb: Optional[float] = Field(default=None, ge=0.0)
    vram_peak_gb: Optional[float] = Field(default=None, ge=0.0)
    cpu_avg_pct: Optional[float] = Field(default=None, ge=0.0)
    gpu_avg_pct: Optional[float] = Field(default=None, ge=0.0)

    # ── Cost (F4 / Task 82) ────────────────────────────────────────
    cost_eur: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "Cost in EUR for this run; populated from provider billing "
            "metadata when available (LiteLLM/OpenAI-compat), or estimated "
            "from ``ModelRoleConfig`` cost rates. ``None`` for local "
            "providers (Ollama) when no role rates are configured."
        ),
    )
    cost_source: Optional[Literal["provider", "estimated", "unknown"]] = Field(
        default=None,
        description=(
            "Origin of ``cost_eur``. ``provider`` when sourced from "
            "explicit billing metadata; ``estimated`` when computed from "
            "``ModelRoleConfig.cost_per_million_*`` rates; ``unknown`` "
            "when no rate information was available."
        ),
    )

    # ── Outcome ─────────────────────────────────────────────────────
    success: bool = Field(description="Whether the run produced a usable result.")
    error: Optional[str] = Field(
        default=None,
        description="Error message when ``success`` is False; ``None`` otherwise.",
    )

    created_at: datetime = Field(default_factory=_utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# Abstract interface
# ═══════════════════════════════════════════════════════════════════════════════


class RuntimeProfileStore(ABC):
    """Abstract base for runtime-profile persistence backends."""

    @abstractmethod
    async def save(
        self, profile: RuntimeModelProfile | dict[str, Any]
    ) -> RuntimeModelProfile:
        """Persist ``profile`` (idempotent on ``id``) and return the validated model."""

    @abstractmethod
    async def get(self, profile_id: str) -> Optional[RuntimeModelProfile]:
        """Return the profile identified by ``profile_id`` or ``None``."""

    @abstractmethod
    async def list_recent(
        self,
        *,
        model: str,
        since: datetime,
        limit: int = 100,
    ) -> list[RuntimeModelProfile]:
        """List recent profiles for ``model`` newer than ``since``.

        Args:
            model: Model identifier filter.
            since: Inclusive lower bound on ``created_at``.
            limit: Hard cap on returned rows (most recent first).

        Returns:
            A list of :class:`RuntimeModelProfile` ordered by
            ``created_at`` desc.
        """

    @abstractmethod
    async def list_by_test_name(
        self,
        test_name: str,
        *,
        limit: int = 100,
    ) -> list[RuntimeModelProfile]:
        """List recent profiles for a specific ``test_name``."""

    @abstractmethod
    async def ensure_indexes(self) -> None:
        """Create the indexes required for efficient lookups (idempotent)."""


# ═══════════════════════════════════════════════════════════════════════════════
# In-memory implementation
# ═══════════════════════════════════════════════════════════════════════════════


class InMemoryRuntimeProfileStore(RuntimeProfileStore):
    """Process-local :class:`RuntimeProfileStore` used for tests and fallback.

    Stores profile docs in a list; ``id`` uniqueness is enforced on
    :meth:`save` (replace-on-collision, mirroring Mongo upsert).
    """

    def __init__(self) -> None:
        self._docs: list[RuntimeModelProfile] = []
        self._lock = asyncio.Lock()

    async def save(
        self, profile: RuntimeModelProfile | dict[str, Any]
    ) -> RuntimeModelProfile:
        doc = (
            profile
            if isinstance(profile, RuntimeModelProfile)
            else RuntimeModelProfile.model_validate(profile)
        )
        async with self._lock:
            for i, existing in enumerate(self._docs):
                if existing.id == doc.id:
                    self._docs[i] = doc
                    return doc
            self._docs.append(doc)
        return doc

    async def get(self, profile_id: str) -> Optional[RuntimeModelProfile]:
        for doc in self._docs:
            if doc.id == profile_id:
                return doc
        return None

    async def list_recent(
        self,
        *,
        model: str,
        since: datetime,
        limit: int = 100,
    ) -> list[RuntimeModelProfile]:
        results = [
            d for d in self._docs if d.model == model and d.created_at >= since
        ]
        results.sort(key=lambda d: d.created_at, reverse=True)
        return results[: int(limit)]

    async def list_by_test_name(
        self,
        test_name: str,
        *,
        limit: int = 100,
    ) -> list[RuntimeModelProfile]:
        results = [d for d in self._docs if d.test_name == test_name]
        results.sort(key=lambda d: d.created_at, reverse=True)
        return results[: int(limit)]

    async def ensure_indexes(self) -> None:
        # No-op for in-memory backend; uniqueness is enforced in ``save``.
        return None

    # ── Test introspection helper ───────────────────────────────────
    @property
    def all_docs(self) -> list[RuntimeModelProfile]:
        """Return a shallow copy of stored docs (test-only convenience)."""
        return list(self._docs)


# ═══════════════════════════════════════════════════════════════════════════════
# Mongo implementation
# ═══════════════════════════════════════════════════════════════════════════════


class MongoRuntimeProfileStore(RuntimeProfileStore):
    """MongoDB-backed :class:`RuntimeProfileStore` (PyMongo Async only).

    Persists documents into ``runtime_model_profiles`` (or override).

    Indexes (created idempotently by :meth:`ensure_indexes`):

    * ``id`` unique — guarantees ``save`` is idempotent on retry.
    * ``(model, created_at)`` non-unique — supports ``list_recent``
      lookups by model.
    * ``(test_name, created_at)`` non-unique — supports
      ``list_by_test_name`` lookups.

    Args:
        db: An async MongoDB database handle (``pymongo.AsyncMongoClient``
            database). PyMongo Async only — no Motor APIs are used.
        collection_name: Override the collection name. Defaults to
            :data:`RUNTIME_PROFILE_COLLECTION_NAME`.
    """

    def __init__(
        self,
        db: Any,
        *,
        collection_name: str = RUNTIME_PROFILE_COLLECTION_NAME,
    ) -> None:
        if db is None:
            raise ValueError(
                "MongoRuntimeProfileStore requires a non-None database handle"
            )
        self._db = db
        self._collection_name = collection_name
        self._indexes_ready = False

    @property
    def collection(self) -> Any:
        """Return the underlying async Mongo collection handle."""
        return self._db[self._collection_name]

    async def ensure_indexes(self) -> None:
        """Create required indexes idempotently.

        Errors are logged but never raised — index creation must not
        prevent the worker from running.
        """
        if self._indexes_ready:
            return
        try:
            await _maybe_await(
                self.collection.create_index("id", unique=True)
            )
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning(
                "create_index(id, unique=True) on %s failed (non-fatal): %s",
                self._collection_name,
                exc,
            )
        try:
            await _maybe_await(
                self.collection.create_index([("model", 1), ("created_at", -1)])
            )
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning(
                "create_index(model, created_at) on %s failed (non-fatal): %s",
                self._collection_name,
                exc,
            )
        try:
            await _maybe_await(
                self.collection.create_index(
                    [("test_name", 1), ("created_at", -1)]
                )
            )
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning(
                "create_index(test_name, created_at) on %s failed (non-fatal): %s",
                self._collection_name,
                exc,
            )
        self._indexes_ready = True

    async def save(
        self, profile: RuntimeModelProfile | dict[str, Any]
    ) -> RuntimeModelProfile:
        doc = (
            profile
            if isinstance(profile, RuntimeModelProfile)
            else RuntimeModelProfile.model_validate(profile)
        )
        payload = doc.model_dump(mode="python")
        try:
            await self.collection.replace_one(
                {"id": doc.id},
                payload,
                upsert=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to persist runtime profile %s into %s: %s",
                doc.id,
                self._collection_name,
                exc,
            )
            raise
        return doc

    async def get(self, profile_id: str) -> Optional[RuntimeModelProfile]:
        try:
            raw = await self.collection.find_one({"id": profile_id})
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "find_one(id=%s) on %s failed: %s",
                profile_id,
                self._collection_name,
                exc,
            )
            return None
        if raw is None:
            return None
        raw.pop("_id", None)
        return RuntimeModelProfile.model_validate(raw)

    async def list_recent(
        self,
        *,
        model: str,
        since: datetime,
        limit: int = 100,
    ) -> list[RuntimeModelProfile]:
        match: dict[str, Any] = {
            "model": model,
            "created_at": {"$gte": since},
        }
        return await self._cursor_to_list(match, limit)

    async def list_by_test_name(
        self,
        test_name: str,
        *,
        limit: int = 100,
    ) -> list[RuntimeModelProfile]:
        match: dict[str, Any] = {"test_name": test_name}
        return await self._cursor_to_list(match, limit)

    async def _cursor_to_list(
        self, match: dict[str, Any], limit: int
    ) -> list[RuntimeModelProfile]:
        results: list[RuntimeModelProfile] = []
        try:
            cursor = self.collection.find(match).sort("created_at", -1)
            count = 0
            async for raw in cursor:
                raw.pop("_id", None)
                try:
                    results.append(RuntimeModelProfile.model_validate(raw))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Skipping malformed runtime profile in %s: %s",
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
