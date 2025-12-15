"""Status dashboard router - Enhanced KPIs and metrics per profile."""

import logging
import asyncio
import psutil
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel

from backend.core.config import settings
from backend.routers.auth import require_admin, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Track API response times for metrics
_response_times: List[Dict[str, Any]] = []
MAX_RESPONSE_SAMPLES = 1000


class ProfileStats(BaseModel):
    """Stats for a single profile."""
    profile_key: str
    profile_name: str
    database: str
    documents_count: int
    chunks_count: int
    total_tokens: int
    avg_chunk_size: float
    storage_size_bytes: int
    last_ingestion: Optional[str] = None
    ingestion_jobs_count: int = 0


class SystemMetrics(BaseModel):
    """System-level metrics."""
    cpu_percent: float
    memory_percent: float
    memory_used_gb: float
    memory_total_gb: float
    disk_percent: float
    disk_used_gb: float
    disk_total_gb: float
    uptime_seconds: float


class StatusDashboard(BaseModel):
    """Complete status dashboard response."""
    profiles: List[ProfileStats]
    active_profile: str
    system_metrics: SystemMetrics
    total_documents: int
    total_chunks: int
    total_profiles: int
    api_uptime_seconds: float
    llm_provider: str
    llm_model: str
    embedding_model: str


# Track startup time
_startup_time = datetime.now()


def get_profile_manager():
    """Get profile manager instance."""
    from src.profile import get_profile_manager as get_pm
    return get_pm(settings.profiles_path)


@router.get("/dashboard", response_model=StatusDashboard)
async def get_status_dashboard(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """
    Get comprehensive status dashboard with KPIs per profile.
    
    Returns system metrics, profile stats, and overall statistics.
    """
    db = request.app.state.db
    pm = get_profile_manager()
    
    profiles_data = []
    total_docs = 0
    total_chunks = 0
    
    # Get stats for each profile
    all_profiles = pm.list_profiles()
    current_profile = pm.active_profile_key
    
    for profile_key, profile in all_profiles.items():
        try:
            # Get collection stats for this profile's database
            profile_stats = await _get_profile_stats(db, profile, profile_key)
            profiles_data.append(profile_stats)
            total_docs += profile_stats.documents_count
            total_chunks += profile_stats.chunks_count
        except Exception as e:
            logger.warning(f"Could not get stats for profile {profile_key}: {e}")
            profiles_data.append(ProfileStats(
                profile_key=profile_key,
                profile_name=profile.name,
                database=profile.database,
                documents_count=0,
                chunks_count=0,
                total_tokens=0,
                avg_chunk_size=0,
                storage_size_bytes=0
            ))
    
    # Get system metrics
    system_metrics = _get_system_metrics()
    
    uptime = (datetime.now() - _startup_time).total_seconds()
    
    return StatusDashboard(
        profiles=profiles_data,
        active_profile=current_profile,
        system_metrics=system_metrics,
        total_documents=total_docs,
        total_chunks=total_chunks,
        total_profiles=len(all_profiles),
        api_uptime_seconds=uptime,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        embedding_model=settings.embedding_model
    )


async def _get_profile_stats(db, profile, profile_key: str) -> ProfileStats:
    """Get stats for a single profile."""
    from backend.core.database import get_db_executor
    
    def get_stats_sync():
        sync_client = db.get_sync_client()
        mongo_db = sync_client[profile.database]
        
        docs_coll = mongo_db[profile.collection_documents]
        chunks_coll = mongo_db[profile.collection_chunks]
        
        # Count documents
        doc_count = docs_coll.estimated_document_count()
        chunk_count = chunks_coll.estimated_document_count()
        
        # Get storage stats
        storage_size = 0
        avg_chunk_size = 0
        total_tokens = 0
        
        try:
            docs_stats = mongo_db.command("collStats", profile.collection_documents)
            chunks_stats = mongo_db.command("collStats", profile.collection_chunks)
            storage_size = docs_stats.get("storageSize", 0) + chunks_stats.get("storageSize", 0)
            avg_chunk_size = chunks_stats.get("avgObjSize", 0)
        except Exception:
            pass
        
        # Get last ingestion job
        last_ingestion = None
        try:
            jobs_coll = mongo_db["ingestion_jobs"]
            last_job = jobs_coll.find_one(
                {"profile": profile_key},
                sort=[("started_at", -1)]
            )
            if last_job:
                last_ingestion = last_job.get("started_at")
            
            # Count jobs
            jobs_count = jobs_coll.count_documents({"profile": profile_key})
        except Exception:
            jobs_count = 0
        
        return {
            "doc_count": doc_count,
            "chunk_count": chunk_count,
            "storage_size": storage_size,
            "avg_chunk_size": avg_chunk_size,
            "total_tokens": total_tokens,
            "last_ingestion": last_ingestion,
            "jobs_count": jobs_count
        }
    
    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(get_db_executor(), get_stats_sync)
    
    return ProfileStats(
        profile_key=profile_key,
        profile_name=profile.name,
        database=profile.database,
        documents_count=stats["doc_count"],
        chunks_count=stats["chunk_count"],
        total_tokens=stats["total_tokens"],
        avg_chunk_size=stats["avg_chunk_size"],
        storage_size_bytes=stats["storage_size"],
        last_ingestion=stats["last_ingestion"],
        ingestion_jobs_count=stats["jobs_count"]
    )


def _get_system_metrics() -> SystemMetrics:
    """Get system resource metrics."""
    cpu = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    # Get process uptime
    process = psutil.Process()
    uptime = time.time() - process.create_time()
    
    return SystemMetrics(
        cpu_percent=cpu,
        memory_percent=memory.percent,
        memory_used_gb=round(memory.used / (1024**3), 2),
        memory_total_gb=round(memory.total / (1024**3), 2),
        disk_percent=disk.percent,
        disk_used_gb=round(disk.used / (1024**3), 2),
        disk_total_gb=round(disk.total / (1024**3), 2),
        uptime_seconds=uptime
    )


@router.get("/metrics/profile/{profile_key}")
async def get_profile_metrics(
    request: Request,
    profile_key: str,
    admin: UserResponse = Depends(require_admin)
):
    """Get detailed metrics for a specific profile."""
    db = request.app.state.db
    pm = get_profile_manager()
    
    profiles = pm.list_profiles()
    if profile_key not in profiles:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    profile = profiles[profile_key]
    stats = await _get_profile_stats(db, profile, profile_key)
    
    # Additional detailed metrics
    detailed = await _get_detailed_profile_metrics(db, profile)
    
    return {
        "profile": stats,
        "detailed": detailed
    }


async def _get_detailed_profile_metrics(db, profile) -> Dict[str, Any]:
    """Get detailed metrics for a profile."""
    from backend.core.database import get_db_executor
    
    def get_detailed_sync():
        sync_client = db.get_sync_client()
        mongo_db = sync_client[profile.database]
        chunks_coll = mongo_db[profile.collection_chunks]
        
        # File type distribution
        pipeline = [
            {"$group": {
                "_id": "$metadata.file_type",
                "count": {"$sum": 1}
            }}
        ]
        file_types = list(chunks_coll.aggregate(pipeline))
        
        # Chunks per document distribution
        doc_pipeline = [
            {"$group": {
                "_id": "$document_id",
                "chunks": {"$sum": 1}
            }},
            {"$group": {
                "_id": None,
                "avg_chunks": {"$avg": "$chunks"},
                "max_chunks": {"$max": "$chunks"},
                "min_chunks": {"$min": "$chunks"}
            }}
        ]
        chunk_dist = list(chunks_coll.aggregate(doc_pipeline))
        
        return {
            "file_types": {ft["_id"] or "unknown": ft["count"] for ft in file_types},
            "chunk_distribution": chunk_dist[0] if chunk_dist else {}
        }
    
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_db_executor(), get_detailed_sync)


