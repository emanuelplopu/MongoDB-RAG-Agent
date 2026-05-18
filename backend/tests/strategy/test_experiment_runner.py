"""Tests for :mod:`backend.agent.strategy.experiment_runner` (Task 74 / P5).

Covers happy path, multi-strategy winner selection, resource pause,
per-case failure isolation, catastrophic evaluation failure,
cooperative cancellation, progress throttling, and the
``InvalidJobStateError`` precondition.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from backend.agent.strategy.experiment_job_models import (
    JobStatus,
    StrategyExperimentJob,
)
from backend.agent.strategy.experiment_job_store import (
    InMemoryExperimentJobStore,
)
from backend.agent.strategy.experiment_runner import (
    ExperimentResult,
    InvalidJobStateError,
    JobResultSummary,
    StrategyExperimentRunner,
)
from backend.agent.strategy.models import (
    StrategyGraph,
    StrategyNode,
    StrategyRunResult,
    StrategyRunState,
    StrategySpec,
)
from backend.agent.strategy.scheduler_models import ResourceLimits
from backend.agent.strategy.spec_store import InMemorySpecStore


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / stubs
# ─────────────────────────────────────────────────────────────────────────────


def _make_spec(strategy_id: str, version: str = "1.0.0") -> StrategySpec:
    """Return a minimal valid :class:`StrategySpec` for runner tests."""
    return StrategySpec(
        strategy_id=strategy_id,
        version=version,
        graph=StrategyGraph(
            nodes=[StrategyNode(node_id="n1", node_type="retrieve")],
            edges=[],
            entry_node="n1",
            terminal_nodes=["n1"],
        ),
    )


def _make_run_result(
    *,
    strategy_id: str,
    trace_id: str,
    duration_ms: float = 1500.0,
    success: bool = True,
) -> StrategyRunResult:
    """Build a :class:`StrategyRunResult` with the given trace id."""
    state = StrategyRunState(
        run_id=f"run-{trace_id}",
        trace_id=trace_id,
        query="q",
    )
    return StrategyRunResult(
        success=success,
        state=state,
        strategy_id=strategy_id,
        total_duration_ms=duration_ms,
    )


class _FakeStrategyRunner:
    """Stub :class:`StrategyRunner` whose ``run`` returns canned results.

    ``handler`` is invoked as ``handler(spec, query, call_index)`` and
    must return a :class:`StrategyRunResult`. To simulate a raise,
    ``handler`` may itself raise.

    The factory shares ``call_index`` across runner instances so tests
    can branch on the global case index, not the per-instance one.
    """

    def __init__(self, handler, counter: list[int]):
        self._handler = handler
        self._counter = counter

    async def run(self, *, spec, context=None, query=""):
        idx = self._counter[0]
        self._counter[0] += 1
        return self._handler(spec, query, idx)


def _make_runner_factory(handler):
    """Return a zero-arg factory producing :class:`_FakeStrategyRunner`.

    All runners produced share a single counter so ``handler`` sees the
    global call index across cases.
    """
    counter = [0]

    def _factory():
        return _FakeStrategyRunner(handler, counter)

    return _factory


class _FakeEvalRunner:
    """Stub evaluation runner exposing ``load_dataset`` + ``score``."""

    def __init__(self, datasets, score_fn):
        self._datasets = datasets
        self._score_fn = score_fn
        self.score_calls = 0

    async def load_dataset(self, dataset_id):
        return list(self._datasets.get(dataset_id, []))

    async def score(self, case, run_result):
        self.score_calls += 1
        return self._score_fn(case, run_result)


class _FakeRuntimeProfiler:
    """Stub :class:`RuntimeProfiler` capturing each ``run_test`` call."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def run_test(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id=f"prof-{len(self.calls)}")


