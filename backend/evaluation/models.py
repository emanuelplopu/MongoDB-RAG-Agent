"""Evaluation system Pydantic models for Strategy OS quality benchmarking.

Defines typed models for multi-dimensional scoring, test case management,
judge calibration, and contradiction detection.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════════


class DimensionId(str, Enum):
    """Quality evaluation dimensions (7 total)."""

    GROUNDEDNESS = "groundedness"
    CITATION_QUALITY = "citation_quality"
    DIRECTNESS = "directness"
    COMPLETENESS = "completeness"
    FORMAT_ADHERENCE = "format_adherence"
    DOMAIN_VALUE = "domain_value"
    CONCISENESS = "conciseness"


class CalibrationStatus(str, Enum):
    """Judge calibration gate status."""

    PASSED = "passed"
    REJECTED = "rejected"
    PENDING = "pending"


class ContradictionAttribution(str, Enum):
    """Source of contradiction between responses."""

    STRATEGY_RELATED = "strategy_related"
    DATA_RELATED = "data_related"
    MODEL_VARIANCE = "model_variance"


# ═══════════════════════════════════════════════════════════════════════════════
# Scoring Models
# ═══════════════════════════════════════════════════════════════════════════════


class DimensionScore(BaseModel):
    """Score for a single evaluation dimension."""

    dimension_id: DimensionId
    score: float = Field(ge=0.0, le=1.0, description="Normalized score 0-1")
    weight: float = Field(ge=0.0, le=1.0, description="Weight in composite formula")
    evidence: Optional[str] = Field(default=None, description="Justification for score")


class LatencyConfig(BaseModel):
    """Latency scoring configuration per profile."""

    target_ms: float = Field(default=5000.0, description="Target latency (score=1.0 at or below)")
    hard_limit_ms: float = Field(default=30000.0, description="Hard limit (score=0.0 at or above)")
    # Piecewise linear degradation between target and hard_limit


class ScoringProfile(BaseModel):
    """Configurable scoring weights per tenant/capability."""

    profile_id: str = Field(default="default")
    weights: dict[str, float] = Field(default_factory=lambda: {
        "groundedness": 0.25,
        "citation_quality": 0.20,
        "directness": 0.10,
        "completeness": 0.15,
        "format_adherence": 0.15,
        "domain_value": 0.10,
        "conciseness": 0.05,
    })
    latency_config: LatencyConfig = Field(default_factory=LatencyConfig)


class CompositeResult(BaseModel):
    """Full composite evaluation result."""

    dimension_scores: list[DimensionScore] = Field(default_factory=list)
    weighted_sum: float = Field(default=0.0, description="Sum of score*weight across dimensions")
    latency_ms: float = Field(default=0.0)
    latency_score: float = Field(default=1.0, ge=0.0, le=1.0)
    fatal_gate: int = Field(default=1, description="0 or 1 — safety violation gate")
    privacy_gate: int = Field(default=1, description="0 or 1 — local-only enforcement gate")
    composite_score: float = Field(default=0.0, ge=0.0, le=1.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Test Case & Run Models
# ═══════════════════════════════════════════════════════════════════════════════


class EvaluationTestCase(BaseModel):
    """Versioned test case for strategy evaluation."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    version: int = Field(default=1, ge=1)
    user_prompt: str
    expected_source_ids: list[str] = Field(default_factory=list)
    expected_topics: list[str] = Field(default_factory=list)
    scoring_profile: str = Field(default="default")
    dataset_id: str = Field(default="default")
    tags: list[str] = Field(default_factory=list)
    notes: Optional[str] = None
    deprecated_at: Optional[datetime] = None
    is_stale: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class EvaluationRun(BaseModel):
    """Single evaluation run result."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    test_case_id: str
    test_case_version: int
    strategy_id: str
    result: CompositeResult = Field(default_factory=CompositeResult)
    judge_model: Optional[str] = None
    judge_variance: Optional[float] = None  # max spread across repeated judge runs
    duration_ms: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# Judge Calibration
# ═══════════════════════════════════════════════════════════════════════════════


class JudgeCalibration(BaseModel):
    """Calibration record for a judge model on a scoring profile."""

    calibration_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    profile_id: str
    judge_model: str
    dimension_correlations: dict[str, float] = Field(default_factory=dict)  # dimension_id -> Pearson r
    overall_correlation: float = 0.0
    gate_status: CalibrationStatus = CalibrationStatus.PENDING
    seed_case_count: int = 0
    variance_mean: float = 0.0  # mean variance across repeated runs
    calibrated_at: datetime = Field(default_factory=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# Contradiction Detection
# ═══════════════════════════════════════════════════════════════════════════════


class ClaimExtraction(BaseModel):
    """Extracted claim from a response for contradiction detection."""

    claim_text: str
    source_span: Optional[str] = None  # snippet of response text containing claim
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class Contradiction(BaseModel):
    """A detected contradiction between two claims."""

    claim_a: ClaimExtraction
    claim_b: ClaimExtraction
    conflict_type: str = Field(default="semantic")  # semantic, factual, temporal
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    attribution: ContradictionAttribution = ContradictionAttribution.MODEL_VARIANCE


class ContradictionResult(BaseModel):
    """Result of contradiction detection for a session turn."""

    session_id: str
    turn: int
    contradictions: list[Contradiction] = Field(default_factory=list)
    has_contradictions: bool = False
    detection_duration_ms: float = 0.0
    timed_out: bool = False
    checked_at: datetime = Field(default_factory=datetime.utcnow)
