"""Composite scoring engine for strategy evaluation."""

from __future__ import annotations

import logging

from backend.evaluation.models import (
    CompositeResult,
    DimensionId,
    DimensionScore,
    LatencyConfig,
    ScoringProfile,
)

logger = logging.getLogger(__name__)


class CompositeScoreCalculator:
    """
    Computes composite quality scores from individual dimension scores.

    Formula: composite = (Σ score_i × weight_i) × latency_score × fatal_gate × privacy_gate

    Latency score: piecewise linear degradation
    - At or below target_ms: 1.0
    - Between target_ms and hard_limit_ms: linear interpolation to 0.0
    - At or above hard_limit_ms: 0.0
    """

    def __init__(self, profile: ScoringProfile | None = None):
        self.profile = profile or ScoringProfile()

    def calculate(
        self,
        dimension_scores: list[DimensionScore],
        latency_ms: float,
        fatal_gate: int = 1,
        privacy_gate: int = 1,
        latency_config: LatencyConfig | None = None,
    ) -> CompositeResult:
        """
        Calculate the composite score.

        Args:
            dimension_scores: List of scored dimensions.
            latency_ms: Response latency in milliseconds.
            fatal_gate: 0 if safety violation detected, 1 otherwise.
            privacy_gate: 0 if local-only policy violated, 1 otherwise.
            latency_config: Override latency parameters (defaults to profile's config).

        Returns:
            CompositeResult with all component scores and final composite.
        """
        config = latency_config or self.profile.latency_config

        weighted_sum = self.calculate_weighted_sum(dimension_scores)
        latency_score = self.calculate_latency_score(latency_ms, config)

        composite = weighted_sum * latency_score * fatal_gate * privacy_gate
        # Clamp to [0, 1]
        composite = max(0.0, min(1.0, composite))

        logger.debug(
            "Composite score: %.4f (weighted=%.4f, latency=%.4f, fatal=%d, privacy=%d)",
            composite,
            weighted_sum,
            latency_score,
            fatal_gate,
            privacy_gate,
        )

        return CompositeResult(
            dimension_scores=dimension_scores,
            weighted_sum=weighted_sum,
            latency_ms=latency_ms,
            latency_score=latency_score,
            fatal_gate=fatal_gate,
            privacy_gate=privacy_gate,
            composite_score=composite,
        )

    def calculate_latency_score(self, latency_ms: float, config: LatencyConfig | None = None) -> float:
        """
        Piecewise linear latency degradation.

        - <= target: 1.0
        - between target and hard_limit: linear from 1.0 to 0.0
        - >= hard_limit: 0.0
        """
        cfg = config or self.profile.latency_config

        if latency_ms <= cfg.target_ms:
            return 1.0
        if latency_ms >= cfg.hard_limit_ms:
            return 0.0

        # Linear interpolation between target and hard limit
        range_ms = cfg.hard_limit_ms - cfg.target_ms
        if range_ms <= 0:
            return 0.0

        elapsed_past_target = latency_ms - cfg.target_ms
        return 1.0 - (elapsed_past_target / range_ms)

    def calculate_weighted_sum(self, dimension_scores: list[DimensionScore]) -> float:
        """Sum of score * weight for all dimensions."""
        if not dimension_scores:
            return 0.0

        total = 0.0
        for ds in dimension_scores:
            total += ds.score * ds.weight
        return total

    @staticmethod
    def create_default_scores(scores_dict: dict[str, float]) -> list[DimensionScore]:
        """
        Helper to create DimensionScore list from a simple {dimension_id: score} dict.

        Uses default weights from ScoringProfile.

        Args:
            scores_dict: Mapping of dimension id string to score value (0-1).

        Returns:
            List of DimensionScore objects with default weights applied.
        """
        default_profile = ScoringProfile()
        result: list[DimensionScore] = []

        for dim_id_str, score in scores_dict.items():
            try:
                dim_id = DimensionId(dim_id_str)
            except ValueError:
                logger.warning("Unknown dimension id: %s, skipping", dim_id_str)
                continue

            weight = default_profile.weights.get(dim_id_str, 0.0)
            result.append(
                DimensionScore(
                    dimension_id=dim_id,
                    score=score,
                    weight=weight,
                )
            )

        return result
