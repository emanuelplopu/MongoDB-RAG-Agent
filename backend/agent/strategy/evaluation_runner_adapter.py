"""Adapter shim bridging :class:`backend.evaluation.runner.EvaluationRunner`
to the API expected by
:class:`backend.agent.strategy.experiment_runner.StrategyExperimentRunner`.

Phase 5 (James) introduced the runner-side contract::

    async def load_dataset(dataset_id) -> list[EvaluationCase]
    async def score(case, run_result) -> CaseScore

…but the existing Phase 3 :class:`EvaluationRunner` only ships a
batched ``run_evaluation(strategy_id, dataset_id)`` entrypoint. Rather
than refactoring the Phase 3 API, this thin adapter delegates the two
runner-side calls to existing services:

* :meth:`load_dataset` defers to
  :class:`backend.evaluation.test_case_manager.TestCaseManager.list_by_dataset`.
* :meth:`score` delegates to the
  :class:`backend.evaluation.composite_scorer.CompositeScoreCalculator`
  already wired on the Phase 3 runner.

The adapter intentionally does **not** re-implement scoring: it only
translates between the runner's expected attribute names and the Phase
3 component surfaces. That keeps the calibration tests, contradiction
detector, and judge calibrator authoritative.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.evaluation.models import (
    CompositeResult,
    EvaluationTestCase,
    ScoringProfile,
)

logger = logging.getLogger(__name__)

__all__ = ["CaseScore", "EvaluationRunnerAdapter"]


class CaseScore(BaseModel):
    """Per-case score returned by :meth:`EvaluationRunnerAdapter.score`.

    The Phase 5 runner reads ``composite_score``, ``latency_ms`` and
    ``metrics`` from the duck-typed score object; this concrete model
    matches that contract exactly so the runner does not need to peek
    at Phase 3's :class:`CompositeResult` directly.

    Attributes:
        composite_score: Final composite score in ``[0, 1]``.
        latency_ms: Latency the score reflects in milliseconds.
        metrics: Optional auxiliary metric mapping (per-dimension scores
            and the latency-component score live here).
    """

    model_config = ConfigDict(extra="forbid")

    composite_score: float = Field(ge=0.0, le=1.0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    metrics: dict[str, Any] = Field(default_factory=dict)


class EvaluationRunnerAdapter:
    """Thin adapter exposing ``load_dataset`` + ``score`` over Phase 3.

    Args:
        base_runner: An existing
            :class:`backend.evaluation.runner.EvaluationRunner` (or any
            object exposing ``test_case_manager``, ``scorer``, and
            ``_score_dimensions`` with the same semantics).
        scoring_profile: Optional :class:`ScoringProfile` override used
            for the latency configuration applied during scoring. When
            ``None`` the adapter uses the scorer's bound profile.
        judge_model: Optional judge-model identifier forwarded to
            :meth:`backend.evaluation.runner.EvaluationRunner._score_dimensions`.
    """

    def __init__(
        self,
        base_runner: Any,
        *,
        scoring_profile: Optional[ScoringProfile] = None,
        judge_model: Optional[str] = None,
    ) -> None:
        if base_runner is None:
            raise ValueError("EvaluationRunnerAdapter requires a base_runner")
        self._base_runner = base_runner
        self._scoring_profile = scoring_profile
        self._judge_model = judge_model

    async def load_dataset(self, dataset_id: str) -> list[EvaluationTestCase]:
        """Return the active test cases for ``dataset_id``.

        Delegates to the Phase 3
        :class:`backend.evaluation.test_case_manager.TestCaseManager`
        already attached to the base runner. Stale cases are filtered
        out — matching the behaviour of
        :meth:`EvaluationRunner.run_evaluation`.

        Args:
            dataset_id: Dataset identifier to load.

        Returns:
            List of non-stale :class:`EvaluationTestCase` instances in
            the dataset's natural order.
        """
        manager = getattr(self._base_runner, "test_case_manager", None)
        if manager is None:
            raise AttributeError(
                "base_runner has no 'test_case_manager'; cannot load datasets"
            )
        cases = await manager.list_by_dataset(dataset_id)
        return [c for c in cases if not getattr(c, "is_stale", False)]

    async def score(self, case: Any, run_result: Any) -> CaseScore:
        """Score ``run_result`` against ``case`` using Phase 3 internals.

        The adapter borrows
        :meth:`EvaluationRunner._score_dimensions` to extract per-
        dimension scores and feeds them to the bound
        :class:`CompositeScoreCalculator`. The shape of ``run_result``
        is normalised first so both
        :class:`backend.agent.strategy.models.StrategyRunResult` and
        the dict-form returned by
        :meth:`EvaluationRunner._execute_strategy` are accepted.

        Args:
            case: An :class:`EvaluationTestCase`.
            run_result: The strategy-run output produced by the
                experiment runner.

        Returns:
            A :class:`CaseScore` carrying the composite score, latency,
            and per-dimension metrics.
        """
        execution_result = self._normalise_run_result(run_result)

        score_dimensions = getattr(self._base_runner, "_score_dimensions", None)
        if score_dimensions is None:
            raise AttributeError(
                "base_runner has no '_score_dimensions' method; cannot score"
            )
        dimension_scores = await score_dimensions(
            execution_result=execution_result,
            test_case=case,
            judge_model=self._judge_model,
        )

        scorer = getattr(self._base_runner, "scorer", None)
        if scorer is None:
            raise AttributeError(
                "base_runner has no 'scorer' attached; cannot compose score"
            )

        latency_ms = float(execution_result.get("duration_ms", 0.0) or 0.0)
        latency_config = (
            self._scoring_profile.latency_config
            if self._scoring_profile is not None
            else None
        )
        composite: CompositeResult = scorer.calculate(
            dimension_scores=dimension_scores,
            latency_ms=latency_ms,
            latency_config=latency_config,
        )
        metrics: dict[str, Any] = {
            ds.dimension_id.value: ds.score for ds in composite.dimension_scores
        }
        metrics["weighted_sum"] = composite.weighted_sum
        metrics["latency_score"] = composite.latency_score
        return CaseScore(
            composite_score=composite.composite_score,
            latency_ms=composite.latency_ms,
            metrics=metrics,
        )

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_run_result(run_result: Any) -> dict[str, Any]:
        """Return a Phase 3-style execution dict for ``run_result``.

        Accepts either a dict (already in execution-result shape) or a
        :class:`backend.agent.strategy.models.StrategyRunResult`.
        Unknown types fall back to an empty dict with a logged warning
        so downstream scoring still produces a valid result.
        """
        if isinstance(run_result, dict):
            return dict(run_result)

        state = getattr(run_result, "state", None)
        synthesis_text = ""
        chunks: list[Any] = []
        source_ids: list[str] = []
        if state is not None:
            synth = getattr(state, "synthesis_result", None)
            if synth is not None:
                synthesis_text = getattr(synth, "text", "") or ""
            retrieved = getattr(state, "retrieved_chunks", None) or []
            for chunk in retrieved:
                source_ids.append(getattr(chunk, "source_id", ""))
                if hasattr(chunk, "model_dump"):
                    chunks.append(chunk.model_dump())
                else:
                    chunks.append(chunk)

        duration = getattr(run_result, "total_duration_ms", 0.0) or 0.0
        success = getattr(run_result, "success", True)
        return {
            "synthesis_text": synthesis_text,
            "duration_ms": float(duration),
            "chunks": chunks,
            "source_ids": source_ids,
            "success": bool(success),
        }
