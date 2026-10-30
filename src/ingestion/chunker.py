"""
Docling HybridChunker implementation for intelligent document splitting.

This module uses Docling's built-in HybridChunker which combines:
- Token-aware chunking (uses actual tokenizer)
- Document structure preservation (headings, sections, tables)
- Semantic boundary respect (paragraphs, code blocks)
- Contextualized output (chunks include heading hierarchy)

Benefits over custom chunking:
- Fast (no LLM API calls)
- Token-precise (not character-based estimates)
- Better for RAG (chunks include document context)
- Battle-tested (maintained by Docling team)
"""

import os
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from dotenv import load_dotenv
from transformers import AutoTokenizer
from docling.chunking import HybridChunker
from docling_core.types.doc import DoclingDocument

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


def _furniture_serializer_provider() -> Optional[Any]:
    """A chunking serializer provider that also visits page headers/footers, or ``None``.

    Returns ``None`` when ``INGEST_PAGE_FURNITURE`` is off or the installed docling-core
    predates the serializer API, in which case the HybridChunker keeps its default
    (body layer only).
    """
    try:
        from src.ingestion.ingest import document_content_layers

        layers = document_content_layers()
        if layers is None:
            return None
        from docling_core.transforms.chunker.hierarchical_chunker import (
            ChunkingDocSerializer,
            ChunkingSerializerProvider,
        )
        from docling_core.transforms.serializer.markdown import MarkdownParams
    except Exception as exc:  # noqa: BLE001 - keep the stock chunker on older docling-core
        logger.warning(f"Page furniture chunking not available ({exc}); chunking the body layer only")
        return None

    # ChunkingDocSerializer is a Markdown serializer whose own default params differ from a
    # bare MarkdownParams: its image_placeholder is "" where MarkdownParams renders
    # "<!-- image -->", so a serializer built from bare params turned every picture into a
    # "<!-- image -->" chunk whenever page furniture was on. Start from the chunker's own
    # defaults and only switch the content layers.
    default_params = ChunkingDocSerializer.model_fields["params"].default
    if hasattr(default_params, "model_copy"):
        params = default_params.model_copy(update={"layers": layers})
    else:  # no usable default on this docling-core: the two fields that matter
        params = MarkdownParams(layers=layers, image_placeholder="")

    class _FurnitureSerializerProvider(ChunkingSerializerProvider):
        def get_serializer(self, doc: DoclingDocument) -> ChunkingDocSerializer:
            return ChunkingDocSerializer(doc=doc, params=params)

    return _FurnitureSerializerProvider()


def furniture_serializer_params() -> Optional[Any]:
    """The serializer params the furniture-aware chunker uses, or ``None`` (diagnostics/tests)."""
    provider = _furniture_serializer_provider()
    if provider is None:
        return None
    return provider.get_serializer(DoclingDocument(name="probe")).params


@dataclass
class ChunkingConfig:
    """Configuration for DoclingHybridChunker."""
    chunk_size: int = 1000  # Target characters per chunk (used in fallback)
    chunk_overlap: int = 200  # Character overlap between chunks (used in fallback)
    max_chunk_size: int = 2000  # Maximum chunk size (used in fallback)
    min_chunk_size: int = 100  # Minimum chunk size (used in fallback)
    max_tokens: int = field(default_factory=lambda: chunk_max_tokens_default())  # chunker budget per chunk
    embed_max_tokens: int = field(default_factory=lambda: embed_max_tokens_default())  # embedder's per-text limit

    def __post_init__(self):
        """Validate configuration."""
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("Chunk overlap must be less than chunk size")
        if self.embed_max_tokens < self.max_tokens:
            raise ValueError("embed_max_tokens must not be below max_tokens")
        if self.min_chunk_size <= 0:
            raise ValueError("Minimum chunk size must be positive")


@dataclass
class DocumentChunk:
    """Represents a document chunk with optional embedding."""
    content: str
    index: int
    start_char: int
    end_char: int
    metadata: Dict[str, Any]
    token_count: Optional[int] = None
    embedding: Optional[List[float]] = None  # For embedder compatibility

    def __post_init__(self):
        """Calculate token count if not provided."""
        if self.token_count is None:
            # Rough estimation: ~4 characters per token
            self.token_count = len(self.content) // 4


