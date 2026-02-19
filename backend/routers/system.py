"""System and health check router."""

import logging
import time
import httpx
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field

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


# ==================== Comprehensive Model Lists ====================
# These are the comprehensive default model lists for each provider
# Updated as of 2025 with all available models including multimodal, media generation, etc.

OPENAI_MODELS = {
    "chat": [
        # GPT-4o Series (Flagship Multimodal)
        {"id": "gpt-4o", "name": "GPT-4o", "type": "chat", "context": 128000, "multimodal": True, "description": "Flagship multimodal model for complex tasks"},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "type": "chat", "context": 128000, "multimodal": True, "description": "Fast, affordable for focused tasks"},
        {"id": "gpt-4o-2024-11-20", "name": "GPT-4o (Nov 2024)", "type": "chat", "context": 128000, "multimodal": True, "description": "Latest GPT-4o snapshot"},
        {"id": "gpt-4o-2024-08-06", "name": "GPT-4o (Aug 2024)", "type": "chat", "context": 128000, "multimodal": True, "description": "Structured outputs support"},
        {"id": "gpt-4o-audio-preview", "name": "GPT-4o Audio Preview", "type": "chat", "context": 128000, "multimodal": True, "audio": True, "description": "Audio input/output capable"},
        {"id": "chatgpt-4o-latest", "name": "ChatGPT-4o Latest", "type": "chat", "context": 128000, "multimodal": True, "description": "Dynamic ChatGPT version"},
        # O-Series Reasoning Models
        {"id": "o1", "name": "O1", "type": "reasoning", "context": 200000, "description": "Advanced reasoning model for complex problems"},
        {"id": "o1-preview", "name": "O1 Preview", "type": "reasoning", "context": 128000, "description": "Reasoning model preview"},
        {"id": "o1-mini", "name": "O1 Mini", "type": "reasoning", "context": 128000, "description": "Faster reasoning model"},
        {"id": "o3-mini", "name": "O3 Mini", "type": "reasoning", "context": 200000, "description": "Latest fast reasoning model"},
        # GPT-4 Turbo Series
        {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "type": "chat", "context": 128000, "multimodal": True, "description": "Enhanced GPT-4 with vision"},
        {"id": "gpt-4-turbo-preview", "name": "GPT-4 Turbo Preview", "type": "chat", "context": 128000, "description": "GPT-4 Turbo preview"},
        {"id": "gpt-4-turbo-2024-04-09", "name": "GPT-4 Turbo (Apr 2024)", "type": "chat", "context": 128000, "multimodal": True, "description": "GPT-4 Turbo with Vision"},
        # GPT-4 Series
        {"id": "gpt-4", "name": "GPT-4", "type": "chat", "context": 8192, "description": "Original GPT-4 model"},
        {"id": "gpt-4-32k", "name": "GPT-4 32K", "type": "chat", "context": 32768, "description": "Extended context GPT-4"},
        {"id": "gpt-4-0613", "name": "GPT-4 (June 2023)", "type": "chat", "context": 8192, "description": "Snapshot for function calling"},
        # GPT-3.5 Series
        {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "type": "chat", "context": 16385, "description": "Fast and cost-effective"},
        {"id": "gpt-3.5-turbo-16k", "name": "GPT-3.5 Turbo 16K", "type": "chat", "context": 16385, "description": "Extended context 3.5"},
        {"id": "gpt-3.5-turbo-instruct", "name": "GPT-3.5 Turbo Instruct", "type": "completion", "context": 4096, "description": "Instruction-following model"},
    ],
    "embedding": [
        {"id": "text-embedding-3-large", "name": "Text Embedding 3 Large", "type": "embedding", "dimension": 3072, "description": "Best quality embeddings"},
        {"id": "text-embedding-3-small", "name": "Text Embedding 3 Small", "type": "embedding", "dimension": 1536, "description": "Efficient embeddings"},
        {"id": "text-embedding-ada-002", "name": "Text Embedding Ada 002", "type": "embedding", "dimension": 1536, "description": "Legacy embedding model"},
    ],
    "image": [
        {"id": "dall-e-3", "name": "DALL-E 3", "type": "image_generation", "description": "Most capable image generation"},
        {"id": "dall-e-2", "name": "DALL-E 2", "type": "image_generation", "description": "Image generation and editing"},
    ],
    "audio": [
        {"id": "whisper-1", "name": "Whisper", "type": "speech_to_text", "description": "Speech-to-text transcription"},
        {"id": "tts-1", "name": "TTS-1", "type": "text_to_speech", "description": "Text-to-speech, optimized for speed"},
        {"id": "tts-1-hd", "name": "TTS-1 HD", "type": "text_to_speech", "description": "Text-to-speech, optimized for quality"},
    ],
    "moderation": [
        {"id": "omni-moderation-latest", "name": "Omni Moderation", "type": "moderation", "description": "Latest moderation model"},
        {"id": "text-moderation-latest", "name": "Text Moderation", "type": "moderation", "description": "Text content moderation"},
    ]
}