class _FakeResourceCollector:
    """Stub :class:`ResourceSnapshotCollector` with overridable hooks."""

    def __init__(
        self,
        *,
        safe: bool = True,
        reasons: Optional[list[str]] = None,
    ):
        self._safe = safe
        self._reasons = list(reasons or [])
        self.is_safe_calls = 0
        self.capture_calls = 0
        self.safe_responder = None  # optional callable: int -> (bool, list[str])

    async def is_safe_to_run(self, limits):
        self.is_safe_calls += 1
        if self.safe_responder is not None:
            return self.safe_responder(self.is_safe_calls)
        return self._safe, list(self._reasons)

    async def capture(self):
        self.capture_calls += 1
        return SimpleNamespace(id=f"snap-{self.capture_calls}")


def _make_case(case_id: str, query: str = "What is X?"):
    return SimpleNamespace(id=case_id, query=query)


def _score(*, composite: float, latency: float = 1500.0, metrics=None):
    return SimpleNamespace(
        composite_score=composite,
        latency_ms=latency,
        metrics=dict(metrics or {}),
    )


async def _seed_queued_job(
    store: InMemoryExperimentJobStore,
    *,
    job_id: str = "job-1",
    strategy_ids: Optional[list[str]] = None,
    datasets: Optional[list[str]] = None,
    mode: str = "regression",
) -> StrategyExperimentJob:
    """Create a fresh job and move it into ``QUEUED``."""
    job = StrategyExperimentJob(
        id=job_id,
        tenant="recallhub",
        mode=mode,  # type: ignore[arg-type]
        datasets=datasets if datasets is not None else ["ds-a"],
        strategy_ids=strategy_ids if strategy_ids is not None else ["strat-a"],
        status=JobStatus.SCHEDULED,
        created_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )
    await store.create(job)
    await store.update_status(job_id, JobStatus.QUEUED)
    return job


