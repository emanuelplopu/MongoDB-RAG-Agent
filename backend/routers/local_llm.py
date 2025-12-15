"""Local LLM discovery router - Offline mode with Ollama/vLLM support."""

import logging
import asyncio
import httpx
import psutil
import socket
from datetime import datetime
from typing import Dict, List, Optional, Any, Set
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel

from backend.core.config import settings
from backend.routers.auth import require_admin, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Hosts to check for local LLM providers
# Order matters: Docker host mapping first, then localhost, then common gateway IPs
DISCOVERY_HOSTS = [
    "host.docker.internal",  # Docker Desktop (Windows/Mac) maps to host OS
    "172.17.0.1",            # Default Docker bridge gateway (Linux)
    "localhost",             # Inside container (fallback)
    "127.0.0.1",             # Explicit localhost
]

# Known local LLM providers with their ports
LOCAL_LLM_PROVIDERS = {
    "ollama": {
        "name": "Ollama",
        "port": 11434,
        "api_path": "/api",
        "models_endpoint": "/api/tags",
        "pull_endpoint": "/api/pull",
        "generate_endpoint": "/api/generate",
        "embeddings_endpoint": "/api/embeddings"
    },
    "vllm": {
        "name": "vLLM",
        "port": 8000,
        "api_path": "/v1",
        "models_endpoint": "/v1/models",
        "completions_endpoint": "/v1/completions",
        "embeddings_endpoint": "/v1/embeddings"
    },
    "localai": {
        "name": "LocalAI",
        "port": 8080,
        "api_path": "/v1",
        "models_endpoint": "/v1/models",
        "completions_endpoint": "/v1/completions",
        "embeddings_endpoint": "/v1/embeddings"
    },
    "lmstudio": {
        "name": "LM Studio",
        "port": 1234,
        "api_path": "/v1",
        "models_endpoint": "/v1/models",
        "completions_endpoint": "/v1/completions"
    }
}

# Store custom endpoints discovered or added by user
CUSTOM_ENDPOINTS_COLLECTION = "custom_llm_endpoints"

# Model recommendations based on system resources
MODEL_RECOMMENDATIONS = {
    # RAM in GB -> recommended models
    8: [
        {"name": "llama3.2:1b", "provider": "ollama", "type": "chat", "size_gb": 1.5, "performance": 100},
        {"name": "qwen2.5:0.5b", "provider": "ollama", "type": "chat", "size_gb": 0.5, "performance": 100},
        {"name": "nomic-embed-text", "provider": "ollama", "type": "embedding", "size_gb": 0.3, "performance": 100},
        {"name": "moondream", "provider": "ollama", "type": "vision", "size_gb": 1.7, "performance": 90},
        {"name": "nanollava", "provider": "ollama", "type": "vision", "size_gb": 1.3, "performance": 85},
    ],
    16: [
        {"name": "llama3.2:3b", "provider": "ollama", "type": "chat", "size_gb": 2.5, "performance": 100},
        {"name": "qwen2.5:3b", "provider": "ollama", "type": "chat", "size_gb": 2.5, "performance": 100},
        {"name": "mistral:7b-q4_0", "provider": "ollama", "type": "chat", "size_gb": 4, "performance": 85},
        {"name": "nomic-embed-text", "provider": "ollama", "type": "embedding", "size_gb": 0.3, "performance": 100},
        {"name": "mxbai-embed-large", "provider": "ollama", "type": "embedding", "size_gb": 0.7, "performance": 100},
        {"name": "llava:7b", "provider": "ollama", "type": "vision", "size_gb": 4.7, "performance": 95},
        {"name": "bakllava", "provider": "ollama", "type": "vision", "size_gb": 4.7, "performance": 90},
        {"name": "moondream", "provider": "ollama", "type": "vision", "size_gb": 1.7, "performance": 95},
        {"name": "minicpm-v", "provider": "ollama", "type": "vision", "size_gb": 5.5, "performance": 88},
    ],
    32: [
        {"name": "llama3.1:8b", "provider": "ollama", "type": "chat", "size_gb": 5, "performance": 100},
        {"name": "mistral:7b", "provider": "ollama", "type": "chat", "size_gb": 4.5, "performance": 100},
        {"name": "qwen2.5:7b", "provider": "ollama", "type": "chat", "size_gb": 5, "performance": 100},
        {"name": "codellama:7b", "provider": "ollama", "type": "chat", "size_gb": 4.5, "performance": 95},
        {"name": "nomic-embed-text", "provider": "ollama", "type": "embedding", "size_gb": 0.3, "performance": 100},
        {"name": "mxbai-embed-large", "provider": "ollama", "type": "embedding", "size_gb": 0.7, "performance": 100},
        {"name": "llava:13b", "provider": "ollama", "type": "vision", "size_gb": 8.5, "performance": 100},
        {"name": "llava-llama3", "provider": "ollama", "type": "vision", "size_gb": 5.5, "performance": 100},
        {"name": "llava-phi3", "provider": "ollama", "type": "vision", "size_gb": 3.2, "performance": 95},
        {"name": "llama3.2-vision", "provider": "ollama", "type": "vision", "size_gb": 7.9, "performance": 98},
    ],
    64: [
        {"name": "llama3.1:70b-q4_0", "provider": "ollama", "type": "chat", "size_gb": 40, "performance": 100},
        {"name": "mixtral:8x7b", "provider": "ollama", "type": "chat", "size_gb": 26, "performance": 95},
        {"name": "qwen2.5:14b", "provider": "ollama", "type": "chat", "size_gb": 9, "performance": 100},
        {"name": "codellama:34b", "provider": "ollama", "type": "chat", "size_gb": 20, "performance": 90},
        {"name": "mxbai-embed-large", "provider": "ollama", "type": "embedding", "size_gb": 0.7, "performance": 100},
        {"name": "llava:34b", "provider": "ollama", "type": "vision", "size_gb": 20, "performance": 100},
        {"name": "llama3.2-vision:11b", "provider": "ollama", "type": "vision", "size_gb": 8, "performance": 100},
        {"name": "llama3.2-vision:90b", "provider": "ollama", "type": "vision", "size_gb": 55, "performance": 100},
    ]
}