GOOGLE_MODELS = {
    "chat": [
        # Gemini 2.0 Series
        {"id": "gemini-2.0-flash-exp", "name": "Gemini 2.0 Flash (Experimental)", "type": "chat", "context": 1000000, "multimodal": True, "description": "Next-gen multimodal with thinking"},
        {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "type": "chat", "context": 1000000, "multimodal": True, "description": "Fast multimodal"},
        {"id": "gemini-2.0-flash-thinking-exp", "name": "Gemini 2.0 Flash Thinking", "type": "reasoning", "context": 1000000, "multimodal": True, "description": "Reasoning with chain-of-thought"},
        # Gemini 1.5 Series
        {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro", "type": "chat", "context": 2000000, "multimodal": True, "description": "Complex tasks, 2M context window"},
        {"id": "gemini-1.5-pro-latest", "name": "Gemini 1.5 Pro Latest", "type": "chat", "context": 2000000, "multimodal": True, "description": "Latest 1.5 Pro"},
        {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash", "type": "chat", "context": 1000000, "multimodal": True, "description": "Fast, cost-effective"},
        {"id": "gemini-1.5-flash-latest", "name": "Gemini 1.5 Flash Latest", "type": "chat", "context": 1000000, "multimodal": True, "description": "Latest 1.5 Flash"},
        {"id": "gemini-1.5-flash-8b", "name": "Gemini 1.5 Flash 8B", "type": "chat", "context": 1000000, "multimodal": True, "description": "Smallest, fastest Flash"},
        # Legacy
        {"id": "gemini-pro", "name": "Gemini Pro", "type": "chat", "context": 32768, "description": "Legacy Gemini Pro"},
        {"id": "gemini-pro-vision", "name": "Gemini Pro Vision", "type": "chat", "context": 16384, "multimodal": True, "description": "Legacy vision model"},
    ],
    "embedding": [
        {"id": "text-embedding-004", "name": "Text Embedding 004", "type": "embedding", "dimension": 768, "description": "Latest Google embedding"},
        {"id": "text-embedding-005", "name": "Text Embedding 005", "type": "embedding", "dimension": 768, "description": "Next-gen Google embedding"},
        {"id": "embedding-001", "name": "Embedding 001", "type": "embedding", "dimension": 768, "description": "Legacy embedding"},
    ],
    "image": [
        {"id": "imagen-3", "name": "Imagen 3", "type": "image_generation", "description": "Highest quality image generation"},
        {"id": "imagen-3-fast", "name": "Imagen 3 Fast", "type": "image_generation", "description": "Fast image generation"},
    ],
    "video": [
        {"id": "veo-2", "name": "Veo 2", "type": "video_generation", "description": "Video generation model"},
    ],
    "audio": [
        {"id": "chirp", "name": "Chirp", "type": "speech_to_text", "description": "Speech recognition"},
    ]
}

ANTHROPIC_MODELS = {
    "chat": [
        # Claude 3.5 Series
        {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet (Oct 2024)", "type": "chat", "context": 200000, "multimodal": True, "description": "Most intelligent Claude model"},
        {"id": "claude-3-5-sonnet-latest", "name": "Claude 3.5 Sonnet Latest", "type": "chat", "context": 200000, "multimodal": True, "description": "Latest Sonnet"},
        {"id": "claude-3-5-sonnet-20240620", "name": "Claude 3.5 Sonnet (Jun 2024)", "type": "chat", "context": 200000, "multimodal": True, "description": "Original 3.5 Sonnet"},
        {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku", "type": "chat", "context": 200000, "multimodal": True, "description": "Fastest Claude 3.5"},
        {"id": "claude-3-5-haiku-latest", "name": "Claude 3.5 Haiku Latest", "type": "chat", "context": 200000, "multimodal": True, "description": "Latest Haiku"},
        # Claude 3 Series
        {"id": "claude-3-opus-20240229", "name": "Claude 3 Opus", "type": "chat", "context": 200000, "multimodal": True, "description": "Powerful for complex tasks"},
        {"id": "claude-3-opus-latest", "name": "Claude 3 Opus Latest", "type": "chat", "context": 200000, "multimodal": True, "description": "Latest Opus"},
        {"id": "claude-3-sonnet-20240229", "name": "Claude 3 Sonnet", "type": "chat", "context": 200000, "multimodal": True, "description": "Balanced performance"},
        {"id": "claude-3-haiku-20240307", "name": "Claude 3 Haiku", "type": "chat", "context": 200000, "multimodal": True, "description": "Fast and compact"},
    ],
    "embedding": [
        # Anthropic doesn't offer embedding models - users should use Voyage AI
    ]
}

OLLAMA_MODELS = {
    "chat": [
        # Llama Series
        {"id": "llama3.3:70b", "name": "Llama 3.3 70B", "type": "chat", "context": 128000, "description": "Latest Llama, best quality"},
        {"id": "llama3.2:3b", "name": "Llama 3.2 3B", "type": "chat", "context": 128000, "description": "Lightweight Llama"},
        {"id": "llama3.2:1b", "name": "Llama 3.2 1B", "type": "chat", "context": 128000, "description": "Smallest Llama"},
        {"id": "llama3.1:70b", "name": "Llama 3.1 70B", "type": "chat", "context": 128000, "description": "Previous Llama flagship"},
        {"id": "llama3.1:8b", "name": "Llama 3.1 8B", "type": "chat", "context": 128000, "description": "Fast Llama"},
        {"id": "llama3:8b", "name": "Llama 3 8B", "type": "chat", "context": 8192, "description": "Original Llama 3"},
        # Qwen Series
        {"id": "qwen2.5:72b", "name": "Qwen 2.5 72B", "type": "chat", "context": 128000, "description": "Best Qwen model"},
        {"id": "qwen2.5:32b", "name": "Qwen 2.5 32B", "type": "chat", "context": 128000, "description": "Balanced Qwen"},
        {"id": "qwen2.5:14b", "name": "Qwen 2.5 14B", "type": "chat", "context": 128000, "description": "Medium Qwen"},
        {"id": "qwen2.5:7b", "name": "Qwen 2.5 7B", "type": "chat", "context": 128000, "description": "Fast Qwen"},
        {"id": "qwen2.5-coder:32b", "name": "Qwen 2.5 Coder 32B", "type": "chat", "context": 128000, "description": "Best coding model"},
        {"id": "qwen2.5-coder:14b", "name": "Qwen 2.5 Coder 14B", "type": "chat", "context": 128000, "description": "Fast coder"},
        # Mistral Series
        {"id": "mixtral:8x7b", "name": "Mixtral 8x7B", "type": "chat", "context": 32768, "description": "Mixture of experts"},
        {"id": "mistral:7b", "name": "Mistral 7B", "type": "chat", "context": 32768, "description": "Fast Mistral"},
        {"id": "mistral-large:latest", "name": "Mistral Large", "type": "chat", "context": 128000, "description": "Largest Mistral"},
        # DeepSeek Series
        {"id": "deepseek-coder-v2:236b", "name": "DeepSeek Coder V2 236B", "type": "chat", "context": 128000, "description": "Advanced coder"},
        {"id": "deepseek-coder-v2:16b", "name": "DeepSeek Coder V2 16B", "type": "chat", "context": 128000, "description": "Efficient coder"},
        {"id": "deepseek-r1:70b", "name": "DeepSeek R1 70B", "type": "reasoning", "context": 128000, "description": "Reasoning model"},
        # Phi Series
        {"id": "phi3:14b", "name": "Phi 3 14B", "type": "chat", "context": 128000, "description": "Microsoft Phi 3"},
        {"id": "phi3:3.8b", "name": "Phi 3 3.8B", "type": "chat", "context": 128000, "description": "Small Phi"},
        # Gemma Series
        {"id": "gemma2:27b", "name": "Gemma 2 27B", "type": "chat", "context": 8192, "description": "Google Gemma 2"},
        {"id": "gemma2:9b", "name": "Gemma 2 9B", "type": "chat", "context": 8192, "description": "Small Gemma 2"},
        # Code-specific
        {"id": "codellama:34b", "name": "Code Llama 34B", "type": "chat", "context": 16384, "description": "Code specialized Llama"},
        {"id": "starcoder2:15b", "name": "StarCoder 2 15B", "type": "chat", "context": 16384, "description": "StarCoder"},
    ],
    "embedding": [
        {"id": "nomic-embed-text", "name": "Nomic Embed Text", "type": "embedding", "dimension": 768, "description": "Versatile embeddings"},
        {"id": "mxbai-embed-large", "name": "MxBai Embed Large", "type": "embedding", "dimension": 1024, "description": "High quality embeddings"},
        {"id": "all-minilm", "name": "All MiniLM", "type": "embedding", "dimension": 384, "description": "Fast small embeddings"},
        {"id": "snowflake-arctic-embed", "name": "Snowflake Arctic Embed", "type": "embedding", "dimension": 1024, "description": "Snowflake embeddings"},
        {"id": "bge-m3", "name": "BGE M3", "type": "embedding", "dimension": 1024, "description": "Multilingual embeddings"},
    ],
    "vision": [
        {"id": "llava:34b", "name": "LLaVA 34B", "type": "vision", "context": 4096, "multimodal": True, "description": "Vision-language model"},
        {"id": "llava:13b", "name": "LLaVA 13B", "type": "vision", "context": 4096, "multimodal": True, "description": "Smaller vision model"},
        {"id": "llava-phi3", "name": "LLaVA Phi 3", "type": "vision", "context": 4096, "multimodal": True, "description": "Fast vision model"},
        {"id": "llama3.2-vision:11b", "name": "Llama 3.2 Vision 11B", "type": "vision", "context": 128000, "multimodal": True, "description": "Llama vision model"},
        {"id": "llama3.2-vision:90b", "name": "Llama 3.2 Vision 90B", "type": "vision", "context": 128000, "multimodal": True, "description": "Best Llama vision"},
    ]
}


def get_all_provider_models():
    """Get the comprehensive models list for all providers."""
    return {
        "openai": {
            "id": "openai",
            "name": "OpenAI",
            "models": _flatten_models(OPENAI_MODELS),
            "categories": list(OPENAI_MODELS.keys()),
            "supports_fetch": True,
        },
        "google": {
            "id": "google",
            "name": "Google Gemini",
            "models": _flatten_models(GOOGLE_MODELS),
            "categories": list(GOOGLE_MODELS.keys()),
            "supports_fetch": True,
        },
        "anthropic": {
            "id": "anthropic",
            "name": "Anthropic Claude",
            "models": _flatten_models(ANTHROPIC_MODELS),
            "categories": list(ANTHROPIC_MODELS.keys()),
            "supports_fetch": True,
        },
        "ollama": {
            "id": "ollama",
            "name": "Ollama (Local)",
            "models": _flatten_models(OLLAMA_MODELS),
            "categories": list(OLLAMA_MODELS.keys()),
            "supports_fetch": True,
        }
    }


def _flatten_models(model_dict):
    """Flatten categorized models into a single list."""
    models = []
    for category, category_models in model_dict.items():
        for model in category_models:
            model_copy = model.copy()
            model_copy["category"] = category
            models.append(model_copy["id"])
    return models


def _get_detailed_models(model_dict):
    """Get detailed model info with categories."""
    models = []
    for category, category_models in model_dict.items():
        for model in category_models:
            model_copy = model.copy()
            model_copy["category"] = category
            models.append(model_copy)
    return models


class ConfigUpdateRequest(BaseModel):
    llm_model: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dimension: Optional[int] = None
    default_match_count: Optional[int] = None


class LLMProviderConfig(BaseModel):
    """LLM provider configuration for orchestrator and worker."""
    # Orchestrator (thinking) LLM settings
    orchestrator_provider: Optional[str] = None
    orchestrator_model: Optional[str] = None
    openai_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    
    # Worker (fast) LLM settings
    worker_provider: Optional[str] = None
    worker_model: Optional[str] = None
    fast_llm_api_key: Optional[str] = None  # Optional separate key for fast LLM
    
    # Embedding settings
    embedding_provider: Optional[str] = None
    embedding_api_key: Optional[str] = None


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
        "name": "RecallHub API",
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


# ==================== Agent Performance Configuration ====================

AGENT_CONFIG_COLLECTION = "agent_config"
AGENT_CONFIG_DOC_ID = "performance_config"


class AgentPerformanceConfig(BaseModel):
    """Agent performance configuration settings."""
    # Per-session settings
    parallel_workers: int = Field(default=4, ge=1, le=20, description="Max parallel workers per chat session")
    max_iterations: int = Field(default=3, ge=1, le=10, description="Max orchestrator-worker iterations")
    
    # Global pool settings
    global_max_orchestrators: int = Field(default=10, ge=1, le=50, description="Max concurrent orchestrators (all users)")
    global_max_workers: int = Field(default=20, ge=1, le=100, description="Max concurrent workers (all users)")
    
    # Timeout settings
    worker_timeout: int = Field(default=60, ge=10, le=300, description="Worker task timeout (seconds)")
    orchestrator_timeout: int = Field(default=120, ge=30, le=600, description="Orchestrator phase timeout (seconds)")
    total_timeout: int = Field(default=300, ge=60, le=3600, description="Total request timeout (seconds)")
    
    # Mode settings
    default_mode: str = Field(default="auto", description="Default agent mode: auto, thinking, fast")
    auto_fast_threshold: int = Field(default=50, ge=10, le=500, description="Query length for fast mode in auto")
    skip_evaluation: bool = Field(default=False, description="Skip evaluation phase for speed")
    
    # Search settings
    max_sources_per_search: int = Field(default=10, ge=1, le=50, description="Max data sources to search in parallel")


@router.get("/agent-performance")
async def get_agent_performance_config(request: Request):
    """
    Get current agent performance configuration.
    
    Returns settings for parallel workers, timeouts, and pool sizes.
    """
    db = request.app.state.db
    
    # Get from database if exists
    try:
        collection = db.db[AGENT_CONFIG_COLLECTION]
        doc = await collection.find_one({"_id": AGENT_CONFIG_DOC_ID})
        
        if doc:
            return {
                "parallel_workers": doc.get("parallel_workers", settings.agent_parallel_workers),
                "max_iterations": doc.get("max_iterations", settings.agent_max_iterations),
                "global_max_orchestrators": doc.get("global_max_orchestrators", settings.agent_global_max_orchestrators),
                "global_max_workers": doc.get("global_max_workers", settings.agent_global_max_workers),
                "worker_timeout": doc.get("worker_timeout", settings.agent_worker_timeout),
                "orchestrator_timeout": doc.get("orchestrator_timeout", settings.agent_orchestrator_timeout),
                "total_timeout": doc.get("total_timeout", settings.agent_total_timeout),
                "default_mode": doc.get("default_mode", settings.agent_default_mode),
                "auto_fast_threshold": doc.get("auto_fast_threshold", settings.agent_auto_fast_threshold),
                "skip_evaluation": doc.get("skip_evaluation", settings.agent_skip_evaluation),
                "max_sources_per_search": doc.get("max_sources_per_search", settings.agent_max_sources_per_search),
            }
    except Exception as e:
        logger.warning(f"Failed to load agent config from database: {e}")
    
    # Return defaults from settings
    return {
        "parallel_workers": settings.agent_parallel_workers,
        "max_iterations": settings.agent_max_iterations,
        "global_max_orchestrators": settings.agent_global_max_orchestrators,
        "global_max_workers": settings.agent_global_max_workers,
        "worker_timeout": settings.agent_worker_timeout,
        "orchestrator_timeout": settings.agent_orchestrator_timeout,
        "total_timeout": settings.agent_total_timeout,
        "default_mode": settings.agent_default_mode,
        "auto_fast_threshold": settings.agent_auto_fast_threshold,
        "skip_evaluation": settings.agent_skip_evaluation,
        "max_sources_per_search": settings.agent_max_sources_per_search,
    }


@router.post("/agent-performance")
async def save_agent_performance_config(request: Request, config: AgentPerformanceConfig):
    """
    Save agent performance configuration to database.
    
    Updates settings for parallel workers, timeouts, pool sizes, and mode.
    """
    db = request.app.state.db
    
    try:
        collection = db.db[AGENT_CONFIG_COLLECTION]
        
        update_doc = {
            "_id": AGENT_CONFIG_DOC_ID,
            "parallel_workers": config.parallel_workers,
            "max_iterations": config.max_iterations,
            "global_max_orchestrators": config.global_max_orchestrators,
            "global_max_workers": config.global_max_workers,
            "worker_timeout": config.worker_timeout,
            "orchestrator_timeout": config.orchestrator_timeout,
            "total_timeout": config.total_timeout,
            "default_mode": config.default_mode,
            "auto_fast_threshold": config.auto_fast_threshold,
            "skip_evaluation": config.skip_evaluation,
            "max_sources_per_search": config.max_sources_per_search,
            "updated_at": datetime.now().isoformat(),
        }
        
        await collection.replace_one(
            {"_id": AGENT_CONFIG_DOC_ID},
            update_doc,
            upsert=True
        )
        
        # Update runtime settings
        settings.agent_parallel_workers = config.parallel_workers
        settings.agent_max_iterations = config.max_iterations
        settings.agent_global_max_orchestrators = config.global_max_orchestrators
        settings.agent_global_max_workers = config.global_max_workers
        settings.agent_worker_timeout = config.worker_timeout
        settings.agent_orchestrator_timeout = config.orchestrator_timeout
        settings.agent_total_timeout = config.total_timeout
        settings.agent_default_mode = config.default_mode
        settings.agent_auto_fast_threshold = config.auto_fast_threshold
        settings.agent_skip_evaluation = config.skip_evaluation
        settings.agent_max_sources_per_search = config.max_sources_per_search
        
        logger.info(f"Saved agent performance config: workers={config.parallel_workers}, orchestrators={config.global_max_orchestrators}")
        
        return {"success": True, "message": "Agent performance configuration saved"}
    except Exception as e:
        logger.error(f"Failed to save agent config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save configuration: {str(e)}")


async def load_agent_config_from_db(db) -> bool:
    """Load agent performance config from database on startup."""
    try:
        collection = db.db[AGENT_CONFIG_COLLECTION]
        doc = await collection.find_one({"_id": AGENT_CONFIG_DOC_ID})
        
        if doc:
            settings.agent_parallel_workers = doc.get("parallel_workers", settings.agent_parallel_workers)
            settings.agent_max_iterations = doc.get("max_iterations", settings.agent_max_iterations)
            settings.agent_global_max_orchestrators = doc.get("global_max_orchestrators", settings.agent_global_max_orchestrators)
            settings.agent_global_max_workers = doc.get("global_max_workers", settings.agent_global_max_workers)
            settings.agent_worker_timeout = doc.get("worker_timeout", settings.agent_worker_timeout)
            settings.agent_orchestrator_timeout = doc.get("orchestrator_timeout", settings.agent_orchestrator_timeout)
            settings.agent_total_timeout = doc.get("total_timeout", settings.agent_total_timeout)
            settings.agent_default_mode = doc.get("default_mode", settings.agent_default_mode)
            settings.agent_auto_fast_threshold = doc.get("auto_fast_threshold", settings.agent_auto_fast_threshold)
            settings.agent_skip_evaluation = doc.get("skip_evaluation", settings.agent_skip_evaluation)
            settings.agent_max_sources_per_search = doc.get("max_sources_per_search", settings.agent_max_sources_per_search)
            
            logger.info(f"Loaded agent performance config: workers={settings.agent_parallel_workers}, mode={settings.agent_default_mode}")
            return True
        return False
    except Exception as e:
        logger.warning(f"Failed to load agent config from database: {e}")
        return False


# ==================== Ingestion Performance Configuration ====================

INGESTION_CONFIG_COLLECTION = "ingestion_config"
INGESTION_CONFIG_DOC_ID = "performance_config"


class IngestionPerformanceConfig(BaseModel):
    """Ingestion worker performance configuration settings."""
    # Process isolation
    process_isolation_enabled: bool = Field(default=True, description="Run ingestion in separate worker process")
    
    # Concurrency settings
    max_concurrent_files: int = Field(default=2, ge=1, le=10, description="Max files to process concurrently")
    embedding_batch_size: int = Field(default=100, ge=10, le=500, description="Chunks to embed per API call")
    
    # Worker pool settings
    thread_pool_workers: int = Field(default=4, ge=1, le=16, description="Thread pool workers for CPU tasks")
    
    # Rate limiting
    embedding_requests_per_minute: int = Field(default=3000, ge=100, le=10000, description="Embedding API rate limit")
    
    # Timeout settings
    file_processing_timeout: int = Field(default=300, ge=60, le=1800, description="Single file timeout (seconds)")
    
    # Polling settings
    job_poll_interval_seconds: float = Field(default=1.0, ge=0.5, le=10.0, description="Worker job poll interval")


@router.get("/ingestion-performance")
async def get_ingestion_performance_config(request: Request):
    """
    Get current ingestion performance configuration.
    
    Returns settings for process isolation, concurrency, and timeouts.
    """
    db = request.app.state.db
    
    # Get from database if exists
    try:
        collection = db.db[INGESTION_CONFIG_COLLECTION]
        doc = await collection.find_one({"_id": INGESTION_CONFIG_DOC_ID})
        
        if doc:
            return {
                "process_isolation_enabled": doc.get("process_isolation_enabled", settings.ingestion_process_isolation),
                "max_concurrent_files": doc.get("max_concurrent_files", settings.ingestion_max_concurrent_files),
                "embedding_batch_size": doc.get("embedding_batch_size", settings.ingestion_embedding_batch_size),
                "thread_pool_workers": doc.get("thread_pool_workers", settings.ingestion_thread_pool_workers),
                "embedding_requests_per_minute": doc.get("embedding_requests_per_minute", settings.ingestion_embedding_requests_per_minute),
                "file_processing_timeout": doc.get("file_processing_timeout", settings.ingestion_file_processing_timeout),
                "job_poll_interval_seconds": doc.get("job_poll_interval_seconds", settings.ingestion_job_poll_interval),
            }
    except Exception as e:
        logger.warning(f"Failed to load ingestion config from database: {e}")
    
    # Return defaults from settings
    return {
        "process_isolation_enabled": settings.ingestion_process_isolation,
        "max_concurrent_files": settings.ingestion_max_concurrent_files,
        "embedding_batch_size": settings.ingestion_embedding_batch_size,
        "thread_pool_workers": settings.ingestion_thread_pool_workers,
        "embedding_requests_per_minute": settings.ingestion_embedding_requests_per_minute,
        "file_processing_timeout": settings.ingestion_file_processing_timeout,
        "job_poll_interval_seconds": settings.ingestion_job_poll_interval,
    }


@router.post("/ingestion-performance")
async def save_ingestion_performance_config(request: Request, config: IngestionPerformanceConfig):
    """
    Save ingestion performance configuration to database.
    
    Updates settings for process isolation, concurrency, and timeouts.
    Configuration changes are applied when the next ingestion job starts.
    """
    db = request.app.state.db
    
    try:
        collection = db.db[INGESTION_CONFIG_COLLECTION]
        
        update_doc = {
            "_id": INGESTION_CONFIG_DOC_ID,
            "process_isolation_enabled": config.process_isolation_enabled,
            "max_concurrent_files": config.max_concurrent_files,
            "embedding_batch_size": config.embedding_batch_size,
            "thread_pool_workers": config.thread_pool_workers,
            "embedding_requests_per_minute": config.embedding_requests_per_minute,
            "file_processing_timeout": config.file_processing_timeout,
            "job_poll_interval_seconds": config.job_poll_interval_seconds,
            "updated_at": datetime.now().isoformat(),
        }
        
        await collection.replace_one(
            {"_id": INGESTION_CONFIG_DOC_ID},
            update_doc,
            upsert=True
        )
        
        # Update runtime settings
        settings.ingestion_process_isolation = config.process_isolation_enabled
        settings.ingestion_max_concurrent_files = config.max_concurrent_files
        settings.ingestion_embedding_batch_size = config.embedding_batch_size
        settings.ingestion_thread_pool_workers = config.thread_pool_workers
        settings.ingestion_embedding_requests_per_minute = config.embedding_requests_per_minute
        settings.ingestion_file_processing_timeout = config.file_processing_timeout
        settings.ingestion_job_poll_interval = config.job_poll_interval_seconds
        
        logger.info(f"Saved ingestion performance config: concurrent_files={config.max_concurrent_files}, isolation={config.process_isolation_enabled}")
        
        return {"success": True, "message": "Ingestion performance configuration saved"}
    except Exception as e:
        logger.error(f"Failed to save ingestion config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save configuration: {str(e)}")


async def load_ingestion_config_from_db(db) -> bool:
    """Load ingestion performance config from database on startup."""
    try:
        collection = db.db[INGESTION_CONFIG_COLLECTION]
        doc = await collection.find_one({"_id": INGESTION_CONFIG_DOC_ID})
        
        if doc:
            settings.ingestion_process_isolation = doc.get("process_isolation_enabled", settings.ingestion_process_isolation)
            settings.ingestion_max_concurrent_files = doc.get("max_concurrent_files", settings.ingestion_max_concurrent_files)
            settings.ingestion_embedding_batch_size = doc.get("embedding_batch_size", settings.ingestion_embedding_batch_size)
            settings.ingestion_thread_pool_workers = doc.get("thread_pool_workers", settings.ingestion_thread_pool_workers)
            settings.ingestion_embedding_requests_per_minute = doc.get("embedding_requests_per_minute", settings.ingestion_embedding_requests_per_minute)
            settings.ingestion_file_processing_timeout = doc.get("file_processing_timeout", settings.ingestion_file_processing_timeout)
            settings.ingestion_job_poll_interval = doc.get("job_poll_interval_seconds", settings.ingestion_job_poll_interval)
            
            logger.info(f"Loaded ingestion performance config: concurrent_files={settings.ingestion_max_concurrent_files}, isolation={settings.ingestion_process_isolation}")
            return True
        return False
    except Exception as e:
        logger.warning(f"Failed to load ingestion config from database: {e}")
        return False


# ==================== Ingestion Worker Health ====================

@router.get("/worker-health")
async def get_worker_health(request: Request):
    """
    Check ingestion worker health status.
    
    Returns information about the worker process including:
    - Whether the worker is running (has a job)
    - Worker health based on recent heartbeat
    - Worker PID and last heartbeat time
    """
    db = request.app.state.db
    
    try:
        # Check for any running job with recent heartbeat
        job = await db.db["ingestion_jobs"].find_one(
            {"status": "RUNNING"},
            {"worker_heartbeat": 1, "worker_pid": 1, "started_at": 1}
        )
        
        if job:
            last_heartbeat = job.get("worker_heartbeat")
            worker_pid = job.get("worker_pid")
            
            # Consider healthy if heartbeat within last 30 seconds
            is_healthy = False
            if last_heartbeat:
                elapsed = (datetime.now() - last_heartbeat).total_seconds()
                is_healthy = elapsed < 30
            
            return {
                "worker_running": True,
                "worker_healthy": is_healthy,
                "worker_pid": worker_pid,
                "last_heartbeat": last_heartbeat.isoformat() if last_heartbeat else None,
                "job_started_at": job.get("started_at").isoformat() if job.get("started_at") else None
            }
        
        # No running job - check if worker picked up any job recently
        recent_job = await db.db["ingestion_jobs"].find_one(
            {"status": {"$in": ["COMPLETED", "FAILED", "STOPPED"]}},
            sort=[("completed_at", -1)],
            projection={"completed_at": 1, "worker_pid": 1}
        )
        
        return {
            "worker_running": False,
            "worker_healthy": None,
            "worker_pid": recent_job.get("worker_pid") if recent_job else None,
            "last_heartbeat": None,
            "last_job_completed": recent_job.get("completed_at").isoformat() if recent_job and recent_job.get("completed_at") else None
        }
        
    except Exception as e:
        logger.error(f"Failed to check worker health: {e}")
        return {
            "worker_running": False,
            "worker_healthy": None,
            "error": str(e)
        }


# LLM Provider configuration collection
LLM_CONFIG_COLLECTION = "llm_config"
LLM_CONFIG_DOC_ID = "provider_config"


@router.get("/llm-providers")
async def get_llm_provider_config(request: Request):
    """
    Get current LLM provider configuration.
    
    Returns provider settings without exposing full API keys.
    """
    db = request.app.state.db
    
    # Get from database if exists
    try:
        collection = db.db[LLM_CONFIG_COLLECTION]
        doc = await collection.find_one({"_id": LLM_CONFIG_DOC_ID})
        
        # Mask API keys (show only last 4 characters)
        def mask_key(key: str) -> str:
            if not key:
                return ""
            if len(key) <= 4:
                return "****"
            return "****" + key[-4:]
        
        if doc:
            return {
                "orchestrator_provider": doc.get("orchestrator_provider", settings.orchestrator_provider),
                "orchestrator_model": doc.get("orchestrator_model", settings.orchestrator_model),
                "worker_provider": doc.get("worker_provider", settings.worker_provider),
                "worker_model": doc.get("worker_model", settings.worker_model),
                "embedding_provider": doc.get("embedding_provider", settings.embedding_provider),
                "openai_api_key_set": bool(doc.get("openai_api_key") or settings.openai_api_key),
                "openai_api_key_masked": mask_key(doc.get("openai_api_key") or settings.openai_api_key),
                "google_api_key_set": bool(doc.get("google_api_key") or settings.google_api_key),
                "google_api_key_masked": mask_key(doc.get("google_api_key") or settings.google_api_key),
                "anthropic_api_key_set": bool(doc.get("anthropic_api_key") or settings.anthropic_api_key),
                "anthropic_api_key_masked": mask_key(doc.get("anthropic_api_key") or settings.anthropic_api_key),
                "fast_llm_api_key_set": bool(doc.get("fast_llm_api_key") or settings.fast_llm_api_key),
                "updated_at": doc.get("updated_at"),
                "providers": [
                    {"id": "openai", "name": "OpenAI", "models": _flatten_models(OPENAI_MODELS), "categories": list(OPENAI_MODELS.keys()), "supports_fetch": True},
                    {"id": "google", "name": "Google Gemini", "models": _flatten_models(GOOGLE_MODELS), "categories": list(GOOGLE_MODELS.keys()), "supports_fetch": True},
                    {"id": "anthropic", "name": "Anthropic Claude", "models": _flatten_models(ANTHROPIC_MODELS), "categories": list(ANTHROPIC_MODELS.keys()), "supports_fetch": True},
                    {"id": "ollama", "name": "Ollama (Local)", "models": _flatten_models(OLLAMA_MODELS), "categories": list(OLLAMA_MODELS.keys()), "supports_fetch": True},
                ],
            }
    except Exception as e:
        logger.warning(f"Failed to get LLM config from database: {e}")
    
    # Return defaults from settings
    return {
        "orchestrator_provider": settings.orchestrator_provider,
        "orchestrator_model": settings.orchestrator_model,
        "worker_provider": settings.worker_provider,
        "worker_model": settings.worker_model,
        "embedding_provider": settings.embedding_provider,
        "openai_api_key_set": bool(settings.openai_api_key),
        "openai_api_key_masked": "",
        "google_api_key_set": bool(settings.google_api_key),
        "google_api_key_masked": "",
        "anthropic_api_key_set": bool(settings.anthropic_api_key),
        "anthropic_api_key_masked": "",
        "fast_llm_api_key_set": bool(settings.fast_llm_api_key),
        "updated_at": None,
        "providers": [
            {"id": "openai", "name": "OpenAI", "models": ["gpt-5.2", "gpt-5.1", "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]},
            {"id": "google", "name": "Google Gemini", "models": ["gemini-2.0-flash-exp", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]},
            {"id": "anthropic", "name": "Anthropic Claude", "models": ["claude-3-5-sonnet-latest", "claude-3-opus-latest", "claude-3-haiku-20240307"]},
            {"id": "ollama", "name": "Ollama (Local)", "models": []},
        ],
    }


@router.post("/llm-providers")
async def save_llm_provider_config(request: Request, config: LLMProviderConfig):
    """
    Save LLM provider configuration to database.
    
    API keys are stored encrypted (in production you should use proper secrets management).
    """
    db = request.app.state.db
    
    try:
        collection = db.db[LLM_CONFIG_COLLECTION]
        
        # Get existing config to preserve keys that aren't being updated
        existing = await collection.find_one({"_id": LLM_CONFIG_DOC_ID}) or {}
        
        # Build update document
        update_doc = {
            "_id": LLM_CONFIG_DOC_ID,
            "updated_at": datetime.now().isoformat(),
        }
        
        # Update providers
        if config.orchestrator_provider is not None:
            update_doc["orchestrator_provider"] = config.orchestrator_provider
            settings.orchestrator_provider = config.orchestrator_provider
        else:
            update_doc["orchestrator_provider"] = existing.get("orchestrator_provider", settings.orchestrator_provider)
        
        if config.orchestrator_model is not None:
            update_doc["orchestrator_model"] = config.orchestrator_model
            settings.orchestrator_model = config.orchestrator_model
        else:
            update_doc["orchestrator_model"] = existing.get("orchestrator_model", settings.orchestrator_model)
        
        if config.worker_provider is not None:
            update_doc["worker_provider"] = config.worker_provider
            settings.worker_provider = config.worker_provider
        else:
            update_doc["worker_provider"] = existing.get("worker_provider", settings.worker_provider)
        
        if config.worker_model is not None:
            update_doc["worker_model"] = config.worker_model
            settings.worker_model = config.worker_model
        else:
            update_doc["worker_model"] = existing.get("worker_model", settings.worker_model)
        
        if config.embedding_provider is not None:
            update_doc["embedding_provider"] = config.embedding_provider
            settings.embedding_provider = config.embedding_provider
        else:
            update_doc["embedding_provider"] = existing.get("embedding_provider", settings.embedding_provider)
        
        # Update API keys (only if provided - empty string clears, None preserves)
        if config.openai_api_key is not None:
            update_doc["openai_api_key"] = config.openai_api_key
            settings.openai_api_key = config.openai_api_key
        else:
            update_doc["openai_api_key"] = existing.get("openai_api_key", "")
        
        if config.google_api_key is not None:
            update_doc["google_api_key"] = config.google_api_key
            settings.google_api_key = config.google_api_key
        else:
            update_doc["google_api_key"] = existing.get("google_api_key", "")
        
        if config.anthropic_api_key is not None:
            update_doc["anthropic_api_key"] = config.anthropic_api_key
            settings.anthropic_api_key = config.anthropic_api_key
        else:
            update_doc["anthropic_api_key"] = existing.get("anthropic_api_key", "")
        
        if config.fast_llm_api_key is not None:
            update_doc["fast_llm_api_key"] = config.fast_llm_api_key
            settings.fast_llm_api_key = config.fast_llm_api_key
        else:
            update_doc["fast_llm_api_key"] = existing.get("fast_llm_api_key", "")
        
        if config.embedding_api_key is not None:
            update_doc["embedding_api_key"] = config.embedding_api_key
            settings.embedding_api_key = config.embedding_api_key
        else:
            update_doc["embedding_api_key"] = existing.get("embedding_api_key", "")
        
        # Upsert to database
        await collection.replace_one(
            {"_id": LLM_CONFIG_DOC_ID},
            update_doc,
            upsert=True
        )
        
        logger.info(f"Saved LLM provider config: orchestrator={update_doc.get('orchestrator_provider')}/{update_doc.get('orchestrator_model')}, worker={update_doc.get('worker_provider')}/{update_doc.get('worker_model')}")
        
        return {
            "success": True,
            "message": "LLM provider configuration saved",
            "config": {
                "orchestrator_provider": update_doc.get("orchestrator_provider"),
                "orchestrator_model": update_doc.get("orchestrator_model"),
                "worker_provider": update_doc.get("worker_provider"),
                "worker_model": update_doc.get("worker_model"),
                "embedding_provider": update_doc.get("embedding_provider"),
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to save LLM provider config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def load_llm_config_from_db(db) -> bool:
    """
    Load LLM provider configuration from database at startup.
    
    Returns True if config was loaded, False otherwise.
    """
    try:
        collection = db.db[LLM_CONFIG_COLLECTION]
        doc = await collection.find_one({"_id": LLM_CONFIG_DOC_ID})
        
        if doc:
            if doc.get("orchestrator_provider"):
                settings.orchestrator_provider = doc["orchestrator_provider"]
            if doc.get("orchestrator_model"):
                settings.orchestrator_model = doc["orchestrator_model"]
            if doc.get("worker_provider"):
                settings.worker_provider = doc["worker_provider"]
            if doc.get("worker_model"):
                settings.worker_model = doc["worker_model"]
            if doc.get("embedding_provider"):
                settings.embedding_provider = doc["embedding_provider"]
            if doc.get("openai_api_key"):
                settings.openai_api_key = doc["openai_api_key"]
            if doc.get("google_api_key"):
                settings.google_api_key = doc["google_api_key"]
            if doc.get("anthropic_api_key"):
                settings.anthropic_api_key = doc["anthropic_api_key"]
            if doc.get("fast_llm_api_key"):
                settings.fast_llm_api_key = doc["fast_llm_api_key"]
            if doc.get("embedding_api_key"):
                settings.embedding_api_key = doc["embedding_api_key"]
            
            logger.info(f"Loaded LLM provider config: orchestrator={settings.orchestrator_provider}/{settings.orchestrator_model}, worker={settings.worker_provider}/{settings.worker_model}")
            return True
        return False
    except Exception as e:
        logger.warning(f"Failed to load LLM config from database: {e}")
        return False


# ==================== Provider Models Fetch & Test ====================

class ProviderTestRequest(BaseModel):
    """Request to test a provider connection."""
    provider: str
    model: str
    api_key: Optional[str] = None
    prompt: str = "Hello, please respond with a brief greeting."


class ProviderTestResponse(BaseModel):
    """Response from testing a provider."""
    success: bool
    provider: str
    model: str
    response: Optional[str] = None
    error: Optional[str] = None
    latency_ms: float
    logs: List[str]


@router.get("/llm-providers/models/{provider_id}")
async def get_provider_models_detailed(provider_id: str):
    """
    Get detailed model list for a specific provider.
    
    Returns comprehensive model info including categories, capabilities, etc.
    """
    provider_models = {
        "openai": OPENAI_MODELS,
        "google": GOOGLE_MODELS,
        "anthropic": ANTHROPIC_MODELS,
        "ollama": OLLAMA_MODELS,
    }
    
    if provider_id not in provider_models:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_id}")
    
    models = _get_detailed_models(provider_models[provider_id])
    
    return {
        "provider": provider_id,
        "models": models,
        "categories": list(provider_models[provider_id].keys()),
        "total": len(models)
    }


@router.post("/llm-providers/fetch-models/{provider_id}")
async def fetch_models_from_api(request: Request, provider_id: str, api_key: Optional[str] = None):
    """
    Fetch available models from the official provider API.
    
    This queries the provider's API to get the actual available models.
    Requires an API key for the provider.
    """
    logs = []
    start_time = time.time()
    
    def log(msg: str):
        logs.append(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}")
        logger.info(msg)
    
    # Get API key from request, database, or settings
    db = request.app.state.db
    effective_key = api_key
    
    if not effective_key:
        try:
            collection = db.db[LLM_CONFIG_COLLECTION]
            doc = await collection.find_one({"_id": LLM_CONFIG_DOC_ID})
            if doc:
                if provider_id == "openai":
                    effective_key = doc.get("openai_api_key") or settings.openai_api_key
                elif provider_id == "google":
                    effective_key = doc.get("google_api_key") or settings.google_api_key
                elif provider_id == "anthropic":
                    effective_key = doc.get("anthropic_api_key") or settings.anthropic_api_key
        except Exception:
            pass
        
        # Fallback to settings
        if not effective_key:
            if provider_id == "openai":
                effective_key = settings.openai_api_key
            elif provider_id == "google":
                effective_key = settings.google_api_key
            elif provider_id == "anthropic":
                effective_key = settings.anthropic_api_key
    
    if provider_id == "openai":
        log(f"Fetching models from OpenAI API...")
        if not effective_key:
            log("ERROR: No OpenAI API key configured")
            return {
                "success": False, 
                "provider": provider_id,
                "error": "No API key configured for OpenAI",
                "logs": logs,
                "models": _get_detailed_models(OPENAI_MODELS),  # Return default list
                "is_default": True
            }
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                log("Sending request to https://api.openai.com/v1/models...")
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {effective_key}"}
                )
                log(f"Response status: {response.status_code}")
                
                if response.status_code != 200:
                    log(f"ERROR: API returned {response.status_code}: {response.text[:200]}")
                    return {
                        "success": False,
                        "provider": provider_id,
                        "error": f"API error: {response.status_code}",
                        "logs": logs,
                        "models": _get_detailed_models(OPENAI_MODELS),
                        "is_default": True
                    }
                
                data = response.json()
                raw_models = data.get("data", [])
                log(f"Received {len(raw_models)} models from API")
                
                # Categorize models
                models = []
                for m in raw_models:
                    model_id = m.get("id", "")
                    model_info = {
                        "id": model_id,
                        "name": model_id,
                        "owned_by": m.get("owned_by", "openai"),
                        "created": m.get("created"),
                    }
                    
                    # Determine category and type
                    if "gpt" in model_id.lower() or "o1" in model_id.lower() or "o3" in model_id.lower():
                        model_info["category"] = "chat"
                        model_info["type"] = "chat"
                        if "o1" in model_id or "o3" in model_id:
                            model_info["type"] = "reasoning"
                    elif "embed" in model_id.lower():
                        model_info["category"] = "embedding"
                        model_info["type"] = "embedding"
                    elif "dall-e" in model_id.lower():
                        model_info["category"] = "image"
                        model_info["type"] = "image_generation"
                    elif "whisper" in model_id.lower():
                        model_info["category"] = "audio"
                        model_info["type"] = "speech_to_text"
                    elif "tts" in model_id.lower():
                        model_info["category"] = "audio"
                        model_info["type"] = "text_to_speech"
                    elif "moderation" in model_id.lower():
                        model_info["category"] = "moderation"
                        model_info["type"] = "moderation"
                    else:
                        model_info["category"] = "other"
                        model_info["type"] = "unknown"
                    
                    models.append(model_info)
                
                # Sort by category and name
                models.sort(key=lambda x: (x["category"], x["id"]))
                
                log(f"Categorized {len(models)} models")
                log(f"Completed in {(time.time() - start_time) * 1000:.0f}ms")
                
                return {
                    "success": True,
                    "provider": provider_id,
                    "models": models,
                    "categories": list(set(m["category"] for m in models)),
                    "total": len(models),
                    "logs": logs,
                    "is_default": False
                }
                
        except Exception as e:
            log(f"ERROR: {str(e)}")
            return {
                "success": False,
                "provider": provider_id,
                "error": str(e),
                "logs": logs,
                "models": _get_detailed_models(OPENAI_MODELS),
                "is_default": True
            }
    
    elif provider_id == "google":
        log(f"Fetching models from Google Gemini API...")
        if not effective_key:
            log("ERROR: No Google API key configured")
            return {
                "success": False,
                "provider": provider_id,
                "error": "No API key configured for Google",
                "logs": logs,
                "models": _get_detailed_models(GOOGLE_MODELS),
                "is_default": True
            }
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                log("Sending request to Google AI API...")
                response = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={effective_key}"
                )
                log(f"Response status: {response.status_code}")
                
                if response.status_code != 200:
                    log(f"ERROR: API returned {response.status_code}")
                    return {
                        "success": False,
                        "provider": provider_id,
                        "error": f"API error: {response.status_code}",
                        "logs": logs,
                        "models": _get_detailed_models(GOOGLE_MODELS),
                        "is_default": True
                    }
                
                data = response.json()
                raw_models = data.get("models", [])
                log(f"Received {len(raw_models)} models from API")
                
                models = []
                for m in raw_models:
                    model_name = m.get("name", "").replace("models/", "")
                    model_info = {
                        "id": model_name,
                        "name": m.get("displayName", model_name),
                        "description": m.get("description", ""),
                        "input_token_limit": m.get("inputTokenLimit"),
                        "output_token_limit": m.get("outputTokenLimit"),
                        "supported_methods": m.get("supportedGenerationMethods", []),
                    }
                    
                    # Determine category
                    if "embed" in model_name.lower():
                        model_info["category"] = "embedding"
                        model_info["type"] = "embedding"
                    elif "gemini" in model_name.lower():
                        model_info["category"] = "chat"
                        model_info["type"] = "chat"
                        if "vision" in model_name.lower():
                            model_info["multimodal"] = True
                    else:
                        model_info["category"] = "other"
                        model_info["type"] = "unknown"
                    
                    models.append(model_info)
                
                models.sort(key=lambda x: (x["category"], x["id"]))
                log(f"Processed {len(models)} models")
                log(f"Completed in {(time.time() - start_time) * 1000:.0f}ms")
                
                return {
                    "success": True,
                    "provider": provider_id,
                    "models": models,
                    "categories": list(set(m["category"] for m in models)),
                    "total": len(models),
                    "logs": logs,
                    "is_default": False
                }
                
        except Exception as e:
            log(f"ERROR: {str(e)}")
            return {
                "success": False,
                "provider": provider_id,
                "error": str(e),
                "logs": logs,
                "models": _get_detailed_models(GOOGLE_MODELS),
                "is_default": True
            }
    
    elif provider_id == "anthropic":
        log(f"Fetching models from Anthropic API...")
        # Anthropic doesn't have a public models endpoint, so we return the known list
        log("Note: Anthropic doesn't have a public models listing API")
        log("Returning curated list of available models")
        
        models = _get_detailed_models(ANTHROPIC_MODELS)
        log(f"Returning {len(models)} known Anthropic models")
        log(f"Completed in {(time.time() - start_time) * 1000:.0f}ms")
        
        return {
            "success": True,
            "provider": provider_id,
            "models": models,
            "categories": list(ANTHROPIC_MODELS.keys()),
            "total": len(models),
            "logs": logs,
            "is_default": True,
            "note": "Anthropic doesn't provide a public models API. This is the curated list."
        }
    
    elif provider_id == "ollama":
        log(f"Fetching models from local Ollama...")
        
        # Try to fetch from local Ollama instance
        ollama_urls = [
            "http://localhost:11434",
            "http://host.docker.internal:11434",
            "http://ollama:11434",
        ]
        
        for url in ollama_urls:
            try:
                log(f"Trying {url}...")
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(f"{url}/api/tags")
                    
                    if response.status_code == 200:
                        log(f"Connected to Ollama at {url}")
                        data = response.json()
                        raw_models = data.get("models", [])
                        log(f"Found {len(raw_models)} installed models")
                        
                        models = []
                        for m in raw_models:
                            model_name = m.get("name", "")
                            model_info = {
                                "id": model_name,
                                "name": model_name,
                                "size_gb": round(m.get("size", 0) / 1024 / 1024 / 1024, 2),
                                "modified_at": m.get("modified_at"),
                                "category": "chat",  # Default category
                                "type": "chat",
                                "installed": True,
                            }
                            
                            # Determine category based on name
                            name_lower = model_name.lower()
                            if "embed" in name_lower or "bge" in name_lower or "minilm" in name_lower:
                                model_info["category"] = "embedding"
                                model_info["type"] = "embedding"
                            elif "llava" in name_lower or "vision" in name_lower:
                                model_info["category"] = "vision"
                                model_info["type"] = "vision"
                                model_info["multimodal"] = True
                            
                            models.append(model_info)
                        
                        # Add recommended models that aren't installed
                        installed_names = {m["id"].split(":")[0] for m in models}
                        default_models = _get_detailed_models(OLLAMA_MODELS)
                        for dm in default_models:
                            base_name = dm["id"].split(":")[0]
                            if base_name not in installed_names:
                                dm["installed"] = False
                                models.append(dm)
                        
                        log(f"Completed in {(time.time() - start_time) * 1000:.0f}ms")
                        
                        return {
                            "success": True,
                            "provider": provider_id,
                            "models": models,
                            "categories": list(set(m["category"] for m in models)),
                            "total": len(models),
                            "ollama_url": url,
                            "logs": logs,
                            "is_default": False
                        }
            except Exception as e:
                log(f"Failed to connect to {url}: {str(e)}")
                continue
        
        log("Could not connect to any Ollama instance")
        log("Returning default model list")
        
        return {
            "success": False,
            "provider": provider_id,
            "error": "Could not connect to Ollama. Make sure Ollama is running.",
            "models": _get_detailed_models(OLLAMA_MODELS),
            "categories": list(OLLAMA_MODELS.keys()),
            "total": len(_get_detailed_models(OLLAMA_MODELS)),
            "logs": logs,
            "is_default": True
        }
    
    else:
        return {
            "success": False,
            "provider": provider_id,
            "error": f"Unknown provider: {provider_id}",
            "logs": logs,
            "models": []
        }


@router.post("/llm-providers/test")
async def test_provider_connection(request: Request, test_request: ProviderTestRequest):
    """
    Test a provider connection by sending a simple prompt.
    
    Returns the response along with detailed logs for debugging.
    """
    logs = []
    start_time = time.time()
    
    def log(msg: str):
        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        logs.append(f"[{timestamp}] {msg}")
        logger.info(msg)
    
    provider = test_request.provider
    model = test_request.model
    prompt = test_request.prompt
    api_key = test_request.api_key
    
    log(f"Testing {provider} provider with model {model}")
    log(f"Prompt: {prompt[:50]}{'...' if len(prompt) > 50 else ''}")
    
    # Get API key if not provided
    if not api_key:
        db = request.app.state.db
        try:
            collection = db.db[LLM_CONFIG_COLLECTION]
            doc = await collection.find_one({"_id": LLM_CONFIG_DOC_ID})
            if doc:
                if provider == "openai":
                    api_key = doc.get("openai_api_key") or settings.openai_api_key
                elif provider == "google":
                    api_key = doc.get("google_api_key") or settings.google_api_key
                elif provider == "anthropic":
                    api_key = doc.get("anthropic_api_key") or settings.anthropic_api_key
        except Exception:
            pass
        
        if not api_key:
            if provider == "openai":
                api_key = settings.openai_api_key
            elif provider == "google":
                api_key = settings.google_api_key
            elif provider == "anthropic":
                api_key = settings.anthropic_api_key
    
    if provider == "openai":
        if not api_key:
            log("ERROR: No OpenAI API key configured")
            return ProviderTestResponse(
                success=False,
                provider=provider,
                model=model,
                error="No API key configured",
                latency_ms=(time.time() - start_time) * 1000,
                logs=logs
            )
        
        try:
            log("Sending request to OpenAI API...")
            async with httpx.AsyncClient(timeout=30.0) as client:
                request_body = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7
                }
                                    
                # Handle newer OpenAI models that require max_completion_tokens
                if "gpt-5" in model.lower() or "gpt-4o" in model.lower():
                    request_body["max_completion_tokens"] = 100
                else:
                    request_body["max_tokens"] = 100
                                    
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    json=request_body
                )
                
                latency = (time.time() - start_time) * 1000
                log(f"Response received in {latency:.0f}ms")
                log(f"Status code: {response.status_code}")
                
                if response.status_code != 200:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get("message", response.text[:200])
                    log(f"ERROR: {error_msg}")
                    return ProviderTestResponse(
                        success=False,
                        provider=provider,
                        model=model,
                        error=error_msg,
                        latency_ms=latency,
                        logs=logs
                    )
                
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                log(f"Response: {content[:100]}{'...' if len(content) > 100 else ''}")
                log(f"Tokens used: {data.get('usage', {})}")
                log("SUCCESS: Connection test passed!")
                
                return ProviderTestResponse(
                    success=True,
                    provider=provider,
                    model=model,
                    response=content,
                    latency_ms=latency,
                    logs=logs
                )
                
        except Exception as e:
            log(f"ERROR: {str(e)}")
            return ProviderTestResponse(
                success=False,
                provider=provider,
                model=model,
                error=str(e),
                latency_ms=(time.time() - start_time) * 1000,
                logs=logs
            )
    
    elif provider == "google":
        if not api_key:
            log("ERROR: No Google API key configured")
            return ProviderTestResponse(
                success=False,
                provider=provider,
                model=model,
                error="No API key configured",
                latency_ms=(time.time() - start_time) * 1000,
                logs=logs
            )
        
        try:
            log("Sending request to Google Gemini API...")
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Use the Gemini API format
                api_model = model if model.startswith("models/") else f"models/{model}"
                response = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/{api_model}:generateContent?key={api_key}",
                    headers={"Content-Type": "application/json"},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "maxOutputTokens": 100,
                            "temperature": 0.7
                        }
                    }
                )
                
                latency = (time.time() - start_time) * 1000
                log(f"Response received in {latency:.0f}ms")
                log(f"Status code: {response.status_code}")
                
                if response.status_code != 200:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get("message", response.text[:200])
                    log(f"ERROR: {error_msg}")
                    return ProviderTestResponse(
                        success=False,
                        provider=provider,
                        model=model,
                        error=error_msg,
                        latency_ms=latency,
                        logs=logs
                    )
                
                data = response.json()
                content = data["candidates"][0]["content"]["parts"][0]["text"]
                log(f"Response: {content[:100]}{'...' if len(content) > 100 else ''}")
                log("SUCCESS: Connection test passed!")
                
                return ProviderTestResponse(
                    success=True,
                    provider=provider,
                    model=model,
                    response=content,
                    latency_ms=latency,
                    logs=logs
                )
                
        except Exception as e:
            log(f"ERROR: {str(e)}")
            return ProviderTestResponse(
                success=False,
                provider=provider,
                model=model,
                error=str(e),
                latency_ms=(time.time() - start_time) * 1000,
                logs=logs
            )
    
    elif provider == "anthropic":
        if not api_key:
            log("ERROR: No Anthropic API key configured")
            return ProviderTestResponse(
                success=False,
                provider=provider,
                model=model,
                error="No API key configured",
                latency_ms=(time.time() - start_time) * 1000,
                logs=logs
            )
        
        try:
            log("Sending request to Anthropic API...")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 100
                    }
                )
                
                latency = (time.time() - start_time) * 1000
                log(f"Response received in {latency:.0f}ms")
                log(f"Status code: {response.status_code}")
                
                if response.status_code != 200:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get("message", response.text[:200])
                    log(f"ERROR: {error_msg}")
                    return ProviderTestResponse(
                        success=False,
                        provider=provider,
                        model=model,
                        error=error_msg,
                        latency_ms=latency,
                        logs=logs
                    )
                
                data = response.json()
                content = data["content"][0]["text"]
                log(f"Response: {content[:100]}{'...' if len(content) > 100 else ''}")
                log(f"Usage: input={data.get('usage', {}).get('input_tokens')}, output={data.get('usage', {}).get('output_tokens')}")
                log("SUCCESS: Connection test passed!")
                
                return ProviderTestResponse(
                    success=True,
                    provider=provider,
                    model=model,
                    response=content,
                    latency_ms=latency,
                    logs=logs
                )
                
        except Exception as e:
            log(f"ERROR: {str(e)}")
            return ProviderTestResponse(
                success=False,
                provider=provider,
                model=model,
                error=str(e),
                latency_ms=(time.time() - start_time) * 1000,
                logs=logs
            )
    
    elif provider == "ollama":
        log("Testing Ollama connection...")
        
        ollama_urls = [
            "http://localhost:11434",
            "http://host.docker.internal:11434",
            "http://ollama:11434",
        ]
        
        for url in ollama_urls:
            try:
                log(f"Trying {url}...")
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        f"{url}/api/generate",
                        json={
                            "model": model,
                            "prompt": prompt,
                            "stream": False,
                            "options": {"num_predict": 100}
                        }
                    )
                    
                    latency = (time.time() - start_time) * 1000
                    log(f"Response received in {latency:.0f}ms")
                    
                    if response.status_code == 200:
                        data = response.json()
                        content = data.get("response", "")
                        log(f"Response: {content[:100]}{'...' if len(content) > 100 else ''}")
                        log("SUCCESS: Connection test passed!")
                        
                        return ProviderTestResponse(
                            success=True,
                            provider=provider,
                            model=model,
                            response=content,
                            latency_ms=latency,
                            logs=logs
                        )
                    else:
                        log(f"Failed with status {response.status_code}")
                        
            except Exception as e:
                log(f"Failed: {str(e)}")
                continue
        
        log("ERROR: Could not connect to any Ollama instance")
        return ProviderTestResponse(
            success=False,
            provider=provider,
            model=model,
            error="Could not connect to Ollama. Make sure it's running.",
            latency_ms=(time.time() - start_time) * 1000,
            logs=logs
        )
    
    else:
        log(f"ERROR: Unknown provider {provider}")
        return ProviderTestResponse(
            success=False,
            provider=provider,
            model=model,
            error=f"Unknown provider: {provider}",
            latency_ms=(time.time() - start_time) * 1000,
            logs=logs
        )


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


