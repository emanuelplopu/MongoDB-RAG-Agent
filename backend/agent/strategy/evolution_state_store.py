"""Persistent state store for the evolutionary exploration mode (Task 77 / P8).

The evolutionary mode in
:mod:`backend.agent.strategy.exploration_modes` produces successive
generations of candidate :class:`~backend.agent.strategy.models.StrategySpec`
instances by applying mutation operators to the top-K winners of the prior
generation. Each generation is described by an
:class:`EvolutionGenerationState` document keyed by
``(experiment_id, generation)`` so that:

* The runner can replay or resume a partially-completed experiment after a
  process restart.
* Subsequent experiment runs can read the prior fitness landscape and seed
  generation 0 with the highest-scoring children.

Robin's P7 explicitly requested that this state live in a **separate**
collection from ``strategy_bandit_state`` to keep the bandit's per-arm
allocation rows clean of evolutionary lineage data. We honour that here by
defaulting to ``strategy_evolution_state``.

Two backends ship:

* :class:`InMemoryEvolutionStateStore` — process-local fake used by unit
  tests and local-dev wiring without a Mongo handle.
* :class:`MongoEvolutionStateStore` — persistent backend wired against an
  async ``pymongo.AsyncMongoClient`` database (Repo rule #3 — no Motor).

The ABC :class:`EvolutionStateStore` describes the small async surface the
mode actually exercises::

    upsert_generation  ── Idempotently create/replace one generation row.
    get_latest         ── Fetch the highest-numbered generation.
    list_generations   ── Fetch every generation row for an experiment.
    ensure_indexes     ── Create the unique compound index. Best-effort.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

__all__ = [
    "EVOLUTION_STATE_COLLECTION_NAME",
    "EvolutionGenerationState",
    "EvolutionStateStore",
    "InMemoryEvolutionStateStore",
    "MongoEvolutionStateStore",
]


#: Canonical Mongo collection name for persisted evolutionary state. Robin's
#: P7 ask: this must be a *separate* collection from the bandit state so the
#: two modes do not stomp on each other.
EVOLUTION_STATE_COLLECTION_NAME: str = "strategy_evolution_state"


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic model
# ─────────────────────────────────────────────────────────────────────────────


class EvolutionGenerationState(BaseModel):
    """Per-generation state for an evolutionary experiment.

    One row per ``(experiment_id, generation)`` pair. ``parent_ids`` records
    the strategy ids that seeded the generation, ``child_ids`` records the
    strategy ids of the candidates that were materialized for the runner
    to evaluate. Fitness aggregates are populated by
    :meth:`backend.agent.strategy.exploration_modes.EvolutionaryMode.record_generation_results`
    once the runner has scored the children.

    Attributes:
        experiment_id: Owning experiment identifier (matches
            :attr:`StrategyExperimentJob.experiment_id`).
        generation: Generation index — ``0`` for the seed pool, ``1..N``
            for evolved generations.
        population_size: Number of children materialized for this
            generation. ``len(child_ids)`` always matches.
        parent_ids: Strategy ids that were used as parents to produce
            this generation. Empty for ``generation=0`` (the seed pool).
        child_ids: Strategy ids of every candidate produced by the
            generation. For ``generation=0`` this is just the seed pool
            itself, mirrored here for symmetry.
        mean_fitness: Mean composite score across this generation's
            children, populated by ``record_generation_results``.
        best_fitness: Highest composite score observed for any child in
            this generation, populated by ``record_generation_results``.
        created_at: UTC timestamp the row was first written.
        completed_at: UTC timestamp at which fitness was recorded.
            ``None`` until the runner finalises the generation.
    """

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    generation: int = Field(ge=0)
    population_size: int = Field(default=0, ge=0)
    parent_ids: list[str] = Field(default_factory=list)
    child_ids: list[str] = Field(default_factory=list)
    mean_fitness: Optional[float] = None
    best_fitness: Optional[float] = None
    created_at: datetime = Field(default_factory=_utcnow)
    completed_at: Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────────────────────────────────────


class EvolutionStateStore(ABC):
    """Abstract base class for evolution-state persistence backends."""

    @abstractmethod
    async def upsert_generation(
        self, state: EvolutionGenerationState
    ) -> EvolutionGenerationState:
        """Insert ``state`` if absent, or replace the existing row.

        Args:
            state: Fully-populated :class:`EvolutionGenerationState`
                instance to persist.

        Returns:
            The persisted row.
        """

    @abstractmethod
    async def get_latest(
        self, experiment_id: str
    ) -> Optional[EvolutionGenerationState]:
        """Return the highest-``generation`` row for ``experiment_id``.

        Returns ``None`` when the experiment has no rows yet.
        """

    @abstractmethod
    async def list_generations(
        self, experiment_id: str
    ) -> list[EvolutionGenerationState]:
        """Return every generation row for ``experiment_id``, ordered by ``generation`` ascending."""

    async def ensure_indexes(self) -> None:
        """Create idempotent indexes used by the store. Default no-op."""
        return None


# ─────────────────────────────────────────────────────────────────────────────
# In-memory implementation
# ─────────────────────────────────────────────────────────────────────────────


class InMemoryEvolutionStateStore(EvolutionStateStore):
    """Process-local, dict-backed :class:`EvolutionStateStore`.

    Used by unit tests and local development when no Mongo handle is
    available. Rows are stored as deep copies of the input model to
    avoid accidental aliasing between caller-held references and the
    store's internal table.
    """

    def __init__(self) -> None:
        self._rows: dict[tuple[str, int], EvolutionGenerationState] = {}
        self._lock = asyncio.Lock()

    async def upsert_generation(
        self, state: EvolutionGenerationState
    ) -> EvolutionGenerationState:
        async with self._lock:
            key = (state.experiment_id, state.generation)
            existing = self._rows.get(key)
            stored = state.model_copy(deep=True)
            if existing is not None and existing.created_at is not None:
                stored.created_at = existing.created_at
            self._rows[key] = stored
            return stored.model_copy(deep=True)

    async def get_latest(
        self, experiment_id: str
    ) -> Optional[EvolutionGenerationState]:
        async with self._lock:
            rows = [
                row
                for (exp, _gen), row in self._rows.items()
                if exp == experiment_id
            ]
            if not rows:
                return None
            rows.sort(key=lambda r: r.generation, reverse=True)
            return rows[0].model_copy(deep=True)

    async def list_generations(
        self, experiment_id: str
    ) -> list[EvolutionGenerationState]:
        async with self._lock:
            rows = [
                row.model_copy(deep=True)
                for (exp, _gen), row in self._rows.items()
                if exp == experiment_id
            ]
            rows.sort(key=lambda r: r.generation)
            return rows


# ─────────────────────────────────────────────────────────────────────────────
# Mongo implementation
# ─────────────────────────────────────────────────────────────────────────────


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` when it is awaitable; otherwise return as-is.

    PyMongo Async returns coroutines for ``create_index`` while some
    test fakes return plain values; this helper bridges both shapes.
    """
    if asyncio.iscoroutine(value):
        return await value
    return value