# Known multimodal model patterns for capability detection
MULTIMODAL_PATTERNS = {
    "vision": [
        "llava", "bakllava", "moondream", "cogvlm", "minicpm-v", "llava-llama3",
        "llava-phi3", "nanollava", "obsidian", "llama3.2-vision", "pixtral",
        "internvl", "qwen2-vl", "phi-3-vision", "fuyu", "deepseek-vl"
    ],
    "audio": [
        "whisper", "distil-whisper", "insanely-fast-whisper", "faster-whisper",
        "whisper-large", "whisper-medium", "whisper-small", "whisper-tiny"
    ],
    "video": [
        "video-llava", "llama-vid", "videollm", "video-chatgpt"
    ]
}


class LocalProvider(BaseModel):
    """Discovered local LLM provider."""
    id: str
    name: str
    url: str
    host: str  # The host where it was discovered
    location: str  # 'host', 'container', 'network', 'custom'
    status: str  # available, unavailable, error
    models: List[Dict[str, Any]] = []
    supports_embeddings: bool = False
    supports_vision: bool = False  # Image processing (LLaVA, etc.)
    supports_audio: bool = False   # Audio transcription (Whisper)
    supports_video: bool = False   # Video understanding
    error: Optional[str] = None


class CustomEndpoint(BaseModel):
    """User-defined custom LLM endpoint."""
    id: str
    name: str
    url: str
    provider_type: str  # ollama, openai-compatible
    enabled: bool = True


class SystemResources(BaseModel):
    """System resource information."""
    cpu_cores: int
    ram_total_gb: float
    ram_available_gb: float
    gpu_available: bool
    gpu_name: Optional[str] = None
    gpu_memory_gb: Optional[float] = None


class ModelRecommendation(BaseModel):
    """Model recommendation with performance metrics."""
    name: str
    provider: str
    type: str  # chat or embedding
    size_gb: float
    performance_score: int  # 0-100, 100 being optimal
    is_installed: bool = False
    warning: Optional[str] = None


class DiscoveryResult(BaseModel):
    """Complete discovery result."""
    providers: List[LocalProvider]
    resources: SystemResources
    recommendations: List[ModelRecommendation]
    offline_ready: bool
    has_chat_model: bool
    has_embedding_model: bool
    has_vision_model: bool = False   # Image processing capability
    has_audio_model: bool = False    # Audio transcription capability
    has_video_model: bool = False    # Video understanding capability
    scanned_hosts: List[str] = []  # Hosts that were scanned
    custom_endpoints: List[CustomEndpoint] = []  # User-defined endpoints


