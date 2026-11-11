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

import contextlib
import contextvars
import os
import logging
import time
from typing import Callable, Iterator, List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

from dotenv import load_dotenv
from transformers import AutoTokenizer
from docling.chunking import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from docling_core.types.doc import DoclingDocument, TableCell, TableData, TableItem

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


class ChunkingDeadlineExceeded(RuntimeError):
    """Chunking ran past the deadline it was given (see :func:`chunking_deadline`)."""


#: ``time.monotonic()`` value after which the chunker aborts, for the task/thread chunking now.
_chunking_deadline: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "chunking_deadline", default=None
)


@contextlib.contextmanager
def chunking_deadline(deadline: Optional[float]) -> Iterator[None]:
    """Make every token count inside the block check ``deadline`` (``time.monotonic()``).

    The HybridChunker's splitting loops count tokens at every step, so a deadline checked
    there ends a pathological split from the inside, wherever docling-core is spinning:
    :class:`DeadlineTokenizer` raises :class:`ChunkingDeadlineExceeded` on the first count
    after the deadline. ``None`` disables the check.
    """
    token = _chunking_deadline.set(deadline)
    try:
        yield
    finally:
        _chunking_deadline.reset(token)


def check_chunking_deadline() -> None:
    """Raise :class:`ChunkingDeadlineExceeded` when the current chunking deadline has passed."""
    deadline = _chunking_deadline.get()
    if deadline is not None and time.monotonic() > deadline:
        raise ChunkingDeadlineExceeded(
            f"chunking ran past its deadline by {time.monotonic() - deadline:.0f}s"
        )


class DeadlineTokenizer(HuggingFaceTokenizer):
    """The HybridChunker's tokenizer, checking the chunking deadline on every count.

    docling-core's table and text splitting is built on ``count_tokens``; a 2,670 x 32 cell
    spreadsheet (2.7 MB of Markdown) made ``_split_table_by_columns`` re-tokenize the whole
    table over and over for hours on the event-loop thread (2026-11-02/03). Checking the
    deadline here costs one clock read per count and aborts any such loop cooperatively.
    """

    def count_tokens(self, text: str) -> int:
        check_chunking_deadline()
        return super().count_tokens(text)


#: A table estimated above this many times ``max_tokens`` is split into row windows before
#: the HybridChunker sees it (:func:`presplit_large_tables`).
TABLE_PRESPLIT_FACTOR = 2
#: Row windows are packed to this share of ``max_tokens``, so that most of them fit one chunk
#: as the chunker serializes them (its own split of a window is correct but costs a few
#: hundred token counts per window).
TABLE_WINDOW_FILL = 0.8


def _table_rows(table: TableItem) -> List[List[TableCell]]:
    """The table's cells grouped by their starting row, in row/column order."""
    rows: Dict[int, List[TableCell]] = {}
    for cell in table.data.table_cells:
        rows.setdefault(cell.start_row_offset_idx, []).append(cell)
    return [sorted(cells, key=lambda c: c.start_col_offset_idx) for _, cells in sorted(rows.items())]


def _row_text(cells: List[TableCell]) -> str:
    return "| " + " | ".join(cell.text for cell in cells) + " |"


def _rebased_cells(rows: List[List[TableCell]], first_row: int, offset: int) -> List[TableCell]:
    """Copies of the cells of ``rows``, their row offsets moved from ``first_row`` to ``offset``."""
    out: List[TableCell] = []
    last_row = first_row + len(rows)
    for row_cells in rows:
        for cell in row_cells:
            end_row = min(cell.end_row_offset_idx, last_row)
            out.append(cell.model_copy(update={
                "start_row_offset_idx": cell.start_row_offset_idx - first_row + offset,
                "end_row_offset_idx": end_row - first_row + offset,
                "row_span": max(1, end_row - cell.start_row_offset_idx),
            }))
    return out


