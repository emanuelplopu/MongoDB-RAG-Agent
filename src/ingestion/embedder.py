"""Document embedding generation for vector search.
Supports OpenAI and Ollama embedding providers with automatic fallback and retry.
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
    """Generates embeddings for document chunks."""

    def __init__(
        self,
        model: Optional[str] = None,
        batch_size: int = 100
    ):
        """
        Initialize embedding generator.

        Args:
            model: Embedding model to use (defaults to settings)
            batch_size: Number of texts to process in parallel
        """
        settings = load_settings(use_profile=False)
        self.model = model or settings.embedding_model
        self.batch_size = batch_size
        self.provider = settings.embedding_provider.lower()

        # Model-specific configurations
        self.model_configs = {
            # OpenAI models
            "text-embedding-3-small": {"dimensions": 1536, "max_tokens": 8191},
            "text-embedding-3-large": {"dimensions": 3072, "max_tokens": 8191},
            "text-embedding-ada-002": {"dimensions": 1536, "max_tokens": 8191},
            # Ollama models
            "nomic-embed-text": {"dimensions": 768, "max_tokens": 8192},
            "mxbai-embed-large": {"dimensions": 1024, "max_tokens": 512},
            "all-minilm": {"dimensions": 384, "max_tokens": 256},
            "snowflake-arctic-embed": {"dimensions": 1024, "max_tokens": 512},
        }

        # Use configured dimension or model default
        default_config = {"dimensions": settings.embedding_dimension, "max_tokens": 8191}
        self.config = self.model_configs.get(self.model, default_config)
        
        # Override with settings if specified
        if settings.embedding_dimension:
            self.config["dimensions"] = settings.embedding_dimension
        
        logger.info(f"Embedding generator initialized: model={self.model}, provider={self.provider}, dimensions={self.config['dimensions']}")

    async def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for a single text with retry and fallback.

        Args:
            text: Text to embed

        Returns:
            Embedding vector
        """
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

    async def generate_embeddings_batch(
        self,
        texts: List[str]
    ) -> List[List[float]]:
        """
        Generate embeddings for a batch of texts with retry and automatic fallback.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
            
        Raises:
            RuntimeError: If all embedding attempts fail
        """
        config = _resilience_config
        
        # Truncate texts if too long
        processed_texts = []
        for text in texts:
            if len(text) > self.config["max_tokens"] * 4:
                text = text[:self.config["max_tokens"] * 4]
            processed_texts.append(text)

        # Try primary provider (OpenAI) with retries
        embeddings, openai_error = await self._try_openai_embeddings(processed_texts)
        
        if embeddings is not None:
            return embeddings
        
        # Primary failed - try fallback if enabled
        if config.enable_fallback:
            logger.warning(f"OpenAI embedding failed after {config.max_retries} retries: {openai_error}")
            logger.info("Attempting Ollama fallback...")
            
            embeddings, fallback_error = await self._try_ollama_fallback(processed_texts)
            
            if embeddings is not None:
                # Note: Ollama embeddings may have different dimensions
                # This is logged but allowed - the caller should handle dimension mismatches
                if embeddings and len(embeddings[0]) != self.config["dimensions"]:
                    logger.warning(
                        f"Fallback embedding dimension ({len(embeddings[0])}) differs from "
                        f"configured dimension ({self.config['dimensions']}). "
                        f"This may cause issues with vector search."
                    )
                return embeddings
            
            # Both failed
            error_msg = (
                f"All embedding attempts failed. "
                f"OpenAI: {openai_error}. Ollama: {fallback_error}"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        else:
            # Fallback disabled, report OpenAI failure
            error_msg = f"OpenAI embedding failed after {config.max_retries} retries: {openai_error}"
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
