"""Tests for :mod:`backend.agent.strategy.promotion_evaluator` (Task 79 / P10).

Covers the Blueprint 05 §11 thresholds:

* All thresholds pass → ``recommended``.
* Insufficient runs → ``needs_more_runs``.
* Score below ``min_composite_score`` → ``blocked``.
* Latency p95 above ``latency_p95_below_ms`` → ``blocked``.
* Citation coverage below threshold → ``blocked``.
* Fatal failure present → ``blocked``.
* Beats current default by less than ``beats_current_default_by`` →
  ``blocked``.
* ``evaluate_all`` returns rows ordered by ``score_delta`` desc.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.agent.strategy.promotion_evaluator import (
    PromotionCandidateSummary,
    PromotionEvaluator,
    PromotionThresholds,
)
from backend.tests.strategy._fakes import _FakeAsyncCollection, _FakeAsyncDB


# ──────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_db() -> _FakeAsyncDB:
    return _FakeAsyncDB()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def window(now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(hours=24), now


def _seed_eval(
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
    judge_variance: float | None = None,
) -> None:
    coll: _FakeAsyncCollection = db["evaluation_results"]
    fatal_gate = 0 if fatal else 1
    privacy_gate = 1 if privacy_pass else 0
    doc: dict[str, Any] = {
        "test_case_id": test_case_id,
        "strategy_id": strategy_id,
        "capability_id": capability_id,
        "result": {
            "composite_score": composite_score,
            "latency_ms": latency_ms,
            "fatal_gate": fatal_gate,
            "privacy_gate": privacy_gate,
        },
        "metrics": {"citation_coverage": citation_coverage},
        "created_at": created_at,
    }
    if judge_variance is not None:
        doc["judge_variance"] = judge_variance
    coll._docs.append(doc)


def _seed_active_default(
    db: _FakeAsyncDB,
    *,
    strategy_id: str,
    capability_id: str,
) -> None:
    coll: _FakeAsyncCollection = db["strategy_specs"]
    coll._docs.append(
        {
            "status": "active",
            "version": "v1",
            "spec_data": {
                "strategy_id": strategy_id,
                "capability_id": capability_id,
                "status": "active",
            },
        }
    )


def _seed_candidate_spec(
    db: _FakeAsyncDB,
    *,
    strategy_id: str,
    capability_id: str,
    status: str = "draft",
    version: str = "v1",
) -> None:
    coll: _FakeAsyncCollection = db["strategy_specs"]
    coll._docs.append(
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


def _seed_passing_set(
    db: _FakeAsyncDB,
    *,
    strategy_id: str,
    capability_id: str,
    created_at: datetime,
    n_runs: int = 35,
    composite_score: float = 0.90,
    default_score: float = 0.80,
    latency_ms: float = 1000.0,
    citation_coverage: float = 0.95,
    default_strategy_id: str = "default-strategy",
) -> None:
    """Seed a candidate + active default with all thresholds passing."""
    _seed_active_default(
        db, strategy_id=default_strategy_id, capability_id=capability_id
    )
    for i in range(n_runs):
        _seed_eval(
            db,
            strategy_id=strategy_id,
            capability_id=capability_id,
            composite_score=composite_score,
            created_at=created_at,
            latency_ms=latency_ms,
            citation_coverage=citation_coverage,
            test_case_id=f"tc-{i}",
        )
        _seed_eval(
            db,
            strategy_id=default_strategy_id,
            capability_id=capability_id,
            composite_score=default_score,
            created_at=created_at,
            latency_ms=latency_ms,
            citation_coverage=citation_coverage,
            test_case_id=f"def-tc-{i}",
        )


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────


def test_thresholds_match_blueprint_05_section_11_defaults() -> None:
    """The eight §11 thresholds match the Blueprint defaults exactly."""
    t = PromotionThresholds()
    assert t.min_runs == 30
    assert t.min_composite_score == 0.82
    assert t.beats_current_default_by == 0.05
    assert t.max_fatal_failures == 0
    assert t.min_citation_coverage == 0.85
    assert t.latency_p95_below_ms == 60_000
    assert t.privacy_policy_pass is True
    assert t.manual_approval_required is True


@pytest.mark.asyncio
async def test_all_thresholds_pass_recommends_promotion(
    fake_db: _FakeAsyncDB,
    now: datetime,
    window: tuple[datetime, datetime],
) -> None:
    in_window = now - timedelta(hours=1)
    _seed_passing_set(
        fake_db,
        strategy_id="cand",
        capability_id="cap",
        created_at=in_window,
    )
    # Disable manual_approval_required so the all-pass case isn't blocked
    # by the operator gate (which the evaluator never auto-clears).
    evaluator = PromotionEvaluator(
        thresholds=PromotionThresholds(manual_approval_required=False)
    )
    summary = await evaluator.evaluate(
        db=fake_db,
        capability_id="cap",
        candidate_strategy_id="cand",
        candidate_version="v2",
        since=window[0],
        until=window[1],
    )
    assert isinstance(summary, PromotionCandidateSummary)
    assert summary.total_runs == 35
    assert summary.recommendation == "recommended"
    assert summary.blocking_reasons == []
    assert summary.passes_thresholds["min_runs"] is True
    assert summary.passes_thresholds["min_composite_score"] is True
    assert summary.passes_thresholds["beats_current_default_by"] is True
    assert summary.score_delta == pytest.approx(0.10, abs=1e-6)


@pytest.mark.asyncio
async def test_insufficient_runs_yields_needs_more_runs(
    fake_db: _FakeAsyncDB,
    now: datetime,
    window: tuple[datetime, datetime],
) -> None:
    in_window = now - timedelta(hours=1)
    # Only 10 candidate runs (< min_runs=30).
    _seed_active_default(
        fake_db, strategy_id="def", capability_id="cap"
    )
    for i in range(10):
        _seed_eval(
            fake_db,
            strategy_id="cand",
            capability_id="cap",
            composite_score=0.90,
            created_at=in_window,
            test_case_id=f"tc-{i}",
        )
        _seed_eval(
            fake_db,
            strategy_id="def",
            capability_id="cap",
            composite_score=0.80,
            created_at=in_window,
            test_case_id=f"def-tc-{i}",
        )
    evaluator = PromotionEvaluator(
        thresholds=PromotionThresholds(manual_approval_required=False)
    )
    summary = await evaluator.evaluate(
        db=fake_db,
        capability_id="cap",
        candidate_strategy_id="cand",
        candidate_version=None,
        since=window[0],
        until=window[1],
    )
    assert summary.recommendation == "needs_more_runs"
    assert summary.passes_thresholds["min_runs"] is False
    assert "min_runs" not in summary.blocking_reasons


@pytest.mark.asyncio
async def test_low_composite_score_blocks_promotion(
    fake_db: _FakeAsyncDB,
    now: datetime,
    window: tuple[datetime, datetime],
) -> None:
    in_window = now - timedelta(hours=1)
    _seed_passing_set(
        fake_db,
        strategy_id="cand",
        capability_id="cap",
        created_at=in_window,
        composite_score=0.70,  # below 0.82
        default_score=0.50,
    )
    evaluator = PromotionEvaluator(
        thresholds=PromotionThresholds(manual_approval_required=False)
    )
    summary = await evaluator.evaluate(
        db=fake_db,
        capability_id="cap",
        candidate_strategy_id="cand",
        candidate_version=None,
        since=window[0],
        until=window[1],
    )
    assert summary.recommendation == "blocked"
    assert "min_composite_score" in summary.blocking_reasons
    assert summary.passes_thresholds["min_composite_score"] is False


@pytest.mark.asyncio
async def test_high_latency_p95_blocks_promotion(
    fake_db: _FakeAsyncDB,
    now: datetime,
    window: tuple[datetime, datetime],
) -> None:
    in_window = now - timedelta(hours=1)
    _seed_passing_set(
        fake_db,
        strategy_id="cand",
        capability_id="cap",
        created_at=in_window,
        latency_ms=120_000.0,  # above 60_000
    )
    evaluator = PromotionEvaluator(
        thresholds=PromotionThresholds(manual_approval_required=False)
    )
    summary = await evaluator.evaluate(
        db=fake_db,
        capability_id="cap",
        candidate_strategy_id="cand",
        candidate_version=None,
        since=window[0],
        until=window[1],
    )
    assert summary.recommendation == "blocked"
    assert "latency_p95_below_ms" in summary.blocking_reasons


@pytest.mark.asyncio
async def test_low_citation_coverage_blocks_promotion(
    fake_db: _FakeAsyncDB,
    now: datetime,
    window: tuple[datetime, datetime],
) -> None:
    in_window = now - timedelta(hours=1)
    _seed_passing_set(
        fake_db,
        strategy_id="cand",
        capability_id="cap",
        created_at=in_window,
        citation_coverage=0.50,  # below 0.85
    )
    evaluator = PromotionEvaluator(
        thresholds=PromotionThresholds(manual_approval_required=False)
    )
    summary = await evaluator.evaluate(
        db=fake_db,
        capability_id="cap",
        candidate_strategy_id="cand",
        candidate_version=None,
        since=window[0],
        until=window[1],
    )
    assert summary.recommendation == "blocked"
    assert "min_citation_coverage" in summary.blocking_reasons


@pytest.mark.asyncio
async def test_fatal_failure_blocks_promotion(
    fake_db: _FakeAsyncDB,
    now: datetime,
    window: tuple[datetime, datetime],
) -> None:
    in_window = now - timedelta(hours=1)
    _seed_passing_set(
        fake_db,
        strategy_id="cand",
        capability_id="cap",
        created_at=in_window,
    )
    # Add one fatal-gate failure for the candidate.
    _seed_eval(
        fake_db,
        strategy_id="cand",
        capability_id="cap",
        composite_score=0.90,
        created_at=in_window,
        fatal=True,
        test_case_id="tc-fatal",
    )
    evaluator = PromotionEvaluator(
        thresholds=PromotionThresholds(manual_approval_required=False)
    )
    summary = await evaluator.evaluate(
        db=fake_db,
        capability_id="cap",
        candidate_strategy_id="cand",
        candidate_version=None,
        since=window[0],
        until=window[1],
    )
    assert summary.fatal_failure_count == 1
    assert summary.recommendation == "blocked"
    assert "max_fatal_failures" in summary.blocking_reasons


@pytest.mark.asyncio
async def test_insufficient_score_delta_blocks_promotion(
    fake_db: _FakeAsyncDB,
    now: datetime,
    window: tuple[datetime, datetime],
) -> None:
    in_window = now - timedelta(hours=1)
    # Candidate beats default by only 0.02 (< 0.05 threshold).
    _seed_passing_set(
        fake_db,
        strategy_id="cand",
        capability_id="cap",
        created_at=in_window,
        composite_score=0.90,
        default_score=0.88,
    )
    evaluator = PromotionEvaluator(
        thresholds=PromotionThresholds(manual_approval_required=False)
    )
    summary = await evaluator.evaluate(
        db=fake_db,
        capability_id="cap",
        candidate_strategy_id="cand",
        candidate_version=None,
        since=window[0],
        until=window[1],
    )
    assert summary.score_delta == pytest.approx(0.02, abs=1e-6)
    assert summary.recommendation == "blocked"
    assert "beats_current_default_by" in summary.blocking_reasons


@pytest.mark.asyncio
async def test_privacy_gate_failure_blocks_promotion(
    fake_db: _FakeAsyncDB,
    now: datetime,
    window: tuple[datetime, datetime],
) -> None:
    in_window = now - timedelta(hours=1)
    _seed_passing_set(
        fake_db,
        strategy_id="cand",
        capability_id="cap",
        created_at=in_window,
    )
    # Inject one privacy-gate failure into the candidate set.
    _seed_eval(
        fake_db,
        strategy_id="cand",
        capability_id="cap",
        composite_score=0.90,
        created_at=in_window,
        privacy_pass=False,
        test_case_id="tc-priv",
    )
    evaluator = PromotionEvaluator(
        thresholds=PromotionThresholds(manual_approval_required=False)
    )
    summary = await evaluator.evaluate(
        db=fake_db,
        capability_id="cap",
        candidate_strategy_id="cand",
        candidate_version=None,
        since=window[0],
        until=window[1],
    )
    assert summary.recommendation == "blocked"
    assert "privacy_policy_pass" in summary.blocking_reasons


@pytest.mark.asyncio
async def test_evaluate_all_returns_results_sorted_by_score_delta_desc(
    fake_db: _FakeAsyncDB,
    now: datetime,
    window: tuple[datetime, datetime],
) -> None:
    in_window = now - timedelta(hours=1)
    # Active default for the capability.
    _seed_active_default(
        fake_db, strategy_id="def-strategy", capability_id="cap-x"
    )
    # Two candidate specs differ only in composite score.
    _seed_candidate_spec(
        fake_db, strategy_id="cand-low", capability_id="cap-x", version="v1"
    )
    _seed_candidate_spec(
        fake_db, strategy_id="cand-high", capability_id="cap-x", version="v1"
    )
    for i in range(35):
        _seed_eval(
            fake_db,
            strategy_id="def-strategy",
            capability_id="cap-x",
            composite_score=0.80,
            created_at=in_window,
            test_case_id=f"def-{i}",
        )
        _seed_eval(
            fake_db,
            strategy_id="cand-low",
            capability_id="cap-x",
            composite_score=0.83,
            created_at=in_window,
            test_case_id=f"low-{i}",
        )
        _seed_eval(
            fake_db,
            strategy_id="cand-high",
            capability_id="cap-x",
            composite_score=0.92,
            created_at=in_window,
            test_case_id=f"high-{i}",
        )
    evaluator = PromotionEvaluator(
        thresholds=PromotionThresholds(manual_approval_required=False)
    )
    rows = await evaluator.evaluate_all(
        db=fake_db, since=window[0], until=window[1]
    )
    ids = [r.strategy_id for r in rows]
    assert ids == ["cand-high", "cand-low"]
    assert rows[0].score_delta is not None
    assert rows[1].score_delta is not None
    assert rows[0].score_delta >= rows[1].score_delta