def _row_weights(rows: List[List[TableCell]], serialized_tokens: Optional[int]) -> List[float]:
    """Estimated tokens of each row as the chunker will serialize it.

    With ``serialized_tokens`` (the token count of the chunker's own serialization of the
    whole table, triplets by default) the rows share it in proportion to their text; without
    it the Markdown row text is counted by the caller instead.
    """
    chars = [sum(len(cell.text) + 3 for cell in row) for row in rows]
    total_chars = max(1, sum(chars))
    if serialized_tokens is None:
        return [float(c) for c in chars]
    return [serialized_tokens * c / total_chars for c in chars]


def presplit_large_tables(
    doc: DoclingDocument,
    max_tokens: int,
    count_tokens: Callable[[str], int],
    serializer_provider: Optional[Any] = None,
) -> Tuple[DoclingDocument, int]:
    """Replace every oversized table by row-window sub-tables, each with the header rows.

    The HybridChunker handles a table that does not fit a chunk by splitting it by columns
    and re-tokenizing the whole table at every step of that search, then feeding every
    column slice to a line chunker, and repeating the whole segmentation whenever a piece
    overflows: 2 s for a 100 x 10 table, 51 s for 200 x 20, hours for 2,670 x 32. Cutting a
    big table into sub-tables of about ``TABLE_WINDOW_FILL * max_tokens`` tokens first keeps
    that work linear in the table's size (one token count of the serialized table here, a
    small table per window there) and gives each window the header rows, which the column
    split lost anyway.

    Tables whose text is estimated below ``TABLE_PRESPLIT_FACTOR * max_tokens`` tokens are
    left to the chunker; when nothing needs splitting the document is returned as is,
    otherwise a copy is modified (the caller's document keeps its tables).

    Args:
        doc: The converted document.
        max_tokens: The chunker's token budget per chunk.
        count_tokens: Token counter of the chunker's tokenizer.
        serializer_provider: The chunker's serializer provider; with it the windows are
            sized from the chunker's own serialization of the table (the triplet form is
            about twice the Markdown), without it from the Markdown rows.

    Returns:
        ``(document, tables_split)``.
    """
    if not doc.tables:
        return doc, 0
    threshold_chars = TABLE_PRESPLIT_FACTOR * max_tokens * CHARS_PER_TOKEN
    oversized = [
        table.self_ref for table in doc.tables
        if sum(len(cell.text) + 3 for cell in table.data.table_cells) > threshold_chars
    ]
    if not oversized:
        return doc, 0
    doc = doc.model_copy(deep=True)
    serializer = serializer_provider.get_serializer(doc=doc) if serializer_provider is not None else None
    split = 0
    for ref in oversized:
        table = next(t for t in doc.tables if t.self_ref == ref)
        rows = _table_rows(table)
        serialized_tokens: Optional[int] = None
        if serializer is not None:
            try:
                serialized_tokens = count_tokens(serializer.serialize(item=table).text)
            except ChunkingDeadlineExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - size the windows from the Markdown rows instead
                logger.debug(f"Table serialization for window sizing failed ({exc}); using Markdown rows")
        header_rows: List[List[TableCell]] = []
        for row in rows:
            if row and all(cell.column_header for cell in row):
                header_rows.append(row)
            else:
                break
        if not header_rows:
            header_rows = rows[:1]
        body_rows = rows[len(header_rows):]
        if serialized_tokens is None:
            header_tokens = sum(count_tokens(_row_text(r)) for r in header_rows)
            weights = [float(count_tokens(_row_text(row))) for row in body_rows]
        else:
            header_tokens = 0  # the serialized form carries the header inside every row
            weights = _row_weights(rows, serialized_tokens)[len(header_rows):]
        budget = max(1.0, TABLE_WINDOW_FILL * max_tokens - header_tokens)
        windows: List[List[List[TableCell]]] = []
        window: List[List[TableCell]] = []
        window_tokens = 0.0
        for row, tokens in zip(body_rows, weights):
            if window and window_tokens + tokens > budget:
                windows.append(window)
                window, window_tokens = [], 0.0
            window.append(row)
            window_tokens += tokens
        if window:
            windows.append(window)
        if len(windows) < 2:
            continue
        header_first = header_rows[0][0].start_row_offset_idx
        sibling = table
        caption = table.captions[0] if table.captions else None
        for window in windows:
            cells = _rebased_cells(header_rows, header_first, 0)
            cells.extend(_rebased_cells(window, window[0][0].start_row_offset_idx, len(header_rows)))
            sibling = doc.insert_table(
                sibling=sibling,
                data=TableData(
                    num_rows=len(header_rows) + len(window),
                    num_cols=table.data.num_cols,
                    table_cells=cells,
                ),
                caption=caption,
                prov=table.prov[0] if table.prov else None,
                label=table.label,
                content_layer=table.content_layer,
                after=True,
            )
            caption = None
        doc.delete_items(node_items=[table])
        split += 1
        logger.info(
            f"Table of {len(rows)} rows x {table.data.num_cols} columns split into "
            f"{len(windows)} row windows of <= {max_tokens} tokens before chunking"
        )
    return doc, split


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
        # The chunker's tokenizer checks the chunking deadline on every token count, so a
        # pathological split (a huge table) ends when the file's post-conversion budget does
        self.deadline_tokenizer = DeadlineTokenizer(tokenizer=self.tokenizer, max_tokens=config.max_tokens)
        self.chunker = HybridChunker(
            tokenizer=self.deadline_tokenizer,
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
        docling_doc: Optional[DoclingDocument] = None,
        deadline: Optional[float] = None,
    ) -> List[DocumentChunk]:
        """
        Chunk a document using Docling's HybridChunker.

        The work is synchronous (:meth:`chunk_document_sync`); the ingestion pipeline runs
        it on a thread so the event loop stays responsive, this coroutine form is kept for
        callers that chunk inline.

        Args:
            content: Document content (markdown format)
            title: Document title
            source: Document source
            metadata: Additional metadata
            docling_doc: Optional pre-converted DoclingDocument (for efficiency)
            deadline: ``time.monotonic()`` value after which chunking aborts with
                :class:`ChunkingDeadlineExceeded`; ``None`` for no limit

        Returns:
            List of document chunks with contextualized content
        """
        return self.chunk_document_sync(
            content=content, title=title, source=source, metadata=metadata,
            docling_doc=docling_doc, deadline=deadline,
        )

    def chunk_document_sync(
        self,
        content: str,
        title: str,
        source: str,
        metadata: Optional[Dict[str, Any]] = None,
        docling_doc: Optional[DoclingDocument] = None,
        deadline: Optional[float] = None,
    ) -> List[DocumentChunk]:
        """Chunk a document (the body of :meth:`chunk_document`, callable from a worker thread).

        Oversized tables are cut into row windows first (:func:`presplit_large_tables`), and
        every token count checks ``deadline``, so neither the HybridChunker's splitting nor
        this method's own loop can run on unbounded.

        Raises:
            ChunkingDeadlineExceeded: ``deadline`` passed before the chunks were ready.
        """
        with chunking_deadline(deadline):
            return self._chunk_document(content, title, source, metadata, docling_doc)

    def _chunk_document(
        self,
        content: str,
        title: str,
        source: str,
        metadata: Optional[Dict[str, Any]],
        docling_doc: Optional[DoclingDocument],
    ) -> List[DocumentChunk]:
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
            # Big tables are cut into row windows first: the HybridChunker's column split
            # re-tokenizes the whole table at every step and took hours on a 2,670-row sheet
            docling_doc, _ = presplit_large_tables(
                docling_doc, self.config.max_tokens, self.deadline_tokenizer.count_tokens,
                serializer_provider=self.chunker.serializer_provider,
            )

            # Use HybridChunker to chunk the DoclingDocument
            chunk_iter = self.chunker.chunk(dl_doc=docling_doc)
            chunks = list(chunk_iter)

            # Convert Docling chunks to DocumentChunk objects
            document_chunks = []
            current_pos = 0

            for i, chunk in enumerate(chunks):
                check_chunking_deadline()
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

        except ChunkingDeadlineExceeded:
            raise
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
            check_chunking_deadline()
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
