"""Document embedding generation for vector search.

Supports multiple embedding providers with automatic fallback and retry:
- OpenAI (text-embedding-3-small/large)
- Google Gemini Embedding 2 (gemini-embedding-2-preview, gemini-embedding-001)
- Voyage AI (voyage-4-large, voyage-4, voyage-4-lite)
- Ollama (local models)

Includes task-type optimization for improved retrieval quality.
"""

import logging
import asyncio
import os
from typing import List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass

from dotenv import load_dotenv
import openai
import httpx

from src.ingestion.chunker import DocumentChunk
from src.settings import load_settings

# Try to import advanced providers (may not be available in all environments)
try:
    from backend.core.embedding_providers import (
        create_embedding_provider,
        EmbeddingProviderConfig,
        EmbeddingTaskType,
        EMBEDDING_MODEL_SPECS,
        get_model_spec,
    )
    ADVANCED_PROVIDERS_AVAILABLE = True
except ImportError:
    ADVANCED_PROVIDERS_AVAILABLE = False
    EmbeddingTaskType = None

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


# Embedding resilience configuration
@dataclass
class EmbeddingResilienceConfig:
    """Configuration for embedding resilience and fallback behavior."""
    max_retries: int = 3
    initial_retry_delay: float = 1.0  # seconds
    max_retry_delay: float = 30.0  # seconds
    retry_multiplier: float = 2.0  # exponential backoff multiplier
    enable_fallback: bool = True
    fallback_provider: str = "ollama"  # ollama, localai, etc.
    fallback_model: str = "nomic-embed-text"  # default Ollama embedding model
    fallback_url: str = "http://localhost:11434"  # Ollama default
    # Task type for retrieval optimization (query vs document embedding)
    default_task_type: str = "default"  # retrieval_query, retrieval_document, default


# Global resilience config - can be overridden
_resilience_config = EmbeddingResilienceConfig()


def configure_embedding_resilience(
    max_retries: int = None,
    enable_fallback: bool = None,
    fallback_model: str = None,
    fallback_url: str = None
):
    """Configure embedding resilience settings."""
    global _resilience_config
    if max_retries is not None:
        _resilience_config.max_retries = max_retries
    if enable_fallback is not None:
        _resilience_config.enable_fallback = enable_fallback
    if fallback_model is not None:
        _resilience_config.fallback_model = fallback_model
    if fallback_url is not None:
        _resilience_config.fallback_url = fallback_url


def get_embedding_client():
    """Get embedding client based on provider configuration."""
    settings = load_settings(use_profile=False)  # Use base settings for embedding client
    
    return openai.AsyncOpenAI(
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url
    )


# Initialize client lazily
_embedding_client = None


def get_client():
    """Get or create embedding client."""
    global _embedding_client
    if _embedding_client is None:
        _embedding_client = get_embedding_client()
    return _embedding_client


async def _generate_ollama_embeddings(texts: List[str], model: str, base_url: str) -> List[List[float]]:
    """
    Generate embeddings using Ollama as fallback.
    
    Args:
        texts: List of texts to embed
        model: Ollama model name (e.g., 'nomic-embed-text')
        base_url: Ollama API base URL
        
    Returns:
        List of embedding vectors
    """
    embeddings = []
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        for text in texts:
            response = await client.post(
                f"{base_url}/api/embeddings",
                json={"model": model, "prompt": text}
            )
            response.raise_for_status()
            data = response.json()
            embeddings.append(data["embedding"])
    
    return embeddings


