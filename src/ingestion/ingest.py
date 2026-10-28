"""
Main ingestion script for processing documents into MongoDB vector database.

This adapts the examples/ingestion/ingest.py pipeline to use MongoDB instead of PostgreSQL,
changing only the database layer while preserving all document processing logic.
"""

import os
import re
import asyncio
import logging
import glob
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Iterator, Optional, Set, Tuple, Callable, Awaitable
from datetime import datetime
import argparse
import concurrent.futures
import contextlib
import contextvars
import time
from dataclasses import dataclass, field, asdict
from concurrent.futures import ThreadPoolExecutor
import functools
import threading

from pymongo import AsyncMongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from bson import ObjectId
from dotenv import load_dotenv

try:
    import pypdfium2 as pdfium
    PDFIUM_AVAILABLE = True
except ImportError:
    PDFIUM_AVAILABLE = False
    pdfium = None

from src.ingestion.chunker import ChunkingConfig, create_chunker, DocumentChunk, chunk_max_tokens_default
from src.ingestion.embedder import create_embedder
from src.ingestion.ocr_repair import repair_docling_document, repair_ocr_text
from src.ingestion import telemetry_hooks
from src.ingestion.progress import progress_percent_label
from src.settings import load_settings
from src.profile import get_profile_manager

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


def compute_file_hash(file_path: str) -> str:
    """
    Compute SHA256 hash of a file for deduplication.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Hex digest of the file's SHA256 hash
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        # Read in 64KB chunks for memory efficiency
        for chunk in iter(lambda: f.read(65536), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def is_image_only_pdf(file_path: str, min_text_chars: int = 50, max_pages_to_check: int = 3) -> Tuple[bool, str]:
    """
    Check if a PDF is image-only (no extractable text layer).
    
    This is a fast pre-check to skip PDFs that would produce no chunks.
    Uses pypdfium2 to quickly extract text without OCR.
    
    Args:
        file_path: Path to the PDF file
        min_text_chars: Minimum characters to consider the PDF text-based (default 50)
        max_pages_to_check: Number of pages to check (default 3 for speed)
        
    Returns:
        Tuple of (is_image_only, reason)
        - is_image_only: True if PDF has no extractable text
        - reason: Human-readable explanation
    """
    if not PDFIUM_AVAILABLE:
        # Can't check without pdfium - assume it has text
        return False, "pypdfium2 not available, assuming text-based"
    
    if not file_path.lower().endswith('.pdf'):
        return False, "Not a PDF file"
    
    try:
        pdf = pdfium.PdfDocument(file_path)
        total_pages = len(pdf)
        pages_to_check = min(total_pages, max_pages_to_check)
        
        extracted_text = ""
        for i in range(pages_to_check):
            page = pdf[i]
            textpage = page.get_textpage()
            page_text = textpage.get_text_range()
            extracted_text += page_text
            
            # Early exit if we found enough text
            if len(extracted_text.strip()) >= min_text_chars:
                pdf.close()
                return False, f"Found {len(extracted_text.strip())} chars of text"
        
        pdf.close()
        
        text_len = len(extracted_text.strip())
        if text_len < min_text_chars:
            return True, f"Only {text_len} chars found (threshold: {min_text_chars}), likely image-only"
        
        return False, f"Found {text_len} chars of text"
        
    except Exception as e:
        # If we can't read it, let Docling try
        logger.debug(f"Could not pre-check PDF {file_path}: {e}")
        return False, f"Pre-check failed: {e}"


#: Canonical set of file extensions the ingestion pipeline knows how to read.
#:
#: This is the authoritative filter used by :meth:`DocumentIngestionPipeline._find_document_files`
#: when walking document folders. The pre-ingestion estimator
#: (:mod:`backend.services.ingestion_estimator`) imports this set so its file
#: count matches what a real run would process. Keep it in sync with the glob
#: ``patterns`` in ``backend/routers/ingestion.py`` and the ``TYPE_PRIORITY``
#: map below.
SUPPORTED_INGESTION_EXTENSIONS: frozenset[str] = frozenset({
    ".md", ".markdown", ".txt",  # Text formats
    ".pdf",  # PDF
    ".docx", ".doc", ".wbk", ".dot", ".dotx",  # Word (incl. backup and templates)
    ".rtf",  # Rich Text (letters, Muster templates)
    ".pptx", ".ppt",  # PowerPoint
    ".xlsx", ".xls",  # Excel (legacy .xls via xlrd)
    ".html", ".htm", ".mht", ".mhtml",  # HTML and MHTML web archives
    ".as", ".ass", ".ds", ".eb", ".do", ".dos", ".3", ".dss",  # DOS-era text system files (see LEGACY_TEXT_EXTENSIONS)
    ".wvb", ".mtt",  # jurXpert "weiteres Vorbringen" text/RTF, gzip-packed Muster templates
    ".msg",  # Outlook email
    ".eml",  # RFC 5322 email
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif",  # Images
    ".mp3", ".wav", ".m4a", ".flac",  # Audio formats
    ".mp4", ".avi", ".mkv", ".mov", ".webm",  # Video formats
})


@dataclass
class IngestionConfig:
    """Configuration for document ingestion."""
    chunk_size: int = 1000
    chunk_overlap: int = 200
    max_chunk_size: int = 2000
    max_tokens: int = field(default_factory=lambda: chunk_max_tokens_default())  # CHUNK_MAX_TOKENS, 1024


@dataclass
class IngestionResult:
    """Result of document ingestion."""
    document_id: str
    title: str
    chunks_created: int
    processing_time_ms: float
    errors: List[str]
    # PDFs only: which scan-text fallback put OCR text back into the document after the
    # conversion (F35): "picture_children", "full_page" or both joined with "+"; None = none
    ocr_fallback: Optional[str] = None


@dataclass
class IngestionFileStats:
    """Detailed stats for a single file ingestion."""
    file_path: str
    file_name: str
    file_size_bytes: int
    started_at: datetime
    completed_at: Optional[datetime]
    processing_time_ms: float
    chunks_created: int
    success: bool
    error_type: Optional[str]  # timeout, error, none, no_chunks, image_only_pdf
    error_message: Optional[str]
    timeout_seconds: float
    profile_key: str
    job_id: Optional[str] = None  # Link to parent ingestion job
    content_hash: Optional[str] = None  # SHA256 hash for change detection
    file_modified_at: Optional[datetime] = None  # Filesystem modification time
    classification: str = "pending"  # FileClassification value
    # PDFs only: True when the file has no text layer (a pure scan), so its text came from
    # OCR (success) or OCR found nothing (error_type image_only_pdf); None = not checked (F9)
    image_only_pdf: Optional[bool] = None
    # PDFs only: the scan-text fallback that recovered OCR text Docling had left out of the
    # body (F35): "picture_children", "full_page" or "picture_children+full_page"; None = none
    ocr_fallback: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for MongoDB storage."""
        result = {
            "file_path": self.file_path,
            "file_name": self.file_name,
            "file_size_bytes": self.file_size_bytes,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "processing_time_ms": self.processing_time_ms,
            "chunks_created": self.chunks_created,
            "success": self.success,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "timeout_seconds": self.timeout_seconds,
            "profile_key": self.profile_key,
            "content_hash": self.content_hash,
            "file_modified_at": self.file_modified_at.isoformat() if self.file_modified_at else None,
            "classification": self.classification,
        }
        if self.job_id:
            result["job_id"] = self.job_id
        if self.image_only_pdf is not None:
            result["image_only_pdf"] = self.image_only_pdf
        if self.ocr_fallback:
            result["ocr_fallback"] = self.ocr_fallback
        return result


@dataclass
class TimeoutConfig:
    """Configuration for file processing timeouts."""
    base_timeout: int = 120  # 2 minutes minimum (increased from 60)
    seconds_per_mb: float = 30.0  # 30 seconds per MB (increased from 10)
    max_timeout: int = 1800  # 30 minutes max (increased from 15)
    pdf_multiplier: float = 1.5  # Extra time for PDFs (OCR-heavy)
    complex_pdf_threshold_mb: float = 1.0  # PDFs above this get extra time
    complex_pdf_multiplier: float = 2.0  # Extra multiplier for complex PDFs
    image_multiplier: float = 2.0  # Extra time for images (OCR processing)
    audio_multiplier: float = 3.0  # Extra time for audio (transcription)
    max_retries: int = 2  # Number of retries for timed-out files
    retry_timeout_multiplier: float = 1.5  # Increase timeout on retry


# Global timeout config - can be updated from database
_timeout_config = TimeoutConfig()


def get_timeout_config() -> TimeoutConfig:
    """Get current timeout configuration."""
    return _timeout_config


def update_timeout_config(
    base_timeout: int = None,
    seconds_per_mb: float = None,
    max_timeout: int = None,
    pdf_multiplier: float = None,
    complex_pdf_threshold_mb: float = None,
    complex_pdf_multiplier: float = None,
    max_retries: int = None,
    retry_timeout_multiplier: float = None
) -> TimeoutConfig:
    """
    Update timeout configuration values.
    
    Args:
        base_timeout: Base timeout in seconds
        seconds_per_mb: Seconds to add per MB of file size
        max_timeout: Maximum timeout cap
        pdf_multiplier: Multiplier for PDF files
        complex_pdf_threshold_mb: Size threshold for complex PDF handling
        complex_pdf_multiplier: Extra multiplier for complex PDFs
        max_retries: Number of retries for timed-out files
        retry_timeout_multiplier: Multiplier for timeout on retries
        
    Returns:
        Updated TimeoutConfig
    """
    global _timeout_config
    
    if base_timeout is not None:
        _timeout_config.base_timeout = base_timeout
    if seconds_per_mb is not None:
        _timeout_config.seconds_per_mb = seconds_per_mb
    if max_timeout is not None:
        _timeout_config.max_timeout = max_timeout
    if pdf_multiplier is not None:
        _timeout_config.pdf_multiplier = pdf_multiplier
    if complex_pdf_threshold_mb is not None:
        _timeout_config.complex_pdf_threshold_mb = complex_pdf_threshold_mb
    if complex_pdf_multiplier is not None:
        _timeout_config.complex_pdf_multiplier = complex_pdf_multiplier
    if max_retries is not None:
        _timeout_config.max_retries = max_retries
    if retry_timeout_multiplier is not None:
        _timeout_config.retry_timeout_multiplier = retry_timeout_multiplier
    
    logger.info(
        f"Updated timeout config: base={_timeout_config.base_timeout}s, "
        f"per_mb={_timeout_config.seconds_per_mb}s, max={_timeout_config.max_timeout}s, "
        f"pdf_mult={_timeout_config.pdf_multiplier}, retries={_timeout_config.max_retries}"
    )
    return _timeout_config


def calculate_file_timeout(
    file_size_bytes: int,
    file_path: str = None,
    max_timeout_seconds: int = None,
    config: TimeoutConfig = None,
    retry_attempt: int = 0,
    concurrency_factor: float = 1.0,
) -> float:
    """
    Calculate adaptive timeout for a file based on its size and type.

    Formula: base_timeout + (size_in_mb * seconds_per_mb) * type_multiplier
    - Base timeout: 120 seconds minimum (configurable)
    - Per MB: 30 seconds per megabyte (configurable)
    - PDF multiplier: 1.5x for standard PDFs, 2x+ for complex PDFs
    - Max: 1800 seconds (30 minutes, configurable)
    - Retry multiplier: Each retry increases timeout
    - Concurrency factor: the CPU share of one conversion at the run's concurrency
      (:func:`conversion_budget_factor`); the formula describes a conversion with
      Docling's default 4 threads to itself

    Args:
        file_size_bytes: Size of the file in bytes
        file_path: Optional path to determine file type for multiplier
        max_timeout_seconds: Override for maximum timeout
        config: Optional TimeoutConfig (uses global if not provided)
        retry_attempt: Retry attempt number (0 = first try)
        concurrency_factor: Multiplier >= 1 for the concurrency of the run (1 = no scaling);
            applied before the ``max_timeout`` cap, which stays absolute

    Returns:
        Timeout in seconds
    """
    cfg = config or _timeout_config
    max_timeout = max_timeout_seconds or cfg.max_timeout
    
    size_mb = file_size_bytes / (1024 * 1024)
    calculated_timeout = cfg.base_timeout + (size_mb * cfg.seconds_per_mb)
    
    # Apply type-specific multipliers
    if file_path:
        ext = os.path.splitext(file_path)[1].lower()
        
        if ext == '.pdf':
            # PDFs need more time due to OCR potential
            calculated_timeout *= cfg.pdf_multiplier
            
            # Complex PDFs (larger than threshold) get even more time
            if size_mb >= cfg.complex_pdf_threshold_mb:
                calculated_timeout *= cfg.complex_pdf_multiplier
                logger.debug(
                    f"Complex PDF detected ({size_mb:.1f}MB): "
                    f"timeout={calculated_timeout:.0f}s"
                )
        
        elif ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.tif'):
            # Images need extra time for OCR processing
            calculated_timeout *= cfg.image_multiplier
            logger.debug(
                f"Image file detected ({size_mb:.1f}MB): "
                f"timeout={calculated_timeout:.0f}s (OCR multiplier)"
            )
        
        elif ext in ('.mp3', '.wav', '.m4a', '.flac', '.ogg'):
            # Audio files need extra time for transcription
            calculated_timeout *= cfg.audio_multiplier
            logger.debug(
                f"Audio file detected ({size_mb:.1f}MB): "
                f"timeout={calculated_timeout:.0f}s (transcription multiplier)"
            )
    
    # Apply retry multiplier for subsequent attempts
    if retry_attempt > 0:
        retry_multiplier = cfg.retry_timeout_multiplier ** retry_attempt
        calculated_timeout *= retry_multiplier
        logger.info(
            f"Retry {retry_attempt}: timeout increased to {calculated_timeout:.0f}s "
            f"(multiplier: {retry_multiplier:.1f}x)"
        )

    # Budget for the CPU share of one conversion at this concurrency (conversion_budget_factor)
    if concurrency_factor and concurrency_factor > 1.0:
        calculated_timeout *= concurrency_factor

    return min(calculated_timeout, max_timeout)


@dataclass
class AcceleratorConfig:
    """Configuration for GPU/CUDA acceleration in document processing."""
    device: str = "auto"  # "auto", "cpu", "cuda", "mps"
    cuda_device_id: int = 0  # CUDA device ID when using GPU
    enable_ocr_acceleration: bool = True  # Enable GPU for OCR models
    torch_dtype: str = "float16"  # "float16", "float32", "bfloat16"


# Global accelerator config
_accelerator_config = AcceleratorConfig()


def get_accelerator_config() -> AcceleratorConfig:
    """Get current accelerator configuration."""
    return _accelerator_config


def update_accelerator_config(
    device: str = None,
    cuda_device_id: int = None,
    enable_ocr_acceleration: bool = None,
    torch_dtype: str = None
) -> AcceleratorConfig:
    """
    Update accelerator configuration values.
    
    Args:
        device: Device to use - "auto", "cpu", "cuda", "mps"
        cuda_device_id: CUDA device ID (for multi-GPU systems)
        enable_ocr_acceleration: Whether to use GPU for OCR
        torch_dtype: Torch data type for models
        
    Returns:
        Updated AcceleratorConfig
    """
    global _accelerator_config
    
    if device is not None:
        _accelerator_config.device = device
    if cuda_device_id is not None:
        _accelerator_config.cuda_device_id = cuda_device_id
    if enable_ocr_acceleration is not None:
        _accelerator_config.enable_ocr_acceleration = enable_ocr_acceleration
    if torch_dtype is not None:
        _accelerator_config.torch_dtype = torch_dtype
    
    logger.info(
        f"Updated accelerator config: device={_accelerator_config.device}, "
        f"cuda_id={_accelerator_config.cuda_device_id}, ocr_accel={_accelerator_config.enable_ocr_acceleration}"
    )
    return _accelerator_config


# ---------------------------------------------------------------------------
# Parallel conversions: thread pool size and CPU threads per model
# ---------------------------------------------------------------------------
#
# The pipeline hashes, converts (Docling) and discovers files on one shared thread
# pool. Its size follows the performance config (`thread_pool_workers`, Configuration
# page) and is never smaller than the file concurrency of a run, so `max_concurrent_files`
# conversions really run at once (it was fixed at 2 until 2026-10-23, which capped every
# run at two conversions whatever the config said). The CPU threads of the Docling
# models (layout, TableFormer) and of each Tesseract process are shared across the pool.

MAX_THREAD_POOL_WORKERS = 32
DEFAULT_THREAD_POOL_WORKERS = 4
MAX_DOCLING_THREADS = 4
_pool_workers_in_force: int = 0


def available_cpu_count() -> int:
    """CPUs this process may run on (affinity aware where the platform reports it)."""
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def _positive_int_env(*names: str) -> Optional[int]:
    """The first positive integer among the environment variables ``names``."""
    for name in names:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            continue
        try:
            value = int(raw)
        except ValueError:
            logger.warning(f"Ignoring non-numeric {name}={raw!r}")
            continue
        if value > 0:
            return value
    return None


def _positive_float_env(*names: str) -> Optional[float]:
    """The first positive number among the environment variables ``names``."""
    for name in names:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            continue
        try:
            value = float(raw)
        except ValueError:
            logger.warning(f"Ignoring non-numeric {name}={raw!r}")
            continue
        if value > 0:
            return value
    return None


def default_thread_pool_workers() -> int:
    """Pool size of a run that passes none: ``INGEST_THREAD_POOL_WORKERS`` or 4."""
    return min(
        MAX_THREAD_POOL_WORKERS,
        _positive_int_env("INGEST_THREAD_POOL_WORKERS") or DEFAULT_THREAD_POOL_WORKERS,
    )


def resolve_thread_pool_workers(configured: Optional[int], max_concurrent_files: int = 1) -> int:
    """Pool size for a run: ``configured`` (``thread_pool_workers``), never below the file concurrency.

    Args:
        configured: The performance config's ``thread_pool_workers``; the default when None/0.
        max_concurrent_files: File concurrency of the run.

    Returns:
        The pool size, 1..MAX_THREAD_POOL_WORKERS.
    """
    base = int(configured) if configured else default_thread_pool_workers()
    return max(1, min(MAX_THREAD_POOL_WORKERS, max(base, int(max_concurrent_files or 1))))


