"""
Main ingestion script for processing documents into MongoDB vector database.

This adapts the examples/ingestion/ingest.py pipeline to use MongoDB instead of PostgreSQL,
changing only the database layer while preserving all document processing logic.
"""

import os
import asyncio
import logging
import glob
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import argparse
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import functools

from pymongo import AsyncMongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from bson import ObjectId
from dotenv import load_dotenv

from src.ingestion.chunker import ChunkingConfig, create_chunker, DocumentChunk
from src.ingestion.embedder import create_embedder
from src.settings import load_settings
from src.profile import get_profile_manager

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


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
        
        Files are sorted for optimal processing order:
        1. Text files first (fastest to process)
        2. Then PDF, DOCX, Excel, etc.
        3. Images
        4. Audio files (slowest)
        5. Large files (>5MB) are processed last within each category

        Returns:
            List of file paths in optimal processing order
        """
        # Supported file patterns - Docling + text formats + audio
        patterns = [
            "*.md", "*.markdown", "*.txt",  # Text formats
            "*.pdf",  # PDF
            "*.docx", "*.doc",  # Word
            "*.pptx", "*.ppt",  # PowerPoint
            "*.xlsx", "*.xls",  # Excel
            "*.html", "*.htm",  # HTML
            "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.bmp",  # Images
            "*.mp3", "*.wav", "*.m4a", "*.flac",  # Audio formats
            "*.mp4", "*.avi", "*.mkv", "*.mov", "*.webm",  # Video formats
        ]
        
        all_files = []
        
        for folder in self.documents_folders:
            if not os.path.exists(folder):
                logger.warning(f"Documents folder not found: {folder}")
                continue
            
            for pattern in patterns:
                all_files.extend(
                    glob.glob(
                        os.path.join(folder, "**", pattern),
                        recursive=True
                    )
                )
        
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
                from docling.document_converter import DocumentConverter

                logger.info(
                    f"Converting {file_ext} file using Docling: "
                    f"{os.path.basename(file_path)}"
                )

                converter = DocumentConverter()
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
        metadata: Dict[str, Any]
    ) -> str:
        """
        Save document and chunks to MongoDB.

        Args:
            title: Document title
            source: Document source path
            content: Document content
            chunks: List of document chunks with embeddings
            metadata: Document metadata

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

    async def _ingest_single_document(self, file_path: str) -> IngestionResult:
        """
        Ingest a single document.

        Args:
            file_path: Path to the document file

        Returns:
            Ingestion result
        """
        start_time = datetime.now()

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
            document_metadata
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

    async def ingest_documents(
        self,
        progress_callback: Optional[callable] = None,
        incremental: bool = True
    ) -> List[IngestionResult]:
        """
        Ingest all documents from the documents folder.

        Args:
            progress_callback: Optional callback for progress updates
            incremental: If True, skip already-ingested files (default: True)

        Returns:
            List of ingestion results
        """
        if not self._initialized:
            await self.initialize()

        # Clean existing data if requested
        if self.clean_before_ingest:
            await self._clean_databases()
            existing_sources = set()  # Nothing exists after cleaning
        elif incremental:
            # Yield to event loop before potentially slow DB query
            await asyncio.sleep(0)
            # Get existing sources for incremental mode
            existing_sources = await self._get_existing_sources()
            logger.info(f"Found {len(existing_sources)} already-ingested documents")
            # Yield again after DB query
            await asyncio.sleep(0)
        else:
            existing_sources = set()

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

        for i, file_path in enumerate(document_files):
            # Give API requests priority - small delay between documents
            # This ensures login, status checks, etc. remain responsive
            await asyncio.sleep(0.05)  # 50ms pause between documents
            
            # Call progress callback BEFORE processing to show current file
            if progress_callback:
                # Pass current file path for file type categorization
                progress_callback(i, len(document_files), file_path)
            
            try:
                logger.info(
                    f"Processing file {i+1}/{len(document_files)}: {file_path}"
                )

                result = await self._ingest_single_document(file_path)
                results.append(result)

                # Call progress callback AFTER processing with result info
                if progress_callback:
                    # Pass current file and chunks created for this file
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
                # Report failure in progress
                if progress_callback:
                    progress_callback(i + 1, len(document_files), file_path, 0)

        # Log summary
        total_chunks = sum(r.chunks_created for r in results)
        total_errors = sum(len(r.errors) for r in results)

        logger.info(
            f"Ingestion complete: {len(results)} documents, "
            f"{total_chunks} chunks, {total_errors} errors"
        )

        return results


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
