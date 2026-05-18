"""Tests for :mod:`backend.agent.strategy.report_generator` (Task 62).

Exercises the nightly report generator against the in-process Mongo
fakes shared with the rest of the strategy test suite. Coverage:

* empty-window summary still writes Markdown and persists,
* regression detection compares against the prior-week baseline,
* top-failing-node ranking is stable and ordered by failure count,
* idempotency: re-running for the same date upserts the row,
* scheduler-health rollup reports paused / open-breaker / failure-threshold
  counts.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.agent.strategy.report_generator import (
    RUN_REPORT_COLLECTION_NAME,
    STRATEGY_RUN_COLLECTION_NAME,
    NightlyReportGenerator,
    NightlyReportSummary,
)
from backend.agent.strategy.scheduler_store import (
    SCHEDULE_COLLECTION_NAME,
    MongoSchedulerStore,
)
from backend.scheduler.models import StrategySchedule
from backend.scheduler.scheduler_daemon import SchedulerDaemon
from backend.tests.strategy._fakes import _FakeAsyncCollection, _FakeAsyncDB


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def fake_db() -> _FakeAsyncDB:
    return _FakeAsyncDB()


@pytest.fixture
def now() -> datetime:
    """A stable UTC anchor for the report window."""
    return datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    target = tmp_path / "reports"
    target.mkdir()
    return target


def _seed_run(
    db: _FakeAsyncDB,
    *,
    started_at: datetime,
    status: str = "success",
    duration_ms: float = 100.0,
    strategy_id: str = "strat-a",
    capability_id: str = "cap-a",
    node_outputs: list[dict[str, Any]] | None = None,
) -> None:
    coll: _FakeAsyncCollection = db[STRATEGY_RUN_COLLECTION_NAME]
    coll._docs.append(
        {
            "run_id": f"run-{len(coll._docs)}",
            "strategy_id": strategy_id,
            "capability_id": capability_id,
            "status": status,
            "started_at": started_at,
            "completed_at": started_at + timedelta(milliseconds=duration_ms),
            "duration_ms": duration_ms,
            "node_outputs": node_outputs or [],
        }
    )


def _seed_evaluation(
    db: _FakeAsyncDB,
    *,
    created_at: datetime,
    composite_score: float,
    strategy_id: str = "strat-a",
    capability_id: str = "cap-a",
) -> None:
    coll: _FakeAsyncCollection = db["evaluation_results"]
    coll._docs.append(
        {
            "test_case_id": f"tc-{len(coll._docs)}",
            "strategy_id": strategy_id,
            "capability_id": capability_id,
            "result": {"composite_score": composite_score},
            "created_at": created_at,
        }
    )


def _seed_scheduler_report(
    db: _FakeAsyncDB,
    *,
    completed_at: datetime,
    rankings: list[dict[str, Any]],
    capability_id: str = "cap-a",
) -> None:
    coll: _FakeAsyncCollection = db["scheduler_reports"]
    coll._docs.append(
        {
            "run_id": f"sched-{len(coll._docs)}",
            "completed_at": completed_at,
            "capability_id": capability_id,
            "candidate_rankings": rankings,
        }
    )


async def _seed_schedule(db: _FakeAsyncDB, schedule: StrategySchedule) -> None:
    store = MongoSchedulerStore(db)
    await store.upsert(schedule)


# ══════════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_empty_window_produces_zero_summary_and_markdown(
    fake_db: _FakeAsyncDB,
    now: datetime,
    output_dir: Path,
) -> None:
    generator = NightlyReportGenerator(fake_db, output_dir=output_dir)

    summary = await generator.generate(run_date=now)

    assert isinstance(summary, NightlyReportSummary)
    assert summary.total_runs == 0
    assert summary.success_count == 0
    assert summary.failure_count == 0
    assert summary.p50_latency_ms == 0.0
    assert summary.p95_latency_ms == 0.0
    assert summary.regressions == []
    assert summary.top_failing_nodes == []

    markdown_path = output_dir / f"{summary.report_date}.md"
    assert markdown_path.exists()
    content = markdown_path.read_text(encoding="utf-8")
    assert "Strategy OS Nightly Report" in content
    assert "## Summary" in content
    # Empty window still emits structured sections.
    assert "## Regressions" in content
    assert "## Top Failing Nodes" in content
    assert "## Scheduler Health" in content

    # Persisted row matches the in-memory summary.
    persisted = fake_db[RUN_REPORT_COLLECTION_NAME]._docs
    assert len(persisted) == 1
    assert persisted[0]["report_date"] == summary.report_date


@pytest.mark.asyncio
async def test_run_aggregation_computes_counts_and_percentiles(
    fake_db: _FakeAsyncDB,
    now: datetime,
    output_dir: Path,
) -> None:
    in_window = now - timedelta(hours=1)
    out_of_window = now - timedelta(days=2)

    for ms in (100.0, 200.0, 300.0, 400.0):
        _seed_run(
            fake_db,
            started_at=in_window,
            status="success",
            duration_ms=ms,
        )
    _seed_run(fake_db, started_at=in_window, status="failed", duration_ms=500.0)
    # Seed an out-of-window doc that should be ignored.
    _seed_run(fake_db, started_at=out_of_window, status="success", duration_ms=9999.0)

    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir
    ).generate(run_date=now)

    assert summary.total_runs == 5
    assert summary.success_count == 4
    assert summary.failure_count == 1
    # Linear-interp p50 of [100,200,300,400,500] is 300.
    assert summary.p50_latency_ms == pytest.approx(300.0)
    # p95 of [100,200,300,400,500] under linear interp is 480.
    assert summary.p95_latency_ms == pytest.approx(480.0)


@pytest.mark.asyncio
async def test_top_failing_nodes_are_ordered_by_count(
    fake_db: _FakeAsyncDB,
    now: datetime,
    output_dir: Path,
) -> None:
    in_window = now - timedelta(hours=2)

    def _fail(node_id: str) -> dict[str, Any]:
        return {"node_id": node_id, "status": "error"}

    _seed_run(
        fake_db,
        started_at=in_window,
        status="failed",
        node_outputs=[_fail("retrieve"), _fail("synthesize")],
    )
    _seed_run(
        fake_db,
        started_at=in_window,
        status="failed",
        node_outputs=[_fail("retrieve"), {"node_id": "ok", "status": "success"}],
    )
    _seed_run(
        fake_db,
        started_at=in_window,
        status="failed",
        node_outputs=[_fail("retrieve")],
    )

    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir
    ).generate(run_date=now)

    assert [entry["node_id"] for entry in summary.top_failing_nodes] == [
        "retrieve",
        "synthesize",
    ]
    assert summary.top_failing_nodes[0]["failure_count"] == 3
    assert summary.top_failing_nodes[1]["failure_count"] == 1


@pytest.mark.asyncio
async def test_regression_detection_flags_drops_against_baseline(
    fake_db: _FakeAsyncDB,
    now: datetime,
    output_dir: Path,
) -> None:
    in_window = now - timedelta(hours=3)
    baseline_at = now - timedelta(days=6, hours=12)

    # Current evaluation: composite score 0.6 for (strat-a, cap-a).
    _seed_evaluation(fake_db, created_at=in_window, composite_score=0.6)
    # Prior-week baseline ranking: 0.9 for the same strategy+capability.
    _seed_scheduler_report(
        fake_db,
        completed_at=baseline_at,
        rankings=[{"strategy_id": "strat-a", "score": 0.9}],
        capability_id="cap-a",
    )

    summary = await NightlyReportGenerator(
        fake_db,
        output_dir=output_dir,
        regression_threshold=0.05,
    ).generate(run_date=now)

    assert summary.evaluation_count == 1
    assert summary.mean_composite_score == pytest.approx(0.6)
    assert len(summary.regressions) == 1
    flagged = summary.regressions[0]
    assert flagged["strategy_id"] == "strat-a"
    assert flagged["capability_id"] == "cap-a"
    assert flagged["current_score"] == pytest.approx(0.6)
    assert flagged["baseline_score"] == pytest.approx(0.9)
    assert flagged["delta"] == pytest.approx(-0.3)


@pytest.mark.asyncio
async def test_regression_below_threshold_is_not_flagged(
    fake_db: _FakeAsyncDB,
    now: datetime,
    output_dir: Path,
) -> None:
    in_window = now - timedelta(hours=3)
    baseline_at = now - timedelta(days=6, hours=12)

    _seed_evaluation(fake_db, created_at=in_window, composite_score=0.86)
    _seed_scheduler_report(
        fake_db,
        completed_at=baseline_at,
        rankings=[{"strategy_id": "strat-a", "score": 0.9}],
    )

    summary = await NightlyReportGenerator(
        fake_db,
        output_dir=output_dir,
        regression_threshold=0.05,
    ).generate(run_date=now)

    assert summary.regressions == []


@pytest.mark.asyncio
async def test_idempotent_upsert_keyed_by_report_date(
    fake_db: _FakeAsyncDB,
    now: datetime,
    output_dir: Path,
) -> None:
    in_window = now - timedelta(hours=2)
    _seed_run(fake_db, started_at=in_window, status="success", duration_ms=150.0)

    generator = NightlyReportGenerator(fake_db, output_dir=output_dir)
    first = await generator.generate(run_date=now)
    second = await generator.generate(run_date=now)

    assert first.report_date == second.report_date
    persisted = fake_db[RUN_REPORT_COLLECTION_NAME]._docs
    # Upsert by report_date keeps a single row.
    assert len(persisted) == 1
    assert persisted[0]["report_date"] == second.report_date

    # Markdown is rewritten in place — file still exists, content fresh.
    markdown_path = output_dir / f"{second.report_date}.md"
    assert markdown_path.exists()

    # Unique index on report_date was created.
    indexes = fake_db[RUN_REPORT_COLLECTION_NAME].created_indexes
    assert any(
        ("report_date" in args[0] if args else False) and kwargs.get("unique") is True
        for args, kwargs in indexes
    )


@pytest.mark.asyncio
async def test_scheduler_health_rollup_counts_paused_and_breakers(
    fake_db: _FakeAsyncDB,
    now: datetime,
    output_dir: Path,
) -> None:
    await _seed_schedule(
        fake_db,
        StrategySchedule(
            id="s-paused",
            name="paused",
            paused=True,
            paused_reason="manual",
            candidate_strategy_ids=[],
        ),
    )
    await _seed_schedule(
        fake_db,
        StrategySchedule(
            id="s-open",
            name="breaker open",
            circuit_breaker_state="open",
            candidate_strategy_ids=[],
        ),
    )
    await _seed_schedule(
        fake_db,
        StrategySchedule(
            id="s-failing",
            name="failing",
            consecutive_failures=4,
            candidate_strategy_ids=[],
        ),
    )
    await _seed_schedule(
        fake_db,
        StrategySchedule(
            id="s-healthy",
            name="healthy",
            candidate_strategy_ids=[],
        ),
    )

    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir
    ).generate(run_date=now)

    assert summary.paused_schedules_count == 1
    assert summary.open_circuit_breakers_count == 1
    assert summary.schedules_at_failure_threshold == 1
    # Sanity: the schedules collection name didn't drift.
    assert SCHEDULE_COLLECTION_NAME == "strategy_schedules"


@pytest.mark.asyncio
async def test_default_output_dir_is_repo_data_reports_strategy(
    fake_db: _FakeAsyncDB, now: datetime, tmp_path: Path
) -> None:
    """Without an override, the generator targets data/reports/strategy."""
    generator = NightlyReportGenerator(fake_db)
    expected = generator._default_output_dir()
    assert expected.parts[-3:] == ("data", "reports", "strategy")


# ═══════════════════════════════════════════════════════════════════════════
# F3: tz-aware nightly-report cron (Task 69)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_nightly_report_triggers_at_local_hour_in_configured_timezone(
    fake_db: _FakeAsyncDB, output_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trigger fires at 03:00 *Vienna* time during DST and standard time.

    Verifies the resolver maps UTC -> Europe/Vienna correctly across
    both spring/summer (CEST, UTC+2) and autumn/winter (CET, UTC+1)
    so operators get the report at 03:00 wall-clock regardless of DST.
    """
    daemon = SchedulerDaemon(
        db=fake_db,
        nightly_report_hour_local=3,
        nightly_report_timezone="Europe/Vienna",
        nightly_report_dir=str(output_dir),
    )

    # Spring-forward / DST active: 2026-06-15 01:00Z == 03:00 CEST.
    summer_utc = datetime(2026, 6, 15, 1, 0, tzinfo=timezone.utc)
    await daemon._maybe_run_nightly_report(summer_utc)
    assert daemon._last_nightly_report_date == "2026-06-15"

    # And 03:00Z (which is 05:00 Vienna) on the same day must NOT
    # re-trigger via the UTC hour matching the configured local hour.
    daemon._last_nightly_report_date = None
    summer_utc_03 = datetime(2026, 6, 15, 3, 0, tzinfo=timezone.utc)
    await daemon._maybe_run_nightly_report(summer_utc_03)
    assert daemon._last_nightly_report_date is None

    # Standard time / fall-back side: 2026-01-15 02:00Z == 03:00 CET.
    daemon._last_nightly_report_date = None
    winter_utc = datetime(2026, 1, 15, 2, 0, tzinfo=timezone.utc)
    await daemon._maybe_run_nightly_report(winter_utc)
    assert daemon._last_nightly_report_date == "2026-01-15"

    # And 03:00Z (== 04:00 Vienna in winter) must NOT trigger.
    daemon._last_nightly_report_date = None
    winter_utc_03 = datetime(2026, 1, 15, 3, 0, tzinfo=timezone.utc)
    await daemon._maybe_run_nightly_report(winter_utc_03)
    assert daemon._last_nightly_report_date is None


