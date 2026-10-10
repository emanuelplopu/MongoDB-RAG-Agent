"""Embedding Benchmark Router.

API endpoints for comparing embedding providers:
- Run benchmarks with file upload
- Run embedding validation checks on real index chunks
- Get available providers
- Test provider connectivity
- View historical results
"""

import logging
import base64
from datetime import datetime
from typing import List, Literal, Optional
from fastapi import APIRouter, Request, HTTPException, Depends, UploadFile, File, Form
from pydantic import BaseModel, Field

from backend.routers.auth import require_admin, UserResponse
from backend.services.embedding_benchmark import (
    EmbeddingBenchmarkService,
    BenchmarkProviderConfig,
)
from backend.services.embedding_checks import CheckOptions, EmbeddingChecksResult, SampleInfo

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response Models

class ProviderConfigRequest(BaseModel):
    """Provider configuration for benchmark."""
    provider_type: str  # "openai", "ollama", "vllm", "custom", "llamacpp"
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    name: Optional[str] = None
    query_prefix: str = ""
    document_prefix: str = ""
    truncate_dim: Optional[int] = Field(default=None, ge=16)

    def to_config(self) -> BenchmarkProviderConfig:
        """Convert to the service-level provider configuration."""
        return BenchmarkProviderConfig(
            provider_type=self.provider_type,
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            name=self.name,
            query_prefix=self.query_prefix,
            document_prefix=self.document_prefix,
            truncate_dim=self.truncate_dim,
        )


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
    google: Optional[ProviderInfo] = None
    voyageai: Optional[ProviderInfo] = None
    ollama: ProviderInfo
    llamacpp: Optional[ProviderInfo] = None
    vllm: ProviderInfo


class CheckSampleRequest(BaseModel):
    """Where the texts for the validation checks come from."""
    source: Literal["index", "file"] = "index"
    limit: int = Field(default=200, ge=10, le=2000)
    collection: Optional[str] = Field(default=None, description="Chunk collection; defaults to the active profile's")
    compare_with_index: bool = Field(default=True, description="Compute ranking agreement vs. stored vectors")
    file_content: Optional[str] = Field(default=None, description="Base64 file content when source == 'file'")
    file_name: Optional[str] = None
    chunk_config: Optional[ChunkConfigRequest] = None


class EmbeddingChecksRequest(BaseModel):
    """Request to run the embedding validation checks."""
    providers: List[ProviderConfigRequest] = Field(min_length=1, max_length=3)
    options: CheckOptions = Field(default_factory=CheckOptions)
    sample: CheckSampleRequest = Field(default_factory=CheckSampleRequest)


class EmbeddingChecksHistoryResponse(BaseModel):
    """Historical validation runs."""
    results: List[dict]
    total: int


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
    providers = [p.to_config() for p in benchmark_request.providers]

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
            query_prefix=p.get("query_prefix", ""),
            document_prefix=p.get("document_prefix", ""),
            truncate_dim=p.get("truncate_dim"),
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


async def _load_check_sample(
    request: Request,
    service: EmbeddingBenchmarkService,
    sample: CheckSampleRequest,
) -> tuple[List[str], Optional[List[List[float]]], SampleInfo]:
    """Resolve the sample texts (and stored reference vectors) for a checks run."""
    db = request.app.state.db

    if sample.source == "file":
        if not sample.file_content or not sample.file_name:
            raise HTTPException(status_code=400, detail="file_content and file_name are required when source is 'file'")
        try:
            file_bytes = base64.b64decode(sample.file_content)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid base64 file content: {e}")
        text, _ = await service._extract_text_content(file_bytes, sample.file_name)
        cfg = sample.chunk_config or ChunkConfigRequest()
        chunks = await service._chunk_document(
            text, sample.file_name, chunk_size=cfg.chunk_size, chunk_overlap=cfg.chunk_overlap, max_tokens=cfg.max_tokens
        )
        texts = [c["content"] for c in chunks][: sample.limit]
        if not texts:
            raise HTTPException(status_code=400, detail="The uploaded file produced no text chunks")
        info = SampleInfo(
            source="file",
            count=len(texts),
            avg_chars=round(sum(len(t) for t in texts) / len(texts), 1),
            file_name=sample.file_name,
        )
        return texts, None, info

    collection = db.db[sample.collection] if sample.collection else db.chunks_collection
    active_name = getattr(db, "_current_chunks_collection", None)
    collection_name = sample.collection or (active_name if isinstance(active_name, str) else "chunks")
    projection = {"content": 1, "embedding": 1, "metadata.embedding_model": 1}
    docs = [d async for d in collection.find({"embedding": {"$exists": True}}, projection).limit(sample.limit)]
    if not docs:
        docs = [d async for d in collection.find({"content": {"$exists": True}}, projection).limit(sample.limit)]
    docs = [d for d in docs if isinstance(d.get("content"), str) and d["content"].strip()]
    if not docs:
        raise HTTPException(
            status_code=400,
            detail=f"No chunks with content found in '{collection_name}'; ingest documents or use source='file'",
        )
    texts = [d["content"] for d in docs]
    vectors = [d["embedding"] for d in docs if isinstance(d.get("embedding"), list)]
    reference = vectors if (sample.compare_with_index and len(vectors) == len(texts)) else None
    reference_model = next(
        (d.get("metadata", {}).get("embedding_model") for d in docs if isinstance(d.get("metadata"), dict)),
        None,
    )
    info = SampleInfo(
        source="index",
        count=len(texts),
        avg_chars=round(sum(len(t) for t in texts) / len(texts), 1),
        database=getattr(db, "current_database_name", None) if isinstance(getattr(db, "current_database_name", None), str) else None,
        collection=collection_name,
        reference_model=(reference_model or "unknown") if reference else None,
        reference_dimension=len(reference[0]) if reference else None,
    )
    return texts, reference, info


@router.post("/checks", response_model=EmbeddingChecksResult)
async def run_embedding_checks(
    request: Request,
    checks_request: EmbeddingChecksRequest,
    admin: UserResponse = Depends(require_admin),
):
    """
    Run the embedding validation checks for up to three providers.

    Samples real chunks from the active index (or chunks an uploaded file),
    then measures query latency, throughput by batch size, vector
    normalisation/dimension, Matryoshka truncation stability, pseudo-query
    retrieval sanity, image-embedding support (llama.cpp) and ranking
    agreement between providers and against the vectors stored in the index.

    Requires admin access.
    """
    db = request.app.state.db
    service = EmbeddingBenchmarkService(db)

    texts, reference, sample_info = await _load_check_sample(request, service, checks_request.sample)
    providers = [p.to_config() for p in checks_request.providers]

    try:
        return await service.run_checks(
            providers=providers,
            texts=texts,
            options=checks_request.options,
            sample=sample_info,
            reference_vectors=reference,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Embedding checks failed: {e}")
        raise HTTPException(status_code=500, detail=f"Embedding checks failed: {str(e)}")


@router.get("/checks/results", response_model=EmbeddingChecksHistoryResponse)
async def get_embedding_checks_results(
    request: Request,
    limit: int = 20,
    admin: UserResponse = Depends(require_admin),
):
    """Get historical embedding validation runs."""
    db = request.app.state.db
    service = EmbeddingBenchmarkService(db)

    results = await service.get_results(limit=limit, kind="checks")
    return EmbeddingChecksHistoryResponse(results=results, total=len(results))


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
