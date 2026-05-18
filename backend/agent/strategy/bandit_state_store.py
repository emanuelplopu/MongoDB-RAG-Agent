"""Persistent state store for the UCB1 bandit exploration mode (Task 76 / P7).

The bandit mode in
:mod:`backend.agent.strategy.exploration_modes` decides which arm
(candidate :class:`~backend.agent.strategy.models.StrategySpec`) to pull
next using UCB1::

    score = mean_reward + c * sqrt(ln(total_pulls) / arm.pull_count)

For the allocator to behave consistently across process restarts (a
nightly experiment may resume mid-run), per-arm bandit progress is
persisted in the dedicated ``strategy_bandit_state`` collection keyed by
``(experiment_id, arm_id)`` — one document per arm per experiment.

Two backends ship:

* :class:`InMemoryBanditStateStore` — in-process dict-of-dicts used by
  unit tests and local development when no Mongo handle is available.
* :class:`MongoBanditStateStore` — persistent backend wired against a
  ``pymongo.AsyncMongoClient`` database handle (Repo rule #3 — no Motor).

The ABC ``BanditStateStore`` defines the small async surface the
exploration mode actually exercises::

    upsert_arm     ── Idempotently create the (experiment_id, arm_id) row.
    get_arm        ── Fetch one row.
    list_arms      ── Fetch all rows for an experiment.
    record_pull    ── Atomically apply one observation (reward, pull_count++).
    ensure_indexes ── Create the unique compound index. Best-effort.

Reward semantics: callers pass the *composite* score from
:class:`~backend.agent.strategy.evaluation_runner_adapter.EvaluationRunnerAdapter`
(0.0 .. 1.0). The store does not normalize on its own.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field

logger = logging.getLogger(__name__)

__all__ = [
    "BANDIT_STATE_COLLECTION_NAME",
    "BanditArmState",
    "BanditStateStore",
    "InMemoryBanditStateStore",
    "MongoBanditStateStore",
]


#: Canonical Mongo collection name for persisted bandit-arm state.
BANDIT_STATE_COLLECTION_NAME: str = "strategy_bandit_state"


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic model
# ─────────────────────────────────────────────────────────────────────────────


class BanditArmState(BaseModel):
    """Per-arm running state for a UCB1 bandit experiment.

    One row per ``(experiment_id, arm_id)`` pair. ``arm_id`` is the
    candidate strategy id materialized by the underlying
    :class:`~backend.agent.strategy.exploration_modes.GridMode` pool.

    Attributes:
        experiment_id: Owning experiment identifier (matches
            :attr:`StrategyExperimentJob.experiment_id`).
        arm_id: Candidate strategy id treated as one bandit arm.
        pull_count: Number of times this arm has been pulled.
        total_reward: Cumulative reward summed across all pulls. The
            :attr:`mean_reward` derived field divides by
            ``max(pull_count, 1)``.
        last_pulled_at: UTC timestamp of the most recent pull. ``None``
            until the first pull lands.
        created_at: UTC timestamp the row was first written.
        updated_at: UTC timestamp of the most recent write.
    """

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    arm_id: str
    pull_count: int = Field(default=0, ge=0)
    total_reward: float = Field(default=0.0)
    last_pulled_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mean_reward(self) -> float:
        """Average reward per pull. Returns ``0.0`` until the first pull."""
        if self.pull_count <= 0:
            return 0.0
        return self.total_reward / float(self.pull_count)


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────────────────────────────────────


class BanditStateStore(ABC):
    """Abstract base class for bandit-arm state persistence backends."""

    @abstractmethod
    async def upsert_arm(self, state: BanditArmState) -> BanditArmState:
        """Insert ``state`` if absent or replace the existing row.

        Args:
            state: Fully-populated :class:`BanditArmState` to persist.

        Returns:
            The persisted row (with ``updated_at`` refreshed).
        """

    @abstractmethod
    async def get_arm(
        self, experiment_id: str, arm_id: str
    ) -> Optional[BanditArmState]:
        """Return the arm row for ``(experiment_id, arm_id)`` or ``None``."""

    @abstractmethod
    async def list_arms(self, experiment_id: str) -> list[BanditArmState]:
        """Return every arm row for ``experiment_id``.

        Order is not guaranteed; callers that need determinism (e.g. UCB
        tie-breaking by lowest ``arm_id``) sort the result themselves.
        """

    @abstractmethod
    async def record_pull(
        self,
        experiment_id: str,
        arm_id: str,
        reward: float,
    ) -> BanditArmState:
        """Atomically apply one observation to ``(experiment_id, arm_id)``.

        Increments ``pull_count`` by one, accumulates ``reward`` into
        ``total_reward``, and refreshes ``last_pulled_at`` and
        ``updated_at``. Creates the row when missing so the bandit
        never crashes on a first-pull race.

        Args:
            experiment_id: Owning experiment identifier.
            arm_id: Arm being pulled.
            reward: Observation in ``[0.0, 1.0]`` (composite score).

        Returns:
            The updated row.
        """

    async def ensure_indexes(self) -> None:
        """Create idempotent indexes used by the store. Default no-op."""
        return None


# ─────────────────────────────────────────────────────────────────────────────
# In-memory implementation
# ─────────────────────────────────────────────────────────────────────────────


class InMemoryBanditStateStore(BanditStateStore):
    """Process-local, dict-backed :class:`BanditStateStore`.

    Used by tests and local dev when no Mongo handle is available. All
    values are stored as :class:`BanditArmState` clones to avoid
    accidental aliasing between caller-held references and the store.
    """

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], BanditArmState] = {}
        self._lock = asyncio.Lock()

    async def upsert_arm(self, state: BanditArmState) -> BanditArmState:
        async with self._lock:
            key = (state.experiment_id, state.arm_id)
            existing = self._rows.get(key)
            if existing is None:
                stored = state.model_copy(deep=True)
                stored.created_at = stored.created_at or _utcnow()
                stored.updated_at = _utcnow()
                self._rows[key] = stored
                return stored.model_copy(deep=True)
            updated = state.model_copy(deep=True)
            updated.created_at = existing.created_at
            updated.updated_at = _utcnow()
            self._rows[key] = updated
            return updated.model_copy(deep=True)

    async def get_arm(
        self, experiment_id: str, arm_id: str
    ) -> Optional[BanditArmState]:
        async with self._lock:
            row = self._rows.get((experiment_id, arm_id))
            return row.model_copy(deep=True) if row is not None else None

    async def list_arms(self, experiment_id: str) -> list[BanditArmState]:
        async with self._lock:
            return [
                row.model_copy(deep=True)
                for (exp, _arm), row in self._rows.items()
                if exp == experiment_id
            ]

    async def record_pull(
        self,
        experiment_id: str,
        arm_id: str,
        reward: float,
    ) -> BanditArmState:
        async with self._lock:
            key = (experiment_id, arm_id)
            existing = self._rows.get(key)
            now = _utcnow()
            if existing is None:
                row = BanditArmState(
                    experiment_id=experiment_id,
                    arm_id=arm_id,
                    pull_count=1,
                    total_reward=float(reward),
                    last_pulled_at=now,
                    created_at=now,
                    updated_at=now,
                )
            else:
                row = existing.model_copy(
                    update={
                        "pull_count": existing.pull_count + 1,
                        "total_reward": existing.total_reward + float(reward),
                        "last_pulled_at": now,
                        "updated_at": now,
                    }
                )
            self._rows[key] = row
            return row.model_copy(deep=True)


# ─────────────────────────────────────────────────────────────────────────────
# Mongo implementation
# ─────────────────────────────────────────────────────────────────────────────


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` when it is awaitable; otherwise return it unchanged.

    PyMongo Async returns coroutines for ``create_index`` while some
    test fakes return plain values; this helper bridges both shapes.
    """
    if asyncio.iscoroutine(value):
        return await value
    return value