@pytest.mark.asyncio
async def test_nightly_report_does_not_double_trigger(
    fake_db: _FakeAsyncDB, output_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once fired at 03:00 local, advancing 30 minutes must not re-fire."""
    from backend.agent.strategy import report_generator as rg_mod

    call_count = {"n": 0}
    real_generate = rg_mod.NightlyReportGenerator.generate

    async def spy_generate(self: Any, *, run_date: datetime | None = None) -> Any:
        call_count["n"] += 1
        return await real_generate(self, run_date=run_date)

    monkeypatch.setattr(
        rg_mod.NightlyReportGenerator, "generate", spy_generate
    )

    daemon = SchedulerDaemon(
        db=fake_db,
        nightly_report_hour_local=3,
        nightly_report_timezone="Europe/Vienna",
        nightly_report_dir=str(output_dir),
    )

    # 03:00 Vienna (CEST).
    t1 = datetime(2026, 6, 15, 1, 0, tzinfo=timezone.utc)
    await daemon._maybe_run_nightly_report(t1)
    assert call_count["n"] == 1
    assert daemon._last_nightly_report_date == "2026-06-15"

    # +30 minutes still inside hour 3 in Vienna; dedup must short-circuit.
    t2 = t1 + timedelta(minutes=30)
    await daemon._maybe_run_nightly_report(t2)
    assert call_count["n"] == 1
    assert daemon._last_nightly_report_date == "2026-06-15"


@pytest.mark.asyncio
async def test_invalid_timezone_falls_back_to_utc(
    fake_db: _FakeAsyncDB,
    output_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Invalid tz strings log a warning and degrade to UTC behaviour."""
    daemon = SchedulerDaemon(
        db=fake_db,
        nightly_report_hour_local=3,
        nightly_report_timezone="Not/A/Real/Zone",
        nightly_report_dir=str(output_dir),
    )

    # 03:00Z must trigger under UTC fallback semantics.
    trigger_utc = datetime(2026, 6, 15, 3, 0, tzinfo=timezone.utc)
    with caplog.at_level(logging.WARNING, logger="backend.scheduler.scheduler_daemon"):
        await daemon._maybe_run_nightly_report(trigger_utc)

    assert daemon._last_nightly_report_date == "2026-06-15"
    assert any(
        "Invalid nightly-report timezone" in record.getMessage()
        for record in caplog.records
    )

    # A second daemon: 02:00Z must NOT trigger when falling back to UTC
    # (would have triggered if Vienna tz were honoured).
    daemon2 = SchedulerDaemon(
        db=fake_db,
        nightly_report_hour_local=3,
        nightly_report_timezone="Bogus/Zone",
        nightly_report_dir=str(output_dir),
    )
    non_trigger_utc = datetime(2026, 6, 15, 2, 0, tzinfo=timezone.utc)
    await daemon2._maybe_run_nightly_report(non_trigger_utc)
    assert daemon2._last_nightly_report_date is None


# ═════════════════════════════════════════════════════════════════════════
# Phase 6 / Task 79 / P10 — Blueprint 05 §10 sections
# ═════════════════════════════════════════════════════════════════════════


def _seed_candidate_spec(
    db: _FakeAsyncDB,
    *,
    strategy_id: str,
    capability_id: str,
    status: str = "draft",
    version: str = "v1",
) -> None:
    db["strategy_specs"]._docs.append(
        {
            "status": status,
            "version": version,
            "spec_data": {
                "strategy_id": strategy_id,
                "capability_id": capability_id,
                "status": status,
            },
        }
    )


def _seed_eval_full(
    db: _FakeAsyncDB,
    *,
    strategy_id: str,
    capability_id: str,
    composite_score: float,
    created_at: datetime,
    latency_ms: float = 1000.0,
    citation_coverage: float = 0.95,
    fatal: bool = False,
    privacy_pass: bool = True,
    test_case_id: str = "tc-1",
    dataset_id: str = "default",
) -> None:
    coll: _FakeAsyncCollection = db["evaluation_results"]
    coll._docs.append(
        {
            "test_case_id": test_case_id,
            "dataset_id": dataset_id,
            "strategy_id": strategy_id,
            "capability_id": capability_id,
            "result": {
                "composite_score": composite_score,
                "latency_ms": latency_ms,
                "fatal_gate": 0 if fatal else 1,
                "privacy_gate": 1 if privacy_pass else 0,
            },
            "metrics": {
                "citation_coverage": citation_coverage,
                "citation_accuracy": citation_coverage,
                "retrieval_precision": 0.9,
                "top_k_used": 10,
            },
            "created_at": created_at,
        }
    )


@pytest.mark.asyncio
async def test_winning_strategies_by_capability_aggregates_correctly(
    fake_db: _FakeAsyncDB, now: datetime, output_dir: Path
) -> None:
    in_window = now - timedelta(hours=3)
    # Two strategies on cap-a (strat-a wins), one on cap-b (only entry).
    _seed_evaluation(
        fake_db, created_at=in_window, composite_score=0.7,
        strategy_id="strat-a", capability_id="cap-a",
    )
    _seed_evaluation(
        fake_db, created_at=in_window, composite_score=0.5,
        strategy_id="strat-b", capability_id="cap-a",
    )
    _seed_evaluation(
        fake_db, created_at=in_window, composite_score=0.6,
        strategy_id="strat-c", capability_id="cap-b",
    )

    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir
    ).generate(run_date=now)

    assert set(summary.winning_strategies_by_capability.keys()) == {
        "cap-a",
        "cap-b",
    }
    assert summary.winning_strategies_by_capability["cap-a"]["strategy_id"] == "strat-a"
    assert summary.winning_strategies_by_capability["cap-a"]["mean_score"] == pytest.approx(0.7)
    assert summary.winning_strategies_by_capability["cap-b"]["strategy_id"] == "strat-c"


