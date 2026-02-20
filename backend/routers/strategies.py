"""Strategies API Router - Endpoints for strategy management and A/B testing.

This module provides REST API endpoints for:
- Listing available strategies
- Getting strategy details
- Comparing strategy performance
- Managing A/B tests
- LLM-based response comparison scoring
"""

import json
import logging
from datetime import timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query, Request

from pydantic import BaseModel

from backend.agent.strategies.registry import StrategyRegistry
from backend.agent.strategies.base import StrategyDomain
from backend.agent.strategies.metrics import get_strategy_metrics
from backend.core.llm_providers import get_llm_client

logger = logging.getLogger(__name__)

router = APIRouter()


# ============== Response Models ==============

class StrategyInfo(BaseModel):
    """Strategy information response."""
    id: str
    name: str
    version: str
    description: str
    domains: List[str]
    tags: List[str]
    is_default: bool
    is_legacy: bool
    author: str


class StrategyDetail(StrategyInfo):
    """Detailed strategy information including config."""
    config: dict
    prompts_preview: dict


class StrategyStats(BaseModel):
    """Strategy statistics for A/B testing."""
    strategy_id: str
    execution_count: int
    avg_latency_ms: float
    median_latency_ms: float
    avg_iterations: float
    avg_confidence: float
    quality_score: float
    quality_distribution: dict
    avg_user_feedback: Optional[float]
    feedback_count: int


class StrategyComparison(BaseModel):
    """Strategy comparison results."""
    strategy_a: str
    strategy_b: str
    filters: dict
    comparison: dict
    winner: Optional[str]
    confidence_in_winner: Optional[str]


# ============== Endpoints ==============

@router.get("", response_model=List[StrategyInfo])
async def list_strategies(
    domain: Optional[str] = Query(None, description="Filter by domain (general, software_dev, legal, hr)")
):
    """List all available strategies.
    
    Args:
        domain: Optional domain filter
    
    Returns:
        List of strategy information
    """
    # Convert domain string to enum if provided
    domain_filter = None
    if domain:
        try:
            domain_filter = StrategyDomain(domain)
        except ValueError:
            raise HTTPException(400, f"Invalid domain: {domain}")
    
    strategies = StrategyRegistry.list_strategies(domain=domain_filter)
    
    return [
        StrategyInfo(
            id=meta.id,
            name=meta.name,
            version=meta.version,
            description=meta.description,
            domains=[d.value if hasattr(d, 'value') else str(d) for d in meta.domains],
            tags=meta.tags,
            is_default=meta.is_default,
            is_legacy=meta.is_legacy,
            author=meta.author
        )
        for meta in strategies
    ]


@router.get("/default", response_model=StrategyInfo)
async def get_default_strategy():
    """Get the default strategy.
    
    Returns:
        Default strategy information
    
    Raises:
        HTTPException 503: If no strategies are available
    """
    try:
        strategy = StrategyRegistry.get_default()
    except ValueError as e:
        raise HTTPException(503, f"No strategies available: {e}")
    except Exception as e:
        raise HTTPException(500, f"Failed to get default strategy: {e}")
    
    meta = strategy.metadata
    
    return StrategyInfo(
        id=meta.id,
        name=meta.name,
        version=meta.version,
        description=meta.description,
        domains=[d.value if hasattr(d, 'value') else str(d) for d in meta.domains],
        tags=meta.tags,
        is_default=meta.is_default,
        is_legacy=meta.is_legacy,
        author=meta.author
    )


