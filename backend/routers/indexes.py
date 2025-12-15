"""Search indexes router - Performance metrics and optimization suggestions."""

import logging
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import deque
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel

from backend.core.config import settings
from backend.routers.auth import require_admin, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Track search response times
MAX_SAMPLES = 1000
_search_latencies: deque = deque(maxlen=MAX_SAMPLES)


class IndexMetrics(BaseModel):
    """Metrics for a search index."""
    name: str
    type: str
    status: str
    size_bytes: int = 0
    documents_indexed: int = 0
    last_updated: Optional[str] = None


class SearchPerformance(BaseModel):
    """Search performance metrics."""
    avg_response_time_ms: float
    p50_response_time_ms: float
    p95_response_time_ms: float
    p99_response_time_ms: float
    total_searches: int
    searches_last_hour: int
    searches_last_24h: int


class OptimizationSuggestion(BaseModel):
    """Database optimization suggestion."""
    category: str
    severity: str  # low, medium, high, critical
    title: str
    description: str
    action: str
    estimated_impact: str


class IndexDashboard(BaseModel):
    """Complete index dashboard response."""
    indexes: List[IndexMetrics]
    performance: SearchPerformance
    suggestions: List[OptimizationSuggestion]
    resource_allocation: Dict[str, Any]


def record_search_latency(latency_ms: float, search_type: str = "hybrid"):
    """Record a search latency sample."""
    _search_latencies.append({
        "latency_ms": latency_ms,
        "search_type": search_type,
        "timestamp": datetime.now()
    })


def get_profile_manager():
    """Get profile manager instance."""
    from src.profile import get_profile_manager as get_pm
    return get_pm(settings.profiles_path)