def docling_num_threads(pool_workers: Optional[int] = None) -> int:
    """CPU threads per Docling model (layout, TableFormer) for the pool size in force.

    ``INGEST_DOCLING_THREADS`` (or Docling's own ``OMP_NUM_THREADS``) pins it; otherwise
    the cores are shared evenly across the pool, at most Docling's default of 4.

    Args:
        pool_workers: Pool size to size for; the pool in force (or the default) when omitted.

    Returns:
        Threads per model, at least 1.
    """
    pinned = _positive_int_env("INGEST_DOCLING_THREADS", "OMP_NUM_THREADS")
    if pinned:
        return pinned
    pool = pool_workers or _pool_workers_in_force or default_thread_pool_workers()
    return max(1, min(MAX_DOCLING_THREADS, available_cpu_count() // max(1, pool)))


def conversion_budget_factor(
    parallel_conversions: int,
    threads_per_conversion: Optional[int] = None,
    cpu_count: Optional[int] = None,
) -> float:
    """How much longer one conversion may take when ``parallel_conversions`` share the CPU.

    The timeout formula (:func:`calculate_file_timeout`) describes a conversion that has
    Docling's default 4 threads to itself. With 16 conversions on 20 cores each gets one
    thread (:func:`docling_num_threads`, the Tesseract OpenMP limit), and scans that
    converted within their budget at concurrency 2 timed out at 16 (F1 of the 2026-10-24
    prompt-test log). The factor is the thread share lost (4 / threads) times the
    oversubscription of the cores (conversions * threads / cores), never below 1. The
    ``max_timeout`` cap is not scaled.

    Args:
        parallel_conversions: Conversions running at once (``max_concurrent_files``).
        threads_per_conversion: CPU threads each conversion has; the Docling thread count
            for that many parallel conversions when omitted.
        cpu_count: Cores available; detected when omitted.

    Returns:
        A multiplier >= 1.0 for the per-file budget.
    """
    parallel = max(1, int(parallel_conversions))
    threads = max(1, int(threads_per_conversion or docling_num_threads(parallel)))
    cores = max(1, int(cpu_count if cpu_count is not None else available_cpu_count()))
    thin_share = MAX_DOCLING_THREADS / threads
    oversubscription = (parallel * threads) / cores
    return max(1.0, thin_share) * max(1.0, oversubscription)


def docling_accelerator_options(device: Optional[str] = None, num_threads: Optional[int] = None):
    """Docling ``AcceleratorOptions`` for ``device`` (the effective device when omitted).

    Always a real options object: a plain dict assigned to
    ``pipeline_options.accelerator_options`` is not coerced by pydantic, and Docling
    then failed every conversion with ``'dict' object has no attribute 'device'`` as
    soon as a GPU was visible to the container (found 2026-10-23).

    Args:
        device: ``cpu``, ``cuda``, ``mps`` or ``auto``; ``cuda`` honours the configured
            ``cuda_device_id`` (``cuda:N``).
        num_threads: CPU threads per model; :func:`docling_num_threads` when omitted.

    Returns:
        A ``docling.datamodel.accelerator_options.AcceleratorOptions``.
    """
    from docling.datamodel.accelerator_options import AcceleratorOptions

    device = device or get_effective_device()
    if device == "cuda" and _accelerator_config.cuda_device_id > 0:
        device = f"cuda:{_accelerator_config.cuda_device_id}"
    return AcceleratorOptions(device=device, num_threads=num_threads or docling_num_threads())


# ---------------------------------------------------------------------------
# OCR engine configuration (Docling; scanned PDFs and image files)
# ---------------------------------------------------------------------------

OCR_ENGINE_AUTO = "auto"
SUPPORTED_OCR_ENGINES: Tuple[str, ...] = (OCR_ENGINE_AUTO, "rapidocr", "tesseract", "easyocr")
DEFAULT_OCR_ENGINE = "tesseract"
DEFAULT_OCR_LANGUAGES = "deu,eng"
# Resolution (dots per inch) at which PDF pages are rasterised for the OCR engine.
# Docling's own default is ``scale=3.0`` = 216 dpi; Tesseract is trained on ~300 dpi text.
DEFAULT_OCR_SCAN_DPI = 216
# Megapixel budget of the OCR bitmap for image files (JPG/PNG/TIF). Docling renders
# images at 3x their size in points, i.e. a phone photo without a DPI tag at 3x its
# pixel size (~140 Mpx for a 12 Mpx photo, 20-60 s of Tesseract time). ``0`` = no cap.
DEFAULT_OCR_IMAGE_MAX_MPX = 8
_DOCLING_DEFAULT_OCR_SCALE = 3.0
# Tesseract page segmentation mode per OCR rectangle; ``None`` = Tesseract's default (3).
DEFAULT_OCR_PSM: Optional[int] = None
# Minimum Tesseract OSD "Orientation confidence" before a detected rotation is applied
# to an OCR rectangle outright; below it the rotation is verified by OCRing both ways
# (src/ingestion/ocr_tesseract.py). ``0`` = apply any (Docling's behaviour). On the
# 2026-10-16 scale sample every rotation proposed below 1 on upright pages was wrong, an
# upright Courier page was flipped at 3.99, genuine rotations score >= 10.
DEFAULT_OCR_OSD_MIN_CONFIDENCE = 10.0
# Dilation (page points) Docling applies to the layout clusters before merging the ones that
# touch into one OCR rectangle. Docling's 20 merges stacked tables (gaps of ~24 pt) into one
# crop, on which Tesseract's segmentation drops whole columns of dates and amounts.
DEFAULT_OCR_RECT_DILATION = 20
_OCR_SCALE_MIN, _OCR_SCALE_MAX = 0.25, 8.0
_DOCLING_POINTS_PER_INCH = 72.0
# Image files whose OCR output is noise (a photo of a car, a building site) are stored
# without text instead of polluting the index with letter soup. The gate is a plausibility
# check on the words plus Docling's OCR confidence; photographed documents pass it.
DEFAULT_OCR_IMAGE_NOISE_GATE = True
_NOISE_MIN_WORD_RATIO = 0.45  # share of tokens that look like words (>= 3 letters, a vowel)
_NOISE_SURE_WORD_RATIO = 0.25  # below this the output is soup whatever the confidence says
_NOISE_MAX_OCR_SCORE = 0.65  # Docling per-page OCR confidence below which soup is dropped
_WORDLIKE_TOKEN_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+")
_VOWEL_RE = re.compile(r"[aeiouyäöüAEIOUYÄÖÜ]")


def image_noise_gate_enabled() -> bool:
    """Whether image OCR output is checked for noise (``OCR_IMAGE_NOISE_GATE``, default on)."""
    raw = os.environ.get("OCR_IMAGE_NOISE_GATE")
    if raw is None:
        try:
            raw = str(load_settings(use_profile=False).ocr_image_noise_gate)
        except Exception:  # settings need MONGODB_URI etc.; the harness has none
            raw = None
    if raw is None:
        return DEFAULT_OCR_IMAGE_NOISE_GATE
    return raw.strip().lower() in ("1", "true", "yes", "on")


def image_text_quality(text: str) -> Tuple[int, float]:
    """Token count and the share of tokens that look like words.

    Args:
        text: OCR output of an image.

    Returns:
        ``(tokens, wordlike_ratio)``; the ratio is 0 for an empty text.
    """
    tokens = _WORDLIKE_TOKEN_RE.findall(text or "")
    if not tokens:
        return 0, 0.0
    wordlike = sum(1 for t in tokens if len(t) >= 3 and _VOWEL_RE.search(t))
    return len(tokens), wordlike / len(tokens)


def image_text_is_noise(text: str, ocr_score: Optional[float]) -> bool:
    """Decide whether an image's OCR output is letter soup rather than text.

    Calibrated on 141 phone photos of the 2026-10-16 scale sample: photos without text
    come back with a word-like token ratio below ~0.4 and an OCR confidence below ~0.5,
    photographed letters, forms and screenshots above 0.6 on both.

    Args:
        text: OCR output (Markdown) of the image.
        ocr_score: Docling's OCR confidence for the page (0-1), ``None`` if unknown.

    Returns:
        ``True`` when the text should not be indexed.
    """
    tokens, ratio = image_text_quality(text)
    if tokens == 0:
        return True
    if ratio < _NOISE_SURE_WORD_RATIO:
        return True
    low_score = ocr_score is None or ocr_score < _NOISE_MAX_OCR_SCORE
    return ratio < _NOISE_MIN_WORD_RATIO and low_score


def conversion_ocr_score(result: Any) -> Optional[float]:
    """Mean of Docling's per-page OCR confidence scores of a conversion result, if any."""
    try:
        scores = [float(p.ocr_score) for p in result.confidence.pages.values()]
        scores = [s for s in scores if s == s]  # drop NaN
        return sum(scores) / len(scores) if scores else None
    except Exception:  # noqa: BLE001
        return None


# Docling's table-structure stage (TableFormer) turns detected tables into Markdown tables
# but consumes the OCR cells inside the table region and drops whatever it cannot place in
# a cell; on OCRed Honorarnote / Grundbuch tables that loses dates and amounts. With the
# stage off, table text is kept as plain text lines (row by row), nothing is dropped.
DEFAULT_INGEST_TABLE_STRUCTURE = True


def table_structure_enabled() -> bool:
    """Whether Docling's table-structure stage runs (``INGEST_TABLE_STRUCTURE``)."""
    raw = os.environ.get("INGEST_TABLE_STRUCTURE")
    if raw is None:
        try:
            raw = str(load_settings(use_profile=False).ingest_table_structure)
        except Exception:  # settings need MONGODB_URI etc.; the harness has none
            raw = None
    if raw is None:
        return DEFAULT_INGEST_TABLE_STRUCTURE
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Image-only PDFs (pure scans, no text layer) are OCRed like every other PDF. The skip is the
# legacy pre-check of the concurrent loop: it dropped ~30% of the diejuristin PDFs before OCR
# ran (docs/26-FORMAT_EDGE_AUDIT.md). It stays available for text-only profiles, where a scan
# can only cost OCR time.
DEFAULT_INGEST_SKIP_IMAGE_ONLY_PDF = False


def skip_image_only_pdf_enabled() -> bool:
    """Whether the concurrent loop skips image-only PDFs before OCR (``INGEST_SKIP_IMAGE_ONLY_PDF``)."""
    raw = os.environ.get("INGEST_SKIP_IMAGE_ONLY_PDF")
    if raw is None:
        try:
            raw = str(load_settings(use_profile=False).ingest_skip_image_only_pdf)
        except Exception:  # settings need MONGODB_URI etc.; the harness has none
            raw = None
    if raw is None:
        return DEFAULT_INGEST_SKIP_IMAGE_ONLY_PDF
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Convert files in killable worker processes (src/ingestion/conversion_workers.py). The
# performance config's `process_isolation_enabled` (Configuration page) is the switch; the
# API and the ingestion worker pass the stored value, the CLI falls back to the environment
# and the settings. Off, conversions run on the thread pool, where a timed-out conversion
# cannot be stopped and keeps its slot reserved until it ends.
DEFAULT_INGEST_PROCESS_ISOLATION = True


def process_isolation_enabled(configured: Optional[bool] = None) -> bool:
    """Whether the concurrent loop converts in worker processes it can kill on a timeout.

    Args:
        configured: The performance config's ``process_isolation_enabled`` when the caller has
            it; otherwise ``INGEST_PROCESS_ISOLATION``, then the settings default (on).

    Returns:
        True for process isolation, False for the thread pool.
    """
    if configured is not None:
        return bool(configured)
    raw = os.environ.get("INGEST_PROCESS_ISOLATION")
    if raw is None:
        try:
            raw = str(load_settings(use_profile=False).ingest_process_isolation)
        except Exception:  # settings need MONGODB_URI etc.; the harness has none
            raw = None
    if raw is None:
        return DEFAULT_INGEST_PROCESS_ISOLATION
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Per-file stats (`ingestion_stats`, `failed_documents`) are written in batches during a run:
# every N recorded files or every M seconds, whichever comes first, and once more at the end.
DEFAULT_STATS_FLUSH_FILES = 25
DEFAULT_STATS_FLUSH_SECONDS = 60.0


class UnreadableFileError(Exception):
    """A reader could not read the file: nothing is stored and the file is recorded as failed.

    Until 2026-10-26 the readers' ``[Error: Could not read file …]`` placeholder was stored,
    chunked and embedded as the document's content (F26 of the prompt-test log), so the file
    counted as ingested and a question naming it got the error string as its source.
    """


#: Reader placeholders that mean "could not read" (an unreadable file, a missing tool)
ERROR_PLACEHOLDER_PREFIXES: Tuple[str, ...] = ("[Error:", "[Skipped:")
#: Reader placeholders that mean "read fine, but there is no text in it"
EMPTY_PLACEHOLDER_PREFIX = "[Document:"
_PLACEHOLDER_MAX_CHARS = 400


def placeholder_kind(content: Optional[str]) -> Optional[str]:
    """Classify a reader's output: ``"error"`` / ``"empty"`` for a placeholder, else ``None``.

    A placeholder is a single bracketed line such as ``[Error: Could not read file x.pdf]``
    or ``[Document: x.rtf - no text content]``; real document text never looks like that.
    """
    text = (content or "").strip()
    if not text or len(text) > _PLACEHOLDER_MAX_CHARS or "\n" in text or not text.endswith("]"):
        return None
    if text.startswith(ERROR_PLACEHOLDER_PREFIXES):
        return "error"
    if text.startswith(EMPTY_PLACEHOLDER_PREFIX):
        return "empty"
    return None


#: Selective-retry flags of an ingestion request -> the recorded error type they ask to retry
RETRY_FLAG_ERROR_TYPES: Dict[str, str] = {
    "retry_timeouts": "timeout",
    "retry_errors": "error",
    "retry_no_chunks": "no_chunks",
    "retry_image_only_pdfs": "image_only_pdf",
}


def retry_error_types(config: Optional[Dict[str, Any]]) -> frozenset:
    """The error types a job's config asks to retry (its ``retry_*`` flags)."""
    if not config:
        return frozenset()
    return frozenset(error_type for flag, error_type in RETRY_FLAG_ERROR_TYPES.items() if config.get(flag))


async def failed_paths_of_job(db: Any, job_id: Optional[str], retry_types: frozenset = frozenset()) -> Dict[str, str]:
    """``file_path -> error_type`` of the failures a job recorded that its resume must not redo.

    A resumed job knows only the ``documents`` collection, so every file that timed out,
    errored or produced no chunks before the restart looked new and was processed again
    (F23: 553 timeouts on 451 files in the first 4 h after a resume). The failures the run
    wrote to ``failed_documents`` (F2) are skipped unless the job's ``retry_*`` flags ask for
    that error type or the record was resolved.

    Args:
        db: The profile's database (``failed_documents`` collection), or ``None``.
        job_id: The job being resumed.
        retry_types: Error types the job's config asks to retry (:func:`retry_error_types`).
    """
    if db is None or not job_id:
        return {}
    skip: Dict[str, str] = {}
    cursor = db["failed_documents"].find(
        {"job_id": job_id, "resolved": {"$ne": True}}, {"file_path": 1, "error_type": 1}
    )
    async for doc in cursor:
        error_type = doc.get("error_type") or "error"
        path = doc.get("file_path")
        if path and error_type not in retry_types:
            skip[path] = error_type
    return skip


def stats_flush_config() -> Tuple[int, float]:
    """``(files, seconds)`` between flushes of the per-file stats of a run.

    ``INGEST_STATS_FLUSH_FILES`` / ``INGEST_STATS_FLUSH_SECONDS`` win, then the settings
    (``ingest_stats_flush_files`` / ``ingest_stats_flush_seconds``), then 25 files / 60 s.

    Returns:
        Files per batch (>= 1) and seconds between time-based flushes (>= 1).
    """
    files = _positive_int_env("INGEST_STATS_FLUSH_FILES")
    seconds = _positive_float_env("INGEST_STATS_FLUSH_SECONDS")
    if files is None or seconds is None:
        try:
            configured = load_settings(use_profile=False)
        except Exception:  # settings need MONGODB_URI etc.; the harness has none
            configured = None
        if configured is not None:
            files = files or getattr(configured, "ingest_stats_flush_files", None)
            seconds = seconds or getattr(configured, "ingest_stats_flush_seconds", None)
    return (
        max(1, int(files or DEFAULT_STATS_FLUSH_FILES)),
        max(1.0, float(seconds or DEFAULT_STATS_FLUSH_SECONDS)),
    )


# Keep page headers and footers (Docling's "furniture" content layer) in the stored content
# and in the chunks. Docling drops them by default, which loses the Geschäftszahl header of
# court documents, ERV delivery stamps and the bank details in letter footers.
DEFAULT_INGEST_PAGE_FURNITURE = True


def include_page_furniture() -> bool:
    """Whether page headers/footers are kept (``INGEST_PAGE_FURNITURE``, default on)."""
    raw = os.environ.get("INGEST_PAGE_FURNITURE")
    if raw is None:
        try:
            raw = str(load_settings(use_profile=False).ingest_page_furniture)
        except Exception:  # settings need MONGODB_URI etc.; the harness has none
            raw = None
    if raw is None:
        return DEFAULT_INGEST_PAGE_FURNITURE
    return raw.strip().lower() in ("1", "true", "yes", "on")


def document_content_layers():
    """Docling content layers to export/chunk, or ``None`` for Docling's default (body only)."""
    if not include_page_furniture():
        return None
    from docling_core.types.doc import ContentLayer

    return {ContentLayer.BODY, ContentLayer.FURNITURE}


def export_document_markdown(document: Any) -> str:
    """Export a DoclingDocument to Markdown the way the ingestion pipeline stores it.

    Args:
        document: Converted ``DoclingDocument``.

    Returns:
        Markdown including page headers/footers when ``INGEST_PAGE_FURNITURE`` is on.
    """
    layers = document_content_layers()
    if layers is None:
        return document.export_to_markdown()
    return document.export_to_markdown(included_content_layers=layers)


# ---------------------------------------------------------------------------
# Email (.msg/.eml) attachment extraction
# ---------------------------------------------------------------------------
# By default emails store only the body, headers and a list of attachment filenames; the
# attached PDFs/scans/documents - which in a law-firm corpus routinely carry the substantive
# content - are not opened. When MSG_EXTRACT_ATTACHMENTS is on, each substantive attachment is
# written to a temp file and run back through the normal document reader (PDF -> OCR,
# .doc -> antiword, .docx -> Docling, images -> OCR) and its text appended to the email content.

DEFAULT_MSG_EXTRACT_ATTACHMENTS = False
DEFAULT_MSG_ATTACHMENT_MAX_MB = 25
DEFAULT_MSG_ATTACHMENT_MAX_COUNT = 20

# Attachment names/extensions that never carry substantive content.
_EMAIL_ATTACHMENT_NOISE_NAME = re.compile(r"(?:smime\.p7[sm]|winmail\.dat)$", re.I)
_EMAIL_ATTACHMENT_SKIP_EXT = frozenset({".p7s", ".p7m", ".p7c", ".asc", ".sig", ".ics", ".vcf"})
# Nested emails and media are not extracted from attachments (recursion / size / broken paths).
_EMAIL_ATTACHMENT_AUDIO_VIDEO = frozenset({
    ".mp3", ".wav", ".m4a", ".flac", ".mp4", ".avi", ".mkv", ".mov", ".webm", ".wmv",
})
_EMAIL_INLINE_IMAGE_EXT = frozenset({".png", ".gif", ".jpg", ".jpeg", ".bmp", ".webp"})
_EMAIL_INLINE_IMAGE_MIN_BYTES = 50 * 1024  # smaller images are treated as logos/signatures
_EMAIL_ATTACHMENT_PLACEHOLDER_RE = re.compile(r"^\s*\[(?:Video|Image|Skipped|Error|Empty email):", re.I)


def email_attachment_text_extensions() -> frozenset:
    """Attachment extensions whose text is extracted (documents + images, not email/media)."""
    return frozenset(SUPPORTED_INGESTION_EXTENSIONS) - {".msg", ".eml"} - _EMAIL_ATTACHMENT_AUDIO_VIDEO


def resolve_msg_attachment_config() -> Tuple[bool, int, int]:
    """Resolve ``(enabled, max_bytes, max_count)`` for email attachment extraction.

    Resolution order per value: environment (``MSG_EXTRACT_ATTACHMENTS``,
    ``MSG_ATTACHMENT_MAX_MB``, ``MSG_ATTACHMENT_MAX_COUNT``), then ``src.settings.Settings``,
    then the module defaults. A bad number is logged and replaced by its default.

    Returns:
        ``(enabled, max_bytes, max_count)``; ``max_bytes``/``max_count`` of 0 means no cap.
    """
    enabled_raw = os.environ.get("MSG_EXTRACT_ATTACHMENTS")
    mb_raw = os.environ.get("MSG_ATTACHMENT_MAX_MB")
    count_raw = os.environ.get("MSG_ATTACHMENT_MAX_COUNT")
    if enabled_raw is None or mb_raw is None or count_raw is None:
        try:
            settings = load_settings(use_profile=False)
            if enabled_raw is None:
                enabled_raw = str(settings.msg_extract_attachments)
            if mb_raw is None:
                mb_raw = str(settings.msg_attachment_max_mb)
            if count_raw is None:
                count_raw = str(settings.msg_attachment_max_count)
        except Exception:  # settings need MONGODB_URI etc.; the harness has none
            pass
    enabled = (enabled_raw or str(DEFAULT_MSG_EXTRACT_ATTACHMENTS)).strip().lower() in (
        "1", "true", "yes", "on",
    )
    max_mb = _parse_optional_int(mb_raw, "MSG_ATTACHMENT_MAX_MB")
    max_mb = DEFAULT_MSG_ATTACHMENT_MAX_MB if max_mb is None or max_mb < 0 else max_mb
    max_count = _parse_optional_int(count_raw, "MSG_ATTACHMENT_MAX_COUNT")
    max_count = DEFAULT_MSG_ATTACHMENT_MAX_COUNT if max_count is None or max_count < 0 else max_count
    return enabled, max_mb * 1024 * 1024, max_count


def extract_email_attachment_text(
    attachments: List[Tuple[str, bytes]],
    reader: Callable[[str], Tuple[str, Any]],
    max_bytes: int,
    max_count: int,
) -> List[str]:
    """Extract text from email attachments by reusing the document reader.

    Each attachment is written to a temporary file with its own extension and passed to
    ``reader`` (normally :meth:`DocumentIngestionPipeline._read_document`), so PDFs are OCRed,
    ``.doc`` goes through antiword, ``.docx`` through Docling and images through OCR. Noise
    attachments (``smime.p7s``, ``winmail.dat``, signatures, calendar/contact files, tiny inline
    logo images), nested emails, audio/video, unsupported types and oversized files are skipped.
    One bad attachment never fails the email: each is isolated in a ``try``/``except``.

    Args:
        attachments: ``(filename, data)`` pairs; empty/non-bytes data is skipped.
        reader: Callable returning ``(markdown_text, docling_doc)`` for a file path.
        max_bytes: Skip attachments whose data exceeds this size (0 = no limit).
        max_count: Extract from at most this many attachments (0 = no limit).

    Returns:
        Markdown blocks (``### Attachment: <name>`` + text), one per attachment with usable text.
    """
    import tempfile

    blocks: List[str] = []
    allowed = email_attachment_text_extensions()
    processed = 0
    for name, data in attachments:
        if max_count and processed >= max_count:
            break
        name = name or "unnamed"
        ext = os.path.splitext(name)[1].lower()
        if not isinstance(data, (bytes, bytearray)) or not data:
            continue
        if _EMAIL_ATTACHMENT_NOISE_NAME.search(name) or ext in _EMAIL_ATTACHMENT_SKIP_EXT:
            continue
        if ext not in allowed:
            continue
        if ext in _EMAIL_INLINE_IMAGE_EXT and len(data) < _EMAIL_INLINE_IMAGE_MIN_BYTES:
            continue  # logo / signature image, not a document scan
        if max_bytes and len(data) > max_bytes:
            logger.info(
                f"Skipping large email attachment {name} "
                f"({len(data) / 1024 / 1024:.1f}MB exceeds cap)"
            )
            continue
        processed += 1
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix=ext, prefix="recallhub-att-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            text, _doc = reader(tmp)
            stripped = (text or "").strip()
            if stripped and len(stripped) > 10 and not _EMAIL_ATTACHMENT_PLACEHOLDER_RE.match(stripped):
                blocks.append(f"### Attachment: {name}\n\n{stripped}")
            else:
                logger.debug(f"Email attachment {name} produced no usable text")
        except Exception as exc:  # noqa: BLE001 - one bad attachment must not fail the email
            logger.warning(f"Failed to extract email attachment {name}: {exc}")
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return blocks


# ISO 639-2/T codes (= Tesseract traineddata names) -> EasyOCR two-letter codes.
# Two-letter input is accepted as well and normalised to the three-letter form.
_OCR_LANGUAGE_CODES: Dict[str, str] = {
    "deu": "de", "eng": "en", "fra": "fr", "ita": "it", "spa": "es", "nld": "nl",
    "por": "pt", "pol": "pl", "ces": "cs", "slk": "sk", "slv": "sl", "hrv": "hr",
    "hun": "hu", "ron": "ro", "tur": "tr", "rus": "ru", "ukr": "uk", "dan": "da",
    "swe": "sv", "nor": "no", "fin": "fi", "ell": "el", "bul": "bg", "srp": "rs_latin",
    "lat": "la",
}
_OCR_LANGUAGE_ALIASES: Dict[str, str] = {
    **{two: three for three, two in _OCR_LANGUAGE_CODES.items() if len(two) == 2},
    "ger": "deu", "german": "deu", "english": "eng", "fre": "fra", "french": "fra",
    "dut": "nld", "cze": "ces", "rum": "ron", "gre": "ell", "scc": "srp",
}


@dataclass(frozen=True)
class OcrConfig:
    """Resolved OCR engine selection for Docling's PDF/image pipelines.

    Attributes:
        engine: One of ``SUPPORTED_OCR_ENGINES``. ``auto`` keeps Docling's own
            ``OcrAutoOptions`` behaviour (today that resolves to RapidOCR).
        languages: ISO 639-2 codes in priority order, e.g. ``("deu", "eng")``.
            Mapped to the engine's own codes by :func:`build_ocr_options`.
    """

    engine: str = DEFAULT_OCR_ENGINE
    languages: Tuple[str, ...] = ("deu", "eng")
    scan_dpi: int = DEFAULT_OCR_SCAN_DPI
    image_max_mpx: int = DEFAULT_OCR_IMAGE_MAX_MPX
    psm: Optional[int] = DEFAULT_OCR_PSM
    tessdata_path: Optional[str] = None
    osd_min_confidence: float = DEFAULT_OCR_OSD_MIN_CONFIDENCE
    rect_dilation: int = DEFAULT_OCR_RECT_DILATION

    def describe(self) -> str:
        """Short human-readable form for logs."""
        if self.engine == OCR_ENGINE_AUTO:
            return "auto (Docling default)"
        extras: List[str] = []
        if self.scan_dpi != DEFAULT_OCR_SCAN_DPI:
            extras.append(f"scan_dpi={self.scan_dpi}")
        if self.image_max_mpx != DEFAULT_OCR_IMAGE_MAX_MPX:
            extras.append(f"image_max_mpx={self.image_max_mpx}")
        if self.psm is not None:
            extras.append(f"psm={self.psm}")
        if self.tessdata_path:
            extras.append(f"tessdata={self.tessdata_path}")
        if self.osd_min_confidence != DEFAULT_OCR_OSD_MIN_CONFIDENCE:
            extras.append(f"osd_min_conf={self.osd_min_confidence:g}")
        if self.rect_dilation != DEFAULT_OCR_RECT_DILATION:
            extras.append(f"rect_dilation={self.rect_dilation}")
        suffix = (" " + " ".join(extras)) if extras else ""
        if self.engine == "rapidocr":
            return f"rapidocr (languages not configurable){suffix}"
        return f"{self.engine} lang={'+'.join(self.languages)}{suffix}"

    def pdf_scale(self) -> float:
        """Docling ``ocr_options.scale`` for PDF pages (points -> OCR pixels)."""
        return _clamp_scale(self.scan_dpi / _DOCLING_POINTS_PER_INCH)

    def image_scale(self, width_px: int, height_px: int, dpi: Optional[float]) -> Optional[float]:
        """Docling ``ocr_options.scale`` for an image file, or ``None`` to keep Docling's default.

        Docling sizes an image page in points from its DPI tag (72 dpi when the tag is
        missing) and renders the OCR bitmap at ``scale`` (3.0) pixels per point, so a
        12 Mpx phone photo without a DPI tag becomes a ~140 Mpx bitmap and costs 20-60 s
        of Tesseract time. The scale returned here keeps Docling's 3.0 unless the bitmap
        would exceed ``image_max_mpx`` megapixels, in which case it is reduced so the
        bitmap lands on that budget. A 300-dpi A4 scan image (4.5 Mpx at scale 3) is not
        affected; scan images are measurably worse at other scales.

        Args:
            width_px: Image width in pixels.
            height_px: Image height in pixels.
            dpi: DPI tag of the image, if any.

        Returns:
            Scale factor, or ``None`` when ``image_max_mpx`` is 0 (no cap).
        """
        if self.image_max_mpx <= 0:
            return None
        effective_dpi = dpi if dpi and dpi > 1 else _DOCLING_POINTS_PER_INCH
        width_pt = max(width_px, 1) * _DOCLING_POINTS_PER_INCH / effective_dpi
        height_pt = max(height_px, 1) * _DOCLING_POINTS_PER_INCH / effective_dpi
        budget_scale = (self.image_max_mpx * 1_000_000 / (width_pt * height_pt)) ** 0.5
        return _clamp_scale(min(_DOCLING_DEFAULT_OCR_SCALE, budget_scale))


def _clamp_scale(scale: float) -> float:
    return round(min(max(scale, _OCR_SCALE_MIN), _OCR_SCALE_MAX), 4)


def prepare_image_for_docling(image_path: str) -> Tuple[str, Optional[str]]:
    """Return a path Docling's image backend can load, re-saving the file when it cannot.

    Docling rejects two kinds of files that phones and cameras produce routinely: JPEGs
    whose DPI tag is ``(0, 0)`` (screenshots, some Apple exports; ``_validate_dpi`` raises)
    and MPO multi-picture containers (Canon). Both are re-saved as a plain PNG without a DPI
    tag in a temporary file; everything else is passed through untouched.

    Args:
        image_path: The image file about to be converted.

    Returns:
        ``(path_to_convert, temp_path)``; ``temp_path`` is ``None`` when the original is used
        and must otherwise be removed by the caller after the conversion.
    """
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            dpi = img.info.get("dpi")
            # TIFF DPI tags are PIL IFDRational values: coerce instead of rejecting the type
            # (every TIFF used to be re-saved needlessly, docs/26)
            values = [_dpi_number(v) for v in dpi] if isinstance(dpi, tuple) and len(dpi) == 2 else None
            bad_dpi = dpi is not None and (values is None or any(v is None or v <= 0 or v != v for v in values))
            if img.format != "MPO" and not bad_dpi:
                return image_path, None
            import tempfile

            fd, tmp = tempfile.mkstemp(suffix=".png", prefix="recallhub-img-")
            os.close(fd)
            frame = img.convert("RGB") if img.mode not in ("L", "RGB") else img.copy()
            frame.save(tmp, "PNG")
            logger.info(
                f"Re-saved {os.path.basename(image_path)} for OCR ({img.format}, dpi tag {dpi}): "
                "Docling cannot load it as is"
            )
            return tmp, tmp
    except Exception as exc:  # noqa: BLE001 - let Docling report the real problem
        logger.debug(f"Image pre-check failed for {image_path}: {exc}")
        return image_path, None


def _dpi_number(value: Any) -> Optional[float]:
    """A DPI tag component as a float (``IFDRational`` included), or ``None`` when not numeric."""
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


def image_frame_count(image_path: str) -> int:
    """Number of frames (pages) of an image file; 1 for single-frame formats or on error."""
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            return max(1, int(getattr(img, "n_frames", 1)))
    except Exception as exc:  # noqa: BLE001 - the converter will report the real error
        logger.debug(f"Could not count frames of {image_path}: {exc}")
        return 1


def rtf_code_page(raw: bytes) -> str:
    """The Python codec of an RTF document's ``\\ansicpg`` code page (``cp1252`` when absent)."""
    match = re.search(rb"\\ansicpg(\d+)", raw[:2048])
    if match:
        return f"cp{match.group(1).decode('ascii')}"
    return "cp1252"


def markdown_table(rows: List[List[Any]]) -> str:
    """Render rows of cells as a Markdown table (first row = header); empty for no rows."""
    if not rows:
        return ""

    def cell(value: Any) -> str:
        text = "" if value is None else str(value)
        return text.replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()

    width = max(len(row) for row in rows)
    lines = []
    for index, row in enumerate(rows):
        cells = [cell(v) for v in row] + [""] * (width - len(row))
        lines.append("| " + " | ".join(cells) + " |")
        if index == 0:
            lines.append("|" + "---|" * width)
    return "\n".join(lines)


#: Files of the DOS-era text system behind the ``Pittner Archiv`` / ``M U S T E R`` folders of
#: Austrian law practices (docs/26): cp437 letters inside ``\xe1`` markers, inline formatting
#: bytes and HP PCL printer escapes. ``.dss`` is also Olympus dictation audio, detected by magic.
LEGACY_TEXT_EXTENSIONS: frozenset[str] = frozenset({".as", ".ass", ".ds", ".eb", ".do", ".dos", ".3", ".dss"})
_DSS_AUDIO_MAGIC = b"\x02dss"
_APPLEDOUBLE_PREFIX = "._"  # macOS resource forks copied onto SMB shares; never documents


def is_sidecar_file(filename: str) -> bool:
    """Whether ``filename`` is a macOS AppleDouble resource fork (``._name``), never a document."""
    return filename.startswith(_APPLEDOUBLE_PREFIX)


def walk_document_files(folder: str, extensions: frozenset) -> Iterator[Tuple[str, int]]:
    """Yield ``(path, size)`` for every supported file below ``folder`` in one pass.

    Uses ``os.scandir`` and takes each file's size right after its directory was listed:
    on CIFS/SMB mounts the kernel fills the attribute cache from the directory query, so
    that ``stat`` is answered locally, while sizing the files after the whole walk (as the
    pipeline did until 2026-10-24) sent one network round trip per file, ~20 min for 400K
    files on every start and resume. AppleDouble ``._`` forks and symlinks are skipped;
    unreadable directories are logged and skipped.

    Args:
        folder: Root directory (an existing path).
        extensions: Lower-case extensions to include, with the dot.

    Yields:
        The file path and its size in bytes (0 when it cannot be read).
    """
    stack = [folder]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    if is_sidecar_file(entry.name):
                        continue
                    if os.path.splitext(entry.name)[1].lower() not in extensions:
                        continue
                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        size = 0
                    yield entry.path, size
        except OSError as exc:
            logger.warning(f"Cannot list {current}: {exc}")


def legacy_dos_text(raw: bytes) -> str:
    """Readable text of a DOS word-processor file of the ``Pittner Archiv`` kind.

    Rules derived from the files on a law firm's NAS (docs/26): non-ASCII letters are
    cp437 bytes wrapped in ``\\xe1`` markers (``f\\xe1\\x81\\xe1r`` = ``für``), ``\\xa9``
    joins hyphenated names, ``ESC`` starts an HP PCL printer command (parameterised ones
    end with a capital letter), and every other byte above 0x7E or control byte except
    tab, CR and LF is formatting and dropped.

    Args:
        raw: The file's bytes.

    Returns:
        The text with runs of blank lines collapsed; empty when nothing readable is left.
    """
    parts: List[str] = []
    i, n = 0, len(raw)
    while i < n:
        b = raw[i]
        if b == 0x1B:
            j = i + 1
            if j < n and 0x21 <= raw[j] <= 0x2F:  # parameterised PCL command: ends with A..Z
                while j < n and j - i < 32 and not (0x41 <= raw[j] <= 0x5A):
                    j += 1
            i = j + 1
            continue
        if b == 0xE1 and i + 2 < n and raw[i + 2] == 0xE1:
            parts.append(bytes([raw[i + 1]]).decode("cp437", errors="replace"))
            i += 3
            continue
        if b == 0xA9:
            parts.append("-")
        elif b in (0x09, 0x0A, 0x0D) or 0x20 <= b <= 0x7E:
            parts.append(chr(b))
        i += 1
    text = "".join(parts).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sniff_text_bytes(raw: bytes, prefer_legacy: bool = False) -> Tuple[str, str]:
    """Classify a text-like file's bytes: ``("rtf"|"html"|"text"|"legacy", decoded text)``.

    ``rtf`` and ``html`` return the raw text (the caller converts it), ``text`` the UTF-8
    (BOM aware) or cp1252 decoding, ``legacy`` the :func:`legacy_dos_text` rendering. A file
    is ``legacy`` when it carries printer escapes or ``\\xe1`` letter markers, when more
    than 2 % of its bytes are control bytes, or when ``prefer_legacy`` is set (files of the
    DOS text system that happen to be plain ASCII decode the same way).

    Args:
        raw: The file's bytes.
        prefer_legacy: Treat anything that is not RTF or HTML as the DOS text format.

    Returns:
        The kind and the decoded text.
    """
    head = raw.lstrip()[:16]
    if head.startswith(b"{\\rtf"):
        return "rtf", raw.decode("latin-1")
    if head[:1] == b"<":
        return "html", decode_html_bytes(raw)
    sample = raw[:8192]
    controls = sum(1 for b in sample if (b < 0x20 and b not in (0x09, 0x0A, 0x0D)) or b == 0x7F)
    legacy = (
        prefer_legacy
        or b"\x1b" in sample
        or re.search(rb"\xe1[\x80-\xff]\xe1", sample) is not None
        or (sample and controls / len(sample) > 0.02)
    )
    if legacy:
        return "legacy", legacy_dos_text(raw)
    try:
        return "text", raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "text", raw.decode("cp1252", errors="replace")


#: Office template containers and the document content type Docling recognises them by.
_OOXML_TEMPLATE_TYPES: Dict[str, Tuple[str, str]] = {
    ".dotx": ("wordprocessingml.template.main+xml", "wordprocessingml.document.main+xml"),
    ".xltx": ("spreadsheetml.template.main+xml", "spreadsheetml.sheet.main+xml"),
    ".potx": ("presentationml.template.main+xml", "presentationml.presentation.main+xml"),
}


def ooxml_template_as_document(src: str, dst: str) -> None:
    """Write the Office template ``src`` (.dotx/.xltx/.potx) to ``dst`` as the matching document.

    A template is the same zip as the document except for the main part's content type in
    ``[Content_Types].xml``, which Docling uses to recognise the format (a renamed copy is
    rejected as an unknown format). Only that entry is rewritten.

    Args:
        src: The template file.
        dst: Destination path (``.docx``/``.xlsx``/``.pptx``).
    """
    import zipfile

    template_type, document_type = _OOXML_TEMPLATE_TYPES[os.path.splitext(src)[1].lower()]
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(template_type.encode("utf-8"), document_type.encode("utf-8"))
            zout.writestr(item, data)


def decode_text_payload(payload: bytes, declared: Optional[str]) -> str:
    """Decode a MIME text payload: valid UTF-8 wins, then the declared charset, then cp1252.

    Saved web pages often declare ``iso-8859-1`` in the MIME header while their bytes are
    UTF-8 (the page's own ``<meta charset>``); decoding with the declared charset turns
    every umlaut into ``Ã¤``.

    Args:
        payload: The decoded-transfer-encoding bytes of the part.
        declared: The part's ``charset`` parameter, if any.

    Returns:
        The text.
    """
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    if declared:
        try:
            return payload.decode(declared)
        except (UnicodeDecodeError, LookupError):
            pass
    return payload.decode("cp1252", errors="replace")


_HTML_CHARSET_META = re.compile(r"<meta\b[^>]*charset[^>]*>", re.IGNORECASE)
_HTML_CHARSET_VALUE = re.compile(rb"charset=[\"']?\s*([A-Za-z0-9_.:-]+)", re.IGNORECASE)


def decode_html_bytes(raw: bytes) -> str:
    """Decode an HTML file's bytes: valid UTF-8, else the page's ``<meta charset>``, else cp1252."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    match = _HTML_CHARSET_VALUE.search(raw[:4096])
    if match:
        try:
            return raw.decode(match.group(1).decode("ascii"))
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode("cp1252", errors="replace")


def html_as_utf8(html: str) -> str:
    """Return ``html`` with its charset declaration replaced by ``<meta charset="utf-8">``.

    The temporary files handed to Docling are written as UTF-8, but its HTML reader lets
    the page's own ``<meta charset>`` / ``http-equiv`` tag win over the bytes, so a page
    saved as windows-1252 came back as ``Ã¤`` mojibake. The tag is inserted at the top
    (readers honour the first declaration).
    """
    cleaned = _HTML_CHARSET_META.sub("", html)
    return '<meta charset="utf-8">' + cleaned


def read_image_dimensions(image_path: str) -> Optional[Tuple[int, int, Optional[float]]]:
    """Width, height and DPI tag of an image file (header only), or ``None`` if unreadable."""
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            dpi = img.info.get("dpi")
            dpi_x = float(dpi[0]) if dpi and dpi[0] else None
            return img.width, img.height, dpi_x
    except Exception as exc:  # noqa: BLE001 - the converter will report the real error
        logger.debug(f"Could not read image dimensions of {image_path}: {exc}")
        return None


def _parse_optional_int(value: Optional[str], name: str) -> Optional[int]:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        logger.error(f"Invalid {name} '{value}' (expected an integer); ignoring it")
        return None


def _parse_float(value: Optional[str], name: str, default: float) -> float:
    text = (value or "").strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        logger.error(f"Invalid {name} '{value}' (expected a number); using {default}")
        return default


def parse_ocr_languages(value: Optional[str]) -> Tuple[str, ...]:
    """Parse ``OCR_LANGUAGES`` into normalised ISO 639-2 codes.

    Accepts comma/semicolon/plus/space separated codes in either two- or
    three-letter form (``de``, ``deu``, ``ger``); unknown codes are kept as
    given (lower-cased) so uncommon Tesseract packs still work. An empty value
    falls back to ``DEFAULT_OCR_LANGUAGES``.

    Args:
        value: Raw setting value.

    Returns:
        Tuple of codes without duplicates, order preserved.
    """
    raw = (value or "").strip() or DEFAULT_OCR_LANGUAGES
    out: List[str] = []
    for token in [t for t in re.split(r"[,;+\s]+", raw) if t]:
        code = token.strip().lower()
        code = _OCR_LANGUAGE_ALIASES.get(code, code)
        if code not in out:
            out.append(code)
    return tuple(out)


def resolve_ocr_config(
    engine: Optional[str] = None,
    languages: Optional[str] = None,
) -> OcrConfig:
    """Resolve the OCR configuration from explicit values, settings or the environment.

    Resolution order per value: explicit argument, ``src.settings.Settings``
    (``OCR_*`` from the environment or ``.env``), then the module defaults. An
    unknown engine name or an unparsable number is logged and replaced by the
    default rather than raising, so a typo in ``.env`` cannot take the ingestion
    worker down.

    Args:
        engine: Engine name override.
        languages: Language list override (raw string).

    Returns:
        Validated :class:`OcrConfig`.
    """
    env = os.environ
    scan_dpi_raw = env.get("OCR_SCAN_DPI")
    image_px_raw = env.get("OCR_IMAGE_MAX_MPX")
    psm_raw = env.get("OCR_PSM")
    tessdata_raw = env.get("OCR_TESSDATA_PATH")
    osd_raw = env.get("OCR_OSD_MIN_CONFIDENCE")
    dilation_raw = env.get("OCR_RECT_DILATION")
    if engine is None or languages is None:
        try:
            settings = load_settings(use_profile=False)
            engine = settings.ocr_engine if engine is None else engine
            languages = settings.ocr_languages if languages is None else languages
            scan_dpi_raw = str(settings.ocr_scan_dpi) if scan_dpi_raw is None else scan_dpi_raw
            image_px_raw = str(settings.ocr_image_max_mpx) if image_px_raw is None else image_px_raw
            psm_raw = settings.ocr_psm if psm_raw is None else psm_raw
            tessdata_raw = settings.ocr_tessdata_path if tessdata_raw is None else tessdata_raw
            osd_raw = str(settings.ocr_osd_min_confidence) if osd_raw is None else osd_raw
            dilation_raw = str(settings.ocr_rect_dilation) if dilation_raw is None else dilation_raw
        except Exception as exc:  # settings need MONGODB_URI etc.; the harness has none
            logger.debug(f"OCR settings not available via src.settings ({exc}); using environment")
            engine = env.get("OCR_ENGINE", DEFAULT_OCR_ENGINE) if engine is None else engine
            languages = env.get("OCR_LANGUAGES", DEFAULT_OCR_LANGUAGES) if languages is None else languages

    engine_name = (engine or DEFAULT_OCR_ENGINE).strip().lower()
    if engine_name not in SUPPORTED_OCR_ENGINES:
        logger.error(
            f"Unknown OCR_ENGINE '{engine}' (supported: {', '.join(SUPPORTED_OCR_ENGINES)}); "
            f"falling back to '{DEFAULT_OCR_ENGINE}'"
        )
        engine_name = DEFAULT_OCR_ENGINE
    scan_dpi = _parse_optional_int(scan_dpi_raw, "OCR_SCAN_DPI")
    if scan_dpi is None or scan_dpi < 72:
        if scan_dpi is not None:
            logger.error(f"OCR_SCAN_DPI={scan_dpi} is below 72; using {DEFAULT_OCR_SCAN_DPI}")
        scan_dpi = DEFAULT_OCR_SCAN_DPI
    image_px = _parse_optional_int(image_px_raw, "OCR_IMAGE_MAX_MPX")
    image_px = DEFAULT_OCR_IMAGE_MAX_MPX if image_px is None or image_px < 0 else image_px
    psm = _parse_optional_int(psm_raw, "OCR_PSM")
    if psm is not None and not 0 <= psm <= 13:
        logger.error(f"OCR_PSM={psm} is not a Tesseract page segmentation mode (0-13); ignoring it")
        psm = None
    tessdata = (tessdata_raw or "").strip() or None
    osd_min_conf = max(_parse_float(osd_raw, "OCR_OSD_MIN_CONFIDENCE", DEFAULT_OCR_OSD_MIN_CONFIDENCE), 0.0)
    dilation = _parse_optional_int(dilation_raw, "OCR_RECT_DILATION")
    dilation = DEFAULT_OCR_RECT_DILATION if dilation is None or dilation < 0 else dilation
    return OcrConfig(
        engine=engine_name,
        languages=parse_ocr_languages(languages),
        scan_dpi=scan_dpi,
        image_max_mpx=image_px,
        psm=psm,
        tessdata_path=tessdata,
        osd_min_confidence=osd_min_conf,
        rect_dilation=dilation,
    )


def build_ocr_options(config: OcrConfig, scale: Optional[float] = None):
    """Translate an :class:`OcrConfig` into Docling ``ocr_options``.

    Args:
        config: Resolved OCR configuration.
        scale: ``ocr_options.scale`` (OCR pixels per page point) to apply; ``None``
            keeps Docling's default of 3.0.

    Returns:
        A Docling ``OcrOptions`` instance, or ``None`` for ``auto`` (leave the
        pipeline's default ``OcrAutoOptions`` untouched).

    Raises:
        ImportError: If Docling is not installed.
    """
    from docling.datamodel import pipeline_options as po

    if config.engine == OCR_ENGINE_AUTO:
        return None
    if config.engine == "rapidocr":
        # Docling's RapidOCR wrapper does not take a language list.
        options = po.RapidOcrOptions()
    elif config.engine == "tesseract":
        # The `tesseract` binary (apt tesseract-ocr + tesseract-ocr-<lang>), not tesserocr.
        kwargs: Dict[str, Any] = {"lang": list(config.languages)}
        if config.psm is not None:
            kwargs["psm"] = config.psm
        if config.tessdata_path:
            kwargs["path"] = config.tessdata_path
        try:
            from src.ingestion.ocr_tesseract import tesseract_command

            # Wrapper that caps the OpenMP threads of each Tesseract process (OCR_OMP_THREAD_LIMIT)
            kwargs["tesseract_cmd"] = tesseract_command()
        except ImportError:  # older Docling without docling.models.stages: plain `tesseract`
            pass
        options = None
        if config.osd_min_confidence > 0:
            try:
                from src.ingestion.ocr_tesseract import RecallHubTesseractCliOcrOptions, register_tesseract_model

                register_tesseract_model()
                options = RecallHubTesseractCliOcrOptions(
                    osd_min_confidence=config.osd_min_confidence, rect_dilation=config.rect_dilation, **kwargs
                )
            except ImportError as exc:  # older Docling without docling.models.stages: stock model
                _warn_once(
                    "orientation-guard",
                    f"Docling's Tesseract model cannot be extended here ({exc}); orientation guard off",
                )
        if options is None:
            options = po.TesseractCliOcrOptions(**kwargs)
    elif config.engine == "easyocr":
        options = po.EasyOcrOptions(lang=[_OCR_LANGUAGE_CODES.get(code, code) for code in config.languages])
    else:
        raise ValueError(f"Unsupported OCR engine: {config.engine}")
    if scale is not None and "scale" in type(options).model_fields:
        options.scale = scale  # RapidOcrOptions carries no scale in some Docling versions
    return options


_ocr_engine_logged: bool = False
_warned: Set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(message)


def apply_ocr_options(pipeline_options, config: Optional[OcrConfig] = None, scale: Optional[float] = None) -> OcrConfig:
    """Apply the configured OCR engine to Docling pipeline options (logged once per process).

    Args:
        pipeline_options: A Docling ``PdfPipelineOptions`` (shared by PDF and image pipelines).
        config: Resolved configuration; resolved from settings when omitted.
        scale: OCR bitmap scale to apply (see :func:`build_ocr_options`).

    Returns:
        The configuration that was applied.
    """
    global _ocr_engine_logged
    config = config or resolve_ocr_config()
    options = build_ocr_options(config, scale=scale)
    if options is not None:
        pipeline_options.ocr_options = options
    if not _ocr_engine_logged:
        logger.info(f"Docling OCR engine: {config.describe()}")
        _ocr_engine_logged = True
    return config


# Converters are cached per thread and (configuration, OCR scale): Docling caches its
# initialised pipeline (layout model, OCR engine probe) per DocumentConverter instance,
# so building one per file - as process_file used to - reloads the models for every
# scanned file. The cache is thread-local because the ingestion executor converts on
# more than one thread and a Docling converter is not documented as thread-safe.
_converter_cache_local = threading.local()
_CONVERTER_CACHE_MAX = 8


def _cached_converter(key: Tuple[Any, ...], factory: Callable[[], Any]) -> Any:
    cache: Dict[Tuple[Any, ...], Any] = getattr(_converter_cache_local, "converters", None) or {}
    _converter_cache_local.converters = cache
    converter = cache.get(key)
    if converter is None:
        if len(cache) >= _CONVERTER_CACHE_MAX:
            cache.pop(next(iter(cache)))
        converter = factory()
        cache[key] = converter
    return converter


def clear_converter_cache() -> None:
    """Drop this thread's cached Docling converters (tests, or after a configuration change)."""
    _converter_cache_local.converters = {}


def detect_available_device() -> str:
    """
    Detect the best available device for processing.
    
    Returns:
        Device string: "cuda", "mps", or "cpu"
    """
    try:
        import torch
        
        if torch.cuda.is_available():
            device_name = torch.cuda.get_device_name(0)
            logger.info(f"CUDA available: {device_name}")
            return "cuda"
        
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            logger.info("MPS (Apple Silicon) available")
            return "mps"
        
        logger.info("No GPU detected, using CPU")
        return "cpu"
        
    except ImportError:
        logger.warning("PyTorch not available, falling back to CPU")
        return "cpu"
    except Exception as e:
        logger.warning(f"Error detecting device: {e}, falling back to CPU")
        return "cpu"


def get_effective_device() -> str:
    """
    Get the effective device to use based on configuration.
    
    Returns:
        Device string for torch/docling
    """
    cfg = _accelerator_config
    
    if cfg.device == "auto":
        return detect_available_device()
    
    return cfg.device


def full_page_ocr_reader(ocr_config: OcrConfig) -> Optional[Callable[[Any], str]]:
    """The one-pass Tesseract reader of the ``full_page`` scan-text fallback (F35).

    Reads a rendered page image with ``deu+eng`` (the configured languages), the configured
    page segmentation mode and tessdata, through the ``OCR_OMP_THREAD_LIMIT`` wrapper of
    :mod:`src.ingestion.ocr_tesseract`.

    Args:
        ocr_config: The pipeline's OCR configuration.

    Returns:
        The reader, or ``None`` when no Tesseract binary is available (the fallback then only
        recovers picture children).
    """
    try:
        from src.ingestion.ocr_tesseract import ocr_pil_image, tesseract_available
    except ImportError:  # Docling without the Tesseract stages
        return None
    if not tesseract_available():
        return None

    def read(image: Any) -> str:
        return ocr_pil_image(
            image, ocr_config.languages, psm=ocr_config.psm, tessdata_path=ocr_config.tessdata_path
        )

    return read


def create_document_converter(ocr_config: Optional[OcrConfig] = None):
    """
    Create (or reuse) a DocumentConverter with optimal settings for the current environment.

    Configures GPU acceleration when available and enabled, and the OCR engine
    from ``OCR_ENGINE`` / ``OCR_LANGUAGES`` (see :func:`resolve_ocr_config`); PDF
    pages are rasterised for OCR at ``OCR_SCAN_DPI``. The converter is cached per
    thread and configuration so Docling's models are loaded once, not per file.

    Args:
        ocr_config: OCR engine selection; resolved from settings when omitted.

    Returns:
        Configured DocumentConverter instance
    """
    ocr_config = ocr_config or resolve_ocr_config()
    key = (
        "pdf", ocr_config, get_effective_device(), _accelerator_config.enable_ocr_acceleration,
        docling_num_threads(),  # a resized pool rebuilds the converters with the new thread count
    )
    return _cached_converter(key, lambda: _build_document_converter(ocr_config))


def _build_document_converter(ocr_config: OcrConfig):
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.datamodel.base_models import InputFormat

        # Get effective device
        device = get_effective_device()
        cfg = _accelerator_config

        # Configure pipeline options for PDF processing
        pipeline_options = PdfPipelineOptions()

        # Accelerator: always a real AcceleratorOptions (see docling_accelerator_options)
        pipeline_options.accelerator_options = docling_accelerator_options(device)
        if device in ["cuda", "mps"]:
            logger.info(
                f"Docling converter using GPU acceleration: {pipeline_options.accelerator_options.device}"
            )
        else:
            logger.info(
                f"Docling converter using CPU ({pipeline_options.accelerator_options.num_threads} threads per model)"
            )

        # Enable OCR with acceleration if configured
        if cfg.enable_ocr_acceleration:
            pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = table_structure_enabled()

        # Select the OCR engine (tesseract deu+eng by default; auto keeps Docling's choice)
        # and the OCR raster resolution for PDF pages (OCR_SCAN_DPI).
        apply_ocr_options(pipeline_options, ocr_config, scale=ocr_config.pdf_scale())

        # Create format options
        format_options = {
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options
            )
        }
        
        converter = DocumentConverter(format_options=format_options)
        return converter
        
    except ImportError as e:
        logger.warning(f"Could not configure advanced Docling options: {e}")
        # Fall back to basic converter
        from docling.document_converter import DocumentConverter
        return DocumentConverter()
    except Exception as e:
        logger.error(f"Error creating DocumentConverter: {e}")
        from docling.document_converter import DocumentConverter
        return DocumentConverter()


def create_image_converter(ocr_config: Optional[OcrConfig] = None, image_path: Optional[str] = None):
    """
    Create (or reuse) the DocumentConverter used for image files (PNG, JPG, TIFF, ...).

    Images go through Docling's PDF pipeline with OCR forced on; the OCR engine
    and languages come from ``OCR_ENGINE`` / ``OCR_LANGUAGES`` exactly as for
    scanned PDFs (:func:`create_document_converter`). With ``OCR_IMAGE_MAX_MPX``
    set and an ``image_path`` given, the OCR bitmap scale is chosen per image so
    its long side lands on that many pixels (see :meth:`OcrConfig.image_scale`);
    converters are cached per thread, configuration and (rounded) scale.

    Args:
        ocr_config: OCR engine selection; resolved from settings when omitted.
        image_path: The image about to be converted (for the per-image scale).

    Returns:
        Configured DocumentConverter instance for ``InputFormat.IMAGE``.

    Raises:
        ImportError: If Docling image support is not installed.
    """
    ocr_config = ocr_config or resolve_ocr_config()
    scale: Optional[float] = None
    if image_path and ocr_config.image_max_mpx > 0:
        dims = read_image_dimensions(image_path)
        if dims is not None:
            scale = ocr_config.image_scale(*dims)
    key = ("image", ocr_config, get_effective_device(), docling_num_threads())
    converter = _cached_converter(key, lambda: _build_image_converter(ocr_config, None))
    if scale is not None:
        set_image_ocr_scale(converter, scale)
    return converter


def set_image_ocr_scale(converter: Any, scale: float) -> bool:
    """Set the OCR bitmap scale on a converter's live image pipeline.

    Docling fixes ``scale`` on the OCR model when the pipeline is created and keys its
    pipeline cache on the options, so changing ``ocr_options.scale`` per image would
    rebuild the pipeline (layout model included) for every distinct photo size. The
    model only reads ``self.scale`` per call, so it is set on the initialised model
    instead; the converter is per thread, so no concurrent conversion sees the change.

    Args:
        converter: A ``DocumentConverter`` built by :func:`create_image_converter`.
        scale: OCR pixels per page point.

    Returns:
        ``True`` if the scale was applied, ``False`` if the pipeline exposes no OCR model.
    """
    try:
        from docling.datamodel.base_models import InputFormat

        pipeline = converter._get_pipeline(InputFormat.IMAGE)  # initialises it on first use
        model = getattr(pipeline, "ocr_model", None)
        if model is None or not hasattr(model, "scale"):
            return False
        model.scale = float(scale)
        return True
    except Exception as exc:  # noqa: BLE001 - keep Docling's default scale rather than failing the file
        logger.warning(f"Could not set the per-image OCR scale ({exc}); using the pipeline default")
        return False


def image_ocr_scale(converter: Any) -> Optional[float]:
    """The OCR scale the converter's image pipeline currently uses (diagnostics)."""
    try:
        from docling.datamodel.base_models import InputFormat

        pipeline = converter.initialized_pipelines.get(
            next(k for k in converter.initialized_pipelines if k[0] is converter.format_to_options[InputFormat.IMAGE].pipeline_cls)
        )
        return float(getattr(pipeline.ocr_model, "scale"))
    except Exception:  # noqa: BLE001
        return None


def _build_image_converter(ocr_config: OcrConfig, scale: Optional[float]):
    from docling.document_converter import DocumentConverter, ImageFormatOption
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.base_models import InputFormat

    # Configure OCR pipeline for images
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.do_table_structure = table_structure_enabled()

    # Accelerator: always a real AcceleratorOptions (see docling_accelerator_options)
    pipeline_options.accelerator_options = docling_accelerator_options(get_effective_device())

    apply_ocr_options(pipeline_options, ocr_config, scale=scale)

    format_options = {
        InputFormat.IMAGE: ImageFormatOption(
            pipeline_options=pipeline_options
        )
    }
    return DocumentConverter(format_options=format_options)


def get_file_size_safe(file_path: str) -> int:
    """Get file size safely, returning 0 if file not found."""
    try:
        return os.path.getsize(file_path)
    except OSError:
        return 0


def get_file_modified_time_safe(file_path: str) -> datetime:
    """Get file modification time safely, returning current time if not accessible."""
    try:
        mtime = os.path.getmtime(file_path)
        return datetime.fromtimestamp(mtime)
    except OSError:
        return datetime.now()


def map_error_type_to_classification(error_type: Optional[str], chunks_created: int) -> str:
    """
    Map error_type and chunks_created to FileClassification value.
    
    Args:
        error_type: Error type from processing (timeout, error, no_chunks, image_only_pdf, duplicate)
        chunks_created: Number of chunks created
        
    Returns:
        FileClassification value string
    """
    if error_type == "timeout":
        return "timeout"
    elif error_type == "error":
        return "error"
    elif error_type == "image_only_pdf":
        return "image_only_pdf"
    elif error_type == "no_chunks":
        return "no_chunks"
    elif error_type == "duplicate":
        return "normal"  # Duplicates are considered normal (already processed)
    elif chunks_created > 0:
        return "normal"
    else:
        return "no_chunks"


@dataclass
class ConversionHandle:
    """What the concurrent loop needs to know about one file's conversion step.

    ``started`` is set when the conversion actually begins (a pool thread picked it up, or a
    conversion worker process acknowledged it): the file's budget starts there, not when the
    file was queued. ``thread_future`` is the pool future of a thread-mode conversion; after a
    timeout it tells the loop whether the thread is still running (a thread cannot be
    interrupted, so its slot stays reserved until it ends). ``notes`` are the facts the
    reader recorded about the conversion with :func:`note_conversion` (today: which scan-text
    fallback fired), brought back from the pool thread or the worker process.
    """

    started: asyncio.Event = field(default_factory=asyncio.Event)
    started_at: Optional[float] = None
    thread_future: Optional[concurrent.futures.Future] = None
    notes: Dict[str, Any] = field(default_factory=dict)

    def mark_started(self) -> None:
        if self.started_at is None:
            self.started_at = time.monotonic()
            self.started.set()


# Facts a reader records about the conversion it is running (``note_conversion``), collected
# per thread by ``conversion_notes``: the thread-pool reader and the conversion worker process
# both wrap ``_read_document`` in it and hand the notes back to the loop on the ConversionHandle.
_conversion_notes_local = threading.local()


@contextlib.contextmanager
def conversion_notes() -> Iterator[Dict[str, Any]]:
    """Collect the :func:`note_conversion` facts of the conversions run in this thread inside the block.

    Yields:
        The dict the notes land in (read it after the block).
    """
    previous = getattr(_conversion_notes_local, "notes", None)
    notes: Dict[str, Any] = {}
    _conversion_notes_local.notes = notes
    try:
        yield notes
    finally:
        _conversion_notes_local.notes = previous


def note_conversion(**facts: Any) -> None:
    """Record facts about the conversion running in this thread; a no-op outside :func:`conversion_notes`."""
    notes = getattr(_conversion_notes_local, "notes", None)
    if notes is not None:
        notes.update(facts)


# The concurrent loop sets the handle of the file it is about to process; the per-file task
# (and the conversion step inside it) inherits the context, so ``_ingest_single_document``
# keeps its signature and subclasses that override it (tests) keep working.
_conversion_handle_var: contextvars.ContextVar[Optional[ConversionHandle]] = contextvars.ContextVar(
    "ingest_conversion_handle", default=None
)


class IngestionStatsRecorder:
    """Buffers the per-file stats of a run and writes them in batches while it is running.

    Until 2026-10-24 the concurrent loop wrote ``ingestion_stats`` and ``failed_documents``
    only after its last file: for a weeks-long job the failed-documents page, the analytics
    endpoints and the retry options had nothing to work with, and a restart lost everything
    held in memory (F2 of the prompt-test log). Stats are now flushed every
    ``flush_every_files`` recorded files (``add`` schedules the write) or every
    ``flush_every_seconds`` (:meth:`timer_loop`), and once more when the run ends.

    Args:
        save: Coroutine function writing one batch (``_save_ingestion_stats``). ``False``
            means the batch was not written; it is kept for the next flush. Any other return
            value counts as written.
        flush_every_files: Recorded files that trigger a flush.
        flush_every_seconds: Seconds between time-based flushes.
        on_flush: Called after every successful write with :meth:`snapshot` (the cumulative
            counts of the stats written so far), e.g. to update job status counters.
    """

    COUNTED: Tuple[str, ...] = ("timeout", "no_chunks", "error", "image_only_pdf", "duplicate", "success")
    #: Stats kept in memory while the database refuses writes; the oldest are dropped beyond it.
    MAX_PENDING = 10_000

    def __init__(
        self,
        save: Callable[[List[IngestionFileStats]], Awaitable[Any]],
        flush_every_files: int = DEFAULT_STATS_FLUSH_FILES,
        flush_every_seconds: float = DEFAULT_STATS_FLUSH_SECONDS,
        on_flush: Optional[Callable[[Dict[str, int]], None]] = None,
    ) -> None:
        self._save = save
        self.flush_every_files = max(1, int(flush_every_files))
        self.flush_every_seconds = max(1.0, float(flush_every_seconds))
        self._on_flush = on_flush
        self._pending: List[IngestionFileStats] = []
        self._lock = threading.Lock()  # add() runs under the loop's results lock; keep it cheap
        self._flush_lock: Optional[asyncio.Lock] = None
        self._flush_task: Optional[asyncio.Task] = None
        self.counts: Dict[str, int] = {}  # of every recorded stat, by error type ("success" if none)
        self.written_counts: Dict[str, int] = {}  # of the stats written to the database
        self.recorded = 0
        self.written = 0
        self.flushes = 0
        self.failed_flushes = 0
        self.dropped = 0

    @staticmethod
    def _count(counter: Dict[str, int], stat: IngestionFileStats) -> None:
        key = stat.error_type or "success"
        counter[key] = counter.get(key, 0) + 1
        if stat.success and stat.image_only_pdf:
            # Pure scans whose OCR produced text (F9): shown next to the skipped/empty ones
            counter["image_only_ocr"] = counter.get("image_only_ocr", 0) + 1

    @property
    def pending(self) -> int:
        """Stats recorded but not yet written."""
        with self._lock:
            return len(self._pending)

    def add(self, stat: IngestionFileStats) -> None:
        """Record one file's stats; schedules a flush once a batch is full."""
        with self._lock:
            self._pending.append(stat)
            self.recorded += 1
            self._count(self.counts, stat)
            due = len(self._pending) >= self.flush_every_files
        if due:
            self.schedule_flush()

    def schedule_flush(self) -> None:
        """Start a flush in the background unless one is already running."""
        if self._flush_task is not None and not self._flush_task.done():
            return
        try:
            self._flush_task = asyncio.get_running_loop().create_task(self.flush())
        except RuntimeError:  # no running loop: the next explicit flush() writes the batch
            self._flush_task = None

    def snapshot(self) -> Dict[str, int]:
        """Cumulative counts of the stats written so far, plus ``written``."""
        counts = {key: self.written_counts.get(key, 0) for key in self.COUNTED}
        counts["written"] = self.written
        return counts

    async def flush(self) -> int:
        """Write the buffered stats.

        Returns:
            Number of stats written (0 when there was nothing, or the write failed and the
            batch was kept).
        """
        if self._flush_lock is None:
            self._flush_lock = asyncio.Lock()
        async with self._flush_lock:
            with self._lock:
                batch, self._pending = self._pending, []
            if not batch:
                return 0
            try:
                outcome = await self._save(batch)
            except Exception as exc:  # noqa: BLE001 - keep the batch, report, carry on
                logger.error(f"Ingestion stats flush failed ({len(batch)} files kept for the next flush): {exc}")
                outcome = False
            if outcome is False:
                self.failed_flushes += 1
                with self._lock:
                    self._pending[0:0] = batch
                    overflow = len(self._pending) - self.MAX_PENDING
                    if overflow > 0:
                        del self._pending[:overflow]
                        self.dropped += overflow
                        logger.error(f"Ingestion stats buffer full: dropped the oldest {overflow} unwritten file stats")
                return 0
            self.flushes += 1
            self.written += len(batch)
            for stat in batch:
                self._count(self.written_counts, stat)
            logger.info(f"Saved {len(batch)} file stats to database ({self.written} this run)")
            if self._on_flush is not None:
                try:
                    self._on_flush(self.snapshot())
                except Exception:  # noqa: BLE001 - a status update must not end the run
                    logger.exception("Ingestion stats callback failed")
            return len(batch)

    async def timer_loop(self) -> None:
        """Flush every ``flush_every_seconds`` until cancelled (time-based flushes)."""
        while True:
            await asyncio.sleep(self.flush_every_seconds)
            if self.pending:
                await self.flush()


class DocumentIngestionPipeline:
    """Pipeline for ingesting documents into MongoDB vector database."""

    # Thread pool for the CPU-bound steps (hashing, Docling conversion, folder discovery),
    # shared across instances. Sized per run by configure_executor() from the performance
    # config; see resolve_thread_pool_workers().
    _executor: Optional[ThreadPoolExecutor] = None
    _executor_workers: int = 0
    # Conversion worker processes of the run in progress (process isolation); None = thread pool
    _conversion_pool: Optional[Any] = None

    @classmethod
    def get_executor(cls) -> ThreadPoolExecutor:
        """The shared thread pool, created at its default size on first use."""
        if cls._executor is None:
            cls.configure_executor(default_thread_pool_workers())
        return cls._executor

    @classmethod
    def configure_executor(cls, workers: int) -> ThreadPoolExecutor:
        """Size the shared pool to ``workers`` threads and share the CPU threads accordingly.

        A pool of another size is replaced; the old one finishes its running tasks in the
        background. Runs never overlap within one process (the API refuses a second job,
        the worker takes one at a time), so a run reconfigures an idle pool. The Tesseract
        thread limit (``OCR_OMP_THREAD_LIMIT``) follows the new size unless pinned, and
        converters built for the old size are rebuilt on their next use (their cache key
        carries the Docling thread count).

        Args:
            workers: Requested pool size; clamped to 1..MAX_THREAD_POOL_WORKERS.

        Returns:
            The pool in force.
        """
        global _pool_workers_in_force
        workers = max(1, min(MAX_THREAD_POOL_WORKERS, int(workers)))
        if cls._executor is not None and cls._executor_workers == workers:
            return cls._executor
        previous = cls._executor
        cls._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ingest_")
        cls._executor_workers = workers
        _pool_workers_in_force = workers
        if previous is not None:
            previous.shutdown(wait=False)
        try:
            from src.ingestion.ocr_tesseract import configure_tesseract_threads
        except ImportError:  # Docling not installed (unit tests without it): no Tesseract either
            tesseract_threads: Optional[int] = None
        else:
            tesseract_threads = configure_tesseract_threads(workers)
        logger.info(
            f"Ingestion thread pool: {workers} workers; Docling threads per model: "
            f"{docling_num_threads(workers)}; Tesseract OpenMP threads per process: "
            f"{tesseract_threads if tesseract_threads else 'unlimited'}"
        )
        return cls._executor

    @classmethod
    def reader_only(cls) -> "DocumentIngestionPipeline":
        """A pipeline that only reads documents, for the conversion worker processes.

        ``_read_document`` and the format readers need ``settings`` only; there is no
        database, chunker or embedder behind this instance.
        """
        reader = cls.__new__(cls)
        reader.settings = load_settings()
        reader.config = None
        reader.clean_before_ingest = False
        reader.documents_folders = []
        reader.documents_folder = ""
        reader.mongo_client = None
        reader.db = None
        reader.chunker = None
        reader.embedder = None
        reader._initialized = False
        return reader

    def __init__(
        self,
        config: IngestionConfig,
        documents_folder: Optional[str] = None,
        documents_folders: Optional[List[str]] = None,
        clean_before_ingest: bool = True,
        use_profile: bool = True
    ):
        """
        Initialize ingestion pipeline.

        Args:
            config: Ingestion configuration
            documents_folder: Single folder containing documents (legacy support)
            documents_folders: List of folders containing documents
            clean_before_ingest: Whether to clean existing data before ingestion
            use_profile: Whether to use profile settings for folders
        """
        self.config = config
        self.clean_before_ingest = clean_before_ingest

        # Load settings
        self.settings = load_settings()
        
        # Determine document folders
        if documents_folders:
            self.documents_folders = documents_folders
        elif documents_folder:
            self.documents_folders = [documents_folder]
        elif use_profile:
            # Use profile's document folders
            profile_manager = get_profile_manager(self.settings.profiles_path)
            self.documents_folders = profile_manager.get_all_document_folders()
        else:
            self.documents_folders = ["documents"]
        
        # Legacy support - primary folder
        self.documents_folder = self.documents_folders[0] if self.documents_folders else "documents"

        # Initialize MongoDB client and database references
        self.mongo_client: Optional[AsyncMongoClient] = None
        self.db: Optional[Any] = None

        # Initialize components
        self.chunker_config = ChunkingConfig(
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
            max_chunk_size=config.max_chunk_size,
            max_tokens=config.max_tokens
        )

        self.chunker = create_chunker(self.chunker_config)
        self.embedder = create_embedder()

        self._initialized = False

    async def initialize(self) -> None:
        """
        Initialize MongoDB connections.

        Raises:
            ConnectionFailure: If MongoDB connection fails
            ServerSelectionTimeoutError: If MongoDB server selection times out
        """
        if self._initialized:
            return

        logger.info("Initializing ingestion pipeline...")

        try:
            # Initialize MongoDB client
            self.mongo_client = AsyncMongoClient(
                self.settings.mongodb_uri,
                serverSelectionTimeoutMS=5000
            )
            self.db = self.mongo_client[self.settings.mongodb_database]

            # Verify connection
            await self.mongo_client.admin.command("ping")
            logger.info(
                f"Connected to MongoDB database: {self.settings.mongodb_database}"
            )

        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            logger.exception(f"mongodb_connection_failed: {str(e)}")
            raise

        self._initialized = True
        logger.info("Ingestion pipeline initialized")

    async def close(self) -> None:
        """Close MongoDB connections."""
        if self._initialized and self.mongo_client:
            await self.mongo_client.close()
            self.mongo_client = None
            self.db = None
            self._initialized = False
            logger.info("MongoDB connection closed")

    def _find_document_files(self) -> List[str]:
        """
        Find all supported document files in all document folders.
        
        Uses os.walk() for single-pass directory traversal instead of multiple
        glob calls, which is much faster on network filesystems (17x faster).
        
        Files are sorted for optimal processing order:
        1. Text files first (fastest to process)
        2. Then PDF, DOCX, Excel, etc.
        3. Images
        4. Audio files (slowest)
        5. Large files (>5MB) are processed last within each category

        Returns:
            List of file paths in optimal processing order
        """
        # Supported extensions (shared with the pre-ingestion estimator).
        extensions = SUPPORTED_INGESTION_EXTENSIONS

        all_files: List[str] = []
        sizes: Dict[str, int] = {}

        for folder in self.documents_folders:
            if not os.path.exists(folder):
                logger.warning(f"Documents folder not found: {folder}")
                continue

            # Single pass with the sizes taken while each directory listing is fresh
            # (see walk_document_files); sizing afterwards cost ~20 min for 400K files
            for path, size in walk_document_files(folder, extensions):
                all_files.append(path)
                sizes[path] = size

        if not all_files:
            logger.error(f"No document files found in folders: {self.documents_folders}")
            return []

        # Sort files for optimal processing order
        all_files = self._sort_files_for_processing(all_files, sizes)

        return all_files

    def _sort_files_for_processing(self, files: List[str], sizes: Optional[Dict[str, int]] = None) -> List[str]:
        """
        Sort files for optimal processing order:
        1. Small text files first (fastest)
        2. PDF, DOCX, Excel (medium speed)
        3. Images (can be slower due to OCR)
        4. Audio files (slowest - require transcription)
        5. Video files (very slow)
        6. Large files (>5MB) at the end within each category
        
        Args:
            files: List of file paths
            sizes: File sizes in bytes collected during discovery; files missing from it
                are sized with one ``stat`` each (slow on network shares).

        Returns:
            Sorted list of file paths
        """
        # Define file type priorities (lower = processed first)
        TYPE_PRIORITY = {
            # Text files - fastest
            '.txt': 1, '.md': 1, '.markdown': 1,
            '.as': 1, '.ass': 1, '.ds': 1, '.eb': 1, '.do': 1, '.dos': 1, '.3': 1, '.dss': 1, '.wvb': 1, '.mtt': 1,
            # HTML - also fast
            '.html': 2, '.htm': 2, '.mht': 2, '.mhtml': 2,
            # Office documents - medium
            '.pdf': 3,
            '.docx': 4, '.doc': 4, '.wbk': 4, '.dot': 4, '.dotx': 4, '.rtf': 4,
            '.xlsx': 5, '.xls': 5,
            '.pptx': 6, '.ppt': 6,
            '.msg': 4,  # Outlook email - similar priority to Office docs
            '.eml': 4,  # RFC 5322 email
            # Images - can need OCR
            '.png': 7, '.jpg': 7, '.jpeg': 7, '.gif': 7, '.webp': 7, '.bmp': 7, '.tiff': 7, '.tif': 7, '.svg': 7,
            # Audio - requires transcription
            '.mp3': 8, '.wav': 8, '.m4a': 8, '.flac': 8, '.ogg': 8,
            # Video - slowest
            '.mp4': 9, '.avi': 9, '.mkv': 9, '.mov': 9, '.webm': 9, '.wmv': 9,
        }
        
        SIZE_THRESHOLD = 5 * 1024 * 1024  # 5MB
        known_sizes = sizes or {}

        def file_size(file_path: str) -> int:
            size = known_sizes.get(file_path)
            if size is None:
                size = get_file_size_safe(file_path)
            return size

        def get_sort_key(file_path: str) -> tuple:
            """Return (is_large, type_priority, size, filename) for sorting."""
            ext = os.path.splitext(file_path)[1].lower()
            type_priority = TYPE_PRIORITY.get(ext, 100)  # Unknown types last
            size = file_size(file_path)
            is_large = size > SIZE_THRESHOLD

            # Sort by: is_large (False first), type_priority, size, filename
            return (is_large, type_priority, size, os.path.basename(file_path).lower())

        keyed = [(get_sort_key(f), f) for f in files]
        keyed.sort(key=lambda item: item[0])
        sorted_files = [f for _, f in keyed]

        # Log the processing order summary (from the keys: no second stat per file)
        large_count = sum(1 for key, _ in keyed if key[0])
        small_count = len(sorted_files) - large_count
        
        if large_count > 0:
            logger.info(
                f"File processing order: {small_count} small files first, "
                f"{large_count} large files (>5MB) last"
            )
        
        return sorted_files

    def _recover_scan_text(self, document: Any, file_path: str) -> Optional[Any]:
        """Put the OCR text of scanned pages Docling left out of the body back into ``document`` (F35).

        Runs :func:`src.ingestion.scan_text_fallback.recover_scan_text` on a converted PDF:
        pages with body text are untouched; a page whose text hangs below a ``PictureItem``
        gets it copied into the body (``picture_children``), a page without any item that is
        a scan is read with one full-page Tesseract pass at the pipeline's OCR scale
        (``full_page``). What fired is logged once per file and recorded for the file's stats
        through :func:`note_conversion`. A failure inside the fallback never fails the file.

        Args:
            document: The converted ``DoclingDocument`` (modified in place).
            file_path: The source PDF.

        Returns:
            The ``ScanTextRecovery``, or ``None`` when nothing was recovered.
        """
        if document is None:
            return None
        try:
            from src.ingestion.scan_text_fallback import recover_scan_text

            ocr_config = resolve_ocr_config()
            recovery = recover_scan_text(
                document,
                file_path,
                ocr_page=full_page_ocr_reader(ocr_config),
                scale=ocr_config.pdf_scale(),
            )
        except Exception as exc:  # noqa: BLE001 - the conversion result stands as Docling returned it
            logger.warning(f"Scan text fallback failed for {os.path.basename(file_path)}: {exc}")
            return None
        if recovery is None:
            return None
        logger.info(f"OCR fallback for {os.path.basename(file_path)} ({recovery.describe()})")
        note_conversion(ocr_fallback=recovery.fallback)
        return recovery

    def _read_document(self, file_path: str) -> tuple[str, Optional[Any]]:
        """
        Read document content from file - supports multiple formats via Docling.

        Args:
            file_path: Path to the document file

        Returns:
            Tuple of (markdown_content, docling_document).
            docling_document is None only for text files.
        """
        file_ext = os.path.splitext(file_path)[1].lower()

        # Word backups (.wbk) and Word 97 templates (.dot) are the same OLE2 format as .doc
        if file_ext in ('.wbk', '.dot'):
            file_ext = '.doc'

        # Word 2007+ templates (.dotx): a .docx container under another name
        if file_ext == '.dotx':
            return self._read_renamed_office_file(file_path, '.docx')

        # RTF letters and templates
        if file_ext == '.rtf':
            return self._read_rtf_file(file_path)

        # MHTML web archives (saved web pages, some e-mail exports)
        if file_ext in ('.mht', '.mhtml'):
            return self._read_mhtml_file(file_path)

        # Legacy Excel: Docling reads .xlsx only
        if file_ext == '.xls':
            return self._read_xls_file(file_path)

        # DOS-era text system files (Pittner Archiv, M U S T E R), Olympus .dss dictations
        if file_ext in LEGACY_TEXT_EXTENSIONS:
            return self._read_legacy_text_file(file_path)

        # jurXpert "weiteres Vorbringen" (.wvb: text or RTF) and gzip-packed templates (.mtt)
        if file_ext == '.wvb':
            return self._read_sniffed_text_file(file_path)
        if file_ext == '.mtt':
            return self._read_gzipped_text_file(file_path)

        # Outlook .msg files - extract email content
        if file_ext == '.msg':
            return self._read_msg_file(file_path)

        # RFC 5322 .eml files - parse with the standard library
        if file_ext == '.eml':
            return self._read_eml_file(file_path)

        # Legacy .doc files - use antiword (Docling doesn't support .doc)
        if file_ext == '.doc':
            return self._read_doc_file(file_path)

        # Audio formats - transcribe with Whisper ASR
        audio_formats = ['.mp3', '.wav', '.m4a', '.flac']
        if file_ext in audio_formats:
            # Returns tuple: (markdown_content, docling_document)
            return self._transcribe_audio(file_path)

        # Video formats - skip with placeholder (no transcription pipeline yet)
        video_formats = ['.mp4', '.avi', '.mkv', '.mov', '.webm', '.wmv']
        if file_ext in video_formats:
            logger.warning(
                f"Skipping video file (no video transcription pipeline): "
                f"{os.path.basename(file_path)}"
            )
            return (
                f"[Video: {os.path.basename(file_path)} - video transcription not yet supported]",
                None
            )

        # Image formats - convert via Docling with OCR
        image_formats = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.tif']
        if file_ext in image_formats:
            # Multi-page TIFF scans: OCR every page, not only the first (docs/26)
            if file_ext in ('.tif', '.tiff'):
                frames = image_frame_count(file_path)
                if frames > 1:
                    return self._read_multipage_tiff(file_path, frames)
            try:
                logger.info(
                    f"Processing image with OCR via Docling: "
                    f"{os.path.basename(file_path)}"
                )

                convert_path, temp_copy = prepare_image_for_docling(file_path)
                try:
                    converter = create_image_converter(image_path=convert_path)
                    result = converter.convert(convert_path)
                finally:
                    if temp_copy and os.path.exists(temp_copy):
                        os.remove(temp_copy)

                markdown_content = export_document_markdown(result.document)

                if image_noise_gate_enabled():
                    ocr_score = conversion_ocr_score(result)
                    if image_text_is_noise(markdown_content, ocr_score):
                        tokens, ratio = image_text_quality(markdown_content)
                        logger.info(
                            f"OCR output of {os.path.basename(file_path)} looks like noise "
                            f"({tokens} tokens, word-like ratio {ratio:.2f}, OCR score "
                            f"{ocr_score if ocr_score is None else round(ocr_score, 2)}); storing no text"
                        )
                        return (
                            f"[Image: {os.path.basename(file_path)} - no readable text]",
                            None
                        )

                if markdown_content and len(markdown_content.strip()) > 10:
                    logger.info(
                        f"Successfully extracted {len(markdown_content)} chars from image "
                        f"via OCR: {os.path.basename(file_path)}"
                    )
                    return (markdown_content, result.document)
                else:
                    logger.warning(
                        f"OCR produced minimal content for image: "
                        f"{os.path.basename(file_path)}"
                    )
                    return (
                        f"[Image: {os.path.basename(file_path)} - OCR extracted minimal text]",
                        None
                    )
            except ImportError as e:
                logger.warning(f"Docling image support not available: {e}")
                return (
                    f"[Image: {os.path.basename(file_path)} - OCR not available]",
                    None
                )
            except Exception as e:
                logger.error(f"Failed to OCR image {file_path}: {e}")
                return (
                    f"[Image: {os.path.basename(file_path)} - OCR failed: {str(e)}]",
                    None
                )

        # Docling-supported formats (convert to markdown); legacy .xls is read above
        docling_formats = [
            '.pdf', '.docx', '.pptx', '.ppt',
            '.xlsx', '.html', '.htm',
            '.md', '.markdown'  # Markdown files for HybridChunker
        ]

        if file_ext in docling_formats:
            try:
                # Use GPU-accelerated converter when available
                logger.info(
                    f"Converting {file_ext} file using Docling: "
                    f"{os.path.basename(file_path)}"
                )

                converter = create_document_converter()
                result = converter.convert(file_path)

                # Scanned pages whose OCR text Docling attached to a picture, or returned
                # empty, get their text back into the document before export/chunking (F35)
                if file_ext == '.pdf':
                    self._recover_scan_text(result.document, file_path)

                # Export to markdown for consistent processing
                markdown_content = export_document_markdown(result.document)
                logger.info(
                    f"Successfully converted {os.path.basename(file_path)} "
                    f"to markdown"
                )

                # Return both markdown and DoclingDocument for HybridChunker
                return (markdown_content, result.document)

            except Exception as e:
                logger.error(f"Failed to convert {file_path} with Docling: {e}")
                # Fall back to raw text if Docling fails
                logger.warning(f"Falling back to raw text extraction for {file_path}")
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        return (f.read(), None)
                except Exception:
                    return (
                        f"[Error: Could not read file {os.path.basename(file_path)}]",
                        None
                    )

        # Text-based formats (read directly)
        else:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return (f.read(), None)
            except UnicodeDecodeError:
                # Try with different encoding
                with open(file_path, 'r', encoding='latin-1') as f:
                    return (f.read(), None)

    # -- Legacy and container formats (law-firm corpora, docs/26) ------------------------

    def _read_rtf_file(self, file_path: str) -> tuple[str, Optional[Any]]:
        """Plain text of an RTF file (letters and ``Muster`` templates of legal practices).

        Uses ``striprtf``. RTF is 7-bit ASCII whose ``\\'xx`` escapes are bytes of the
        document's ``\\ansicpg`` code page, so the file is read as latin-1 and the parser
        decodes the escapes with that code page (``cp1252`` when none is declared).

        Args:
            file_path: Path to the .rtf file.

        Returns:
            Tuple of (text, None); an ``[Error: …]`` placeholder when it cannot be read.
        """
        basename = os.path.basename(file_path)
        try:
            from striprtf.striprtf import rtf_to_text
        except ImportError:
            logger.error(f"striprtf is not installed; cannot read RTF file {basename}")
            return (f"[Error: Could not read file {basename} (no RTF reader installed)]", None)
        try:
            with open(file_path, "rb") as handle:
                raw = handle.read()
            text = rtf_to_text(raw.decode("latin-1"), encoding=rtf_code_page(raw), errors="replace")
        except Exception as exc:  # noqa: BLE001 - one unreadable file must not stop the run
            logger.error(f"Failed to read RTF {basename}: {exc}")
            return (f"[Error: Could not read file {basename}]", None)
        text = text.strip()
        if not text:
            logger.warning(f"RTF file has no text content: {basename}")
            return (f"[Document: {basename} - no text content]", None)
        logger.info(f"Extracted {len(text)} chars from RTF: {basename}")
        return (text, None)

    def _read_xls_file(self, file_path: str) -> tuple[str, Optional[Any]]:
        """Legacy Excel (.xls) as one Markdown table per sheet (pandas + xlrd).

        Files with an ``.xls`` name that are really HTML or XML exports (common with
        bookkeeping software) are handed to Docling's HTML reader instead.

        Args:
            file_path: Path to the .xls file.

        Returns:
            Tuple of (markdown, None); an ``[Error: …]`` placeholder when it cannot be read.
        """
        basename = os.path.basename(file_path)
        try:
            with open(file_path, "rb") as handle:
                head = handle.read(512).lstrip()
        except OSError as exc:
            logger.error(f"Failed to open {basename}: {exc}")
            return (f"[Error: Could not read file {basename}]", None)
        if head[:1] == b"<":
            logger.info(f"{basename} is an HTML/XML export named .xls; reading it as HTML")
            return self._read_renamed_office_file(file_path, ".html")
        try:
            import pandas as pd

            sheets = pd.read_excel(file_path, sheet_name=None, header=None, engine="xlrd")
        except ImportError as exc:
            logger.error(f"xlrd/pandas not installed; cannot read legacy .xls {basename}: {exc}")
            return (f"[Error: Could not read file {basename} (no .xls reader installed)]", None)
        except Exception as exc:  # noqa: BLE001 - one unreadable file must not stop the run
            logger.error(f"Failed to read legacy .xls {basename}: {exc}")
            return (f"[Error: Could not read file {basename}]", None)
        parts: List[str] = []
        for sheet_name, frame in sheets.items():
            frame = frame.dropna(how="all").dropna(axis=1, how="all")
            if frame.empty:
                continue
            rows = frame.astype(object).where(frame.notna(), "").values.tolist()
            parts.append(f"## {sheet_name}\n\n{markdown_table(rows)}")
        if not parts:
            logger.warning(f"Legacy .xls has no cell content: {basename}")
            return (f"[Document: {basename} - no cell content]", None)
        logger.info(f"Extracted {len(parts)} sheet(s) from legacy .xls: {basename}")
        return ("\n\n".join(parts), None)

    def _read_mhtml_file(self, file_path: str) -> tuple[str, Optional[Any]]:
        """MHTML web archive (.mht): its HTML part is converted by Docling's HTML reader.

        Args:
            file_path: Path to the .mht/.mhtml file.

        Returns:
            Tuple of (markdown, DoclingDocument); an ``[Error: …]`` placeholder when unreadable.
        """
        import email
        import email.policy
        import tempfile

        basename = os.path.basename(file_path)
        try:
            with open(file_path, "rb") as handle:
                message = email.message_from_binary_file(handle, policy=email.policy.default)
            html_part = next((p for p in message.walk() if p.get_content_type() == "text/html"), None)
            if html_part is None:
                text_part = next((p for p in message.walk() if p.get_content_type() == "text/plain"), None)
                if text_part is None:
                    logger.warning(f"MHTML archive has no HTML or text part: {basename}")
                    return (f"[Document: {basename} - no readable part]", None)
                payload = text_part.get_payload(decode=True) or b""
                return (decode_text_payload(payload, text_part.get_content_charset()), None)
            payload = html_part.get_payload(decode=True) or b""
            html = decode_text_payload(payload, html_part.get_content_charset())
        except Exception as exc:  # noqa: BLE001 - one unreadable file must not stop the run
            logger.error(f"Failed to parse MHTML {basename}: {exc}")
            return (f"[Error: Could not read file {basename}]", None)
        fd, tmp = tempfile.mkstemp(suffix=".html", prefix="recallhub-mht-")
        os.close(fd)
        try:
            with open(tmp, "w", encoding="utf-8") as out:
                out.write(html_as_utf8(html))
            converter = create_document_converter()
            result = converter.convert(tmp)
            logger.info(f"Converted MHTML archive via its HTML part: {basename}")
            return (export_document_markdown(result.document), result.document)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to convert the HTML part of {basename}: {exc}")
            return (f"[Error: Could not read file {basename}]", None)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _read_legacy_text_file(self, file_path: str) -> tuple[str, Optional[Any]]:
        """Text of a DOS-era word-processor file (:data:`LEGACY_TEXT_EXTENSIONS`).

        Olympus ``.dss`` dictation audio (same extension) is recognised by its magic and
        stored as a placeholder; RTF content under these extensions goes to the RTF reader.

        Args:
            file_path: Path to the file.

        Returns:
            Tuple of (text, None).
        """
        basename = os.path.basename(file_path)
        try:
            with open(file_path, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            logger.error(f"Failed to open {basename}: {exc}")
            return (f"[Error: Could not read file {basename}]", None)
        if raw.startswith(_DSS_AUDIO_MAGIC):
            logger.info(f"Skipping Olympus DSS dictation (no DSS transcription): {basename}")
            return (f"[Audio: {basename} - Olympus DSS dictation, not transcribed]", None)
        return self._text_from_sniffed_bytes(raw, basename, prefer_legacy=True)

    def _read_sniffed_text_file(self, file_path: str) -> tuple[str, Optional[Any]]:
        """Text of a file that is plain text, RTF or HTML under a custom extension (``.wvb``)."""
        basename = os.path.basename(file_path)
        try:
            with open(file_path, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            logger.error(f"Failed to open {basename}: {exc}")
            return (f"[Error: Could not read file {basename}]", None)
        return self._text_from_sniffed_bytes(raw, basename)

    def _read_gzipped_text_file(self, file_path: str) -> tuple[str, Optional[Any]]:
        """Text of a gzip-packed template (``.mtt``): the packed bytes are sniffed like ``.wvb``."""
        import gzip

        basename = os.path.basename(file_path)
        try:
            with open(file_path, "rb") as handle:
                raw = gzip.decompress(handle.read())
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            logger.error(f"Failed to unpack {basename}: {exc}")
            return (f"[Error: Could not read file {basename}]", None)
        return self._text_from_sniffed_bytes(raw, basename)

    def _text_from_sniffed_bytes(
        self, raw: bytes, basename: str, prefer_legacy: bool = False
    ) -> tuple[str, Optional[Any]]:
        """Dispatch sniffed bytes (:func:`sniff_text_bytes`) to the RTF, HTML or text path."""
        import tempfile

        kind, text = sniff_text_bytes(raw, prefer_legacy=prefer_legacy)
        if kind == "rtf":
            try:
                from striprtf.striprtf import rtf_to_text

                text = rtf_to_text(text, encoding=rtf_code_page(raw), errors="replace")
            except ImportError:
                logger.error(f"striprtf is not installed; cannot read RTF content of {basename}")
                return (f"[Error: Could not read file {basename} (no RTF reader installed)]", None)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed to read RTF content of {basename}: {exc}")
                return (f"[Error: Could not read file {basename}]", None)
        elif kind == "html":
            fd, tmp = tempfile.mkstemp(suffix=".html", prefix="recallhub-sniffed-")
            os.close(fd)
            try:
                with open(tmp, "w", encoding="utf-8") as out:
                    out.write(html_as_utf8(text))
                result = create_document_converter().convert(tmp)
                logger.info(f"Converted HTML content of {basename}")
                return (export_document_markdown(result.document), result.document)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed to convert HTML content of {basename}: {exc}")
                return (f"[Error: Could not read file {basename}]", None)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        text = text.strip()
        if not text:
            logger.warning(f"No readable text in {basename}")
            return (f"[Document: {basename} - no text content]", None)
        logger.info(f"Extracted {len(text)} chars ({kind}) from {basename}")
        return (text, None)

    def _read_renamed_office_file(self, file_path: str, suffix: str) -> tuple[str, Optional[Any]]:
        """Convert a file whose content is a Docling format under another extension.

        Docling picks its reader from the file name and, for Office zips, from the main
        part's content type (``.dotx`` templates are ``.docx`` containers, some ``.xls``
        exports are HTML), so a temporary copy with the right suffix - and for templates
        the document content type - is converted instead.

        Args:
            file_path: Path to the file.
            suffix: Extension Docling should see, e.g. ``.docx``.

        Returns:
            Tuple of (markdown, DoclingDocument); an ``[Error: …]`` placeholder when unreadable.
        """
        import shutil
        import tempfile

        basename = os.path.basename(file_path)
        fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="recallhub-renamed-")
        os.close(fd)
        try:
            if os.path.splitext(file_path)[1].lower() in _OOXML_TEMPLATE_TYPES:
                ooxml_template_as_document(file_path, tmp)
            else:
                shutil.copyfile(file_path, tmp)
            converter = create_document_converter()
            result = converter.convert(tmp)
            logger.info(f"Converted {basename} as {suffix}")
            return (export_document_markdown(result.document), result.document)
        except Exception as exc:  # noqa: BLE001 - one unreadable file must not stop the run
            logger.error(f"Failed to convert {basename} as {suffix}: {exc}")
            return (f"[Error: Could not read file {basename}]", None)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _read_multipage_tiff(self, file_path: str, frames: int) -> tuple[str, Optional[Any]]:
        """OCR every page of a multi-page TIFF scan; pages are separated by a rule.

        Each frame is written to a temporary PNG (with the TIFF's DPI when it is usable)
        and converted like a single image. The result carries no DoclingDocument, so the
        chunker works from the Markdown.

        Args:
            file_path: Path to the .tif/.tiff file.
            frames: Number of frames (from :func:`image_frame_count`).

        Returns:
            Tuple of (markdown, None).
        """
        import tempfile
        from PIL import Image, ImageSequence

        basename = os.path.basename(file_path)
        logger.info(f"Processing multi-page TIFF with OCR via Docling: {basename} ({frames} pages)")
        pages: List[str] = []
        failed = 0
        try:
            with Image.open(file_path) as img:
                dpi = img.info.get("dpi")
                values = [_dpi_number(v) for v in dpi] if isinstance(dpi, tuple) and len(dpi) == 2 else None
                save_dpi = tuple(values) if values and all(v and v > 0 for v in values) else None
                for index, frame in enumerate(ImageSequence.Iterator(img), start=1):
                    fd, tmp = tempfile.mkstemp(suffix=".png", prefix="recallhub-tiff-")
                    os.close(fd)
                    try:
                        page = frame.convert("RGB") if frame.mode not in ("L", "RGB") else frame.copy()
                        page.save(tmp, "PNG", dpi=save_dpi) if save_dpi else page.save(tmp, "PNG")
                        converter = create_image_converter(image_path=tmp)
                        result = converter.convert(tmp)
                        text = export_document_markdown(result.document).strip()
                    except Exception as exc:  # noqa: BLE001 - keep the other pages
                        failed += 1
                        logger.warning(f"OCR failed for page {index} of {basename}: {exc}")
                        text = ""
                    finally:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    if text:
                        pages.append(text)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to read multi-page TIFF {basename}: {exc}")
            return (f"[Image: {basename} - OCR failed: {exc}]", None)
        if not pages:
            logger.warning(f"OCR produced no text for any page of {basename}")
            return (f"[Image: {basename} - OCR extracted minimal text]", None)
        logger.info(
            f"OCRed {len(pages)} of {frames} pages of {basename}"
            + (f" ({failed} failed)" if failed else "")
        )
        return ("\n\n---\n\n".join(pages), None)

    def _read_doc_file(self, file_path: str) -> tuple[str, Optional[Any]]:
        """
        Extract text content from legacy .doc files using antiword.
        
        Docling does not support the legacy .doc binary format (only .docx).
        This method uses the antiword command-line tool as a reliable fallback.
        
        Args:
            file_path: Path to the .doc file
            
        Returns:
            Tuple of (text_content, None)
        """
        import subprocess
        import shutil
        
        basename = os.path.basename(file_path)
        
        # Try antiword first (best quality for .doc)
        if shutil.which('antiword'):
            try:
                result = subprocess.run(
                    ['antiword', '-w', '0', file_path],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0 and result.stdout.strip():
                    logger.info(f"Successfully extracted .doc with antiword: {basename}")
                    return (result.stdout.strip(), None)
                else:
                    logger.warning(
                        f"antiword returned empty or failed for {basename}: "
                        f"{result.stderr.strip()}"
                    )
            except subprocess.TimeoutExpired:
                logger.warning(f"antiword timed out for {basename}")
            except Exception as e:
                logger.warning(f"antiword failed for {basename}: {e}")
        else:
            logger.warning(
                "antiword not installed. Legacy .doc extraction will be limited. "
                "Install with: apt-get install antiword"
            )
        
        # Fallback: try reading as text (works for some .doc files that are actually RTF/text)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                # Check if content looks like binary garbage
                printable_ratio = sum(1 for c in content[:1000] if c.isprintable() or c.isspace()) / max(len(content[:1000]), 1)
                if printable_ratio > 0.8:
                    logger.info(f"Extracted .doc as text (text-like content): {basename}")
                    return (content, None)
                else:
                    logger.warning(f"Skipping .doc with binary content (not readable as text): {basename}")
                    return (f"[Skipped: {basename} - legacy .doc format requires antiword]", None)
        except UnicodeDecodeError:
            logger.warning(f"Cannot read .doc as text: {basename}")
            return (f"[Skipped: {basename} - legacy .doc format requires antiword]", None)

    def _read_msg_file(self, file_path: str) -> tuple[str, Optional[Any]]:
        """
        Extract text content from Outlook .msg email files.

        Extracts subject, sender, date, recipients, and body text. Attachment filenames are
        always listed; when ``MSG_EXTRACT_ATTACHMENTS`` is on, the text of substantive
        attachments (PDF/doc/docx/images) is also extracted and appended
        (:func:`extract_email_attachment_text`).
        
        Args:
            file_path: Path to the .msg file
            
        Returns:
            Tuple of (markdown_content, None)
        """
        try:
            import extract_msg
            
            msg = extract_msg.Message(file_path)
            
            parts = []
            
            # Email header metadata
            if msg.subject:
                parts.append(f"# {msg.subject}")
            
            header_lines = []
            if msg.sender:
                header_lines.append(f"**From:** {msg.sender}")
            if msg.to:
                header_lines.append(f"**To:** {msg.to}")
            if msg.cc:
                header_lines.append(f"**CC:** {msg.cc}")
            if msg.date:
                header_lines.append(f"**Date:** {msg.date}")
            
            if header_lines:
                parts.append("\n".join(header_lines))
                parts.append("---")
            
            # Email body
            body = msg.body
            if body:
                parts.append(body.strip())
            else:
                # Try HTML body as fallback
                html_body = msg.htmlBody
                if html_body:
                    # Basic HTML stripping - Docling can handle HTML properly
                    # but for .msg we just need the text
                    import re
                    text = re.sub(r'<[^>]+>', ' ', html_body if isinstance(html_body, str) else html_body.decode('utf-8', errors='replace'))
                    text = re.sub(r'\s+', ' ', text).strip()
                    parts.append(text)
            
            # List attachments (always) and, when enabled, extract their text
            if msg.attachments:
                parts.append("\n---\n**Attachments:**")
                att_pairs: List[Tuple[str, bytes]] = []
                for att in msg.attachments:
                    att_name = getattr(att, 'longFilename', None) or getattr(att, 'shortFilename', None) or 'unnamed'
                    parts.append(f"- {att_name}")
                    data = getattr(att, 'data', None)
                    if isinstance(data, (bytes, bytearray)):
                        att_pairs.append((att_name, bytes(data)))
                enabled, max_bytes, max_count = resolve_msg_attachment_config()
                if enabled and att_pairs:
                    blocks = extract_email_attachment_text(
                        att_pairs, self._read_document, max_bytes, max_count
                    )
                    if blocks:
                        parts.append("\n---\n## Attachment contents")
                        parts.extend(blocks)

            msg.close()
            
            content = "\n\n".join(parts)
            if not content.strip():
                content = f"[Empty email: {os.path.basename(file_path)}]"
            
            logger.info(f"Successfully extracted email: {os.path.basename(file_path)}")
            return (content, None)
            
        except ImportError:
            logger.error("extract-msg package not installed. Install with: pip install extract-msg")
            return (f"[Error: extract-msg package required for .msg files]", None)
        except Exception as e:
            logger.error(f"Failed to extract .msg file {file_path}: {e}")
            return (f"[Error: Could not read email {os.path.basename(file_path)}: {e}]", None)

    def _read_eml_file(self, file_path: str) -> tuple[str, Optional[Any]]:
        """
        Extract text content from RFC 5322 email files (.eml).

        Mirrors :meth:`_read_msg_file`: subject as heading, sender, recipients and
        date as header lines, then the plain-text body (HTML body as fallback).
        Attachment filenames are listed; when ``MSG_EXTRACT_ATTACHMENTS`` is on, the
        text of substantive attachments is extracted and appended too.

        Args:
            file_path: Path to the .eml file

        Returns:
            Tuple of (markdown_content, None)
        """
        import re
        from email import policy
        from email.parser import BytesParser

        try:
            with open(file_path, "rb") as fh:
                msg = BytesParser(policy=policy.default).parse(fh)

            parts = []
            if msg["subject"]:
                parts.append(f"# {msg['subject']}")
            header_lines = []
            for label, key in (("From", "from"), ("To", "to"), ("CC", "cc"), ("Date", "date")):
                if msg[key]:
                    header_lines.append(f"**{label}:** {msg[key]}")
            if header_lines:
                parts.append("\n".join(header_lines))
                parts.append("---")

            body_part = msg.get_body(preferencelist=("plain", "html"))
            if body_part is not None:
                body = body_part.get_content()
                if body_part.get_content_subtype() == "html":
                    body = re.sub(r"<[^>]+>", " ", body)
                    body = re.sub(r"\s+", " ", body)
                if body and body.strip():
                    parts.append(body.strip())

            att_pairs: List[Tuple[str, bytes]] = []
            attachments = []
            for a in msg.iter_attachments():
                name = a.get_filename() or "unnamed"
                attachments.append(name)
                payload = a.get_payload(decode=True)
                if isinstance(payload, (bytes, bytearray)):
                    att_pairs.append((name, bytes(payload)))
            if attachments:
                parts.append("\n---\n**Attachments:**")
                parts.extend(f"- {name}" for name in attachments)
                enabled, max_bytes, max_count = resolve_msg_attachment_config()
                if enabled and att_pairs:
                    blocks = extract_email_attachment_text(
                        att_pairs, self._read_document, max_bytes, max_count
                    )
                    if blocks:
                        parts.append("\n---\n## Attachment contents")
                        parts.extend(blocks)

            content = "\n\n".join(parts)
            if not content.strip():
                content = f"[Empty email: {os.path.basename(file_path)}]"
            logger.info(f"Successfully extracted email: {os.path.basename(file_path)}")
            return (content, None)
        except Exception as e:
            logger.error(f"Failed to extract .eml file {file_path}: {e}")
            return (f"[Error: Could not read email {os.path.basename(file_path)}: {e}]", None)

    def _transcribe_audio(self, file_path: str) -> tuple[str, Optional[Any]]:
        """
        Transcribe audio file using the best available method.
        
        Priority:
        1. If offline mode enabled with audio model configured -> Use local Ollama Whisper
        2. If OpenAI API key available -> Use cloud Whisper API (faster)
        3. Fallback to local Docling Whisper (slower but works offline)
        
        For large files (>25MB), splits audio into chunks and processes in parallel.

        Args:
            file_path: Path to the audio file

        Returns:
            Tuple of (markdown_content, None) - no DoclingDocument for API transcription
        """
        # Check if offline mode is enabled with audio model configured
        offline_audio_enabled = self._check_offline_audio_mode()
        
        if offline_audio_enabled:
            logger.info("Offline mode enabled - using local Whisper transcription")
            return self._transcribe_audio_local(file_path)
        
        # Try cloud-based transcription
        try:
            from pathlib import Path
            from openai import OpenAI
            import os as os_module

            audio_path = Path(file_path).resolve()
            logger.info(
                f"Transcribing audio via OpenAI Whisper API: {audio_path.name}"
            )

            # Verify file exists
            if not audio_path.exists():
                raise FileNotFoundError(f"Audio file not found: {audio_path}")

            # Check file size (OpenAI limit is 25MB)
            file_size_mb = audio_path.stat().st_size / (1024 * 1024)
            
            # For large files, use chunked transcription
            if file_size_mb > 25:
                logger.info(
                    f"Audio file {file_size_mb:.1f}MB exceeds 25MB limit, "
                    "using chunked transcription for optimal speed"
                )
                return self._transcribe_audio_chunked(file_path)

            # Initialize OpenAI client (use LLM API key which is OpenAI key)
            client = OpenAI(api_key=self.settings.llm_api_key)

            # Transcribe with OpenAI Whisper API
            with open(audio_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="verbose_json",  # Get timestamps
                    timestamp_granularities=["segment"]
                )

            # Format as markdown with timestamps
            markdown_lines = [f"# {audio_path.stem}\n"]
            markdown_lines.append(f"**Source**: {audio_path.name}\n")
            markdown_lines.append(f"**Language**: {transcription.language}\n")
            markdown_lines.append(f"**Duration**: {transcription.duration:.1f}s\n")
            markdown_lines.append("\n---\n\n")

            # Add segments with timestamps
            if hasattr(transcription, 'segments') and transcription.segments:
                for segment in transcription.segments:
                    start_time = segment.get('start', 0)
                    end_time = segment.get('end', 0)
                    text = segment.get('text', '').strip()
                    
                    # Format timestamp as [MM:SS]
                    start_min, start_sec = divmod(int(start_time), 60)
                    end_min, end_sec = divmod(int(end_time), 60)
                    
                    markdown_lines.append(
                        f"[{start_min:02d}:{start_sec:02d} - {end_min:02d}:{end_sec:02d}] {text}\n\n"
                    )
            else:
                # Fallback to plain text if no segments
                markdown_lines.append(transcription.text)

            markdown_content = "".join(markdown_lines)
            logger.info(
                f"Successfully transcribed {audio_path.name} "
                f"(language: {transcription.language}, duration: {transcription.duration:.1f}s)"
            )

            return (markdown_content, None)

        except Exception as e:
            logger.error(f"OpenAI Whisper API failed for {file_path}: {e}")
            logger.info("Falling back to chunked transcription...")
            try:
                return self._transcribe_audio_chunked(file_path)
            except Exception as e2:
                logger.error(f"Chunked transcription also failed: {e2}")
                logger.info("Falling back to local Whisper transcription...")
                return self._transcribe_audio_local(file_path)

    def _check_offline_audio_mode(self) -> bool:
        """
        Check if offline mode is enabled with audio model configured.
        
        Reads the offline config from MongoDB if available.
        
        Returns:
            True if should use local audio transcription
        """
        try:
            # Try to read offline config from environment or check MongoDB
            import os
            
            # Quick check - if no API key or explicitly offline
            if not self.settings.llm_api_key:
                logger.info("No OpenAI API key - will use local transcription")
                return True
            
            # Check for offline mode environment variable
            if os.environ.get('OFFLINE_MODE', '').lower() == 'true':
                logger.info("OFFLINE_MODE environment variable set")
                return True
            
            # TODO: Could also check MongoDB for offline config, but that would
            # require async call. For now, rely on env var or missing API key.
            
            return False
            
        except Exception as e:
            logger.warning(f"Error checking offline mode: {e}")
            return False

    def _transcribe_audio_chunked(self, file_path: str) -> tuple[str, Optional[Any]]:
        """
        Transcribe large audio file by splitting into chunks and processing in parallel.
        
        This is MUCH faster than local Whisper for large files (2 hour audio in 3-5 min).

        Args:
            file_path: Path to the audio file

        Returns:
            Tuple of (markdown_content, None)
        """
        import tempfile
        import concurrent.futures
        from pathlib import Path
        from openai import OpenAI
        
        audio_path = Path(file_path).resolve()
        logger.info(f"Starting chunked transcription for: {audio_path.name}")
        
        try:
            from pydub import AudioSegment
        except ImportError:
            logger.warning("pydub not installed, falling back to local Whisper")
            return self._transcribe_audio_local(file_path)
        
        # Load audio file
        file_ext = audio_path.suffix.lower()
        if file_ext == '.mp3':
            audio = AudioSegment.from_mp3(str(audio_path))
        elif file_ext == '.wav':
            audio = AudioSegment.from_wav(str(audio_path))
        elif file_ext in ['.m4a', '.mp4']:
            audio = AudioSegment.from_file(str(audio_path), format="m4a")
        elif file_ext == '.flac':
            audio = AudioSegment.from_file(str(audio_path), format="flac")
        else:
            audio = AudioSegment.from_file(str(audio_path))
        
        total_duration = len(audio) / 1000  # Duration in seconds
        logger.info(f"Audio duration: {total_duration/60:.1f} minutes")
        
        # Split into ~10 minute chunks (keeps each under 25MB for most formats)
        chunk_duration_ms = 10 * 60 * 1000  # 10 minutes in ms
        chunks = []
        chunk_files = []
        
        for i in range(0, len(audio), chunk_duration_ms):
            chunk = audio[i:i + chunk_duration_ms]
            chunk_start_time = i / 1000  # Convert to seconds
            chunks.append((chunk, chunk_start_time))
        
        logger.info(f"Split audio into {len(chunks)} chunks for parallel processing")
        
        # Export chunks to temporary files
        temp_dir = tempfile.mkdtemp(prefix="whisper_chunks_")
        try:
            for idx, (chunk, _) in enumerate(chunks):
                chunk_path = Path(temp_dir) / f"chunk_{idx:04d}.mp3"
                chunk.export(str(chunk_path), format="mp3", bitrate="64k")
                chunk_files.append(chunk_path)
            
            # Initialize OpenAI client
            client = OpenAI(api_key=self.settings.llm_api_key)
            
            # Process chunks in parallel (max 4 concurrent to respect rate limits)
            def transcribe_chunk(args):
                chunk_path, chunk_start = args
                with open(chunk_path, "rb") as audio_file:
                    result = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        response_format="verbose_json",
                        timestamp_granularities=["segment"]
                    )
                return (chunk_start, result)
            
            results = []
            chunk_args = list(zip(chunk_files, [c[1] for c in chunks]))
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                future_to_chunk = {
                    executor.submit(transcribe_chunk, args): args 
                    for args in chunk_args
                }
                for future in concurrent.futures.as_completed(future_to_chunk):
                    try:
                        results.append(future.result())
                    except Exception as e:
                        logger.error(f"Chunk transcription failed: {e}")
            
            # Sort by start time and combine results
            results.sort(key=lambda x: x[0])
            
            # Format as markdown with timestamps
            markdown_lines = [f"# {audio_path.stem}\n"]
            markdown_lines.append(f"**Source**: {audio_path.name}\n")
            if results:
                markdown_lines.append(f"**Language**: {results[0][1].language}\n")
            markdown_lines.append(f"**Duration**: {total_duration:.1f}s ({total_duration/60:.1f} min)\n")
            markdown_lines.append(f"**Processed**: {len(results)} chunks in parallel\n")
            markdown_lines.append("\n---\n\n")
            
            for chunk_start, transcription in results:
                if hasattr(transcription, 'segments') and transcription.segments:
                    for segment in transcription.segments:
                        # Adjust timestamps by chunk start time
                        start_time = segment.get('start', 0) + chunk_start
                        end_time = segment.get('end', 0) + chunk_start
                        text = segment.get('text', '').strip()
                        
                        start_min, start_sec = divmod(int(start_time), 60)
                        end_min, end_sec = divmod(int(end_time), 60)
                        
                        markdown_lines.append(
                            f"[{start_min:02d}:{start_sec:02d} - {end_min:02d}:{end_sec:02d}] {text}\n\n"
                        )
                else:
                    markdown_lines.append(transcription.text + "\n\n")
            
            markdown_content = "".join(markdown_lines)
            logger.info(
                f"Successfully transcribed {audio_path.name} using chunked processing "
                f"({len(chunks)} chunks, {total_duration/60:.1f} min)"
            )
            
            return (markdown_content, None)
            
        finally:
            # Clean up temp files
            import shutil
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

    def _transcribe_audio_local(self, file_path: str) -> tuple[str, Optional[Any]]:
        """
        Fallback: Transcribe audio file using local Whisper via Docling.
        Used when OpenAI API fails or file is too large.

        Args:
            file_path: Path to the audio file

        Returns:
            Tuple of (markdown_content, docling_document)
        """
        try:
            from pathlib import Path
            from docling.document_converter import (
                DocumentConverter,
                AudioFormatOption
            )
            from docling.datamodel.pipeline_options import AsrPipelineOptions
            from docling.datamodel.base_models import InputFormat
            from docling.pipeline.asr_pipeline import AsrPipeline
            from docling.datamodel.pipeline_options_asr_model import (
                InlineAsrNativeWhisperOptions,
                InferenceAsrFramework
            )
            from docling.datamodel.accelerator_options import AcceleratorDevice

            audio_path = Path(file_path).resolve()
            logger.info(
                f"Transcribing audio locally with Whisper: {audio_path.name}"
            )

            if not audio_path.exists():
                raise FileNotFoundError(f"Audio file not found: {audio_path}")

            # Configure with default English (auto-detect not supported with None)
            asr_options = InlineAsrNativeWhisperOptions(
                repo_id="turbo",
                language="en",  # Default to English
                timestamps=True,
                word_timestamps=True,
                verbose=True,
                temperature=0.0,
                max_new_tokens=256,
                max_time_chunk=30.0,
                inference_framework=InferenceAsrFramework.WHISPER,
                supported_devices=[AcceleratorDevice.CPU, AcceleratorDevice.CUDA],
            )

            pipeline_options = AsrPipelineOptions()
            pipeline_options.asr_options = asr_options

            converter = DocumentConverter(
                format_options={
                    InputFormat.AUDIO: AudioFormatOption(
                        pipeline_cls=AsrPipeline,
                        pipeline_options=pipeline_options,
                    )
                }
            )

            result = converter.convert(audio_path)
            markdown_content = export_document_markdown(result.document)
            logger.info(f"Successfully transcribed {audio_path.name} locally")

            return (markdown_content, result.document)

        except Exception as e:
            logger.error(f"Local Whisper failed for {file_path}: {e}")
            return (
                f"[Error: Could not transcribe audio file "
                f"{os.path.basename(file_path)}]",
                None
            )

    def _extract_title(self, content: str, file_path: str) -> str:
        """
        Extract title from document content or filename.

        Args:
            content: Document content
            file_path: Path to the document file

        Returns:
            Document title
        """
        # Try to find markdown title
        lines = content.split('\n')
        for line in lines[:10]:  # Check first 10 lines
            line = line.strip()
            if line.startswith('# '):
                return line[2:].strip()

        # Fallback to filename
        return os.path.splitext(os.path.basename(file_path))[0]

    def _extract_document_metadata(
        self,
        content: str,
        file_path: str
    ) -> Dict[str, Any]:
        """
        Extract metadata from document content.

        Args:
            content: Document content
            file_path: Path to the document file

        Returns:
            Document metadata dictionary
        """
        metadata = {
            "file_path": file_path,
            "file_size": len(content),
            "ingestion_date": datetime.now().isoformat()
        }

        # Try to extract YAML frontmatter
        if content.startswith('---'):
            try:
                import yaml
                end_marker = content.find('\n---\n', 4)
                if end_marker != -1:
                    frontmatter = content[4:end_marker]
                    yaml_metadata = yaml.safe_load(frontmatter)
                    if isinstance(yaml_metadata, dict):
                        metadata.update(yaml_metadata)
            except ImportError:
                logger.warning(
                    "PyYAML not installed, skipping frontmatter extraction"
                )
            except Exception as e:
                logger.warning(f"Failed to parse frontmatter: {e}")

        # Extract some basic metadata from content
        lines = content.split('\n')
        metadata['line_count'] = len(lines)
        metadata['word_count'] = len(content.split())

        return metadata

    async def _delete_existing_document(self, source: str) -> int:
        """
        Delete existing document and its chunks by source path.
        
        This prevents duplicate documents when re-ingesting the same file.
        Chunks are deleted first to respect foreign key relationships.
        
        Args:
            source: Document source path to match against
            
        Returns:
            Number of documents deleted (0 or 1)
        """
        documents_collection = self.db[
            self.settings.mongodb_collection_documents
        ]
        chunks_collection = self.db[self.settings.mongodb_collection_chunks]
        
        # Find existing document(s) with this source
        existing_docs = []
        cursor = documents_collection.find({"source": source}, {"_id": 1})
        async for doc in cursor:
            existing_docs.append(doc["_id"])
        
        if not existing_docs:
            return 0
        
        # Delete chunks for these documents first
        chunks_result = await chunks_collection.delete_many(
            {"document_id": {"$in": existing_docs}}
        )
        
        # Delete the document(s)
        docs_result = await documents_collection.delete_many(
            {"_id": {"$in": existing_docs}}
        )
        
        logger.info(
            f"Replaced existing document '{source}': "
            f"deleted {docs_result.deleted_count} doc(s), "
            f"{chunks_result.deleted_count} chunk(s)"
        )
        
        return docs_result.deleted_count

    async def _save_to_mongodb(
        self,
        title: str,
        source: str,
        content: str,
        chunks: List[DocumentChunk],
        metadata: Dict[str, Any],
        content_hash: Optional[str] = None
    ) -> str:
        """
        Save document and chunks to MongoDB.
        
        Always deletes any existing document with the same source path
        before inserting, preventing duplicates regardless of ingestion mode.

        Args:
            title: Document title
            source: Document source path
            content: Document content
            chunks: List of document chunks with embeddings
            metadata: Document metadata
            content_hash: SHA256 hash of original file for deduplication

        Returns:
            Document ID (ObjectId as string)

        Raises:
            Exception: If MongoDB operations fail
        """
        # Get collection references
        documents_collection = self.db[
            self.settings.mongodb_collection_documents
        ]
        chunks_collection = self.db[self.settings.mongodb_collection_chunks]

        # Delete any existing document with the same source to prevent duplicates
        await self._delete_existing_document(source)

        # Insert document
        document_dict = {
            "title": title,
            "source": source,
            "content": content,
            "content_hash": content_hash,  # Store hash for deduplication
            "metadata": {
                **metadata,
                "chunks_count": len(chunks)  # Store chunks count for efficient retrieval
            },
            "created_at": datetime.now()
        }

        document_result = await documents_collection.insert_one(document_dict)
        document_id = document_result.inserted_id

        logger.info(f"Inserted document with ID: {document_id}")

        # Insert chunks with embeddings as Python lists
        chunk_dicts = []
        for chunk in chunks:
            chunk_dict = {
                "document_id": document_id,
                "content": chunk.content,
                "embedding": chunk.embedding,  # Python list, NOT string!
                "chunk_index": chunk.index,
                "metadata": chunk.metadata,
                "token_count": chunk.token_count,
                "created_at": datetime.now()
            }
            chunk_dicts.append(chunk_dict)

        # Batch insert with ordered=False for partial success
        if chunk_dicts:
            await chunks_collection.insert_many(chunk_dicts, ordered=False)
            logger.info(f"Inserted {len(chunk_dicts)} chunks")

        return str(document_id)

    async def _clean_databases(self) -> None:
        """Clean existing data from MongoDB collections."""
        logger.warning("Cleaning existing data from MongoDB...")

        # Get collection references
        documents_collection = self.db[
            self.settings.mongodb_collection_documents
        ]
        chunks_collection = self.db[self.settings.mongodb_collection_chunks]

        # Delete all chunks first (to respect FK relationships)
        chunks_result = await chunks_collection.delete_many({})
        logger.info(f"Deleted {chunks_result.deleted_count} chunks")

        # Delete all documents
        docs_result = await documents_collection.delete_many({})
        logger.info(f"Deleted {docs_result.deleted_count} documents")

    # -- Conversion step: thread pool or worker processes --------------------------------

    def _start_conversion_pool(self, size: int) -> Any:
        """Create this run's conversion worker processes (started on their first file).

        The CPU threads (Docling models, Tesseract) are shared across the ``size`` worker
        processes instead of the thread pool, which then only hashes and discovers files.

        Args:
            size: Number of worker processes, one per file slot (``max_concurrent_files``).

        Returns:
            The ``ConversionProcessPool`` now set as :attr:`_conversion_pool`.
        """
        from src.ingestion.conversion_workers import ConversionProcessPool, WorkerInit

        try:
            from src.ingestion.ocr_tesseract import configure_tesseract_threads
        except ImportError:  # Docling not installed: no Tesseract either
            pass
        else:
            configure_tesseract_threads(size)
        init = WorkerInit(
            pool_workers=size,
            accelerator=asdict(_accelerator_config),
            log_level=logging.getLogger().getEffectiveLevel() or logging.INFO,
        )
        self._conversion_pool = ConversionProcessPool(size, init=init)
        logger.info(
            f"Process isolation on: {size} conversion worker processes (each started on its first "
            f"file), Docling threads per model: {docling_num_threads(size)}"
        )
        return self._conversion_pool

    async def _stop_conversion_pool(self) -> None:
        """End this run's conversion worker processes, if any (off the event loop)."""
        pool = self._conversion_pool
        self._conversion_pool = None
        if pool is None:
            return
        await asyncio.get_running_loop().run_in_executor(None, pool.shutdown)
        if pool.kills:
            logger.info(f"Conversion workers killed during the run: {pool.kills} (processes started: {pool.starts})")

    async def _convert_document(
        self, file_path: str, handle: Optional[ConversionHandle] = None
    ) -> tuple[str, Optional[Any]]:
        """Run :meth:`_read_document` off the event loop.

        In this run's conversion worker process when process isolation is on, otherwise on
        the shared thread pool. ``handle`` learns when the conversion starts and, on the
        thread pool, which future keeps running after a cancel.

        Args:
            file_path: Document to convert.
            handle: The loop's view of this conversion; the one the concurrent loop put in
                the task's context (``_conversion_handle_var``) when omitted, else a private one.

        Returns:
            ``(markdown_content, docling_document)`` as :meth:`_read_document` returns it.
        """
        handle = handle or _conversion_handle_var.get() or ConversionHandle()
        pool = self._conversion_pool
        if pool is not None:
            content, document, notes = await pool.convert_with_notes(file_path, on_started=handle.mark_started)
            handle.notes.update(notes)
            return content, document
        loop = asyncio.get_running_loop()

        def read() -> tuple[str, Optional[Any]]:
            try:
                loop.call_soon_threadsafe(handle.mark_started)
            except RuntimeError:  # loop already closed: nothing waits for this conversion
                pass
            with conversion_notes() as notes:
                outcome = self._read_document(file_path)
            handle.notes.update(notes)
            return outcome

        future = self.get_executor().submit(read)
        handle.thread_future = future
        return await asyncio.wrap_future(future, loop=loop)

    @staticmethod
    async def _run_with_budget(coro: Awaitable[Any], handle: ConversionHandle, timeout_seconds: float) -> Any:
        """Await ``coro`` with ``timeout_seconds`` counted from the moment its conversion starts.

        Waiting for a pool thread or for a conversion worker's warm-up is not charged to the
        file: once the conversion starts, the file has the whole budget from that moment. A
        file whose conversion has not started within one budget times out all the same, so a
        step stuck before the conversion cannot hold a slot forever. Like ``asyncio.wait_for``,
        a timeout cancels the inner task first (which kills a conversion worker process) and
        raises ``asyncio.TimeoutError``.

        Args:
            coro: The per-file coroutine (``_ingest_single_document``).
            handle: The conversion handle of that file; it is put in the context the task
                inherits, so the conversion step finds it without a parameter.
            timeout_seconds: Budget from the conversion start.

        Returns:
            The coroutine's result.
        """
        token = _conversion_handle_var.set(handle)
        try:
            task = asyncio.ensure_future(coro)  # copies the context: the handle travels with the task
        finally:
            _conversion_handle_var.reset(token)
        started = asyncio.ensure_future(handle.started.wait())
        try:
            done, _ = await asyncio.wait(
                {task, started}, timeout=timeout_seconds, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            raise
        finally:
            started.cancel()
        if task in done:
            return task.result()
        if not done:
            # Nothing started within the budget: stuck before the conversion (or never converting)
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            raise asyncio.TimeoutError()
        remaining = max(0.0, (handle.started_at or time.monotonic()) + timeout_seconds - time.monotonic())
        return await asyncio.wait_for(task, timeout=remaining)

    async def _ingest_single_document(
        self,
        file_path: str,
        content_hash: Optional[str] = None
    ) -> IngestionResult:
        """
        Ingest a single document.

        Emits ``file.start`` / ``file.end`` telemetry (with per-step timings) through
        :mod:`src.ingestion.telemetry_hooks`; a no-op unless a host process registered a sink.
        The concurrent loop watches the conversion step through the :class:`ConversionHandle`
        it put in the task's context (see :meth:`_convert_document`).

        Args:
            file_path: Path to the document file
            content_hash: Pre-computed SHA256 hash of the file (for deduplication)

        Returns:
            Ingestion result
        """
        with telemetry_hooks.file_scope(file_path) as trace:
            result = await self._ingest_single_document_impl(file_path, content_hash)
            if result.errors:
                trace.outcome = "empty" if result.errors == ["No chunks created"] else "error"
            telemetry_hooks.note(chunks=result.chunks_created)
            return result

    async def _ingest_single_document_impl(
        self,
        file_path: str,
        content_hash: Optional[str] = None
    ) -> IngestionResult:
        """Body of :meth:`_ingest_single_document`, with each step timed for telemetry."""
        start_time = datetime.now()

        # Compute hash if not provided
        if content_hash is None:
            loop = asyncio.get_running_loop()
            with telemetry_hooks.step("hash"):
                content_hash = await loop.run_in_executor(
                    self.get_executor(),
                    compute_file_hash,
                    file_path
                )

        # Convert off the event loop (thread pool, or this run's conversion worker process),
        # so API requests (login, status checks) are served during ingestion. The reader's
        # notes about the conversion (which scan-text fallback fired, F35) come back on the
        # ConversionHandle the loop put in the task's context; a sequential or direct call
        # gets a private one here.
        handle = _conversion_handle_var.get()
        handle_token: Optional[contextvars.Token] = None
        if handle is None:
            handle = ConversionHandle()
            handle_token = _conversion_handle_var.set(handle)
        try:
            with telemetry_hooks.step("convert"):
                document_content, docling_doc = await self._convert_document(file_path)
        finally:
            if handle_token is not None:
                _conversion_handle_var.reset(handle_token)
        ocr_fallback: Optional[str] = handle.notes.get("ocr_fallback") or None
        pages = getattr(docling_doc, "pages", None)
        telemetry_hooks.note(
            pages=len(pages) if isinstance(pages, (dict, list)) else None,
            chars=len(document_content) if document_content else 0,
            ocr_fallback=ocr_fallback,
        )

        # Post-OCR repair of statute references ("$ 94 ÄrzteG" -> "§ 94 ÄrzteG"); the Docling
        # document is repaired too because the chunker builds chunk text from its items.
        # A reader placeholder is never stored: "[Error: …]" / "[Skipped: …]" is a failed read
        # (F26), "[Document: … - no text content]" an empty file (no chunks, no warning)
        kind = placeholder_kind(document_content)
        if kind == "error":
            raise UnreadableFileError(document_content.strip().strip("[]"))
        if kind == "empty":
            logger.info(f"No text in {os.path.basename(file_path)}: {document_content.strip()}")
            return IngestionResult(
                document_id="",
                title=os.path.basename(file_path),
                chunks_created=0,
                processing_time_ms=(datetime.now() - start_time).total_seconds() * 1000,
                errors=["No chunks created"],
                ocr_fallback=ocr_fallback,
            )

        with telemetry_hooks.step("ocr_repair"):
            repaired_items = repair_docling_document(docling_doc)
            repaired_content = repair_ocr_text(document_content)
        if repaired_items or repaired_content != document_content:
            logger.info(
                f"OCR repair: restored § references in {os.path.basename(file_path)} "
                f"({repaired_items} document items)"
            )
            document_content = repaired_content

        # Yield control after heavy operation
        await asyncio.sleep(0)
        
        document_title = self._extract_title(document_content, file_path)
        
        # Find which documents folder this file belongs to for relative path
        document_source = os.path.basename(file_path)
        for folder in self.documents_folders:
            if os.path.abspath(file_path).startswith(os.path.abspath(folder)):
                document_source = os.path.relpath(file_path, folder)
                break

        # Extract metadata from content
        document_metadata = self._extract_document_metadata(
            document_content,
            file_path
        )

        logger.info(f"Processing document: {document_title}")

        # Chunk the document - pass DoclingDocument for HybridChunker
        with telemetry_hooks.step("chunk"):
            chunks = await self.chunker.chunk_document(
                content=document_content,
                title=document_title,
                source=document_source,
                metadata=document_metadata,
                docling_doc=docling_doc  # Pass DoclingDocument for HybridChunker
            )

        if not chunks:
            # An empty file is expected (Outlook ATT0000x.txt placeholders, frameset pages):
            # no warning for those, one for a file whose text produced no chunk (F9)
            log = logger.info if not (document_content or "").strip() else logger.warning
            log(f"No chunks created for {document_title}")
            return IngestionResult(
                document_id="",
                title=document_title,
                chunks_created=0,
                processing_time_ms=(
                    datetime.now() - start_time
                ).total_seconds() * 1000,
                errors=["No chunks created"],
                ocr_fallback=ocr_fallback,
            )

        logger.info(f"Created {len(chunks)} chunks")

        # Yield control to allow API requests between chunking and embedding
        await asyncio.sleep(0)

        # Generate embeddings
        with telemetry_hooks.step("embed"):
            embedded_chunks = await self.embedder.embed_chunks(chunks)
        logger.info(f"Generated embeddings for {len(embedded_chunks)} chunks")

        # Save to MongoDB
        with telemetry_hooks.step("save"):
            document_id = await self._save_to_mongodb(
                document_title,
                document_source,
                document_content,
                embedded_chunks,
                document_metadata,
                content_hash
            )

        logger.info(f"Saved document to MongoDB with ID: {document_id}")

        # Calculate processing time
        processing_time = (
            datetime.now() - start_time
        ).total_seconds() * 1000

        return IngestionResult(
            document_id=document_id,
            title=document_title,
            chunks_created=len(chunks),
            processing_time_ms=processing_time,
            errors=[],
            ocr_fallback=ocr_fallback,
        )

    async def _get_existing_sources(self) -> set:
        """
        Get set of already-ingested document sources from MongoDB.
        
        Returns:
            Set of source paths that are already in the database
        """
        documents_collection = self.db[
            self.settings.mongodb_collection_documents
        ]
        
        # Get all existing sources
        cursor = documents_collection.find({}, {"source": 1})
        existing = set()
        async for doc in cursor:
            if "source" in doc:
                existing.add(doc["source"])
        
        return existing

    async def _get_existing_hashes(self) -> Dict[str, str]:
        """
        Get mapping of content hashes to document sources from MongoDB.
        
        Used for content-based deduplication to detect identical files
        at different paths.
        
        Returns:
            Dict mapping content_hash to source path
        """
        documents_collection = self.db[
            self.settings.mongodb_collection_documents
        ]
        
        # Get all existing content hashes
        cursor = documents_collection.find(
            {"content_hash": {"$exists": True, "$ne": None}},
            {"content_hash": 1, "source": 1}
        )
        existing = {}
        async for doc in cursor:
            if doc.get("content_hash"):
                existing[doc["content_hash"]] = doc.get("source", "unknown")
        
        return existing

    async def ingest_documents(
        self,
        progress_callback: Optional[callable] = None,
        incremental: bool = True,
        max_concurrent_files: int = 1,
        job_id: Optional[str] = None,
        pause_checkpoint: Optional[Callable[[], Awaitable[None]]] = None,
        thread_pool_workers: Optional[int] = None,
        process_isolation: Optional[bool] = None,
        stats_callback: Optional[Callable[[Dict[str, int]], None]] = None,
        retry_failed_types: Optional[frozenset] = None,
        queue_callback: Optional[Callable[[List[str]], None]] = None,
    ) -> List[IngestionResult]:
        """Ingest all documents; see :meth:`_ingest_documents_impl` for the arguments.

        Binds ``job_id`` for :mod:`src.ingestion.telemetry_hooks`, so every per-file event of
        this run (including concurrent file tasks) carries it.
        """
        token = telemetry_hooks.bind_job(job_id) if job_id is not None else None
        try:
            return await self._ingest_documents_impl(
                progress_callback=progress_callback,
                incremental=incremental,
                max_concurrent_files=max_concurrent_files,
                job_id=job_id,
                pause_checkpoint=pause_checkpoint,
                thread_pool_workers=thread_pool_workers,
                process_isolation=process_isolation,
                stats_callback=stats_callback,
                retry_failed_types=retry_failed_types,
                queue_callback=queue_callback,
            )
        finally:
            if token is not None:
                telemetry_hooks.reset_job(token)

    async def _ingest_documents_impl(
        self,
        progress_callback: Optional[callable] = None,
        incremental: bool = True,
        max_concurrent_files: int = 1,
        job_id: Optional[str] = None,
        pause_checkpoint: Optional[Callable[[], Awaitable[None]]] = None,
        thread_pool_workers: Optional[int] = None,
        process_isolation: Optional[bool] = None,
        stats_callback: Optional[Callable[[Dict[str, int]], None]] = None,
        retry_failed_types: Optional[frozenset] = None,
        queue_callback: Optional[Callable[[List[str]], None]] = None,
    ) -> List[IngestionResult]:
        """
        Ingest all documents from the documents folder.

        Args:
            retry_failed_types: Error types the job asks to retry (:func:`retry_error_types`).
                In incremental mode with a ``job_id``, files the same job already recorded in
                ``failed_documents`` are skipped unless their error type is listed here (F23).
            queue_callback: Called once with the final list of files this run will process
                (after the incremental and failed-file filters), e.g. for the per-extension ETA.
            progress_callback: Optional callback for progress updates
            incremental: If True, skip already-ingested files (default: True)
            max_concurrent_files: Number of files to process concurrently (default: 1)
            job_id: Optional job ID to link stats to
            pause_checkpoint: Optional coroutine function awaited before each file
                starts. It may block to pause the run between files, or raise
                ``asyncio.CancelledError`` to abort it. The synchronous
                ``progress_callback`` cannot do either without blocking the event loop.
            thread_pool_workers: Size of the shared thread pool for this run (the
                performance config's ``thread_pool_workers``); ``INGEST_THREAD_POOL_WORKERS``
                or 4 when omitted, and never below ``max_concurrent_files``.
            process_isolation: Convert in killable worker processes (the performance
                config's ``process_isolation_enabled``); :func:`process_isolation_enabled`
                resolves the environment/settings default when omitted. Only the concurrent
                loop (``max_concurrent_files > 1``) has per-file timeouts and uses it.
            stats_callback: Called after every flush of per-file stats with the cumulative
                counts written so far (see :class:`IngestionStatsRecorder`); the API and the
                worker update the job's status counters from it.

        Returns:
            List of ingestion results
        """
        if not self._initialized:
            await self.initialize()

        # Size the shared pool for this run: conversions, hashing and discovery run on it
        self.configure_executor(resolve_thread_pool_workers(thread_pool_workers, max_concurrent_files))

        # Clean existing data if requested
        if self.clean_before_ingest:
            await self._clean_databases()
            existing_sources = set()  # Nothing exists after cleaning
            existing_hashes = {}  # No hashes after cleaning
        elif incremental:
            # Yield to event loop before potentially slow DB query
            await asyncio.sleep(0)
            # Get existing sources for incremental mode
            existing_sources = await self._get_existing_sources()
            logger.info(f"Found {len(existing_sources)} already-ingested documents")
            # Get existing hashes for content-based deduplication
            existing_hashes = await self._get_existing_hashes()
            logger.info(f"Found {len(existing_hashes)} content hashes for deduplication")
            # Yield again after DB query
            await asyncio.sleep(0)
        else:
            existing_sources = set()
            existing_hashes = {}

        # Find all supported document files - run in thread pool to avoid blocking
        # glob.glob with recursive=True can be slow on large directory structures
        loop = asyncio.get_running_loop()
        document_files = await loop.run_in_executor(
            self.get_executor(),
            self._find_document_files
        )

        if not document_files:
            logger.warning(
                f"No supported document files found in {self.documents_folders}"
            )
            return []

        logger.info(f"Found {len(document_files)} document files to process")

        # Filter out already-ingested files in incremental mode
        if incremental and existing_sources:
            original_count = len(document_files)
            document_files_to_process = []
            
            for file_path in document_files:
                # Calculate the source path as stored in DB
                document_source = os.path.basename(file_path)
                for folder in self.documents_folders:
                    if os.path.abspath(file_path).startswith(os.path.abspath(folder)):
                        document_source = os.path.relpath(file_path, folder)
                        break
                
                if document_source not in existing_sources:
                    document_files_to_process.append(file_path)
            
            skipped_count = original_count - len(document_files_to_process)
            if skipped_count > 0:
                logger.info(
                    f"Incremental mode: Skipping {skipped_count} already-ingested files"
                )
            document_files = document_files_to_process

        # A resumed job does not redo the failures it already recorded (F23)
        if incremental and job_id and document_files:
            skip_failed = await failed_paths_of_job(self.db, job_id, retry_failed_types or frozenset())
            if skip_failed:
                before = len(document_files)
                document_files = [f for f in document_files if f not in skip_failed]
                if before > len(document_files):
                    logger.info(
                        f"Skipping {before - len(document_files)} file(s) that already failed in job "
                        f"{job_id} (use the retry options to process them again)"
                    )

        if queue_callback is not None:
            try:
                queue_callback(list(document_files))
            except Exception as exc:  # noqa: BLE001 - a display hook must not stop the run
                logger.warning(f"queue_callback failed: {exc}")

        if not document_files:
            logger.info("No new documents to ingest")
            return []

        logger.info(f"Processing {len(document_files)} new documents")

        results = []
        duplicates_skipped = 0
        
        # Use concurrent processing if max_concurrent_files > 1
        if max_concurrent_files > 1:
            # Killable conversions (process isolation) for the loop with per-file timeouts
            use_processes = process_isolation_enabled(process_isolation)
            if use_processes:
                self._start_conversion_pool(max_concurrent_files)
            try:
                results, duplicates_skipped = await self._ingest_documents_concurrent(
                    document_files=document_files,
                    existing_hashes=existing_hashes,
                    incremental=incremental,
                    max_concurrent=max_concurrent_files,
                    progress_callback=progress_callback,
                    job_id=job_id,
                    pause_checkpoint=pause_checkpoint,
                    stats_callback=stats_callback,
                )
            finally:
                if use_processes:
                    await self._stop_conversion_pool()
        else:
            # Sequential processing (original behavior)
            results, duplicates_skipped = await self._ingest_documents_sequential(
                document_files=document_files,
                existing_hashes=existing_hashes,
                incremental=incremental,
                progress_callback=progress_callback,
                pause_checkpoint=pause_checkpoint
            )

        # Log summary
        total_chunks = sum(r.chunks_created for r in results)
        total_errors = sum(1 for r in results if r.errors and "Duplicate of:" not in r.errors[0])

        logger.info(
            f"Ingestion complete: {len(results)} documents, "
            f"{total_chunks} chunks, {total_errors} errors, "
            f"{duplicates_skipped} duplicates skipped"
        )

        return results
    
    async def _ingest_documents_sequential(
        self,
        document_files: List[str],
        existing_hashes: Dict[str, str],
        incremental: bool,
        progress_callback: Optional[callable] = None,
        pause_checkpoint: Optional[Callable[[], Awaitable[None]]] = None
    ) -> Tuple[List[IngestionResult], int]:
        """
        Process documents sequentially (original behavior).

        Args:
            document_files: List of file paths to process
            existing_hashes: Dict of content hashes to source paths
            incremental: Whether to skip duplicates
            progress_callback: Optional progress callback
            pause_checkpoint: Optional coroutine function awaited before each file

        Returns:
            Tuple of (results list, duplicates skipped count)
        """
        results = []
        duplicates_skipped = 0

        for i, file_path in enumerate(document_files):
            # Pause point between files: blocks while the caller holds the run paused
            if pause_checkpoint:
                await pause_checkpoint()

            # Give API requests priority - small delay between documents
            await asyncio.sleep(0.05)
            
            # Call progress callback BEFORE processing
            if progress_callback:
                progress_callback(i, len(document_files), file_path)
            
            try:
                # Compute file hash for content-based deduplication
                loop = asyncio.get_running_loop()
                content_hash = await loop.run_in_executor(
                    self.get_executor(),
                    compute_file_hash,
                    file_path
                )
                
                # Check if this content already exists
                if incremental and content_hash in existing_hashes:
                    original_source = existing_hashes[content_hash]
                    logger.info(
                        f"Skipping duplicate content: {file_path} "
                        f"(identical to: {original_source})"
                    )
                    duplicates_skipped += 1
                    results.append(IngestionResult(
                        document_id="",
                        title=os.path.basename(file_path),
                        chunks_created=0,
                        processing_time_ms=0,
                        errors=[f"Duplicate of: {original_source}"]
                    ))
                    if progress_callback:
                        progress_callback(i + 1, len(document_files), file_path, 0)
                    continue
                
                logger.info(
                    f"Processing file {i+1}/{len(document_files)} "
                    f"({progress_percent_label(i + 1, len(document_files))}): {file_path}"
                )
                
                result = await self._ingest_single_document(file_path, content_hash)
                results.append(result)
                
                # Add hash to catch duplicates within same batch
                if content_hash and result.document_id:
                    existing_hashes[content_hash] = os.path.basename(file_path)
                
                # Progress callback AFTER processing
                if progress_callback:
                    progress_callback(
                        i + 1, 
                        len(document_files), 
                        file_path,
                        result.chunks_created if result else 0
                    )
                    
            except Exception as e:
                logger.exception(f"Failed to process {file_path}: {e}")
                results.append(IngestionResult(
                    document_id="",
                    title=os.path.basename(file_path),
                    chunks_created=0,
                    processing_time_ms=0,
                    errors=[str(e)]
                ))
                if progress_callback:
                    progress_callback(i + 1, len(document_files), file_path, 0)
        
        return results, duplicates_skipped
    
    async def _ingest_documents_concurrent(
        self,
        document_files: List[str],
        existing_hashes: Dict[str, str],
        incremental: bool,
        max_concurrent: int,
        progress_callback: Optional[callable] = None,
        job_id: Optional[str] = None,
        pause_checkpoint: Optional[Callable[[], Awaitable[None]]] = None,
        stats_callback: Optional[Callable[[Dict[str, int]], None]] = None,
    ) -> Tuple[List[IngestionResult], int]:
        """
        Process documents concurrently using a semaphore with timeout and failure tracking.

        Each file takes one of ``max_concurrent`` slots. Its budget (:func:`calculate_file_timeout`,
        scaled by :func:`conversion_budget_factor` for the CPU share of one conversion at this
        concurrency) starts when its conversion actually starts, not when it was queued. On a
        timeout the conversion is stopped: with process isolation the worker process is killed;
        on the thread pool the thread cannot be interrupted, so the slot stays reserved until it
        ends and the pool never runs more conversions than there are slots (F1 of
        ``docs/system-validation/INGESTION_PROMPT_TEST_LOG_2026-10-24.md``). Timed-out files are
        retried in later rounds with a larger budget (``max_retries``).

        Per-file stats go to ``ingestion_stats`` / ``failed_documents`` in batches while the run
        is going (:class:`IngestionStatsRecorder`, :func:`stats_flush_config`), not only at the
        end (F2). The hash of every stored file is added to ``existing_hashes``; a copy of a file
        that is still in flight is deferred until that file's outcome is known and then skipped
        as a duplicate (F4).

        Args:
            document_files: List of file paths to process
            existing_hashes: Dict of content hashes to source paths; the hashes of files stored
                during this run are added to it
            incremental: Whether to skip duplicates
            max_concurrent: Maximum concurrent files to process
            progress_callback: Optional progress callback
            job_id: Optional job ID to link stats to
            pause_checkpoint: Optional coroutine function awaited before each file
            stats_callback: Called after every stats flush with the cumulative counts of the
                stats written so far (``timeout``, ``no_chunks``, ``error``, ``image_only_pdf``,
                ``duplicate``, ``success``, ``written``)

        Returns:
            Tuple of (results list, duplicates skipped count)

        Raises:
            asyncio.CancelledError: A file task was cancelled by a stop/shutdown check. Files
                already in progress finish first; stats of processed files are still saved.
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        results: List[IngestionResult] = []
        duplicates_skipped = 0
        processed_count = 0
        results_lock = threading.Lock()
        loop = asyncio.get_running_loop()

        total_files = len(document_files)
        profile_key = os.getenv("PROFILE", "default")
        timeout_cfg = get_timeout_config()
        # Resolved once per run; scans are OCRed unless INGEST_SKIP_IMAGE_ONLY_PDF is set
        skip_image_only_pdfs = skip_image_only_pdf_enabled()
        pool = self._conversion_pool
        # Budget scaling for the CPU share of one conversion: worker processes share the cores
        # among themselves, pool threads according to the pool size in force
        threads_per_conversion = docling_num_threads(max_concurrent) if pool is not None else docling_num_threads()
        budget_factor = conversion_budget_factor(max_concurrent, threads_per_conversion)
        if budget_factor > 1.0:
            logger.info(
                f"Per-file budgets x{budget_factor:.1f}: {max_concurrent} conversions at once with "
                f"{threads_per_conversion} CPU thread(s) each"
            )

        flush_files, flush_seconds = stats_flush_config()
        recorder = IngestionStatsRecorder(
            self._save_ingestion_stats,
            flush_every_files=flush_files,
            flush_every_seconds=flush_seconds,
            on_flush=stats_callback,
        )

        # Timed-out files waiting for a retry round: (index, file_path, content_hash, next attempt)
        timed_out_files: List[Tuple[int, str, Optional[str], int]] = []
        # Content hash of every file being processed right now -> its path (same-run copies, F4)
        in_flight_hashes: Dict[str, str] = {}
        # Copies of a file in flight, processed once its outcome is known: (index, path, hash)
        deferred_copies: List[Tuple[int, str, str]] = []
        # Thread-pool conversions still running after their timeout; each keeps its slot, and a
        # retry of the same file waits for it (retrying alongside it would only double its load)
        orphan_threads: Set[concurrent.futures.Future] = set()
        orphan_by_file: Dict[str, concurrent.futures.Future] = {}

        async def process_file_with_retry(
            index: int,
            file_path: str,
            retry_attempt: int = 0,
            prev_content_hash: Optional[str] = None,
        ) -> None:
            """Process a file once; timed-out and deferred files are queued for a later round."""
            nonlocal duplicates_skipped, processed_count

            await semaphore.acquire()
            release_slot = True
            claimed_hash: Optional[str] = None
            handle: Optional[ConversionHandle] = None
            try:
                # Pause point after taking a slot: every file task is created up front,
                # so a check before the semaphore would let queued tasks slip past a pause
                if pause_checkpoint:
                    await pause_checkpoint()

                file_start_time = datetime.now()
                file_size = get_file_size_safe(file_path)
                file_modified_at = get_file_modified_time_safe(file_path)
                # Adaptive budget: file size and type, retry round, CPU share at this concurrency
                timeout_seconds = calculate_file_timeout(
                    file_size,
                    file_path=file_path,
                    retry_attempt=retry_attempt,
                    concurrency_factor=budget_factor,
                )

                # Initialize content_hash before try block to avoid NameError
                # in exception handlers if hash computation itself times out
                content_hash: Optional[str] = prev_content_hash or None

                def stat(**overrides: Any) -> IngestionFileStats:
                    """This file's stats record; ``overrides`` set outcome fields."""
                    completed = datetime.now()
                    values: Dict[str, Any] = dict(
                        file_path=file_path,
                        file_name=os.path.basename(file_path),
                        file_size_bytes=file_size,
                        started_at=file_start_time,
                        completed_at=completed,
                        processing_time_ms=(completed - file_start_time).total_seconds() * 1000,
                        chunks_created=0,
                        success=False,
                        error_type=None,
                        error_message=None,
                        timeout_seconds=timeout_seconds,
                        profile_key=profile_key,
                        job_id=job_id,
                        content_hash=content_hash,
                        file_modified_at=file_modified_at,
                        classification="error",
                    )
                    values.update(overrides)
                    return IngestionFileStats(**values)

                def failed_result(error: str, processing_time_ms: float) -> IngestionResult:
                    return IngestionResult(
                        document_id="",
                        title=os.path.basename(file_path),
                        chunks_created=0,
                        processing_time_ms=processing_time_ms,
                        errors=[error],
                    )

                # Progress callback before processing
                if progress_callback:
                    progress_callback(processed_count, total_files, file_path)

                try:
                    # Compute file hash with timeout (reuse from previous attempt if retrying)
                    if not content_hash:
                        content_hash = await asyncio.wait_for(
                            loop.run_in_executor(self.get_executor(), compute_file_hash, file_path),
                            timeout=60,  # 1 minute timeout for hash computation
                        )

                    # Duplicate check (thread-safe). A copy of a file that is being processed right
                    # now is deferred until that file's outcome is known: both used to start together
                    # (identical files sort next to each other) and both were stored.
                    original_source: Optional[str] = None
                    with results_lock:
                        if incremental and content_hash in existing_hashes:
                            original_source = existing_hashes[content_hash]
                        elif incremental and content_hash in in_flight_hashes:
                            deferred_copies.append((index, file_path, content_hash))
                            logger.info(
                                f"Deferring {file_path}: identical to {in_flight_hashes[content_hash]}, "
                                f"which is being processed"
                            )
                            return
                        elif content_hash:
                            in_flight_hashes[content_hash] = file_path
                            claimed_hash = content_hash
                    if original_source is not None:
                        logger.info(f"Skipping duplicate: {file_path} (identical to: {original_source})")
                        with results_lock:
                            duplicates_skipped += 1
                            results.append(failed_result(f"Duplicate of: {original_source}", 0))
                            processed_count += 1
                            # Track stats for duplicates too
                            recorder.add(stat(
                                success=True,
                                error_type="duplicate",
                                error_message=f"Duplicate of: {original_source}",
                                classification="normal",  # Duplicates mean file was already processed
                            ))
                        if progress_callback:
                            progress_callback(processed_count, total_files, file_path, 0)
                        return

                    # Pre-check (INGEST_SKIP_IMAGE_ONLY_PDF, off by default): skip image-only
                    # PDFs for text-only profiles. Otherwise they go through Docling + OCR below,
                    # exactly like the sequential path; a blank scan ends up as no_chunks.
                    if skip_image_only_pdfs and file_path.lower().endswith('.pdf'):
                        is_image_only, check_reason = is_image_only_pdf(file_path)
                        if is_image_only:
                            logger.info(f"Skipping image-only PDF: {file_path} ({check_reason})")
                            with results_lock:
                                processed_count += 1
                                recorder.add(stat(
                                    success=True,  # Not an error, just no content
                                    error_type="image_only_pdf",
                                    error_message=check_reason,
                                    classification="image_only_pdf",
                                ))
                            if progress_callback:
                                progress_callback(processed_count, total_files, file_path, 0)
                            return

                    size_mb = file_size / (1024 * 1024)
                    logger.info(
                        f"Processing file ({processed_count+1}/{total_files}, "
                        f"{progress_percent_label(processed_count + 1, total_files)}): {file_path} "
                        f"({size_mb:.1f}MB, timeout={timeout_seconds:.0f}s)"
                    )

                    # Process with a budget counted from the start of the conversion
                    handle = ConversionHandle()
                    result = await self._run_with_budget(
                        self._ingest_single_document(file_path, content_hash),
                        handle,
                        timeout_seconds,
                    )

                    chunks_created = result.chunks_created if result else 0
                    is_no_chunks = chunks_created == 0 and result and not result.document_id
                    # Pure scans are recorded as such (F9): OCRed ones carry the flag, ones
                    # whose OCR found nothing count as image_only_pdf instead of no_chunks
                    image_only: Optional[bool] = None
                    image_only_reason = ""
                    if file_path.lower().endswith('.pdf') and (is_no_chunks or result.document_id):
                        try:
                            image_only, image_only_reason = await loop.run_in_executor(
                                self.get_executor(), is_image_only_pdf, file_path
                            )
                        except Exception as exc:  # noqa: BLE001 - the stat is written either way
                            logger.debug(f"image-only check failed for {file_path}: {exc}")
                            image_only = None

                    with results_lock:
                        results.append(result)
                        if content_hash and result.document_id:
                            existing_hashes[content_hash] = os.path.basename(file_path)
                        processed_count += 1

                        # Track stats - mark as failure if no chunks created
                        if is_no_chunks and image_only:
                            error_type = "image_only_pdf"
                            error_message = f"Image-only PDF: OCR found no text ({image_only_reason})"
                        elif is_no_chunks:
                            error_type = "no_chunks"
                            error_message = "No content extracted from document"
                        else:
                            error_type = None
                            error_message = None
                        recorder.add(stat(
                            chunks_created=chunks_created,
                            success=not is_no_chunks,
                            error_type=error_type,
                            error_message=error_message,
                            classification=map_error_type_to_classification(error_type, chunks_created),
                            image_only_pdf=image_only,
                            ocr_fallback=getattr(result, "ocr_fallback", None) if result else None,
                        ))

                    if progress_callback:
                        progress_callback(
                            processed_count,
                            total_files,
                            file_path,
                            result.chunks_created if result else 0,
                        )

                except asyncio.TimeoutError:
                    # Timeout - check if we should retry or record as failure
                    processing_time = (datetime.now() - file_start_time).total_seconds() * 1000
                    size_mb = file_size / (1024 * 1024)

                    thread_future = handle.thread_future if handle is not None else None
                    if thread_future is not None and not thread_future.done():
                        # A pool thread cannot be interrupted (process isolation would have killed
                        # the worker). Keep its slot until it ends, so the pool never runs more
                        # conversions than there are slots; the file's retry waits for a later round.
                        release_slot = False
                        orphan_threads.add(thread_future)
                        orphan_by_file[file_path] = thread_future

                        def thread_ended(done_future: concurrent.futures.Future, _path: str = file_path) -> None:
                            orphan_threads.discard(done_future)
                            if orphan_by_file.get(_path) is done_future:
                                orphan_by_file.pop(_path, None)
                            try:
                                loop.call_soon_threadsafe(semaphore.release)
                            except RuntimeError:  # loop closed: no slot left to free
                                pass

                        thread_future.add_done_callback(thread_ended)
                        logger.warning(
                            f"Conversion thread of {file_path} keeps running after its "
                            f"{timeout_seconds:.0f}s budget; its slot stays reserved until it ends "
                            f"({len(orphan_threads)} such thread(s)). Process isolation stops them instead."
                        )

                    # Check if we should retry
                    if retry_attempt < timeout_cfg.max_retries:
                        next_timeout = calculate_file_timeout(
                            file_size,
                            file_path=file_path,
                            retry_attempt=retry_attempt + 1,
                            concurrency_factor=budget_factor,
                        )
                        logger.warning(
                            f"TIMEOUT (attempt {retry_attempt + 1}/{timeout_cfg.max_retries + 1}) "
                            f"processing {file_path}: {timeout_seconds:.0f}s elapsed. "
                            f"Will retry with {next_timeout:.0f}s timeout."
                        )
                        # Queue for retry with increased timeout
                        with results_lock:
                            timed_out_files.append((index, file_path, content_hash, retry_attempt + 1))
                        return  # Don't count as processed yet

                    # Max retries exceeded - record as final failure
                    error_msg = (
                        f"Timeout after {timeout_seconds:.0f}s "
                        f"(file size: {size_mb:.1f}MB, retries exhausted: {retry_attempt + 1}/{timeout_cfg.max_retries + 1})"
                    )
                    logger.error(f"TIMEOUT (final) processing {file_path}: {error_msg}")

                    with results_lock:
                        results.append(failed_result(error_msg, processing_time))
                        processed_count += 1
                        # Track timeout stats
                        recorder.add(stat(
                            processing_time_ms=processing_time,
                            error_type="timeout",
                            error_message=error_msg,
                            classification="timeout",
                        ))

                    if progress_callback:
                        progress_callback(processed_count, total_files, file_path, 0)

                except Exception as e:
                    # General error - log and continue with others
                    processing_time = (datetime.now() - file_start_time).total_seconds() * 1000
                    error_msg = str(e)
                    logger.exception(f"Failed to process {file_path}: {error_msg}")

                    with results_lock:
                        results.append(failed_result(error_msg, processing_time))
                        processed_count += 1
                        # Track error stats
                        recorder.add(stat(
                            processing_time_ms=processing_time,
                            error_type="error",
                            error_message=error_msg[:500],  # Truncate long error messages
                            classification="error",
                        ))

                    if progress_callback:
                        progress_callback(processed_count, total_files, file_path, 0)
            finally:
                if claimed_hash is not None:
                    with results_lock:
                        if in_flight_hashes.get(claimed_hash) == file_path:
                            del in_flight_hashes[claimed_hash]
                if release_slot:
                    semaphore.release()

        def raise_if_cancelled(outcomes: List[Any]) -> None:
            # gather(return_exceptions=True) keeps one failing file from ending the run, but it
            # also returns the CancelledError a stop/shutdown raises inside a file task (from
            # pause_checkpoint or progress_callback) instead of raising it. Every remaining task
            # has already ended at its next check, so re-raise to end the run as cancelled.
            for outcome in outcomes:
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome

        async def run_round(tasks: List[asyncio.Task]) -> None:
            if tasks:
                raise_if_cancelled(await asyncio.gather(*tasks, return_exceptions=True))

        flush_timer = asyncio.create_task(recorder.timer_loop())
        try:
            # First round: every file once
            await run_round([
                asyncio.create_task(process_file_with_retry(i, file_path, retry_attempt=0))
                for i, file_path in enumerate(document_files)
            ])

            # Later rounds: copies deferred behind a file in flight (first attempts), then timed-out
            # files (next attempt, larger budget). Every round settles at least one copy per hash
            # and retries are bounded by max_retries, so the loop ends.
            retry_round = 0
            while True:
                with results_lock:
                    copies, deferred_copies[:] = list(deferred_copies), []
                    retries, timed_out_files[:] = list(timed_out_files), []
                if not copies and not retries:
                    break
                if copies:
                    logger.info(f"Processing {len(copies)} deferred copies of files converted in this run")
                    await run_round([
                        asyncio.create_task(process_file_with_retry(idx, fpath, retry_attempt=0, prev_content_hash=chash))
                        for idx, fpath, chash in copies
                    ])
                if retries:
                    retry_round += 1
                    still_running = [
                        orphan_by_file[fpath] for _, fpath, _, _ in retries
                        if fpath in orphan_by_file and not orphan_by_file[fpath].done()
                    ]
                    if still_running:
                        logger.info(
                            f"Waiting for {len(still_running)} conversion thread(s) of timed-out files to end "
                            f"before retrying them"
                        )
                        await asyncio.gather(
                            *(asyncio.wrap_future(f) for f in still_running), return_exceptions=True
                        )
                    logger.info(
                        f"Retry round {retry_round}/{timeout_cfg.max_retries}: "
                        f"Processing {len(retries)} timed-out files with increased timeout"
                    )
                    await run_round([
                        asyncio.create_task(
                            process_file_with_retry(idx, fpath, retry_attempt=attempt, prev_content_hash=chash)
                        )
                        for idx, fpath, chash, attempt in retries
                    ])

            # Log retry summary
            if timeout_cfg.max_retries > 0:
                timeout_failures = recorder.counts.get("timeout", 0)
                if timeout_failures > 0:
                    logger.warning(
                        f"Retry summary: {timeout_failures} files still timed out after "
                        f"{timeout_cfg.max_retries} retries"
                    )
            if orphan_threads:
                logger.warning(
                    f"{len(orphan_threads)} conversion thread(s) of timed-out files are still running "
                    f"after the run; they end on their own (process isolation would have stopped them)"
                )
        finally:
            # Save stats to database - also when the run is stopped, for the files already processed
            flush_timer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await flush_timer
            await recorder.flush()
            if recorder.pending:
                logger.error(f"{recorder.pending} file stats of this run could not be written to the database")

        return results, duplicates_skipped

    async def _save_ingestion_stats(self, file_stats: List[IngestionFileStats]) -> bool:
        """Write a batch of per-file stats to ``ingestion_stats`` and its failures to ``failed_documents``.

        Called by :class:`IngestionStatsRecorder` during and at the end of a concurrent run.

        Args:
            file_stats: The batch.

        Returns:
            True when the stats were written (or there was nothing to write, or no database);
            False when the ``ingestion_stats`` write failed, so the recorder keeps the batch
            for its next flush.
        """
        if self.db is None or not file_stats:
            return True

        try:
            # Save all stats to ingestion_stats collection
            stats_collection = self.db["ingestion_stats"]
            stats_docs = [stat.to_dict() for stat in file_stats]
            if stats_docs:
                await stats_collection.insert_many(stats_docs)
        except Exception as e:
            logger.error(f"Failed to save ingestion stats: {e}")
            return False

        try:
            # Save failed documents to failed_documents collection
            failed_stats = [s for s in file_stats if not s.success]
            if failed_stats:
                failed_collection = self.db["failed_documents"]
                failed_docs = []
                for stat in failed_stats:
                    doc = {
                        "file_path": stat.file_path,
                        "file_name": stat.file_name,
                        "file_size_bytes": stat.file_size_bytes,
                        "error_type": stat.error_type,
                        "error_message": stat.error_message,
                        "timeout_seconds": stat.timeout_seconds,
                        "processing_time_ms": stat.processing_time_ms,
                        "failed_at": stat.completed_at.isoformat() if stat.completed_at else datetime.now().isoformat(),
                        "profile_key": stat.profile_key,
                        "resolved": False,
                        "retry_count": 0
                    }
                    if stat.job_id:
                        doc["job_id"] = stat.job_id
                    failed_docs.append(doc)
                await failed_collection.insert_many(failed_docs)
                logger.warning(f"Recorded {len(failed_docs)} failed documents to database")
        except Exception as e:
            # The stats are in; do not write them twice for the sake of the failure list
            logger.error(f"Failed to record failed documents: {e}")
        return True


async def main() -> None:
    """Main function for running ingestion."""
    parser = argparse.ArgumentParser(
        description="Ingest documents into MongoDB vector database"
    )
    parser.add_argument(
        "--documents", "-d",
        default=None,
        help="Documents folder path (overrides profile setting)"
    )
    parser.add_argument(
        "--profile", "-p",
        default=None,
        help="Profile to use for ingestion"
    )
    parser.add_argument(
        "--no-profile",
        action="store_true",
        help="Don't use profile settings, use defaults"
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Skip cleaning existing data before ingestion (enables incremental mode)"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force full re-ingestion, don't skip existing files"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1000,
        help="Chunk size for splitting documents"
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=200,
        help="Chunk overlap size"
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=512,
        help="Maximum tokens per chunk for embeddings"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Switch profile if specified
    if args.profile:
        profile_manager = get_profile_manager()
        if not profile_manager.switch_profile(args.profile):
            print(f"Error: Profile '{args.profile}' not found")
            print(f"Available profiles: {list(profile_manager.list_profiles().keys())}")
            return
        print(f"Using profile: {args.profile}")

    # Create ingestion configuration
    config = IngestionConfig(
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        max_chunk_size=args.chunk_size * 2,
        max_tokens=args.max_tokens
    )

    # Determine document folder
    documents_folder = args.documents if args.documents else None
    use_profile = not args.no_profile and documents_folder is None

    # Create and run pipeline
    pipeline = DocumentIngestionPipeline(
        config=config,
        documents_folder=documents_folder,
        clean_before_ingest=not args.no_clean,
        use_profile=use_profile
    )
    
    # Show which folders will be processed
    print(f"Document folders: {pipeline.documents_folders}")
    print(f"Database: {pipeline.settings.mongodb_database}")

    def progress_callback(current: int, total: int) -> None:
        print(f"Progress: {current}/{total} documents processed")

    try:
        start_time = datetime.now()

        results = await pipeline.ingest_documents(
            progress_callback,
            incremental=not args.full  # Incremental by default unless --full
        )

        end_time = datetime.now()
        total_time = (end_time - start_time).total_seconds()

        # Print summary
        print("\n" + "="*50)
        print("INGESTION SUMMARY")
        print("="*50)
        print(f"Documents processed: {len(results)}")
        print(f"Total chunks created: {sum(r.chunks_created for r in results)}")
        print(f"Total errors: {sum(len(r.errors) for r in results)}")
        print(f"Total processing time: {total_time:.2f} seconds")
        print()

        # Print individual results
        for result in results:
            status = "[OK]" if not result.errors else "[FAILED]"
            print(f"{status} {result.title}: {result.chunks_created} chunks")

            if result.errors:
                for error in result.errors:
                    print(f"  Error: {error}")

        # Print next steps
        print("\n" + "="*50)
        print("NEXT STEPS")
        print("="*50)
        print("1. Create vector search index in Atlas UI:")
        print("   - Index name: vector_index")
        print("   - Collection: chunks")
        print("   - Field: embedding")
        print("   - Dimensions: 1536 (for text-embedding-3-small)")
        print()
        print("2. Create text search index in Atlas UI:")
        print("   - Index name: text_index")
        print("   - Collection: chunks")
        print("   - Field: content")
        print()
        print("See .claude/reference/mongodb-patterns.md for detailed instructions")

    except KeyboardInterrupt:
        print("\nIngestion interrupted by user")
    except Exception as e:
        logger.exception(f"Ingestion failed: {e}")
        raise
    finally:
        await pipeline.close()


if __name__ == "__main__":
    asyncio.run(main())
