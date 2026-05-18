"""Tests for :mod:`backend.agent.strategy.evaluation_runner_adapter` (Task 75)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.agent.strategy.evaluation_runner_adapter import (
    CaseScore,
    EvaluationRunnerAdapter,
)
from backend.evaluation.composite_scorer import CompositeScoreCalculator
from backend.evaluation.models import (
    DimensionId,
    DimensionScore,
    EvaluationTestCase,
    ScoringProfile,
)


class _FakeTestCaseManager:
    """Stub :class:`TestCaseManager` returning canned cases."""

    def __init__(self, cases_by_dataset: dict[str, list[EvaluationTestCase]]):
        self._cases = cases_by_dataset
        self.calls: list[str] = []

    async def list_by_dataset(
        self, dataset_id: str
    ) -> list[EvaluationTestCase]:
        self.calls.append(dataset_id)
        return list(self._cases.get(dataset_id, []))


class _FakeBaseRunner:
    """Stub Phase 3 :class:`EvaluationRunner` exposing the adapter contract."""

    def __init__(
        self,
        cases_by_dataset: dict[str, list[EvaluationTestCase]],
        *,
        dimension_scores: list[DimensionScore] | None = None,
    ):
        self.test_case_manager = _FakeTestCaseManager(cases_by_dataset)
        self.scorer = CompositeScoreCalculator()
        self._dimension_scores = dimension_scores or []
        self.score_calls: list[dict[str, Any]] = []

    async def _score_dimensions(
        self,
        *,
        execution_result: dict,
        test_case: EvaluationTestCase,
        judge_model: str | None,
    ) -> list[DimensionScore]:
        self.score_calls.append(
            {
                "execution_result": dict(execution_result),
                "test_case_id": test_case.id,
                "judge_model": judge_model,
            }
        )
        return list(self._dimension_scores)


def _profile_dimension_scores() -> list[DimensionScore]:
    """Return one DimensionScore per dim with default profile weights."""
    profile = ScoringProfile()
    out: list[DimensionScore] = []
    for dim in DimensionId:
        out.append(
            DimensionScore(
                dimension_id=dim,
                score=0.8,
                weight=profile.weights.get(dim.value, 0.0),
            )
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# load_dataset
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_dataset_returns_active_cases() -> None:
    case_a = EvaluationTestCase(user_prompt="A", dataset_id="ds-a")
    case_b = EvaluationTestCase(user_prompt="B", dataset_id="ds-a")
    base = _FakeBaseRunner({"ds-a": [case_a, case_b]})
    adapter = EvaluationRunnerAdapter(base)

    cases = await adapter.load_dataset("ds-a")
    assert [c.id for c in cases] == [case_a.id, case_b.id]
    assert base.test_case_manager.calls == ["ds-a"]


@pytest.mark.asyncio
async def test_load_dataset_filters_stale_cases() -> None:
    """Stale cases are skipped, mirroring Phase 3 behavior."""
    fresh = EvaluationTestCase(user_prompt="ok", dataset_id="ds-a")
    stale = EvaluationTestCase(user_prompt="bad", dataset_id="ds-a", is_stale=True)
    base = _FakeBaseRunner({"ds-a": [fresh, stale]})
    adapter = EvaluationRunnerAdapter(base)

    cases = await adapter.load_dataset("ds-a")
    assert [c.id for c in cases] == [fresh.id]


# ─────────────────────────────────────────────────────────────────────────────
# score
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_score_returns_case_score_with_composite() -> None:
    """``score`` produces a CaseScore using the bound CompositeScoreCalculator."""
    base = _FakeBaseRunner(
        {}, dimension_scores=_profile_dimension_scores()
    )
    adapter = EvaluationRunnerAdapter(base)

    case = EvaluationTestCase(user_prompt="A", dataset_id="ds-a")
    run_result = SimpleNamespace(
        success=True,
        total_duration_ms=1500.0,
        state=SimpleNamespace(
            synthesis_result=SimpleNamespace(text="Result text"),
            retrieved_chunks=[],
        ),
    )
    score = await adapter.score(case, run_result)

    assert isinstance(score, CaseScore)
    # All dimensions scored 0.8 with default-profile weights → composite=0.8.
    assert score.composite_score == pytest.approx(0.8)
    assert score.latency_ms == pytest.approx(1500.0)
    # Per-dimension scores live in metrics.
    assert set(DimensionId).issubset(
        {DimensionId(k) for k in score.metrics if k in DimensionId.__members__.values()}
        or {DimensionId(k) for k in score.metrics if k in {d.value for d in DimensionId}}
    )
    assert "weighted_sum" in score.metrics
    assert "latency_score" in score.metrics


@pytest.mark.asyncio
async def test_score_normalises_dict_run_result() -> None:
    """Dict-shaped run results are passed through to ``_score_dimensions``."""
    base = _FakeBaseRunner(
        {}, dimension_scores=_profile_dimension_scores()
    )
    adapter = EvaluationRunnerAdapter(base)
    case = EvaluationTestCase(user_prompt="Q", dataset_id="ds-a")
    run_result = {
        "synthesis_text": "## answer",
        "duration_ms": 4200.0,
        "chunks": [],
        "source_ids": [],
        "success": True,
    }
    score = await adapter.score(case, run_result)
    assert score.latency_ms == pytest.approx(4200.0)
    assert base.score_calls[0]["execution_result"]["duration_ms"] == 4200.0


def test_adapter_requires_base_runner() -> None:
    """Constructor rejects ``None`` base runners."""
    with pytest.raises(ValueError):
        EvaluationRunnerAdapter(None)