async def _check_ollama_available(base_url: str, model: str) -> bool:
    """Check if Ollama is available and has the required model."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Check if Ollama is running
            response = await client.get(f"{base_url}/api/tags")
            if response.status_code != 200:
                return False
            
            # Check if model is available
            data = response.json()
            models = [m.get("name", "").split(":")[0] for m in data.get("models", [])]
            
            if model.split(":")[0] not in models:
                logger.warning(f"Ollama model '{model}' not found. Available: {models}")
                return False
            
            return True
    except Exception as e:
        logger.debug(f"Ollama check failed: {e}")
        return False


class EmbeddingGenerator:
    """Generates embeddings for document chunks.
    
    Supports multiple providers:
    - OpenAI (text-embedding-3-small/large)
    - Google Gemini (gemini-embedding-2-preview, gemini-embedding-001)
    - Voyage AI (voyage-4-large, voyage-4, voyage-4-lite)
    - Ollama (local models)
    """

    def __init__(
        self,
        model: Optional[str] = None,
        batch_size: int = 100,
        task_type: Optional[str] = None
    ):
        """
        Initialize embedding generator.

        Args:
            model: Embedding model to use (defaults to settings)
            batch_size: Number of texts to process in parallel
            task_type: Task type for retrieval optimization (retrieval_query, retrieval_document)
        """
        settings = load_settings(use_profile=False)
        self.model = model or settings.embedding_model
        self.batch_size = batch_size
        self.provider = settings.embedding_provider.lower()
        self.task_type = task_type or _resilience_config.default_task_type
        
        # Store API keys for different providers
        self._api_keys = {
            "openai": settings.embedding_api_key,
            "google": getattr(settings, 'google_api_key', None) or settings.embedding_api_key,
            "voyageai": getattr(settings, 'voyage_api_key', None) or settings.embedding_api_key,
        }
        self._base_url = settings.embedding_base_url

        # Model-specific configurations
        self.model_configs = {
            # OpenAI models
            "text-embedding-3-small": {"dimensions": 1536, "max_tokens": 8191, "provider": "openai"},
            "text-embedding-3-large": {"dimensions": 3072, "max_tokens": 8191, "provider": "openai"},
            "text-embedding-ada-002": {"dimensions": 1536, "max_tokens": 8191, "provider": "openai"},
            # Google Gemini models
            "gemini-embedding-2-preview": {"dimensions": 3072, "max_tokens": 8192, "provider": "google"},
            "gemini-embedding-001": {"dimensions": 3072, "max_tokens": 2048, "provider": "google"},
            "text-embedding-004": {"dimensions": 768, "max_tokens": 2048, "provider": "google"},
            # Voyage AI models
            "voyage-4-large": {"dimensions": 1024, "max_tokens": 32000, "provider": "voyageai"},
            "voyage-4": {"dimensions": 1024, "max_tokens": 32000, "provider": "voyageai"},
            "voyage-4-lite": {"dimensions": 1024, "max_tokens": 32000, "provider": "voyageai"},
            "voyage-code-3": {"dimensions": 1024, "max_tokens": 32000, "provider": "voyageai"},
            "voyage-3-large": {"dimensions": 1024, "max_tokens": 32000, "provider": "voyageai"},
            # Ollama models
            "nomic-embed-text": {"dimensions": 768, "max_tokens": 8192, "provider": "ollama"},
            "mxbai-embed-large": {"dimensions": 1024, "max_tokens": 512, "provider": "ollama"},
            "all-minilm": {"dimensions": 384, "max_tokens": 256, "provider": "ollama"},
            "snowflake-arctic-embed": {"dimensions": 1024, "max_tokens": 512, "provider": "ollama"},
            "bge-large": {"dimensions": 1024, "max_tokens": 512, "provider": "ollama"},
            "bge-m3": {"dimensions": 1024, "max_tokens": 8192, "provider": "ollama"},
        }

        # Use configured dimension or model default
        default_config = {"dimensions": settings.embedding_dimension, "max_tokens": 8191}
        self.config = self.model_configs.get(self.model, default_config)
        
        # Override with settings if specified
        if settings.embedding_dimension:
            self.config["dimensions"] = settings.embedding_dimension
        
        logger.info(f"Embedding generator initialized: model={self.model}, provider={self.provider}, dimensions={self.config['dimensions']}")

    def _sanitize_text(self, text: str) -> str:
        """
        Sanitize and validate text for embedding.
        
        Args:
            text: Input text
            
        Returns:
            Sanitized text
        """
        if not text:
            return ""
        # Strip whitespace and normalize
        text = text.strip()
        # Replace null bytes and other problematic characters
        text = text.replace('\x00', '').replace('\r\n', '\n')
        return text
    
    def _validate_api_key(self, provider: str) -> Tuple[bool, str]:
        """
        Validate that an API key is configured for the provider.
        
        Args:
            provider: Provider name
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        key = self._api_keys.get(provider)
        if not key or key.strip() == "":
            return False, f"{provider.upper()} API key not configured or empty"
        return True, ""

    async def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for a single text with retry and fallback.

        Args:
            text: Text to embed

        Returns:
            Embedding vector
            
        Raises:
            ValueError: If text is empty after sanitization
        """
        text = self._sanitize_text(text)
        if not text:
            raise ValueError("Cannot generate embedding for empty text")
        embeddings = await self.generate_embeddings_batch([text])
        return embeddings[0]

    async def _try_openai_embeddings(
        self,
        texts: List[str]
    ) -> Tuple[Optional[List[List[float]]], Optional[str]]:
        """
        Try to generate embeddings using OpenAI with retry logic.
        
        Returns:
            Tuple of (embeddings, error_message). If successful, error is None.
        """
        config = _resilience_config
        last_error = None
        
        for attempt in range(config.max_retries):
            try:
                response = await get_client().embeddings.create(
                    model=self.model,
                    input=texts
                )
                return [data.embedding for data in response.data], None
                
            except openai.RateLimitError as e:
                last_error = f"Rate limit: {e}"
                delay = min(
                    config.initial_retry_delay * (config.retry_multiplier ** attempt),
                    config.max_retry_delay
                )
                logger.warning(f"OpenAI rate limit hit (attempt {attempt + 1}/{config.max_retries}), retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
                
            except openai.APITimeoutError as e:
                last_error = f"Timeout: {e}"
                delay = min(
                    config.initial_retry_delay * (config.retry_multiplier ** attempt),
                    config.max_retry_delay
                )
                logger.warning(f"OpenAI timeout (attempt {attempt + 1}/{config.max_retries}), retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
                
            except openai.APIConnectionError as e:
                last_error = f"Connection error: {e}"
                delay = min(
                    config.initial_retry_delay * (config.retry_multiplier ** attempt),
                    config.max_retry_delay
                )
                logger.warning(f"OpenAI connection error (attempt {attempt + 1}/{config.max_retries}), retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
                
            except openai.APIStatusError as e:
                # 5xx errors are retryable, 4xx are not (except rate limits)
                if e.status_code >= 500:
                    last_error = f"Server error ({e.status_code}): {e}"
                    delay = min(
                        config.initial_retry_delay * (config.retry_multiplier ** attempt),
                        config.max_retry_delay
                    )
                    logger.warning(f"OpenAI server error (attempt {attempt + 1}/{config.max_retries}), retrying in {delay:.1f}s...")
                    await asyncio.sleep(delay)
                else:
                    # Non-retryable error (auth, invalid request, etc.)
                    return None, f"OpenAI API error ({e.status_code}): {e}"
                    
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                logger.warning(f"Unexpected OpenAI error (attempt {attempt + 1}/{config.max_retries}): {e}")
                delay = min(
                    config.initial_retry_delay * (config.retry_multiplier ** attempt),
                    config.max_retry_delay
                )
                await asyncio.sleep(delay)
        
        return None, last_error

    async def _try_ollama_fallback(
        self,
        texts: List[str]
    ) -> Tuple[Optional[List[List[float]]], Optional[str]]:
        """
        Try to generate embeddings using Ollama as fallback.
        
        Returns:
            Tuple of (embeddings, error_message). If successful, error is None.
        """
        config = _resilience_config
        
        # Check if Ollama is available
        if not await _check_ollama_available(config.fallback_url, config.fallback_model):
            return None, "Ollama not available or model not found"
        
        try:
            logger.info(f"Attempting Ollama fallback with model '{config.fallback_model}'...")
            embeddings = await _generate_ollama_embeddings(
                texts, 
                config.fallback_model, 
                config.fallback_url
            )
            logger.info(f"Ollama fallback successful: generated {len(embeddings)} embeddings")
            return embeddings, None
            
        except Exception as e:
            return None, f"Ollama fallback failed: {e}"

    async def _try_gemini_embeddings(
        self,
        texts: List[str],
        task_type: Optional[str] = None
    ) -> Tuple[Optional[List[List[float]]], Optional[str]]:
        """
        Try to generate embeddings using Google Gemini API.
        
        Args:
            texts: List of texts to embed
            task_type: Task type for optimization (retrieval_query, retrieval_document)
            
        Returns:
            Tuple of (embeddings, error_message). If successful, error is None.
        """
        config = _resilience_config
        api_key = self._api_keys.get("google")
        
        if not api_key:
            return None, "Google API key not configured"
        
        # Validate API key first
        is_valid, error_msg = self._validate_api_key("google")
        if not is_valid:
            return None, error_msg
        
        # Map task type to Gemini format
        gemini_task_map = {
            "retrieval_query": "RETRIEVAL_QUERY",
            "retrieval_document": "RETRIEVAL_DOCUMENT",
            "semantic_similarity": "SEMANTIC_SIMILARITY",
            "classification": "CLASSIFICATION",
            "clustering": "CLUSTERING",
            "question_answering": "QUESTION_ANSWERING",
            "code_retrieval_query": "CODE_RETRIEVAL_QUERY",
        }
        task = gemini_task_map.get(task_type or self.task_type)
        
        base_url = "https://generativelanguage.googleapis.com/v1beta"
        # Use batch endpoint for multiple texts (max 100 per batch for Gemini)
        use_batch = len(texts) > 1 and len(texts) <= 100
        url = f"{base_url}/models/{self.model}:{'batchEmbedContents' if use_batch else 'embedContent'}"
        
        last_error = None
        
        for attempt in range(config.max_retries):
            try:
                embeddings = []
                async with httpx.AsyncClient(timeout=120.0) as client:
                    for text in texts:
                        payload = {
                            "model": f"models/{self.model}",
                            "content": {"parts": [{"text": text}]}
                        }
                        if task:
                            payload["taskType"] = task
                        
                        response = await client.post(
                            url,
                            headers={
                                "Content-Type": "application/json",
                                "x-goog-api-key": api_key,
                            },
                            json=payload,
                        )
                        response.raise_for_status()
                        data = response.json()
                        embedding_data = data.get("embedding", {})
                        embedding_values = embedding_data.get("values", [])
                        
                        # Validate embedding response
                        if not embedding_values:
                            return None, f"Gemini returned empty embedding for text (index {len(embeddings)})"
                        
                        embeddings.append(embedding_values)
                
                # Final validation
                if len(embeddings) != len(texts):
                    return None, f"Gemini returned {len(embeddings)} embeddings for {len(texts)} texts"
                
                logger.info(f"Gemini embeddings generated: {len(embeddings)} vectors")
                return embeddings, None
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:  # Rate limit
                    delay = min(
                        config.initial_retry_delay * (config.retry_multiplier ** attempt),
                        config.max_retry_delay
                    )
                    logger.warning(f"Gemini rate limit (attempt {attempt + 1}/{config.max_retries}), retrying in {delay:.1f}s...")
                    await asyncio.sleep(delay)
                    last_error = f"Rate limit: {e}"
                elif e.response.status_code >= 500:
                    delay = min(
                        config.initial_retry_delay * (config.retry_multiplier ** attempt),
                        config.max_retry_delay
                    )
                    logger.warning(f"Gemini server error (attempt {attempt + 1}/{config.max_retries}), retrying in {delay:.1f}s...")
                    await asyncio.sleep(delay)
                    last_error = f"Server error: {e}"
                else:
                    return None, f"Gemini API error ({e.response.status_code}): {e}"
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                delay = min(
                    config.initial_retry_delay * (config.retry_multiplier ** attempt),
                    config.max_retry_delay
                )
                logger.warning(f"Gemini error (attempt {attempt + 1}/{config.max_retries}): {e}")
                await asyncio.sleep(delay)
        
        return None, last_error

    async def _try_voyage_embeddings(
        self,
        texts: List[str],
        task_type: Optional[str] = None
    ) -> Tuple[Optional[List[List[float]]], Optional[str]]:
        """
        Try to generate embeddings using Voyage AI API.
        
        Args:
            texts: List of texts to embed
            task_type: Task type for optimization (retrieval_query, retrieval_document)
            
        Returns:
            Tuple of (embeddings, error_message). If successful, error is None.
        """
        config = _resilience_config
        api_key = self._api_keys.get("voyageai")
        
        if not api_key:
            return None, "Voyage AI API key not configured"
        
        # Validate API key first
        is_valid, error_msg = self._validate_api_key("voyageai")
        if not is_valid:
            return None, error_msg
        
        # Map task type to Voyage input_type
        voyage_input_map = {
            "retrieval_query": "query",
            "retrieval_document": "document",
            "question_answering": "query",
            "code_retrieval_query": "query",
        }
        input_type = voyage_input_map.get(task_type or self.task_type)
        
        base_url = "https://api.voyageai.com/v1"
        url = f"{base_url}/embeddings"
        
        # Voyage AI has a batch limit of 128 texts per request
        VOYAGE_BATCH_LIMIT = 128
        
        last_error = None
        
        for attempt in range(config.max_retries):
            try:
                all_embeddings = []
                
                # Process in batches if needed
                for batch_start in range(0, len(texts), VOYAGE_BATCH_LIMIT):
                    batch_texts = texts[batch_start:batch_start + VOYAGE_BATCH_LIMIT]
                    
                    payload = {
                        "model": self.model,
                        "input": batch_texts,
                    }
                    if input_type:
                        payload["input_type"] = input_type
                    
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        response = await client.post(
                            url,
                            headers={
                                "Content-Type": "application/json",
                                "Authorization": f"Bearer {api_key}",
                            },
                            json=payload,
                        )
                        response.raise_for_status()
                        data = response.json()
                    
                    batch_embeddings = [item["embedding"] for item in data.get("data", [])]
                    
                    # Validate batch response
                    if len(batch_embeddings) != len(batch_texts):
                        return None, f"Voyage AI returned {len(batch_embeddings)} embeddings for {len(batch_texts)} texts in batch"
                    
                    all_embeddings.extend(batch_embeddings)
                
                logger.info(f"Voyage AI embeddings generated: {len(all_embeddings)} vectors")
                return all_embeddings, None
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:  # Rate limit
                    delay = min(
                        config.initial_retry_delay * (config.retry_multiplier ** attempt),
                        config.max_retry_delay
                    )
                    logger.warning(f"Voyage AI rate limit (attempt {attempt + 1}/{config.max_retries}), retrying in {delay:.1f}s...")
                    await asyncio.sleep(delay)
                    last_error = f"Rate limit: {e}"
                elif e.response.status_code >= 500:
                    delay = min(
                        config.initial_retry_delay * (config.retry_multiplier ** attempt),
                        config.max_retry_delay
                    )
                    logger.warning(f"Voyage AI server error (attempt {attempt + 1}/{config.max_retries}), retrying in {delay:.1f}s...")
                    await asyncio.sleep(delay)
                    last_error = f"Server error: {e}"
                else:
                    return None, f"Voyage AI API error ({e.response.status_code}): {e}"
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                delay = min(
                    config.initial_retry_delay * (config.retry_multiplier ** attempt),
                    config.max_retry_delay
                )
                logger.warning(f"Voyage AI error (attempt {attempt + 1}/{config.max_retries}): {e}")
                await asyncio.sleep(delay)
        
        return None, last_error

    def _get_provider_for_model(self) -> str:
        """Determine the provider type for the current model."""
        model_config = self.model_configs.get(self.model, {})
        return model_config.get("provider", self.provider)

    async def generate_embeddings_batch(
        self,
        texts: List[str],
        task_type: Optional[str] = None
    ) -> List[List[float]]:
        """
        Generate embeddings for a batch of texts with retry and automatic fallback.

        Args:
            texts: List of texts to embed
            task_type: Override task type for this batch (retrieval_query, retrieval_document)

        Returns:
            List of embedding vectors
            
        Raises:
            RuntimeError: If all embedding attempts fail
            ValueError: If texts list is empty or contains only empty strings
        """
        # Validate input
        if not texts:
            raise ValueError("Cannot generate embeddings for empty text list")
        
        config = _resilience_config
        effective_task_type = task_type or self.task_type
        
        # Sanitize and truncate texts
        processed_texts = []
        empty_indices = []
        for idx, text in enumerate(texts):
            sanitized = self._sanitize_text(text)
            if not sanitized:
                # Track empty texts and use placeholder
                empty_indices.append(idx)
                sanitized = "[empty]"  # Placeholder to maintain index alignment
                logger.warning(f"Empty text at index {idx}, using placeholder")
            
            if len(sanitized) > self.config["max_tokens"] * 4:
                sanitized = sanitized[:self.config["max_tokens"] * 4]
            processed_texts.append(sanitized)
        
        if len(empty_indices) == len(texts):
            raise ValueError("All texts are empty after sanitization")

        # Determine provider for the current model
        provider = self._get_provider_for_model()
        embeddings = None
        primary_error = None
        
        # Try primary provider based on model configuration
        if provider == "google":
            logger.info(f"Using Google Gemini embedding: {self.model}")
            embeddings, primary_error = await self._try_gemini_embeddings(
                processed_texts, effective_task_type
            )
        elif provider == "voyageai":
            logger.info(f"Using Voyage AI embedding: {self.model}")
            embeddings, primary_error = await self._try_voyage_embeddings(
                processed_texts, effective_task_type
            )
        elif provider == "ollama":
            logger.info(f"Using Ollama embedding: {self.model}")
            embeddings, primary_error = await self._try_ollama_fallback(processed_texts)
        else:
            # Default to OpenAI
            logger.info(f"Using OpenAI embedding: {self.model}")
            embeddings, primary_error = await self._try_openai_embeddings(processed_texts)
        
        if embeddings is not None:
            return embeddings
        
        # Primary failed - try fallback chain if enabled
        if config.enable_fallback:
            logger.warning(f"{provider.upper()} embedding failed after {config.max_retries} retries: {primary_error}")
            
            # Fallback chain: Try other providers in order
            # NOTE: Using factory functions to avoid lambda closure issues with task_type
            fallback_providers = []
            if provider != "openai" and self._api_keys.get("openai"):
                fallback_providers.append(("openai", self._try_openai_embeddings, None))
            if provider != "google" and self._api_keys.get("google"):
                fallback_providers.append(("google", self._try_gemini_embeddings, effective_task_type))
            if provider != "voyageai" and self._api_keys.get("voyageai"):
                fallback_providers.append(("voyageai", self._try_voyage_embeddings, effective_task_type))
            if provider != "ollama":
                fallback_providers.append(("ollama", self._try_ollama_fallback, None))
            
            fallback_errors = []
            for fallback_name, fallback_fn, fallback_task_type in fallback_providers:
                logger.info(f"Attempting {fallback_name} fallback...")
                if fallback_task_type:
                    embeddings, fallback_error = await fallback_fn(processed_texts, fallback_task_type)
                else:
                    embeddings, fallback_error = await fallback_fn(processed_texts)
                
                if embeddings is not None:
                    # Check dimension mismatch
                    if embeddings and len(embeddings[0]) != self.config["dimensions"]:
                        logger.warning(
                            f"Fallback embedding dimension ({len(embeddings[0])}) differs from "
                            f"configured dimension ({self.config['dimensions']}). "
                            f"This may cause issues with vector search."
                        )
                    return embeddings
                
                fallback_errors.append(f"{fallback_name}: {fallback_error}")
            
            # All fallbacks failed
            error_msg = (
                f"All embedding attempts failed. "
                f"Primary ({provider}): {primary_error}. "
                f"Fallbacks: {'; '.join(fallback_errors)}"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        else:
            # Fallback disabled, report primary failure
            error_msg = f"{provider.upper()} embedding failed after {config.max_retries} retries: {primary_error}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    async def embed_chunks(
        self,
        chunks: List[DocumentChunk],
        progress_callback: Optional[callable] = None
    ) -> List[DocumentChunk]:
        """
        Generate embeddings for document chunks.

        Args:
            chunks: List of document chunks
            progress_callback: Optional callback for progress updates

        Returns:
            Chunks with embeddings added
        """
        if not chunks:
            return chunks

        logger.info(f"Generating embeddings for {len(chunks)} chunks")

        # Process chunks in batches
        embedded_chunks = []
        total_batches = (len(chunks) + self.batch_size - 1) // self.batch_size

        for i in range(0, len(chunks), self.batch_size):
            batch_chunks = chunks[i:i + self.batch_size]
            batch_texts = [chunk.content for chunk in batch_chunks]

            # Generate embeddings for this batch
            embeddings = await self.generate_embeddings_batch(batch_texts)

            # Add embeddings to chunks
            for chunk, embedding in zip(batch_chunks, embeddings):
                embedded_chunk = DocumentChunk(
                    content=chunk.content,
                    index=chunk.index,
                    start_char=chunk.start_char,
                    end_char=chunk.end_char,
                    metadata={
                        **chunk.metadata,
                        "embedding_model": self.model,
                        "embedding_generated_at": datetime.now().isoformat()
                    },
                    token_count=chunk.token_count
                )
                embedded_chunk.embedding = embedding
                embedded_chunks.append(embedded_chunk)

            # Progress update
            current_batch = (i // self.batch_size) + 1
            if progress_callback:
                progress_callback(current_batch, total_batches)

            logger.info(f"Processed batch {current_batch}/{total_batches}")
            
            # Yield control to event loop between batches
            # This allows API requests to be processed during heavy ingestion
            await asyncio.sleep(0)

        logger.info(f"Generated embeddings for {len(embedded_chunks)} chunks")
        return embedded_chunks

    async def embed_query(self, query: str) -> List[float]:
        """
        Generate embedding for a search query.

        Args:
            query: Search query

        Returns:
            Query embedding
        """
        return await self.generate_embedding(query)

    def get_embedding_dimension(self) -> int:
        """Get the dimension of embeddings for this model."""
        return self.config["dimensions"]


def create_embedder(model: Optional[str] = None, **kwargs) -> EmbeddingGenerator:
    """
    Create embedding generator.

    Args:
        model: Embedding model to use (defaults to settings)
        **kwargs: Additional arguments for EmbeddingGenerator

    Returns:
        EmbeddingGenerator instance
    """
    return EmbeddingGenerator(model=model, **kwargs)
