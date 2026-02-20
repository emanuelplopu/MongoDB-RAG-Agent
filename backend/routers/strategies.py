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
from backend.core.llm_providers import get_llm_manager

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


# ============== LLM-Based A/B Comparison ==============

class ABCompareResponsesRequest(BaseModel):
    """Request for LLM-based comparison of two responses."""
    query: str
    response_a: str
    response_b: str
    strategy_a: str
    strategy_b: str
    latency_a_ms: float
    latency_b_ms: float


class ResponseScore(BaseModel):
    """Score for a single response on various metrics."""
    quality: float  # 0-10
    hallucination: float  # 0-10 (lower is better, more hallucination = lower score)
    readability: float  # 0-10
    factuality: float  # 0-10
    relevance: float  # 0-10 (how on-point)
    overall: float  # 0-10 weighted average


class ABCompareResponsesResult(BaseModel):
    """Result of LLM-based comparison."""
    query: str
    strategy_a: str
    strategy_b: str
    scores_a: ResponseScore
    scores_b: ResponseScore
    speed_winner: str  # strategy_a or strategy_b
    quality_winner: str  # strategy_a or strategy_b
    overall_winner: str  # strategy_a or strategy_b
    analysis: str  # LLM's detailed analysis
    recommendation: str  # Brief recommendation


AB_COMPARE_PROMPT = """You are an expert evaluator comparing two AI-generated responses to a user query.

## User Query
{query}

## Response A (Strategy: {strategy_a})
{response_a}

## Response B (Strategy: {strategy_b})
{response_b}

## Performance Data
- Response A latency: {latency_a_ms:.0f}ms
- Response B latency: {latency_b_ms:.0f}ms

## Your Task
Evaluate both responses on the following metrics (score 0-10 for each):

1. **Quality** (0-10): Overall quality of the response - coherence, completeness, depth
2. **Hallucination** (0-10): How free of made-up/false information (10 = no hallucination, 0 = full of hallucinations)
3. **Readability** (0-10): How easy to read and understand - formatting, structure, clarity
4. **Factuality** (0-10): How accurate and factual the information is
5. **Relevance** (0-10): How on-point and relevant to the user's actual question

## Output Format
Return ONLY valid JSON with this exact structure:
{{
    "scores_a": {{
        "quality": <number>,
        "hallucination": <number>,
        "readability": <number>,
        "factuality": <number>,
        "relevance": <number>
    }},
    "scores_b": {{
        "quality": <number>,
        "hallucination": <number>,
        "readability": <number>,
        "factuality": <number>,
        "relevance": <number>
    }},
    "analysis": "<detailed comparison analysis explaining your scoring>",
    "recommendation": "<brief recommendation on which strategy to use and why>"
}}

Be objective and thorough in your evaluation. Consider that speed matters but quality is paramount."""


@router.post("/ab-compare-responses", response_model=ABCompareResponsesResult)
async def ab_compare_responses(request: ABCompareResponsesRequest, req: Request):
    """Use LLM to compare two strategy responses and score them.
    
    This endpoint takes two responses generated by different strategies
    and uses an LLM to evaluate them on multiple quality metrics.
    
    Args:
        request: The comparison request with query and both responses
    
    Returns:
        Detailed scoring and analysis of both responses
    """
    if not request.query or not request.query.strip():
        raise HTTPException(400, "Query cannot be empty")
    if not request.response_a or not request.response_a.strip():
        raise HTTPException(400, "Response A cannot be empty")
    if not request.response_b or not request.response_b.strip():
        raise HTTPException(400, "Response B cannot be empty")
    
    # Get LLM client using the provider manager
    try:
        llm_manager = get_llm_manager()
        llm_client = llm_manager.get_orchestrator_client()
    except Exception as e:
        logger.error(f"Failed to get LLM client: {e}")
        raise HTTPException(500, f"Failed to initialize LLM: {e}")
    
    # Build the comparison prompt
    prompt = AB_COMPARE_PROMPT.format(
        query=request.query,
        strategy_a=request.strategy_a,
        response_a=request.response_a,
        strategy_b=request.strategy_b,
        response_b=request.response_b,
        latency_a_ms=request.latency_a_ms,
        latency_b_ms=request.latency_b_ms,
    )
    
    try:
        # Call LLM for evaluation using the LLMClient interface
        messages = [
            {"role": "system", "content": "You are an expert AI response evaluator. Return only valid JSON."},
            {"role": "user", "content": prompt}
        ]
        
        # Use complete_json which handles JSON parsing automatically
        evaluation = await llm_client.complete_json(
            messages=messages,
            temperature=0.3,  # Lower temperature for more consistent scoring
            max_tokens=2000,
        )
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}")
        raise HTTPException(500, "LLM returned invalid JSON response")
    except Exception as e:
        logger.error(f"LLM evaluation failed: {e}")
        raise HTTPException(500, f"Failed to evaluate responses: {e}")
    
    # Calculate overall scores (weighted average)
    def calc_overall(scores: dict) -> float:
        weights = {
            "quality": 0.25,
            "hallucination": 0.25,
            "readability": 0.15,
            "factuality": 0.20,
            "relevance": 0.15,
        }
        total = sum(scores.get(k, 0) * v for k, v in weights.items())
        return round(total, 2)
    
    scores_a = evaluation.get("scores_a", {})
    scores_b = evaluation.get("scores_b", {})
    
    overall_a = calc_overall(scores_a)
    overall_b = calc_overall(scores_b)
    
    # Determine winners
    speed_winner = request.strategy_a if request.latency_a_ms <= request.latency_b_ms else request.strategy_b
    quality_winner = request.strategy_a if overall_a >= overall_b else request.strategy_b
    
    # Overall winner considers both speed and quality (quality weighted 70%, speed 30%)
    speed_score_a = 10 * (1 - request.latency_a_ms / max(request.latency_a_ms, request.latency_b_ms, 1))
    speed_score_b = 10 * (1 - request.latency_b_ms / max(request.latency_a_ms, request.latency_b_ms, 1))
    
    combined_a = 0.7 * overall_a + 0.3 * speed_score_a
    combined_b = 0.7 * overall_b + 0.3 * speed_score_b
    
    overall_winner = request.strategy_a if combined_a >= combined_b else request.strategy_b
    
    return ABCompareResponsesResult(
        query=request.query,
        strategy_a=request.strategy_a,
        strategy_b=request.strategy_b,
        scores_a=ResponseScore(
            quality=scores_a.get("quality", 0),
            hallucination=scores_a.get("hallucination", 0),
            readability=scores_a.get("readability", 0),
            factuality=scores_a.get("factuality", 0),
            relevance=scores_a.get("relevance", 0),
            overall=overall_a,
        ),
        scores_b=ResponseScore(
            quality=scores_b.get("quality", 0),
            hallucination=scores_b.get("hallucination", 0),
            readability=scores_b.get("readability", 0),
            factuality=scores_b.get("factuality", 0),
            relevance=scores_b.get("relevance", 0),
            overall=overall_b,
        ),
        speed_winner=speed_winner,
        quality_winner=quality_winner,
        overall_winner=overall_winner,
        analysis=evaluation.get("analysis", "No analysis provided"),
        recommendation=evaluation.get("recommendation", "No recommendation provided"),
    )