@pytest.mark.asyncio
async def test_recommended_promotions_populated_from_evaluator(
    fake_db: _FakeAsyncDB, now: datetime, output_dir: Path
) -> None:
    in_window = now - timedelta(hours=2)
    # Active default + draft candidate that beats it.
    fake_db["strategy_specs"]._docs.append(
        {
            "status": "active",
            "version": "v1",
            "spec_data": {
                "strategy_id": "def-strat",
                "capability_id": "cap-x",
                "status": "active",
            },
        }
    )
    _seed_candidate_spec(
        fake_db, strategy_id="cand-strat", capability_id="cap-x"
    )
    for i in range(35):
        _seed_eval_full(
            fake_db,
            strategy_id="def-strat",
            capability_id="cap-x",
            composite_score=0.80,
            created_at=in_window,
            test_case_id=f"def-{i}",
        )
        _seed_eval_full(
            fake_db,
            strategy_id="cand-strat",
            capability_id="cap-x",
            composite_score=0.92,
            created_at=in_window,
            test_case_id=f"cand-{i}",
        )

    # Patch the manual-approval gate so the candidate clears all
    # thresholds. The default thresholds always block on this gate.
    from backend.agent.strategy import promotion_evaluator as pe_mod

    original_init = pe_mod.PromotionEvaluator.__init__

    def _init_no_manual(self: pe_mod.PromotionEvaluator, **kwargs: Any) -> None:
        kwargs.setdefault(
            "thresholds",
            pe_mod.PromotionThresholds(manual_approval_required=False),
        )
        original_init(self, **kwargs)

    pe_mod.PromotionEvaluator.__init__ = _init_no_manual
    try:
        summary = await NightlyReportGenerator(
            fake_db, output_dir=output_dir
        ).generate(run_date=now)
    finally:
        pe_mod.PromotionEvaluator.__init__ = original_init

    assert len(summary.recommended_promotions) == 1
    assert summary.recommended_promotions[0].strategy_id == "cand-strat"
    assert summary.recommended_promotions[0].recommendation == "recommended"
    # Breakdown contains the same row.
    assert any(
        c.strategy_id == "cand-strat"
        for c in summary.promotion_evaluation_breakdown
    )