class DoclingHybridChunker:
    """
    Docling HybridChunker wrapper for intelligent document splitting.

    This chunker uses Docling's built-in HybridChunker which:
    - Respects document structure (sections, paragraphs, tables)
    - Is token-aware (fits embedding model limits)
    - Preserves semantic coherence
    - Includes heading context in chunks
    """

    def __init__(self, config: ChunkingConfig):
        """
        Initialize chunker.

        Args:
            config: Chunking configuration
        """
        self.config = config

        # Initialize tokenizer for token-aware chunking
        model_id = "sentence-transformers/all-MiniLM-L6-v2"
        logger.info(f"Initializing tokenizer: {model_id}")
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)

        # Create HybridChunker. Page headers/footers (Docling's furniture layer) are chunked
        # too when INGEST_PAGE_FURNITURE is on: the chunker only visits the content layers
        # its serializer declares, and the default is the body alone.
        chunker_kwargs: Dict[str, Any] = {}
        serializer_provider = _furniture_serializer_provider()
        if serializer_provider is not None:
            chunker_kwargs["serializer_provider"] = serializer_provider
        self.chunker = HybridChunker(
            tokenizer=self.tokenizer,
            max_tokens=config.max_tokens,
            merge_peers=True,  # Merge small adjacent chunks
            **chunker_kwargs,
        )

        logger.info(f"HybridChunker initialized (max_tokens={config.max_tokens})")

    async def chunk_document(
        self,
        content: str,
        title: str,
        source: str,
        metadata: Optional[Dict[str, Any]] = None,
        docling_doc: Optional[DoclingDocument] = None
    ) -> List[DocumentChunk]:
        """
        Chunk a document using Docling's HybridChunker.

        Args:
            content: Document content (markdown format)
            title: Document title
            source: Document source
            metadata: Additional metadata
            docling_doc: Optional pre-converted DoclingDocument (for efficiency)

        Returns:
            List of document chunks with contextualized content
        """
        if not content.strip():
            return []

        base_metadata = {
            "title": title,
            "source": source,
            "chunk_method": "hybrid",
            **(metadata or {})
        }

        # If we don't have a DoclingDocument, we need to create one from markdown
        if docling_doc is None:
            # For markdown content, we need to convert it to DoclingDocument
            # This is a simplified version - in practice, content comes from
            # Docling's document converter in the ingestion pipeline
            logger.warning("No DoclingDocument provided, using simple chunking fallback")
            return self._simple_fallback_chunk(content, base_metadata)

        try:
            # Use HybridChunker to chunk the DoclingDocument
            chunk_iter = self.chunker.chunk(dl_doc=docling_doc)
            chunks = list(chunk_iter)

            # Convert Docling chunks to DocumentChunk objects
            document_chunks = []
            current_pos = 0

            for i, chunk in enumerate(chunks):
                # Get contextualized text (includes heading hierarchy)
                contextualized_text = self.chunker.contextualize(chunk=chunk)

                # Count actual tokens
                token_count = len(self.tokenizer.encode(contextualized_text))

                # Create chunk metadata
                chunk_metadata = {
                    **base_metadata,
                    "total_chunks": len(chunks),
                    "token_count": token_count,
                    "has_context": True  # Flag indicating contextualized chunk
                }

                # Estimate character positions
                start_char = current_pos
                end_char = start_char + len(contextualized_text)

                document_chunks.append(DocumentChunk(
                    content=contextualized_text.strip(),
                    index=i,
                    start_char=start_char,
                    end_char=end_char,
                    metadata=chunk_metadata,
                    token_count=token_count
                ))

                current_pos = end_char

            document_chunks = split_oversized_chunks(
                document_chunks, self.config.max_tokens, lambda t: len(self.tokenizer.encode(t)),
                limit_tokens=self.config.embed_max_tokens,
            )
            logger.info(f"Created {len(document_chunks)} chunks using HybridChunker")
            return document_chunks

        except Exception as e:
            logger.error(f"HybridChunker failed: {e}, falling back to simple chunking")
            return self._simple_fallback_chunk(content, base_metadata)

    def _simple_fallback_chunk(
        self,
        content: str,
        base_metadata: Dict[str, Any]
    ) -> List[DocumentChunk]:
        """
        Simple fallback chunking when HybridChunker can't be used.

        This is used when:
        - No DoclingDocument is provided
        - HybridChunker fails

        Args:
            content: Content to chunk
            base_metadata: Base metadata for chunks

        Returns:
            List of document chunks
        """
        chunks = []
        chunk_size = self.config.chunk_size
        overlap = self.config.chunk_overlap

        # Simple sliding window approach
        start = 0
        chunk_index = 0

        while start < len(content):
            end = start + chunk_size

            if end >= len(content):
                # Last chunk
                chunk_text = content[start:]
            else:
                # Try to end at sentence boundary
                chunk_end = end
                for i in range(end, max(start + self.config.min_chunk_size, end - 200), -1):
                    if i < len(content) and content[i] in '.!?\n':
                        chunk_end = i + 1
                        break
                chunk_text = content[start:chunk_end]
                end = chunk_end

            if chunk_text.strip():
                token_count = len(self.tokenizer.encode(chunk_text))

                chunks.append(DocumentChunk(
                    content=chunk_text.strip(),
                    index=chunk_index,
                    start_char=start,
                    end_char=end,
                    metadata={
                        **base_metadata,
                        "chunk_method": "simple_fallback",
                        "total_chunks": -1  # Will update after
                    },
                    token_count=token_count
                ))

                chunk_index += 1

            # Move forward with overlap
            start = end - overlap

        # Update total chunks
        for chunk in chunks:
            chunk.metadata["total_chunks"] = len(chunks)

        logger.info(f"Created {len(chunks)} chunks using simple fallback")
        return chunks


