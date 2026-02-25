"""Ingestion management router."""

import logging
import asyncio
import uuid
import os
import signal
from collections import deque
from datetime import datetime
from typing import Dict, Optional, List
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from bson import ObjectId

from backend.models.schemas import (
    IngestionStartRequest, IngestionStatusResponse, IngestionStatus,
    DocumentInfo, DocumentListResponse, SuccessResponse,
    IngestionRunSummary, IngestionRunsResponse, FileClassification
)
from backend.core.config import settings
from backend.routers.auth import require_admin, UserResponse
from backend.routers.backup import trigger_post_ingestion_backup
from backend.services.file_registry import FileRegistryService
from fastapi import Depends

logger = logging.getLogger(__name__)

router = APIRouter()

# Collection name for persisted ingestion jobs
INGESTION_JOBS_COLLECTION = "ingestion_jobs"

# Track active ingestion task for graceful shutdown
_active_ingestion_task: Optional[asyncio.Task] = None
_current_job_id: Optional[str] = None
_shutdown_requested: bool = False
_pause_requested: bool = False
_stop_requested: bool = False
_is_paused: bool = False
_current_job_state: Optional[dict] = None  # In-memory state for real-time status

# Log buffer with max 50000 lines (kept in-memory for performance)
MAX_LOG_LINES = 50000
_ingestion_logs: deque = deque(maxlen=MAX_LOG_LINES)

# Metadata rebuild state tracking
_metadata_rebuild_state: Optional[dict] = None
_metadata_rebuild_task: Optional[asyncio.Task] = None


class IngestionLogHandler(logging.Handler):
    """Custom handler to capture logs during ingestion."""
    
    def emit(self, record: logging.LogRecord):
        try:
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "level": record.levelname,
                "message": self.format(record),
                "logger": record.name
            }
            _ingestion_logs.append(log_entry)
        except Exception:
            pass


# Install log handler for ingestion
_log_handler = IngestionLogHandler()
_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
_log_handler.setLevel(logging.DEBUG)


# ============== Database-backed Job Storage ==============

async def get_jobs_collection(db):
    """Get the ingestion jobs collection."""
    return db.db[INGESTION_JOBS_COLLECTION]


async def save_job_to_db(db, job: dict):
    """Save or update a job in the database."""
    collection = await get_jobs_collection(db)
    job_doc = {**job, "_id": job["job_id"]}
    await collection.replace_one(
        {"_id": job["job_id"]},
        job_doc,
        upsert=True
    )


async def get_job_from_db(db, job_id: str) -> Optional[dict]:
    """Get a job from the database."""
    collection = await get_jobs_collection(db)
    doc = await collection.find_one({"_id": job_id})
    if doc:
        doc["job_id"] = doc.pop("_id")
        return _flatten_job_progress(doc)
    return None


async def get_latest_job_from_db(db) -> Optional[dict]:
    """Get the most recent job from the database."""
    collection = await get_jobs_collection(db)
    cursor = collection.find().sort("started_at", -1).limit(1)
    async for doc in cursor:
        doc["job_id"] = doc.pop("_id")
        # Flatten nested progress object from worker
        return _flatten_job_progress(doc)
    return None


def _flatten_job_progress(doc: dict) -> dict:
    """
    Flatten nested progress fields from worker into top-level fields.
    
    Worker stores: {"progress": {"total_files": 10, "processed_files": 5, ...}}
    API expects: {"total_files": 10, "processed_files": 5, ...}
    """
    if not doc:
        return doc
    
    progress = doc.pop("progress", None)
    if progress and isinstance(progress, dict):
        # Map progress fields to top-level, preserving existing values
        field_mapping = {
            "total_files": "total_files",
            "processed_files": "processed_files", 
            "current_file": "current_file",
            "chunks_created": "chunks_created",
            "progress_percent": "progress_percent",
            "document_count": "document_count",
            "image_count": "image_count",
            "audio_count": "audio_count",
            "video_count": "video_count",
            "failed_files": "failed_files",
            "duplicates_skipped": "duplicates_skipped",
        }
        
        for progress_key, doc_key in field_mapping.items():
            if progress_key in progress and doc_key not in doc:
                doc[doc_key] = progress[progress_key]
    
    return doc


async def get_running_job_from_db(db) -> Optional[dict]:
    """Get any running or interrupted job from the database."""
    collection = await get_jobs_collection(db)
    # Look for RUNNING or INTERRUPTED status
    doc = await collection.find_one({
        "status": {"$in": [IngestionStatus.RUNNING, "INTERRUPTED"]}
    })
    if doc:
        doc["job_id"] = doc.pop("_id")
        return _flatten_job_progress(doc)
    return None


async def mark_job_interrupted(db, job_id: str):
    """Mark a job as interrupted (for recovery after restart)."""
    collection = await get_jobs_collection(db)
    await collection.update_one(
        {"_id": job_id, "status": IngestionStatus.RUNNING},
        {"$set": {
            "status": "INTERRUPTED",
            "interrupted_at": datetime.now().isoformat()
        }}
    )
    logger.info(f"Marked job {job_id} as interrupted")


async def check_and_resume_interrupted_jobs(db) -> Optional[str]:
    """
    Check for interrupted ingestion jobs and resume them.
    
    Called at startup to recover from container restarts.
    Returns the job_id if a job was resumed, None otherwise.
    """
    # First, mark any RUNNING jobs as INTERRUPTED (they were killed by restart)
    collection = await get_jobs_collection(db)
    result = await collection.update_many(
        {"status": IngestionStatus.RUNNING},
        {"$set": {
            "status": "INTERRUPTED",
            "interrupted_at": datetime.now().isoformat()
        }}
    )
    
    if result.modified_count > 0:
        logger.info(f"Marked {result.modified_count} running job(s) as interrupted")
    
    # Check for interrupted jobs to resume
    interrupted_job = await collection.find_one({"status": "INTERRUPTED"})
    
    if interrupted_job:
        job_id = interrupted_job["_id"]
        config = interrupted_job.get("config", {})
        
        # If config doesn't have incremental set, default to True for resume
        if "incremental" not in config:
            config["incremental"] = True
        
        logger.info(f"Found interrupted job {job_id}, resuming...")
        
        # Resume the job using asyncio.create_task
        asyncio.create_task(run_ingestion(job_id, config, db))
        
        return job_id
    
    return None


async def graceful_shutdown_handler(db):
    """
    Handle graceful shutdown - mark running jobs as interrupted.
    
    Called during application shutdown.
    """
    global _shutdown_requested
    
    if _current_job_id:
        logger.info(f"Graceful shutdown: marking job {_current_job_id} as interrupted")
        _shutdown_requested = True
        await mark_job_interrupted(db, _current_job_id)
    else:
        # Just in case, mark any running jobs
        collection = await get_jobs_collection(db)
        result = await collection.update_many(
            {"status": IngestionStatus.RUNNING},
            {"$set": {
                "status": "INTERRUPTED",
                "interrupted_at": datetime.now().isoformat()
            }}
        )
        if result.modified_count > 0:
            logger.info(f"Marked {result.modified_count} job(s) as interrupted during shutdown")


def _find_files_sync(folders: List[str], patterns: List[str]) -> List[str]:
    """
    Synchronous helper to find files - runs in thread pool.
    
    Uses os.walk() for single-pass directory traversal instead of multiple
    glob calls, which is much faster on network filesystems.
    """
    # Convert glob patterns to extensions set for O(1) lookup
    extensions = set()
    for pattern in patterns:
        # Extract extension from pattern like "*.pdf" -> ".pdf"
        if pattern.startswith("*."):
            extensions.add(pattern[1:].lower())  # ".pdf"
        elif pattern.startswith("*"):
            extensions.add(pattern[1:].lower())
    
    all_files = []
    for folder in folders:
        if not os.path.exists(folder):
            continue
        
        # Single-pass directory traversal
        for root, dirs, files in os.walk(folder):
            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if ext in extensions:
                    all_files.append(os.path.join(root, filename))
    
    return all_files