@router.get("/dashboard", response_model=IndexDashboard)
async def get_index_dashboard(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """
    Get comprehensive search indexes dashboard.
    
    Includes index metrics, performance stats, and optimization suggestions.
    """
    db = request.app.state.db
    
    # Get index information
    indexes = await _get_index_metrics(db)
    
    # Get performance metrics
    performance = _calculate_performance_metrics()
    
    # Generate optimization suggestions
    suggestions = await _generate_optimization_suggestions(db, indexes, performance)
    
    # Get resource allocation info
    resource_allocation = await _get_resource_allocation(db)
    
    return IndexDashboard(
        indexes=indexes,
        performance=performance,
        suggestions=suggestions,
        resource_allocation=resource_allocation
    )


async def _get_index_metrics(db) -> List[IndexMetrics]:
    """Get metrics for all search indexes."""
    from backend.core.database import get_db_executor
    
    def get_indexes_sync():
        sync_client = db.get_sync_client()
        mongo_db = sync_client[db.current_database_name]
        chunks_coll_name = settings.mongodb_collection_chunks
        
        indexes = []
        
        try:
            # Get search indexes
            coll_info = mongo_db.command("listSearchIndexes", chunks_coll_name)
            
            for idx in coll_info.get("cursor", {}).get("firstBatch", []):
                idx_type = idx.get("type", "search")
                if idx_type == "vectorSearch":
                    idx_type = "vector"
                
                indexes.append({
                    "name": idx.get("name", "unknown"),
                    "type": idx_type,
                    "status": idx.get("status", "UNKNOWN"),
                    "size_bytes": 0,  # Not directly available
                    "documents_indexed": 0
                })
        except Exception as e:
            logger.warning(f"Could not list search indexes: {e}")
        
        # Get collection stats for document count and sizes
        try:
            coll_stats = mongo_db.command("collStats", chunks_coll_name)
            doc_count = coll_stats.get("count", 0)
            index_size = coll_stats.get("totalIndexSize", 0)
            
            # Update doc count for all indexes
            for idx in indexes:
                idx["documents_indexed"] = doc_count
                idx["size_bytes"] = index_size // max(len(indexes), 1)
        except Exception as e:
            logger.warning(f"Could not get collection stats: {e}")
        
        return indexes
    
    loop = asyncio.get_running_loop()
    indexes_data = await loop.run_in_executor(get_db_executor(), get_indexes_sync)
    
    return [IndexMetrics(**idx) for idx in indexes_data]


def _calculate_performance_metrics() -> SearchPerformance:
    """Calculate search performance metrics from collected samples."""
    if not _search_latencies:
        return SearchPerformance(
            avg_response_time_ms=0,
            p50_response_time_ms=0,
            p95_response_time_ms=0,
            p99_response_time_ms=0,
            total_searches=0,
            searches_last_hour=0,
            searches_last_24h=0
        )
    
    samples = list(_search_latencies)
    latencies = sorted([s["latency_ms"] for s in samples])
    
    now = datetime.now()
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(hours=24)
    
    searches_hour = sum(1 for s in samples if s["timestamp"] > hour_ago)
    searches_day = sum(1 for s in samples if s["timestamp"] > day_ago)
    
    n = len(latencies)
    
    return SearchPerformance(
        avg_response_time_ms=round(sum(latencies) / n, 2),
        p50_response_time_ms=round(latencies[n // 2], 2),
        p95_response_time_ms=round(latencies[int(n * 0.95)], 2) if n > 20 else round(latencies[-1], 2),
        p99_response_time_ms=round(latencies[int(n * 0.99)], 2) if n > 100 else round(latencies[-1], 2),
        total_searches=n,
        searches_last_hour=searches_hour,
        searches_last_24h=searches_day
    )


async def _generate_optimization_suggestions(
    db, 
    indexes: List[IndexMetrics], 
    performance: SearchPerformance
) -> List[OptimizationSuggestion]:
    """Generate database optimization suggestions."""
    suggestions = []
    
    # Check if indexes are missing
    has_vector = any(idx.type == "vector" for idx in indexes)
    has_text = any(idx.type in ["search", "text"] for idx in indexes)
    
    if not has_vector:
        suggestions.append(OptimizationSuggestion(
            category="indexes",
            severity="critical",
            title="Vector Search Index Missing",
            description="No vector search index found. Semantic search will not work.",
            action="Create vector search index on the 'embedding' field",
            estimated_impact="Enable semantic search functionality"
        ))
    
    if not has_text:
        suggestions.append(OptimizationSuggestion(
            category="indexes",
            severity="high",
            title="Text Search Index Missing",
            description="No text search index found. Full-text search will not work.",
            action="Create text search index on the 'content' field",
            estimated_impact="Enable full-text search functionality"
        ))
    
    # Check index status
    for idx in indexes:
        if idx.status not in ["READY", "ready"]:
            suggestions.append(OptimizationSuggestion(
                category="indexes",
                severity="medium",
                title=f"Index '{idx.name}' Not Ready",
                description=f"Index status is '{idx.status}'. It may still be building.",
                action="Wait for index building to complete or check for errors",
                estimated_impact="Search performance may be degraded"
            ))
    
    # Performance-based suggestions
    if performance.avg_response_time_ms > 500:
        suggestions.append(OptimizationSuggestion(
            category="performance",
            severity="medium",
            title="High Average Response Time",
            description=f"Average search response time is {performance.avg_response_time_ms}ms",
            action="Consider reducing match_count or optimizing embedding dimensions",
            estimated_impact="Improve search response by 30-50%"
        ))
    
    if performance.p95_response_time_ms > 2000:
        suggestions.append(OptimizationSuggestion(
            category="performance",
            severity="high",
            title="High P95 Latency",
            description=f"95th percentile latency is {performance.p95_response_time_ms}ms",
            action="Review complex queries and consider query caching",
            estimated_impact="Reduce worst-case response times"
        ))
    
    # Check document count for index efficiency
    doc_count = max((idx.documents_indexed for idx in indexes), default=0)
    if doc_count > 100000:
        suggestions.append(OptimizationSuggestion(
            category="scaling",
            severity="low",
            title="Large Collection Size",
            description=f"Collection has {doc_count:,} documents",
            action="Consider sharding or archiving old documents",
            estimated_impact="Maintain performance as data grows"
        ))
    
    # Memory-based suggestions
    try:
        import psutil
        memory = psutil.virtual_memory()
        if memory.percent > 80:
            suggestions.append(OptimizationSuggestion(
                category="resources",
                severity="medium",
                title="High Memory Usage",
                description=f"System memory is {memory.percent}% utilized",
                action="Consider increasing RAM or reducing concurrent operations",
                estimated_impact="Prevent out-of-memory errors"
            ))
    except Exception:
        pass
    
    return suggestions


async def _get_resource_allocation(db) -> Dict[str, Any]:
    """Get current resource allocation information."""
    import psutil
    
    cpu_count = psutil.cpu_count()
    memory = psutil.virtual_memory()
    
    return {
        "cpu": {
            "cores": cpu_count,
            "usage_percent": psutil.cpu_percent(interval=0.1),
            "recommended_workers": max(2, cpu_count - 1)
        },
        "memory": {
            "total_gb": round(memory.total / (1024**3), 2),
            "available_gb": round(memory.available / (1024**3), 2),
            "recommended_cache_mb": min(1024, int(memory.available / (1024**2) * 0.2))
        },
        "mongodb": {
            "connection_pool_size": 10,  # Default
            "recommended_pool_size": min(100, cpu_count * 5)
        }
    }


@router.post("/create")
async def create_search_indexes(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """Create or recreate search indexes."""
    from backend.core.database import get_db_executor
    
    db = request.app.state.db
    
    def create_indexes_sync():
        results = {
            "success": False,
            "vector_index": None,
            "text_index": None,
            "errors": []
        }
        
        try:
            sync_client = db.get_sync_client()
            mongo_db = sync_client[db.current_database_name]
            chunks_coll_name = settings.mongodb_collection_chunks
            
            doc_count = mongo_db[chunks_coll_name].estimated_document_count()
            results["documents_to_index"] = doc_count
            
            # Vector Search Index
            vector_index_def = {
                "name": settings.mongodb_vector_index,
                "type": "vectorSearch",
                "definition": {
                    "fields": [{
                        "type": "vector",
                        "path": "embedding",
                        "numDimensions": settings.embedding_dimension,
                        "similarity": "cosine"
                    }]
                }
            }
            
            try:
                try:
                    mongo_db.command({
                        "dropSearchIndex": chunks_coll_name,
                        "name": settings.mongodb_vector_index
                    })
                except Exception:
                    pass
                
                mongo_db.command({
                    "createSearchIndexes": chunks_coll_name,
                    "indexes": [vector_index_def]
                })
                results["vector_index"] = {
                    "name": settings.mongodb_vector_index,
                    "status": "created",
                    "dimensions": settings.embedding_dimension
                }
            except Exception as e:
                results["errors"].append(f"Vector index: {str(e)}")
            
            # Text Search Index
            text_index_def = {
                "name": settings.mongodb_text_index,
                "definition": {
                    "mappings": {
                        "dynamic": False,
                        "fields": {
                            "content": {
                                "type": "string",
                                "analyzer": "lucene.standard"
                            }
                        }
                    }
                }
            }
            
            try:
                try:
                    mongo_db.command({
                        "dropSearchIndex": chunks_coll_name,
                        "name": settings.mongodb_text_index
                    })
                except Exception:
                    pass
                
                mongo_db.command({
                    "createSearchIndexes": chunks_coll_name,
                    "indexes": [text_index_def]
                })
                results["text_index"] = {
                    "name": settings.mongodb_text_index,
                    "status": "created"
                }
            except Exception as e:
                results["errors"].append(f"Text index: {str(e)}")
            
            results["success"] = results["vector_index"] is not None or results["text_index"] is not None
            results["message"] = "Indexes created. They may take time to become READY."
            
            return results
        except Exception as e:
            results["errors"].append(str(e))
            return results
    
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_db_executor(), create_indexes_sync)


@router.get("/performance/history")
async def get_performance_history(
    request: Request,
    hours: int = 24,
    admin: UserResponse = Depends(require_admin)
):
    """Get historical performance data."""
    cutoff = datetime.now() - timedelta(hours=hours)
    
    samples = [s for s in _search_latencies if s["timestamp"] > cutoff]
    
    # Group by hour
    hourly_data = {}
    for s in samples:
        hour_key = s["timestamp"].strftime("%Y-%m-%d %H:00")
        if hour_key not in hourly_data:
            hourly_data[hour_key] = {"latencies": [], "count": 0}
        hourly_data[hour_key]["latencies"].append(s["latency_ms"])
        hourly_data[hour_key]["count"] += 1
    
    history = []
    for hour, data in sorted(hourly_data.items()):
        latencies = data["latencies"]
        history.append({
            "hour": hour,
            "count": data["count"],
            "avg_ms": round(sum(latencies) / len(latencies), 2),
            "max_ms": round(max(latencies), 2),
            "min_ms": round(min(latencies), 2)
        })
    
    return {"history": history, "total_samples": len(samples)}