@pytest.mark.asyncio
async def test_recommended_deprecations_flags_score_drops(
    fake_db: _FakeAsyncDB, now: datetime, output_dir: Path
) -> None:
    # Current 30-day mean << prior 30-day mean for strat-a.
    current_at = now - timedelta(days=5)
    prior_at = now - timedelta(days=45)
    in_window = now - timedelta(hours=2)
    # One in-window doc to register strat-a in current_per_strategy.
    _seed_evaluation(
        fake_db, created_at=in_window, composite_score=0.50,
        strategy_id="strat-a", capability_id="cap-a",
    )
    # 30-day current window: 0.50.
    for _ in range(3):
        _seed_evaluation(
            fake_db, created_at=current_at, composite_score=0.50,
            strategy_id="strat-a", capability_id="cap-a",
        )
    # 30-day prior window: 0.90 (huge drop).
    for _ in range(3):
        _seed_evaluation(
            fake_db, created_at=prior_at, composite_score=0.90,
            strategy_id="strat-a", capability_id="cap-a",
        )

    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir
    ).generate(run_date=now)

    flagged = [r for r in summary.recommended_deprecations if r["strategy_id"] == "strat-a"]
    assert len(flagged) == 1
    assert "score_drop_gt_10pct" in flagged[0]["reasons"]
    assert flagged[0]["score_delta_pct"] is not None
    assert flagged[0]["score_delta_pct"] < -0.10


