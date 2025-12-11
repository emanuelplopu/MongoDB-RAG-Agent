"""System and health check router."""

import logging
import time
from datetime import datetime
from fastapi import APIRouter, Request

from backend.models.schemas import (
    HealthResponse, SystemStatsResponse, ConfigResponse
)
from backend.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Track startup time
_startup_time = datetime.now()


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request):
    """
    Health check endpoint.
    
    Returns the health status of the API and database connection.
    """
    db = request.app.state.db
    
    # Check database connection
    db_status = "healthy"
    try:
        await db.client.admin.command('ping')
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
    
    uptime = (datetime.now() - _startup_time).total_seconds()
    
    return HealthResponse(
        status="healthy" if db_status == "healthy" else "degraded",
        database=db_status,
        version="1.0.0",
        uptime_seconds=uptime
    )


@router.get("/stats", response_model=SystemStatsResponse)
async def get_stats(request: Request):
    """
    Get system statistics.
    
    Returns database stats, index status, and configuration info.
    """
    db = request.app.state.db
    
    # Get database stats
    db_stats = await db.get_stats()
    
    # Get index status
    index_status = await db.check_indexes()
    
    # Get config (non-sensitive)
    config = {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_dimension": settings.embedding_dimension,
        "default_match_count": settings.default_match_count,
        "database": settings.mongodb_database
    }
    
    return SystemStatsResponse(
        database=db_stats,
        indexes=index_status,
        config=config
    )


@router.get("/config", response_model=ConfigResponse)
async def get_config():
    """
    Get current configuration (non-sensitive).
    
    Returns the current API configuration without sensitive values.
    """
    # Get active profile
    try:
        from src.profile import get_profile_manager
        pm = get_profile_manager()
        active_profile = pm.active_profile_key
    except Exception:
        active_profile = "default"
    
    return ConfigResponse(
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
        embedding_dimension=settings.embedding_dimension,
        default_match_count=settings.default_match_count,
        active_profile=active_profile,
        database=settings.mongodb_database
    )


@router.get("/indexes")
async def get_indexes(request: Request):
    """Get detailed index status."""
    db = request.app.state.db
    return await db.check_indexes()


@router.get("/info")
async def get_info():
    """Get API information."""
    uptime = (datetime.now() - _startup_time).total_seconds()
    
    return {
        "name": "MongoDB RAG Agent API",
        "version": "1.0.0",
        "description": "Production-ready RAG API with hybrid search",
        "uptime_seconds": uptime,
        "startup_time": _startup_time.isoformat(),
        "endpoints": {
            "chat": "/api/v1/chat",
            "search": "/api/v1/search",
            "profiles": "/api/v1/profiles",
            "ingestion": "/api/v1/ingestion",
            "system": "/api/v1/system"
        },
        "documentation": {
            "swagger": "/docs",
            "redoc": "/redoc",
            "openapi": "/openapi.json"
        }
    }


@router.post("/reload-settings")
async def reload_settings():
    """
    Reload settings from environment.
    
    Reloads configuration without restarting the server.
    """
    try:
        from backend.core.config import get_settings
        global settings
        settings = get_settings()
        
        return {
            "success": True,
            "message": "Settings reloaded",
            "config": {
                "database": settings.mongodb_database,
                "llm_model": settings.llm_model,
                "embedding_model": settings.embedding_model
            }
        }
    except Exception as e:
        logger.error(f"Failed to reload settings: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/database-stats")
async def get_database_stats(request: Request):
    """Get detailed database statistics."""
    db = request.app.state.db
    
    try:
        # Get collection counts
        doc_count = await db.documents_collection.count_documents({})
        chunk_count = await db.chunks_collection.count_documents({})
        
        # Get storage stats
        doc_stats = await db.db.command("collStats", settings.mongodb_collection_documents)
        chunk_stats = await db.db.command("collStats", settings.mongodb_collection_chunks)
        
        # Get sample of recent documents
        recent_docs = []
        cursor = db.documents_collection.find({}).sort("created_at", -1).limit(5)
        async for doc in cursor:
            recent_docs.append({
                "id": str(doc["_id"]),
                "title": doc.get("title", "Untitled"),
                "source": doc.get("source", "Unknown"),
                "created_at": doc.get("created_at")
            })
        
        return {
            "documents": {
                "count": doc_count,
                "size_mb": round(doc_stats.get("size", 0) / 1024 / 1024, 2),
                "avg_size_kb": round(doc_stats.get("avgObjSize", 0) / 1024, 2)
            },
            "chunks": {
                "count": chunk_count,
                "size_mb": round(chunk_stats.get("size", 0) / 1024 / 1024, 2),
                "avg_size_kb": round(chunk_stats.get("avgObjSize", 0) / 1024, 2)
            },
            "database": settings.mongodb_database,
            "recent_documents": recent_docs
        }
        
    except Exception as e:
        logger.error(f"Failed to get database stats: {e}")
        return {"error": str(e)}
