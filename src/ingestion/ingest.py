"""
Main ingestion script for processing documents into MongoDB vector database.

This adapts the examples/ingestion/ingest.py pipeline to use MongoDB instead of PostgreSQL,
changing only the database layer while preserving all document processing logic.
"""

import os
import asyncio
import logging
import glob
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple
from datetime import datetime
import argparse
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import functools

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

from src.ingestion.chunker import ChunkingConfig, create_chunker, DocumentChunk
from src.ingestion.embedder import create_embedder
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


@dataclass
class IngestionConfig:
    """Configuration for document ingestion."""
    chunk_size: int = 1000
    chunk_overlap: int = 200
    max_chunk_size: int = 2000
    max_tokens: int = 512


@dataclass
class IngestionResult:
    """Result of document ingestion."""
    document_id: str
    title: str
    chunks_created: int
    processing_time_ms: float
    errors: List[str]


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
    retry_attempt: int = 0
) -> float:
    """
    Calculate adaptive timeout for a file based on its size and type.
    
    Formula: base_timeout + (size_in_mb * seconds_per_mb) * type_multiplier
    - Base timeout: 120 seconds minimum (configurable)
    - Per MB: 30 seconds per megabyte (configurable)
    - PDF multiplier: 1.5x for standard PDFs, 2x+ for complex PDFs
    - Max: 1800 seconds (30 minutes, configurable)
    - Retry multiplier: Each retry increases timeout
    
    Args:
        file_size_bytes: Size of the file in bytes
        file_path: Optional path to determine file type for multiplier
        max_timeout_seconds: Override for maximum timeout
        config: Optional TimeoutConfig (uses global if not provided)
        retry_attempt: Retry attempt number (0 = first try)
        
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
    
    # Apply retry multiplier for subsequent attempts
    if retry_attempt > 0:
        retry_multiplier = cfg.retry_timeout_multiplier ** retry_attempt
        calculated_timeout *= retry_multiplier
        logger.info(
            f"Retry {retry_attempt}: timeout increased to {calculated_timeout:.0f}s "
            f"(multiplier: {retry_multiplier:.1f}x)"
        )
    
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


def create_document_converter():
    """
    Create a DocumentConverter with optimal settings for the current environment.
    
    Configures GPU acceleration when available and enabled.
    
    Returns:
        Configured DocumentConverter instance
    """
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.datamodel.base_models import InputFormat
        
        # Get effective device
        device = get_effective_device()
        cfg = _accelerator_config
        
        # Configure pipeline options for PDF processing
        pipeline_options = PdfPipelineOptions()
        
        # Set accelerator device
        if device in ["cuda", "mps"]:
            pipeline_options.accelerator_options = {
                "device": device,
            }
            if device == "cuda" and cfg.cuda_device_id > 0:
                pipeline_options.accelerator_options["device_id"] = cfg.cuda_device_id
            
            logger.info(f"Docling converter using GPU acceleration: {device}")
        else:
            logger.info("Docling converter using CPU")
        
        # Enable OCR with acceleration if configured
        if cfg.enable_ocr_acceleration:
            pipeline_options.do_ocr = True
            pipeline_options.do_table_structure = True
        
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


class DocumentIngestionPipeline:
    """Pipeline for ingesting documents into MongoDB vector database."""
    
    # Thread pool for CPU-intensive operations (shared across instances)
    # Using max 2 workers to leave resources for API handling
    _executor: Optional[ThreadPoolExecutor] = None
    
    @classmethod
    def get_executor(cls) -> ThreadPoolExecutor:
        """Get or create thread pool executor."""
        if cls._executor is None:
            # Use only 2 threads to leave CPU for API requests
            cls._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ingest_")
        return cls._executor

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
        # Supported extensions (converted from glob patterns)
        extensions = {
            ".md", ".markdown", ".txt",  # Text formats
            ".pdf",  # PDF
            ".docx", ".doc",  # Word
            ".pptx", ".ppt",  # PowerPoint
            ".xlsx", ".xls",  # Excel
            ".html", ".htm",  # HTML
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",  # Images
            ".mp3", ".wav", ".m4a", ".flac",  # Audio formats
            ".mp4", ".avi", ".mkv", ".mov", ".webm",  # Video formats
        }
        
        all_files = []
        
        for folder in self.documents_folders:
            if not os.path.exists(folder):
                logger.warning(f"Documents folder not found: {folder}")
                continue
            
            # Single-pass directory traversal - much faster than multiple glob calls
            for root, dirs, files in os.walk(folder):
                for filename in files:
                    ext = os.path.splitext(filename)[1].lower()
                    if ext in extensions:
                        all_files.append(os.path.join(root, filename))
        
        if not all_files:
            logger.error(f"No document files found in folders: {self.documents_folders}")
            return []
        
        # Sort files for optimal processing order
        all_files = self._sort_files_for_processing(all_files)
        
        return all_files
    
    def _sort_files_for_processing(self, files: List[str]) -> List[str]:
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
            
        Returns:
            Sorted list of file paths
        """
        # Define file type priorities (lower = processed first)
        TYPE_PRIORITY = {
            # Text files - fastest
            '.txt': 1, '.md': 1, '.markdown': 1,
            # HTML - also fast
            '.html': 2, '.htm': 2,
            # Office documents - medium
            '.pdf': 3,
            '.docx': 4, '.doc': 4,
            '.xlsx': 5, '.xls': 5,
            '.pptx': 6, '.ppt': 6,
            # Images - can need OCR
            '.png': 7, '.jpg': 7, '.jpeg': 7, '.gif': 7, '.webp': 7, '.bmp': 7, '.svg': 7,
            # Audio - requires transcription
            '.mp3': 8, '.wav': 8, '.m4a': 8, '.flac': 8, '.ogg': 8,
            # Video - slowest
            '.mp4': 9, '.avi': 9, '.mkv': 9, '.mov': 9, '.webm': 9, '.wmv': 9,
        }
        
        SIZE_THRESHOLD = 5 * 1024 * 1024  # 5MB
        
        def get_sort_key(file_path: str) -> tuple:
            """Return (is_large, type_priority, size, filename) for sorting."""
            ext = os.path.splitext(file_path)[1].lower()
            type_priority = TYPE_PRIORITY.get(ext, 100)  # Unknown types last
            
            try:
                size = os.path.getsize(file_path)
            except OSError:
                size = 0
            
            is_large = size > SIZE_THRESHOLD
            
            # Sort by: is_large (False first), type_priority, size, filename
            return (is_large, type_priority, size, os.path.basename(file_path).lower())
        
        sorted_files = sorted(files, key=get_sort_key)
        
        # Log the processing order summary
        small_count = sum(1 for f in sorted_files if os.path.getsize(f) <= SIZE_THRESHOLD)
        large_count = len(sorted_files) - small_count
        
        if large_count > 0:
            logger.info(
                f"File processing order: {small_count} small files first, "
                f"{large_count} large files (>5MB) last"
            )
        
        return sorted_files

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

        # Audio formats - transcribe with Whisper ASR
        audio_formats = ['.mp3', '.wav', '.m4a', '.flac']
        if file_ext in audio_formats:
            # Returns tuple: (markdown_content, docling_document)
            return self._transcribe_audio(file_path)

        # Docling-supported formats (convert to markdown)
        docling_formats = [
            '.pdf', '.docx', '.doc', '.pptx', '.ppt',
            '.xlsx', '.xls', '.html', '.htm',
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

                # Export to markdown for consistent processing
                markdown_content = result.document.export_to_markdown()
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
            markdown_content = result.document.export_to_markdown()
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

    async def _ingest_single_document(
        self,
        file_path: str,
        content_hash: Optional[str] = None
    ) -> IngestionResult:
        """
        Ingest a single document.

        Args:
            file_path: Path to the document file
            content_hash: Pre-computed SHA256 hash of the file (for deduplication)

        Returns:
            Ingestion result
        """
        start_time = datetime.now()
        
        # Compute hash if not provided
        if content_hash is None:
            loop = asyncio.get_running_loop()
            content_hash = await loop.run_in_executor(
                self.get_executor(),
                compute_file_hash,
                file_path
            )

        # Run CPU-intensive document reading in thread pool to avoid blocking event loop
        # This allows API requests (login, status checks) to be processed during ingestion
        loop = asyncio.get_running_loop()
        document_content, docling_doc = await loop.run_in_executor(
            self.get_executor(),
            self._read_document,
            file_path
        )
        
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
        chunks = await self.chunker.chunk_document(
            content=document_content,
            title=document_title,
            source=document_source,
            metadata=document_metadata,
            docling_doc=docling_doc  # Pass DoclingDocument for HybridChunker
        )

        if not chunks:
            logger.warning(f"No chunks created for {document_title}")
            return IngestionResult(
                document_id="",
                title=document_title,
                chunks_created=0,
                processing_time_ms=(
                    datetime.now() - start_time
                ).total_seconds() * 1000,
                errors=["No chunks created"]
            )

        logger.info(f"Created {len(chunks)} chunks")

        # Yield control to allow API requests between chunking and embedding
        await asyncio.sleep(0)

        # Generate embeddings
        embedded_chunks = await self.embedder.embed_chunks(chunks)
        logger.info(f"Generated embeddings for {len(embedded_chunks)} chunks")

        # Save to MongoDB
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
            errors=[]
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
        job_id: Optional[str] = None
    ) -> List[IngestionResult]:
        """
        Ingest all documents from the documents folder.

        Args:
            progress_callback: Optional callback for progress updates
            incremental: If True, skip already-ingested files (default: True)
            max_concurrent_files: Number of files to process concurrently (default: 1)
            job_id: Optional job ID to link stats to

        Returns:
            List of ingestion results
        """
        if not self._initialized:
            await self.initialize()

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
        
        if not document_files:
            logger.info("No new documents to ingest")
            return []
        
        logger.info(f"Processing {len(document_files)} new documents")

        results = []
        duplicates_skipped = 0
        
        # Use concurrent processing if max_concurrent_files > 1
        if max_concurrent_files > 1:
            results, duplicates_skipped = await self._ingest_documents_concurrent(
                document_files=document_files,
                existing_hashes=existing_hashes,
                incremental=incremental,
                max_concurrent=max_concurrent_files,
                progress_callback=progress_callback,
                job_id=job_id
            )
        else:
            # Sequential processing (original behavior)
            results, duplicates_skipped = await self._ingest_documents_sequential(
                document_files=document_files,
                existing_hashes=existing_hashes,
                incremental=incremental,
                progress_callback=progress_callback
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
        progress_callback: Optional[callable] = None
    ) -> Tuple[List[IngestionResult], int]:
        """
        Process documents sequentially (original behavior).
        
        Args:
            document_files: List of file paths to process
            existing_hashes: Dict of content hashes to source paths
            incremental: Whether to skip duplicates
            progress_callback: Optional progress callback
            
        Returns:
            Tuple of (results list, duplicates skipped count)
        """
        results = []
        duplicates_skipped = 0
        
        for i, file_path in enumerate(document_files):
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
                
                logger.info(f"Processing file {i+1}/{len(document_files)}: {file_path}")
                
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
        job_id: Optional[str] = None
    ) -> Tuple[List[IngestionResult], int]:
        """
        Process documents concurrently using a semaphore with timeout and failure tracking.
        
        Args:
            document_files: List of file paths to process
            existing_hashes: Dict of content hashes to source paths
            incremental: Whether to skip duplicates
            max_concurrent: Maximum concurrent files to process
            progress_callback: Optional progress callback
            job_id: Optional job ID to link stats to
            
        Returns:
            Tuple of (results list, duplicates skipped count)
        """
        import threading
        
        semaphore = asyncio.Semaphore(max_concurrent)
        results: List[IngestionResult] = []
        duplicates_skipped = 0
        processed_count = 0
        results_lock = threading.Lock()
        file_stats: List[IngestionFileStats] = []
        
        total_files = len(document_files)
        profile_key = os.getenv("PROFILE", "default")
        timeout_cfg = get_timeout_config()
        
        # Track timeout failures for potential retry
        timed_out_files: List[Tuple[int, str, str]] = []  # (index, file_path, content_hash)
        
        async def process_file_with_retry(
            index: int, 
            file_path: str, 
            retry_attempt: int = 0,
            prev_content_hash: str = None
        ):
            """Process a file with retry support for timeouts."""
            nonlocal duplicates_skipped, processed_count
            
            async with semaphore:
                file_start_time = datetime.now()
                file_size = get_file_size_safe(file_path)
                file_modified_at = get_file_modified_time_safe(file_path)
                # Use adaptive timeout with file type and retry awareness
                timeout_seconds = calculate_file_timeout(
                    file_size, 
                    file_path=file_path,
                    retry_attempt=retry_attempt
                )
                
                # Progress callback before processing
                if progress_callback:
                    progress_callback(processed_count, total_files, file_path)
                
                try:
                    # Compute file hash with timeout (reuse from previous attempt if retrying)
                    if prev_content_hash:
                        content_hash = prev_content_hash
                    else:
                        loop = asyncio.get_running_loop()
                        content_hash = await asyncio.wait_for(
                            loop.run_in_executor(
                                self.get_executor(),
                                compute_file_hash,
                                file_path
                            ),
                            timeout=60  # 1 minute timeout for hash computation
                        )
                    
                    # Check for duplicates (thread-safe check)
                    with results_lock:
                        if incremental and content_hash in existing_hashes:
                            original_source = existing_hashes[content_hash]
                            logger.info(
                                f"Skipping duplicate: {file_path} "
                                f"(identical to: {original_source})"
                            )
                            duplicates_skipped += 1
                            result = IngestionResult(
                                document_id="",
                                title=os.path.basename(file_path),
                                chunks_created=0,
                                processing_time_ms=0,
                                errors=[f"Duplicate of: {original_source}"]
                            )
                            results.append(result)
                            processed_count += 1
                            # Track stats for duplicates too
                            file_stats.append(IngestionFileStats(
                                file_path=file_path,
                                file_name=os.path.basename(file_path),
                                file_size_bytes=file_size,
                                started_at=file_start_time,
                                completed_at=datetime.now(),
                                processing_time_ms=(datetime.now() - file_start_time).total_seconds() * 1000,
                                chunks_created=0,
                                success=True,
                                error_type="duplicate",
                                error_message=f"Duplicate of: {original_source}",
                                timeout_seconds=timeout_seconds,
                                profile_key=profile_key,
                                job_id=job_id,
                                content_hash=content_hash,
                                file_modified_at=file_modified_at,
                                classification="normal"  # Duplicates mean file was already processed
                            ))
                            if progress_callback:
                                progress_callback(processed_count, total_files, file_path, 0)
                            return
                    
                    # Pre-check: Skip image-only PDFs to save processing time
                    if file_path.lower().endswith('.pdf'):
                        is_image_only, check_reason = is_image_only_pdf(file_path)
                        if is_image_only:
                            logger.info(f"Skipping image-only PDF: {file_path} ({check_reason})")
                            with results_lock:
                                processed_count += 1
                                file_stats.append(IngestionFileStats(
                                    file_path=file_path,
                                    file_name=os.path.basename(file_path),
                                    file_size_bytes=file_size,
                                    started_at=file_start_time,
                                    completed_at=datetime.now(),
                                    processing_time_ms=(datetime.now() - file_start_time).total_seconds() * 1000,
                                    chunks_created=0,
                                    success=True,  # Not an error, just no content
                                    error_type="image_only_pdf",
                                    error_message=check_reason,
                                    timeout_seconds=timeout_seconds,
                                    profile_key=profile_key,
                                    job_id=job_id,
                                    content_hash=content_hash,
                                    file_modified_at=file_modified_at,
                                    classification="image_only_pdf"
                                ))
                            if progress_callback:
                                progress_callback(processed_count, total_files, file_path, 0)
                            return
                    
                    size_mb = file_size / (1024 * 1024)
                    logger.info(f"Processing file ({processed_count+1}/{total_files}): {file_path} ({size_mb:.1f}MB, timeout={timeout_seconds:.0f}s)")
                    
                    # Process with timeout - this is the key improvement
                    result = await asyncio.wait_for(
                        self._ingest_single_document(file_path, content_hash),
                        timeout=timeout_seconds
                    )
                    
                    processing_time = (datetime.now() - file_start_time).total_seconds() * 1000
                    
                    with results_lock:
                        results.append(result)
                        if content_hash and result.document_id:
                            existing_hashes[content_hash] = os.path.basename(file_path)
                        processed_count += 1
                        
                        # Track stats - mark as failure if no chunks created
                        chunks_created = result.chunks_created if result else 0
                        is_no_chunks = chunks_created == 0 and result and not result.document_id
                        error_type = "no_chunks" if is_no_chunks else None
                        
                        file_stats.append(IngestionFileStats(
                            file_path=file_path,
                            file_name=os.path.basename(file_path),
                            file_size_bytes=file_size,
                            started_at=file_start_time,
                            completed_at=datetime.now(),
                            processing_time_ms=processing_time,
                            chunks_created=chunks_created,
                            success=not is_no_chunks,
                            error_type=error_type,
                            error_message="No content extracted from document" if is_no_chunks else None,
                            timeout_seconds=timeout_seconds,
                            profile_key=profile_key,
                            job_id=job_id,
                            content_hash=content_hash,
                            file_modified_at=file_modified_at,
                            classification=map_error_type_to_classification(error_type, chunks_created)
                        ))
                    
                    if progress_callback:
                        progress_callback(
                            processed_count, 
                            total_files, 
                            file_path,
                            result.chunks_created if result else 0
                        )
                        
                except asyncio.TimeoutError:
                    # Timeout - check if we should retry or record as failure
                    processing_time = (datetime.now() - file_start_time).total_seconds() * 1000
                    size_mb = file_size / (1024 * 1024)
                    
                    # Check if we should retry
                    if retry_attempt < timeout_cfg.max_retries:
                        next_timeout = calculate_file_timeout(
                            file_size, 
                            file_path=file_path,
                            retry_attempt=retry_attempt + 1
                        )
                        logger.warning(
                            f"TIMEOUT (attempt {retry_attempt + 1}/{timeout_cfg.max_retries + 1}) "
                            f"processing {file_path}: {timeout_seconds:.0f}s elapsed. "
                            f"Will retry with {next_timeout:.0f}s timeout."
                        )
                        # Queue for retry with increased timeout
                        with results_lock:
                            timed_out_files.append((index, file_path, content_hash))
                        return  # Don't count as processed yet
                    
                    # Max retries exceeded - record as final failure
                    error_msg = (
                        f"Timeout after {timeout_seconds:.0f}s "
                        f"(file size: {size_mb:.1f}MB, retries exhausted: {retry_attempt + 1}/{timeout_cfg.max_retries + 1})"
                    )
                    logger.error(f"TIMEOUT (final) processing {file_path}: {error_msg}")
                    
                    with results_lock:
                        results.append(IngestionResult(
                            document_id="",
                            title=os.path.basename(file_path),
                            chunks_created=0,
                            processing_time_ms=processing_time,
                            errors=[error_msg]
                        ))
                        processed_count += 1
                        # Track timeout stats
                        file_stats.append(IngestionFileStats(
                            file_path=file_path,
                            file_name=os.path.basename(file_path),
                            file_size_bytes=file_size,
                            started_at=file_start_time,
                            completed_at=datetime.now(),
                            processing_time_ms=processing_time,
                            chunks_created=0,
                            success=False,
                            error_type="timeout",
                            error_message=error_msg,
                            timeout_seconds=timeout_seconds,
                            profile_key=profile_key,
                            job_id=job_id,
                            content_hash=content_hash,
                            file_modified_at=file_modified_at,
                            classification="timeout"
                        ))
                    
                    if progress_callback:
                        progress_callback(processed_count, total_files, file_path, 0)
                        
                except Exception as e:
                    # General error - log and continue with others
                    processing_time = (datetime.now() - file_start_time).total_seconds() * 1000
                    error_msg = str(e)
                    logger.exception(f"Failed to process {file_path}: {error_msg}")
                    
                    with results_lock:
                        results.append(IngestionResult(
                            document_id="",
                            title=os.path.basename(file_path),
                            chunks_created=0,
                            processing_time_ms=processing_time,
                            errors=[error_msg]
                        ))
                        processed_count += 1
                        # Track error stats
                        file_stats.append(IngestionFileStats(
                            file_path=file_path,
                            file_name=os.path.basename(file_path),
                            file_size_bytes=file_size,
                            started_at=file_start_time,
                            completed_at=datetime.now(),
                            processing_time_ms=processing_time,
                            chunks_created=0,
                            success=False,
                            error_type="error",
                            error_message=error_msg[:500],  # Truncate long error messages
                            timeout_seconds=timeout_seconds,
                            profile_key=profile_key,
                            job_id=job_id,
                            content_hash=content_hash,
                            file_modified_at=file_modified_at,
                            classification="error"
                        ))
                    
                    if progress_callback:
                        progress_callback(processed_count, total_files, file_path, 0)
        
        # Create initial tasks for all files (first attempt)
        tasks = [
            asyncio.create_task(process_file_with_retry(i, file_path, retry_attempt=0))
            for i, file_path in enumerate(document_files)
        ]
        
        # Wait for all initial tasks to complete
        await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process retry queue with increasing retry attempts
        for retry_round in range(1, timeout_cfg.max_retries + 1):
            if not timed_out_files:
                break
                
            files_to_retry = list(timed_out_files)
            timed_out_files.clear()
            
            logger.info(
                f"Retry round {retry_round}/{timeout_cfg.max_retries}: "
                f"Processing {len(files_to_retry)} timed-out files with increased timeout"
            )
            
            retry_tasks = [
                asyncio.create_task(
                    process_file_with_retry(
                        idx, 
                        fpath, 
                        retry_attempt=retry_round,
                        prev_content_hash=chash
                    )
                )
                for idx, fpath, chash in files_to_retry
            ]
            
            await asyncio.gather(*retry_tasks, return_exceptions=True)
        
        # Log retry summary
        if timeout_cfg.max_retries > 0:
            timeout_failures = sum(1 for s in file_stats if s.error_type == "timeout")
            if timeout_failures > 0:
                logger.warning(
                    f"Retry summary: {timeout_failures} files still timed out after "
                    f"{timeout_cfg.max_retries} retries"
                )
        
        # Save stats to database
        await self._save_ingestion_stats(file_stats)
        
        return results, duplicates_skipped
    
    async def _save_ingestion_stats(self, file_stats: List[IngestionFileStats]) -> None:
        """Save ingestion stats to database for analysis."""
        if not self.db or not file_stats:
            return
            
        try:
            # Save all stats to ingestion_stats collection
            stats_collection = self.db["ingestion_stats"]
            stats_docs = [stat.to_dict() for stat in file_stats]
            if stats_docs:
                await stats_collection.insert_many(stats_docs)
                logger.info(f"Saved {len(stats_docs)} file stats to database")
            
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
            logger.error(f"Failed to save ingestion stats: {e}")


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