def _make_runner(
    *,
    job_store: InMemoryExperimentJobStore,
    strategy_runner_factory,
    eval_runner: _FakeEvalRunner,
    spec_resolver=None,
    resource_collector: Optional[_FakeResourceCollector] = None,
    runtime_profiler: Optional[_FakeRuntimeProfiler] = None,
    progress_throttle_ms: int = 0,
) -> StrategyExperimentRunner:
    return StrategyExperimentRunner(
        job_store=job_store,
        run_trace_store=SimpleNamespace(),
        runtime_profiler=runtime_profiler or _FakeRuntimeProfiler(),
        resource_snapshot_collector=resource_collector or _FakeResourceCollector(),
        evaluation_runner=eval_runner,
        strategy_runner_factory=strategy_runner_factory,
        spec_resolver=spec_resolver,
        progress_throttle_ms=progress_throttle_ms,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_single_strategy_completes() -> None:
    """One strategy × 3-case dataset transitions QUEUED → RUNNING → COMPLETED."""
    store = InMemoryExperimentJobStore()
    job = await _seed_queued_job(
        store, strategy_ids=["strat-a"], datasets=["ds-a"]
    )

    spec_a = _make_spec("strat-a")

    async def resolve(sid):
        assert sid == "strat-a"
        return spec_a

    cases = [_make_case(f"c{i}") for i in range(3)]
    eval_runner = _FakeEvalRunner(
        {"ds-a": cases},
        score_fn=lambda case, _r: _score(composite=0.7),
    )

    def handler(spec, query, idx):
        return _make_run_result(
            strategy_id=spec.strategy_id, trace_id=f"trace-{idx}"
        )

    runner = _make_runner(
        job_store=store,
        strategy_runner_factory=_make_runner_factory(handler),
        eval_runner=eval_runner,
        spec_resolver=resolve,
    )

    finished = await runner.run_job(job.id)

    assert finished.status == JobStatus.COMPLETED
    assert finished.started_at is not None
    assert finished.completed_at is not None
    assert finished.result_summary is not None
    summary = finished.result_summary
    assert summary["total_runs"] == 3
    assert summary["completed_runs"] == 3
    assert summary["failed_runs"] == 0
    assert summary["winning_strategy"] == "strat-a"
    assert summary["winning_score"] == pytest.approx(0.7)
    assert len(summary["results"]) == 1
    res = summary["results"][0]
    assert res["candidate_strategy_id"] == "strat-a"
    assert res["case_count"] == 3
    assert sorted(finished.trace_ids) == ["trace-0", "trace-1", "trace-2"]
    # The runner's ExperimentResult also retains them locally.
    assert sorted(res["trace_ids"]) == ["trace-0", "trace-1", "trace-2"]
    # One resource snapshot captured per candidate.
    assert summary["resource_violations"] == []


@pytest.mark.asyncio
async def test_multi_strategy_winner_picks_highest_mean() -> None:
    """Two strategies on one dataset → the higher mean wins."""
    store = InMemoryExperimentJobStore()
    job = await _seed_queued_job(
        store, strategy_ids=["strat-low", "strat-high"], datasets=["ds-a"]
    )

    specs = {
        "strat-low": _make_spec("strat-low"),
        "strat-high": _make_spec("strat-high"),
    }

    async def resolve(sid):
        return specs[sid]

    cases = [_make_case(f"c{i}") for i in range(2)]
    score_lookup = {"strat-low": 0.3, "strat-high": 0.9}

    eval_runner = _FakeEvalRunner(
        {"ds-a": cases},
        score_fn=lambda case, run_result: _score(
            composite=score_lookup[run_result.strategy_id]
        ),
    )

    def handler(spec, query, idx):
        return _make_run_result(
            strategy_id=spec.strategy_id, trace_id=f"{spec.strategy_id}-{idx}"
        )

    runner = _make_runner(
        job_store=store,
        strategy_runner_factory=_make_runner_factory(handler),
        eval_runner=eval_runner,
        spec_resolver=resolve,
    )

    finished = await runner.run_job(job.id)
    assert finished.status == JobStatus.COMPLETED
    summary = finished.result_summary
    assert summary["winning_strategy"] == "strat-high"
    assert summary["winning_score"] == pytest.approx(0.9)
    assert len(summary["results"]) == 2


@pytest.mark.asyncio
async def test_resource_pressure_pauses_job() -> None:
    """Unsafe pre-check → job ends in PAUSED with violations recorded."""
    store = InMemoryExperimentJobStore()
    job = await _seed_queued_job(store, strategy_ids=["strat-a"])

    spec_a = _make_spec("strat-a")

    async def resolve(sid):
        return spec_a

    cases = [_make_case("c1")]
    eval_runner = _FakeEvalRunner(
        {"ds-a": cases},
        score_fn=lambda case, _r: _score(composite=1.0),
    )

    def handler(spec, query, idx):
        raise AssertionError("strategy_runner should not run when paused")

    collector = _FakeResourceCollector(
        safe=False, reasons=["CPU 99% exceeds limit 85%"]
    )
    runner = _make_runner(
        job_store=store,
        strategy_runner_factory=_make_runner_factory(handler),
        eval_runner=eval_runner,
        spec_resolver=resolve,
        resource_collector=collector,
    )

    finished = await runner.run_job(job.id)
    assert finished.status == JobStatus.PAUSED
    # We do not finalize the result_summary on pause — but the job's
    # transient summary state should still mention nothing succeeded.
    # The runner does record the reasons in the in-memory summary; we
    # just confirm no traces and the collector saw the pre-check.
    assert collector.is_safe_calls == 1
    assert finished.trace_ids == []


@pytest.mark.asyncio
async def test_failure_per_case_does_not_abort_job() -> None:
    """Strategy raise on case 2 → that case fails; job still COMPLETED."""
    store = InMemoryExperimentJobStore()
    job = await _seed_queued_job(store, strategy_ids=["strat-a"])

    spec_a = _make_spec("strat-a")

    async def resolve(sid):
        return spec_a

    cases = [_make_case(f"c{i}") for i in range(3)]

    def handler(spec, query, idx):
        if idx == 1:
            raise RuntimeError("simulated transient failure")
        return _make_run_result(
            strategy_id=spec.strategy_id, trace_id=f"trace-{idx}"
        )

    eval_runner = _FakeEvalRunner(
        {"ds-a": cases},
        score_fn=lambda case, _r: _score(composite=0.5),
    )

    runner = _make_runner(
        job_store=store,
        strategy_runner_factory=_make_runner_factory(handler),
        eval_runner=eval_runner,
        spec_resolver=resolve,
    )

    finished = await runner.run_job(job.id)
    assert finished.status == JobStatus.COMPLETED
    summary = finished.result_summary
    assert summary["total_runs"] == 3
    assert summary["completed_runs"] == 2
    assert summary["failed_runs"] == 1
    res = summary["results"][0]
    assert res["success_count"] == 2
    assert res["failure_count"] == 1
    assert res["fatal_failure_count"] == 0
    # Only successful cases produced trace ids.
    assert sorted(res["trace_ids"]) == ["trace-0", "trace-2"]


@pytest.mark.asyncio
async def test_evaluation_runner_persistent_failure_counts_as_fatal() -> None:
    """Score raising on every case → no successes, summary completed but failed_runs all."""
    store = InMemoryExperimentJobStore()
    job = await _seed_queued_job(store, strategy_ids=["strat-a"])

    spec_a = _make_spec("strat-a")

    async def resolve(sid):
        return spec_a

    cases = [_make_case(f"c{i}") for i in range(3)]

    def handler(spec, query, idx):
        return _make_run_result(
            strategy_id=spec.strategy_id, trace_id=f"trace-{idx}"
        )

    def boom(case, run_result):
        raise RuntimeError("evaluator broken")

    eval_runner = _FakeEvalRunner({"ds-a": cases}, score_fn=boom)

    runner = _make_runner(
        job_store=store,
        strategy_runner_factory=_make_runner_factory(handler),
        eval_runner=eval_runner,
        spec_resolver=resolve,
    )

    finished = await runner.run_job(job.id)
    # Per-case score failure is non-fatal: the job still completes,
    # but every case is recorded as a fatal_failure.
    assert finished.status == JobStatus.COMPLETED
    summary = finished.result_summary
    assert summary["completed_runs"] == 0
    assert summary["failed_runs"] == 3
    assert summary["winning_strategy"] is None
    res = summary["results"][0]
    assert res["fatal_failure_count"] == 3
    assert res["success_count"] == 0


@pytest.mark.asyncio
async def test_load_dataset_failure_marks_job_failed() -> None:
    """Catastrophic load_dataset raise → job transitions to FAILED."""
    store = InMemoryExperimentJobStore()
    job = await _seed_queued_job(store, strategy_ids=["strat-a"])

    spec_a = _make_spec("strat-a")

    async def resolve(sid):
        return spec_a

    class _BadEval:
        async def load_dataset(self, dataset_id):
            raise RuntimeError("dataset gone")

        async def score(self, case, run_result):  # pragma: no cover
            raise AssertionError("not reached")

    runner = _make_runner(
        job_store=store,
        strategy_runner_factory=_make_runner_factory(
            lambda spec, q, i: _make_run_result(
                strategy_id=spec.strategy_id, trace_id="t"
            )
        ),
        eval_runner=_BadEval(),
        spec_resolver=resolve,
    )

    finished = await runner.run_job(job.id)
    assert finished.status == JobStatus.FAILED
    assert finished.error and "dataset gone" in finished.error


@pytest.mark.asyncio
async def test_external_cancellation_short_circuits_run() -> None:
    """External CANCELLED status mid-run → runner exits to CANCELLED."""
    store = InMemoryExperimentJobStore()
    job = await _seed_queued_job(store, strategy_ids=["strat-a"])
    spec_a = _make_spec("strat-a")

    async def resolve(sid):
        return spec_a

    cases = [_make_case(f"c{i}") for i in range(5)]

    async def cancel_after_first(case, run_result):
        # Cancel the job after the first case has been scored.
        await store.update_status(job.id, JobStatus.CANCELLED)
        return _score(composite=0.8)

    eval_runner = _FakeEvalRunner(
        {"ds-a": cases},
        score_fn=lambda case, run_result: _score(composite=0.8),
    )

    # Override score with one that mutates store state on first call.
    call_state = {"count": 0}

    async def score_with_cancel(case, run_result):
        call_state["count"] += 1
        if call_state["count"] == 1:
            await store.update_status(job.id, JobStatus.CANCELLED)
        return _score(composite=0.8)

    eval_runner.score = score_with_cancel  # type: ignore[assignment]

    def handler(spec, query, idx):
        return _make_run_result(
            strategy_id=spec.strategy_id, trace_id=f"trace-{idx}"
        )

    runner = _make_runner(
        job_store=store,
        strategy_runner_factory=_make_runner_factory(handler),
        eval_runner=eval_runner,
        spec_resolver=resolve,
    )

    finished = await runner.run_job(job.id)
    assert finished.status == JobStatus.CANCELLED
    # Fewer than the full 5 traces should have been appended.
    assert len(finished.trace_ids) < 5


@pytest.mark.asyncio
async def test_progress_writes_are_throttled() -> None:
    """100 cases in tight loop → fewer than 50 update_progress writes."""
    store = InMemoryExperimentJobStore()
    job = await _seed_queued_job(store, strategy_ids=["strat-a"])
    spec_a = _make_spec("strat-a")

    async def resolve(sid):
        return spec_a

    cases = [_make_case(f"c{i}") for i in range(100)]
    eval_runner = _FakeEvalRunner(
        {"ds-a": cases},
        score_fn=lambda case, _r: _score(composite=0.5),
    )

    def handler(spec, query, idx):
        return _make_run_result(
            strategy_id=spec.strategy_id, trace_id=f"trace-{idx}"
        )

    progress_writes = {"count": 0}
    real_update_progress = store.update_progress

    async def counting_update_progress(job_id, progress):
        progress_writes["count"] += 1
        return await real_update_progress(job_id, progress)

    store.update_progress = counting_update_progress  # type: ignore[assignment]

    runner = _make_runner(
        job_store=store,
        strategy_runner_factory=_make_runner_factory(handler),
        eval_runner=eval_runner,
        spec_resolver=resolve,
        progress_throttle_ms=500,
    )
    finished = await runner.run_job(job.id)
    assert finished.status == JobStatus.COMPLETED
    # The loop is tight enough that throttling should keep this very low —
    # in practice it is typically 1 write. The brief allows up to <50.
    assert progress_writes["count"] < 50, (
        f"progress was written {progress_writes['count']} times — throttle ineffective"
    )


@pytest.mark.asyncio
async def test_invalid_state_raises_when_not_queued() -> None:
    """A job in SCHEDULED state cannot be run — expect InvalidJobStateError."""
    store = InMemoryExperimentJobStore()
    job = StrategyExperimentJob(
        id="job-x",
        tenant="recallhub",
        mode="regression",
        datasets=["ds-a"],
        strategy_ids=["strat-a"],
        status=JobStatus.SCHEDULED,
    )
    await store.create(job)

    runner = _make_runner(
        job_store=store,
        strategy_runner_factory=_make_runner_factory(
            lambda spec, q, i: _make_run_result(
                strategy_id=spec.strategy_id, trace_id="t"
            )
        ),
        eval_runner=_FakeEvalRunner({}, score_fn=lambda c, r: _score(composite=1.0)),
        spec_resolver=None,
    )
    with pytest.raises(InvalidJobStateError) as excinfo:
        await runner.run_job("job-x")
    assert excinfo.value.expected_status == JobStatus.QUEUED
    assert excinfo.value.actual_status == JobStatus.SCHEDULED


@pytest.mark.asyncio
async def test_runtime_profile_captured_once_per_candidate() -> None:
    """Runtime profile is run exactly once per candidate (not per case)."""
    store = InMemoryExperimentJobStore()
    job = await _seed_queued_job(
        store, strategy_ids=["strat-a", "strat-b"], datasets=["ds-a"]
    )
    specs = {sid: _make_spec(sid) for sid in ("strat-a", "strat-b")}
    # Wire a synth role so the profiler is actually invoked.
    for spec in specs.values():
        spec.model_roles = {"synth_model": "ollama/llama3.1"}

    async def resolve(sid):
        return specs[sid]

    cases = [_make_case(f"c{i}") for i in range(4)]
    eval_runner = _FakeEvalRunner(
        {"ds-a": cases},
        score_fn=lambda c, r: _score(composite=0.5),
    )

    def handler(spec, query, idx):
        return _make_run_result(
            strategy_id=spec.strategy_id, trace_id=f"{spec.strategy_id}-{idx}"
        )

    profiler = _FakeRuntimeProfiler()
    runner = _make_runner(
        job_store=store,
        strategy_runner_factory=_make_runner_factory(handler),
        eval_runner=eval_runner,
        spec_resolver=resolve,
        runtime_profiler=profiler,
    )
    finished = await runner.run_job(job.id)
    assert finished.status == JobStatus.COMPLETED
    # Two candidates → exactly two profiler invocations even though
    # there are 4 cases per candidate.
    assert len(profiler.calls) == 2
    for call in profiler.calls:
        assert call["test_name"] == "evidence_synthesis"
        assert call["provider"] == "ollama"
        assert call["model"] == "llama3.1"
    summary = finished.result_summary
    # Each ExperimentResult carries one runtime_profile_id.
    assert all(len(r["runtime_profile_ids"]) == 1 for r in summary["results"])
    assert all(len(r["resource_snapshot_ids"]) == 1 for r in summary["results"])


# ─────────────────────────────────────────────────────────────────────────────
# P6 / Task 75 integration: ModeRegistry wiring + typed resource_limits
# ─────────────────────────────────────────────────────────────────────────────


import os  # noqa: E402 - kept local to the P6 integration block below

from backend.agent.strategy.spec_loader import load_spec_from_yaml  # noqa: E402

_FAST_EVIDENCE_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "config",
        "strategy_specs",
        "fast_evidence_v1.yaml",
    )
)


