"""Evaluation system HTTP endpoints."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.evaluation.models import EvaluationTestCase, EvaluationRun, ScoringProfile
from backend.evaluation.runner import EvaluationRunner
from backend.evaluation.test_case_manager import TestCaseManager

logger = logging.getLogger(__name__)
router = APIRouter()

# Module-level in-memory instances (no DB for now)
_runner = EvaluationRunner(db=None)
_test_case_manager = TestCaseManager(db=None)


# ═══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ═══════════════════════════════════════════════════════════════════════════════


class RunEvaluationRequest(BaseModel):
    """Request body for triggering an evaluation run."""

    strategy_id: str = Field(..., description="Strategy to evaluate")
    dataset_id: str = Field(default="default", description="Test case dataset")
    judge_model: Optional[str] = Field(default=None, description="LLM judge model identifier")


class RunEvaluationResponse(BaseModel):
    """Response for evaluation run."""

    run_count: int
    results: list[EvaluationRun]


class CompareRequest(BaseModel):
    """Request body for comparing multiple strategies."""

    strategy_ids: list[str] = Field(..., description="Strategies to compare")
    dataset_id: str = Field(default="default", description="Test case dataset")


class CompareResponse(BaseModel):
    """Response for strategy comparison."""

    comparison: dict[str, list[EvaluationRun]]


class LeaderboardResponse(BaseModel):
    """Response for leaderboard rankings."""

    rankings: list[dict]


class StrategyResultsResponse(BaseModel):
    """Response for historical strategy results."""

    strategy_id: str
    results: list[EvaluationRun]


class CreateTestCaseRequest(BaseModel):
    """Request body for creating a test case."""

    user_prompt: str = Field(..., description="User prompt for evaluation")
    expected_source_ids: list[str] = Field(default_factory=list)
    expected_topics: list[str] = Field(default_factory=list)
    scoring_profile: str = Field(default="default")
    dataset_id: str = Field(default="default")
    tags: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class TestCaseListResponse(BaseModel):
    """Response for listing test cases."""

    test_cases: list[EvaluationTestCase]


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("/run", response_model=RunEvaluationResponse)
async def run_evaluation(request: RunEvaluationRequest):
    """Trigger an evaluation run for a strategy against a dataset."""
    try:
        results = await _runner.run_evaluation(
            strategy_id=request.strategy_id,
            dataset_id=request.dataset_id,
            judge_model=request.judge_model,
        )
        return RunEvaluationResponse(
            run_count=len(results),
            results=results,
        )
    except Exception as e:
        logger.error("Evaluation run failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Evaluation run failed: {str(e)}")


@router.post("/compare", response_model=CompareResponse)
async def compare_strategies(request: CompareRequest):
    """Compare multiple strategies against a dataset."""
    try:
        if not request.strategy_ids:
            raise HTTPException(status_code=400, detail="At least one strategy_id required")

        comparison = await _runner.run_comparison(
            strategy_ids=request.strategy_ids,
            dataset_id=request.dataset_id,
        )
        return CompareResponse(comparison=comparison)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Strategy comparison failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Comparison failed: {str(e)}")


@router.get("/leaderboard", response_model=LeaderboardResponse)
async def get_leaderboard(dataset_id: str = "default", limit: int = 10):
    """Get strategy rankings by average composite score."""
    try:
        rankings = await _runner.get_leaderboard(
            dataset_id=dataset_id,
            limit=limit,
        )
        return LeaderboardResponse(rankings=rankings)
    except Exception as e:
        logger.error("Leaderboard retrieval failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Leaderboard failed: {str(e)}")


@router.get("/results/{strategy_id}", response_model=StrategyResultsResponse)
async def get_strategy_results(strategy_id: str):
    """Get historical evaluation results for a strategy."""
    try:
        results = [
            run for run in _runner._in_memory_results
            if run.strategy_id == strategy_id
        ]
        return StrategyResultsResponse(
            strategy_id=strategy_id,
            results=results,
        )
    except Exception as e:
        logger.error("Results retrieval failed for %s: %s", strategy_id, e)
        raise HTTPException(status_code=500, detail=f"Results retrieval failed: {str(e)}")


@router.post("/test-cases", response_model=EvaluationTestCase)
async def create_test_case(request: CreateTestCaseRequest):
    """Create a new evaluation test case."""
    try:
        test_case = EvaluationTestCase(
            user_prompt=request.user_prompt,
            expected_source_ids=request.expected_source_ids,
            expected_topics=request.expected_topics,
            scoring_profile=request.scoring_profile,
            dataset_id=request.dataset_id,
            tags=request.tags,
            notes=request.notes,
        )
        created = await _test_case_manager.create(test_case)
        return created
    except Exception as e:
        logger.error("Test case creation failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Test case creation failed: {str(e)}")


@router.get("/test-cases", response_model=TestCaseListResponse)
async def list_test_cases(dataset_id: str = "default", include_deprecated: bool = False):
    """List test cases in a dataset."""
    try:
        test_cases = await _test_case_manager.list_by_dataset(
            dataset_id=dataset_id,
            include_deprecated=include_deprecated,
        )
        return TestCaseListResponse(test_cases=test_cases)
    except Exception as e:
        logger.error("Test case listing failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Test case listing failed: {str(e)}")


@router.delete("/test-cases/{test_case_id}")
async def deprecate_test_case(test_case_id: str):
    """Deprecate (soft-delete) a test case."""
    try:
        await _test_case_manager.deprecate(test_case_id)
        return {"status": "deprecated"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Test case deprecation failed for %s: %s", test_case_id, e)
        raise HTTPException(status_code=500, detail=f"Deprecation failed: {str(e)}")
