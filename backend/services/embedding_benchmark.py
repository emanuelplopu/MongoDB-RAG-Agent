"""Embedding Benchmark Service.

Provides functionality to benchmark and compare embedding providers:
- OpenAI (cloud API)
- Ollama (local)
- vLLM/OpenAI-compatible (remote/VPN)

Collects metrics: time, tokens, memory, CPU, latency, dimensions.
"""

import logging
import asyncio
import time
import tracemalloc
import base64
import tempfile
import os
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field

import psutil
import httpx
import openai

logger = logging.getLogger(__name__)


# Cost per 1M tokens for OpenAI embedding models (USD)
OPENAI_EMBEDDING_COSTS = {
    "text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
    "text-embedding-ada-002": 0.10,
}

# Known embedding model dimensions
EMBEDDING_DIMENSIONS = {
    # OpenAI
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    # Ollama
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "snowflake-arctic-embed": 1024,
    "bge-large": 1024,
    "bge-base": 768,
    "bge-small": 384,
}


@dataclass
class BenchmarkProviderConfig:
    """Configuration for an embedding provider to benchmark."""
    provider_type: str  # "openai", "ollama", "vllm", "custom"
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    name: Optional[str] = None  # Display name
    
    def __post_init__(self):
        if not self.name:
            self.name = f"{self.provider_type}/{self.model}"


@dataclass
class BenchmarkMetrics:
    """Metrics collected during benchmark."""
    provider: str
    model: str
    provider_type: str
    # Timing
    total_time_ms: float = 0.0
    chunking_time_ms: float = 0.0
    embedding_time_ms: float = 0.0
    avg_latency_ms: float = 0.0
    # Counts
    tokens_processed: int = 0
    chunks_created: int = 0
    embedding_dimension: int = 0
    # Resources
    memory_before_mb: float = 0.0
    memory_after_mb: float = 0.0
    memory_peak_mb: float = 0.0
    cpu_percent: float = 0.0
    # Cost (API providers only)
    cost_estimate_usd: Optional[float] = None
    # Status
    success: bool = True
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "provider_type": self.provider_type,
            "total_time_ms": round(self.total_time_ms, 2),
            "chunking_time_ms": round(self.chunking_time_ms, 2),
            "embedding_time_ms": round(self.embedding_time_ms, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "tokens_processed": self.tokens_processed,
            "chunks_created": self.chunks_created,
            "embedding_dimension": self.embedding_dimension,
            "memory_before_mb": round(self.memory_before_mb, 2),
            "memory_after_mb": round(self.memory_after_mb, 2),
            "memory_peak_mb": round(self.memory_peak_mb, 2),
            "cpu_percent": round(self.cpu_percent, 2),
            "cost_estimate_usd": round(self.cost_estimate_usd, 6) if self.cost_estimate_usd else None,
            "success": self.success,
            "error": self.error,
        }


@dataclass
class BenchmarkResult:
    """Complete benchmark result."""
    id: str
    timestamp: datetime
    file_name: str
    file_size_bytes: int
    content_preview: str  # First 500 chars
    chunk_config: Dict[str, Any]
    results: List[BenchmarkMetrics]
    winner: Optional[str] = None  # Best performing provider
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "file_name": self.file_name,
            "file_size_bytes": self.file_size_bytes,
            "content_preview": self.content_preview,
            "chunk_config": self.chunk_config,
            "results": [r.to_dict() for r in self.results],
            "winner": self.winner,
        }