@pytest.mark.asyncio
async def test_recommended_deprecations_flags_fatal_failures(
    fake_db: _FakeAsyncDB, now: datetime, output_dir: Path
) -> None:
    in_window = now - timedelta(hours=2)
    # Seed a strategy run with status=fatal.
    fake_db[STRATEGY_RUN_COLLECTION_NAME]._docs.append(
        {
            "run_id": "run-fatal",
            "strategy_id": "strat-fatal",
            "capability_id": "cap-a",
            "status": "fatal",
            "started_at": in_window,
            "completed_at": in_window + timedelta(milliseconds=100),
            "duration_ms": 100.0,
            "halt_reason": "hard_limit_breach",
            "node_outputs": [],
            "dataset_id": "ds-1",
            "test_case_id": "tc-fatal",
        }
    )
    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir
    ).generate(run_date=now)

    assert len(summary.fatal_failures) == 1
    assert summary.fatal_failures[0]["strategy_id"] == "strat-fatal"
    flagged = [
        r for r in summary.recommended_deprecations if r["strategy_id"] == "strat-fatal"
    ]
    assert len(flagged) == 1
    assert "fatal_failure_in_window" in flagged[0]["reasons"]


@pytest.mark.asyncio
async def test_cases_needing_better_gold_labels_picks_low_scoring_cases(
    fake_db: _FakeAsyncDB, now: datetime, output_dir: Path
) -> None:
    in_window = now - timedelta(hours=1)
    # Case tc-bad: every strategy scores < 0.5.
    _seed_eval_full(
        fake_db,
        strategy_id="strat-a",
        capability_id="cap",
        composite_score=0.30,
        created_at=in_window,
        test_case_id="tc-bad",
        dataset_id="ds-1",
    )
    _seed_eval_full(
        fake_db,
        strategy_id="strat-b",
        capability_id="cap",
        composite_score=0.40,
        created_at=in_window,
        test_case_id="tc-bad",
        dataset_id="ds-1",
    )
    # Case tc-good: at least one strategy scores >= 0.5.
    _seed_eval_full(
        fake_db,
        strategy_id="strat-a",
        capability_id="cap",
        composite_score=0.70,
        created_at=in_window,
        test_case_id="tc-good",
        dataset_id="ds-1",
    )

    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir
    ).generate(run_date=now)

    flagged_ids = [r["case_id"] for r in summary.cases_needing_better_gold_labels]
    assert "tc-bad" in flagged_ids
    assert "tc-good" not in flagged_ids