def _state_to_doc(state: BanditArmState) -> dict[str, Any]:
    """Serialise a :class:`BanditArmState` into a flat Mongo document.

    The ``mean_reward`` computed field is intentionally dropped before
    persistence — it is derived from ``total_reward`` / ``pull_count``
    and would otherwise drift out of sync after a non-atomic update.
    """
    payload = state.model_dump(mode="python")
    payload.pop("mean_reward", None)
    return payload


def _doc_to_state(doc: Optional[dict[str, Any]]) -> Optional[BanditArmState]:
    """Deserialise a Mongo document back into :class:`BanditArmState`."""
    if doc is None:
        return None
    data = {k: v for k, v in doc.items() if k != "_id"}
    data.pop("mean_reward", None)
    try:
        return BanditArmState(**data)
    except Exception as exc:  # noqa: BLE001 - tolerate legacy docs
        logger.warning("Failed to parse bandit-arm document: %s", exc)
        return None


class MongoBanditStateStore(BanditStateStore):
    """MongoDB-backed :class:`BanditStateStore` implementation.

    Documents live in :data:`BANDIT_STATE_COLLECTION_NAME` keyed by the
    ``(experiment_id, arm_id)`` compound. The store is compatible with
    both ``pymongo.AsyncMongoClient`` databases and the in-process fake
    collection used by the strategy unit tests; only a small subset of
    the collection API is exercised (``find_one``, ``find``,
    ``replace_one``, ``update_one``, ``create_index``).

    Args:
        db: An async MongoDB database handle.
        collection_name: Override the collection name. Defaults to
            :data:`BANDIT_STATE_COLLECTION_NAME`.
    """

    def __init__(
        self,
        db: Any,
        *,
        collection_name: str = BANDIT_STATE_COLLECTION_NAME,
    ) -> None:
        if db is None:
            raise ValueError(
                "MongoBanditStateStore requires a non-None database handle"
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
                        [("experiment_id", 1), ("arm_id", 1)], unique=True
                    )
                )
                await _maybe_await(
                    collection.create_index("experiment_id")
                )
            except Exception as exc:  # noqa: BLE001 - best effort
                logger.warning(
                    "MongoBanditStateStore.ensure_indexes failed (non-fatal): %s",
                    exc,
                )
            self._indexes_ready = True

    # ── BanditStateStore methods ───────────────────────────────────────

    async def upsert_arm(self, state: BanditArmState) -> BanditArmState:
        now = _utcnow()
        existing = await self.get_arm(state.experiment_id, state.arm_id)
        stored = state.model_copy(deep=True)
        if existing is not None:
            stored.created_at = existing.created_at
        else:
            stored.created_at = stored.created_at or now
        stored.updated_at = now
        await self.collection.replace_one(
            {
                "experiment_id": stored.experiment_id,
                "arm_id": stored.arm_id,
            },
            _state_to_doc(stored),
            upsert=True,
        )
        return stored

    async def get_arm(
        self, experiment_id: str, arm_id: str
    ) -> Optional[BanditArmState]:
        doc = await self.collection.find_one(
            {"experiment_id": experiment_id, "arm_id": arm_id}
        )
        return _doc_to_state(doc)

    async def list_arms(self, experiment_id: str) -> list[BanditArmState]:
        cursor = self.collection.find({"experiment_id": experiment_id})
        out: list[BanditArmState] = []
        async for doc in cursor:
            row = _doc_to_state(doc)
            if row is not None:
                out.append(row)
        return out

    async def record_pull(
        self,
        experiment_id: str,
        arm_id: str,
        reward: float,
    ) -> BanditArmState:
        now = _utcnow()
        existing = await self.get_arm(experiment_id, arm_id)
        if existing is None:
            row = BanditArmState(
                experiment_id=experiment_id,
                arm_id=arm_id,
                pull_count=1,
                total_reward=float(reward),
                last_pulled_at=now,
                created_at=now,
                updated_at=now,
            )
            await self.collection.replace_one(
                {"experiment_id": experiment_id, "arm_id": arm_id},
                _state_to_doc(row),
                upsert=True,
            )
            return row
        row = existing.model_copy(
            update={
                "pull_count": existing.pull_count + 1,
                "total_reward": existing.total_reward + float(reward),
                "last_pulled_at": now,
                "updated_at": now,
            }
        )
        await self.collection.replace_one(
            {"experiment_id": experiment_id, "arm_id": arm_id},
            _state_to_doc(row),
            upsert=True,
        )
        return row


# Internal helper kept private but useful for stable tests of cloning.
def _clone_state(state: BanditArmState) -> BanditArmState:
    """Return a deep copy of ``state`` (used by the in-memory store)."""
    return copy.deepcopy(state)