def _spec_to_snapshot(spec: StrategySpec) -> dict[str, Any]:
    return {
        "spec_data": spec.model_dump(mode="json"),
        "version": spec.version,
        "version_counter": 1,
        "status": spec.status,
        "spec_hash": spec.spec_hash or "",
        "created_at": datetime.utcnow(),
    }


@pytest.mark.asyncio
async def test_runner_uses_mode_registry_when_mode_is_grid() -> None:
    """With ``mode='grid'`` and a spec_store, the runner dispatches via ModeRegistry."""
    base_spec = load_spec_from_yaml(_FAST_EVIDENCE_PATH)
    spec_store = InMemorySpecStore()
    await spec_store.upsert(_spec_to_snapshot(base_spec))

    store = InMemoryExperimentJobStore()
    job = StrategyExperimentJob(
        id="job-grid",
        tenant=base_spec.tenant_scope,
        mode="grid",
        datasets=["ds-a"],
        strategy_ids=[base_spec.strategy_id],
        status=JobStatus.SCHEDULED,
    )
    await store.create(job)
    await store.update_status(job.id, JobStatus.QUEUED)

    cases = [_make_case(f"c{i}") for i in range(2)]
    eval_runner = _FakeEvalRunner(
        {"ds-a": cases},
        score_fn=lambda case, _r: _score(composite=0.6),
    )

    seen_strategy_ids: list[str] = []

    def handler(spec, query, idx):
        seen_strategy_ids.append(spec.strategy_id)
        return _make_run_result(
            strategy_id=spec.strategy_id, trace_id=f"trace-{idx}"
        )

    runner = StrategyExperimentRunner(
        job_store=store,
        run_trace_store=SimpleNamespace(),
        runtime_profiler=_FakeRuntimeProfiler(),
        resource_snapshot_collector=_FakeResourceCollector(),
        evaluation_runner=eval_runner,
        strategy_runner_factory=_make_runner_factory(handler),
        spec_store=spec_store,
        progress_throttle_ms=0,
    )

    finished = await runner.run_job(job.id)
    assert finished.status == JobStatus.COMPLETED
    summary = finished.result_summary
    # GridMode applied a Cartesian product → multiple grid candidates.
    assert len(summary["results"]) >= 2
    for result in summary["results"]:
        assert result["candidate_strategy_id"].startswith(
            f"{base_spec.strategy_id}__grid_"
        )
    # Every executed strategy id must be one of the grid candidates.
    grid_ids = {r["candidate_strategy_id"] for r in summary["results"]}
    assert set(seen_strategy_ids).issubset(grid_ids)


