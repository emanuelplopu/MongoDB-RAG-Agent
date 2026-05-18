"""Strategy experiment-job persistence layer (Phase 6 / Task 73).

Provides the durable lifecycle store for
:class:`backend.agent.strategy.experiment_job_models.StrategyExperimentJob`.

Mirrors the ABC + ``InMemory*`` + ``Mongo*`` shape used by
:mod:`backend.agent.strategy.spec_store` and
:mod:`backend.agent.strategy.scheduler_store` so the runner (P5) and
operator surfaces (P11) can wire against either backend uniformly.

State-machine enforcement: every status mutation goes through
:meth:`ExperimentJobStore.update_status` (and its
:meth:`ExperimentJobStore.finalize` shortcut), which validates the
transition against
:meth:`StrategyExperimentJob.allowed_transitions` and raises
:class:`InvalidJobTransitionError` when illegal. No silent coercion.

Repo rule #3: PyMongo Async only. The Mongo implementation uses the
``pymongo.AsyncMongoClient`` collection API surface (``find_one``,
``find``, ``update_one``, ``replace_one``, ``create_index``).
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

from backend.agent.strategy.experiment_job_models import (
    JobProgress,
    JobStatus,
    StrategyExperimentJob,
)

logger = logging.getLogger(__name__)

__all__ = [
    "EXPERIMENT_JOB_COLLECTION_NAME",
    "InvalidJobTransitionError",
    "ExperimentJobStore",
    "InMemoryExperimentJobStore",
    "MongoExperimentJobStore",
]


#: Canonical Mongo collection name for persisted experiment jobs.
EXPERIMENT_JOB_COLLECTION_NAME: str = "strategy_experiment_jobs"


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


class InvalidJobTransitionError(Exception):
    """Raised when :meth:`ExperimentJobStore.update_status` rejects a transition.

    Attributes:
        job_id: Identifier of the job whose transition was rejected.
        from_status: The current status of the job at the time of the
            attempt.
        to_status: The status the caller attempted to move to.
    """

    def __init__(
        self,
        *,
        job_id: str,
        from_status: JobStatus,
        to_status: JobStatus,
    ) -> None:
        super().__init__(
            f"Illegal job transition for {job_id}: "
            f"{getattr(from_status, 'value', from_status)} -> "
            f"{getattr(to_status, 'value', to_status)}"
        )
        self.job_id = job_id
        self.from_status = from_status
        self.to_status = to_status


# ═══════════════════════════════════════════════════════════════════════════════
# Abstract interface
# ═══════════════════════════════════════════════════════════════════════════════


class ExperimentJobStore(ABC):
    """Abstract base for :class:`StrategyExperimentJob` persistence backends.

    Implementations expose async CRUD plus targeted mutators:

    * :meth:`update_status` enforces the legal state machine.
    * :meth:`append_trace_id` accumulates trace ids with ``$addToSet``
      semantics so retries are idempotent.
    * :meth:`update_progress` replaces the embedded :class:`JobProgress`.
    * :meth:`finalize` is a convenience wrapper that writes the
      ``result_summary`` and atomically transitions to ``COMPLETED``.
    """

    @abstractmethod
    async def create(self, job: StrategyExperimentJob) -> StrategyExperimentJob:
        """Persist a freshly created ``job`` and return the stored copy.

        Args:
            job: The job to persist. ``created_at`` / ``updated_at`` are
                refreshed if not already set.

        Returns:
            The persisted :class:`StrategyExperimentJob`.
        """

    @abstractmethod
    async def get(self, job_id: str) -> Optional[StrategyExperimentJob]:
        """Return the job identified by ``job_id`` or ``None``."""

    @abstractmethod
    async def list(
        self,
        *,
        status: Optional[JobStatus] = None,
        schedule_id: Optional[str] = None,
        tenant: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[StrategyExperimentJob]:
        """Return jobs matching the supplied filters.

        Args:
            status: Optional :class:`JobStatus` filter.
            schedule_id: Optional ``schedule_id`` filter.
            tenant: Optional ``tenant`` filter.
            since: Optional inclusive lower bound on ``created_at``.
            limit: Hard cap on returned rows (most recent first).

        Returns:
            Jobs ordered by ``created_at`` descending, capped to
            ``limit``.
        """

    @abstractmethod
    async def update_status(
        self,
        job_id: str,
        new_status: JobStatus,
        *,
        error: Optional[str] = None,
    ) -> StrategyExperimentJob:
        """Transition ``job_id`` to ``new_status``.

        Enforces
        :meth:`StrategyExperimentJob.allowed_transitions`. Side-effects
        on legal transitions:

        * ``RUNNING``: sets ``started_at`` to ``utcnow`` if unset.
        * ``COMPLETED`` / ``FAILED`` / ``CANCELLED``: sets
          ``completed_at`` to ``utcnow``.
        * ``FAILED``: writes ``error`` (when provided).

        Args:
            job_id: Identifier of the job to mutate.
            new_status: Target lifecycle status.
            error: Optional error string; persisted only when
                ``new_status`` is ``FAILED``.

        Raises:
            KeyError: ``job_id`` does not exist.
            InvalidJobTransitionError: The transition is not permitted
                by the state machine.

        Returns:
            The updated job document.
        """

    @abstractmethod
    async def append_trace_id(self, job_id: str, trace_id: str) -> None:
        """Add ``trace_id`` to the job's ``trace_ids`` set.

        Uses ``$addToSet`` semantics — appending the same trace id
        repeatedly is a no-op so runner retries cannot duplicate entries.

        Args:
            job_id: Identifier of the job.
            trace_id: A
                :attr:`backend.agent.strategy.run_trace_store.RunTraceDoc.trace_id`
                value to associate with the job.
        """

    @abstractmethod
    async def update_progress(self, job_id: str, progress: JobProgress) -> None:
        """Replace the job's embedded :class:`JobProgress` document.

        Args:
            job_id: Identifier of the job.
            progress: New progress snapshot. ``last_progress_at`` is
                stamped to ``utcnow`` when not already set.
        """

    @abstractmethod
    async def finalize(
        self,
        job_id: str,
        *,
        result_summary: dict,
        completed_at: datetime,
    ) -> StrategyExperimentJob:
        """Mark the job ``COMPLETED`` and persist ``result_summary``.

        Enforces :meth:`StrategyExperimentJob.allowed_transitions` —
        only jobs currently in ``RUNNING`` may be finalized.

        Args:
            job_id: Identifier of the job.
            result_summary: Aggregate scores, latency, top-winner data.
            completed_at: UTC timestamp recorded as ``completed_at``.

        Raises:
            KeyError: ``job_id`` does not exist.
            InvalidJobTransitionError: The job is not in a status from
                which ``COMPLETED`` is reachable.

        Returns:
            The finalized job.
        """

    async def ensure_indexes(self) -> None:
        """Create idempotent indexes used by the store. Default no-op."""
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# In-memory implementation
# ═══════════════════════════════════════════════════════════════════════════════


class InMemoryExperimentJobStore(ExperimentJobStore):
    """Process-local, dict-backed :class:`ExperimentJobStore`.

    Used by tests and as a fallback when no MongoDB handle is
    available. Jobs are stored as deep copies to avoid accidental
    aliasing between caller-held references and the store's internal
    state.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, StrategyExperimentJob] = {}

    async def create(self, job: StrategyExperimentJob) -> StrategyExperimentJob:
        if job.id in self._jobs:
            raise ValueError(f"Job {job.id!r} already exists")
        copy = job.model_copy(deep=True)
        copy.updated_at = _utcnow()
        self._jobs[copy.id] = copy
        return copy.model_copy(deep=True)

    async def get(self, job_id: str) -> Optional[StrategyExperimentJob]:
        existing = self._jobs.get(job_id)
        return existing.model_copy(deep=True) if existing is not None else None

    async def list(
        self,
        *,
        status: Optional[JobStatus] = None,
        schedule_id: Optional[str] = None,
        tenant: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[StrategyExperimentJob]:
        results: list[StrategyExperimentJob] = []
        for job in self._jobs.values():
            if status is not None and job.status != status:
                continue
            if schedule_id is not None and job.schedule_id != schedule_id:
                continue
            if tenant is not None and job.tenant != tenant:
                continue
            if since is not None and job.created_at < since:
                continue
            results.append(job.model_copy(deep=True))
        results.sort(key=lambda j: j.created_at, reverse=True)
        return results[: int(limit)]

    async def update_status(
        self,
        job_id: str,
        new_status: JobStatus,
        *,
        error: Optional[str] = None,
    ) -> StrategyExperimentJob:
        existing = self._jobs.get(job_id)
        if existing is None:
            raise KeyError(job_id)
        _validate_transition(job_id, existing.status, new_status)
        updates: dict[str, Any] = {"status": new_status}
        now = _utcnow()
        if new_status is JobStatus.RUNNING and existing.started_at is None:
            updates["started_at"] = now
        if StrategyExperimentJob.is_terminal(new_status):
            updates["completed_at"] = now
        if new_status is JobStatus.FAILED and error is not None:
            updates["error"] = error
        updated = existing.model_copy(update=updates)
        updated.updated_at = now
        self._jobs[job_id] = updated
        return updated.model_copy(deep=True)

    async def append_trace_id(self, job_id: str, trace_id: str) -> None:
        existing = self._jobs.get(job_id)
        if existing is None:
            raise KeyError(job_id)
        if trace_id in existing.trace_ids:
            return
        new_ids = list(existing.trace_ids) + [trace_id]
        updated = existing.model_copy(update={"trace_ids": new_ids})
        updated.updated_at = _utcnow()
        self._jobs[job_id] = updated

    async def update_progress(self, job_id: str, progress: JobProgress) -> None:
        existing = self._jobs.get(job_id)
        if existing is None:
            raise KeyError(job_id)
        snapshot = progress.model_copy(deep=True)
        if snapshot.last_progress_at is None:
            snapshot.last_progress_at = _utcnow()
        updated = existing.model_copy(update={"progress": snapshot})
        updated.updated_at = _utcnow()
        self._jobs[job_id] = updated

    async def finalize(
        self,
        job_id: str,
        *,
        result_summary: dict,
        completed_at: datetime,
    ) -> StrategyExperimentJob:
        existing = self._jobs.get(job_id)
        if existing is None:
            raise KeyError(job_id)
        _validate_transition(job_id, existing.status, JobStatus.COMPLETED)
        updated = existing.model_copy(
            update={
                "status": JobStatus.COMPLETED,
                "result_summary": dict(result_summary),
                "completed_at": completed_at,
            }
        )
        updated.updated_at = _utcnow()
        self._jobs[job_id] = updated
        return updated.model_copy(deep=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Mongo implementation
# ═══════════════════════════════════════════════════════════════════════════════


class MongoExperimentJobStore(ExperimentJobStore):
    """MongoDB-backed :class:`ExperimentJobStore`.

    Documents are stored in :data:`EXPERIMENT_JOB_COLLECTION_NAME`
    keyed by the :attr:`StrategyExperimentJob.id` field. Indexes
    (created idempotently by :meth:`ensure_indexes`):

    * unique on ``id``
    * non-unique on ``(schedule_id, created_at)`` for per-schedule history
    * non-unique on ``(status, priority, created_at)`` for the
      runner's queue scan
    * non-unique on ``(tenant, created_at)`` for tenant-scoped views

    Args:
        db: An async MongoDB database handle (PyMongo Async).
        collection_name: Override the collection name. Defaults to
            :data:`EXPERIMENT_JOB_COLLECTION_NAME`.
    """

    def __init__(
        self,
        db: Any,
        *,
        collection_name: str = EXPERIMENT_JOB_COLLECTION_NAME,
    ) -> None:
        if db is None:
            raise ValueError(
                "MongoExperimentJobStore requires a non-None database handle"
            )
        self._db = db
        self._collection_name = collection_name
        self._index_lock = asyncio.Lock()
        self._indexes_ready = False

    @property
    def collection(self) -> Any:
        """Return the underlying async Mongo collection handle."""
        return self._db[self._collection_name]

    # ── Index management ────────────────────────────────────────────────

    async def ensure_indexes(self) -> None:
        """Create the documented indexes idempotently.

        Best-effort: a failed ``create_index`` is logged at ``WARNING``
        but does not raise so app lifespan startup remains resilient
        when the user lacks DDL privileges.
        """
        if self._indexes_ready:
            return
        async with self._index_lock:
            if self._indexes_ready:
                return
            collection = self.collection
            specs: list[tuple[Any, dict[str, Any]]] = [
                (("id",), {"unique": True}),
                ([("schedule_id", 1), ("created_at", -1)], {}),
                ([("status", 1), ("priority", -1), ("created_at", 1)], {}),
                ([("tenant", 1), ("created_at", -1)], {}),
            ]
            for spec, kwargs in specs:
                try:
                    if isinstance(spec, tuple):
                        await _maybe_await(
                            collection.create_index(*spec, **kwargs)
                        )
                    else:
                        await _maybe_await(
                            collection.create_index(spec, **kwargs)
                        )
                except Exception as exc:  # noqa: BLE001 - best effort
                    logger.warning(
                        "MongoExperimentJobStore.ensure_indexes(%s) failed (non-fatal): %s",
                        spec,
                        exc,
                    )
            self._indexes_ready = True

    # ── ExperimentJobStore methods ──────────────────────────────────────

    async def create(self, job: StrategyExperimentJob) -> StrategyExperimentJob:
        existing = await self.collection.find_one({"id": job.id})
        if existing is not None:
            raise ValueError(f"Job {job.id!r} already exists")
        copy = job.model_copy(deep=True)
        copy.updated_at = _utcnow()
        await self.collection.replace_one(
            {"id": copy.id}, _job_to_doc(copy), upsert=True
        )
        return copy

    async def get(self, job_id: str) -> Optional[StrategyExperimentJob]:
        doc = await self.collection.find_one({"id": job_id})
        return _doc_to_job(doc)

    async def list(
        self,
        *,
        status: Optional[JobStatus] = None,
        schedule_id: Optional[str] = None,
        tenant: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[StrategyExperimentJob]:
        query: dict[str, Any] = {}
        if status is not None:
            query["status"] = (
                status.value if isinstance(status, JobStatus) else status
            )
        if schedule_id is not None:
            query["schedule_id"] = schedule_id
        if tenant is not None:
            query["tenant"] = tenant
        if since is not None:
            query["created_at"] = {"$gte": since}

        cursor = self.collection.find(query).sort("created_at", -1)
        out: list[StrategyExperimentJob] = []
        count = 0
        async for doc in cursor:
            job = _doc_to_job(doc)
            if job is None:
                continue
            out.append(job)
            count += 1
            if count >= int(limit):
                break
        return out

    async def update_status(
        self,
        job_id: str,
        new_status: JobStatus,
        *,
        error: Optional[str] = None,
    ) -> StrategyExperimentJob:
        existing = await self.get(job_id)
        if existing is None:
            raise KeyError(job_id)
        _validate_transition(job_id, existing.status, new_status)

        now = _utcnow()
        sets: dict[str, Any] = {
            "status": new_status.value,
            "updated_at": now,
        }
        if new_status is JobStatus.RUNNING and existing.started_at is None:
            sets["started_at"] = now
        if StrategyExperimentJob.is_terminal(new_status):
            sets["completed_at"] = now
        if new_status is JobStatus.FAILED and error is not None:
            sets["error"] = error

        await self.collection.update_one({"id": job_id}, {"$set": sets})
        refreshed = await self.get(job_id)
        if refreshed is None:  # pragma: no cover - defensive
            raise KeyError(job_id)
        return refreshed

    async def append_trace_id(self, job_id: str, trace_id: str) -> None:
        result = await self.collection.update_one(
            {"id": job_id},
            {
                "$addToSet": {"trace_ids": trace_id},
                "$set": {"updated_at": _utcnow()},
            },
        )
        if getattr(result, "deleted_count", None) is None and getattr(
            result, "matched_count", 0
        ) == 0:
            # Some driver versions surface "matched_count"; defensively
            # cross-check with a follow-up read for the fake collection.
            existing = await self.collection.find_one({"id": job_id})
            if existing is None:
                raise KeyError(job_id)

    async def update_progress(self, job_id: str, progress: JobProgress) -> None:
        snapshot = progress.model_copy(deep=True)
        if snapshot.last_progress_at is None:
            snapshot.last_progress_at = _utcnow()
        existing = await self.collection.find_one({"id": job_id})
        if existing is None:
            raise KeyError(job_id)
        await self.collection.update_one(
            {"id": job_id},
            {
                "$set": {
                    "progress": snapshot.model_dump(mode="python"),
                    "updated_at": _utcnow(),
                }
            },
        )

    async def finalize(
        self,
        job_id: str,
        *,
        result_summary: dict,
        completed_at: datetime,
    ) -> StrategyExperimentJob:
        existing = await self.get(job_id)
        if existing is None:
            raise KeyError(job_id)
        _validate_transition(job_id, existing.status, JobStatus.COMPLETED)
        await self.collection.update_one(
            {"id": job_id},
            {
                "$set": {
                    "status": JobStatus.COMPLETED.value,
                    "result_summary": dict(result_summary),
                    "completed_at": completed_at,
                    "updated_at": _utcnow(),
                }
            },
        )
        refreshed = await self.get(job_id)
        if refreshed is None:  # pragma: no cover - defensive
            raise KeyError(job_id)
        return refreshed


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_transition(
    job_id: str,
    from_status: JobStatus,
    to_status: JobStatus,
) -> None:
    """Raise :class:`InvalidJobTransitionError` for illegal transitions.

    Same-status writes are rejected too — the runner should not rely
    on idempotent status writes; ``update_progress`` /
    ``append_trace_id`` cover the no-op cases.
    """
    current = (
        from_status
        if isinstance(from_status, JobStatus)
        else JobStatus(from_status)
    )
    target = (
        to_status if isinstance(to_status, JobStatus) else JobStatus(to_status)
    )
    if target not in StrategyExperimentJob.allowed_transitions(current):
        raise InvalidJobTransitionError(
            job_id=job_id, from_status=current, to_status=target
        )


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` when it is a coroutine; otherwise return it.

    PyMongo Async returns coroutines for ``create_index`` while some
    test fakes return plain values; this helper bridges both shapes.
    """
    if asyncio.iscoroutine(value):
        return await value
    return value


def _job_to_doc(job: StrategyExperimentJob) -> dict[str, Any]:
    """Serialize a :class:`StrategyExperimentJob` into a Mongo document."""
    return job.model_dump(mode="python")


def _doc_to_job(doc: Optional[dict[str, Any]]) -> Optional[StrategyExperimentJob]:
    """Deserialize a Mongo document back into :class:`StrategyExperimentJob`."""
    if doc is None:
        return None
    data = {k: v for k, v in doc.items() if k != "_id"}
    try:
        return StrategyExperimentJob.model_validate(data)
    except Exception as exc:  # noqa: BLE001 - tolerate legacy / partial docs
        logger.warning("Failed to parse experiment job document: %s", exc)
        return None
