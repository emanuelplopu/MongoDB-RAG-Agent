"""Embedding Provider Clients for RecallHub.

Provides unified interfaces for multiple embedding providers:
- OpenAI (text-embedding-3-small/large)
- Google Gemini Embedding 2 (gemini-embedding-2-preview, gemini-embedding-001)
- Voyage AI (voyage-4-large, voyage-4, voyage-4-lite, voyage-code-3)
- Ollama (local models)

Each provider implements task-type optimization for improved retrieval quality.
"""

import logging
import asyncio
import time
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

import httpx
import openai

logger = logging.getLogger(__name__)


class EmbeddingTaskType(str, Enum):
    """Task types for embedding optimization."""
    # General purpose
    RETRIEVAL_QUERY = "retrieval_query"
    RETRIEVAL_DOCUMENT = "retrieval_document"
    SEMANTIC_SIMILARITY = "semantic_similarity"
    CLASSIFICATION = "classification"
    CLUSTERING = "clustering"
    # Specialized
    QUESTION_ANSWERING = "question_answering"
    FACT_VERIFICATION = "fact_verification"
    CODE_RETRIEVAL_QUERY = "code_retrieval_query"
    # Default (no task type specified)
    DEFAULT = "default"


@dataclass
class EmbeddingProviderConfig:
    """Configuration for an embedding provider."""
    provider_type: str  # openai, google, voyageai, ollama
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    output_dimension: Optional[int] = None  # For models with adjustable dimensions
    task_type: EmbeddingTaskType = EmbeddingTaskType.DEFAULT


# Model specifications and costs
EMBEDDING_MODEL_SPECS = {
    # OpenAI Models
    "text-embedding-3-small": {
        "provider": "openai",
        "dimensions": 1536,
        "max_tokens": 8191,
        "cost_per_million": 0.02,
        "supports_task_type": False,
    },
    "text-embedding-3-large": {
        "provider": "openai",
        "dimensions": 3072,
        "max_tokens": 8191,
        "cost_per_million": 0.13,
        "supports_task_type": False,
    },
    "text-embedding-ada-002": {
        "provider": "openai",
        "dimensions": 1536,
        "max_tokens": 8191,
        "cost_per_million": 0.10,
        "supports_task_type": False,
    },
    # Google Gemini Embedding Models
    "gemini-embedding-2-preview": {
        "provider": "google",
        "dimensions": 3072,
        "max_tokens": 8192,
        "cost_per_million": 0.00,  # Preview is free
        "supports_task_type": True,
        "supports_multimodal": True,
        "adjustable_dimensions": [256, 512, 768, 1024, 1536, 2048, 3072],
    },
    "gemini-embedding-001": {
        "provider": "google",
        "dimensions": 3072,
        "max_tokens": 2048,
        "cost_per_million": 0.00025,
        "supports_task_type": True,
        "adjustable_dimensions": [256, 512, 768, 1024, 1536, 2048, 3072],
    },
    "text-embedding-004": {
        "provider": "google",
        "dimensions": 768,
        "max_tokens": 2048,
        "cost_per_million": 0.00025,
        "supports_task_type": True,
    },
    # Voyage AI Models
    "voyage-4-large": {
        "provider": "voyageai",
        "dimensions": 1024,
        "max_tokens": 32000,
        "cost_per_million": 0.12,
        "supports_task_type": True,
        "adjustable_dimensions": [256, 512, 1024, 2048],
    },
    "voyage-4": {
        "provider": "voyageai",
        "dimensions": 1024,
        "max_tokens": 32000,
        "cost_per_million": 0.06,
        "supports_task_type": True,
        "adjustable_dimensions": [256, 512, 1024, 2048],
    },
    "voyage-4-lite": {
        "provider": "voyageai",
        "dimensions": 1024,
        "max_tokens": 32000,
        "cost_per_million": 0.02,
        "supports_task_type": True,
        "adjustable_dimensions": [256, 512, 1024, 2048],
    },
    "voyage-code-3": {
        "provider": "voyageai",
        "dimensions": 1024,
        "max_tokens": 32000,
        "cost_per_million": 0.12,
        "supports_task_type": True,
        "adjustable_dimensions": [256, 512, 1024, 2048],
    },
    "voyage-3-large": {
        "provider": "voyageai",
        "dimensions": 1024,
        "max_tokens": 32000,
        "cost_per_million": 0.12,
        "supports_task_type": True,
        "adjustable_dimensions": [256, 512, 1024, 2048],
    },
    # Ollama Models (Local)
    "nomic-embed-text": {
        "provider": "ollama",
        "dimensions": 768,
        "max_tokens": 8192,
        "cost_per_million": 0.0,
        "supports_task_type": False,
    },
    "mxbai-embed-large": {
        "provider": "ollama",
        "dimensions": 1024,
        "max_tokens": 512,
        "cost_per_million": 0.0,
        "supports_task_type": False,
    },
    "all-minilm": {
        "provider": "ollama",
        "dimensions": 384,
        "max_tokens": 256,
        "cost_per_million": 0.0,
        "supports_task_type": False,
    },
    "snowflake-arctic-embed": {
        "provider": "ollama",
        "dimensions": 1024,
        "max_tokens": 512,
        "cost_per_million": 0.0,
        "supports_task_type": False,
    },
    "bge-large": {
        "provider": "ollama",
        "dimensions": 1024,
        "max_tokens": 512,
        "cost_per_million": 0.0,
        "supports_task_type": False,
    },
    "bge-m3": {
        "provider": "ollama",
        "dimensions": 1024,
        "max_tokens": 8192,
        "cost_per_million": 0.0,
        "supports_task_type": False,
    },
}