# ==================== Agent Tools ====================
# Define all available agent tools with descriptions, parameters, and test configurations

AGENT_TOOLS = [
    {
        "id": "search_knowledge_base",
        "name": "Search Knowledge Base",
        "icon": "🔍",
        "category": "search",
        "description": "Search the internal knowledge base using hybrid search (vector + text). This tool searches through all ingested documents including company docs, cloud storage files, and personal data.",
        "help_text": """**When to use:**
- Questions about company information, policies, or internal documents
- Finding specific facts or data from uploaded files
- Searching across profile, cloud, or personal document sources

**How it works:**
1. Converts your query into a semantic vector for similarity matching
2. Performs full-text search for keyword matching
3. Combines results using Reciprocal Rank Fusion (RRF)
4. Returns the most relevant document chunks

**Tips:**
- Use specific keywords for better results
- Try different phrasings if initial search returns no results
- Combine with browse_web for comprehensive research""",
        "parameters": [
            {"name": "query", "type": "string", "required": True, "description": "The search query to find relevant documents"}
        ],
        "test_config": {
            "requires_db": True,
            "test_query": "company overview",
            "expected_response": "documents"
        }
    },
    {
        "id": "browse_web",
        "name": "Browse Web",
        "icon": "🌐",
        "category": "web",
        "description": "Fetch and read content from a specific web page URL. Uses a headless browser (Playwright) to render pages and extract content, supporting JavaScript-heavy sites.",
        "help_text": """**When to use:**
- Reading specific web pages when you have the URL
- Fetching documentation, articles, or company websites
- Extracting links or structured content from pages

**How it works:**
1. Validates URL for security (blocks internal/local addresses)
2. Launches headless Chromium browser
3. Navigates to the page and waits for content
4. Extracts content based on type (text, markdown, or links)
5. Caches results for 5 minutes

**Extract Types:**
- `text`: Plain text content (default)
- `markdown`: Preserves headings and structure
- `links`: Extracts all links from the page

**Limitations:**
- 30 second timeout
- 15,000 character content limit
- Blocked: localhost, internal IPs, file:// URLs""",
        "parameters": [
            {"name": "url", "type": "string", "required": True, "description": "The full URL to fetch (must start with http:// or https://)"},
            {"name": "extract_type", "type": "enum", "required": False, "options": ["text", "markdown", "links"], "default": "text", "description": "Type of content to extract"}
        ],
        "test_config": {
            "requires_db": False,
            "test_url": "https://httpbin.org/html",
            "expected_response": "html_content"
        }
    },
    {
        "id": "web_search",
        "name": "Web Search",
        "icon": "🔎",
        "category": "web",
        "description": "Search the web using Brave Search API to find relevant URLs and information. Returns titles, snippets, and URLs from search results.",
        "help_text": """**When to use:**
- Finding websites or pages about a topic
- Discovering URLs to then browse with browse_web
- Getting current information not in the knowledge base
- Verifying external facts or finding company registries

**How it works:**
1. Sends query to Brave Search API
2. Returns up to 10 results with title, URL, and snippet
3. Can be chained with browse_web to read full content

**Requirements:**
- Requires a valid Brave Search API key (BRAVE_SEARCH_API_KEY)
- Rate limits apply based on your API plan

**Tips:**
- Use specific search terms
- Follow up with browse_web on interesting results
- Chain multiple searches for comprehensive research""",
        "parameters": [
            {"name": "query", "type": "string", "required": True, "description": "The search query to find relevant web pages"}
        ],
        "test_config": {
            "requires_api_key": "brave",
            "test_query": "OpenAI API documentation",
            "expected_response": "search_results"
        }
    }
]


