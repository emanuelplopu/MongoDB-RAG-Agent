"""Strategy Metrics - Performance tracking for A/B testing.

This module provides metrics collection and analysis for comparing
different strategy performance in the agent system.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from collections import defaultdict
import statistics

logger = logging.getLogger(__name__)


class StrategyMetrics:
    """Collect and analyze strategy performance metrics for A/B testing.
    
    Stores metrics in memory for quick access, with optional persistence
    to MongoDB for long-term analysis.
    """
    
    def __init__(self, persist_to_db: bool = False, mongo_client=None):
        """Initialize metrics collector.
        
        Args:
            persist_to_db: Whether to persist metrics to MongoDB
            mongo_client: Optional MongoDB client for persistence
        """
        self.persist_to_db = persist_to_db
        self._mongo_client = mongo_client
        
        # In-memory metrics storage
        # Structure: {strategy_id: [execution_records]}
        self._executions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        
        # Aggregated stats cache
        self._stats_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = timedelta(minutes=5)
        self._cache_timestamps: Dict[str, datetime] = {}
    
    async def record_execution(
        self,
        strategy_id: str,
        query_type: str,
        domain: str,
        latency_ms: float,
        iterations: int,
        result_count: int,
        result_quality: str,
        confidence: float,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_feedback: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Record metrics for a single strategy execution.
        
        Args:
            strategy_id: ID of the strategy used
            query_type: Type of query (FACTUAL, EXPLORATORY, etc.)
            domain: Domain context (software_dev, legal, hr, general)
            latency_ms: Total execution time in milliseconds
            iterations: Number of search iterations
            result_count: Number of results returned
            result_quality: Quality assessment (excellent, good, partial, empty)
            confidence: Confidence score (0-1)
            user_id: Optional user identifier
            session_id: Optional session identifier
            user_feedback: Optional user feedback score (1-5)
            metadata: Optional additional metadata
        
        Returns:
            The recorded execution record
        """
        record = {
            "strategy_id": strategy_id,
            "timestamp": datetime.utcnow(),
            "query_type": query_type,
            "domain": domain,
            "latency_ms": latency_ms,
            "iterations": iterations,
            "result_count": result_count,
            "result_quality": result_quality,
            "confidence": confidence,
            "user_id": user_id,
            "session_id": session_id,
            "user_feedback": user_feedback,
            "metadata": metadata or {}
        }
        
        # Store in memory
        self._executions[strategy_id].append(record)
        
        # Invalidate cache for this strategy
        if strategy_id in self._stats_cache:
            del self._stats_cache[strategy_id]
        
        # Persist to database if enabled
        if self.persist_to_db and self._mongo_client:
            try:
                db = self._mongo_client["rag_metrics"]
                await db.strategy_executions.insert_one(record.copy())
            except Exception as e:
                logger.error(f"Failed to persist metrics: {e}")
        
        logger.debug(f"Recorded execution for strategy {strategy_id}: {result_quality}, {latency_ms:.0f}ms")
        
        return record
    
    async def record_user_feedback(
        self,
        strategy_id: str,
        session_id: str,
        feedback_score: int,
        feedback_text: Optional[str] = None
    ):
        """Record user feedback for a strategy execution.
        
        Args:
            strategy_id: ID of the strategy
            session_id: Session identifier
            feedback_score: User feedback score (1-5)
            feedback_text: Optional feedback text
        """
        # Find and update the execution record
        for record in reversed(self._executions[strategy_id]):
            if record.get("session_id") == session_id:
                record["user_feedback"] = feedback_score
                record["feedback_text"] = feedback_text
                
                # Update in database if persisting
                if self.persist_to_db and self._mongo_client:
                    try:
                        db = self._mongo_client["rag_metrics"]
                        await db.strategy_executions.update_one(
                            {"strategy_id": strategy_id, "session_id": session_id},
                            {"$set": {"user_feedback": feedback_score, "feedback_text": feedback_text}}
                        )
                    except Exception as e:
                        logger.error(f"Failed to update feedback in database: {e}")
                
                break
        
        # Invalidate cache
        if strategy_id in self._stats_cache:
            del self._stats_cache[strategy_id]
    
    def get_strategy_stats(
        self,
        strategy_id: str,
        time_window: Optional[timedelta] = None,
        domain: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get aggregated statistics for a strategy.
        
        Args:
            strategy_id: ID of the strategy
            time_window: Optional time window to filter results
            domain: Optional domain to filter results
        
        Returns:
            Aggregated statistics dictionary
        """
        if not strategy_id:
            return self._empty_stats("unknown")
        
        cache_key = f"{strategy_id}_{time_window}_{domain}"
        
        # Check cache
        if cache_key in self._stats_cache:
            cache_time = self._cache_timestamps.get(cache_key, datetime.min)
            if datetime.utcnow() - cache_time < self._cache_ttl:
                return self._stats_cache[cache_key]
        
        # Filter executions
        executions = self._executions.get(strategy_id, [])
        
        if time_window:
            cutoff = datetime.utcnow() - time_window
            executions = [e for e in executions if e.get("timestamp", datetime.min) > cutoff]
        
        if domain:
            executions = [e for e in executions if e.get("domain") == domain]
        
        if not executions:
            return self._empty_stats(strategy_id)
        
        # Calculate statistics safely
        try:
            latencies = [e.get("latency_ms", 0) for e in executions if e.get("latency_ms") is not None]
            iterations = [e.get("iterations", 0) for e in executions if e.get("iterations") is not None]
            confidences = [e.get("confidence", 0) for e in executions if e.get("confidence") is not None]
            feedbacks = [e.get("user_feedback") for e in executions if e.get("user_feedback") is not None]
            
            # Quality distribution
            quality_counts = defaultdict(int)
            for e in executions:
                quality = e.get("result_quality", "unknown")
                quality_counts[quality] += 1
            
            # Domain distribution
            domain_counts = defaultdict(int)
            for e in executions:
                dom = e.get("domain", "unknown")
                domain_counts[dom] += 1
            
            # Query type distribution
            query_type_counts = defaultdict(int)
            for e in executions:
                qt = e.get("query_type", "unknown")
                query_type_counts[qt] += 1
            
            # Calculate aggregates safely
            avg_latency = statistics.mean(latencies) if latencies else 0.0
            median_latency = statistics.median(latencies) if latencies else 0.0
            
            # P95 latency calculation
            if len(latencies) >= 20:
                sorted_latencies = sorted(latencies)
                p95_idx = int(len(latencies) * 0.95)
                p95_latency = sorted_latencies[min(p95_idx, len(sorted_latencies) - 1)]
            else:
                p95_latency = max(latencies) if latencies else 0.0
            
            avg_iterations_val = statistics.mean(iterations) if iterations else 0.0
            avg_confidence_val = statistics.mean(confidences) if confidences else 0.0
            avg_feedback = statistics.mean(feedbacks) if feedbacks else None
            
            # Time range
            timestamps = [e.get("timestamp") for e in executions if e.get("timestamp")]
            time_range = {}
            if timestamps:
                time_range = {
                    "start": min(timestamps).isoformat(),
                    "end": max(timestamps).isoformat()
                }
            
            stats = {
                "strategy_id": strategy_id,
                "execution_count": len(executions),
                "avg_latency_ms": avg_latency,
                "median_latency_ms": median_latency,
                "p95_latency_ms": p95_latency,
                "avg_iterations": avg_iterations_val,
                "avg_confidence": avg_confidence_val,
                "quality_distribution": dict(quality_counts),
                "quality_score": self._calculate_quality_score(quality_counts),
                "avg_user_feedback": avg_feedback,
                "feedback_count": len(feedbacks),
                "domain_distribution": dict(domain_counts),
                "query_type_distribution": dict(query_type_counts),
                "time_range": time_range
            }
            
            # Cache the result
            self._stats_cache[cache_key] = stats
            self._cache_timestamps[cache_key] = datetime.utcnow()
            
            return stats
            
        except Exception as e:
            logger.error(f"Error calculating stats for strategy '{strategy_id}': {e}")
            return self._empty_stats(strategy_id)
    
    def _empty_stats(self, strategy_id: str) -> Dict[str, Any]:
        """Return an empty stats dictionary.
        
        Args:
            strategy_id: Strategy ID
        
        Returns:
            Empty stats dictionary with default values
        """
        return {
            "strategy_id": strategy_id,
            "execution_count": 0,
            "avg_latency_ms": 0.0,
            "median_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "avg_iterations": 0.0,
            "avg_confidence": 0.0,
            "quality_distribution": {},
            "quality_score": 0.0,
            "avg_user_feedback": None,
            "feedback_count": 0,
            "domain_distribution": {},
            "query_type_distribution": {},
            "time_range": {}
        }
    
    def _calculate_quality_score(self, quality_counts: Dict[str, int]) -> float:
        """Calculate a weighted quality score from quality distribution.
        
        Args:
            quality_counts: Distribution of quality ratings
        
        Returns:
            Weighted score from 0-100
        """
        weights = {
            "excellent": 100,
            "good": 75,
            "partial": 40,
            "empty": 0
        }
        
        total = sum(quality_counts.values())
        if total == 0:
            return 0
        
        weighted_sum = sum(
            weights.get(quality, 50) * count
            for quality, count in quality_counts.items()
        )
        
        return weighted_sum / total
    
    def compare_strategies(
        self,
        strategy_a: str,
        strategy_b: str,
        time_window: Optional[timedelta] = None,
        domain: Optional[str] = None
    ) -> Dict[str, Any]:
        """Compare two strategies' performance.
        
        Args:
            strategy_a: First strategy ID
            strategy_b: Second strategy ID
            time_window: Optional time window to filter results
            domain: Optional domain to filter results
        
        Returns:
            Comparison results with statistical analysis
        """
        stats_a = self.get_strategy_stats(strategy_a, time_window, domain)
        stats_b = self.get_strategy_stats(strategy_b, time_window, domain)
        
        comparison = {
            "strategy_a": strategy_a,
            "strategy_b": strategy_b,
            "filters": {
                "time_window": str(time_window) if time_window else "all_time",
                "domain": domain or "all_domains"
            },
            "stats_a": stats_a,
            "stats_b": stats_b,
            "comparison": {}
        }
        
        # Calculate deltas (positive means A is better)
        if stats_a["execution_count"] > 0 and stats_b["execution_count"] > 0:
            comparison["comparison"] = {
                "latency_delta_ms": stats_b["avg_latency_ms"] - stats_a["avg_latency_ms"],
                "latency_improvement_pct": (
                    (stats_b["avg_latency_ms"] - stats_a["avg_latency_ms"]) / stats_b["avg_latency_ms"] * 100
                    if stats_b["avg_latency_ms"] > 0 else 0
                ),
                "quality_score_delta": stats_a["quality_score"] - stats_b["quality_score"],
                "confidence_delta": stats_a["avg_confidence"] - stats_b["avg_confidence"],
                "iterations_delta": stats_b["avg_iterations"] - stats_a["avg_iterations"],
            }
            
            # Determine winner
            wins_a = 0
            wins_b = 0
            
            if comparison["comparison"]["latency_delta_ms"] > 0:
                wins_a += 1
            else:
                wins_b += 1
            
            if comparison["comparison"]["quality_score_delta"] > 0:
                wins_a += 1
            else:
                wins_b += 1
            
            if comparison["comparison"]["confidence_delta"] > 0:
                wins_a += 1
            else:
                wins_b += 1
            
            comparison["comparison"]["winner"] = strategy_a if wins_a > wins_b else strategy_b
            comparison["comparison"]["confidence_in_winner"] = "high" if abs(wins_a - wins_b) >= 2 else "low"
            
            # Add user feedback comparison if available
            if stats_a["avg_user_feedback"] and stats_b["avg_user_feedback"]:
                comparison["comparison"]["feedback_delta"] = (
                    stats_a["avg_user_feedback"] - stats_b["avg_user_feedback"]
                )
        
        return comparison
    
    def get_all_strategy_stats(
        self,
        time_window: Optional[timedelta] = None,
        domain: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get stats for all tracked strategies.
        
        Args:
            time_window: Optional time window to filter results
            domain: Optional domain to filter results
        
        Returns:
            List of stats dictionaries for all strategies
        """
        all_stats = []
        for strategy_id in self._executions.keys():
            stats = self.get_strategy_stats(strategy_id, time_window, domain)
            all_stats.append(stats)
        
        # Sort by quality score descending
        all_stats.sort(key=lambda x: x["quality_score"], reverse=True)
        
        return all_stats
    
    def clear_cache(self):
        """Clear the stats cache."""
        self._stats_cache.clear()
        self._cache_timestamps.clear()
    
    def get_execution_count(self, strategy_id: str) -> int:
        """Get the total number of executions for a strategy.
        
        Args:
            strategy_id: ID of the strategy
        
        Returns:
            Number of executions
        """
        return len(self._executions.get(strategy_id, []))


# Singleton instance
_metrics: Optional[StrategyMetrics] = None


def get_strategy_metrics(persist_to_db: bool = False, mongo_client=None) -> StrategyMetrics:
    """Get or create the strategy metrics singleton.
    
    Args:
        persist_to_db: Whether to persist metrics to MongoDB
        mongo_client: Optional MongoDB client
    
    Returns:
        StrategyMetrics instance
    """
    global _metrics
    if _metrics is None:
        _metrics = StrategyMetrics(persist_to_db=persist_to_db, mongo_client=mongo_client)
    return _metrics