@pytest.mark.asyncio
async def test_markdown_includes_all_blueprint_sections(
    fake_db: _FakeAsyncDB, now: datetime, output_dir: Path
) -> None:
    """All ten Blueprint 05 §10 section headers render even on empty input."""
    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir
    ).generate(run_date=now)
    content = (output_dir / f"{summary.report_date}.md").read_text(encoding="utf-8")

    expected_headers = [
        "## Summary",
        "## Winning Strategies by Capability",
        "## Regressions",
        "## Fatal Failures",
        "## Latency / Cost / Resource Table",
        "## Retrieval Quality Table",
        "## Citation Quality Table",
        "## Recommended Promotions",
        "## Recommended Deprecations",
        "## Test Cases Needing Better Gold Labels",
    ]
    # Each header appears exactly once and in the documented order.
    last_index = -1
    for header in expected_headers:
        assert header in content, f"missing section: {header}"
        idx = content.index(header)
        assert idx > last_index, f"section {header} out of order"
        last_index = idx
    # Empty period placeholder is rendered (not omitted).
    assert "_None this period._" in content


@pytest.mark.asyncio
async def test_regression_entries_carry_mode_and_triggers(
    fake_db: _FakeAsyncDB, now: datetime, output_dir: Path
) -> None:
    """Regression rows are augmented with RegressionMode triggers per §6.4."""
    in_window = now - timedelta(hours=3)
    baseline_at = now - timedelta(days=6, hours=12)
    _seed_evaluation(fake_db, created_at=in_window, composite_score=0.6)
    _seed_scheduler_report(
        fake_db,
        completed_at=baseline_at,
        rankings=[{"strategy_id": "strat-a", "score": 0.9}],
        capability_id="cap-a",
    )

    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir, regression_threshold=0.05
    ).generate(run_date=now)

    assert len(summary.regressions) == 1
    flagged = summary.regressions[0]
    assert flagged["mode"] == "regression"
    assert flagged["triggers"] == [
        "score_drop_exceeds_threshold",
        "forbidden_source_leak",
        "citation_coverage_drop",
        "latency_hard_limit_breach",
    ]


