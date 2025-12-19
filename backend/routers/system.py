"""System and health check router."""

import logging
import time
import httpx
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from backend.models.schemas import (
    HealthResponse, SystemStatsResponse, ConfigResponse
)
from backend.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Track startup time
_startup_time = datetime.now()

# Available embedding models (fallback list for OpenAI)
EMBEDDING_MODELS_FALLBACK = [
    {"id": "text-embedding-3-small", "dimension": 1536, "provider": "openai"},
    {"id": "text-embedding-3-large", "dimension": 3072, "provider": "openai"},
    {"id": "text-embedding-ada-002", "dimension": 1536, "provider": "openai"},
]

# Known embedding model dimensions
EMBEDDING_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    # New models may have different dimensions
    "text-embedding-4": 3072,
    "text-embedding-4-small": 1536,
    "text-embedding-4-large": 3072,
}

# Default match count options
MATCH_COUNT_OPTIONS = [5, 10, 15, 20, 25, 50, 100]


class ConfigUpdateRequest(BaseModel):
    llm_model: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dimension: Optional[int] = None
    default_match_count: Optional[int] = None


# Collection name for persisted config
CONFIG_COLLECTION = "system_config"
CONFIG_DOC_ID = "active_config"


class ModelInfo(BaseModel):
    id: str
    owned_by: str
    created: Optional[int] = None


class ModelsResponse(BaseModel):
    models: List[ModelInfo]
    provider: str
    cached: bool = False


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
    """Get detailed index status with stats."""
    db = request.app.state.db
    index_info = await db.check_indexes()
    
    # Add additional stats if indexes exist
    if "indexes" in index_info and index_info["indexes"]:
        try:
            # Run sync operations in dedicated thread pool to avoid blocking
            import asyncio
            from backend.core.database import get_db_executor
            loop = asyncio.get_running_loop()
            
            def get_index_stats_sync():
                import time as sync_time
                logger.info("get_index_stats_sync: starting")
                start = sync_time.time()
                
                sync_client = db.get_sync_client()
                mongo_db = sync_client[db.current_database_name]
                chunks_coll = mongo_db[settings.mongodb_collection_chunks]
                logger.info(f"get_index_stats_sync: got client in {sync_time.time()-start:.2f}s")
                
                # Get collection stats for index size estimate
                coll_stats = mongo_db.command("collStats", settings.mongodb_collection_chunks)
                logger.info(f"get_index_stats_sync: got collStats in {sync_time.time()-start:.2f}s")
                
                # Use estimated_document_count for faster results
                doc_count = chunks_coll.estimated_document_count()
                logger.info(f"get_index_stats_sync: got doc_count={doc_count} in {sync_time.time()-start:.2f}s")
                
                # Skip the slow find_one query - use None for last_updated
                # The find_one(sort=[("created_at", -1)]) scans entire collection without index
                last_updated = None
                logger.info(f"get_index_stats_sync: completed in {sync_time.time()-start:.2f}s")
                
                return {
                    "indexed_documents": doc_count,
                    "total_index_size_bytes": coll_stats.get("totalIndexSize", 0),
                    "storage_size_bytes": coll_stats.get("storageSize", 0),
                    "last_document_indexed": last_updated
                }
            
            index_info["stats"] = await loop.run_in_executor(get_db_executor(), get_index_stats_sync)
        except Exception as e:
            logger.warning(f"Could not get index stats: {e}")
            index_info["stats"] = None
    
    return index_info


