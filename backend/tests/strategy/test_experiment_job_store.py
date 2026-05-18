"""Tests for :mod:`backend.agent.strategy.experiment_job_store` (Task 73).

Covers parity between :class:`InMemoryExperimentJobStore` and
:class:`MongoExperimentJobStore` (against the shared ``_FakeAsyncDB``)
plus the state-machine, ``$addToSet`` idempotency, progress, finalize,
and index idempotency contracts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.agent.strategy.experiment_job_models import (
    JobProgress,
    JobStatus,
    StrategyExperimentJob,
)
from backend.agent.strategy.experiment_job_store import (
    EXPERIMENT_JOB_COLLECTION_NAME,
    ExperimentJobStore,
    InMemoryExperimentJobStore,
    InvalidJobTransitionError,
    MongoExperimentJobStore,
)
from backend.tests.strategy._fakes import _FakeAsyncDB


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _make_job(
    *,
    job_id: str = "job-1",
    schedule_id: str | None = "sched-1",
    tenant: str = "recallhub",
    status: JobStatus = JobStatus.SCHEDULED,
    mode: str = "regression",
    priority: int = 0,
    created_at: datetime | None = None,
    strategy_ids: list[str] | None = None,
    datasets: list[str] | None = None,
) -> StrategyExperimentJob:
    base_created = created_at or datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    return StrategyExperimentJob(
        id=job_id,
        schedule_id=schedule_id,
        experiment_id=f"exp-{job_id}",
        status=status,
        priority=priority,
        tenant=tenant,
        profile_key="legal_core",
        mode=mode,  # type: ignore[arg-type]
        datasets=datasets if datasets is not None else ["ds-a", "ds-b"],
        strategy_ids=strategy_ids if strategy_ids is not None else ["strat-a", "strat-b"],
        candidate_generator_id=None,
        scoring_profile="default",
        created_at=base_created,
        updated_at=base_created,
    )


@pytest.fixture
def in_memory_store() -> InMemoryExperimentJobStore:
    return InMemoryExperimentJobStore()


@pytest.fixture
def fake_db() -> _FakeAsyncDB:
    return _FakeAsyncDB()


@pytest.fixture
def mongo_store(fake_db: _FakeAsyncDB) -> MongoExperimentJobStore:
    return MongoExperimentJobStore(fake_db)


@pytest.fixture(params=["in_memory", "mongo"])
def store(
    request: pytest.FixtureRequest,
    in_memory_store: InMemoryExperimentJobStore,
    mongo_store: MongoExperimentJobStore,
) -> ExperimentJobStore:
    """Parametrize across both backends to exercise CRUD parity."""
    return in_memory_store if request.param == "in_memory" else mongo_store


# ─────────────────────────────────────────────────────────────────────────────
# Model-level state-machine tests
# ─────────────────────────────────────────────────────────────────────────────


def test_allowed_transitions_match_blueprint() -> None:
    """The state machine matches Blueprint 05 §8 exactly."""
    assert StrategyExperimentJob.allowed_transitions(JobStatus.SCHEDULED) == {
        JobStatus.QUEUED,
        JobStatus.CANCELLED,
    }
    assert StrategyExperimentJob.allowed_transitions(JobStatus.QUEUED) == {
        JobStatus.RUNNING,
        JobStatus.CANCELLED,
        JobStatus.PAUSED,
    }
    assert StrategyExperimentJob.allowed_transitions(JobStatus.RUNNING) == {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.PAUSED,
    }
    assert StrategyExperimentJob.allowed_transitions(JobStatus.PAUSED) == {
        JobStatus.QUEUED,
        JobStatus.CANCELLED,
    }
    # Terminal: no transitions out.
    for terminal in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        assert StrategyExperimentJob.allowed_transitions(terminal) == set()


# ─────────────────────────────────────────────────────────────────────────────
# Create + get round-trip
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_then_get_round_trip_preserves_all_fields(
    store: ExperimentJobStore,
) -> None:
    """``create`` followed by ``get`` returns an equivalent document."""
    job = _make_job(job_id="job-roundtrip", priority=5)
    job.trace_ids = []
    persisted = await store.create(job)
    assert persisted.id == "job-roundtrip"

    fetched = await store.get("job-roundtrip")
    assert fetched is not None
    assert fetched.id == job.id
    assert fetched.schedule_id == job.schedule_id
    assert fetched.experiment_id == job.experiment_id
    assert fetched.status == JobStatus.SCHEDULED
    assert fetched.priority == 5
    assert fetched.tenant == job.tenant
    assert fetched.profile_key == job.profile_key
    assert fetched.mode == job.mode
    assert fetched.datasets == job.datasets
    assert fetched.strategy_ids == job.strategy_ids
    assert fetched.scoring_profile == job.scoring_profile
    assert fetched.progress.completed_runs == 0
    assert fetched.trace_ids == []
    assert fetched.result_summary is None


@pytest.mark.asyncio
async def test_get_unknown_returns_none(store: ExperimentJobStore) -> None:
    assert await store.get("does-not-exist") is None


# ─────────────────────────────────────────────────────────────────────────────
# list filters
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_filters_by_status_schedule_tenant_and_since(
    store: ExperimentJobStore,
) -> None:
    """``list`` respects status / schedule_id / tenant / since filters."""
    base = datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc)
    await store.create(
        _make_job(
            job_id="j-sched-q",
            schedule_id="sched-A",
            tenant="recallhub",
            status=JobStatus.QUEUED,
            created_at=base + timedelta(hours=1),
        )
    )
    await store.create(
        _make_job(
            job_id="j-sched-r",
            schedule_id="sched-A",
            tenant="recallhub",
            status=JobStatus.RUNNING,
            created_at=base + timedelta(hours=2),
        )
    )
    await store.create(
        _make_job(
            job_id="j-other-tenant",
            schedule_id="sched-A",
            tenant="quellex",
            status=JobStatus.QUEUED,
            created_at=base + timedelta(hours=3),
        )
    )
    await store.create(
        _make_job(
            job_id="j-other-sched",
            schedule_id="sched-B",
            tenant="recallhub",
            status=JobStatus.QUEUED,
            created_at=base + timedelta(hours=4),
        )
    )

    queued = await store.list(status=JobStatus.QUEUED)
    assert {j.id for j in queued} == {"j-sched-q", "j-other-tenant", "j-other-sched"}

    sched_a = await store.list(schedule_id="sched-A")
    assert {j.id for j in sched_a} == {"j-sched-q", "j-sched-r", "j-other-tenant"}

    recall = await store.list(tenant="recallhub")
    assert {j.id for j in recall} == {"j-sched-q", "j-sched-r", "j-other-sched"}

    since_h2 = await store.list(since=base + timedelta(hours=2, minutes=30))
    assert {j.id for j in since_h2} == {"j-other-tenant", "j-other-sched"}

    combined = await store.list(
        status=JobStatus.QUEUED, schedule_id="sched-A", tenant="recallhub"
    )
    assert {j.id for j in combined} == {"j-sched-q"}

    # Limit caps the result count.
    limited = await store.list(limit=1)
    assert len(limited) == 1


# ─────────────────────────────────────────────────────────────────────────────
# update_status
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_status_legal_chain_succeeds(
    store: ExperimentJobStore,
) -> None:
    """SCHEDULED → QUEUED → RUNNING → COMPLETED is a fully legal walk."""
    await store.create(_make_job(job_id="j-walk"))

    queued = await store.update_status("j-walk", JobStatus.QUEUED)
    assert queued.status == JobStatus.QUEUED

    running = await store.update_status("j-walk", JobStatus.RUNNING)
    assert running.status == JobStatus.RUNNING
    assert running.started_at is not None

    completed = await store.update_status("j-walk", JobStatus.COMPLETED)
    assert completed.status == JobStatus.COMPLETED
    assert completed.completed_at is not None


@pytest.mark.asyncio
async def test_update_status_rejects_illegal_transitions(
    store: ExperimentJobStore,
) -> None:
    """Skipping or escaping terminal states raises :class:`InvalidJobTransitionError`."""
    await store.create(_make_job(job_id="j-bad"))

    # SCHEDULED -> RUNNING is illegal (must pass through QUEUED).
    with pytest.raises(InvalidJobTransitionError) as exc_info:
        await store.update_status("j-bad", JobStatus.RUNNING)
    assert exc_info.value.job_id == "j-bad"
    assert exc_info.value.from_status == JobStatus.SCHEDULED
    assert exc_info.value.to_status == JobStatus.RUNNING

    # Drive to COMPLETED via legal path, then attempt to escape.
    await store.update_status("j-bad", JobStatus.QUEUED)
    await store.update_status("j-bad", JobStatus.RUNNING)
    await store.update_status("j-bad", JobStatus.COMPLETED)

    # COMPLETED -> RUNNING must raise (terminal).
    with pytest.raises(InvalidJobTransitionError):
        await store.update_status("j-bad", JobStatus.RUNNING)


@pytest.mark.asyncio
async def test_update_status_failed_records_error(
    store: ExperimentJobStore,
) -> None:
    """Transitioning to FAILED writes the supplied ``error`` string."""
    await store.create(_make_job(job_id="j-fail"))
    await store.update_status("j-fail", JobStatus.QUEUED)
    await store.update_status("j-fail", JobStatus.RUNNING)

    failed = await store.update_status(
        "j-fail", JobStatus.FAILED, error="boom"
    )
    assert failed.status == JobStatus.FAILED
    assert failed.error == "boom"
    assert failed.completed_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# append_trace_id
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_append_trace_id_is_idempotent(
    store: ExperimentJobStore,
) -> None:
    """``$addToSet`` semantics — repeated trace ids do not duplicate."""
    await store.create(_make_job(job_id="j-traces"))

    await store.append_trace_id("j-traces", "trace-1")
    await store.append_trace_id("j-traces", "trace-2")
    # Retry the same trace id — must remain a single entry.
    await store.append_trace_id("j-traces", "trace-1")
    await store.append_trace_id("j-traces", "trace-2")
    await store.append_trace_id("j-traces", "trace-3")

    fetched = await store.get("j-traces")
    assert fetched is not None
    assert sorted(fetched.trace_ids) == ["trace-1", "trace-2", "trace-3"]


# ─────────────────────────────────────────────────────────────────────────────
# update_progress
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_progress_replaces_embedded_document(
    store: ExperimentJobStore,
) -> None:
    """``update_progress`` replaces the embedded :class:`JobProgress`."""
    await store.create(_make_job(job_id="j-progress"))
    progress = JobProgress(
        total_runs=10,
        completed_runs=4,
        failed_runs=1,
        current_strategy="strat-a",
        current_dataset="ds-1",
        current_case_index=3,
    )
    await store.update_progress("j-progress", progress)

    fetched = await store.get("j-progress")
    assert fetched is not None
    assert fetched.progress.total_runs == 10
    assert fetched.progress.completed_runs == 4
    assert fetched.progress.failed_runs == 1
    assert fetched.progress.current_strategy == "strat-a"
    assert fetched.progress.current_dataset == "ds-1"
    assert fetched.progress.current_case_index == 3
    assert fetched.progress.last_progress_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# finalize
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_finalize_writes_result_summary_and_marks_completed(
    store: ExperimentJobStore,
) -> None:
    """``finalize`` writes ``result_summary``, sets ``COMPLETED`` and ``completed_at``."""
    await store.create(_make_job(job_id="j-finalize"))
    await store.update_status("j-finalize", JobStatus.QUEUED)
    await store.update_status("j-finalize", JobStatus.RUNNING)

    completion_ts = datetime(2026, 5, 18, 23, 30, tzinfo=timezone.utc)
    summary = {
        "winner": "strat-a@1.2.0",
        "p50_latency_ms": 410,
        "p95_latency_ms": 950,
        "composite_score": 0.87,
    }
    finalized = await store.finalize(
        "j-finalize",
        result_summary=summary,
        completed_at=completion_ts,
    )
    assert finalized.status == JobStatus.COMPLETED
    assert finalized.completed_at == completion_ts
    assert finalized.result_summary == summary

    fetched = await store.get("j-finalize")
    assert fetched is not None
    assert fetched.status == JobStatus.COMPLETED
    assert fetched.result_summary == summary


@pytest.mark.asyncio
async def test_finalize_rejects_non_running_jobs(
    store: ExperimentJobStore,
) -> None:
    """``finalize`` only accepts jobs currently in ``RUNNING``."""
    await store.create(_make_job(job_id="j-finalize-bad"))
    # Still in SCHEDULED — finalize must reject.
    with pytest.raises(InvalidJobTransitionError):
        await store.finalize(
            "j-finalize-bad",
            result_summary={"winner": "x"},
            completed_at=datetime.now(timezone.utc),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Mongo-specific: ensure_indexes idempotency + persistence shape
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mongo_store_requires_db() -> None:
    with pytest.raises(ValueError):
        MongoExperimentJobStore(None)


@pytest.mark.asyncio
async def test_mongo_ensure_indexes_is_idempotent(
    mongo_store: MongoExperimentJobStore, fake_db: _FakeAsyncDB
) -> None:
    """Indexes are created exactly once across repeated invocations."""
    await mongo_store.ensure_indexes()
    await mongo_store.ensure_indexes()  # second call must short-circuit
    collection = fake_db[EXPERIMENT_JOB_COLLECTION_NAME]
    # Four distinct indexes, registered exactly once each.
    assert len(collection.created_indexes) == 4  # type: ignore[attr-defined]