class NetworkScanRequest(BaseModel):
    """Request to scan network for LLM providers."""
    ip_range: Optional[str] = None  # e.g., "192.168.1.0/24" or "192.168.1.1-255"
    custom_ips: List[str] = []  # Specific IPs to check
    ports: List[int] = []  # Specific ports to check (default: all known provider ports)


class OfflineModeConfig(BaseModel):
    """Offline mode configuration."""
    enabled: bool
    # Chat model
    chat_provider: Optional[str] = None
    chat_model: Optional[str] = None
    chat_url: Optional[str] = None
    # Embedding model
    embedding_provider: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_url: Optional[str] = None
    # Vision model (for image processing)
    vision_provider: Optional[str] = None
    vision_model: Optional[str] = None
    vision_url: Optional[str] = None
    # Audio model (for transcription - local Whisper)
    audio_provider: Optional[str] = None
    audio_model: Optional[str] = None
    audio_url: Optional[str] = None


# Store offline mode config
_offline_config: Optional[OfflineModeConfig] = None
OFFLINE_CONFIG_COLLECTION = "offline_config"


def get_profile_manager():
    """Get profile manager instance."""
    from src.profile import get_profile_manager as get_pm
    return get_pm(settings.profiles_path)


@router.get("/discover", response_model=DiscoveryResult)
async def discover_local_llms(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """
    Discover available local LLM providers.
    
    Scans for Ollama, vLLM, LocalAI, and LM Studio installations.
    Checks host OS (via host.docker.internal), localhost, and custom endpoints.
    Returns available models and recommendations based on system resources.
    """
    providers = []
    resources = _get_system_resources()
    scanned_hosts = []
    
    # Get custom endpoints from database
    db = request.app.state.db
    custom_endpoints = await _get_custom_endpoints(db)
    
    # Check each provider across multiple hosts
    for provider_id, config in LOCAL_LLM_PROVIDERS.items():
        provider_found = False
        
        # Check each potential host
        for host in DISCOVERY_HOSTS:
            if host not in scanned_hosts:
                scanned_hosts.append(host)
            
            url = f"http://{host}:{config['port']}"
            provider_result = await _check_provider_at_url(
                provider_id, config, url, host, _get_location(host)
            )
            
            if provider_result.status == "available":
                providers.append(provider_result)
                provider_found = True
                logger.info(f"Found {config['name']} at {url}")
                break  # Found this provider, move to next
        
        # If not found on any host, add as unavailable
        if not provider_found:
            providers.append(LocalProvider(
                id=provider_id,
                name=config["name"],
                url=f"http://localhost:{config['port']}",
                host="localhost",
                location="unknown",
                status="unavailable",
                error="Not found on any scanned host"
            ))
    
    # Check custom endpoints
    for endpoint in custom_endpoints:
        if endpoint.enabled:
            provider_config = _get_provider_config_for_type(endpoint.provider_type)
            if provider_config:
                result = await _check_provider_at_url(
                    endpoint.id, provider_config, endpoint.url, 
                    endpoint.url, "custom"
                )
                result.name = endpoint.name
                providers.append(result)
    
    # Get model recommendations
    recommendations = _get_model_recommendations(resources, providers)
    
    # Check capabilities across all available providers
    has_chat = any(
        m.get("type") in ["chat", "vision", "video"] or "chat" in m.get("capabilities", [])
        for p in providers 
        if p.status == "available" 
        for m in p.models
    )
    has_embedding = any(
        p.supports_embeddings
        for p in providers 
        if p.status == "available"
    )
    has_vision = any(
        p.supports_vision
        for p in providers 
        if p.status == "available"
    )
    has_audio = any(
        p.supports_audio
        for p in providers 
        if p.status == "available"
    )
    has_video = any(
        p.supports_video
        for p in providers 
        if p.status == "available"
    )
    
    return DiscoveryResult(
        providers=providers,
        resources=resources,
        recommendations=recommendations,
        offline_ready=has_chat and has_embedding,
        has_chat_model=has_chat,
        has_embedding_model=has_embedding,
        has_vision_model=has_vision,
        has_audio_model=has_audio,
        has_video_model=has_video,
        scanned_hosts=scanned_hosts,
        custom_endpoints=custom_endpoints
    )


def _get_location(host: str) -> str:
    """Determine the location type based on host."""
    if host == "host.docker.internal":
        return "host"  # Host OS
    elif host == "172.17.0.1":
        return "host"  # Docker gateway (Linux)
    elif host in ["localhost", "127.0.0.1"]:
        return "container"  # Inside container
    else:
        return "network"  # Network/custom


def _get_provider_config_for_type(provider_type: str) -> Optional[Dict]:
    """Get provider config based on type."""
    if provider_type == "ollama":
        return LOCAL_LLM_PROVIDERS["ollama"]
    elif provider_type in ["openai-compatible", "vllm", "localai"]:
        return LOCAL_LLM_PROVIDERS["vllm"]  # Use vLLM config for OpenAI-compatible
    return None


async def _get_custom_endpoints(db) -> List[CustomEndpoint]:
    """Get custom endpoints from database."""
    try:
        collection = db.db[CUSTOM_ENDPOINTS_COLLECTION]
        cursor = collection.find({})
        endpoints = []
        async for doc in cursor:
            doc.pop("_id", None)
            endpoints.append(CustomEndpoint(**doc))
        return endpoints
    except Exception as e:
        logger.error(f"Error getting custom endpoints: {e}")
        return []


async def _check_provider_at_url(
    provider_id: str, 
    config: Dict[str, Any], 
    url: str,
    host: str,
    location: str
) -> LocalProvider:
    """Check if a provider is available at a specific URL and get its models."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:  # Short timeout for scanning
            # Try to get models
            models_url = f"{url}{config['models_endpoint']}"
            response = await client.get(models_url)
            response.raise_for_status()
            
            data = response.json()
            
            models = []
            supports_embeddings = False
            supports_vision = False
            supports_audio = False
            supports_video = False
            
            if provider_id == "ollama" or config.get("models_endpoint") == "/api/tags":
                # Ollama returns models in "models" key
                for model in data.get("models", []):
                    model_name = model.get("name", "").lower()
                    model_type, capabilities = _detect_model_capabilities(model_name)
                    
                    model_info = {
                        "name": model.get("name", ""),
                        "size_gb": round(model.get("size", 0) / (1024**3), 2),
                        "modified": model.get("modified_at", ""),
                        "type": model_type,
                        "capabilities": capabilities
                    }
                    
                    if "embedding" in capabilities:
                        supports_embeddings = True
                    if "vision" in capabilities:
                        supports_vision = True
                    if "audio" in capabilities:
                        supports_audio = True
                    if "video" in capabilities:
                        supports_video = True
                    
                    models.append(model_info)
            else:
                # OpenAI-compatible API
                for model in data.get("data", []):
                    model_name = model.get("id", "").lower()
                    model_type, capabilities = _detect_model_capabilities(model_name)
                    
                    model_info = {
                        "name": model.get("id", ""),
                        "type": model_type,
                        "capabilities": capabilities
                    }
                    
                    if "embedding" in capabilities:
                        supports_embeddings = True
                    if "vision" in capabilities:
                        supports_vision = True
                    if "audio" in capabilities:
                        supports_audio = True
                    if "video" in capabilities:
                        supports_video = True
                    
                    models.append(model_info)
            
            return LocalProvider(
                id=provider_id,
                name=config["name"],
                url=url,
                host=host,
                location=location,
                status="available",
                models=models,
                supports_embeddings=supports_embeddings,
                supports_vision=supports_vision,
                supports_audio=supports_audio,
                supports_video=supports_video
            )
            
    except httpx.ConnectError:
        return LocalProvider(
            id=provider_id,
            name=config["name"],
            url=url,
            host=host,
            location=location,
            status="unavailable",
            error="Connection refused"
        )
    except httpx.TimeoutException:
        return LocalProvider(
            id=provider_id,
            name=config["name"],
            url=url,
            host=host,
            location=location,
            status="unavailable",
            error="Connection timeout"
        )
    except Exception as e:
        return LocalProvider(
            id=provider_id,
            name=config["name"],
            url=url,
            host=host,
            location=location,
            status="error",
            error=str(e)
        )


def _detect_model_capabilities(model_name: str) -> tuple[str, List[str]]:
    """
    Detect model type and multimodal capabilities from model name.
    
    Returns:
        Tuple of (primary_type, list_of_capabilities)
    """
    model_lower = model_name.lower()
    capabilities = []
    primary_type = "chat"
    
    # Check for embedding models
    if "embed" in model_lower:
        capabilities.append("embedding")
        primary_type = "embedding"
    
    # Check for vision models
    for pattern in MULTIMODAL_PATTERNS.get("vision", []):
        if pattern in model_lower:
            capabilities.append("vision")
            if primary_type == "chat":
                primary_type = "vision"
            break
    
    # Check for audio models
    for pattern in MULTIMODAL_PATTERNS.get("audio", []):
        if pattern in model_lower:
            capabilities.append("audio")
            if primary_type == "chat":
                primary_type = "audio"
            break
    
    # Check for video models
    for pattern in MULTIMODAL_PATTERNS.get("video", []):
        if pattern in model_lower:
            capabilities.append("video")
            # Video models typically also support vision
            if "vision" not in capabilities:
                capabilities.append("vision")
            if primary_type == "chat":
                primary_type = "video"
            break
    
    # Default to chat if no special capabilities
    if not capabilities:
        capabilities.append("chat")
    
    return primary_type, capabilities


def _get_system_resources() -> SystemResources:
    """Get system resource information."""
    memory = psutil.virtual_memory()
    
    gpu_available = False
    gpu_name = None
    gpu_memory_gb = None
    
    # Try to detect GPU
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            gpu_available = True
            parts = result.stdout.strip().split(", ")
            if len(parts) >= 2:
                gpu_name = parts[0]
                gpu_memory_gb = round(float(parts[1]) / 1024, 2)
    except Exception:
        pass
    
    return SystemResources(
        cpu_cores=psutil.cpu_count(),
        ram_total_gb=round(memory.total / (1024**3), 2),
        ram_available_gb=round(memory.available / (1024**3), 2),
        gpu_available=gpu_available,
        gpu_name=gpu_name,
        gpu_memory_gb=gpu_memory_gb
    )


def _get_model_recommendations(resources: SystemResources, providers: List[LocalProvider]) -> List[ModelRecommendation]:
    """Get model recommendations based on system resources."""
    ram_gb = resources.ram_total_gb
    
    # Find appropriate tier
    if ram_gb >= 64:
        tier = 64
    elif ram_gb >= 32:
        tier = 32
    elif ram_gb >= 16:
        tier = 16
    else:
        tier = 8
    
    recommended_models = MODEL_RECOMMENDATIONS.get(tier, MODEL_RECOMMENDATIONS[8])
    
    # Get installed models
    installed_models = set()
    for provider in providers:
        if provider.status == "available":
            for model in provider.models:
                installed_models.add(model.get("name", ""))
    
    recommendations = []
    for model in recommended_models:
        # Calculate performance impact for larger models
        perf = model["performance"]
        warning = None
        
        if model["size_gb"] > resources.ram_available_gb * 0.7:
            perf = max(30, perf - 40)
            warning = f"Model may cause slowdowns - requires {model['size_gb']}GB but only {resources.ram_available_gb:.1f}GB available"
        elif model["size_gb"] > resources.ram_available_gb * 0.5:
            perf = max(50, perf - 20)
            warning = "Model may compete for memory with other processes"
        
        recommendations.append(ModelRecommendation(
            name=model["name"],
            provider=model["provider"],
            type=model["type"],
            size_gb=model["size_gb"],
            performance_score=perf,
            is_installed=model["name"] in installed_models,
            warning=warning
        ))
    
    # Sort by performance, but prioritize installed models
    recommendations.sort(key=lambda x: (-x.is_installed, -x.performance_score))
    
    return recommendations


@router.post("/pull/{provider_id}")
async def pull_model(
    request: Request,
    provider_id: str,
    model_name: str,
    provider_url: Optional[str] = None,  # Allow specifying the URL
    admin: UserResponse = Depends(require_admin)
):
    """
    Pull/download a model for a local provider.
    
    Currently supports Ollama only.
    """
    if provider_id != "ollama" and provider_id not in LOCAL_LLM_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unknown provider")
    
    if provider_id != "ollama":
        raise HTTPException(status_code=400, detail="Pull is only supported for Ollama")
    
    config = LOCAL_LLM_PROVIDERS["ollama"]
    
    # Try to find a working URL if not specified
    if not provider_url:
        for host in DISCOVERY_HOSTS:
            test_url = f"http://{host}:{config['port']}"
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    response = await client.get(f"{test_url}/api/tags")
                    if response.status_code == 200:
                        provider_url = test_url
                        break
            except:
                continue
    
    if not provider_url:
        raise HTTPException(status_code=404, detail="Could not find Ollama instance")
    
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:  # 10 minute timeout for large models
            url = f"{provider_url}{config['pull_endpoint']}"
            
            # Start the pull
            response = await client.post(
                url,
                json={"name": model_name, "stream": False},
                timeout=600.0
            )
            
            if response.status_code == 200:
                return {"success": True, "message": f"Model {model_name} pulled successfully"}
            else:
                return {"success": False, "error": response.text}
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pull-status/{provider_id}")
async def get_pull_status(
    request: Request,
    provider_id: str,
    admin: UserResponse = Depends(require_admin)
):
    """Get the status of ongoing model pulls."""
    # This would need to track pull jobs, simplified for now
    return {"status": "idle", "message": "No active pulls"}


@router.get("/offline-config")
async def get_offline_config(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """Get current offline mode configuration."""
    db = request.app.state.db
    
    try:
        collection = db.db[OFFLINE_CONFIG_COLLECTION]
        doc = await collection.find_one({"_id": "config"})
        
        if doc:
            del doc["_id"]
            return OfflineModeConfig(**doc)
        
        return OfflineModeConfig(enabled=False)
    except Exception as e:
        logger.error(f"Error getting offline config: {e}")
        return OfflineModeConfig(enabled=False)


@router.post("/offline-config")
async def save_offline_config(
    request: Request,
    config: OfflineModeConfig,
    admin: UserResponse = Depends(require_admin)
):
    """Save offline mode configuration."""
    db = request.app.state.db
    
    try:
        collection = db.db[OFFLINE_CONFIG_COLLECTION]
        doc = {"_id": "config", **config.model_dump()}
        
        await collection.replace_one(
            {"_id": "config"},
            doc,
            upsert=True
        )
        
        # Apply configuration to settings if enabled
        if config.enabled:
            _apply_offline_config(config)
        
        return {"success": True, "config": config}
    except Exception as e:
        logger.error(f"Error saving offline config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _apply_offline_config(config: OfflineModeConfig):
    """Apply offline configuration to runtime settings."""
    if config.chat_provider and config.chat_model:
        if config.chat_provider == "ollama":
            settings.llm_provider = "ollama"
            settings.llm_model = config.chat_model
            settings.llm_api_base = config.chat_url or "http://localhost:11434"
        elif config.chat_provider in ["vllm", "localai", "lmstudio"]:
            settings.llm_provider = "openai"  # OpenAI-compatible
            settings.llm_model = config.chat_model
            settings.llm_api_base = config.chat_url
    
    if config.embedding_provider and config.embedding_model:
        if config.embedding_provider == "ollama":
            settings.embedding_provider = "ollama"
            settings.embedding_model = config.embedding_model
            settings.embedding_api_base = config.embedding_url or "http://localhost:11434"
        elif config.embedding_provider in ["vllm", "localai"]:
            settings.embedding_provider = "openai"
            settings.embedding_model = config.embedding_model
            settings.embedding_api_base = config.embedding_url


@router.post("/test-model")
async def test_local_model(
    request: Request,
    provider_id: str,
    model_name: str,
    model_type: str = "chat",  # chat or embedding
    provider_url: Optional[str] = None,  # Allow specifying the URL
    admin: UserResponse = Depends(require_admin)
):
    """Test a local model to ensure it works."""
    if provider_id not in LOCAL_LLM_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unknown provider")
    
    config = LOCAL_LLM_PROVIDERS[provider_id]
    
    # Find working URL if not specified
    if not provider_url:
        for host in DISCOVERY_HOSTS:
            test_url = f"http://{host}:{config['port']}"
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    response = await client.get(f"{test_url}{config['models_endpoint']}")
                    if response.status_code == 200:
                        provider_url = test_url
                        break
            except:
                continue
    
    if not provider_url:
        return {"success": False, "error": f"Could not find {config['name']} instance"}
    
    url = provider_url
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if model_type == "chat":
                if provider_id == "ollama":
                    response = await client.post(
                        f"{url}/api/generate",
                        json={
                            "model": model_name,
                            "prompt": "Say 'Hello' and nothing else.",
                            "stream": False
                        }
                    )
                else:
                    response = await client.post(
                        f"{url}/v1/completions",
                        json={
                            "model": model_name,
                            "prompt": "Say 'Hello' and nothing else.",
                            "max_tokens": 10
                        }
                    )
                
                if response.status_code == 200:
                    return {
                        "success": True,
                        "model": model_name,
                        "type": model_type,
                        "response": response.json()
                    }
                else:
                    return {"success": False, "error": response.text}
            
            else:  # embedding
                if provider_id == "ollama":
                    response = await client.post(
                        f"{url}/api/embeddings",
                        json={
                            "model": model_name,
                            "prompt": "Test embedding"
                        }
                    )
                else:
                    response = await client.post(
                        f"{url}/v1/embeddings",
                        json={
                            "model": model_name,
                            "input": "Test embedding"
                        }
                    )
                
                if response.status_code == 200:
                    data = response.json()
                    embedding = data.get("embedding") or data.get("data", [{}])[0].get("embedding", [])
                    return {
                        "success": True,
                        "model": model_name,
                        "type": model_type,
                        "dimensions": len(embedding) if embedding else 0
                    }
                else:
                    return {"success": False, "error": response.text}
                    
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/compare-models")
async def compare_model_performance(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """
    Compare performance metrics between recommended models.
    
    Shows estimated tokens/second and response times based on system resources.
    """
    resources = _get_system_resources()
    
    # Base performance estimates (tokens/second on optimal hardware)
    base_performance = {
        "llama3.2:1b": {"tps": 100, "latency_ms": 50},
        "llama3.2:3b": {"tps": 60, "latency_ms": 80},
        "llama3.1:8b": {"tps": 35, "latency_ms": 150},
        "mistral:7b": {"tps": 40, "latency_ms": 120},
        "qwen2.5:7b": {"tps": 38, "latency_ms": 130},
        "mixtral:8x7b": {"tps": 15, "latency_ms": 300},
        "llama3.1:70b-q4_0": {"tps": 8, "latency_ms": 500},
    }
    
    comparisons = []
    
    for model, perf in base_performance.items():
        # Adjust for system resources
        model_info = next(
            (m for tier in MODEL_RECOMMENDATIONS.values() for m in tier if m["name"] == model),
            None
        )
        
        if not model_info:
            continue
        
        # Calculate adjusted performance
        memory_factor = min(1.0, resources.ram_available_gb / (model_info["size_gb"] * 2))
        cpu_factor = min(1.0, resources.cpu_cores / 8)
        gpu_factor = 2.0 if resources.gpu_available else 1.0
        
        adjusted_tps = int(perf["tps"] * memory_factor * cpu_factor * gpu_factor)
        adjusted_latency = int(perf["latency_ms"] / (memory_factor * gpu_factor))
        
        comparisons.append({
            "model": model,
            "size_gb": model_info["size_gb"],
            "tokens_per_second": adjusted_tps,
            "first_token_latency_ms": adjusted_latency,
            "recommended": memory_factor >= 0.7,
            "warning": "May be slow - model larger than available memory" if memory_factor < 0.7 else None
        })
    
    # Sort by tokens per second (higher is better)
    comparisons.sort(key=lambda x: -x["tokens_per_second"])
    
    return {
        "comparisons": comparisons,
        "system": {
            "ram_gb": resources.ram_total_gb,
            "cpu_cores": resources.cpu_cores,
            "gpu": resources.gpu_name if resources.gpu_available else "None"
        }
    }


# ============================================
# Network Scanning Endpoints
# ============================================

@router.post("/scan-network")
async def scan_network_for_llms(
    request: Request,
    scan_request: NetworkScanRequest,
    admin: UserResponse = Depends(require_admin)
):
    """
    Scan network for LLM providers.
    
    Can scan:
    - Specific IPs provided in custom_ips
    - IP range like "192.168.1.1-50" or "192.168.1.0/24"
    
    Checks all known LLM provider ports on each IP.
    """
    found_providers = []
    scanned_ips = []
    ports_to_check = scan_request.ports or [p["port"] for p in LOCAL_LLM_PROVIDERS.values()]
    
    # Collect IPs to scan
    ips_to_scan: Set[str] = set(scan_request.custom_ips)
    
    # Parse IP range if provided
    if scan_request.ip_range:
        ips_to_scan.update(_parse_ip_range(scan_request.ip_range))
    
    if not ips_to_scan:
        return {
            "success": False,
            "error": "No IPs to scan. Provide custom_ips or ip_range.",
            "found": [],
            "scanned": []
        }
    
    # Limit scan to prevent abuse
    MAX_IPS = 256
    if len(ips_to_scan) > MAX_IPS:
        return {
            "success": False,
            "error": f"Too many IPs to scan. Maximum is {MAX_IPS}.",
            "found": [],
            "scanned": []
        }
    
    # Scan in parallel with semaphore to limit concurrent connections
    semaphore = asyncio.Semaphore(20)  # Max 20 concurrent connections
    
    async def check_ip_port(ip: str, port: int, provider_id: str, config: Dict):
        async with semaphore:
            url = f"http://{ip}:{port}"
            result = await _check_provider_at_url(
                provider_id, config, url, ip, "network"
            )
            if result.status == "available":
                return result
            return None
    
    tasks = []
    for ip in ips_to_scan:
        scanned_ips.append(ip)
        for provider_id, config in LOCAL_LLM_PROVIDERS.items():
            if config["port"] in ports_to_check:
                tasks.append(check_ip_port(ip, config["port"], provider_id, config))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for result in results:
        if result and not isinstance(result, Exception):
            found_providers.append(result)
    
    return {
        "success": True,
        "found": [p.model_dump() for p in found_providers],
        "scanned": scanned_ips,
        "scanned_count": len(scanned_ips),
        "found_count": len(found_providers)
    }


def _parse_ip_range(ip_range: str) -> List[str]:
    """Parse IP range string into list of IPs."""
    ips = []
    
    try:
        if "-" in ip_range:
            # Format: 192.168.1.1-50 or 192.168.1.1-192.168.1.50
            parts = ip_range.split("-")
            if len(parts) == 2:
                base = parts[0].rsplit(".", 1)
                if len(base) == 2:
                    prefix = base[0]
                    start = int(base[1])
                    end = int(parts[1]) if "." not in parts[1] else int(parts[1].rsplit(".", 1)[1])
                    for i in range(start, min(end + 1, 256)):
                        ips.append(f"{prefix}.{i}")
        elif "/" in ip_range:
            # CIDR notation: 192.168.1.0/24
            import ipaddress
            network = ipaddress.ip_network(ip_range, strict=False)
            # Limit to /24 or smaller
            if network.prefixlen >= 24:
                for ip in network.hosts():
                    ips.append(str(ip))
            else:
                # For larger networks, just sample
                for i, ip in enumerate(network.hosts()):
                    if i >= 256:
                        break
                    ips.append(str(ip))
    except Exception as e:
        logger.error(f"Error parsing IP range {ip_range}: {e}")
    
    return ips


# ============================================
# Custom Endpoints Management
# ============================================

@router.get("/custom-endpoints")
async def list_custom_endpoints(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """List all custom LLM endpoints."""
    db = request.app.state.db
    endpoints = await _get_custom_endpoints(db)
    return {"endpoints": [e.model_dump() for e in endpoints]}


@router.post("/custom-endpoints")
async def add_custom_endpoint(
    request: Request,
    endpoint: CustomEndpoint,
    admin: UserResponse = Depends(require_admin)
):
    """Add a custom LLM endpoint."""
    db = request.app.state.db
    
    try:
        collection = db.db[CUSTOM_ENDPOINTS_COLLECTION]
        doc = {"_id": endpoint.id, **endpoint.model_dump()}
        
        await collection.replace_one(
            {"_id": endpoint.id},
            doc,
            upsert=True
        )
        
        return {"success": True, "endpoint": endpoint}
    except Exception as e:
        logger.error(f"Error adding custom endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/custom-endpoints/{endpoint_id}")
async def delete_custom_endpoint(
    request: Request,
    endpoint_id: str,
    admin: UserResponse = Depends(require_admin)
):
    """Delete a custom LLM endpoint."""
    db = request.app.state.db
    
    try:
        collection = db.db[CUSTOM_ENDPOINTS_COLLECTION]
        result = await collection.delete_one({"_id": endpoint_id})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Endpoint not found")
        
        return {"success": True, "deleted_id": endpoint_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting custom endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/custom-endpoints/{endpoint_id}/test")
async def test_custom_endpoint(
    request: Request,
    endpoint_id: str,
    admin: UserResponse = Depends(require_admin)
):
    """Test a custom endpoint to check if it's reachable."""
    db = request.app.state.db
    
    # Get the endpoint
    try:
        collection = db.db[CUSTOM_ENDPOINTS_COLLECTION]
        doc = await collection.find_one({"_id": endpoint_id})
        
        if not doc:
            raise HTTPException(status_code=404, detail="Endpoint not found")
        
        doc.pop("_id", None)
        endpoint = CustomEndpoint(**doc)
        
        # Test the endpoint
        config = _get_provider_config_for_type(endpoint.provider_type)
        if not config:
            return {"success": False, "error": "Unknown provider type"}
        
        result = await _check_provider_at_url(
            endpoint.id, config, endpoint.url, endpoint.url, "custom"
        )
        
        return {
            "success": result.status == "available",
            "status": result.status,
            "models": result.models,
            "error": result.error
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}