@pytest.mark.asyncio
async def test_runner_uses_typed_resource_limits_field() -> None:
    """Typed ``job.resource_limits`` is consulted instead of result_summary."""
    store = InMemoryExperimentJobStore()
    typed_limits = ResourceLimits(
        max_cpu_utilization_pct=42, max_ram_usage_pct=42
    )
    job = StrategyExperimentJob(
        id="job-limits",
        tenant="recallhub",
        mode="regression",
        datasets=["ds-a"],
        strategy_ids=["strat-a"],
        status=JobStatus.SCHEDULED,
        # Legacy shim with very different values that must NOT win.
        result_summary={
            "resource_limits": {
                "max_cpu_utilization_pct": 99,
                "max_ram_usage_pct": 99,
            }
        },
        resource_limits=typed_limits,
    )
    await store.create(job)
    await store.update_status(job.id, JobStatus.QUEUED)

    spec_a = _make_spec("strat-a")

    async def resolve(sid):
        return spec_a

    cases = [_make_case("c0")]
    eval_runner = _FakeEvalRunner(
        {"ds-a": cases},
        score_fn=lambda case, _r: _score(composite=0.5),
    )

    captured_limits: list[ResourceLimits] = []

    class _CapturingCollector(_FakeResourceCollector):
        async def is_safe_to_run(self, limits):
            captured_limits.append(limits)
            return await super().is_safe_to_run(limits)

    runner = _make_runner(
        job_store=store,
        strategy_runner_factory=_make_runner_factory(
            lambda spec, q, i: _make_run_result(
                strategy_id=spec.strategy_id, trace_id=f"trace-{i}"
            )
        ),
        eval_runner=eval_runner,
        spec_resolver=resolve,
        resource_collector=_CapturingCollector(),
    )

    finished = await runner.run_job(job.id)
    assert finished.status == JobStatus.COMPLETED
    assert captured_limits, "is_safe_to_run was never called"
    # Typed field wins over the result_summary shim.
    assert captured_limits[0].max_cpu_utilization_pct == 42
    assert captured_limits[0].max_ram_usage_pct == 42