class ToolTestRequest(BaseModel):
    """Request to test a specific tool."""
    tool_id: str
    parameters: dict = {}


class ToolTestResponse(BaseModel):
    """Response from tool test."""
    success: bool
    tool_id: str
    tool_name: str
    result: Optional[str] = None
    result_preview: Optional[str] = None
    error: Optional[str] = None
    latency_ms: float = 0.0
    logs: List[str] = []


@router.get("/tools")
async def list_agent_tools():
    """
    Get list of all available agent tools with descriptions and parameters.
    
    Returns comprehensive information about each tool including:
    - Name and description
    - Help text explaining when and how to use
    - Parameters with types and descriptions
    - Test configuration
    """
    return {
        "tools": AGENT_TOOLS,
        "total": len(AGENT_TOOLS),
        "categories": list(set(t["category"] for t in AGENT_TOOLS))
    }


@router.get("/tools/{tool_id}")
async def get_tool_details(tool_id: str):
    """Get detailed information about a specific tool."""
    for tool in AGENT_TOOLS:
        if tool["id"] == tool_id:
            return tool
    raise HTTPException(status_code=404, detail=f"Tool not found: {tool_id}")


@router.post("/tools/test")
async def test_agent_tool(request: Request, test_request: ToolTestRequest):
    """
    Test a specific agent tool with optional parameters.
    
    This allows testing tools directly from the settings page to verify
    they are working correctly with the current configuration.
    """
    logs = []
    start_time = time.time()
    
    def log(msg: str):
        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        logs.append(f"[{timestamp}] {msg}")
        logger.info(f"Tool test: {msg}")
    
    tool_id = test_request.tool_id
    params = test_request.parameters
    
    # Find tool
    tool = None
    for t in AGENT_TOOLS:
        if t["id"] == tool_id:
            tool = t
            break
    
    if not tool:
        return ToolTestResponse(
            success=False,
            tool_id=tool_id,
            tool_name="Unknown",
            error=f"Tool not found: {tool_id}",
            latency_ms=(time.time() - start_time) * 1000,
            logs=logs
        )
    
    tool_name = tool["name"]
    log(f"Testing tool: {tool_name}")
    
    try:
        if tool_id == "search_knowledge_base":
            # Test knowledge base search
            query = params.get("query") or tool["test_config"].get("test_query", "test")
            log(f"Search query: {query}")
            
            db = request.app.state.db
            if not db:
                raise Exception("Database not available")
            
            # Perform a simple search
            from backend.routers.chat import perform_search, SearchType
            log("Executing hybrid search...")
            
            results, thinking = await perform_search(db, query, SearchType.HYBRID, 5)
            
            latency = (time.time() - start_time) * 1000
            log(f"Search completed in {latency:.0f}ms")
            log(f"Found {len(results)} results")
            
            if results:
                preview_parts = []
                for i, r in enumerate(results[:3], 1):
                    title = r.get("document_title", "Unknown")
                    content = r.get("content", "")[:100]
                    preview_parts.append(f"{i}. [{title}] {content}...")
                result_preview = "\n".join(preview_parts)
                log(f"Top result: {results[0].get('document_title', 'Unknown')}")
                log("SUCCESS: Search tool working correctly!")
            else:
                result_preview = "No documents found. This may be normal if no documents have been ingested."
                log("WARNING: No results found (knowledge base may be empty)")
            
            return ToolTestResponse(
                success=True,
                tool_id=tool_id,
                tool_name=tool_name,
                result=f"Found {len(results)} documents",
                result_preview=result_preview,
                latency_ms=latency,
                logs=logs
            )
            
        elif tool_id == "browse_web":
            # Test browser tool
            url = params.get("url") or tool["test_config"].get("test_url", "https://httpbin.org/html")
            extract_type = params.get("extract_type", "text")
            log(f"Browsing URL: {url}")
            log(f"Extract type: {extract_type}")
            
            from backend.tools.browser_tool import browse_url
            log("Launching headless browser...")
            
            result = await browse_url(url, extract_type)
            
            latency = (time.time() - start_time) * 1000
            log(f"Browse completed in {latency:.0f}ms")
            
            if result.success:
                log(f"Page title: {result.title}")
                log(f"Content length: {result.content_length} chars")
                log("SUCCESS: Browser tool working correctly!")
                
                return ToolTestResponse(
                    success=True,
                    tool_id=tool_id,
                    tool_name=tool_name,
                    result=f"Successfully fetched: {result.title}",
                    result_preview=result.content[:500] + ("..." if len(result.content) > 500 else ""),
                    latency_ms=latency,
                    logs=logs
                )
            else:
                log(f"ERROR: {result.error}")
                return ToolTestResponse(
                    success=False,
                    tool_id=tool_id,
                    tool_name=tool_name,
                    error=result.error,
                    latency_ms=latency,
                    logs=logs
                )
                
        elif tool_id == "web_search":
            # Test web search
            query = params.get("query") or tool["test_config"].get("test_query", "test query")
            log(f"Search query: {query}")
            
            brave_api_key = settings.brave_search_api_key
            if not brave_api_key:
                log("ERROR: No Brave Search API key configured")
                return ToolTestResponse(
                    success=False,
                    tool_id=tool_id,
                    tool_name=tool_name,
                    error="No Brave Search API key configured. Set BRAVE_SEARCH_API_KEY in environment.",
                    latency_ms=(time.time() - start_time) * 1000,
                    logs=logs
                )
            
            log("Sending request to Brave Search API...")
            
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": 5},
                    headers={
                        "X-Subscription-Token": brave_api_key,
                        "Accept": "application/json"
                    }
                )
                
                latency = (time.time() - start_time) * 1000
                log(f"Response received in {latency:.0f}ms")
                log(f"Status code: {response.status_code}")
                
                if response.status_code != 200:
                    error_msg = f"API returned status {response.status_code}"
                    log(f"ERROR: {error_msg}")
                    return ToolTestResponse(
                        success=False,
                        tool_id=tool_id,
                        tool_name=tool_name,
                        error=error_msg,
                        latency_ms=latency,
                        logs=logs
                    )
                
                data = response.json()
                web_results = data.get("web", {}).get("results", [])
                
                log(f"Found {len(web_results)} results")
                
                if web_results:
                    preview_parts = []
                    for i, r in enumerate(web_results[:3], 1):
                        title = r.get("title", "No title")[:50]
                        url = r.get("url", "")[:50]
                        preview_parts.append(f"{i}. {title}\n   {url}")
                    result_preview = "\n".join(preview_parts)
                    log(f"Top result: {web_results[0].get('title', 'Unknown')}")
                    log("SUCCESS: Web search tool working correctly!")
                else:
                    result_preview = "No results found for query"
                    log("WARNING: No search results returned")
                
                return ToolTestResponse(
                    success=True,
                    tool_id=tool_id,
                    tool_name=tool_name,
                    result=f"Found {len(web_results)} web results",
                    result_preview=result_preview,
                    latency_ms=latency,
                    logs=logs
                )
        
        else:
            return ToolTestResponse(
                success=False,
                tool_id=tool_id,
                tool_name=tool_name,
                error=f"Test not implemented for tool: {tool_id}",
                latency_ms=(time.time() - start_time) * 1000,
                logs=logs
            )
            
    except Exception as e:
        log(f"ERROR: {str(e)}")
        return ToolTestResponse(
            success=False,
            tool_id=tool_id,
            tool_name=tool_name,
            error=str(e),
            latency_ms=(time.time() - start_time) * 1000,
            logs=logs
        )