@router.get("/health/detailed")
async def get_detailed_health(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """Get detailed health check with component status."""
    db = request.app.state.db
    
    components = {
        "database": {"status": "unknown", "latency_ms": 0},
        "vector_index": {"status": "unknown"},
        "text_index": {"status": "unknown"},
        "embedding_api": {"status": "unknown"},
        "llm_api": {"status": "unknown"}
    }
    
    # Check database
    try:
        start = time.time()
        await db.client.admin.command('ping')
        latency = (time.time() - start) * 1000
        components["database"] = {"status": "healthy", "latency_ms": round(latency, 2)}
    except Exception as e:
        components["database"] = {"status": "unhealthy", "error": str(e)}
    
    # Check indexes
    try:
        index_info = await db.check_indexes()
        for idx in index_info.get("indexes", []):
            if "vector" in idx.get("name", "").lower():
                components["vector_index"] = {"status": idx.get("status", "unknown")}
            if "text" in idx.get("name", "").lower():
                components["text_index"] = {"status": idx.get("status", "unknown")}
    except Exception as e:
        logger.warning(f"Index check failed: {e}")
    
    # Check embedding API
    if settings.embedding_api_key:
        components["embedding_api"] = {"status": "configured", "model": settings.embedding_model}
    else:
        components["embedding_api"] = {"status": "not_configured"}
    
    # Check LLM API
    if settings.llm_api_key:
        components["llm_api"] = {"status": "configured", "model": settings.llm_model}
    else:
        components["llm_api"] = {"status": "not_configured"}
    
    overall = "healthy" if all(
        c.get("status") in ["healthy", "configured", "READY"]
        for c in components.values()
    ) else "degraded"
    
    return {
        "overall": overall,
        "components": components,
        "timestamp": datetime.now().isoformat()
    }