# ══════════════════════════════════════════════════════════════════════════
# F4 / Task 82 — latency_cost_table reads typed cost_eur
# ══════════════════════════════════════════════════════════════════════════


def _seed_runtime_profile(
    db: _FakeAsyncDB,
    *,
    profile_id: str,
    strategy_id: str,
    cost_eur: float | None,
    cost_source: str | None = "estimated",
) -> None:
    """Insert a minimal ``runtime_model_profiles`` doc the validator accepts."""
    doc: dict[str, Any] = {
        "id": profile_id,
        "host_id": "h",
        "tenant": "t",
        "model": "m",
        "provider": "ollama",
        "test_name": "short_rag",
        "context_tokens": 100,
        "output_tokens": 10,
        "total_latency_ms": 1,
        "tokens_per_second": 1.0,
        "success": True,
        # ``strategy_id`` lives in extras (not a core profile field).
        "strategy_id": strategy_id,
    }
    if cost_eur is not None:
        doc["cost_eur"] = cost_eur
        doc["cost_source"] = cost_source
    db["runtime_model_profiles"]._docs.append(doc)


@pytest.mark.asyncio
async def test_latency_cost_table_includes_costs_from_typed_field(
    fake_db: _FakeAsyncDB, now: datetime, output_dir: Path
) -> None:
    """Mean cost in latency_cost_table is sourced from typed ``cost_eur``."""
    in_window = now - timedelta(hours=2)
    _seed_run(
        fake_db,
        started_at=in_window,
        status="success",
        duration_ms=120.0,
        strategy_id="strat-a",
    )
    _seed_runtime_profile(
        fake_db, profile_id="rt-1", strategy_id="strat-a", cost_eur=0.01
    )
    _seed_runtime_profile(
        fake_db, profile_id="rt-2", strategy_id="strat-a", cost_eur=0.03
    )

    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir
    ).generate(run_date=now)

    rows = [r for r in summary.latency_cost_table if r["strategy_id"] == "strat-a"]
    assert len(rows) == 1
    assert rows[0]["mean_cost_eur"] == pytest.approx(0.02)

    # Markdown renders the numeric cost with 4 decimal places.
    content = (output_dir / f"{summary.report_date}.md").read_text(encoding="utf-8")
    assert "0.0200" in content


@pytest.mark.asyncio
async def test_latency_cost_table_renders_em_dash_when_cost_missing(
    fake_db: _FakeAsyncDB, now: datetime, output_dir: Path
) -> None:
    """With no ``cost_eur`` populated, Markdown shows the em-dash placeholder."""
    in_window = now - timedelta(hours=2)
    _seed_run(
        fake_db,
        started_at=in_window,
        status="success",
        duration_ms=120.0,
        strategy_id="strat-no-cost",
    )
    # Profile exists but no cost_eur → contributes nothing to the rollup.
    _seed_runtime_profile(
        fake_db,
        profile_id="rt-nocost",
        strategy_id="strat-no-cost",
        cost_eur=None,
        cost_source=None,
    )

    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir
    ).generate(run_date=now)

    rows = [
        r for r in summary.latency_cost_table if r["strategy_id"] == "strat-no-cost"
    ]
    assert len(rows) == 1
    assert rows[0]["mean_cost_eur"] is None

    content = (output_dir / f"{summary.report_date}.md").read_text(encoding="utf-8")
    assert "strat-no-cost" in content
    # The cost column for this row uses the em-dash placeholder.
    cost_row = next(
        line for line in content.splitlines() if "strat-no-cost" in line
    )
    assert cost_row.rstrip().endswith("— |")