# ─────────────────────────────────────────────────────────────────────────────
# P7 / Task 76 integration: BanditMode arm-by-arm allocation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runner_uses_bandit_allocation() -> None:
    """With ``mode='bandit'`` the runner asks ``next_arm`` per case.

    Wires a real :class:`BanditMode` whose internal :class:`GridMode`
    is replaced by a stub returning two canned arms, then drives a
    single-dataset / four-case experiment. Asserts:

    * The runner pulled in a deterministic order matching
      :meth:`BanditMode.next_arm` outputs.
    * Every successful pull recorded a reward into the bandit state
      store (composite_score, not raw latency).
    * The runner ends in ``COMPLETED`` with one
      :class:`ExperimentResult` per ``(arm, dataset)`` pulled.
    """
    from backend.agent.strategy.bandit_state_store import (
        InMemoryBanditStateStore,
    )
    from backend.agent.strategy.exploration_modes import BanditMode, GridMode
    from backend.agent.strategy.spec_store import InMemorySpecStore

    arms = [_make_spec("arm-a"), _make_spec("arm-b")]

    class _StubGrid(GridMode):
        async def materialize_candidates(
            self, job, *, spec_store, candidate_generator_factory=None
        ):
            return list(arms)

    state = InMemoryBanditStateStore()
    bandit = BanditMode(
        grid_mode=_StubGrid(),
        state_store=state,
        warmup_pulls_per_arm=1,
    )

    store = InMemoryExperimentJobStore()
    job = StrategyExperimentJob(
        id="job-bandit",
        tenant="recallhub",
        mode="bandit",
        datasets=["ds-a"],
        strategy_ids=["arm-a"],
        status=JobStatus.SCHEDULED,
    )
    await store.create(job)
    await store.update_status(job.id, JobStatus.QUEUED)

    cases = [_make_case(f"c{i}") for i in range(4)]

    # Reward varies by arm so post-warmup UCB1 sees a clear winner.
    def _score_fn(case, run_result):
        sid = getattr(run_result, "strategy_id", "")
        return _score(composite=0.9 if sid == "arm-a" else 0.1)

    eval_runner = _FakeEvalRunner({"ds-a": cases}, score_fn=_score_fn)

    seen: list[str] = []

    def handler(spec, query, idx):
        seen.append(spec.strategy_id)
        return _make_run_result(
            strategy_id=spec.strategy_id, trace_id=f"trace-{idx}"
        )

    # Force the runner down the registry path by passing a spec_store,
    # but inject our :class:`BanditMode` via an explicit ``mode_dispatcher``
    # that returns the same arms the bandit's GridMode stub would —
    # the runner detects ``BanditMode`` via the registry route, so we
    # register the stub bandit directly and drive through that.
    from backend.agent.strategy.exploration_modes import ModeRegistry

    # Snapshot + override the registered bandit factory so the runner
    # sees *our* preconfigured instance (in lieu of plumbing a dedicated
    # factory parameter).
    original_bandit_factory = ModeRegistry._registry["bandit"]
    ModeRegistry.register("bandit", lambda **_: bandit)
    try:
        runner = StrategyExperimentRunner(
            job_store=store,
            run_trace_store=SimpleNamespace(),
            runtime_profiler=_FakeRuntimeProfiler(),
            resource_snapshot_collector=_FakeResourceCollector(),
            evaluation_runner=eval_runner,
            strategy_runner_factory=_make_runner_factory(handler),
            spec_store=InMemorySpecStore(),  # unused: stub bypasses it
            progress_throttle_ms=0,
        )
        finished = await runner.run_job(job.id)
    finally:
        ModeRegistry.register("bandit", original_bandit_factory)

    assert finished.status == JobStatus.COMPLETED
    summary = finished.result_summary
    assert summary is not None

    # Total pulls equal the number of cases (4), so the bandit was
    # asked once per case.
    assert len(seen) == 4
    # Bandit deterministic warmup: a then b on the first two pulls.
    assert seen[:2] == ["arm-a", "arm-b"]
    # The higher-reward arm dominates the post-warmup pulls.
    assert seen[2:].count("arm-a") >= seen[2:].count("arm-b")

    # State store accumulated rewards from every successful pull.
    rows = sorted(
        await state.list_arms(job.experiment_id), key=lambda r: r.arm_id
    )
    assert [r.arm_id for r in rows] == ["arm-a", "arm-b"]
    assert sum(r.pull_count for r in rows) == 4
    arm_a_row = next(r for r in rows if r.arm_id == "arm-a")
    arm_b_row = next(r for r in rows if r.arm_id == "arm-b")
    # Reward = composite_score (0.9 / 0.1) — NOT latency.
    assert arm_a_row.mean_reward == pytest.approx(0.9)
    assert arm_b_row.mean_reward == pytest.approx(0.1)

    # One ExperimentResult per (arm, dataset) actually pulled.
    by_arm = {r["candidate_strategy_id"]: r for r in summary["results"]}
    assert set(by_arm) == {"arm-a", "arm-b"}
    assert by_arm["arm-a"]["composite_score_mean"] == pytest.approx(0.9)
    assert by_arm["arm-b"]["composite_score_mean"] == pytest.approx(0.1)
    # Total case_count across results equals total pulls.
    assert sum(r["case_count"] for r in summary["results"]) == 4
    # Winner is arm-a (mean_reward 0.9 > 0.1).
    assert summary["winning_strategy"] == "arm-a"