#: A chunk above this many times ``max_tokens`` is cut into windows (the HybridChunker
#: emits a whole table as one chunk when it cannot split it: Firmenbuch extracts gave
#: 8,000-token chunks that the embedding server rejected together with their batch).
OVERSIZE_FACTOR = 4
#: Window size of the cut pieces, as a multiple of ``max_tokens``.
WINDOW_FACTOR = 3
#: Characters per token assumed when a counter is not given.
CHARS_PER_TOKEN = 3
WINDOW_OVERLAP_CHARS = 200


#: Default chunker budget (tokens per chunk) and the embedder's per-text limit (tokens);
#: ``CHUNK_MAX_TOKENS`` / ``EMBEDDING_MAX_TOKENS`` in the environment override them
#: (``src/settings.py`` documents them). 512 until 2026-10-26: with EmbeddingGemma 2's
#: 2,048-token window the literature of the diejuristin corpus averaged 23 chunks per
#: document; 1,024 halves the chunk count and keeps a clause or a table row together.
DEFAULT_CHUNK_MAX_TOKENS = 1024
DEFAULT_EMBED_MAX_TOKENS = 2048


def _env_int(name: str, default: int, minimum: int = 64) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(minimum, int(raw.strip()))
    except ValueError:
        logger.warning(f"{name}={raw!r} is not an integer; using {default}")
        return default


def chunk_max_tokens_default() -> int:
    """The chunker's token budget per chunk: ``CHUNK_MAX_TOKENS`` or 1024."""
    return _env_int("CHUNK_MAX_TOKENS", DEFAULT_CHUNK_MAX_TOKENS)


def embed_max_tokens_default() -> int:
    """The embedding model's per-text token limit: ``EMBEDDING_MAX_TOKENS`` or 2048."""
    return _env_int("EMBEDDING_MAX_TOKENS", DEFAULT_EMBED_MAX_TOKENS)


def split_oversized_chunks(
    chunks: List[DocumentChunk], max_tokens: int, count_tokens=None, limit_tokens: Optional[int] = None
) -> List[DocumentChunk]:
    """Cut chunks longer than the embedder allows into overlapping windows.

    A chunk is oversized above ``OVERSIZE_FACTOR * max_tokens`` tokens or above
    ``limit_tokens`` (the embedding model's per-text limit), whichever is smaller; the
    windows stay below ``limit_tokens``.

    Args:
        chunks: Chunks in document order.
        max_tokens: The chunker's token target.
        count_tokens: Optional token counter; ``len(text) / CHARS_PER_TOKEN`` when omitted.
        limit_tokens: The embedding model's window; ``OVERSIZE_FACTOR * max_tokens`` when omitted.

    Returns:
        The chunks with oversized ones replaced by their windows, indices renumbered and
        ``total_chunks`` updated; untouched when nothing is oversized.
    """
    counter = count_tokens or (lambda text: len(text) // CHARS_PER_TOKEN)
    limit = OVERSIZE_FACTOR * max_tokens
    window_tokens = WINDOW_FACTOR * max_tokens
    if limit_tokens:
        limit = min(limit, int(limit_tokens))
        window_tokens = min(window_tokens, int(limit_tokens) * 3 // 4)
    window_chars = max(CHARS_PER_TOKEN * 64, window_tokens * CHARS_PER_TOKEN)
    result: List[DocumentChunk] = []
    changed = False
    for chunk in chunks:
        tokens = chunk.token_count if chunk.token_count is not None else counter(chunk.content)
        if tokens <= limit:
            result.append(chunk)
            continue
        changed = True
        text = chunk.content
        pieces = []
        start = 0
        while start < len(text):
            end = min(len(text), start + window_chars)
            if end < len(text):
                cut = text.rfind("\n", start + window_chars // 2, end)
                if cut > start:
                    end = cut
            pieces.append(text[start:end].strip())
            if end >= len(text):
                break
            start = max(end - WINDOW_OVERLAP_CHARS, start + 1)
        pieces = [p for p in pieces if p]
        logger.warning(
            f"Chunk {chunk.index} of {chunk.metadata.get('source', '?')} has ~{tokens} tokens; "
            f"split into {len(pieces)} windows of <= {window_chars} chars"
        )
        offset = chunk.start_char
        for part, piece in enumerate(pieces, start=1):
            result.append(DocumentChunk(
                content=piece,
                index=0,
                start_char=offset,
                end_char=offset + len(piece),
                metadata={**chunk.metadata, "split_part": part, "split_parts": len(pieces)},
                token_count=counter(piece),
            ))
            offset += len(piece)
    if not changed:
        return chunks
    for index, chunk in enumerate(result):
        chunk.index = index
        chunk.metadata["total_chunks"] = len(result)
        chunk.metadata["token_count"] = chunk.token_count
    return result


def create_chunker(config: ChunkingConfig):
    """
    Create DoclingHybridChunker for intelligent document splitting.

    Args:
        config: Chunking configuration

    Returns:
        DoclingHybridChunker instance
    """
    return DoclingHybridChunker(config)
