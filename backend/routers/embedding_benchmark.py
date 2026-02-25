"""Embedding Benchmark Router.

API endpoints for comparing embedding providers:
- Run benchmarks with file upload
- Get available providers
- Test provider connectivity
- View historical results
"""

import logging
import base64
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Request, HTTPException, Depends, UploadFile, File, Form
from pydantic import BaseModel

from backend.routers.auth import require_admin, UserResponse
from backend.services.embedding_benchmark import (
    EmbeddingBenchmarkService,
    BenchmarkProviderConfig,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response Models

class ProviderConfigRequest(BaseModel):
    """Provider configuration for benchmark."""
    provider_type: str  # "openai", "ollama", "vllm", "custom"
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    name: Optional[str] = None


class ChunkConfigRequest(BaseModel):
    """Chunking configuration."""
    chunk_size: int = 1000
    chunk_overlap: int = 200
    max_tokens: int = 512


class BenchmarkRequest(BaseModel):
    """Request to run a benchmark."""
    providers: List[ProviderConfigRequest]
    file_content: str  # Base64 encoded file content
    file_name: str
    chunk_config: Optional[ChunkConfigRequest] = None


class BenchmarkMetricsResponse(BaseModel):
    """Metrics for a single provider."""
    provider: str
    model: str
    provider_type: str
    total_time_ms: float
    chunking_time_ms: float
    embedding_time_ms: float
    avg_latency_ms: float
    tokens_processed: int
    chunks_created: int
    embedding_dimension: int
    memory_before_mb: float
    memory_after_mb: float
    memory_peak_mb: float
    cpu_percent: float
    cost_estimate_usd: Optional[float] = None
    success: bool
    error: Optional[str] = None


class BenchmarkResultResponse(BaseModel):
    """Complete benchmark result."""
    id: str
    timestamp: str
    file_name: str
    file_size_bytes: int
    content_preview: str
    chunk_config: dict
    results: List[BenchmarkMetricsResponse]
    winner: Optional[str] = None


class ProviderTestRequest(BaseModel):
    """Request to test a provider."""
    provider_type: str
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class ProviderTestResponse(BaseModel):
    """Response from provider test."""
    success: bool
    provider: str
    model: str
    latency_ms: float
    dimension: int
    error: Optional[str] = None


class ProviderModelInfo(BaseModel):
    """Information about an embedding model."""
    id: str
    name: str
    dimension: int


class ProviderInfo(BaseModel):
    """Information about an embedding provider."""
    name: str
    available: bool
    url: Optional[str] = None
    models: List[ProviderModelInfo]


class AvailableProvidersResponse(BaseModel):
    """Response with available providers."""
    openai: ProviderInfo
    ollama: ProviderInfo
    vllm: ProviderInfo


# Endpoints

@router.post("/run", response_model=BenchmarkResultResponse)
async def run_benchmark(
    request: Request,
    benchmark_request: BenchmarkRequest,
    admin: UserResponse = Depends(require_admin),
):
    """
    Run an embedding benchmark comparing multiple providers.
    
    Processes a file through chunking and embedding generation with
    each configured provider, collecting detailed performance metrics.
    
    Requires admin access.
    """
    db = request.app.state.db
    service = EmbeddingBenchmarkService(db)
    
    # Validate providers
    if len(benchmark_request.providers) < 1:
        raise HTTPException(
            status_code=400,
            detail="At least one provider is required"
        )
    
    if len(benchmark_request.providers) > 3:
        raise HTTPException(
            status_code=400,
            detail="Maximum 3 providers allowed for comparison"
        )
    
    # Decode file content
    try:
        file_content = base64.b64decode(benchmark_request.file_content)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid base64 file content: {e}"
        )
    
    # Convert provider configs
    providers = [
        BenchmarkProviderConfig(
            provider_type=p.provider_type,
            model=p.model,
            base_url=p.base_url,
            api_key=p.api_key,
            name=p.name,
        )
        for p in benchmark_request.providers
    ]
    
    # Get chunk config
    chunk_config = benchmark_request.chunk_config or ChunkConfigRequest()
    
    try:
        result = await service.run_benchmark(
            file_content=file_content,
            file_name=benchmark_request.file_name,
            providers=providers,
            chunk_size=chunk_config.chunk_size,
            chunk_overlap=chunk_config.chunk_overlap,
            max_tokens=chunk_config.max_tokens,
        )
        
        return BenchmarkResultResponse(
            id=result.id,
            timestamp=result.timestamp.isoformat(),
            file_name=result.file_name,
            file_size_bytes=result.file_size_bytes,
            content_preview=result.content_preview,
            chunk_config=result.chunk_config,
            results=[
                BenchmarkMetricsResponse(**m.to_dict())
                for m in result.results
            ],
            winner=result.winner,
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Benchmark failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Benchmark failed: {str(e)}"
        )