class EmbeddingBenchmarkService:
    """Service for benchmarking embedding providers."""
    
    def __init__(self, db=None):
        """Initialize benchmark service.
        
        Args:
            db: Database manager for storing results
        """
        self.db = db
        self._chunker = None
        self._process = psutil.Process()
    
    async def run_benchmark(
        self,
        file_content: bytes,
        file_name: str,
        providers: List[BenchmarkProviderConfig],
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        max_tokens: int = 512,
    ) -> BenchmarkResult:
        """
        Run benchmark on a file with multiple embedding providers.
        
        Args:
            file_content: File content as bytes
            file_name: Name of the file
            providers: List of provider configurations (max 3)
            chunk_size: Target chunk size in characters
            chunk_overlap: Overlap between chunks
            max_tokens: Maximum tokens per chunk
            
        Returns:
            BenchmarkResult with metrics for all providers
        """
        import uuid
        
        if len(providers) > 3:
            raise ValueError("Maximum 3 providers allowed for comparison")
        
        if len(providers) < 1:
            raise ValueError("At least 1 provider required")
        
        benchmark_id = str(uuid.uuid4())
        file_size = len(file_content)
        
        # Parse and chunk the document (common for all providers)
        logger.info(f"Starting benchmark {benchmark_id} for {file_name}")
        
        # Convert file to text content
        text_content, content_preview = await self._extract_text_content(
            file_content, file_name
        )
        
        # Chunk the document
        chunk_start = time.perf_counter()
        chunks = await self._chunk_document(
            text_content, 
            file_name,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            max_tokens=max_tokens
        )
        chunking_time_ms = (time.perf_counter() - chunk_start) * 1000
        
        logger.info(f"Created {len(chunks)} chunks in {chunking_time_ms:.2f}ms")
        
        # Calculate total tokens
        total_tokens = sum(c.get("token_count", len(c["content"]) // 4) for c in chunks)
        
        # Run benchmark for each provider
        results = []
        for provider in providers:
            logger.info(f"Benchmarking {provider.provider_type}/{provider.model}...")
            
            metrics = await self._benchmark_provider(
                provider=provider,
                chunks=chunks,
                total_tokens=total_tokens,
                chunking_time_ms=chunking_time_ms,
            )
            results.append(metrics)
        
        # Determine winner (fastest successful embedding time)
        successful_results = [r for r in results if r.success]
        winner = None
        if successful_results:
            winner_result = min(successful_results, key=lambda r: r.embedding_time_ms)
            winner = winner_result.provider
        
        # Create result object
        chunk_config = {
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "max_tokens": max_tokens,
        }
        
        result = BenchmarkResult(
            id=benchmark_id,
            timestamp=datetime.utcnow(),
            file_name=file_name,
            file_size_bytes=file_size,
            content_preview=content_preview[:500],
            chunk_config=chunk_config,
            results=results,
            winner=winner,
        )
        
        # Store in database
        if self.db:
            await self._store_result(result)
        
        logger.info(f"Benchmark {benchmark_id} complete. Winner: {winner}")
        return result
    
    async def _extract_text_content(
        self, 
        file_content: bytes, 
        file_name: str
    ) -> Tuple[str, str]:
        """Extract text content from file."""
        file_ext = os.path.splitext(file_name)[1].lower()
        
        # For simple text files, decode directly
        if file_ext in [".txt", ".md", ".markdown"]:
            try:
                text = file_content.decode("utf-8")
            except UnicodeDecodeError:
                text = file_content.decode("latin-1")
            return text, text[:500]
        
        # For other formats, try to use docling if available
        try:
            from docling.document_converter import DocumentConverter
            
            # Write to temp file for docling
            with tempfile.NamedTemporaryFile(
                delete=False, 
                suffix=file_ext
            ) as tmp:
                tmp.write(file_content)
                tmp_path = tmp.name
            
            try:
                converter = DocumentConverter()
                result = converter.convert(tmp_path)
                text = result.document.export_to_markdown()
                return text, text[:500]
            finally:
                os.unlink(tmp_path)
                
        except ImportError:
            logger.warning("Docling not available, treating as plain text")
            try:
                text = file_content.decode("utf-8")
            except UnicodeDecodeError:
                text = file_content.decode("latin-1")
            return text, text[:500]
        except Exception as e:
            logger.error(f"Error extracting content: {e}")
            # Fallback to plain text
            try:
                text = file_content.decode("utf-8")
            except UnicodeDecodeError:
                text = file_content.decode("latin-1")
            return text, text[:500]
    
    async def _chunk_document(
        self,
        content: str,
        title: str,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        max_tokens: int = 512,
    ) -> List[Dict[str, Any]]:
        """Chunk document using simple sliding window approach."""
        chunks = []
        start = 0
        chunk_index = 0
        min_chunk_size = 100
        
        while start < len(content):
            end = start + chunk_size
            
            if end >= len(content):
                chunk_text = content[start:]
            else:
                # Try to end at sentence boundary
                chunk_end = end
                for i in range(end, max(start + min_chunk_size, end - 200), -1):
                    if i < len(content) and content[i] in '.!?\n':
                        chunk_end = i + 1
                        break
                chunk_text = content[start:chunk_end]
                end = chunk_end
            
            if chunk_text.strip():
                # Estimate token count (~4 chars per token)
                token_count = len(chunk_text) // 4
                
                chunks.append({
                    "content": chunk_text.strip(),
                    "index": chunk_index,
                    "token_count": token_count,
                    "start_char": start,
                    "end_char": end,
                })
                chunk_index += 1
            
            start = end - chunk_overlap
        
        return chunks
    
    async def _benchmark_provider(
        self,
        provider: BenchmarkProviderConfig,
        chunks: List[Dict[str, Any]],
        total_tokens: int,
        chunking_time_ms: float,
    ) -> BenchmarkMetrics:
        """Benchmark a single embedding provider."""
        metrics = BenchmarkMetrics(
            provider=provider.name or f"{provider.provider_type}/{provider.model}",
            model=provider.model,
            provider_type=provider.provider_type,
            chunking_time_ms=chunking_time_ms,
            tokens_processed=total_tokens,
            chunks_created=len(chunks),
        )
        
        try:
            # Record baseline memory
            metrics.memory_before_mb = self._process.memory_info().rss / 1024 / 1024
            
            # Start memory tracking
            tracemalloc.start()
            
            # Record CPU before
            cpu_start = psutil.cpu_percent(interval=None)
            
            # Start timing
            start_time = time.perf_counter()
            
            # Generate embeddings
            texts = [c["content"] for c in chunks]
            embeddings, latencies = await self._generate_embeddings(
                provider, texts
            )
            
            # Calculate timing
            embedding_time_ms = (time.perf_counter() - start_time) * 1000
            
            # Get memory stats
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            
            metrics.memory_after_mb = self._process.memory_info().rss / 1024 / 1024
            metrics.memory_peak_mb = peak / 1024 / 1024
            
            # CPU measurement
            cpu_end = psutil.cpu_percent(interval=None)
            metrics.cpu_percent = (cpu_start + cpu_end) / 2
            
            # Fill metrics
            metrics.embedding_time_ms = embedding_time_ms
            metrics.total_time_ms = chunking_time_ms + embedding_time_ms
            
            if latencies:
                metrics.avg_latency_ms = sum(latencies) / len(latencies)
            
            if embeddings and len(embeddings) > 0:
                metrics.embedding_dimension = len(embeddings[0])
            else:
                metrics.embedding_dimension = EMBEDDING_DIMENSIONS.get(
                    provider.model, 0
                )
            
            # Calculate cost for OpenAI
            if provider.provider_type == "openai" and provider.model in OPENAI_EMBEDDING_COSTS:
                cost_per_million = OPENAI_EMBEDDING_COSTS[provider.model]
                metrics.cost_estimate_usd = (total_tokens / 1_000_000) * cost_per_million
            
            metrics.success = True
            
        except Exception as e:
            logger.error(f"Benchmark failed for {provider.name}: {e}")
            metrics.success = False
            metrics.error = str(e)
            
            # Stop tracemalloc if still running
            if tracemalloc.is_tracing():
                tracemalloc.stop()
        
        return metrics
    
    async def _generate_embeddings(
        self,
        provider: BenchmarkProviderConfig,
        texts: List[str],
        batch_size: int = 50,
    ) -> Tuple[List[List[float]], List[float]]:
        """Generate embeddings using the specified provider."""
        embeddings = []
        latencies = []
        
        if provider.provider_type == "openai":
            embeddings, latencies = await self._generate_openai_embeddings(
                texts, provider.model, provider.api_key, provider.base_url, batch_size
            )
        elif provider.provider_type == "ollama":
            embeddings, latencies = await self._generate_ollama_embeddings(
                texts, provider.model, provider.base_url or "http://localhost:11434"
            )
        elif provider.provider_type in ["vllm", "custom"]:
            embeddings, latencies = await self._generate_openai_compatible_embeddings(
                texts, provider.model, provider.base_url, provider.api_key, batch_size
            )
        else:
            raise ValueError(f"Unknown provider type: {provider.provider_type}")
        
        return embeddings, latencies
    
    async def _generate_openai_embeddings(
        self,
        texts: List[str],
        model: str,
        api_key: Optional[str],
        base_url: Optional[str],
        batch_size: int,
    ) -> Tuple[List[List[float]], List[float]]:
        """Generate embeddings using OpenAI API."""
        from backend.core.config import settings
        
        client = openai.AsyncOpenAI(
            api_key=api_key or settings.embedding_api_key,
            base_url=base_url or settings.embedding_base_url or "https://api.openai.com/v1",
        )
        
        embeddings = []
        latencies = []
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            
            start = time.perf_counter()
            response = await client.embeddings.create(
                model=model,
                input=batch,
            )
            latency = (time.perf_counter() - start) * 1000
            latencies.append(latency)
            
            for item in response.data:
                embeddings.append(item.embedding)
        
        return embeddings, latencies
    
    async def _generate_ollama_embeddings(
        self,
        texts: List[str],
        model: str,
        base_url: str,
    ) -> Tuple[List[List[float]], List[float]]:
        """Generate embeddings using Ollama."""
        embeddings = []
        latencies = []
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            for text in texts:
                start = time.perf_counter()
                response = await client.post(
                    f"{base_url}/api/embeddings",
                    json={"model": model, "prompt": text},
                )
                latency = (time.perf_counter() - start) * 1000
                latencies.append(latency)
                
                response.raise_for_status()
                data = response.json()
                embeddings.append(data["embedding"])
        
        return embeddings, latencies
    
    async def _generate_openai_compatible_embeddings(
        self,
        texts: List[str],
        model: str,
        base_url: str,
        api_key: Optional[str],
        batch_size: int,
    ) -> Tuple[List[List[float]], List[float]]:
        """Generate embeddings using OpenAI-compatible API (vLLM, etc.)."""
        client = openai.AsyncOpenAI(
            api_key=api_key or "dummy",  # Some local servers don't need API key
            base_url=base_url,
        )
        
        embeddings = []
        latencies = []
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            
            start = time.perf_counter()
            response = await client.embeddings.create(
                model=model,
                input=batch,
            )
            latency = (time.perf_counter() - start) * 1000
            latencies.append(latency)
            
            for item in response.data:
                embeddings.append(item.embedding)
        
        return embeddings, latencies
    
    async def test_provider(
        self,
        provider: BenchmarkProviderConfig,
    ) -> Dict[str, Any]:
        """Test if a provider is accessible and working."""
        result = {
            "success": False,
            "provider": provider.name or f"{provider.provider_type}/{provider.model}",
            "model": provider.model,
            "latency_ms": 0,
            "dimension": 0,
            "error": None,
        }
        
        try:
            test_text = "This is a test sentence for embedding generation."
            
            start = time.perf_counter()
            embeddings, _ = await self._generate_embeddings(provider, [test_text])
            latency = (time.perf_counter() - start) * 1000
            
            if embeddings and len(embeddings) > 0:
                result["success"] = True
                result["latency_ms"] = round(latency, 2)
                result["dimension"] = len(embeddings[0])
            else:
                result["error"] = "No embeddings returned"
                
        except Exception as e:
            result["error"] = str(e)
        
        return result
    
    async def _store_result(self, result: BenchmarkResult) -> None:
        """Store benchmark result in MongoDB."""
        try:
            collection = self.db.db["benchmark_results"]
            doc = result.to_dict()
            doc["_id"] = result.id
            await collection.insert_one(doc)
            logger.info(f"Stored benchmark result {result.id}")
        except Exception as e:
            logger.error(f"Failed to store benchmark result: {e}")
    
    async def get_results(
        self,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Get historical benchmark results."""
        if not self.db:
            return []
        
        try:
            collection = self.db.db["benchmark_results"]
            cursor = collection.find().sort("timestamp", -1).limit(limit)
            
            results = []
            async for doc in cursor:
                doc["id"] = doc.pop("_id")
                results.append(doc)
            
            return results
        except Exception as e:
            logger.error(f"Failed to get benchmark results: {e}")
            return []
    
    async def get_available_providers(self) -> Dict[str, Any]:
        """Get available embedding providers from discovery."""
        from backend.core.config import settings
        
        providers = {
            "openai": {
                "name": "OpenAI",
                "available": bool(settings.embedding_api_key),
                "models": [
                    {"id": "text-embedding-3-small", "name": "text-embedding-3-small", "dimension": 1536},
                    {"id": "text-embedding-3-large", "name": "text-embedding-3-large", "dimension": 3072},
                    {"id": "text-embedding-ada-002", "name": "text-embedding-ada-002", "dimension": 1536},
                ],
            },
            "ollama": {
                "name": "Ollama (Local)",
                "available": False,
                "url": None,
                "models": [],
            },
            "vllm": {
                "name": "vLLM / OpenAI-compatible",
                "available": False,
                "models": [],
            },
        }
        
        # Check Ollama
        ollama_urls = [
            "http://localhost:11434",
            "http://host.docker.internal:11434",
            "http://ollama:11434",
        ]
        
        for url in ollama_urls:
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    response = await client.get(f"{url}/api/tags")
                    if response.status_code == 200:
                        providers["ollama"]["available"] = True
                        providers["ollama"]["url"] = url
                        
                        # Get embedding models
                        data = response.json()
                        embedding_patterns = [
                            "embed", "nomic", "bge", "minilm", "e5", "arctic"
                        ]
                        
                        for model in data.get("models", []):
                            name = model.get("name", "")
                            if any(p in name.lower() for p in embedding_patterns):
                                dim = EMBEDDING_DIMENSIONS.get(
                                    name.split(":")[0], 768
                                )
                                providers["ollama"]["models"].append({
                                    "id": name,
                                    "name": name,
                                    "dimension": dim,
                                })
                        break
            except Exception:
                continue
        
        # Get custom endpoints from DB if available
        if self.db:
            try:
                collection = self.db.db["custom_llm_endpoints"]
                cursor = collection.find({"enabled": True})
                
                async for endpoint in cursor:
                    if endpoint.get("provider_type") in ["vllm", "openai-compatible"]:
                        providers["vllm"]["available"] = True
                        # Note: Would need to query each endpoint for models
            except Exception:
                pass
        
        return providers