def _get_files_metadata_batch_sync(file_paths: List[str], folders: List[str]) -> List[dict]:
    """Get file metadata for a batch of files synchronously - runs in thread pool."""
    results = []
    for file_path in file_paths:
        try:
            # Calculate source path
            document_source = os.path.basename(file_path)
            for folder in folders:
                if os.path.abspath(file_path).startswith(os.path.abspath(folder)):
                    document_source = os.path.relpath(file_path, folder)
                    break
            
            stat = os.stat(file_path)
            ext = os.path.splitext(file_path)[1].lower()
            results.append({
                "name": os.path.basename(file_path),
                "path": document_source,
                "size_bytes": stat.st_size,
                "format": ext[1:] if ext else "unknown",
                "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
        except OSError:
            continue
    return results


async def _build_pending_files_queue(pipeline, incremental: bool = True, job_state: dict = None, config: dict = None, db = None):
    """
    Build the pending files queue from the ingestion pipeline.
    
    Called at the start of ingestion to populate the queue.
    Uses thread pool for blocking file system operations.
    Updates job_state with discovery progress if provided.
    
    Supports selective ingestion filters:
    - retry_image_only_pdfs: Only process image-only PDFs
    - retry_timeouts: Only process timed-out files
    - retry_errors: Only process errored files
    - retry_no_chunks: Only process files with 0 chunks
    - skip_image_only_pdfs: Skip known image-only PDFs
    """
    global _pending_files_queue
    
    logger.info("Starting _build_pending_files_queue...")
    
    # Extract filter options from config
    retry_image_only_pdfs = config.get("retry_image_only_pdfs", False) if config else False
    retry_timeouts = config.get("retry_timeouts", False) if config else False
    retry_errors = config.get("retry_errors", False) if config else False
    retry_no_chunks = config.get("retry_no_chunks", False) if config else False
    skip_image_only_pdfs = config.get("skip_image_only_pdfs", False) if config else False
    
    any_retry_filter = retry_image_only_pdfs or retry_timeouts or retry_errors or retry_no_chunks
    profile_key = config.get("profile", "") if config else ""
    
    # Initialize file registry service if db is available
    registry_service = FileRegistryService(db) if db else None
    
    # Helper to update discovery progress
    def update_discovery(folders_scanned=None, total_folders=None, files_found=None, 
                         files_to_process=None, files_skipped=None, current_folder=None):
        if job_state is not None:
            dp = job_state.get("discovery_progress", {})
            if folders_scanned is not None:
                dp["folders_scanned"] = folders_scanned
            if total_folders is not None:
                dp["total_folders"] = total_folders
            if files_found is not None:
                dp["files_found"] = files_found
            if files_to_process is not None:
                dp["files_to_process"] = files_to_process
            if files_skipped is not None:
                dp["files_skipped"] = files_skipped
            if current_folder is not None:
                dp["current_folder"] = current_folder
            job_state["discovery_progress"] = dp
    
    try:
        patterns = [
            "*.md", "*.markdown", "*.txt",
            "*.pdf", "*.docx", "*.doc",
            "*.pptx", "*.ppt", "*.xlsx", "*.xls",
            "*.html", "*.htm",
            "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.bmp",
            "*.mp3", "*.wav", "*.m4a", "*.flac",
            "*.mp4", "*.avi", "*.mkv", "*.mov", "*.webm",
        ]
        
        # Update phase to discovering
        if job_state is not None:
            job_state["phase"] = "discovering"
            job_state["phase_message"] = "Scanning folders for files..."
            update_discovery(total_folders=len(pipeline.documents_folders))
        
        _ingestion_logs.append({
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": f"Scanning {len(pipeline.documents_folders)} folder(s) for documents...",
            "logger": "ingestion"
        })
        
        # Run glob in thread pool to avoid blocking event loop
        loop = asyncio.get_running_loop()
        all_files = await loop.run_in_executor(
            None,
            _find_files_sync,
            pipeline.documents_folders,
            patterns
        )
        
        # Update discovery progress
        update_discovery(
            folders_scanned=len(pipeline.documents_folders),
            files_found=len(all_files)
        )
        
        # Yield control back to event loop
        await asyncio.sleep(0)
        
        if not all_files:
            _pending_files_queue = []
            if job_state is not None:
                job_state["phase_message"] = "No files found"
            return
        
        logger.info(f"Found {len(all_files)} total files, building queue...")
        
        _ingestion_logs.append({
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": f"Found {len(all_files)} total files, filtering...",
            "logger": "ingestion"
        })
        
        # Update phase to filtering
        if job_state is not None:
            job_state["phase"] = "filtering"
            job_state["phase_message"] = f"Filtering {len(all_files)} files..."
        
        # Get existing sources if incremental
        existing_sources = set()
        if incremental and pipeline._initialized:
            documents_collection = pipeline.db[
                pipeline.settings.mongodb_collection_documents
            ]
            cursor = documents_collection.find({}, {"source": 1})
            async for doc in cursor:
                if "source" in doc:
                    existing_sources.add(doc["source"])
        
        # Get file registry data for selective filtering
        registry_by_path = {}
        files_to_retry = set()  # Files matching retry filters
        files_to_skip = set()   # Files to skip based on classification
        
        if registry_service and profile_key:
            # Build registry lookup by file path
            registry_entries = await registry_service.list_files(
                profile_key=profile_key,
                limit=100000  # Get all entries
            )
            for entry in registry_entries:
                registry_by_path[entry.get("file_path", "")] = entry
            
            # If any retry filter is active, collect files to retry
            if any_retry_filter:
                classifications_to_retry = []
                if retry_image_only_pdfs:
                    classifications_to_retry.append(FileClassification.IMAGE_ONLY_PDF.value)
                if retry_timeouts:
                    classifications_to_retry.append(FileClassification.TIMEOUT.value)
                if retry_errors:
                    classifications_to_retry.append(FileClassification.ERROR.value)
                if retry_no_chunks:
                    classifications_to_retry.append(FileClassification.NO_CHUNKS.value)
                
                for path, entry in registry_by_path.items():
                    if entry.get("classification") in classifications_to_retry:
                        files_to_retry.add(path)
                
                _ingestion_logs.append({
                    "timestamp": datetime.now().isoformat(),
                    "level": "INFO",
                    "message": f"Retry filters active: {len(files_to_retry)} files match retry criteria",
                    "logger": "ingestion"
                })
            
            # If skip_image_only_pdfs is set, collect files to skip
            if skip_image_only_pdfs:
                for path, entry in registry_by_path.items():
                    if entry.get("classification") == FileClassification.IMAGE_ONLY_PDF.value:
                        files_to_skip.add(path)
                
                _ingestion_logs.append({
                    "timestamp": datetime.now().isoformat(),
                    "level": "INFO",
                    "message": f"Skip filter active: {len(files_to_skip)} image-only PDFs will be skipped",
                    "logger": "ingestion"
                })
        
        # Filter files that need processing
        files_to_process = []
        skipped_count = 0
        skipped_by_registry = 0
        retry_filter_count = 0
        
        for file_path in all_files:
            document_source = os.path.basename(file_path)
            for folder in pipeline.documents_folders:
                if os.path.abspath(file_path).startswith(os.path.abspath(folder)):
                    document_source = os.path.relpath(file_path, folder)
                    break
            
            # If retry filters are active and incremental is NOT checked:
            # Only process files that match retry criteria
            if any_retry_filter and not incremental:
                if file_path in files_to_retry:
                    files_to_process.append(file_path)
                    retry_filter_count += 1
                else:
                    skipped_by_registry += 1
                continue
            
            # Skip files based on skip filters
            if file_path in files_to_skip:
                skipped_by_registry += 1
                continue
            
            # Standard incremental check
            if incremental and document_source in existing_sources:
                skipped_count += 1
                continue
            
            files_to_process.append(file_path)
        
        # Update discovery with filter results
        total_skipped = skipped_count + skipped_by_registry
        update_discovery(
            files_to_process=len(files_to_process),
            files_skipped=total_skipped
        )
        
        # Yield control
        await asyncio.sleep(0)
        
        if not files_to_process:
            _pending_files_queue = []
            logger.info("No new files to process")
            if job_state is not None:
                skip_details = []
                if skipped_count > 0:
                    skip_details.append(f"{skipped_count} existing")
                if skipped_by_registry > 0:
                    skip_details.append(f"{skipped_by_registry} filtered")
                job_state["phase_message"] = f"No files to process (skipped: {', '.join(skip_details) if skip_details else '0'})"
            return
        
        # Build log message with filter details
        log_parts = [f"Processing {len(files_to_process)} files"]
        if retry_filter_count > 0:
            log_parts.append(f"retry filter matched: {retry_filter_count}")
        if skipped_count > 0:
            log_parts.append(f"existing: {skipped_count}")
        if skipped_by_registry > 0:
            log_parts.append(f"filtered by registry: {skipped_by_registry}")
        
        _ingestion_logs.append({
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": " | ".join(log_parts),
            "logger": "ingestion"
        })
        
        # Get metadata for all files in a single batch in thread pool
        if job_state is not None:
            job_state["phase_message"] = f"Building queue for {len(files_to_process)} files..."
        
        pending_files = await loop.run_in_executor(
            None,
            _get_files_metadata_batch_sync,
            files_to_process,
            pipeline.documents_folders
        )
        
        # Yield control
        await asyncio.sleep(0)
        
        # Sort by processing priority
        def sort_key(f):
            ext = f["format"].lower()
            type_priority = {
                "txt": 1, "md": 1, "markdown": 1,
                "html": 2, "htm": 2,
                "pdf": 3, "docx": 4, "doc": 4,
                "xlsx": 5, "xls": 5, "pptx": 6, "ppt": 6,
                "png": 7, "jpg": 7, "jpeg": 7, "gif": 7, "webp": 7, "bmp": 7,
                "mp3": 8, "wav": 8, "m4a": 8, "flac": 8,
                "mp4": 9, "avi": 9, "mkv": 9, "mov": 9, "webm": 9
            }.get(ext, 10)
            is_large = 1 if f["size_bytes"] > 5 * 1024 * 1024 else 0
            return (is_large, type_priority, f["size_bytes"])
        
        pending_files.sort(key=sort_key)
        _pending_files_queue = pending_files
        
        logger.info(f"Built pending files queue with {len(pending_files)} files")
        
    except Exception as e:
        logger.error(f"Error building pending files queue: {e}")
        _pending_files_queue = []


def _update_pending_files_queue(current: int, total: int, current_file: str):
    """
    Update the pending files queue by removing processed files.
    
    Called from the progress callback during ingestion.
    """
    global _pending_files_queue
    
    if not _pending_files_queue:
        return
    
    # Remove the processed file from the queue
    # Match by filename since we only have the path from progress callback
    filename = os.path.basename(current_file) if current_file else None
    if filename:
        _pending_files_queue = [
            f for f in _pending_files_queue 
            if f["name"] != filename
        ][:500]  # Keep max 500 for display


async def _apply_offline_mode_for_ingestion(db):
    """
    Check offline mode configuration and set environment variable.
    
    This is called before starting ingestion to ensure the OFFLINE_MODE
    environment variable is set based on the user's configuration.
    The ingestion pipeline will check this variable to decide whether
    to use local Whisper for audio transcription.
    """
    try:
        collection = db.db["offline_config"]
        doc = await collection.find_one({"_id": "config"})
        
        if doc and doc.get("enabled"):
            os.environ["OFFLINE_MODE"] = "true"
            
            # Also set the audio model URL if configured
            if doc.get("audio_url"):
                os.environ["OFFLINE_AUDIO_URL"] = doc.get("audio_url")
            if doc.get("audio_model"):
                os.environ["OFFLINE_AUDIO_MODEL"] = doc.get("audio_model")
            
            # Set vision model info for image processing
            if doc.get("vision_url"):
                os.environ["OFFLINE_VISION_URL"] = doc.get("vision_url")
            if doc.get("vision_model"):
                os.environ["OFFLINE_VISION_MODEL"] = doc.get("vision_model")
                
            logger.info(f"Offline mode enabled for ingestion (audio: {doc.get('audio_model')}, vision: {doc.get('vision_model')})")
        else:
            os.environ["OFFLINE_MODE"] = "false"
            # Clear model-specific env vars
            for key in ["OFFLINE_AUDIO_URL", "OFFLINE_AUDIO_MODEL", "OFFLINE_VISION_URL", "OFFLINE_VISION_MODEL"]:
                os.environ.pop(key, None)
    except Exception as e:
        logger.warning(f"Could not check offline config: {e}")
        # Default to not offline mode on error
        os.environ["OFFLINE_MODE"] = "false"


async def _calculate_job_extended_metrics(db, job_id: str) -> dict:
    """
    Calculate extended metrics for a job from ingestion_stats collection.
    
    Args:
        db: Database connection
        job_id: Job ID to calculate metrics for
        
    Returns:
        Dictionary with extended metrics:
        - image_only_pdf: Count of PDFs skipped due to no text layer
        - no_chunks: Count of files that produced 0 chunks after processing
        - timeout: Count of files that timed out
        - error: Count of files with processing errors
        - avg_processing_time_ms: Average processing time
        - total_size_bytes: Total bytes processed
    """
    try:
        stats_collection = db.db["ingestion_stats"]
        
        # Aggregate by error_type
        error_pipeline = [
            {"$match": {"job_id": job_id}},
            {
                "$group": {
                    "_id": "$error_type",
                    "count": {"$sum": 1}
                }
            }
        ]
        error_results = await stats_collection.aggregate(error_pipeline).to_list(length=100)
        
        # Build error type counts
        error_counts = {}
        for r in error_results:
            error_type = r["_id"] if r["_id"] else "success"
            error_counts[error_type] = r["count"]
        
        # Get aggregate stats
        stats_pipeline = [
            {"$match": {"job_id": job_id}},
            {
                "$group": {
                    "_id": None,
                    "avg_processing_time_ms": {"$avg": "$processing_time_ms"},
                    "total_size_bytes": {"$sum": "$file_size_bytes"}
                }
            }
        ]
        agg_results = await stats_collection.aggregate(stats_pipeline).to_list(length=1)
        agg_stats = agg_results[0] if agg_results else {}
        
        return {
            **error_counts,
            "avg_processing_time_ms": agg_stats.get("avg_processing_time_ms", 0) or 0,
            "total_size_bytes": agg_stats.get("total_size_bytes", 0) or 0
        }
        
    except Exception as e:
        logger.error(f"Failed to calculate extended metrics for job {job_id}: {e}")
        return {}


async def run_ingestion(job_id: str, config: dict, db):
    """Run ingestion in background with DB persistence."""
    global _current_job_id, _shutdown_requested, _pause_requested, _stop_requested, _is_paused, _current_job_state, _pending_files_queue
    _current_job_id = job_id
    _shutdown_requested = False
    _pause_requested = False
    _stop_requested = False
    _is_paused = False
    _pending_files_queue = []
    
    # Clear logs and add handler
    _ingestion_logs.clear()
    
    # Add log handler to capture all ingestion logs (root logger only - src.* logs propagate up)
    root_logger = logging.getLogger()
    root_logger.addHandler(_log_handler)
    
    # Helper to update job in memory and DB
    job_state = {
        "job_id": job_id,
        "status": IngestionStatus.RUNNING,
        "started_at": datetime.now().isoformat(),
        "completed_at": None,
        "total_files": 0,
        "processed_files": 0,
        "failed_files": 0,
        "duplicates_skipped": 0,
        "excluded_files": 0,
        "document_count": 0,
        "image_count": 0,
        "audio_count": 0,
        "video_count": 0,
        "chunks_created": 0,
        "current_file": None,
        "errors": [],
        "progress_percent": 0.0,
        "config": config,  # Store config for resume capability
        "profile": config.get("profile"),
        # Phase tracking for transparency
        "phase": "initializing",
        "phase_message": "Starting ingestion...",
        "discovery_progress": {
            "folders_scanned": 0,
            "total_folders": 0,
            "files_found": 0,
            "files_to_process": 0,
            "files_skipped": 0,
            "current_folder": None
        },
        # Processing metrics
        "first_file_time": None,
        "processing_rate": 0.0,  # files per minute
        # Extended metrics for job analysis
        "image_only_pdfs": 0,  # PDFs skipped due to no text layer
        "no_chunks_files": 0,  # Files that produced 0 chunks after processing
        "timeout_files": 0,  # Files that timed out
        "error_files": 0,  # Files with processing errors
        "avg_processing_time_ms": 0.0,
        "total_size_bytes": 0,
        "files_per_hour": 0.0,
        "success_rate": 0.0,  # Percentage of files that produced chunks
    }
    
    # Store in global for real-time status access
    _current_job_state = job_state
    
    async def update_job_state(**kwargs):
        job_state.update(kwargs)
        await save_job_to_db(db, job_state)
    
    try:
        await update_job_state()
        
        _ingestion_logs.append({
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": f"Starting ingestion job {job_id}",
            "logger": "ingestion"
        })
        
        # Check offline mode configuration and set environment variable
        await _apply_offline_mode_for_ingestion(db)
        
        # Import ingestion module
        from src.ingestion.ingest import DocumentIngestionPipeline, IngestionConfig
        from src.profile import get_profile_manager
        
        # Switch profile if specified
        if config.get("profile"):
            pm = get_profile_manager()
            pm.switch_profile(config["profile"])
            _ingestion_logs.append({
                "timestamp": datetime.now().isoformat(),
                "level": "INFO",
                "message": f"Switched to profile: {config['profile']}",
                "logger": "ingestion"
            })
        
        # Create ingestion config
        ing_config = IngestionConfig(
            chunk_size=config.get("chunk_size", 1000),
            chunk_overlap=config.get("chunk_overlap", 200),
            max_chunk_size=config.get("chunk_size", 1000) * 2,
            max_tokens=config.get("max_tokens", 512)
        )
        
        # Helper to create pipeline synchronously (includes heavy tokenizer loading)
        def create_pipeline_sync():
            return DocumentIngestionPipeline(
                config=ing_config,
                documents_folder=config.get("documents_folder"),
                clean_before_ingest=config.get("clean_before_ingest", False),
                use_profile=True
            )
        
        # Create pipeline in thread pool to avoid blocking event loop
        # The pipeline __init__ loads tokenizers which can be slow
        job_state["phase_message"] = "Loading tokenizers..."
        _ingestion_logs.append({
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": "Initializing pipeline (loading tokenizers)...",
            "logger": "ingestion"
        })
        
        loop = asyncio.get_running_loop()
        pipeline = await loop.run_in_executor(None, create_pipeline_sync)
        
        # Yield control after heavy initialization
        await asyncio.sleep(0)
        
        # If not incremental, show cleaning phase
        if config.get("clean_before_ingest", False):
            job_state["phase"] = "cleaning"
            job_state["phase_message"] = "Removing existing data..."
            await save_job_to_db(db, job_state)
            _ingestion_logs.append({
                "timestamp": datetime.now().isoformat(),
                "level": "INFO",
                "message": "Cleaning existing data from database...",
                "logger": "ingestion"
            })
        
        # Build initial pending files queue
        await _build_pending_files_queue(pipeline, config.get("incremental", True), job_state, config, db)
        
        # Update phase to processing
        job_state["phase"] = "processing"
        total_to_process = job_state["discovery_progress"].get("files_to_process", 0)
        job_state["phase_message"] = f"Processing {total_to_process} files..."
        
        # Track progress - save to DB periodically
        last_db_update = datetime.now()
        
        async def progress_callback_async(current: int, total: int, current_file: str = None, chunks_in_file: int = 0):
            nonlocal last_db_update
            global _pause_requested, _stop_requested, _is_paused
            
            # Check for stop request
            if _stop_requested:
                raise asyncio.CancelledError("Stop requested by user")
            
            # Check for shutdown
            if _shutdown_requested:
                raise asyncio.CancelledError("Shutdown requested")
            
            # Handle pause - wait until unpaused
            while _pause_requested:
                _is_paused = True
                job_state["status"] = IngestionStatus.PAUSED
                await save_job_to_db(db, job_state)
                await asyncio.sleep(1)
                if _stop_requested:
                    raise asyncio.CancelledError("Stop requested while paused")
            
            if _is_paused:
                _is_paused = False
                job_state["status"] = IngestionStatus.RUNNING
                await save_job_to_db(db, job_state)
            
            job_state["processed_files"] = current
            job_state["total_files"] = total
            job_state["progress_percent"] = (current / total * 100) if total > 0 else 0
            
            # Track first file time and calculate processing rate
            if current == 1 and job_state.get("first_file_time") is None:
                job_state["first_file_time"] = datetime.now().isoformat()
            
            if job_state.get("first_file_time") and current > 0:
                first_time = datetime.fromisoformat(job_state["first_file_time"])
                elapsed_minutes = (datetime.now() - first_time).total_seconds() / 60
                if elapsed_minutes > 0:
                    job_state["processing_rate"] = round(current / elapsed_minutes, 2)
            
            # Update phase message with current progress
            job_state["phase_message"] = f"Processing file {current} of {total}..."
            
            # Accumulate chunks incrementally
            if chunks_in_file > 0:
                job_state["chunks_created"] = job_state.get("chunks_created", 0) + chunks_in_file
            
            if current_file:
                job_state["current_file"] = current_file
                
                # Update pending files queue - remove processed file
                _update_pending_files_queue(current, total, current_file)
                
                # Categorize file by extension - only count once when processing starts (current < processed_files + 1)
                # We only increment counts when chunks_in_file > 0 (after processing)
                if chunks_in_file > 0 or current == job_state.get("_last_counted_file_idx", -1):
                    pass  # Already counted or processing complete
                else:
                    job_state["_last_counted_file_idx"] = current
                    ext = current_file.lower().split('.')[-1] if '.' in current_file else ''
                    if ext in ['pdf', 'doc', 'docx', 'txt', 'md', 'html', 'htm', 'xlsx', 'xls', 'pptx', 'ppt']:
                        job_state["document_count"] = job_state.get("document_count", 0) + 1
                    elif ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp']:
                        job_state["image_count"] = job_state.get("image_count", 0) + 1
                    elif ext in ['mp3', 'wav', 'flac', 'm4a', 'ogg', 'wma']:
                        job_state["audio_count"] = job_state.get("audio_count", 0) + 1
                    elif ext in ['mp4', 'avi', 'mkv', 'mov', 'wmv', 'webm']:
                        job_state["video_count"] = job_state.get("video_count", 0) + 1
                
                _ingestion_logs.append({
                    "timestamp": datetime.now().isoformat(),
                    "level": "INFO",
                    "message": f"Processing ({current}/{total}): {current_file}" + (f" - {chunks_in_file} chunks" if chunks_in_file > 0 else ""),
                    "logger": "ingestion"
                })
            
            # Update DB every 10 seconds to track progress
            if (datetime.now() - last_db_update).total_seconds() > 10:
                await save_job_to_db(db, job_state)
                last_db_update = datetime.now()
        
        # Sync wrapper for the callback
        def progress_callback(current: int, total: int, current_file: str = None, chunks_in_file: int = 0):
            asyncio.create_task(progress_callback_async(current, total, current_file, chunks_in_file))
        
        # Load performance config for concurrency
        perf_config = await db.db["ingestion_config"].find_one({"_id": "performance_config"})
        max_concurrent = perf_config.get("max_concurrent_files", 1) if perf_config else 1
        
        logger.info(f"Running ingestion with max_concurrent_files={max_concurrent}")
        
        # Run ingestion with configured concurrency
        results = await pipeline.ingest_documents(
            progress_callback=progress_callback,
            incremental=config.get("incremental", True),
            max_concurrent_files=max_concurrent,
            job_id=job_id
        )
        
        # Update phase to finalizing
        job_state["phase"] = "finalizing"
        job_state["phase_message"] = "Calculating metrics..."
        
        # Calculate stats - distinguish between real errors and duplicates
        duplicates_skipped = sum(
            1 for r in results 
            if r.errors and len(r.errors) > 0 and "Duplicate of:" in r.errors[0]
        )
        actual_failures = sum(
            1 for r in results 
            if r.errors and len(r.errors) > 0 and "Duplicate of:" not in r.errors[0]
        )
        
        # Calculate extended metrics from ingestion_stats
        extended_metrics = await _calculate_job_extended_metrics(db, job_id)
        
        # Calculate elapsed time and files per hour
        elapsed_seconds = (datetime.now() - datetime.fromisoformat(job_state["started_at"])).total_seconds()
        files_per_hour = (len(results) / elapsed_seconds * 3600) if elapsed_seconds > 0 else 0
        
        # Calculate success rate (files that produced chunks)
        successful_files = sum(1 for r in results if r.chunks_created > 0)
        success_rate = (successful_files / len(results) * 100) if len(results) > 0 else 0
        
        # Update job status to completed with extended metrics
        await update_job_state(
            status=IngestionStatus.COMPLETED,
            completed_at=datetime.now().isoformat(),
            processed_files=len(results),
            chunks_created=sum(r.chunks_created for r in results),
            failed_files=actual_failures,
            duplicates_skipped=duplicates_skipped,
            errors=[err for r in results for err in r.errors if "Duplicate of:" not in err][:20],
            progress_percent=100.0,
            phase="completed",
            phase_message="Ingestion complete",
            # Extended metrics
            image_only_pdfs=extended_metrics.get("image_only_pdf", 0),
            no_chunks_files=extended_metrics.get("no_chunks", 0),
            timeout_files=extended_metrics.get("timeout", 0),
            error_files=extended_metrics.get("error", 0),
            avg_processing_time_ms=extended_metrics.get("avg_processing_time_ms", 0),
            total_size_bytes=extended_metrics.get("total_size_bytes", 0),
            files_per_hour=files_per_hour,
            success_rate=success_rate
        )
        
        _ingestion_logs.append({
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": f"Ingestion completed. Files: {len(results)}, Chunks: {job_state['chunks_created']}",
            "logger": "ingestion"
        })
        
        # Trigger post-ingestion backup
        try:
            profile_key = config.get("profile", "default")
            backup_result = await trigger_post_ingestion_backup(
                db_manager=db,
                profile_key=profile_key,
                job_id=job_id,
                documents_added=len(results),
                chunks_added=job_state.get("chunks_created", 0)
            )
            if backup_result:
                _ingestion_logs.append({
                    "timestamp": datetime.now().isoformat(),
                    "level": "INFO",
                    "message": f"Post-ingestion backup created: {backup_result.backup_id}",
                    "logger": "backup"
                })
        except Exception as backup_error:
            logger.warning(f"Post-ingestion backup failed (non-critical): {backup_error}")
        
    except asyncio.CancelledError:
        logger.warning(f"Ingestion job {job_id} was cancelled/interrupted")
        await update_job_state(
            status="INTERRUPTED",
            interrupted_at=datetime.now().isoformat(),
            phase="stopped",
            phase_message="Ingestion interrupted"
        )
        _ingestion_logs.append({
            "timestamp": datetime.now().isoformat(),
            "level": "WARNING",
            "message": f"Ingestion interrupted (will resume on restart)",
            "logger": "ingestion"
        })
        
    except Exception as e:
        logger.error(f"Ingestion job {job_id} failed: {e}")
        await update_job_state(
            status=IngestionStatus.FAILED,
            completed_at=datetime.now().isoformat(),
            errors=[str(e)],
            phase="failed",
            phase_message=f"Error: {str(e)[:100]}"
        )
        _ingestion_logs.append({
            "timestamp": datetime.now().isoformat(),
            "level": "ERROR",
            "message": f"Ingestion failed: {str(e)}",
            "logger": "ingestion"
        })
    finally:
        # Save logs to job document before clearing
        if job_id:
            try:
                collection = await get_jobs_collection(db)
                # Get final logs list (convert deque to list)
                final_logs = list(_ingestion_logs)
                await collection.update_one(
                    {"_id": job_id},
                    {"$set": {"logs": final_logs}}
                )
                logger.info(f"Saved {len(final_logs)} log entries to job {job_id}")
            except Exception as e:
                logger.error(f"Failed to save logs for job {job_id}: {e}")
        
        # Remove log handler
        root_logger.removeHandler(_log_handler)
        _current_job_id = None
        _current_job_state = None
        _pending_files_queue = []  # Clear queue


@router.post("/start", response_model=IngestionStatusResponse)
async def start_ingestion(
    request_obj: Request,
    request: IngestionStartRequest,
    background_tasks: BackgroundTasks
):
    """
    Start document ingestion.
    
    Initiates a background ingestion job for documents in the configured folder.
    """
    db = request_obj.app.state.db
    
    # Check if another job is running (in DB)
    running_job = await get_running_job_from_db(db)
    if running_job and running_job.get("status") == IngestionStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail=f"Ingestion job {running_job['job_id']} is already running"
        )
    
    # Create new job
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "status": IngestionStatus.PENDING,
        "started_at": None,
        "completed_at": None,
        "total_files": 0,
        "processed_files": 0,
        "failed_files": 0,
        "duplicates_skipped": 0,
        "chunks_created": 0,
        "current_file": None,
        "errors": [],
        "progress_percent": 0.0
    }
    
    # Save initial job state to DB
    await save_job_to_db(db, job)
    
    # Start background task with DB reference
    config = request.model_dump()
    background_tasks.add_task(run_ingestion, job_id, config, db)
    
    return IngestionStatusResponse(**job)