def _state_to_doc(state: EvolutionGenerationState) -> dict[str, Any]:
    """Serialise an :class:`EvolutionGenerationState` to a flat Mongo doc."""
    return state.model_dump(mode="python")


def _doc_to_state(
    doc: Optional[dict[str, Any]],
) -> Optional[EvolutionGenerationState]:
    """Deserialise a Mongo document back into :class:`EvolutionGenerationState`."""
    if doc is None:
        return None
    data = {k: v for k, v in doc.items() if k != "_id"}
    try:
        return EvolutionGenerationState(**data)
    except Exception as exc:  # noqa: BLE001 - tolerate legacy docs
        logger.warning("Failed to parse evolution-state document: %s", exc)
        return None


class MongoEvolutionStateStore(EvolutionStateStore):
    """MongoDB-backed :class:`EvolutionStateStore` implementation.

    Documents live in :data:`EVOLUTION_STATE_COLLECTION_NAME` keyed by
    the ``(experiment_id, generation)`` compound. Compatible with both
    ``pymongo.AsyncMongoClient`` databases and the in-process fake
    collection used by the strategy unit tests; only a small subset of
    the collection API is exercised (``find_one``, ``find``,
    ``replace_one``, ``create_index``).

    Args:
        db: An async MongoDB database handle.
        collection_name: Override the collection name. Defaults to
            :data:`EVOLUTION_STATE_COLLECTION_NAME`.
    """

    def __init__(
        self,
        db: Any,
        *,
        collection_name: str = EVOLUTION_STATE_COLLECTION_NAME,
    ) -> None:
        if db is None:
            raise ValueError(
                "MongoEvolutionStateStore requires a non-None database handle"
            )
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
        """Create the unique compound index plus the per-experiment lookup.

        Idempotent: re-issuing ``create_index`` on the same key spec is
        a no-op according to the MongoDB driver. Best-effort; logs and
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
                    collection.create_index(
                        [("experiment_id", 1), ("generation", 1)],
                        unique=True,
                    )
                )
                await _maybe_await(
                    collection.create_index("experiment_id")
                )
            except Exception as exc:  # noqa: BLE001 - best effort
                logger.warning(
                    "MongoEvolutionStateStore.ensure_indexes failed "
                    "(non-fatal): %s",
                    exc,
                )
            self._indexes_ready = True

    # ── EvolutionStateStore methods ────────────────────────────────────

    async def upsert_generation(
        self, state: EvolutionGenerationState
    ) -> EvolutionGenerationState:
        existing = await self._find_one(state.experiment_id, state.generation)
        stored = state.model_copy(deep=True)
        if existing is not None:
            stored.created_at = existing.created_at
        await self.collection.replace_one(
            {
                "experiment_id": stored.experiment_id,
                "generation": stored.generation,
            },
            _state_to_doc(stored),
            upsert=True,
        )
        return stored

    async def get_latest(
        self, experiment_id: str
    ) -> Optional[EvolutionGenerationState]:
        cursor = self.collection.find(
            {"experiment_id": experiment_id}
        ).sort("generation", -1)
        async for doc in cursor:
            row = _doc_to_state(doc)
            if row is not None:
                return row
            break
        return None

    async def list_generations(
        self, experiment_id: str
    ) -> list[EvolutionGenerationState]:
        cursor = self.collection.find(
            {"experiment_id": experiment_id}
        ).sort("generation", 1)
        out: list[EvolutionGenerationState] = []
        async for doc in cursor:
            row = _doc_to_state(doc)
            if row is not None:
                out.append(row)
        return out

    # ── Internal helpers ────────────────────────────────────────────────

    async def _find_one(
        self, experiment_id: str, generation: int
    ) -> Optional[EvolutionGenerationState]:
        doc = await self.collection.find_one(
            {"experiment_id": experiment_id, "generation": generation}
        )
        return _doc_to_state(doc)


# Internal helper kept private but useful for stable tests of cloning.
def _clone_state(state: EvolutionGenerationState) -> EvolutionGenerationState:
    """Return a deep copy of ``state`` (used by the in-memory store)."""
    return copy.deepcopy(state)
