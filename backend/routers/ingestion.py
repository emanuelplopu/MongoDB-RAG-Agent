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
    IngestionRunSummary, IngestionRunsResponse
)
from backend.core.config import settings
from backend.routers.auth import require_admin, UserResponse
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
    return doc


async def get_latest_job_from_db(db) -> Optional[dict]:
    """Get the most recent job from the database."""
    collection = await get_jobs_collection(db)
    cursor = collection.find().sort("started_at", -1).limit(1)
    async for doc in cursor:
        doc["job_id"] = doc.pop("_id")
        return doc
    return None


async def get_running_job_from_db(db) -> Optional[dict]:
    """Get any running or interrupted job from the database."""
    collection = await get_jobs_collection(db)
    # Look for RUNNING or INTERRUPTED status
    doc = await collection.find_one({
        "status": {"$in": [IngestionStatus.RUNNING, "INTERRUPTED"]}
    })
    if doc:
        doc["job_id"] = doc.pop("_id")
    return doc


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


async def run_ingestion(job_id: str, config: dict, db):
    """Run ingestion in background with DB persistence."""
    global _current_job_id, _shutdown_requested, _pause_requested, _stop_requested, _is_paused, _current_job_state
    _current_job_id = job_id
    _shutdown_requested = False
    _pause_requested = False
    _stop_requested = False
    _is_paused = False
    
    # Clear logs and add handler
    _ingestion_logs.clear()
    
    # Add log handler to capture all ingestion logs
    root_logger = logging.getLogger()
    src_logger = logging.getLogger("src")
    root_logger.addHandler(_log_handler)
    src_logger.addHandler(_log_handler)
    
    # Helper to update job in memory and DB
    job_state = {
        "job_id": job_id,
        "status": IngestionStatus.RUNNING,
        "started_at": datetime.now().isoformat(),
        "completed_at": None,
        "total_files": 0,
        "processed_files": 0,
        "failed_files": 0,
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
        "profile": config.get("profile")
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
        
        # Create pipeline
        pipeline = DocumentIngestionPipeline(
            config=ing_config,
            documents_folder=config.get("documents_folder"),
            clean_before_ingest=config.get("clean_before_ingest", False),
            use_profile=True
        )
        
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
            
            # Accumulate chunks incrementally
            if chunks_in_file > 0:
                job_state["chunks_created"] = job_state.get("chunks_created", 0) + chunks_in_file
            
            if current_file:
                job_state["current_file"] = current_file
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
        
        # Run ingestion
        results = await pipeline.ingest_documents(
            progress_callback=progress_callback,
            incremental=config.get("incremental", True)
        )
        
        # Update job status to completed
        await update_job_state(
            status=IngestionStatus.COMPLETED,
            completed_at=datetime.now().isoformat(),
            processed_files=len(results),
            chunks_created=sum(r.chunks_created for r in results),
            failed_files=sum(1 for r in results if r.errors),
            errors=[err for r in results for err in r.errors][:20],
            progress_percent=100.0
        )
        
        _ingestion_logs.append({
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": f"Ingestion completed. Files: {len(results)}, Chunks: {job_state['chunks_created']}",
            "logger": "ingestion"
        })
        
    except asyncio.CancelledError:
        logger.warning(f"Ingestion job {job_id} was cancelled/interrupted")
        await update_job_state(
            status="INTERRUPTED",
            interrupted_at=datetime.now().isoformat()
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
            errors=[str(e)]
        )
        _ingestion_logs.append({
            "timestamp": datetime.now().isoformat(),
            "level": "ERROR",
            "message": f"Ingestion failed: {str(e)}",
            "logger": "ingestion"
        })
    finally:
        # Remove log handler
        root_logger.removeHandler(_log_handler)
        src_logger.removeHandler(_log_handler)
        _current_job_id = None
        _current_job_state = None


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
        # Parse datetime if string
        if isinstance(started_at, str):
            started_at = datetime.fromisoformat(started_at)
        if completed_at:
            if isinstance(completed_at, str):
                completed_at = datetime.fromisoformat(completed_at)
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
                     "is_paused", "can_pause", "can_stop"]
    }
    
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
    
    # Get total count
    total = await collection.count_documents({})
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
            profile=doc.get("profile")
        ))
    
    return IngestionRunsResponse(
        runs=runs,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages
    )


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
    page_size: int = 20
):
    """
    List ingested documents.
    
    Returns paginated list of documents in the knowledge base.
    """
    db = request.app.state.db
    collection = db.documents_collection
    
    # Get total count
    total = await collection.count_documents({})
    
    # Calculate pagination
    skip = (page - 1) * page_size
    total_pages = (total + page_size - 1) // page_size
    
    # Get documents
    cursor = collection.find({}).skip(skip).limit(page_size).sort("created_at", -1)
    
    documents = []
    async for doc in cursor:
        # Count chunks for this document
        chunks_count = await db.chunks_collection.count_documents({
            "document_id": doc["_id"]
        })
        
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
    
    This only works when the backend is running locally (not in Docker).
    For Docker, it returns the file path for the user to navigate manually.
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
    
    # Check if file exists
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
        else:  # Linux
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