@router.post("/indexes/create")
async def create_search_indexes(request: Request):
    """
    Create or recreate search indexes.
    
    Creates both vector search and text search indexes for the chunks collection.
    """
    from datetime import datetime
    import asyncio
    from backend.core.database import get_db_executor
    
    db = request.app.state.db
    
    def create_indexes_sync():
        """Run sync index creation in thread pool."""
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
            
            # Use estimated_document_count for faster results
            doc_count = mongo_db[chunks_coll_name].estimated_document_count()
            results["documents_to_index"] = doc_count
            
            if doc_count == 0:
                results["warning"] = "No documents found in chunks collection"
            
            # Vector Search Index
            vector_index_def = {
                "name": settings.mongodb_vector_index,
                "type": "vectorSearch",
                "definition": {
                    "fields": [
                        {
                            "type": "vector",
                            "path": "embedding",
                            "numDimensions": settings.embedding_dimension,
                            "similarity": "cosine"
                        }
                    ]
                }
            }
            
            try:
                # Drop existing index if exists
                try:
                    mongo_db.command({
                        "dropSearchIndex": chunks_coll_name,
                        "name": settings.mongodb_vector_index
                    })
                except Exception:
                    pass
                
                # Create new index
                mongo_db.command({
                    "createSearchIndexes": chunks_coll_name,
                    "indexes": [vector_index_def]
                })
                results["vector_index"] = {
                    "name": settings.mongodb_vector_index,
                    "status": "created",
                    "dimensions": settings.embedding_dimension
                }
                logger.info(f"Created vector index: {settings.mongodb_vector_index}")
            except Exception as e:
                error_msg = f"Vector index error: {str(e)}"
                results["errors"].append(error_msg)
                logger.error(error_msg)
            
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
                # Drop existing index if exists
                try:
                    mongo_db.command({
                        "dropSearchIndex": chunks_coll_name,
                        "name": settings.mongodb_text_index
                    })
                except Exception:
                    pass
                
                # Create new index
                mongo_db.command({
                    "createSearchIndexes": chunks_coll_name,
                    "indexes": [text_index_def]
                })
                results["text_index"] = {
                    "name": settings.mongodb_text_index,
                    "status": "created"
                }
                logger.info(f"Created text index: {settings.mongodb_text_index}")
            except Exception as e:
                error_msg = f"Text index error: {str(e)}"
                results["errors"].append(error_msg)
                logger.error(error_msg)
            
            # Check if at least one index was created
            results["success"] = results["vector_index"] is not None or results["text_index"] is not None
            results["created_at"] = datetime.now().isoformat()
            results["message"] = "Indexes created. They may take a few seconds to become READY."
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to create indexes: {e}")
            results["errors"].append(str(e))
            return results
    
    # Run in thread pool to avoid blocking
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_db_executor(), create_indexes_sync)


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
        import asyncio
        from backend.core.database import get_db_executor
        
        loop = asyncio.get_running_loop()
        
        def get_stats_sync():
            """Run sync DB operations in thread pool."""
            sync_client = db.get_sync_client()
            mongo_db = sync_client[db.current_database_name]
            
            docs_coll = mongo_db[settings.mongodb_collection_documents]
            chunks_coll = mongo_db[settings.mongodb_collection_chunks]
            
            # Use estimated_document_count for fast results
            doc_count = docs_coll.estimated_document_count()
            chunk_count = chunks_coll.estimated_document_count()
            
            # Get storage stats
            doc_stats = mongo_db.command("collStats", settings.mongodb_collection_documents)
            chunk_stats = mongo_db.command("collStats", settings.mongodb_collection_chunks)
            
            # Get sample of recent documents (with index on created_at this is fast)
            recent_docs = []
            for doc in docs_coll.find({}).sort("created_at", -1).limit(5):
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
        
        return await loop.run_in_executor(get_db_executor(), get_stats_sync)
        
    except Exception as e:
        logger.error(f"Failed to get database stats: {e}")
        return {"error": str(e)}


# Cache for models list (expires after 5 minutes)
_models_cache: dict = {
    "models": [],
    "timestamp": 0,
    "ttl": 300  # 5 minutes
}