@router.get("/status", response_model=IngestionStatusResponse)
async def get_ingestion_status(request: Request):
    """
    Get current ingestion status.
    
    Returns the status of the most recent or running ingestion job.
    Uses in-memory state for real-time updates when job is running.
    """
    global _is_paused, _pause_requested, _current_job_state
    db = request.app.state.db
    
    # If there's a running job, return the real-time in-memory state
    if _current_job_state is not None and _current_job_id is not None:
        job_data = _current_job_state
    else:
        # Get latest job from DB
        job_data = await get_latest_job_from_db(db)
    
    if not job_data:
        return IngestionStatusResponse(
            status=IngestionStatus.COMPLETED,
            job_id=None,
            total_files=0,
            processed_files=0,
            progress_percent=0.0,
            elapsed_seconds=0.0,
            is_paused=False,
            can_pause=False,
            can_stop=False
        )
    
    # Calculate elapsed time and ETA
    elapsed_seconds = 0.0
    estimated_remaining = None
    
    started_at = job_data.get("started_at")
    completed_at = job_data.get("completed_at")
    
    if started_at:
        # Parse datetime if string and strip timezone info for consistency
        if isinstance(started_at, str):
            started_at = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
            if started_at.tzinfo is not None:
                started_at = started_at.replace(tzinfo=None)
        elif hasattr(started_at, 'tzinfo') and started_at.tzinfo is not None:
            started_at = started_at.replace(tzinfo=None)
            
        if completed_at:
            if isinstance(completed_at, str):
                completed_at = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
                if completed_at.tzinfo is not None:
                    completed_at = completed_at.replace(tzinfo=None)
            elif hasattr(completed_at, 'tzinfo') and completed_at.tzinfo is not None:
                completed_at = completed_at.replace(tzinfo=None)
            elapsed_seconds = (completed_at - started_at).total_seconds()
        else:
            elapsed_seconds = (datetime.now() - started_at).total_seconds()
        
        # Calculate ETA if running
        status = job_data.get("status")
        if status == IngestionStatus.RUNNING or status == "running":
            progress = job_data.get("progress_percent", 0)
            if progress > 0:
                total_estimated = elapsed_seconds / (progress / 100)
                estimated_remaining = max(0, total_estimated - elapsed_seconds)
    
    # Determine if job is currently pausable/stoppable
    status = job_data.get("status")
    is_active = status in [IngestionStatus.RUNNING, IngestionStatus.PAUSED, "running", "paused", "INTERRUPTED"]
    
    # Filter out fields not in response model
    response_fields = {
        k: v for k, v in job_data.items() 
        if k not in ["elapsed_seconds", "estimated_remaining_seconds", "config", "interrupted_at", 
                     "is_paused", "can_pause", "can_stop", "_last_counted_file_idx", "first_file_time"]
    }
    
    # Normalize status to lowercase (DB may have uppercase from worker)
    if "status" in response_fields and isinstance(response_fields["status"], str):
        response_fields["status"] = response_fields["status"].lower()
    
    return IngestionStatusResponse(
        **response_fields,
        elapsed_seconds=elapsed_seconds,
        estimated_remaining_seconds=estimated_remaining,
        is_paused=_is_paused or _pause_requested,
        can_pause=is_active and not _pause_requested,
        can_stop=is_active
    )