# ════════════════════════════════════════════════════════════════════════
# F5 / Task 87 — JSON sibling next to Markdown report
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_generate_writes_json_sibling(
    fake_db: _FakeAsyncDB, now: datetime, output_dir: Path
) -> None:
    """A JSON file is written next to the Markdown report with the full summary."""
    in_window = now - timedelta(hours=2)
    _seed_run(
        fake_db,
        started_at=in_window,
        status="success",
        duration_ms=120.0,
        strategy_id="strat-a",
    )
    _seed_evaluation(fake_db, created_at=in_window, composite_score=0.75)

    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir
    ).generate(run_date=now)

    md_path = output_dir / f"{summary.report_date}.md"
    json_path = output_dir / f"{summary.report_date}.json"
    assert md_path.exists()
    assert json_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    # Top-level fields surface through model_dump(mode="json").
    assert payload["report_date"] == summary.report_date
    assert "window_start" in payload
    assert "window_end" in payload
    assert payload["total_runs"] == 1
    assert "latency_cost_table" in payload
    assert "recommended_promotions" in payload
    # Path fields are serialized as strings under mode="json".
    assert isinstance(payload["markdown_path"], str)
    assert payload["markdown_path"].endswith(f"{summary.report_date}.md")
    assert isinstance(payload["json_path"], str)
    assert payload["json_path"].endswith(f"{summary.report_date}.json")


@pytest.mark.asyncio
async def test_json_sibling_includes_cost_eur_from_f4(
    fake_db: _FakeAsyncDB, now: datetime, output_dir: Path
) -> None:
    """The JSON sibling carries the typed ``cost_eur`` rollup added in F4."""
    in_window = now - timedelta(hours=2)
    _seed_run(
        fake_db,
        started_at=in_window,
        status="success",
        duration_ms=140.0,
        strategy_id="strat-cost",
    )
    _seed_runtime_profile(
        fake_db, profile_id="rt-cost-1", strategy_id="strat-cost", cost_eur=0.05
    )

    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir
    ).generate(run_date=now)

    json_path = output_dir / f"{summary.report_date}.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    rows = [
        r for r in payload["latency_cost_table"] if r["strategy_id"] == "strat-cost"
    ]
    assert len(rows) == 1
    assert rows[0]["mean_cost_eur"] == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_json_sibling_idempotent(
    fake_db: _FakeAsyncDB, now: datetime, output_dir: Path
) -> None:
    """Re-running for the same date overwrites the JSON file in place."""
    in_window = now - timedelta(hours=2)
    _seed_run(fake_db, started_at=in_window, status="success", duration_ms=180.0)

    generator = NightlyReportGenerator(fake_db, output_dir=output_dir)
    first = await generator.generate(run_date=now)
    json_path = output_dir / f"{first.report_date}.json"
    first_text = json_path.read_text(encoding="utf-8")
    first_payload = json.loads(first_text)

    second = await generator.generate(run_date=now)
    assert second.report_date == first.report_date
    second_text = json_path.read_text(encoding="utf-8")
    second_payload = json.loads(second_text)

    # Single file, no append, parseable JSON, content stable modulo
    # ``generated_at`` (which is recomputed on each invocation).
    first_payload.pop("generated_at", None)
    second_payload.pop("generated_at", None)
    assert first_payload == second_payload


@pytest.mark.asyncio
async def test_summary_exposes_paths(
    fake_db: _FakeAsyncDB, now: datetime, output_dir: Path
) -> None:
    """``summary.markdown_path`` and ``summary.json_path`` are populated Paths."""
    summary = await NightlyReportGenerator(
        fake_db, output_dir=output_dir
    ).generate(run_date=now)

    assert isinstance(summary.markdown_path, Path)
    assert isinstance(summary.json_path, Path)
    assert summary.markdown_path == output_dir / f"{summary.report_date}.md"
    assert summary.json_path == output_dir / f"{summary.report_date}.json"
    assert summary.markdown_path.exists()
    assert summary.json_path.exists()