@router.post("/run-file")
async def run_benchmark_with_file(
    request: Request,
    file: UploadFile = File(...),
    providers: str = Form(...),  # JSON string of providers
    chunk_size: int = Form(default=1000),
    chunk_overlap: int = Form(default=200),
    max_tokens: int = Form(default=512),
    admin: UserResponse = Depends(require_admin),
):
    """
    Run benchmark with direct file upload.
    
    Alternative endpoint that accepts multipart form data
    instead of base64-encoded content.
    """
    import json
    
    db = request.app.state.db
    service = EmbeddingBenchmarkService(db)
    
    # Parse providers JSON
    try:
        providers_data = json.loads(providers)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid providers JSON: {e}"
        )
    
    if not isinstance(providers_data, list):
        raise HTTPException(
            status_code=400,
            detail="Providers must be a list"
        )
    
    if len(providers_data) < 1:
        raise HTTPException(
            status_code=400,
            detail="At least one provider is required"
        )
    
    if len(providers_data) > 3:
        raise HTTPException(
            status_code=400,
            detail="Maximum 3 providers allowed"
        )
    
    # Read file content
    file_content = await file.read()
    file_name = file.filename or "uploaded_file"
    
    # Convert provider configs
    provider_configs = [
        BenchmarkProviderConfig(
            provider_type=p.get("provider_type", ""),
            model=p.get("model", ""),
            base_url=p.get("base_url"),
            api_key=p.get("api_key"),
            name=p.get("name"),
        )
        for p in providers_data
    ]
    
    try:
        result = await service.run_benchmark(
            file_content=file_content,
            file_name=file_name,
            providers=provider_configs,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            max_tokens=max_tokens,
        )
        
        return result.to_dict()
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Benchmark failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Benchmark failed: {str(e)}"
        )


@router.get("/providers")
async def get_available_providers(
    request: Request,
    admin: UserResponse = Depends(require_admin),
):
    """
    Get available embedding providers.
    
    Returns information about configured providers including:
    - OpenAI (if API key is configured)
    - Ollama (if running locally)
    - vLLM/custom endpoints (from database)
    """
    db = request.app.state.db
    service = EmbeddingBenchmarkService(db)
    
    providers = await service.get_available_providers()
    return providers


@router.post("/test-provider", response_model=ProviderTestResponse)
async def test_provider(
    request: Request,
    test_request: ProviderTestRequest,
    admin: UserResponse = Depends(require_admin),
):
    """
    Test connectivity to an embedding provider.
    
    Generates a test embedding to verify the provider is accessible
    and returns the embedding dimension and latency.
    """
    db = request.app.state.db
    service = EmbeddingBenchmarkService(db)
    
    config = BenchmarkProviderConfig(
        provider_type=test_request.provider_type,
        model=test_request.model,
        base_url=test_request.base_url,
        api_key=test_request.api_key,
    )
    
    result = await service.test_provider(config)
    
    return ProviderTestResponse(
        success=result["success"],
        provider=result["provider"],
        model=result["model"],
        latency_ms=result["latency_ms"],
        dimension=result["dimension"],
        error=result.get("error"),
    )


@router.get("/results")
async def get_benchmark_results(
    request: Request,
    limit: int = 20,
    admin: UserResponse = Depends(require_admin),
):
    """
    Get historical benchmark results.
    
    Returns the most recent benchmark results from the database.
    """
    db = request.app.state.db
    service = EmbeddingBenchmarkService(db)
    
    results = await service.get_results(limit=limit)
    return {"results": results, "total": len(results)}


@router.get("/results/{benchmark_id}")
async def get_benchmark_result(
    request: Request,
    benchmark_id: str,
    admin: UserResponse = Depends(require_admin),
):
    """
    Get a specific benchmark result by ID.
    """
    db = request.app.state.db
    
    try:
        collection = db.db["benchmark_results"]
        doc = await collection.find_one({"_id": benchmark_id})
        
        if not doc:
            raise HTTPException(
                status_code=404,
                detail=f"Benchmark result {benchmark_id} not found"
            )
        
        doc["id"] = doc.pop("_id")
        return doc
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get benchmark result: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get benchmark result: {str(e)}"
        )


@router.delete("/results/{benchmark_id}")
async def delete_benchmark_result(
    request: Request,
    benchmark_id: str,
    admin: UserResponse = Depends(require_admin),
):
    """
    Delete a benchmark result.
    """
    db = request.app.state.db
    
    try:
        collection = db.db["benchmark_results"]
        result = await collection.delete_one({"_id": benchmark_id})
        
        if result.deleted_count == 0:
            raise HTTPException(
                status_code=404,
                detail=f"Benchmark result {benchmark_id} not found"
            )
        
        return {"success": True, "message": f"Deleted benchmark {benchmark_id}"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete benchmark result: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete benchmark result: {str(e)}"
        )