@router.get("/status/{job_id}", response_model=IngestionStatusResponse)
async def get_job_status(request: Request, job_id: str):
    """Get status of a specific ingestion job."""
    db = request.app.state.db
    job = await get_job_from_db(db, job_id)
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Filter out config field
    response_fields = {k: v for k, v in job.items() if k not in ["config", "interrupted_at"]}
    
    # Normalize status to lowercase
    if "status" in response_fields and isinstance(response_fields["status"], str):
        response_fields["status"] = response_fields["status"].lower()
    
    return IngestionStatusResponse(**response_fields)


@router.post("/cancel/{job_id}", response_model=SuccessResponse)
async def cancel_ingestion(request: Request, job_id: str):
    """
    Cancel a running ingestion job.
    
    Note: This marks the job as cancelled but may not stop immediately.
    """
    global _shutdown_requested
    db = request.app.state.db
    
    job = await get_job_from_db(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job["status"] != IngestionStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Job is not running (status: {job['status']})"
        )
    
    # Signal shutdown to the running task
    if _current_job_id == job_id:
        _shutdown_requested = True
    
    # Update job in DB
    job["status"] = IngestionStatus.CANCELLED
    job["completed_at"] = datetime.now().isoformat()
    await save_job_to_db(db, job)
    
    return SuccessResponse(success=True, message="Ingestion cancelled")


@router.get("/jobs")
async def list_ingestion_jobs(request: Request):
    """List all ingestion jobs."""
    db = request.app.state.db
    collection = await get_jobs_collection(db)
    
    jobs = []
    async for doc in collection.find().sort("started_at", -1).limit(20):
        doc["job_id"] = doc.pop("_id")
        jobs.append({
            "job_id": doc["job_id"],
            "status": doc.get("status"),
            "started_at": doc.get("started_at"),
            "completed_at": doc.get("completed_at"),
            "progress_percent": doc.get("progress_percent", 0)
        })
    
    return {"jobs": jobs}


@router.get("/runs", response_model=IngestionRunsResponse)
async def list_ingestion_runs(
    request: Request,
    page: int = 1,
    page_size: int = 5
):
    """
    Get paginated list of ingestion runs with full stats.
    
    Args:
        page: Page number (1-based)
        page_size: Number of runs per page (default 5)
    """
    db = request.app.state.db
    collection = await get_jobs_collection(db)
    
    # Use estimated_document_count for faster results (jobs collection is small anyway)
    total = await collection.estimated_document_count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    
    # Get paginated runs
    skip = (page - 1) * page_size
    runs = []
    
    async for doc in collection.find().sort("started_at", -1).skip(skip).limit(page_size):
        job_id = doc.pop("_id")
        
        # Calculate elapsed time
        elapsed_seconds = 0.0
        started_at = doc.get("started_at")
        completed_at = doc.get("completed_at")
        
        if started_at:
            if isinstance(started_at, str):
                started_at = datetime.fromisoformat(started_at)
            if completed_at:
                if isinstance(completed_at, str):
                    completed_at = datetime.fromisoformat(completed_at)
                elapsed_seconds = (completed_at - started_at).total_seconds()
            else:
                elapsed_seconds = (datetime.now() - started_at).total_seconds()
        
        runs.append(IngestionRunSummary(
            job_id=job_id,
            status=doc.get("status", "unknown"),
            started_at=doc.get("started_at"),
            completed_at=doc.get("completed_at"),
            total_files=doc.get("total_files", 0),
            processed_files=doc.get("processed_files", 0),
            failed_files=doc.get("failed_files", 0),
            excluded_files=doc.get("excluded_files", 0),
            document_count=doc.get("document_count", 0),
            image_count=doc.get("image_count", 0),
            audio_count=doc.get("audio_count", 0),
            video_count=doc.get("video_count", 0),
            chunks_created=doc.get("chunks_created", 0),
            elapsed_seconds=elapsed_seconds,
            profile=doc.get("profile"),
            # Extended metrics
            duplicates_skipped=doc.get("duplicates_skipped", 0),
            image_only_pdfs=doc.get("image_only_pdfs", 0),
            no_chunks_files=doc.get("no_chunks_files", 0),
            timeout_files=doc.get("timeout_files", 0),
            error_files=doc.get("error_files", 0),
            avg_processing_time_ms=doc.get("avg_processing_time_ms", 0.0),
            total_size_bytes=doc.get("total_size_bytes", 0),
            files_per_hour=doc.get("files_per_hour", 0.0),
            success_rate=doc.get("success_rate", 0.0)
        ))
    
    return IngestionRunsResponse(
        runs=runs,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages
    )


# Global to track pending files queue during ingestion
_pending_files_queue: List[dict] = []
_pending_files_lock = asyncio.Lock()


@router.get("/pending-files")
async def get_pending_files(
    request: Request,
    limit: int = 500
):
    """
    Get list of files pending to be indexed.
    
    Returns files that are queued for processing, including their metadata.
    Limited to 500 files by default.
    Uses thread pool for blocking file operations.
    
    Args:
        limit: Maximum number of files to return (default 500, max 1000)
    """
    global _pending_files_queue
    
    limit = min(limit, 1000)
    
    # If ingestion is running, return the current queue
    if _current_job_id is not None and _pending_files_queue:
        return {
            "files": _pending_files_queue[:limit],
            "total": len(_pending_files_queue),
            "is_running": True
        }
    
    # Otherwise, calculate pending files from disk and DB
    db = request.app.state.db
    
    try:
        from src.profile import get_profile_manager
        
        # Create a temporary pipeline to find files
        pm = get_profile_manager()
        profile = pm.get_profile(pm.get_active_profile_name())
        documents_folders = profile.get("documents_folders", [])
        
        if not documents_folders:
            return {"files": [], "total": 0, "is_running": False}
        
        # Supported file patterns
        patterns = [
            "*.md", "*.markdown", "*.txt",
            "*.pdf", "*.docx", "*.doc",
            "*.pptx", "*.ppt", "*.xlsx", "*.xls",
            "*.html", "*.htm",
            "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.bmp",
            "*.mp3", "*.wav", "*.m4a", "*.flac",
            "*.mp4", "*.avi", "*.mkv", "*.mov", "*.webm",
        ]
        
        # Run glob in thread pool to avoid blocking event loop
        loop = asyncio.get_running_loop()
        all_files = await loop.run_in_executor(
            None,
            _find_files_sync,
            documents_folders,
            patterns
        )
        
        if not all_files:
            return {"files": [], "total": 0, "is_running": False}
        
        # Get existing sources from DB
        documents_collection = db.documents_collection
        existing_sources = set()
        cursor = documents_collection.find({}, {"source": 1})
        async for doc in cursor:
            if "source" in doc:
                existing_sources.add(doc["source"])
        
        # Filter to pending files
        files_to_process = []
        for file_path in all_files:
            # Calculate source path
            document_source = os.path.basename(file_path)
            for folder in documents_folders:
                if os.path.abspath(file_path).startswith(os.path.abspath(folder)):
                    document_source = os.path.relpath(file_path, folder)
                    break
            
            if document_source in existing_sources:
                continue
            
            files_to_process.append(file_path)
            
            # Limit to first 1000 files for quick response
            if len(files_to_process) >= 1000:
                break
        
        if not files_to_process:
            return {"files": [], "total": 0, "is_running": False}
        
        # Yield control
        await asyncio.sleep(0)
        
        # Get metadata for all files in a single batch in thread pool
        pending_files = await loop.run_in_executor(
            None,
            _get_files_metadata_batch_sync,
            files_to_process,
            documents_folders
        )
        
        # Sort by processing priority (text first, large files last)
        def sort_key(f):
            ext = f["format"].lower()
            type_priority = {
                "txt": 1, "md": 1, "markdown": 1,
                "html": 2, "htm": 2,
                "pdf": 3, "docx": 4, "doc": 4,
                "xlsx": 5, "xls": 5, "pptx": 6, "ppt": 6,
                "png": 7, "jpg": 7, "jpeg": 7, "gif": 7, "webp": 7, "bmp": 7,
                "mp3": 8, "wav": 8, "m4a": 8, "flac": 8,
                "mp4": 9, "avi": 9, "mkv": 9, "mov": 9, "webm": 9
            }.get(ext, 10)
            is_large = 1 if f["size_bytes"] > 5 * 1024 * 1024 else 0
            return (is_large, type_priority, f["size_bytes"])
        
        pending_files.sort(key=sort_key)
        
        return {
            "files": pending_files[:limit],
            "total": len(pending_files),
            "is_running": False
        }
        
    except Exception as e:
        logger.error(f"Error getting pending files: {e}")
        return {"files": [], "total": 0, "is_running": False, "error": str(e)}


# ============== Metadata Rebuild ==============

