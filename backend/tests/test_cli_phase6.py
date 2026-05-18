"""Phase 6 CLI command tests for ``quellexctl``.

Covers the new ``schedule``, ``experiment list/get``, and ``profiler``
sub-commands plus the ``experiment run --mode`` dispatch added in
Task 80. Tests rely on the dependency-injection hooks defined in
:mod:`backend.cli.phase6_commands` so no MongoDB connection is needed.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from backend.cli.main import HAS_TYPER

pytestmark = pytest.mark.skipif(not HAS_TYPER, reason="typer not installed")

if HAS_TYPER:
    from typer.testing import CliRunner

    from backend.cli.main import app
    import backend.cli.strategy_commands  # noqa: F401 - register Phase 4
    import backend.cli.eval_commands  # noqa: F401 - register Phase 4
    import backend.cli.phase6_commands as phase6  # noqa: E402

    from backend.agent.strategy.experiment_job_models import (
        JobProgress,
        JobStatus,
        StrategyExperimentJob,
    )
    from backend.agent.strategy.experiment_job_store import (
        InMemoryExperimentJobStore,
    )
    from backend.agent.strategy.scheduler_store import InMemorySchedulerStore
    from backend.scheduler.models import StrategySchedule
    from backend.scheduler.scheduler_daemon import ScheduleNotFoundError

    runner = CliRunner(mix_stderr=False)


@pytest.fixture(autouse=True)
def _reset_hooks():
    """Clear hooks before and after each test."""
    phase6._clear_test_hooks()
    yield
    phase6._clear_test_hooks()


# ─── Helpers ────────────────────────────────────────────────────────────────


def _install_store_with_schedule(name: str = "nightly") -> InMemorySchedulerStore:
    store = InMemorySchedulerStore()
    seeded = StrategySchedule(
        name=name,
        candidate_strategy_ids=["a", "b"],
        dataset_id="ds-1",
    )
    # Persist directly (synchronous on the in-memory backend via asyncio).
    import asyncio

    asyncio.run(store.upsert(seeded))
    phase6._install_test_hook("scheduler_store", store)
    return store


# ─── 1. schedule list ───────────────────────────────────────────────────────


def test_schedule_list_returns_existing_schedules():
    """``schedule list --format json`` returns the seeded schedule."""
    _install_store_with_schedule("nightly")
    result = runner.invoke(app, ["schedule", "list", "--format", "json"])
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    assert any(s.get("name") == "nightly" for s in parsed)


# ─── 2. schedule add ────────────────────────────────────────────────────────


def test_schedule_add_then_list_shows_new_doc():
    """``schedule add`` persists a new schedule visible in ``list``."""
    store = InMemorySchedulerStore()
    phase6._install_test_hook("scheduler_store", store)

    add_result = runner.invoke(
        app,
        [
            "schedule",
            "add",
            "--name",
            "weekend",
            "--mode",
            "regression",
            "--tenant",
            "recallhub",
            "--datasets",
            "ds-a,ds-b",
            "--strategies",
            "strat-1,strat-2",
            "--cron",
            "0 22 * * *",
            "--window",
            "22:00-06:00",
            "--timezone",
            "UTC",
            "--format",
            "json",
        ],
    )
    assert add_result.exit_code == 0, add_result.stdout + (add_result.stderr or "")
    added = json.loads(add_result.stdout)
    assert added["name"] == "weekend"
    assert added["allowed_window_start"] == "22:00"
    assert added["allowed_window_end"] == "06:00"

    list_result = runner.invoke(app, ["schedule", "list", "--format", "json"])
    assert list_result.exit_code == 0
    names = {s.get("name") for s in json.loads(list_result.stdout)}
    assert "weekend" in names


# ─── 3. schedule run-now ────────────────────────────────────────────────────


def test_schedule_run_now_invokes_daemon():
    """``schedule run-now`` calls the daemon's ``run_now``."""
    store = _install_store_with_schedule("nightly")
    seeded = next(iter(store._schedules.values()))  # noqa: SLF001 - test only

    invocations: list[str] = []

    class _FakeDaemon:
        async def run_now(self, schedule_id: str) -> str:
            invocations.append(schedule_id)
            return "trace-xyz"

    phase6._install_test_hook("scheduler_daemon", _FakeDaemon())

    result = runner.invoke(
        app, ["schedule", "run-now", "nightly", "--format", "json"]
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    payload = json.loads(result.stdout)
    assert payload["trace_id"] == "trace-xyz"
    assert invocations == [seeded.id]


def test_schedule_run_now_unknown_name_errors():
    """Non-existent schedule name → non-zero exit + error message."""
    phase6._install_test_hook("scheduler_store", InMemorySchedulerStore())

    class _FakeDaemon:
        async def run_now(self, schedule_id: str) -> str:  # pragma: no cover
            raise AssertionError("should not be called")

    phase6._install_test_hook("scheduler_daemon", _FakeDaemon())

    result = runner.invoke(app, ["schedule", "run-now", "ghost"])
    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "ghost" in combined or "not found" in combined.lower()


# ─── 4. experiment run --dry-run ────────────────────────────────────────────


def test_experiment_run_dry_run_prints_candidates_without_running():
    """``--dry-run`` exits 0 and emits a candidate preview."""
    result = runner.invoke(
        app,
        [
            "experiment",
            "run",
            "--mode",
            "grid",
            "--strategies",
            "a,b",
            "--dataset",
            "d1",
            "--dry-run",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    parsed = json.loads(result.stdout)
    assert parsed["mode"] == "grid"
    assert parsed["candidates"] == ["a", "b"]
    assert parsed["datasets"] == ["d1"]
    assert parsed["dry_run"] is True


# ─── 5. experiment run --watch ──────────────────────────────────────────────


def test_experiment_run_watch_streams_progress():
    """``--watch`` emits live progress lines and finishes successfully."""
    job_store = InMemoryExperimentJobStore()
    phase6._install_test_hook("experiment_job_store", job_store)

    class _FakeRunner:
        async def run_job(self, job_id: str):
            # Move job to RUNNING + bump progress so the watcher sees deltas.
            await job_store.update_status(job_id, JobStatus.RUNNING)
            await job_store.update_progress(
                job_id,
                JobProgress(total_runs=2, completed_runs=1, current_strategy="a"),
            )
            return await job_store.finalize(
                job_id,
                result_summary={"winner": "a"},
                completed_at=__import__("datetime").datetime.utcnow(),
            )

    phase6._install_test_hook("experiment_runner", _FakeRunner())

    result = runner.invoke(
        app,
        [
            "experiment",
            "run",
            "--mode",
            "grid",
            "--strategies",
            "a",
            "--dataset",
            "d1",
            "--watch",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    # The watcher writes carriage-return progress lines + a JSON payload.
    assert "[watch]" in result.stdout
    # Extract the JSON payload that follows the live progress stream.
    # Strategy: split on newlines, find the first line starting with '{',
    # and join from there.
    lines = result.stdout.splitlines()
    json_start = next(
        (i for i, ln in enumerate(lines) if ln.lstrip().startswith("{")), None
    )
    assert json_start is not None, f"no JSON payload in: {result.stdout!r}"
    payload = json.loads("\n".join(lines[json_start:]))
    assert payload["status"] in ("completed", JobStatus.COMPLETED.value)


# ─── 6. experiment list --status running ────────────────────────────────────


def test_experiment_list_filters_by_status():
    """``experiment list --status running`` returns only running jobs."""
    job_store = InMemoryExperimentJobStore()
    phase6._install_test_hook("experiment_job_store", job_store)

    import asyncio

    async def _seed():
        # Two jobs — one RUNNING, one COMPLETED.
        running = StrategyExperimentJob(
            tenant="recallhub", mode="grid", strategy_ids=["a"]
        )
        completed = StrategyExperimentJob(
            tenant="recallhub", mode="grid", strategy_ids=["b"]
        )
        await job_store.create(running)
        await job_store.create(completed)
        await job_store.update_status(running.id, JobStatus.QUEUED)
        await job_store.update_status(running.id, JobStatus.RUNNING)
        await job_store.update_status(completed.id, JobStatus.QUEUED)
        await job_store.update_status(completed.id, JobStatus.RUNNING)
        from datetime import datetime

        await job_store.finalize(
            completed.id, result_summary={}, completed_at=datetime.utcnow()
        )
        return running.id

    running_id = asyncio.run(_seed())

    result = runner.invoke(
        app,
        ["experiment", "list", "--status", "running", "--format", "json"],
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    jobs = json.loads(result.stdout)
    assert len(jobs) == 1
    assert jobs[0]["id"] == running_id


# ─── 7. experiment get ──────────────────────────────────────────────────────


def test_experiment_get_shows_summary():
    """``experiment get <job_id>`` returns the full job document."""
    job_store = InMemoryExperimentJobStore()
    phase6._install_test_hook("experiment_job_store", job_store)

    import asyncio

    async def _seed():
        job = StrategyExperimentJob(
            tenant="recallhub", mode="smoke", strategy_ids=["x"]
        )
        await job_store.create(job)
        return job.id

    job_id = asyncio.run(_seed())

    result = runner.invoke(
        app, ["experiment", "get", job_id, "--format", "json"]
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    parsed = json.loads(result.stdout)
    assert parsed["id"] == job_id
    assert parsed["mode"] == "smoke"


# ─── 8. profiler model-test ─────────────────────────────────────────────────


def test_profiler_model_test_runs_with_mock_provider():
    """``profiler model-test`` calls the profiler and prints results."""
    captured: dict[str, Any] = {}

    class _FakeProfile:
        def model_dump(self, mode: str = "python"):
            return {
                "test_name": "short_rag",
                "context_tokens": 2048,
                "total_latency_ms": 1234,
                "tokens_per_second": 42.5,
                "success": True,
            }

    class _FakeProfiler:
        async def profile_model(self, **kwargs):
            captured.update(kwargs)
            return [_FakeProfile()]

    phase6._install_test_hook("runtime_profiler", _FakeProfiler())

    result = runner.invoke(
        app,
        [
            "profiler",
            "model-test",
            "--model",
            "gemma3:4b",
            "--contexts",
            "2k",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list) and parsed[0]["test_name"] == "short_rag"
    assert captured["model"] == "gemma3:4b"
    assert captured["contexts"] == [2048]


# ─── 9. profiler strategy-profile ───────────────────────────────────────────


def test_profiler_strategy_profile_invokes_hook():
    """``profiler strategy-profile`` uses the hook and emits an aggregate."""

    async def _hook(strategy_id: str, dataset_id: str):
        return {
            "strategy_id": strategy_id,
            "dataset_id": dataset_id,
            "profiles": 3,
            "avg_latency_ms": 1500.0,
            "avg_tps": 30.0,
        }

    phase6._install_test_hook("strategy_profile_func", _hook)

    result = runner.invoke(
        app,
        [
            "profiler",
            "strategy-profile",
            "--strategy",
            "fast_evidence_v1",
            "--dataset",
            "legal_core_v1",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    parsed = json.loads(result.stdout)
    assert parsed["strategy_id"] == "fast_evidence_v1"
    assert parsed["dataset_id"] == "legal_core_v1"
    assert parsed["profiles"] == 3


# ─── 10. invalid mode error ─────────────────────────────────────────────────


def test_experiment_run_invalid_mode_exits_nonzero():
    """Unknown ``--mode`` value emits an error and exits non-zero."""
    result = runner.invoke(
        app,
        [
            "experiment",
            "run",
            "--mode",
            "no-such-mode",
            "--strategies",
            "a,b",
            "--dataset",
            "d1",
        ],
    )
    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "no-such-mode" in combined or "Unknown" in combined


# ─── 11. schedule pause/resume round-trip ──────────────────────────────────


def test_schedule_pause_then_resume_updates_flag():
    """``schedule pause`` sets paused=True, ``resume`` clears it."""
    store = _install_store_with_schedule("nightly")

    pause_result = runner.invoke(
        app, ["schedule", "pause", "nightly", "--reason", "operator"]
    )
    assert pause_result.exit_code == 0
    schedules = list(store._schedules.values())  # noqa: SLF001
    assert schedules[0].paused is True
    assert schedules[0].paused_reason == "operator"

    resume_result = runner.invoke(app, ["schedule", "resume", "nightly"])
    assert resume_result.exit_code == 0
    schedules = list(store._schedules.values())  # noqa: SLF001
    assert schedules[0].paused is False


# ─── 12. CLI --help surfaces the new groups ─────────────────────────────────


def test_cli_help_lists_phase6_groups():
    """The top-level ``--help`` includes schedule and profiler groups."""
    # Ensure phase6 module registration ran.
    import backend.cli.phase6_commands  # noqa: F401

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "schedule" in combined
    assert "profiler" in combined
    assert "experiment" in combined


# --- 13. Task 83: schedule add persists Phase 6 dispatch fields ------------


def test_schedule_add_persists_new_fields():
    """``schedule add`` round-trips ``mode``/``tenant``/``profile_key``/``candidate_generator_id``.

    Adds a schedule via the CLI then fetches it back via ``schedule get``
    and asserts every Phase 6 dispatch field survived persistence.
    """
    store = InMemorySchedulerStore()
    phase6._install_test_hook("scheduler_store", store)

    add_result = runner.invoke(
        app,
        [
            "schedule",
            "add",
            "--name",
            "phase6-add",
            "--mode",
            "grid",
            "--tenant",
            "recallhub",
            "--datasets",
            "ds-a",
            "--strategies",
            "strat-1",
            "--cron",
            "0 22 * * *",
            "--timezone",
            "UTC",
            "--profile-key",
            "rag_test_law",
            "--candidate-generator",
            "grid-default",
            "--format",
            "json",
        ],
    )
    assert add_result.exit_code == 0, add_result.stdout + (add_result.stderr or "")
    added = json.loads(add_result.stdout)
    assert added["mode"] == "grid"
    assert added["tenant"] == "recallhub"
    assert added["profile_key"] == "rag_test_law"
    assert added["candidate_generator_id"] == "grid-default"

    get_result = runner.invoke(
        app, ["schedule", "get", "phase6-add", "--format", "json"]
    )
    assert get_result.exit_code == 0, get_result.stdout + (get_result.stderr or "")
    fetched = json.loads(get_result.stdout)
    assert fetched["mode"] == "grid"
    assert fetched["tenant"] == "recallhub"
    assert fetched["profile_key"] == "rag_test_law"
    assert fetched["candidate_generator_id"] == "grid-default"


def test_schedule_list_renders_new_fields():
    """``schedule list`` table output surfaces the persisted ``mode`` per row."""
    import asyncio

    store = InMemorySchedulerStore()
    phase6._install_test_hook("scheduler_store", store)

    asyncio.run(
        store.upsert(
            StrategySchedule(
                name="grid-nightly",
                mode="grid",
                tenant="recallhub",
                profile_key="rag_test_law",
                candidate_generator_id="grid-default",
            )
        )
    )
    asyncio.run(
        store.upsert(
            StrategySchedule(
                name="bandit-weekly",
                mode="bandit",
                tenant="quellex",
                profile_key="profiler",
                candidate_generator_id="bandit-ucb1",
            )
        )
    )

    # Default table output must surface the modes. Force a wider terminal
    # so Rich's auto-truncation does not chop ``bandit`` to ``ban…``.
    table_result = runner.invoke(
        app,
        ["schedule", "list"],
        env={"COLUMNS": "240", "TERM": "xterm-256color"},
    )
    assert table_result.exit_code == 0, table_result.stdout + (
        table_result.stderr or ""
    )
    assert "Mode" in table_result.stdout
    assert "grid" in table_result.stdout
    assert "bandit" in table_result.stdout

    # JSON output must also include both Phase 6 fields per row.
    json_result = runner.invoke(app, ["schedule", "list", "--format", "json"])
    assert json_result.exit_code == 0
    rows = json.loads(json_result.stdout)
    by_name = {row["name"]: row for row in rows}
    assert by_name["grid-nightly"]["mode"] == "grid"
    assert by_name["grid-nightly"]["candidate_generator_id"] == "grid-default"
    assert by_name["bandit-weekly"]["mode"] == "bandit"
    assert by_name["bandit-weekly"]["tenant"] == "quellex"