@router.get("/models/llm")
async def list_llm_models():
    """
    List available LLM models from OpenAI.
    
    Fetches models from the OpenAI API and filters to show only chat models.
    Results are cached for 5 minutes.
    """
    import time as time_module
    
    current_time = time_module.time()
    
    # Check cache
    if _models_cache["models"] and (current_time - _models_cache["timestamp"]) < _models_cache["ttl"]:
        return {
            "models": _models_cache["models"],
            "provider": "openai",
            "cached": True
        }
    
    # Fetch from OpenAI
    api_key = settings.llm_api_key or settings.embedding_api_key
    if not api_key:
        # Return fallback list if no API key
        fallback_models = [
            {"id": "gpt-5.2", "owned_by": "openai", "created": None},
            {"id": "gpt-5", "owned_by": "openai", "created": None},
            {"id": "gpt-4o", "owned_by": "openai", "created": None},
            {"id": "gpt-4o-mini", "owned_by": "openai", "created": None},
            {"id": "gpt-4.1", "owned_by": "openai", "created": None},
            {"id": "gpt-4-turbo", "owned_by": "openai", "created": None},
            {"id": "gpt-4", "owned_by": "openai", "created": None},
            {"id": "gpt-3.5-turbo", "owned_by": "openai", "created": None},
            {"id": "o1-preview", "owned_by": "openai", "created": None},
            {"id": "o1-mini", "owned_by": "openai", "created": None},
        ]
        return {
            "models": fallback_models,
            "provider": "openai",
            "cached": False,
            "fallback": True
        }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()
        
        # Filter to show relevant chat/completion models
        models = []
        for model in data.get("data", []):
            model_id = model.get("id", "")
            # Include GPT models (all versions), O1 models, and other chat-capable models
            if any(prefix in model_id.lower() for prefix in ["gpt-", "o1-", "o3-", "o4-", "chatgpt"]):
                models.append({
                    "id": model_id,
                    "owned_by": model.get("owned_by", "unknown"),
                    "created": model.get("created")
                })
        
        # Sort by model ID (prefer gpt-5 first, then gpt-4o, then gpt-4, then others)
        def sort_key(m):
            mid = m["id"]
            if "gpt-5" in mid:
                return (0, mid)
            elif "gpt-4o" in mid:
                return (1, mid)
            elif "gpt-4.1" in mid:
                return (2, mid)
            elif "gpt-4" in mid:
                return (3, mid)
            elif "o1-" in mid or "o3-" in mid:
                return (4, mid)
            elif "gpt-3.5" in mid:
                return (5, mid)
            return (9, mid)
        
        models.sort(key=sort_key)
        
        # Update cache
        _models_cache["models"] = models
        _models_cache["timestamp"] = current_time
        
        return {
            "models": models,
            "provider": "openai",
            "cached": False
        }
        
    except httpx.HTTPStatusError as e:
        logger.error(f"OpenAI API error: {e.response.status_code}")
        raise HTTPException(status_code=e.response.status_code, detail="Failed to fetch models from OpenAI")
    except Exception as e:
        logger.error(f"Failed to fetch models: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Cache for embedding models
_embedding_models_cache = {
    "models": None,
    "timestamp": 0
}


@router.get("/models/embedding")
async def list_embedding_models():
    """
    List available embedding models from OpenAI API.
    
    Returns embedding models with their dimensions, cached for 5 minutes.
    """
    current_time = time.time()
    cache_ttl = 300  # 5 minutes
    
    # Check cache
    if (_embedding_models_cache["models"] is not None and 
        current_time - _embedding_models_cache["timestamp"] < cache_ttl):
        return {
            "models": _embedding_models_cache["models"],
            "provider": "openai",
            "cached": True
        }
    
    # Try to fetch from OpenAI
    api_key = settings.embedding_api_key or settings.llm_api_key
    if not api_key:
        return {
            "models": EMBEDDING_MODELS_FALLBACK,
            "provider": "openai",
            "fallback": True
        }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()
        
        # Filter for embedding models
        models = []
        for model in data.get("data", []):
            model_id = model.get("id", "")
            if "embed" in model_id.lower():
                # Get dimension from known list or default
                dimension = EMBEDDING_DIMENSIONS.get(model_id, 1536)
                models.append({
                    "id": model_id,
                    "dimension": dimension,
                    "provider": "openai"
                })
        
        # Sort: prefer text-embedding-4 first, then 3, then ada
        def sort_key(m):
            mid = m["id"]
            if "text-embedding-4" in mid:
                return (0, mid)
            elif "text-embedding-3-large" in mid:
                return (1, mid)
            elif "text-embedding-3-small" in mid:
                return (2, mid)
            elif "text-embedding-3" in mid:
                return (3, mid)
            return (9, mid)
        
        models.sort(key=sort_key)
        
        # Update cache
        _embedding_models_cache["models"] = models
        _embedding_models_cache["timestamp"] = current_time
        
        return {
            "models": models if models else EMBEDDING_MODELS_FALLBACK,
            "provider": "openai",
            "cached": False
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch embedding models: {e}")
        return {
            "models": EMBEDDING_MODELS_FALLBACK,
            "provider": "openai",
            "fallback": True,
            "error": str(e)
        }


@router.get("/config/options")
async def get_config_options():
    """
    Get available configuration options.
    
    Returns the current config values and available options for dropdowns.
    """
    return {
        "current": {
            "llm_model": settings.llm_model,
            "embedding_model": settings.embedding_model,
            "embedding_dimension": settings.embedding_dimension,
            "default_match_count": settings.default_match_count,
        },
        "options": {
            "embedding_models": EMBEDDING_MODELS_FALLBACK,
            "match_count_options": MATCH_COUNT_OPTIONS,
            "embedding_dimensions": [256, 512, 768, 1024, 1536, 3072],
        }
    }


@router.post("/config/update")
async def update_config(update: ConfigUpdateRequest):
    """
    Update runtime configuration.
    
    Updates configuration values in memory. These changes persist until restart.
    For permanent changes, use /config/save to persist to database.
    """
    updated = {}
    
    if update.llm_model is not None:
        settings.llm_model = update.llm_model
        updated["llm_model"] = update.llm_model
    
    if update.embedding_model is not None:
        settings.embedding_model = update.embedding_model
        updated["embedding_model"] = update.embedding_model
    
    if update.embedding_dimension is not None:
        settings.embedding_dimension = update.embedding_dimension
        updated["embedding_dimension"] = update.embedding_dimension
    
    if update.default_match_count is not None:
        settings.default_match_count = update.default_match_count
        updated["default_match_count"] = update.default_match_count
    
    if not updated:
        return {"success": False, "message": "No fields to update"}
    
    logger.info(f"Configuration updated (runtime): {updated}")
    
    return {
        "success": True,
        "message": "Configuration updated (runtime only - will reset on restart)",
        "updated": updated,
        "current": {
            "llm_model": settings.llm_model,
            "embedding_model": settings.embedding_model,
            "embedding_dimension": settings.embedding_dimension,
            "default_match_count": settings.default_match_count,
        }
    }


@router.post("/config/save")
async def save_config_to_db(request: Request, update: ConfigUpdateRequest):
    """
    Save configuration to database for persistence across restarts.
    
    This saves the config to MongoDB so it will be loaded automatically on startup.
    """
    db = request.app.state.db
    
    # Also update runtime settings
    if update.llm_model is not None:
        settings.llm_model = update.llm_model
    if update.embedding_model is not None:
        settings.embedding_model = update.embedding_model
    if update.embedding_dimension is not None:
        settings.embedding_dimension = update.embedding_dimension
    if update.default_match_count is not None:
        settings.default_match_count = update.default_match_count
    
    # Build config document
    config_doc = {
        "_id": CONFIG_DOC_ID,
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "embedding_dimension": settings.embedding_dimension,
        "default_match_count": settings.default_match_count,
        "updated_at": datetime.now().isoformat(),
    }
    
    try:
        # Upsert config to database
        collection = db.db[CONFIG_COLLECTION]
        await collection.replace_one(
            {"_id": CONFIG_DOC_ID},
            config_doc,
            upsert=True
        )
        
        logger.info(f"Configuration saved to database: {config_doc}")
        
        return {
            "success": True,
            "message": "Configuration saved to database (will persist across restarts)",
            "config": {
                "llm_model": settings.llm_model,
                "embedding_model": settings.embedding_model,
                "embedding_dimension": settings.embedding_dimension,
                "default_match_count": settings.default_match_count,
            }
        }
    except Exception as e:
        logger.error(f"Failed to save config to database: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save config: {str(e)}")


@router.get("/config/saved")
async def get_saved_config(request: Request):
    """
    Get the saved configuration from database.
    
    Returns the persisted config if it exists, otherwise returns None.
    """
    db = request.app.state.db
    
    try:
        collection = db.db[CONFIG_COLLECTION]
        doc = await collection.find_one({"_id": CONFIG_DOC_ID})
        
        if doc:
            return {
                "exists": True,
                "config": {
                    "llm_model": doc.get("llm_model"),
                    "embedding_model": doc.get("embedding_model"),
                    "embedding_dimension": doc.get("embedding_dimension"),
                    "default_match_count": doc.get("default_match_count"),
                    "updated_at": doc.get("updated_at"),
                }
            }
        return {"exists": False, "config": None}
    except Exception as e:
        logger.error(f"Failed to get saved config: {e}")
        return {"exists": False, "config": None, "error": str(e)}


async def load_config_from_db(db) -> bool:
    """
    Load configuration from database at startup.
    
    Returns True if config was loaded, False otherwise.
    """
    try:
        collection = db.db[CONFIG_COLLECTION]
        doc = await collection.find_one({"_id": CONFIG_DOC_ID})
        
        if doc:
            if doc.get("llm_model"):
                settings.llm_model = doc["llm_model"]
            if doc.get("embedding_model"):
                settings.embedding_model = doc["embedding_model"]
            if doc.get("embedding_dimension"):
                settings.embedding_dimension = doc["embedding_dimension"]
            if doc.get("default_match_count"):
                settings.default_match_count = doc["default_match_count"]
            
            logger.info(f"Loaded configuration from database: llm={settings.llm_model}, embedding={settings.embedding_model}")
            return True
        return False
    except Exception as e:
        logger.warning(f"Failed to load config from database: {e}")
        return False


# ==================== Database Management ====================

class DatabaseCheckResponse(BaseModel):
    """Response for database existence check."""
    database: str
    exists: bool
    has_collections: bool
    collections: List[str]
    documents_count: int
    chunks_count: int
    can_create: bool


class DatabaseCreateRequest(BaseModel):
    """Request to create a database with required collections."""
    database: str
    create_indexes: bool = True


@router.get("/database/check/{database_name}")
async def check_database_exists(request: Request, database_name: str):
    """
    Check if a MongoDB database exists and has required collections.
    
    Returns information about the database status.
    """
    db = request.app.state.db
    
    try:
        import asyncio
        from backend.core.database import get_db_executor
        
        loop = asyncio.get_running_loop()
        
        def check_db_sync():
            sync_client = db.get_sync_client()
            
            # List all databases
            db_list = sync_client.list_database_names()
            exists = database_name in db_list
            
            if exists:
                mongo_db = sync_client[database_name]
                collections = mongo_db.list_collection_names()
                
                # Check for required RAG collections
                has_documents = settings.mongodb_collection_documents in collections
                has_chunks = settings.mongodb_collection_chunks in collections
                
                # Get counts
                doc_count = 0
                chunk_count = 0
                if has_documents:
                    doc_count = mongo_db[settings.mongodb_collection_documents].estimated_document_count()
                if has_chunks:
                    chunk_count = mongo_db[settings.mongodb_collection_chunks].estimated_document_count()
                
                return {
                    "database": database_name,
                    "exists": True,
                    "has_collections": has_documents or has_chunks,
                    "collections": collections,
                    "documents_count": doc_count,
                    "chunks_count": chunk_count,
                    "has_documents_collection": has_documents,
                    "has_chunks_collection": has_chunks,
                    "can_create": False
                }
            else:
                return {
                    "database": database_name,
                    "exists": False,
                    "has_collections": False,
                    "collections": [],
                    "documents_count": 0,
                    "chunks_count": 0,
                    "has_documents_collection": False,
                    "has_chunks_collection": False,
                    "can_create": True
                }
        
        return await loop.run_in_executor(get_db_executor(), check_db_sync)
        
    except Exception as e:
        logger.error(f"Failed to check database: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/database/create")
async def create_database(request: Request, create_request: DatabaseCreateRequest):
    """
    Create a MongoDB database with required RAG collections.
    
    Creates the documents and chunks collections for the specified database.
    Optionally creates search indexes.
    """
    db = request.app.state.db
    database_name = create_request.database
    
    try:
        import asyncio
        from backend.core.database import get_db_executor
        
        loop = asyncio.get_running_loop()
        
        def create_db_sync():
            sync_client = db.get_sync_client()
            mongo_db = sync_client[database_name]
            
            results = {
                "database": database_name,
                "created": True,
                "collections_created": [],
                "indexes_created": [],
                "errors": []
            }
            
            # Create documents collection
            try:
                if settings.mongodb_collection_documents not in mongo_db.list_collection_names():
                    mongo_db.create_collection(settings.mongodb_collection_documents)
                    results["collections_created"].append(settings.mongodb_collection_documents)
                    logger.info(f"Created collection: {database_name}.{settings.mongodb_collection_documents}")
            except Exception as e:
                results["errors"].append(f"Failed to create documents collection: {e}")
            
            # Create chunks collection
            try:
                if settings.mongodb_collection_chunks not in mongo_db.list_collection_names():
                    mongo_db.create_collection(settings.mongodb_collection_chunks)
                    results["collections_created"].append(settings.mongodb_collection_chunks)
                    logger.info(f"Created collection: {database_name}.{settings.mongodb_collection_chunks}")
            except Exception as e:
                results["errors"].append(f"Failed to create chunks collection: {e}")
            
            # Create standard indexes on collections
            try:
                docs_coll = mongo_db[settings.mongodb_collection_documents]
                docs_coll.create_index("source", background=True)
                docs_coll.create_index("created_at", background=True)
                results["indexes_created"].append(f"{settings.mongodb_collection_documents}.source")
                results["indexes_created"].append(f"{settings.mongodb_collection_documents}.created_at")
            except Exception as e:
                results["errors"].append(f"Failed to create document indexes: {e}")
            
            try:
                chunks_coll = mongo_db[settings.mongodb_collection_chunks]
                chunks_coll.create_index("document_id", background=True)
                chunks_coll.create_index("created_at", background=True)
                results["indexes_created"].append(f"{settings.mongodb_collection_chunks}.document_id")
                results["indexes_created"].append(f"{settings.mongodb_collection_chunks}.created_at")
            except Exception as e:
                results["errors"].append(f"Failed to create chunk indexes: {e}")
            
            return results
        
        result = await loop.run_in_executor(get_db_executor(), create_db_sync)
        return result
        
    except Exception as e:
        logger.error(f"Failed to create database: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/database/test-connection")
async def test_database_connection(request: Request):
    """
    Test the MongoDB connection and return status.
    """
    db = request.app.state.db
    
    try:
        # Ping the database
        result = await db.client.admin.command('ping')
        
        # Get server info
        server_info = await db.client.server_info()
        
        # Extract URI host safely
        try:
            uri_host = settings.mongodb_uri.split("@")[-1].split("/")[0] if "@" in settings.mongodb_uri else "localhost"
        except Exception:
            uri_host = "localhost"
        
        return {
            "connected": True,
            "ping_ok": result.get("ok") == 1.0,  # Don't return raw result - contains non-serializable Timestamp
            "server_version": server_info.get("version"),
            "current_database": db.current_database_name,
            "uri_host": uri_host
        }
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        return {
            "connected": False,
            "error": str(e)
        }


# ==================== Airbyte Status ====================

@router.get("/airbyte/status")
async def get_airbyte_status():
    """
    Check Airbyte service status and availability.
    """
    if not settings.airbyte_enabled:
        return {
            "enabled": False,
            "available": False,
            "message": "Airbyte integration is disabled"
        }
    
    try:
        import httpx
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Check Airbyte health endpoint
            response = await client.get(f"{settings.airbyte_api_url}/api/v1/health")
            
            if response.status_code == 200:
                return {
                    "enabled": True,
                    "available": True,
                    "api_url": settings.airbyte_api_url,
                    "webapp_url": settings.airbyte_webapp_url,
                    "status": response.json() if response.headers.get("content-type", "").startswith("application/json") else "healthy"
                }
            else:
                return {
                    "enabled": True,
                    "available": False,
                    "api_url": settings.airbyte_api_url,
                    "status_code": response.status_code,
                    "message": "Airbyte API returned non-200 status"
                }
                
    except httpx.ConnectError:
        return {
            "enabled": True,
            "available": False,
            "api_url": settings.airbyte_api_url,
            "message": "Cannot connect to Airbyte. Make sure Airbyte containers are running."
        }
    except Exception as e:
        logger.error(f"Failed to check Airbyte status: {e}")
        return {
            "enabled": True,
            "available": False,
            "api_url": settings.airbyte_api_url,
            "error": str(e)
        }


@router.get("/email-providers")
async def list_email_providers():
    """
    List available email providers and their configuration requirements.
    """
    return {
        "providers": [
            {
                "type": "gmail",
                "display_name": "Gmail",
                "icon": "📧",
                "auth_type": "oauth2",
                "description": "Connect to Gmail via Google OAuth",
                "requires_airbyte": True,
                "config_fields": []
            },
            {
                "type": "outlook",
                "display_name": "Outlook / Microsoft 365",
                "icon": "📨",
                "auth_type": "oauth2",
                "description": "Connect to Outlook via Microsoft OAuth",
                "requires_airbyte": True,
                "config_fields": []
            },
            {
                "type": "imap",
                "display_name": "IMAP (Generic)",
                "icon": "✉️",
                "auth_type": "password",
                "description": "Connect to any email server via IMAP",
                "requires_airbyte": True,
                "config_fields": [
                    {"name": "host", "label": "IMAP Server", "type": "text", "required": True, "placeholder": "imap.example.com"},
                    {"name": "port", "label": "Port", "type": "number", "required": True, "default": 993},
                    {"name": "username", "label": "Email Address", "type": "email", "required": True},
                    {"name": "password", "label": "Password / App Password", "type": "password", "required": True},
                    {"name": "ssl", "label": "Use SSL", "type": "checkbox", "default": True},
                    {"name": "folders", "label": "Folders to sync", "type": "text", "placeholder": "INBOX, Sent", "required": False}
                ]
            }
        ]
    }


@router.get("/cloud-storage-providers")
async def list_cloud_storage_providers():
    """
    List available cloud storage providers and their configuration requirements.
    """
    return {
        "providers": [
            {
                "type": "google_drive",
                "display_name": "Google Drive",
                "icon": "🔵",
                "auth_type": "oauth2",
                "description": "Connect to Google Drive for document syncing",
                "requires_airbyte": False,
                "supports_multiple": True,
                "config_fields": []
            },
            {
                "type": "dropbox",
                "display_name": "Dropbox",
                "icon": "📦",
                "auth_type": "oauth2",
                "description": "Connect to Dropbox for document syncing",
                "requires_airbyte": False,
                "supports_multiple": True,
                "config_fields": []
            },
            {
                "type": "onedrive",
                "display_name": "OneDrive / SharePoint",
                "icon": "☁️",
                "auth_type": "oauth2",
                "description": "Connect to Microsoft OneDrive or SharePoint",
                "requires_airbyte": False,
                "supports_multiple": True,
                "config_fields": []
            },
            {
                "type": "webdav",
                "display_name": "WebDAV (Nextcloud, ownCloud)",
                "icon": "🌐",
                "auth_type": "password",
                "description": "Connect to any WebDAV server",
                "requires_airbyte": False,
                "supports_multiple": True,
                "config_fields": [
                    {"name": "url", "label": "WebDAV URL", "type": "url", "required": True, "placeholder": "https://cloud.example.com/remote.php/dav/files/user"},
                    {"name": "username", "label": "Username", "type": "text", "required": True},
                    {"name": "password", "label": "Password", "type": "password", "required": True}
                ]
            },
            {
                "type": "confluence",
                "display_name": "Confluence",
                "icon": "📝",
                "auth_type": "api_key",
                "description": "Connect to Atlassian Confluence wiki",
                "requires_airbyte": True,
                "supports_multiple": True,
                "config_fields": [
                    {"name": "domain", "label": "Confluence Domain", "type": "text", "required": True, "placeholder": "your-domain.atlassian.net"},
                    {"name": "email", "label": "Email", "type": "email", "required": True},
                    {"name": "api_token", "label": "API Token", "type": "password", "required": True}
                ]
            },
            {
                "type": "jira",
                "display_name": "Jira",
                "icon": "🔷",
                "auth_type": "api_key",
                "description": "Connect to Atlassian Jira for issue tracking",
                "requires_airbyte": True,
                "supports_multiple": True,
                "config_fields": [
                    {"name": "domain", "label": "Jira Domain", "type": "text", "required": True, "placeholder": "your-domain.atlassian.net"},
                    {"name": "email", "label": "Email", "type": "email", "required": True},
                    {"name": "api_token", "label": "API Token", "type": "password", "required": True}
                ]
            }
        ]
    }