async def _run_metadata_rebuild(db):
    """
    Background task to rebuild chunks_count metadata for all documents.
    
    Processes documents in batches using pagination to avoid blocking.
    """
    global _metadata_rebuild_state
    
    try:
        collection = db.documents_collection
        chunks_collection = db.chunks_collection
        
        # Count documents needing update (chunks_count missing or 0)
        query = {
            "$or": [
                {"metadata.chunks_count": {"$exists": False}},
                {"metadata.chunks_count": None},
                {"metadata.chunks_count": 0}
            ]
        }
        total_docs = await collection.count_documents(query)
        
        _metadata_rebuild_state["total"] = total_docs
        _metadata_rebuild_state["status"] = "running"
        
        if total_docs == 0:
            _metadata_rebuild_state["status"] = "completed"
            _metadata_rebuild_state["message"] = "All documents already have valid chunks_count"
            return
        
        logger.info(f"Starting metadata rebuild for {total_docs} documents")
        
        # Strategy: Get all chunk counts in a single aggregation, then bulk update documents
        # Run in thread executor to avoid blocking event loop
        
        logger.info("[Rebuild] Aggregating all chunk counts (this may take a moment)...")
        _metadata_rebuild_state["message"] = "Counting chunks..."
        
        # Get all document IDs that need updating
        doc_ids = []
        async for doc in collection.find(query, {"_id": 1}):
            doc_ids.append(doc["_id"])
        
        logger.info(f"[Rebuild] Found {len(doc_ids)} documents to process")
        
        if not doc_ids:
            _metadata_rebuild_state["status"] = "completed"
            _metadata_rebuild_state["message"] = "No documents need updating"
            return
        
        # Get chunk counts using a simpler approach - lookup from chunks
        # For each document, we'll update with count or -1
        _metadata_rebuild_state["message"] = "Updating documents..."
        
        processed = 0
        updated = 0
        
        # Process in small batches to allow yielding
        batch_size = 10
        for i in range(0, len(doc_ids), batch_size):
            batch = doc_ids[i:i + batch_size]
            
            # Get chunk counts for this batch using aggregation
            pipeline = [
                {"$match": {"document_id": {"$in": batch}}},
                {"$group": {"_id": "$document_id", "count": {"$sum": 1}}}
            ]
            
            chunk_counts = {}
            async for result in chunks_collection.aggregate(pipeline):
                chunk_counts[result["_id"]] = result["count"]
            
            # Update documents in this batch
            for doc_id in batch:
                count = chunk_counts.get(doc_id, 0)
                final_count = count if count > 0 else -1
                
                await collection.update_one(
                    {"_id": doc_id},
                    {"$set": {"metadata.chunks_count": final_count}}
                )
                
                if count > 0:
                    updated += 1
                processed += 1
            
            # Update progress
            _metadata_rebuild_state["processed"] = processed
            _metadata_rebuild_state["updated"] = updated
            _metadata_rebuild_state["progress_percent"] = round(processed / total_docs * 100, 1)
            
            # Log every 50 documents and yield to event loop
            if processed % 50 == 0 or processed == total_docs:
                logger.info(f"Rebuild progress: {processed}/{total_docs} ({_metadata_rebuild_state['progress_percent']}%)")
            await asyncio.sleep(0.01)
        
        _metadata_rebuild_state["status"] = "completed"
        _metadata_rebuild_state["progress_percent"] = 100
        _metadata_rebuild_state["message"] = f"Updated {updated} documents out of {processed} processed"
        _metadata_rebuild_state["completed_at"] = datetime.now().isoformat()
        
        logger.info(f"Metadata rebuild completed: {updated}/{processed} documents updated")
        
    except asyncio.CancelledError:
        _metadata_rebuild_state["status"] = "cancelled"
        _metadata_rebuild_state["message"] = "Rebuild was cancelled"
        logger.warning("Metadata rebuild was cancelled")
    except Exception as e:
        _metadata_rebuild_state["status"] = "failed"
        _metadata_rebuild_state["error"] = str(e)
        logger.error(f"Metadata rebuild failed: {e}", exc_info=True)


@router.post("/rebuild-metadata")
async def start_metadata_rebuild(request: Request):
    """
    Start async metadata rebuild to fix chunks_count for all documents.
    
    Processes documents with missing or zero chunks_count in batches,
    counting actual chunks from the chunks collection and updating
    the document metadata.
    
    Returns immediately with job status - use GET /rebuild-metadata for progress.
    """
    global _metadata_rebuild_state, _metadata_rebuild_task
    
    # Check if rebuild is already running
    if _metadata_rebuild_state and _metadata_rebuild_state.get("status") == "running":
        return {
            "success": False,
            "message": "Metadata rebuild is already running",
            "status": _metadata_rebuild_state
        }
    
    db = request.app.state.db
    
    # Initialize state
    _metadata_rebuild_state = {
        "status": "starting",
        "started_at": datetime.now().isoformat(),
        "completed_at": None,
        "total": 0,
        "processed": 0,
        "updated": 0,
        "progress_percent": 0,
        "message": None,
        "error": None
    }
    
    # Start background task
    _metadata_rebuild_task = asyncio.create_task(_run_metadata_rebuild(db))
    
    return {
        "success": True,
        "message": "Metadata rebuild started",
        "status": _metadata_rebuild_state
    }


@router.get("/rebuild-metadata")
async def get_metadata_rebuild_status():
    """
    Get the status of the metadata rebuild process.
    
    Returns current progress if a rebuild is running or the result of the last rebuild.
    """
    global _metadata_rebuild_state
    
    if not _metadata_rebuild_state:
        return {
            "running": False,
            "status": None,
            "message": "No rebuild has been started"
        }
    
    return {
        "running": _metadata_rebuild_state.get("status") == "running",
        "status": _metadata_rebuild_state
    }


@router.delete("/rebuild-metadata")
async def cancel_metadata_rebuild():
    """
    Cancel the running metadata rebuild process.
    """
    global _metadata_rebuild_task, _metadata_rebuild_state
    
    if not _metadata_rebuild_task or not _metadata_rebuild_state:
        return {"success": False, "message": "No rebuild is running"}
    
    if _metadata_rebuild_state.get("status") != "running":
        return {"success": False, "message": "Rebuild is not currently running"}
    
    _metadata_rebuild_task.cancel()
    return {"success": True, "message": "Rebuild cancellation requested"}


@router.post("/pause", response_model=SuccessResponse)
async def pause_ingestion(request: Request):
    """
    Pause the current ingestion job.
    
    The job will complete processing the current document before pausing.
    """
    global _pause_requested, _is_paused
    
    if not _current_job_id:
        raise HTTPException(status_code=400, detail="No ingestion job is running")
    
    if _pause_requested:
        return SuccessResponse(success=True, message="Already pausing or paused")
    
    _pause_requested = True
    
    _ingestion_logs.append({
        "timestamp": datetime.now().isoformat(),
        "level": "INFO",
        "message": "Pause requested - will pause after current document",
        "logger": "ingestion"
    })
    
    return SuccessResponse(success=True, message="Pause requested - will pause after current document")


@router.post("/resume", response_model=SuccessResponse)
async def resume_ingestion(request: Request):
    """
    Resume a paused ingestion job.
    """
    global _pause_requested, _is_paused
    
    if not _current_job_id:
        raise HTTPException(status_code=400, detail="No ingestion job to resume")
    
    if not _pause_requested and not _is_paused:
        return SuccessResponse(success=True, message="Job is not paused")
    
    _pause_requested = False
    
    _ingestion_logs.append({
        "timestamp": datetime.now().isoformat(),
        "level": "INFO",
        "message": "Resuming ingestion",
        "logger": "ingestion"
    })
    
    return SuccessResponse(success=True, message="Ingestion resumed")


@router.post("/stop", response_model=SuccessResponse)
async def stop_ingestion(request: Request):
    """
    Stop the current ingestion job immediately.
    
    The job will be marked as stopped and cannot be resumed.
    """
    global _stop_requested, _pause_requested
    db = request.app.state.db
    
    if not _current_job_id:
        raise HTTPException(status_code=400, detail="No ingestion job is running")
    
    _stop_requested = True
    _pause_requested = False  # Unpause if paused to allow stop
    
    _ingestion_logs.append({
        "timestamp": datetime.now().isoformat(),
        "level": "WARNING",
        "message": "Stop requested - stopping ingestion",
        "logger": "ingestion"
    })
    
    # Update job status in DB
    job = await get_job_from_db(db, _current_job_id)
    if job:
        job["status"] = IngestionStatus.STOPPED
        job["completed_at"] = datetime.now().isoformat()
        await save_job_to_db(db, job)
    
    return SuccessResponse(success=True, message="Ingestion stopped")


@router.get("/logs/stream")
async def stream_logs():
    """
    Stream ingestion logs via Server-Sent Events (SSE).
    
    Returns real-time log entries as they are generated.
    """
    async def event_generator():
        last_sent_index = max(0, len(_ingestion_logs) - 100)  # Start with last 100
        
        while True:
            current_len = len(_ingestion_logs)
            
            # Send new logs since last sent
            if current_len > last_sent_index:
                logs_list = list(_ingestion_logs)
                new_logs = logs_list[last_sent_index:current_len]
                last_sent_index = current_len
                
                for log in new_logs:
                    import json
                    yield f"data: {json.dumps(log)}\n\n"
            
            # Also send current status
            status_data = {
                "type": "status",
                "is_running": _current_job_id is not None,
                "is_paused": _is_paused,
                "job_id": _current_job_id,
                "total_logs": current_len
            }
            import json
            yield f"data: {json.dumps(status_data)}\n\n"
            
            await asyncio.sleep(0.5)  # Poll every 500ms
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@router.get("/events/stream")
async def stream_events():
    """
    Stream structured ingestion events via Server-Sent Events (SSE).
    
    Returns real-time state updates including:
    - Phase information (initializing, discovering, filtering, processing, etc.)
    - Discovery progress (files found, folders scanned)
    - Processing metrics (rate, ETA)
    - Current job status
    """
    async def event_generator():
        import json
        
        while True:
            # Build state snapshot
            if _current_job_state is not None and _current_job_id is not None:
                # Calculate elapsed and ETA
                started_at = _current_job_state.get("started_at")
                elapsed_seconds = 0.0
                estimated_remaining = None
                
                if started_at:
                    if isinstance(started_at, str):
                        started_at = datetime.fromisoformat(started_at)
                    elapsed_seconds = (datetime.now() - started_at).total_seconds()
                    
                    # Calculate ETA based on progress
                    progress = _current_job_state.get("progress_percent", 0)
                    if progress > 0:
                        total_estimated = elapsed_seconds / (progress / 100)
                        estimated_remaining = max(0, total_estimated - elapsed_seconds)
                
                state = {
                    "type": "state",
                    "is_running": True,
                    "is_paused": _is_paused,
                    "job_id": _current_job_id,
                    "phase": _current_job_state.get("phase"),
                    "phase_message": _current_job_state.get("phase_message"),
                    "status": str(_current_job_state.get("status", "")),
                    "discovery_progress": _current_job_state.get("discovery_progress"),
                    "progress": {
                        "processed_files": _current_job_state.get("processed_files", 0),
                        "total_files": _current_job_state.get("total_files", 0),
                        "progress_percent": _current_job_state.get("progress_percent", 0),
                        "chunks_created": _current_job_state.get("chunks_created", 0),
                        "failed_files": _current_job_state.get("failed_files", 0),
                        "current_file": _current_job_state.get("current_file"),
                    },
                    "metrics": {
                        "processing_rate": _current_job_state.get("processing_rate", 0),
                        "elapsed_seconds": elapsed_seconds,
                        "estimated_remaining_seconds": estimated_remaining
                    },
                    "counts": {
                        "document_count": _current_job_state.get("document_count", 0),
                        "image_count": _current_job_state.get("image_count", 0),
                        "audio_count": _current_job_state.get("audio_count", 0),
                        "video_count": _current_job_state.get("video_count", 0),
                    }
                }
            else:
                state = {
                    "type": "state",
                    "is_running": False,
                    "is_paused": False,
                    "job_id": None,
                    "phase": None,
                    "phase_message": None
                }
            
            yield f"data: {json.dumps(state)}\n\n"
            await asyncio.sleep(0.5)  # Poll every 500ms
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@router.get("/logs")
async def get_ingestion_logs(since: int = 0, limit: int = 1000):
    """
    Get ingestion logs.
    
    Args:
        since: Return logs after this index (for incremental fetching)
        limit: Maximum number of logs to return (default 1000, max 5000)
    
    Returns:
        List of log entries with their index
    """
    limit = min(limit, 5000)  # Cap at 5000 per request
    logs_list = list(_ingestion_logs)
    total = len(logs_list)
    
    # Return logs after 'since' index
    if since > 0 and since < total:
        logs_list = logs_list[since:since + limit]
        start_index = since
    else:
        # Return latest logs
        logs_list = logs_list[-limit:] if len(logs_list) > limit else logs_list
        start_index = max(0, total - limit)
    
    return {
        "logs": logs_list,
        "total": total,
        "start_index": start_index,
        "max_lines": MAX_LOG_LINES
    }