@router.get("/{strategy_id}", response_model=StrategyDetail)
async def get_strategy(strategy_id: str):
    """Get detailed information about a specific strategy.
    
    Args:
        strategy_id: Strategy identifier
    
    Returns:
        Detailed strategy information
    
    Raises:
        HTTPException 400: If strategy_id is empty
        HTTPException 404: If strategy not found
    """
    if not strategy_id or not strategy_id.strip():
        raise HTTPException(400, "Strategy ID cannot be empty")
    
    try:
        strategy = StrategyRegistry.get(strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")
    except Exception as e:
        raise HTTPException(500, f"Failed to get strategy: {e}")
    
    meta = strategy.metadata
    config = strategy.config
    
    # Get prompt previews safely (handle short prompts)
    def safe_preview(prompt: str, max_len: int = 200) -> str:
        if not prompt:
            return "(empty)"
        if len(prompt) <= max_len:
            return prompt
        return prompt[:max_len] + "..."
    
    prompts_preview = {
        "analyze": safe_preview(strategy.get_analyze_prompt()),
        "plan": safe_preview(strategy.get_plan_prompt()),
        "evaluate": safe_preview(strategy.get_evaluate_prompt()),
        "synthesize": safe_preview(strategy.get_synthesize_prompt())
    }
    
    return StrategyDetail(
        id=meta.id,
        name=meta.name,
        version=meta.version,
        description=meta.description,
        domains=[d.value if hasattr(d, 'value') else str(d) for d in meta.domains],
        tags=meta.tags,
        is_default=meta.is_default,
        is_legacy=meta.is_legacy,
        author=meta.author,
        config={
            "max_iterations": config.max_iterations,
            "confidence_threshold": config.confidence_threshold,
            "early_exit_enabled": config.early_exit_enabled,
            "cross_search_boost": config.cross_search_boost,
            "content_length_penalty": config.content_length_penalty,
            "custom_params": config.custom_params
        },
        prompts_preview=prompts_preview
    )


@router.get("/{strategy_id}/metrics", response_model=StrategyStats)
async def get_strategy_metrics_endpoint(
    strategy_id: str,
    hours: Optional[int] = Query(None, ge=1, le=8760, description="Time window in hours (1-8760)"),
    domain: Optional[str] = Query(None, description="Filter by domain")
):
    """Get performance metrics for a strategy.
    
    Args:
        strategy_id: Strategy identifier
        hours: Optional time window in hours (1-8760)
        domain: Optional domain filter
    
    Returns:
        Strategy performance statistics
    
    Raises:
        HTTPException 400: If strategy_id is empty
        HTTPException 404: If strategy not found
    """
    if not strategy_id or not strategy_id.strip():
        raise HTTPException(400, "Strategy ID cannot be empty")
    
    # Verify strategy exists
    try:
        StrategyRegistry.get(strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")
    except Exception as e:
        raise HTTPException(500, f"Failed to verify strategy: {e}")
    
    try:
        metrics = get_strategy_metrics()
        time_window = timedelta(hours=hours) if hours else None
        
        stats = metrics.get_strategy_stats(strategy_id, time_window=time_window, domain=domain)
    except Exception as e:
        raise HTTPException(500, f"Failed to get metrics: {e}")
    
    return StrategyStats(
        strategy_id=stats.get("strategy_id", strategy_id),
        execution_count=stats.get("execution_count", 0),
        avg_latency_ms=stats.get("avg_latency_ms", 0.0),
        median_latency_ms=stats.get("median_latency_ms", stats.get("avg_latency_ms", 0.0)),
        avg_iterations=stats.get("avg_iterations", 0.0),
        avg_confidence=stats.get("avg_confidence", 0.0),
        quality_score=stats.get("quality_score", 0.0),
        quality_distribution=stats.get("quality_distribution", {}),
        avg_user_feedback=stats.get("avg_user_feedback"),
        feedback_count=stats.get("feedback_count", 0)
    )


class CompareRequest(BaseModel):
    """Request body for strategy comparison."""
    strategy_a: str
    strategy_b: str
    hours: Optional[int] = None
    domain: Optional[str] = None


@router.post("/compare", response_model=StrategyComparison)
async def compare_strategies_endpoint(request: CompareRequest):
    """Compare two strategies' performance.
    
    Args:
        request: Comparison request with strategy IDs and filters
    
    Returns:
        Comparison results with statistical analysis
    
    Raises:
        HTTPException 400: If strategy IDs are empty or same
        HTTPException 404: If strategy not found
    """
    # Validate inputs
    if not request.strategy_a or not request.strategy_a.strip():
        raise HTTPException(400, "strategy_a cannot be empty")
    if not request.strategy_b or not request.strategy_b.strip():
        raise HTTPException(400, "strategy_b cannot be empty")
    if request.strategy_a == request.strategy_b:
        raise HTTPException(400, "Cannot compare a strategy with itself")
    if request.hours is not None and (request.hours < 1 or request.hours > 8760):
        raise HTTPException(400, "hours must be between 1 and 8760")
    
    # Verify strategies exist
    for sid in [request.strategy_a, request.strategy_b]:
        try:
            StrategyRegistry.get(sid)
        except KeyError:
            raise HTTPException(404, f"Strategy not found: {sid}")
        except Exception as e:
            raise HTTPException(500, f"Failed to verify strategy '{sid}': {e}")
    
    try:
        metrics = get_strategy_metrics()
        time_window = timedelta(hours=request.hours) if request.hours else None
        
        comparison = metrics.compare_strategies(
            strategy_a=request.strategy_a,
            strategy_b=request.strategy_b,
            time_window=time_window,
            domain=request.domain
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to compare strategies: {e}")
    
    return StrategyComparison(
        strategy_a=comparison["strategy_a"],
        strategy_b=comparison["strategy_b"],
        filters=comparison["filters"],
        comparison=comparison.get("comparison", {}),
        winner=comparison.get("comparison", {}).get("winner"),
        confidence_in_winner=comparison.get("comparison", {}).get("confidence_in_winner")
    )


@router.get("/for-domain/{domain}", response_model=StrategyInfo)
async def get_strategy_for_domain(domain: str):
    """Get the best strategy for a specific domain.
    
    Args:
        domain: Domain identifier (software_dev, legal, hr, general)
    
    Returns:
        Recommended strategy for the domain
    """
    try:
        domain_enum = StrategyDomain(domain)
    except ValueError:
        raise HTTPException(400, f"Invalid domain: {domain}")
    
    strategy = StrategyRegistry.get_for_domain(domain_enum)
    meta = strategy.metadata
    
    return StrategyInfo(
        id=meta.id,
        name=meta.name,
        version=meta.version,
        description=meta.description,
        domains=[d.value if hasattr(d, 'value') else str(d) for d in meta.domains],
        tags=meta.tags,
        is_default=meta.is_default,
        is_legacy=meta.is_legacy,
        author=meta.author
    )


class AutoDetectRequest(BaseModel):
    """Request for auto-detecting strategy from query."""
    query: str


@router.post("/auto-detect", response_model=StrategyInfo)
async def auto_detect_strategy(request: AutoDetectRequest):
    """Auto-detect the best strategy for a given query.
    
    Args:
        request: Request containing the query to analyze
    
    Returns:
        Recommended strategy for the query
    """
    strategy = StrategyRegistry.auto_detect(request.query)
    meta = strategy.metadata
    
    return StrategyInfo(
        id=meta.id,
        name=meta.name,
        version=meta.version,
        description=meta.description,
        domains=[d.value if hasattr(d, 'value') else str(d) for d in meta.domains],
        tags=meta.tags,
        is_default=meta.is_default,
        is_legacy=meta.is_legacy,
        author=meta.author
    )


@router.get("/metrics/all", response_model=List[StrategyStats])
async def get_all_metrics(
    hours: Optional[int] = Query(None, description="Time window in hours"),
    domain: Optional[str] = Query(None, description="Filter by domain")
):
    """Get metrics for all strategies.
    
    Args:
        hours: Optional time window in hours
        domain: Optional domain filter
    
    Returns:
        List of strategy statistics sorted by quality score
    """
    metrics = get_strategy_metrics()
    time_window = timedelta(hours=hours) if hours else None
    
    all_stats = metrics.get_all_strategy_stats(time_window=time_window, domain=domain)
    
    return [
        StrategyStats(
            strategy_id=stats["strategy_id"],
            execution_count=stats["execution_count"],
            avg_latency_ms=stats["avg_latency_ms"],
            median_latency_ms=stats.get("median_latency_ms", stats["avg_latency_ms"]),
            avg_iterations=stats["avg_iterations"],
            avg_confidence=stats["avg_confidence"],
            quality_score=stats["quality_score"],
            quality_distribution=stats["quality_distribution"],
            avg_user_feedback=stats["avg_user_feedback"],
            feedback_count=stats.get("feedback_count", 0)
        )
        for stats in all_stats
    ]


class FeedbackRequest(BaseModel):
    """Request for recording user feedback."""
    strategy_id: str
    session_id: str
    score: int  # 1-5
    text: Optional[str] = None


@router.post("/feedback")
async def record_feedback(request: FeedbackRequest):
    """Record user feedback for a strategy execution.
    
    Args:
        request: Feedback request with score and optional text
    
    Returns:
        Success message
    """
    if not 1 <= request.score <= 5:
        raise HTTPException(400, "Feedback score must be between 1 and 5")
    
    metrics = get_strategy_metrics()
    await metrics.record_user_feedback(
        strategy_id=request.strategy_id,
        session_id=request.session_id,
        feedback_score=request.score,
        feedback_text=request.text
    )
    
    return {"status": "success", "message": "Feedback recorded"}
