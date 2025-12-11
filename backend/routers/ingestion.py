"""Ingestion management router."""

import logging
import asyncio
import uuid
from collections import deque
from datetime import datetime
from typing import Dict, Optional, List
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from bson import ObjectId

from backend.models.schemas import (
    IngestionStartRequest, IngestionStatusResponse, IngestionStatus,
    DocumentInfo, DocumentListResponse, SuccessResponse
)
from backend.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Track ingestion jobs (use Redis in production)
_ingestion_jobs: Dict[str, dict] = {}

# Log buffer with max 50000 lines
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


async def run_ingestion(job_id: str, config: dict):
    """Run ingestion in background."""
    # Clear logs and add handler
    _ingestion_logs.clear()
    
    # Add log handler to capture all ingestion logs
    root_logger = logging.getLogger()
    src_logger = logging.getLogger("src")
    root_logger.addHandler(_log_handler)
    src_logger.addHandler(_log_handler)
    
    try:
        _ingestion_jobs[job_id]["status"] = IngestionStatus.RUNNING
        _ingestion_jobs[job_id]["started_at"] = datetime.now()
        
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
        
        # Track progress
        def progress_callback(current: int, total: int, current_file: str = None):
            _ingestion_jobs[job_id]["processed_files"] = current
            _ingestion_jobs[job_id]["total_files"] = total
            _ingestion_jobs[job_id]["progress_percent"] = (current / total * 100) if total > 0 else 0
            if current_file:
                _ingestion_jobs[job_id]["current_file"] = current_file
                _ingestion_logs.append({
                    "timestamp": datetime.now().isoformat(),
                    "level": "INFO",
                    "message": f"Processing ({current}/{total}): {current_file}",
                    "logger": "ingestion"
                })
        
        # Run ingestion
        results = await pipeline.ingest_documents(
            progress_callback=progress_callback,
            incremental=config.get("incremental", True)
        )
        
        # Update job status
        _ingestion_jobs[job_id]["status"] = IngestionStatus.COMPLETED
        _ingestion_jobs[job_id]["completed_at"] = datetime.now()
        _ingestion_jobs[job_id]["processed_files"] = len(results)
        _ingestion_jobs[job_id]["chunks_created"] = sum(r.chunks_created for r in results)
        _ingestion_jobs[job_id]["failed_files"] = sum(1 for r in results if r.errors)
        _ingestion_jobs[job_id]["errors"] = [
            err for r in results for err in r.errors
        ][:20]  # Limit errors stored
        _ingestion_jobs[job_id]["progress_percent"] = 100.0
        
        _ingestion_logs.append({
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": f"Ingestion completed. Files: {len(results)}, Chunks: {_ingestion_jobs[job_id]['chunks_created']}",
            "logger": "ingestion"
        })
        
    except Exception as e:
        logger.error(f"Ingestion job {job_id} failed: {e}")
        _ingestion_jobs[job_id]["status"] = IngestionStatus.FAILED
        _ingestion_jobs[job_id]["errors"] = [str(e)]
        _ingestion_jobs[job_id]["completed_at"] = datetime.now()
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


@router.post("/start", response_model=IngestionStatusResponse)
async def start_ingestion(
    request: IngestionStartRequest,
    background_tasks: BackgroundTasks
):
    """
    Start document ingestion.
    
    Initiates a background ingestion job for documents in the configured folder.
    """
    # Check if another job is running
    for job_id, job in _ingestion_jobs.items():
        if job["status"] == IngestionStatus.RUNNING:
            raise HTTPException(
                status_code=409,
                detail=f"Ingestion job {job_id} is already running"
            )
    
    # Create new job
    job_id = str(uuid.uuid4())
    _ingestion_jobs[job_id] = {
        "status": IngestionStatus.PENDING,
        "job_id": job_id,
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
    
    # Start background task
    config = request.model_dump()
    background_tasks.add_task(run_ingestion, job_id, config)
    
    return IngestionStatusResponse(**_ingestion_jobs[job_id])


@router.get("/status", response_model=IngestionStatusResponse)
async def get_ingestion_status():
    """
    Get current ingestion status.
    
    Returns the status of the most recent or running ingestion job.
    """
    if not _ingestion_jobs:
        return IngestionStatusResponse(
            status=IngestionStatus.COMPLETED,
            job_id=None,
            total_files=0,
            processed_files=0,
            progress_percent=0.0,
            elapsed_seconds=0.0
        )
    
    # Get most recent job
    latest_job = max(
        _ingestion_jobs.values(),
        key=lambda j: j.get("started_at") or datetime.min
    )
    
    # Calculate elapsed time and ETA
    elapsed_seconds = 0.0
    estimated_remaining = None
    
    if latest_job.get("started_at"):
        if latest_job.get("completed_at"):
            elapsed_seconds = (latest_job["completed_at"] - latest_job["started_at"]).total_seconds()
        else:
            elapsed_seconds = (datetime.now() - latest_job["started_at"]).total_seconds()
        
        # Calculate ETA if running
        if latest_job["status"] == IngestionStatus.RUNNING:
            progress = latest_job.get("progress_percent", 0)
            if progress > 0:
                total_estimated = elapsed_seconds / (progress / 100)
                estimated_remaining = max(0, total_estimated - elapsed_seconds)
    
    return IngestionStatusResponse(
        **{k: v for k, v in latest_job.items() if k not in ["elapsed_seconds", "estimated_remaining_seconds"]},
        elapsed_seconds=elapsed_seconds,
        estimated_remaining_seconds=estimated_remaining
    )


@router.get("/status/{job_id}", response_model=IngestionStatusResponse)
async def get_job_status(job_id: str):
    """Get status of a specific ingestion job."""
    if job_id not in _ingestion_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return IngestionStatusResponse(**_ingestion_jobs[job_id])


@router.post("/cancel/{job_id}", response_model=SuccessResponse)
async def cancel_ingestion(job_id: str):
    """
    Cancel a running ingestion job.
    
    Note: This marks the job as cancelled but may not stop immediately.
    """
    if job_id not in _ingestion_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = _ingestion_jobs[job_id]
    
    if job["status"] != IngestionStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Job is not running (status: {job['status']})"
        )
    
    job["status"] = IngestionStatus.CANCELLED
    job["completed_at"] = datetime.now()
    
    return SuccessResponse(success=True, message="Ingestion cancelled")


@router.get("/jobs")
async def list_ingestion_jobs():
    """List all ingestion jobs."""
    return {
        "jobs": [
            {
                "job_id": job["job_id"],
                "status": job["status"],
                "started_at": job["started_at"],
                "completed_at": job["completed_at"],
                "progress_percent": job["progress_percent"]
            }
            for job in _ingestion_jobs.values()
        ]
    }


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