def get_model_spec(model: str) -> Dict[str, Any]:
    """Get specifications for a model, with fallback defaults."""
    return EMBEDDING_MODEL_SPECS.get(model, {
        "provider": "unknown",
        "dimensions": 1536,
        "max_tokens": 8191,
        "cost_per_million": 0.0,
        "supports_task_type": False,
    })


class BaseEmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""
    
    def __init__(self, config: EmbeddingProviderConfig):
        self.config = config
        self.model_spec = get_model_spec(config.model)
    
    @abstractmethod
    async def generate_embeddings(
        self,
        texts: List[str],
        task_type: Optional[EmbeddingTaskType] = None,
    ) -> Tuple[List[List[float]], Dict[str, Any]]:
        """Generate embeddings for a list of texts.
        
        Returns:
            Tuple of (embeddings, metadata) where metadata includes timing, token count, etc.
        """
        pass
    
    @abstractmethod
    async def test_connection(self) -> Dict[str, Any]:
        """Test provider connection and return status."""
        pass
    
    def get_dimension(self) -> int:
        """Get the output dimension for this provider/model."""
        if self.config.output_dimension:
            return self.config.output_dimension
        return self.model_spec.get("dimensions", 1536)
    
    def get_max_tokens(self) -> int:
        """Get max tokens for this model."""
        return self.model_spec.get("max_tokens", 8191)
    
    def estimate_cost(self, token_count: int) -> float:
        """Estimate cost in USD for the given token count."""
        cost_per_million = self.model_spec.get("cost_per_million", 0.0)
        return (token_count / 1_000_000) * cost_per_million


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    """OpenAI embedding provider (text-embedding-3-small/large)."""
    
    def __init__(self, config: EmbeddingProviderConfig):
        super().__init__(config)
        self.client = openai.AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url or "https://api.openai.com/v1",
        )
    
    async def generate_embeddings(
        self,
        texts: List[str],
        task_type: Optional[EmbeddingTaskType] = None,
    ) -> Tuple[List[List[float]], Dict[str, Any]]:
        """Generate embeddings using OpenAI API."""
        start_time = time.perf_counter()
        
        # OpenAI doesn't support task types, but we can keep the interface consistent
        response = await self.client.embeddings.create(
            model=self.config.model,
            input=texts,
        )
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        embeddings = [item.embedding for item in response.data]
        
        metadata = {
            "latency_ms": elapsed_ms,
            "token_count": response.usage.total_tokens if response.usage else len(texts) * 100,
            "model": self.config.model,
            "provider": "openai",
            "dimension": len(embeddings[0]) if embeddings else self.get_dimension(),
        }
        
        return embeddings, metadata
    
    async def test_connection(self) -> Dict[str, Any]:
        """Test OpenAI connection."""
        try:
            embeddings, metadata = await self.generate_embeddings(["Test connection"])
            return {
                "success": True,
                "latency_ms": metadata["latency_ms"],
                "dimension": metadata["dimension"],
                "model": self.config.model,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    """Google Gemini Embedding provider (gemini-embedding-2-preview, gemini-embedding-001)."""
    
    # Task type mapping for Gemini API
    TASK_TYPE_MAP = {
        EmbeddingTaskType.RETRIEVAL_QUERY: "RETRIEVAL_QUERY",
        EmbeddingTaskType.RETRIEVAL_DOCUMENT: "RETRIEVAL_DOCUMENT",
        EmbeddingTaskType.SEMANTIC_SIMILARITY: "SEMANTIC_SIMILARITY",
        EmbeddingTaskType.CLASSIFICATION: "CLASSIFICATION",
        EmbeddingTaskType.CLUSTERING: "CLUSTERING",
        EmbeddingTaskType.QUESTION_ANSWERING: "QUESTION_ANSWERING",
        EmbeddingTaskType.FACT_VERIFICATION: "FACT_VERIFICATION",
        EmbeddingTaskType.CODE_RETRIEVAL_QUERY: "CODE_RETRIEVAL_QUERY",
        EmbeddingTaskType.DEFAULT: None,
    }
    
    # Gemini API batch limit
    MAX_BATCH_SIZE = 100
    
    def __init__(self, config: EmbeddingProviderConfig):
        super().__init__(config)
        self.api_key = config.api_key
        if not self.api_key or self.api_key.strip() == "":
            raise ValueError("Google API key is required for Gemini provider")
        # Gemini API endpoint
        self.base_url = config.base_url or "https://generativelanguage.googleapis.com/v1beta"
    
    async def generate_embeddings(
        self,
        texts: List[str],
        task_type: Optional[EmbeddingTaskType] = None,
    ) -> Tuple[List[List[float]], Dict[str, Any]]:
        """Generate embeddings using Gemini API."""
        if not texts:
            raise ValueError("Cannot generate embeddings for empty text list")
        
        start_time = time.perf_counter()
        
        embeddings = []
        total_tokens = 0
        
        # Gemini embedding API endpoint
        url = f"{self.base_url}/models/{self.config.model}:embedContent"
        
        # Build request payload
        task = self.TASK_TYPE_MAP.get(task_type or self.config.task_type)
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                for text in texts:
                    # Skip empty texts
                    if not text or not text.strip():
                        logger.warning("Skipping empty text in Gemini embedding")
                        embeddings.append([])
                        continue
                    
                    payload = {
                        "model": f"models/{self.config.model}",
                        "content": {
                            "parts": [{"text": text}]
                        }
                    }
                    
                    # Add task type if supported and specified
                    if task:
                        payload["taskType"] = task
                    
                    # Add output dimensionality if specified
                    if self.config.output_dimension:
                        payload["outputDimensionality"] = self.config.output_dimension
                    
                    response = await client.post(
                        url,
                        headers={
                            "Content-Type": "application/json",
                            "x-goog-api-key": self.api_key,
                        },
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    # Extract embedding from response
                    embedding_data = data.get("embedding", {})
                    embedding_values = embedding_data.get("values", [])
                    
                    if not embedding_values:
                        raise ValueError(f"Gemini returned empty embedding")
                    
                    embeddings.append(embedding_values)
                    
                    # Track tokens (Gemini returns statistics)
                    stats = embedding_data.get("statistics", {})
                    total_tokens += stats.get("token_count", len(text) // 4)
        except httpx.TimeoutException as e:
            raise TimeoutError(f"Gemini API timeout: {e}")
        except httpx.ConnectError as e:
            raise ConnectionError(f"Failed to connect to Gemini API: {e}")
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        
        metadata = {
            "latency_ms": elapsed_ms,
            "token_count": total_tokens,
            "model": self.config.model,
            "provider": "google",
            "dimension": len(embeddings[0]) if embeddings and embeddings[0] else self.get_dimension(),
            "task_type": task,
        }
        
        return embeddings, metadata
    
    async def generate_embeddings_batch(
        self,
        texts: List[str],
        task_type: Optional[EmbeddingTaskType] = None,
    ) -> Tuple[List[List[float]], Dict[str, Any]]:
        """Generate embeddings in batch (more efficient for multiple texts)."""
        start_time = time.perf_counter()
        
        # Gemini batch embedding endpoint
        url = f"{self.base_url}/models/{self.config.model}:batchEmbedContents"
        
        task = self.TASK_TYPE_MAP.get(task_type or self.config.task_type)
        
        # Build batch request
        requests = []
        for text in texts:
            req = {
                "model": f"models/{self.config.model}",
                "content": {
                    "parts": [{"text": text}]
                }
            }
            if task:
                req["taskType"] = task
            if self.config.output_dimension:
                req["outputDimensionality"] = self.config.output_dimension
            requests.append(req)
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.api_key,
                },
                json={"requests": requests},
            )
            response.raise_for_status()
            data = response.json()
        
        # Extract embeddings
        embeddings = []
        total_tokens = 0
        for emb_data in data.get("embeddings", []):
            embeddings.append(emb_data.get("values", []))
            stats = emb_data.get("statistics", {})
            total_tokens += stats.get("token_count", 0)
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        
        metadata = {
            "latency_ms": elapsed_ms,
            "token_count": total_tokens,
            "model": self.config.model,
            "provider": "google",
            "dimension": len(embeddings[0]) if embeddings else self.get_dimension(),
            "task_type": task,
        }
        
        return embeddings, metadata
    
    async def test_connection(self) -> Dict[str, Any]:
        """Test Gemini connection."""
        try:
            embeddings, metadata = await self.generate_embeddings(["Test connection"])
            return {
                "success": True,
                "latency_ms": metadata["latency_ms"],
                "dimension": metadata["dimension"],
                "model": self.config.model,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


class VoyageAIEmbeddingProvider(BaseEmbeddingProvider):
    """Voyage AI embedding provider (voyage-4-large, voyage-4, voyage-4-lite)."""
    
    # Input type mapping for Voyage AI
    INPUT_TYPE_MAP = {
        EmbeddingTaskType.RETRIEVAL_QUERY: "query",
        EmbeddingTaskType.RETRIEVAL_DOCUMENT: "document",
        EmbeddingTaskType.QUESTION_ANSWERING: "query",
        EmbeddingTaskType.CODE_RETRIEVAL_QUERY: "query",
        EmbeddingTaskType.DEFAULT: None,
    }
    
    # Voyage AI batch limit
    MAX_BATCH_SIZE = 128
    
    def __init__(self, config: EmbeddingProviderConfig):
        super().__init__(config)
        self.api_key = config.api_key
        if not self.api_key or self.api_key.strip() == "":
            raise ValueError("Voyage AI API key is required for VoyageAI provider")
        self.base_url = config.base_url or "https://api.voyageai.com/v1"
    
    async def generate_embeddings(
        self,
        texts: List[str],
        task_type: Optional[EmbeddingTaskType] = None,
    ) -> Tuple[List[List[float]], Dict[str, Any]]:
        """Generate embeddings using Voyage AI API."""
        if not texts:
            raise ValueError("Cannot generate embeddings for empty text list")
        
        start_time = time.perf_counter()
        
        # Voyage AI embedding endpoint
        url = f"{self.base_url}/embeddings"
        
        # Build request payload
        payload = {
            "model": self.config.model,
            "input": texts,
        }
        
        # Add input_type for retrieval optimization
        input_type = self.INPUT_TYPE_MAP.get(task_type or self.config.task_type)
        if input_type:
            payload["input_type"] = input_type
        
        # Add output dimension if specified and supported
        if self.config.output_dimension:
            payload["output_dimension"] = self.config.output_dimension
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as e:
            raise TimeoutError(f"Voyage AI API timeout: {e}")
        except httpx.ConnectError as e:
            raise ConnectionError(f"Failed to connect to Voyage AI API: {e}")
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        
        # Extract embeddings from response
        embeddings = [item["embedding"] for item in data.get("data", [])]
        
        # Validate response
        if len(embeddings) != len(texts):
            raise ValueError(f"Voyage AI returned {len(embeddings)} embeddings for {len(texts)} texts")
        
        # Get usage info
        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", sum(len(t) // 4 for t in texts))
        
        metadata = {
            "latency_ms": elapsed_ms,
            "token_count": total_tokens,
            "model": self.config.model,
            "provider": "voyageai",
            "dimension": len(embeddings[0]) if embeddings else self.get_dimension(),
            "input_type": input_type,
        }
        
        return embeddings, metadata
    
    async def test_connection(self) -> Dict[str, Any]:
        """Test Voyage AI connection."""
        try:
            embeddings, metadata = await self.generate_embeddings(["Test connection"])
            return {
                "success": True,
                "latency_ms": metadata["latency_ms"],
                "dimension": metadata["dimension"],
                "model": self.config.model,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


class OllamaEmbeddingProvider(BaseEmbeddingProvider):
    """Ollama local embedding provider."""
    
    def __init__(self, config: EmbeddingProviderConfig):
        super().__init__(config)
        self.base_url = config.base_url or "http://host.docker.internal:11434"
    
    async def generate_embeddings(
        self,
        texts: List[str],
        task_type: Optional[EmbeddingTaskType] = None,
    ) -> Tuple[List[List[float]], Dict[str, Any]]:
        """Generate embeddings using Ollama API."""
        start_time = time.perf_counter()
        
        embeddings = []
        total_tokens = 0
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            for text in texts:
                response = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.config.model, "prompt": text},
                )
                response.raise_for_status()
                data = response.json()
                embeddings.append(data["embedding"])
                total_tokens += len(text) // 4  # Estimate
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        
        metadata = {
            "latency_ms": elapsed_ms,
            "token_count": total_tokens,
            "model": self.config.model,
            "provider": "ollama",
            "dimension": len(embeddings[0]) if embeddings else self.get_dimension(),
        }
        
        return embeddings, metadata
    
    async def test_connection(self) -> Dict[str, Any]:
        """Test Ollama connection."""
        try:
            # First check if Ollama is running
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                if response.status_code != 200:
                    return {"success": False, "error": "Ollama not responding"}
                
                # Check if model exists
                data = response.json()
                models = [m.get("name", "").split(":")[0] for m in data.get("models", [])]
                model_base = self.config.model.split(":")[0]
                
                if model_base not in models:
                    return {
                        "success": False,
                        "error": f"Model '{self.config.model}' not found. Available: {models}"
                    }
            
            # Test actual embedding generation
            embeddings, metadata = await self.generate_embeddings(["Test connection"])
            return {
                "success": True,
                "latency_ms": metadata["latency_ms"],
                "dimension": metadata["dimension"],
                "model": self.config.model,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


def create_embedding_provider(config: EmbeddingProviderConfig) -> BaseEmbeddingProvider:
    """Factory function to create the appropriate embedding provider.
    
    Args:
        config: Provider configuration
        
    Returns:
        Configured embedding provider instance
        
    Raises:
        ValueError: If provider type is unknown
    """
    provider_type = config.provider_type.lower()
    
    if provider_type == "openai":
        return OpenAIEmbeddingProvider(config)
    elif provider_type in ["google", "gemini"]:
        return GeminiEmbeddingProvider(config)
    elif provider_type in ["voyageai", "voyage"]:
        return VoyageAIEmbeddingProvider(config)
    elif provider_type == "ollama":
        return OllamaEmbeddingProvider(config)
    else:
        raise ValueError(f"Unknown embedding provider type: {provider_type}")


async def test_provider_connectivity(
    provider_type: str,
    model: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Test connectivity to an embedding provider.
    
    Args:
        provider_type: Type of provider (openai, google, voyageai, ollama)
        model: Model name
        api_key: API key (if required)
        base_url: Custom base URL (optional)
        
    Returns:
        Connection test result with success status, latency, and dimension
    """
    config = EmbeddingProviderConfig(
        provider_type=provider_type,
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
    
    try:
        provider = create_embedding_provider(config)
        return await provider.test_connection()
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_available_models_for_provider(provider_type: str) -> List[Dict[str, Any]]:
    """Get list of available models for a provider type.
    
    Args:
        provider_type: Type of provider
        
    Returns:
        List of model specifications
    """
    provider_type = provider_type.lower()
    if provider_type in ["google", "gemini"]:
        provider_type = "google"
    elif provider_type in ["voyageai", "voyage"]:
        provider_type = "voyageai"
    
    models = []
    for model_name, spec in EMBEDDING_MODEL_SPECS.items():
        if spec.get("provider") == provider_type:
            models.append({
                "id": model_name,
                "name": model_name,
                "dimension": spec.get("dimensions", 1536),
                "max_tokens": spec.get("max_tokens", 8191),
                "cost_per_million": spec.get("cost_per_million", 0.0),
                "supports_task_type": spec.get("supports_task_type", False),
                "adjustable_dimensions": spec.get("adjustable_dimensions"),
            })
    
    return models