@router.delete("/logs")
async def clear_logs():
    """Clear all ingestion logs."""
    _ingestion_logs.clear()
    return SuccessResponse(success=True, message="Logs cleared")


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    folder: Optional[str] = None,
    search: Optional[str] = None,
    exact_folder: bool = False,
    sort_by: str = "modified",
    sort_order: str = "desc"
):
    """
    List ingested documents.
    
    Returns paginated list of documents in the knowledge base.
    Supports filtering by folder path and search term.
    
    Args:
        page: Page number (1-indexed)
        page_size: Number of documents per page
        folder: Filter by folder path (documents whose source starts with this path)
        search: Search term to filter by title or source
        exact_folder: If True, only return documents directly in folder (not in subfolders)
        sort_by: Field to sort by (name, modified, size, type). Default: modified
        sort_order: Sort direction (asc, desc). Default: desc
    """
    import re
    
    db = request.app.state.db
    collection = db.documents_collection
    
    # Build query filter
    query: dict = {}
    
    # Folder filter
    if folder:
        # Normalize folder path
        folder_normalized = folder.replace("\\", "/")
        
        if exact_folder:
            # Match documents DIRECTLY in this folder (not in subfolders)
            # Pattern: folder/filename (no more slashes after folder)
            query["$or"] = [
                {"source": {"$regex": f"^{re.escape(folder_normalized)}/[^/]+$", "$options": "i"}},
                {"source": {"$regex": f"^{re.escape(folder_normalized.replace('/', chr(92)+chr(92)))}\\\\[^\\\\]+$", "$options": "i"}},
            ]
        else:
            # Match documents in this folder or subfolders (recursive)
            query["$or"] = [
                {"source": {"$regex": f"^{re.escape(folder_normalized)}/", "$options": "i"}},
                {"source": {"$regex": f"^{re.escape(folder_normalized.replace('/', chr(92)+chr(92)))}\\\\", "$options": "i"}},
            ]
    elif exact_folder:
        # Root level - documents with no folder (just filename)
        query["source"] = {"$regex": "^[^/\\\\]+$", "$options": "i"}
    
    # Search filter - match title or source containing search term
    if search:
        search_regex = re.escape(search)
        search_conditions = [
            {"title": {"$regex": search_regex, "$options": "i"}},
            {"source": {"$regex": search_regex, "$options": "i"}}
        ]
        if "$or" in query:
            # Combine with folder filter using $and
            query = {"$and": [{"$or": query["$or"]}, {"$or": search_conditions}]}
        elif "source" in query:
            query = {"$and": [{"source": query["source"]}, {"$or": search_conditions}]}
        else:
            query["$or"] = search_conditions
    
    # Get total count for this query
    if query:
        total = await collection.count_documents(query)
    else:
        # Use estimated count for unfiltered queries (faster)
        total = await collection.estimated_document_count()
    
    # Calculate pagination
    skip = (page - 1) * page_size
    total_pages = max(1, (total + page_size - 1) // page_size)
    
    # Determine sort field and direction
    sort_field_map = {
        "name": "title",
        "modified": "created_at",
        "size": "metadata.chunks_count",
        "type": "source"  # Will sort by extension
    }
    sort_field = sort_field_map.get(sort_by, "created_at")
    sort_direction = -1 if sort_order == "desc" else 1
    
    # Get documents
    cursor = collection.find(query).skip(skip).limit(page_size).sort(sort_field, sort_direction)
    
    documents = []
    async for doc in cursor:
        # Use stored chunks_count from metadata, or 0 if not available
        # -1 means "checked but has 0 chunks" (used internally), display as 0
        # Use the rebuild-metadata endpoint to fix missing counts
        chunks_count = doc.get("metadata", {}).get("chunks_count") or 0
        if chunks_count < 0:
            chunks_count = 0
        
        documents.append(DocumentInfo(
            id=str(doc["_id"]),
            title=doc.get("title", "Untitled"),
            source=doc.get("source", "Unknown"),
            chunks_count=chunks_count,
            created_at=doc.get("created_at"),
            metadata=doc.get("metadata", {})
        ))
    
    return DocumentListResponse(
        documents=documents,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages
    )


@router.get("/documents/folders")
async def get_document_folders(request: Request):
    """
    Get all unique folder paths from documents.
    
    Returns a list of folder paths with document counts for building
    a folder tree in the UI. This is efficient as it only fetches
    the 'source' field and aggregates in MongoDB.
    """
    db = request.app.state.db
    collection = db.documents_collection
    
    try:
        # Use aggregation pipeline to get unique folder paths efficiently
        pipeline = [
            # Only get documents with source field
            {"$match": {"source": {"$exists": True, "$ne": None}}},
            # Project only the source field
            {"$project": {"source": 1}},
        ]
        
        # Execute aggregation
        cursor = collection.aggregate(pipeline)
        
        # Build folder structure with counts
        folder_counts: dict = {}
        total_documents = 0
        
        async for doc in cursor:
            source = doc.get("source", "")
            if not source:
                continue
                
            total_documents += 1
            
            # Normalize path separators
            source = source.replace("\\", "/")
            parts = source.split("/")
            
            # Build folder path hierarchy
            current_path = ""
            for i, part in enumerate(parts[:-1]):  # Exclude filename
                current_path = f"{current_path}/{part}" if current_path else part
                if current_path not in folder_counts:
                    folder_counts[current_path] = {
                        "path": current_path,
                        "name": part,
                        "depth": i,
                        "count": 0
                    }
                folder_counts[current_path]["count"] += 1
        
        # Convert to list and sort by path
        folders = sorted(folder_counts.values(), key=lambda x: x["path"])
        
        return {
            "folders": folders,
            "total_folders": len(folders),
            "total_documents": total_documents
        }
        
    except Exception as e:
        logger.error(f"Error getting document folders: {e}")
        return {
            "folders": [],
            "total_folders": 0,
            "total_documents": 0,
            "error": str(e)
        }


@router.get("/documents/lookup")
async def find_document_by_source(request: Request, source: str):
    """
    Find a document by its source path.
    
    This performs a flexible search matching source field or title.
    Returns the document ID if found, null otherwise.
    
    NOTE: This endpoint MUST be defined before /documents/{document_id}
    to avoid path parameter matching "lookup" as a document_id.
    """
    db = request.app.state.db
    
    # Try exact match on source first
    doc = await db.documents_collection.find_one({"source": source})
    
    if not doc:
        # Try title match
        doc = await db.documents_collection.find_one({"title": source})
    
    if not doc:
        # Try partial match on source (filename only)
        import re
        filename = source.split('/')[-1].split('\\')[-1]  # Get filename from path
        regex_pattern = re.escape(filename)
        doc = await db.documents_collection.find_one({
            "$or": [
                {"source": {"$regex": regex_pattern, "$options": "i"}},
                {"title": {"$regex": regex_pattern, "$options": "i"}}
            ]
        })
    
    if not doc:
        return {"found": False, "document": None}
    
    return {
        "found": True,
        "document": {
            "id": str(doc["_id"]),
            "title": doc.get("title", "Untitled"),
            "source": doc.get("source", "Unknown")
        }
    }


@router.get("/documents/{document_id}")
async def get_document(request: Request, document_id: str):
    """Get a specific document by ID."""
    db = request.app.state.db
    
    try:
        doc = await db.documents_collection.find_one({"_id": ObjectId(document_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID format")
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Get chunks
    chunks_cursor = db.chunks_collection.find({"document_id": ObjectId(document_id)})
    chunks = []
    async for chunk in chunks_cursor:
        chunks.append({
            "id": str(chunk["_id"]),
            "content": chunk["content"],
            "chunk_index": chunk.get("chunk_index", 0),
            "metadata": chunk.get("metadata", {})
        })
    
    return {
        "id": str(doc["_id"]),
        "title": doc.get("title", "Untitled"),
        "source": doc.get("source", "Unknown"),
        "content": doc.get("content", ""),
        "created_at": doc.get("created_at"),
        "metadata": doc.get("metadata", {}),
        "chunks": chunks,
        "chunks_count": len(chunks)
    }


@router.delete("/documents/{document_id}", response_model=SuccessResponse)
async def delete_document(request: Request, document_id: str):
    """Delete a document and its chunks."""
    db = request.app.state.db
    
    try:
        obj_id = ObjectId(document_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID format")
    
    # Check if document exists
    doc = await db.documents_collection.find_one({"_id": obj_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Delete chunks first
    chunks_result = await db.chunks_collection.delete_many({"document_id": obj_id})
    
    # Delete document
    doc_result = await db.documents_collection.delete_one({"_id": obj_id})
    
    return SuccessResponse(
        success=True,
        message=f"Deleted document and {chunks_result.deleted_count} chunks"
    )


@router.post("/documents/{document_id}/open-explorer")
async def open_in_explorer(request: Request, document_id: str):
    """
    Open the document's folder in OS file explorer.
    
    For local execution: Opens the file in the OS file explorer.
    For Docker: Returns the container path (cannot open explorer from container).
    """
    import subprocess
    import platform
    
    db = request.app.state.db
    
    try:
        doc = await db.documents_collection.find_one({"_id": ObjectId(document_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID format")
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Get the source path from metadata or document source
    file_path = doc.get("metadata", {}).get("file_path", "")
    source = doc.get("source", "")
    
    if not file_path:
        # Try to reconstruct from source and profile settings
        return {
            "success": False,
            "message": "File path not available in document metadata",
            "source": source
        }
    
    # Check if we're running in Docker (container path or .dockerenv exists)
    is_docker = file_path.startswith("/app/mounts/") or os.path.exists("/.dockerenv")
    
    if is_docker:
        # Running in Docker - cannot open file explorer, return path for manual navigation
        # The file exists in the container but we can't open explorer from there
        return {
            "success": False,
            "message": "Cannot open explorer from Docker container. Copy the path below to navigate manually.",
            "file_path": file_path,
            "is_docker": True
        }
    
    # Check if file exists (only for non-Docker, where paths are local)
    if not os.path.exists(file_path):
        return {
            "success": False,
            "message": f"File not found at: {file_path}",
            "file_path": file_path
        }
    
    try:
        folder_path = os.path.dirname(file_path)
        system = platform.system()
        
        if system == "Windows":
            # Open folder and select the file
            subprocess.Popen(['explorer', '/select,', file_path])
        elif system == "Darwin":  # macOS
            subprocess.Popen(['open', '-R', file_path])
        else:  # Linux (with GUI - non-Docker Linux)
            subprocess.Popen(['xdg-open', folder_path])
        
        return {
            "success": True,
            "message": "Opened in file explorer",
            "file_path": file_path
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Failed to open explorer: {str(e)}",
            "file_path": file_path
        }


@router.get("/documents/{document_id}/file")
async def get_document_file(request: Request, document_id: str):
    """
    Serve the document file for browser preview.
    
    Returns the file content with appropriate content type for browser display.
    Supports: PDF, images, text files, HTML, markdown.
    """
    from fastapi.responses import FileResponse, Response
    import mimetypes
    
    db = request.app.state.db
    
    try:
        doc = await db.documents_collection.find_one({"_id": ObjectId(document_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID format")
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    file_path = doc.get("metadata", {}).get("file_path", "")
    
    if not file_path or not os.path.exists(file_path):
        # Return the stored content as fallback
        content = doc.get("content", "")
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="{doc.get("title", "document")}.txt"'}
        )
    
    # Get MIME type
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type is None:
        mime_type = "application/octet-stream"
    
    # For certain file types, return as FileResponse for direct viewing
    viewable_types = [
        "application/pdf",
        "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
        "text/plain", "text/html", "text/markdown", "text/css", "text/javascript",
        "application/json", "application/xml"
    ]
    
    filename = os.path.basename(file_path)
    
    if mime_type in viewable_types or mime_type.startswith("text/"):
        return FileResponse(
            path=file_path,
            media_type=mime_type,
            filename=filename,
            headers={"Content-Disposition": f'inline; filename="{filename}"'}
        )
    else:
        # For non-viewable types (Word, Excel, etc.), return download
        return FileResponse(
            path=file_path,
            media_type=mime_type,
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )


@router.get("/documents/{document_id}/info")
async def get_document_full_info(request: Request, document_id: str):
    """
    Get complete document information including all chunks with embeddings metadata.
    
    Returns comprehensive data from both documents and chunks collections.
    """
    db = request.app.state.db
    
    try:
        doc = await db.documents_collection.find_one({"_id": ObjectId(document_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid document ID format")
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Get all chunks with full details
    chunks_cursor = db.chunks_collection.find(
        {"document_id": ObjectId(document_id)}
    ).sort("chunk_index", 1)
    
    chunks = []
    async for chunk in chunks_cursor:
        chunk_data = {
            "id": str(chunk["_id"]),
            "content": chunk["content"],
            "chunk_index": chunk.get("chunk_index", 0),
            "token_count": chunk.get("token_count"),
            "metadata": chunk.get("metadata", {}),
            "created_at": chunk.get("created_at"),
            "has_embedding": "embedding" in chunk and chunk["embedding"] is not None,
            "embedding_dimensions": len(chunk["embedding"]) if chunk.get("embedding") else None
        }
        chunks.append(chunk_data)
    
    file_path = doc.get("metadata", {}).get("file_path", "")
    file_exists = os.path.exists(file_path) if file_path else False
    
    # Get file stats if available
    file_stats = None
    if file_exists:
        stat = os.stat(file_path)
        file_stats = {
            "size_bytes": stat.st_size,
            "modified_time": stat.st_mtime,
            "extension": os.path.splitext(file_path)[1].lower()
        }
    
    return {
        "id": str(doc["_id"]),
        "title": doc.get("title", "Untitled"),
        "source": doc.get("source", "Unknown"),
        "content": doc.get("content", ""),
        "content_length": len(doc.get("content", "")),
        "created_at": doc.get("created_at"),
        "metadata": doc.get("metadata", {}),
        "file_path": file_path,
        "file_exists": file_exists,
        "file_stats": file_stats,
        "chunks": chunks,
        "chunks_count": len(chunks),
        "total_tokens": sum(c.get("token_count") or 0 for c in chunks)
    }


@router.post("/setup-indexes", response_model=SuccessResponse)
async def setup_indexes(request: Request):
    """
    Setup MongoDB search indexes.
    
    Creates vector and text search indexes if they don't exist.
    """
    try:
        from src.setup_indexes import create_indexes
        create_indexes()
        
        return SuccessResponse(
            success=True,
            message="Indexes created successfully"
        )
    except Exception as e:
        logger.error(f"Failed to setup indexes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===== Failed Documents Endpoints =====

@router.get("/failed-documents")
async def get_failed_documents(
    request: Request,
    skip: int = 0,
    limit: int = 50,
    error_type: Optional[str] = None,
    resolved: Optional[bool] = None
):
    """
    Get list of failed documents from ingestion.
    
    Args:
        skip: Number of documents to skip (pagination)
        limit: Maximum documents to return
        error_type: Filter by error type (timeout, error)
        resolved: Filter by resolved status
    """
    db = request.app.state.db.db
    
    # Build query filter
    query = {}
    if error_type:
        query["error_type"] = error_type
    if resolved is not None:
        query["resolved"] = resolved
    
    # Get total count
    total = await db["failed_documents"].count_documents(query)
    
    # Get documents with pagination
    cursor = db["failed_documents"].find(query).sort("failed_at", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit)
    
    # Convert ObjectId to string
    for doc in docs:
        doc["_id"] = str(doc["_id"])
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "documents": docs
    }


@router.get("/failed-documents/summary")
async def get_failed_documents_summary(request: Request):
    """Get summary statistics of failed documents."""
    db = request.app.state.db.db
    
    pipeline = [
        {
            "$group": {
                "_id": "$error_type",
                "count": {"$sum": 1},
                "total_size_bytes": {"$sum": "$file_size_bytes"},
                "avg_processing_time_ms": {"$avg": "$processing_time_ms"}
            }
        }
    ]
    
    results = await db["failed_documents"].aggregate(pipeline).to_list(length=100)
    
    # Get counts
    total_unresolved = await db["failed_documents"].count_documents({"resolved": False})
    total_resolved = await db["failed_documents"].count_documents({"resolved": True})
    
    return {
        "by_error_type": {r["_id"]: r for r in results},
        "total_unresolved": total_unresolved,
        "total_resolved": total_resolved,
        "total": total_unresolved + total_resolved
    }


@router.post("/failed-documents/{doc_id}/resolve")
async def resolve_failed_document(request: Request, doc_id: str):
    """Mark a failed document as resolved (manually handled)."""
    db = request.app.state.db.db
    
    result = await db["failed_documents"].update_one(
        {"_id": ObjectId(doc_id)},
        {"$set": {"resolved": True, "resolved_at": datetime.now().isoformat()}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Failed document not found")
    
    return {"success": True, "message": "Document marked as resolved"}


@router.delete("/failed-documents/{doc_id}")
async def delete_failed_document(request: Request, doc_id: str):
    """Delete a failed document record."""
    db = request.app.state.db.db
    
    result = await db["failed_documents"].delete_one({"_id": ObjectId(doc_id)})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Failed document not found")
    
    return {"success": True, "message": "Failed document record deleted"}


@router.delete("/failed-documents")
async def clear_failed_documents(
    request: Request,
    resolved_only: bool = True
):
    """Clear failed document records."""
    db = request.app.state.db.db
    
    query = {"resolved": True} if resolved_only else {}
    result = await db["failed_documents"].delete_many(query)
    
    return {
        "success": True,
        "deleted_count": result.deleted_count,
        "message": f"Deleted {result.deleted_count} failed document records"
    }


# ===== Ingestion Analytics Endpoints =====

@router.get("/analytics/overview")
async def get_ingestion_analytics_overview(request: Request):
    """Get overview analytics for ingestion."""
    db = request.app.state.db.db
    
    # Get total stats
    total_files = await db["ingestion_stats"].count_documents({})
    successful = await db["ingestion_stats"].count_documents({"success": True})
    failed = await db["ingestion_stats"].count_documents({"success": False})
    
    # Get processing time stats
    time_pipeline = [
        {"$match": {"success": True}},
        {
            "$group": {
                "_id": None,
                "avg_processing_time_ms": {"$avg": "$processing_time_ms"},
                "min_processing_time_ms": {"$min": "$processing_time_ms"},
                "max_processing_time_ms": {"$max": "$processing_time_ms"},
                "total_processing_time_ms": {"$sum": "$processing_time_ms"},
                "total_chunks": {"$sum": "$chunks_created"},
                "total_size_bytes": {"$sum": "$file_size_bytes"}
            }
        }
    ]
    time_stats = await db["ingestion_stats"].aggregate(time_pipeline).to_list(length=1)
    time_stats = time_stats[0] if time_stats else {}
    
    # Get error type breakdown
    error_pipeline = [
        {"$match": {"success": False}},
        {
            "$group": {
                "_id": "$error_type",
                "count": {"$sum": 1}
            }
        }
    ]
    error_stats = await db["ingestion_stats"].aggregate(error_pipeline).to_list(length=100)
    
    return {
        "total_files_processed": total_files,
        "successful": successful,
        "failed": failed,
        "success_rate": (successful / total_files * 100) if total_files > 0 else 0,
        "avg_processing_time_ms": time_stats.get("avg_processing_time_ms", 0),
        "min_processing_time_ms": time_stats.get("min_processing_time_ms", 0),
        "max_processing_time_ms": time_stats.get("max_processing_time_ms", 0),
        "total_processing_hours": (time_stats.get("total_processing_time_ms", 0) / 1000 / 3600),
        "total_chunks_created": time_stats.get("total_chunks", 0),
        "total_size_gb": (time_stats.get("total_size_bytes", 0) / 1024 / 1024 / 1024),
        "errors_by_type": {e["_id"]: e["count"] for e in error_stats}
    }


@router.get("/analytics/outliers")
async def get_ingestion_outliers(
    request: Request,
    threshold_std: float = 2.0,
    limit: int = 50
):
    """
    Get outlier files - files with unusually long processing times.
    
    Args:
        threshold_std: Standard deviations from mean to consider outlier
        limit: Maximum outliers to return
    """
    db = request.app.state.db.db
    
    # First get mean and stddev of processing times
    stats_pipeline = [
        {"$match": {"success": True}},
        {
            "$group": {
                "_id": None,
                "avg": {"$avg": "$processing_time_ms"},
                "stdDev": {"$stdDevPop": "$processing_time_ms"}
            }
        }
    ]
    stats = await db["ingestion_stats"].aggregate(stats_pipeline).to_list(length=1)
    
    if not stats:
        return {"outliers": [], "threshold_ms": 0, "avg_ms": 0, "std_dev_ms": 0}
    
    avg = stats[0].get("avg", 0)
    std_dev = stats[0].get("stdDev", 0)
    threshold_ms = avg + (threshold_std * std_dev)
    
    # Find outliers
    outliers_cursor = db["ingestion_stats"].find({
        "success": True,
        "processing_time_ms": {"$gt": threshold_ms}
    }).sort("processing_time_ms", -1).limit(limit)
    
    outliers = await outliers_cursor.to_list(length=limit)
    
    # Convert ObjectId to string
    for doc in outliers:
        doc["_id"] = str(doc["_id"])
    
    return {
        "outliers": outliers,
        "threshold_ms": threshold_ms,
        "avg_ms": avg,
        "std_dev_ms": std_dev,
        "threshold_std": threshold_std
    }


@router.get("/analytics/by-extension")
async def get_analytics_by_extension(request: Request):
    """Get processing stats grouped by file extension."""
    db = request.app.state.db.db
    
    pipeline = [
        {
            "$addFields": {
                "extension": {
                    "$toLower": {
                        "$arrayElemAt": [
                            {"$split": ["$file_name", "."]},
                            -1
                        ]
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$extension",
                "count": {"$sum": 1},
                "successful": {"$sum": {"$cond": ["$success", 1, 0]}},
                "failed": {"$sum": {"$cond": ["$success", 0, 1]}},
                "avg_processing_time_ms": {"$avg": "$processing_time_ms"},
                "max_processing_time_ms": {"$max": "$processing_time_ms"},
                "total_chunks": {"$sum": "$chunks_created"},
                "avg_size_bytes": {"$avg": "$file_size_bytes"},
                "total_size_bytes": {"$sum": "$file_size_bytes"}
            }
        },
        {"$sort": {"count": -1}}
    ]
    
    results = await db["ingestion_stats"].aggregate(pipeline).to_list(length=100)
    
    return {
        "by_extension": [
            {
                "extension": r["_id"],
                "count": r["count"],
                "successful": r["successful"],
                "failed": r["failed"],
                "success_rate": (r["successful"] / r["count"] * 100) if r["count"] > 0 else 0,
                "avg_processing_time_ms": r["avg_processing_time_ms"],
                "max_processing_time_ms": r["max_processing_time_ms"],
                "total_chunks": r["total_chunks"],
                "avg_size_mb": r["avg_size_bytes"] / 1024 / 1024 if r["avg_size_bytes"] else 0,
                "total_size_mb": r["total_size_bytes"] / 1024 / 1024 if r["total_size_bytes"] else 0
            }
            for r in results
        ]
    }


@router.get("/analytics/no-chunks")
async def get_no_chunks_analytics(request: Request):
    """
    Get detailed analytics for documents that produced no chunks.
    This helps identify content extraction issues, especially with PDFs and OCR.
    """
    db = request.app.state.db.db
    
    # Get no_chunks failures by extension
    extension_pipeline = [
        {"$match": {"error_type": "no_chunks"}},
        {
            "$addFields": {
                "extension": {
                    "$toLower": {
                        "$arrayElemAt": [
                            {"$split": ["$file_name", "."]},
                            -1
                        ]
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$extension",
                "count": {"$sum": 1},
                "avg_size_bytes": {"$avg": "$file_size_bytes"},
                "total_size_bytes": {"$sum": "$file_size_bytes"},
                "avg_processing_time_ms": {"$avg": "$processing_time_ms"},
                "sample_files": {"$push": {"$substr": ["$file_name", 0, 100]}}
            }
        },
        {"$sort": {"count": -1}}
    ]
    by_extension = await db["ingestion_stats"].aggregate(extension_pipeline).to_list(length=50)
    
    # Get total counts
    total_no_chunks = await db["ingestion_stats"].count_documents({"error_type": "no_chunks"})
    total_successful = await db["ingestion_stats"].count_documents({"success": True})
    total_processed = await db["ingestion_stats"].count_documents({})
    
    # Get recent no_chunks files (last 100)
    recent_pipeline = [
        {"$match": {"error_type": "no_chunks"}},
        {"$sort": {"completed_at": -1}},
        {"$limit": 100},
        {
            "$project": {
                "file_name": 1,
                "file_path": 1,
                "file_size_bytes": 1,
                "processing_time_ms": 1,
                "completed_at": 1,
                "job_id": 1
            }
        }
    ]
    recent_files = await db["ingestion_stats"].aggregate(recent_pipeline).to_list(length=100)
    
    return {
        "summary": {
            "total_no_chunks": total_no_chunks,
            "total_successful": total_successful,
            "total_processed": total_processed,
            "no_chunks_rate": (total_no_chunks / total_processed * 100) if total_processed > 0 else 0
        },
        "by_extension": [
            {
                "extension": r["_id"],
                "count": r["count"],
                "avg_size_mb": r["avg_size_bytes"] / 1024 / 1024 if r["avg_size_bytes"] else 0,
                "total_size_mb": r["total_size_bytes"] / 1024 / 1024 if r["total_size_bytes"] else 0,
                "avg_processing_time_ms": r["avg_processing_time_ms"],
                "sample_files": r["sample_files"][:5]  # First 5 samples
            }
            for r in by_extension
        ],
        "recent_files": [
            {
                "file_name": f.get("file_name"),
                "file_path": f.get("file_path"),
                "size_mb": f.get("file_size_bytes", 0) / 1024 / 1024,
                "processing_time_ms": f.get("processing_time_ms"),
                "completed_at": f.get("completed_at"),
                "job_id": f.get("job_id")
            }
            for f in recent_files
        ]
    }


@router.get("/analytics/timeline")
async def get_analytics_timeline(
    request: Request,
    hours: int = 24
):
    """Get processing timeline for the last N hours."""
    db = request.app.state.db.db
    
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    
    pipeline = [
        {"$match": {"started_at": {"$gte": cutoff}}},
        {
            "$addFields": {
                "hour": {
                    "$substr": ["$started_at", 0, 13]  # Extract YYYY-MM-DDTHH
                }
            }
        },
        {
            "$group": {
                "_id": "$hour",
                "files_processed": {"$sum": 1},
                "successful": {"$sum": {"$cond": ["$success", 1, 0]}},
                "failed": {"$sum": {"$cond": ["$success", 0, 1]}},
                "chunks_created": {"$sum": "$chunks_created"},
                "avg_processing_time_ms": {"$avg": "$processing_time_ms"}
            }
        },
        {"$sort": {"_id": 1}}
    ]
    
    results = await db["ingestion_stats"].aggregate(pipeline).to_list(length=hours + 1)
    
    return {
        "hours": hours,
        "timeline": [
            {
                "hour": r["_id"],
                "files_processed": r["files_processed"],
                "successful": r["successful"],
                "failed": r["failed"],
                "chunks_created": r["chunks_created"],
                "avg_processing_time_ms": r["avg_processing_time_ms"]
            }
            for r in results
        ]
    }


@router.delete("/analytics/clear")
async def clear_analytics_data(request: Request, older_than_days: int = 30):
    """Clear old analytics data."""
    db = request.app.state.db.db
    
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=older_than_days)).isoformat()
    
    result = await db["ingestion_stats"].delete_many({"started_at": {"$lt": cutoff}})
    
    return {
        "success": True,
        "deleted_count": result.deleted_count,
        "message": f"Deleted {result.deleted_count} stats records older than {older_than_days} days"
    }


# =============================================================================
# JOB HISTORY ENDPOINTS
# =============================================================================

@router.get("/jobs")
async def list_jobs(
    request: Request,
    skip: int = 0,
    limit: int = 20,
    profile: Optional[str] = None,
    status: Optional[str] = None
):
    """
    List all ingestion jobs with pagination and filtering.
    
    Returns a summary of each job with key metrics.
    """
    db = request.app.state.db.db
    
    # Build query filter
    query = {}
    if profile:
        query["profile"] = profile
    if status:
        query["status"] = status.lower()
    
    # Get total count
    total = await db[INGESTION_JOBS_COLLECTION].count_documents(query)
    
    # Get jobs with pagination, sorted by started_at descending
    cursor = db[INGESTION_JOBS_COLLECTION].find(query).sort("started_at", -1).skip(skip).limit(limit)
    jobs = await cursor.to_list(length=limit)
    
    # Transform for response
    job_list = []
    for job in jobs:
        job_id = job.pop("_id", job.get("job_id"))
        job["job_id"] = job_id
        
        # Calculate duration if completed
        started_at = job.get("started_at")
        completed_at = job.get("completed_at")
        duration_seconds = None
        if started_at and completed_at:
            try:
                start = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                end = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
                if start.tzinfo:
                    start = start.replace(tzinfo=None)
                if end.tzinfo:
                    end = end.replace(tzinfo=None)
                duration_seconds = (end - start).total_seconds()
            except:
                pass
        
        # Flatten progress if nested
        progress = job.pop("progress", None)
        if progress:
            job["total_files"] = progress.get("total_files", job.get("total_files", 0))
            job["processed_files"] = progress.get("processed_files", job.get("processed_files", 0))
            job["failed_files"] = progress.get("failed_files", job.get("failed_files", 0))
            job["chunks_created"] = progress.get("chunks_created", job.get("chunks_created", 0))
            job["duplicates_skipped"] = progress.get("duplicates_skipped", job.get("duplicates_skipped", 0))
        
        # Count related stats and failed docs
        stats_count = await db["ingestion_stats"].count_documents({"job_id": job_id})
        failed_count = await db["failed_documents"].count_documents({"job_id": job_id})
        logs_count = len(job.get("logs", []))
        
        job_list.append({
            "job_id": job_id,
            "profile": job.get("profile"),
            "status": job.get("status", "unknown").lower() if isinstance(job.get("status"), str) else str(job.get("status", "unknown")),
            "phase": job.get("phase"),
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_seconds": duration_seconds,
            "total_files": job.get("total_files", 0),
            "processed_files": job.get("processed_files", 0),
            "failed_files": job.get("failed_files", 0),
            "chunks_created": job.get("chunks_created", 0),
            "duplicates_skipped": job.get("duplicates_skipped", 0),
            "stats_count": stats_count,
            "failed_count": failed_count,
            "logs_count": logs_count,
            "errors": job.get("errors", [])[:3]  # First 3 errors as preview
        })
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "jobs": job_list
    }


@router.get("/jobs/{job_id}")
async def get_job_details(request: Request, job_id: str):
    """
    Get full details for a specific job including all metadata.
    """
    db = request.app.state.db.db
    
    job = await db[INGESTION_JOBS_COLLECTION].find_one({"_id": job_id})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job["job_id"] = job.pop("_id")
    
    # Flatten progress if nested
    progress = job.pop("progress", None)
    if progress:
        for key, value in progress.items():
            if key not in job:
                job[key] = value
    
    # Get aggregated stats
    stats_pipeline = [
        {"$match": {"job_id": job_id}},
        {"$group": {
            "_id": None,
            "total_files": {"$sum": 1},
            "successful": {"$sum": {"$cond": ["$success", 1, 0]}},
            "failed": {"$sum": {"$cond": ["$success", 0, 1]}},
            "total_processing_time_ms": {"$sum": "$processing_time_ms"},
            "total_chunks": {"$sum": "$chunks_created"},
            "total_size_bytes": {"$sum": "$file_size_bytes"},
            "avg_processing_time_ms": {"$avg": "$processing_time_ms"}
        }}
    ]
    stats = await db["ingestion_stats"].aggregate(stats_pipeline).to_list(length=1)
    
    # Get failed documents count by error type
    failed_pipeline = [
        {"$match": {"job_id": job_id}},
        {"$group": {
            "_id": "$error_type",
            "count": {"$sum": 1}
        }}
    ]
    failed_by_type = await db["failed_documents"].aggregate(failed_pipeline).to_list(length=10)
    
    return {
        "job": job,
        "stats_summary": stats[0] if stats else None,
        "failed_by_type": {r["_id"]: r["count"] for r in failed_by_type}
    }


@router.get("/jobs/{job_id}/stats")
async def get_job_stats(
    request: Request,
    job_id: str,
    skip: int = 0,
    limit: int = 100,
    success: Optional[bool] = None
):
    """
    Get per-file stats for a specific job with pagination.
    """
    db = request.app.state.db.db
    
    # Build query
    query = {"job_id": job_id}
    if success is not None:
        query["success"] = success
    
    # Get total count
    total = await db["ingestion_stats"].count_documents(query)
    
    # Get stats
    cursor = db["ingestion_stats"].find(query).sort("started_at", -1).skip(skip).limit(limit)
    stats = await cursor.to_list(length=limit)
    
    # Convert ObjectIds to strings
    for stat in stats:
        if "_id" in stat:
            stat["_id"] = str(stat["_id"])
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "stats": stats
    }


@router.get("/jobs/{job_id}/logs")
async def get_job_logs(request: Request, job_id: str):
    """
    Get logs for a specific job.
    """
    db = request.app.state.db.db
    
    job = await db[INGESTION_JOBS_COLLECTION].find_one({"_id": job_id}, {"logs": 1})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return {
        "job_id": job_id,
        "logs": job.get("logs", [])
    }


@router.get("/jobs/{job_id}/failed")
async def get_job_failed_documents(
    request: Request,
    job_id: str,
    skip: int = 0,
    limit: int = 50,
    error_type: Optional[str] = None
):
    """
    Get failed documents for a specific job.
    """
    db = request.app.state.db.db
    
    # Build query
    query = {"job_id": job_id}
    if error_type:
        query["error_type"] = error_type
    
    # Get total count
    total = await db["failed_documents"].count_documents(query)
    
    # Get failed docs
    cursor = db["failed_documents"].find(query).sort("failed_at", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit)
    
    # Convert ObjectIds to strings
    for doc in docs:
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
    
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "failed_documents": docs
    }


@router.delete("/jobs/{job_id}")
async def delete_job(request: Request, job_id: str):
    """
    Delete a job and all its related data (stats, failed documents, logs).
    """
    db = request.app.state.db.db
    
    # Check job exists
    job = await db[INGESTION_JOBS_COLLECTION].find_one({"_id": job_id})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Delete related data
    stats_result = await db["ingestion_stats"].delete_many({"job_id": job_id})
    failed_result = await db["failed_documents"].delete_many({"job_id": job_id})
    job_result = await db[INGESTION_JOBS_COLLECTION].delete_one({"_id": job_id})
    
    return {
        "success": True,
        "deleted": {
            "job": job_result.deleted_count,
            "stats": stats_result.deleted_count,
            "failed_documents": failed_result.deleted_count
        }
    }


@router.get("/jobs/stats/summary")
async def get_jobs_summary(request: Request):
    """
    Get overall summary statistics for all jobs.
    """
    db = request.app.state.db.db
    
    # Get job counts by status
    status_pipeline = [
        {"$group": {
            "_id": "$status",
            "count": {"$sum": 1}
        }}
    ]
    status_counts = await db[INGESTION_JOBS_COLLECTION].aggregate(status_pipeline).to_list(length=20)
    
    # Get totals
    total_jobs = await db[INGESTION_JOBS_COLLECTION].count_documents({})
    
    # Get overall processing stats
    stats_pipeline = [
        {"$group": {
            "_id": None,
            "total_files_processed": {"$sum": 1},
            "successful_files": {"$sum": {"$cond": ["$success", 1, 0]}},
            "failed_files": {"$sum": {"$cond": ["$success", 0, 1]}},
            "total_chunks": {"$sum": "$chunks_created"},
            "total_size_bytes": {"$sum": "$file_size_bytes"},
            "avg_processing_time_ms": {"$avg": "$processing_time_ms"}
        }}
    ]
    overall_stats = await db["ingestion_stats"].aggregate(stats_pipeline).to_list(length=1)
    
    # Get recent jobs (last 5)
    recent_jobs = await db[INGESTION_JOBS_COLLECTION].find().sort("started_at", -1).limit(5).to_list(length=5)
    recent = []
    for job in recent_jobs:
        recent.append({
            "job_id": job.get("_id"),
            "profile": job.get("profile"),
            "status": str(job.get("status", "unknown")).lower(),
            "started_at": job.get("started_at")
        })
    
    return {
        "total_jobs": total_jobs,
        "by_status": {str(r["_id"]).lower(): r["count"] for r in status_counts},
        "overall_stats": overall_stats[0] if overall_stats else None,
        "recent_jobs": recent
    }
