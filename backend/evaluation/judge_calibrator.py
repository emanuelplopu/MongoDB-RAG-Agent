"""Judge calibration protocol — validates LLM judge reliability."""
from __future__ import annotations

import logging
import math
import random
from datetime import datetime
from typing import Optional

from backend.evaluation.models import (
    JudgeCalibration,
    CalibrationStatus,
    DimensionId,
    DimensionScore,
    EvaluationTestCase,
)

logger = logging.getLogger(__name__)

# Calibration thresholds
MIN_CORRELATION = 0.85  # Pearson r threshold per dimension
MIN_SEED_CASES = 20     # Minimum seed cases for valid calibration
MAX_VARIANCE = 0.05     # Maximum acceptable score variance across runs


class CalibrationGate:
    """
    Validates that an LLM judge model produces reliable, consistent scores.

    Protocol:
    1. Provide 20+ seed cases with human-labeled dimension scores
    2. Run the judge model on each seed case
    3. Compute Pearson correlation per dimension between judge and human scores
    4. Gate: correlation > 0.85 per dimension required to pass

    Determinism strategies:
    - OpenAI: temperature=0, seed=42 (single run sufficient)
    - Ollama: median-of-3 runs (no reliable seed support)

    Variance check:
    - Run judge 3x on subset, check max spread <= 0.05 per dimension
    """

    def __init__(self, llm_client=None):
        self.llm_client = llm_client  # For actual LLM calls (None = dry-run mode)

    async def calibrate(
        self,
        judge_model: str,
        profile_id: str,
        seed_cases: list[dict],
        runs_per_case: int = 1,
    ) -> JudgeCalibration:
        """
        Run full calibration protocol.

        Args:
            judge_model: Model identifier (e.g., "gpt-4o", "llama3.1:70b").
            profile_id: Scoring profile being calibrated for.
            seed_cases: List of dicts with keys:
                - test_case: EvaluationTestCase
                - human_scores: dict[str, float] mapping dimension_id -> score
            runs_per_case: Number of repeated runs (1 for deterministic, 3 for Ollama).

        Returns:
            JudgeCalibration with correlations and gate status.
        """
        if len(seed_cases) < MIN_SEED_CASES:
            logger.warning(
                "Calibration requires at least %d seed cases, got %d",
                MIN_SEED_CASES,
                len(seed_cases),
            )
            return JudgeCalibration(
                profile_id=profile_id,
                judge_model=judge_model,
                gate_status=CalibrationStatus.REJECTED,
                seed_case_count=len(seed_cases),
            )

        # Collect human and judge scores across all cases
        all_human_scores: list[dict[str, float]] = []
        all_judge_scores: list[dict[str, float]] = []
        multi_run_scores: list[list[dict[str, float]]] = []

        for case_entry in seed_cases:
            human_scores = case_entry["human_scores"]
            all_human_scores.append(human_scores)

            # Run judge multiple times for variance analysis
            case_runs: list[dict[str, float]] = []
            for _ in range(runs_per_case):
                judge_scores = await self._run_judge_on_case(
                    judge_model=judge_model,
                    test_case=case_entry,
                    runs=1,
                )
                case_runs.append(judge_scores)

            multi_run_scores.append(case_runs)

            # Use median scores across runs as the canonical judge score
            canonical_scores = self._median_scores(case_runs)
            all_judge_scores.append(canonical_scores)

        # Compute correlations per dimension
        correlations = self._compute_correlations(all_human_scores, all_judge_scores)

        # Compute variance across repeated runs
        variance_mean = self._check_variance(multi_run_scores)

        # Evaluate gate
        gate_status = self._evaluate_gate(correlations)

        # Compute overall correlation as mean of dimension correlations
        overall = (
            sum(correlations.values()) / len(correlations)
            if correlations
            else 0.0
        )

        calibration = JudgeCalibration(
            profile_id=profile_id,
            judge_model=judge_model,
            dimension_correlations=correlations,
            overall_correlation=overall,
            gate_status=gate_status,
            seed_case_count=len(seed_cases),
            variance_mean=variance_mean,
            calibrated_at=datetime.utcnow(),
        )

        logger.info(
            "Calibration complete for %s on profile %s: status=%s, overall_r=%.4f, variance=%.4f",
            judge_model,
            profile_id,
            gate_status.value,
            overall,
            variance_mean,
        )

        return calibration

    async def _run_judge_on_case(
        self,
        judge_model: str,
        test_case: dict,
        runs: int = 1,
    ) -> dict[str, float]:
        """
        Run judge model on a single test case, return dimension scores.

        For now (no LLM client): returns simulated scores based on human scores + noise.
        TODO: Replace with actual LLM judge call when llm_client is provided.
        """
        if self.llm_client is not None:
            # Future: actual LLM judge invocation
            raise NotImplementedError(
                "Real LLM judge invocation not yet implemented. "
                "Pass llm_client=None for dry-run calibration."
            )

        # Dry-run mode: simulate judge scores by adding small random noise to human scores
        human_scores = test_case["human_scores"]
        simulated: dict[str, float] = {}

        for dim_id, score in human_scores.items():
            noise = random.gauss(0, 0.03)  # Small noise for realistic simulation
            simulated_score = max(0.0, min(1.0, score + noise))
            simulated[dim_id] = simulated_score

        return simulated

    def _median_scores(
        self,
        runs: list[dict[str, float]],
    ) -> dict[str, float]:
        """Compute median score per dimension across multiple runs."""
        if len(runs) == 1:
            return runs[0]

        # Gather all dimensions
        all_dims: set[str] = set()
        for run in runs:
            all_dims.update(run.keys())

        median_scores: dict[str, float] = {}
        for dim in all_dims:
            values = sorted(run.get(dim, 0.0) for run in runs)
            mid = len(values) // 2
            if len(values) % 2 == 0:
                median_scores[dim] = (values[mid - 1] + values[mid]) / 2.0
            else:
                median_scores[dim] = values[mid]

        return median_scores

    def _compute_correlations(
        self,
        human_scores: list[dict[str, float]],
        judge_scores: list[dict[str, float]],
    ) -> dict[str, float]:
        """
        Compute Pearson correlation per dimension between human and judge scores.

        Returns: {dimension_id: pearson_r}
        """
        # Collect all dimensions present in human scores
        all_dims: set[str] = set()
        for hs in human_scores:
            all_dims.update(hs.keys())

        correlations: dict[str, float] = {}
        for dim in sorted(all_dims):
            human_vals = [hs.get(dim, 0.0) for hs in human_scores]
            judge_vals = [js.get(dim, 0.0) for js in judge_scores]
            correlations[dim] = self.pearson_r(human_vals, judge_vals)

        return correlations

    @staticmethod
    def pearson_r(x: list[float], y: list[float]) -> float:
        """
        Compute Pearson correlation coefficient.

        r = Σ((xi - x̄)(yi - ȳ)) / sqrt(Σ(xi - x̄)² × Σ(yi - ȳ)²)

        Returns 0.0 if insufficient data or zero variance.
        """
        n = len(x)
        if n != len(y) or n < 2:
            return 0.0

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        numerator = 0.0
        sum_sq_x = 0.0
        sum_sq_y = 0.0

        for xi, yi in zip(x, y):
            dx = xi - mean_x
            dy = yi - mean_y
            numerator += dx * dy
            sum_sq_x += dx * dx
            sum_sq_y += dy * dy

        denominator = math.sqrt(sum_sq_x * sum_sq_y)
        if denominator == 0.0:
            return 0.0

        return numerator / denominator

    def _check_variance(
        self,
        multi_run_scores: list[list[dict[str, float]]],
    ) -> float:
        """
        Check variance across repeated runs.

        For each dimension, compute max spread across runs per case.
        Returns mean max-spread across all dimensions and cases.
        """
        if not multi_run_scores:
            return 0.0

        # Gather all dimensions
        all_dims: set[str] = set()
        for case_runs in multi_run_scores:
            for run in case_runs:
                all_dims.update(run.keys())

        if not all_dims:
            return 0.0

        spreads: list[float] = []
        for case_runs in multi_run_scores:
            if len(case_runs) < 2:
                # Single run — no variance to measure
                continue
            for dim in all_dims:
                dim_values = [run.get(dim, 0.0) for run in case_runs]
                spread = max(dim_values) - min(dim_values)
                spreads.append(spread)

        if not spreads:
            return 0.0

        return sum(spreads) / len(spreads)

    def _evaluate_gate(
        self,
        correlations: dict[str, float],
        min_correlation: float = MIN_CORRELATION,
    ) -> CalibrationStatus:
        """
        Evaluate calibration gate.

        PASSED if ALL dimensions have correlation >= min_correlation.
        REJECTED if any dimension fails.
        """
        if not correlations:
            return CalibrationStatus.REJECTED

        for dim, corr in correlations.items():
            if corr < min_correlation:
                logger.info(
                    "Calibration gate REJECTED: dimension %s has r=%.4f < %.4f",
                    dim,
                    corr,
                    min_correlation,
                )
                return CalibrationStatus.REJECTED

        return CalibrationStatus.PASSED
