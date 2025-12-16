"""
Cloud Source Sync Router

Manages sync configurations and sync job execution for cloud sources.
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Depends, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from bson import ObjectId
import json

from backend.routers.cloud_sources.schemas import (
    SyncJobStatus,
    SyncJobType,
    SyncFrequency,
    SyncConfigCreateRequest,
    SyncConfigUpdateRequest,
    SyncConfigResponse,
    SyncConfigListResponse,
    SyncConfigStats,
    SyncJobRunRequest,
    SyncJobResponse,
    SyncJobListResponse,
    SyncJobProgress,
    SyncJobError,
    DashboardResponse,
    SourceSummary,
    SuccessResponse,
    SourcePath,
    SyncFilters,
    SyncSchedule,
    ConnectionStatus,
)
from backend.routers.cloud_sources.connections import get_connections_collection
from backend.routers.auth import require_auth, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cloud-sources-sync"])

# Collection names
SYNC_CONFIGS_COLLECTION = "cloud_source_sync_configs"
SYNC_JOBS_COLLECTION = "cloud_source_sync_jobs"

# Active jobs tracking
_active_jobs: dict = {}  # job_id -> job state


async def get_sync_configs_collection(request: Request):
    """Get the sync configs collection."""
    return request.app.state.db.db[SYNC_CONFIGS_COLLECTION]


async def get_sync_jobs_collection(request: Request):
    """Get the sync jobs collection."""
    return request.app.state.db.db[SYNC_JOBS_COLLECTION]


def calculate_next_run(schedule: SyncSchedule, from_time: Optional[datetime] = None) -> Optional[datetime]:
    """Calculate the next scheduled run time."""
    if not schedule.enabled:
        return None
    
    now = from_time or datetime.utcnow()
    
    if schedule.frequency == SyncFrequency.HOURLY:
        # Next hour at minute 0
        next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    elif schedule.frequency == SyncFrequency.DAILY:
        # Next day at specified hour
        next_run = now.replace(hour=schedule.hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
    elif schedule.frequency == SyncFrequency.WEEKLY:
        # Next week on specified day at specified hour
        days_ahead = (schedule.day_of_week or 0) - now.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        next_run = now.replace(hour=schedule.hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    elif schedule.frequency == SyncFrequency.MONTHLY:
        # Next month on specified day at specified hour
        next_run = now.replace(
            day=min(schedule.day_of_month or 1, 28),
            hour=schedule.hour,
            minute=0,
            second=0,
            microsecond=0
        )
        if next_run <= now:
            if now.month == 12:
                next_run = next_run.replace(year=now.year + 1, month=1)
            else:
                next_run = next_run.replace(month=now.month + 1)
    else:
        return None
    
    return next_run


def sync_config_doc_to_response(doc: dict, connection_doc: Optional[dict] = None) -> SyncConfigResponse:
    """Convert a MongoDB document to a SyncConfigResponse."""
    return SyncConfigResponse(
        id=str(doc["_id"]),
        user_id=str(doc["user_id"]),
        connection_id=str(doc["connection_id"]),
        connection_display_name=connection_doc.get("display_name", "Unknown") if connection_doc else doc.get("connection_display_name", "Unknown"),
        provider=connection_doc.get("provider") if connection_doc else doc.get("provider"),
        profile_key=doc["profile_key"],
        name=doc["name"],
        source_paths=[SourcePath(**p) for p in doc.get("source_paths", [])],
        filters=SyncFilters(**doc.get("filters", {})),
        schedule=SyncSchedule(**doc.get("schedule", {})),
        delete_removed=doc.get("delete_removed", True),
        status=doc.get("status", "active"),
        stats=SyncConfigStats(**doc.get("stats", {})),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


# ==================== Sync Configuration Endpoints ====================

@router.get("/sync-configs", response_model=SyncConfigListResponse)
async def list_sync_configs(
    request: Request,
    connection_id: Optional[str] = None,
    user: UserResponse = Depends(require_auth)
):
    """List all sync configurations for the user."""
    collection = await get_sync_configs_collection(request)
    connections_collection = await get_connections_collection(request)
    
    # Build query
    query = {"user_id": user.id}
    if connection_id:
        query["connection_id"] = connection_id
    
    # Fetch configs
    configs = []
    async for doc in collection.find(query).sort("created_at", -1):
        # Get connection info
        connection_doc = await connections_collection.find_one(
            {"_id": ObjectId(doc["connection_id"])}
        )
        configs.append(sync_config_doc_to_response(doc, connection_doc))
    
    return SyncConfigListResponse(
        configs=configs,
        total=len(configs)
    )


@router.post("/sync-configs", response_model=SyncConfigResponse)
async def create_sync_config(
    request: Request,
    config_request: SyncConfigCreateRequest,
    user: UserResponse = Depends(require_auth)
):
    """Create a new sync configuration."""
    collection = await get_sync_configs_collection(request)
    connections_collection = await get_connections_collection(request)
    
    # Verify connection belongs to user
    try:
        connection_doc = await connections_collection.find_one({
            "_id": ObjectId(config_request.connection_id),
            "user_id": user.id
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid connection ID")
    
    if not connection_doc:
        raise HTTPException(status_code=404, detail="Connection not found")
    
    if connection_doc.get("status") != ConnectionStatus.ACTIVE.value:
        raise HTTPException(status_code=400, detail="Connection is not active")
    
    now = datetime.utcnow()
    next_run = calculate_next_run(config_request.schedule)
    
    doc = {
        "user_id": user.id,
        "connection_id": config_request.connection_id,
        "profile_key": config_request.profile_key,
        "name": config_request.name,
        "source_paths": [p.model_dump() for p in config_request.source_paths],
        "filters": config_request.filters.model_dump(),
        "schedule": config_request.schedule.model_dump(),
        "delete_removed": config_request.delete_removed,
        "status": "active",
        "stats": {
            "total_files": 0,
            "total_size_bytes": 0,
            "next_scheduled_run": next_run,
        },
        "created_at": now,
        "updated_at": now,
    }
    
    result = await collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    
    return sync_config_doc_to_response(doc, connection_doc)


@router.get("/sync-configs/{config_id}", response_model=SyncConfigResponse)
async def get_sync_config(
    request: Request,
    config_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Get a specific sync configuration."""
    collection = await get_sync_configs_collection(request)
    connections_collection = await get_connections_collection(request)
    
    try:
        doc = await collection.find_one({
            "_id": ObjectId(config_id),
            "user_id": user.id
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid config ID")
    
    if not doc:
        raise HTTPException(status_code=404, detail="Sync configuration not found")
    
    connection_doc = await connections_collection.find_one(
        {"_id": ObjectId(doc["connection_id"])}
    )
    
    return sync_config_doc_to_response(doc, connection_doc)


@router.put("/sync-configs/{config_id}", response_model=SyncConfigResponse)
async def update_sync_config(
    request: Request,
    config_id: str,
    update_request: SyncConfigUpdateRequest,
    user: UserResponse = Depends(require_auth)
):
    """Update a sync configuration."""
    collection = await get_sync_configs_collection(request)
    connections_collection = await get_connections_collection(request)
    
    try:
        doc = await collection.find_one({
            "_id": ObjectId(config_id),
            "user_id": user.id
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid config ID")
    
    if not doc:
        raise HTTPException(status_code=404, detail="Sync configuration not found")
    
    # Build update
    update = {"updated_at": datetime.utcnow()}
    
    if update_request.name is not None:
        update["name"] = update_request.name
    if update_request.source_paths is not None:
        update["source_paths"] = [p.model_dump() for p in update_request.source_paths]
    if update_request.filters is not None:
        update["filters"] = update_request.filters.model_dump()
    if update_request.schedule is not None:
        update["schedule"] = update_request.schedule.model_dump()
        update["stats.next_scheduled_run"] = calculate_next_run(update_request.schedule)
    if update_request.delete_removed is not None:
        update["delete_removed"] = update_request.delete_removed
    
    await collection.update_one(
        {"_id": ObjectId(config_id)},
        {"$set": update}
    )
    
    doc = await collection.find_one({"_id": ObjectId(config_id)})
    connection_doc = await connections_collection.find_one(
        {"_id": ObjectId(doc["connection_id"])}
    )
    
    return sync_config_doc_to_response(doc, connection_doc)


@router.delete("/sync-configs/{config_id}", response_model=SuccessResponse)
async def delete_sync_config(
    request: Request,
    config_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Delete a sync configuration."""
    collection = await get_sync_configs_collection(request)
    
    try:
        result = await collection.delete_one({
            "_id": ObjectId(config_id),
            "user_id": user.id
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid config ID")
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Sync configuration not found")
    
    return SuccessResponse(
        success=True,
        message="Sync configuration deleted"
    )


# ==================== Sync Job Endpoints ====================

@router.post("/sync-configs/{config_id}/run", response_model=SyncJobResponse)
async def run_sync(
    request: Request,
    config_id: str,
    run_request: SyncJobRunRequest,
    background_tasks: BackgroundTasks,
    user: UserResponse = Depends(require_auth)
):
    """Trigger a manual sync for a configuration."""
    configs_collection = await get_sync_configs_collection(request)
    jobs_collection = await get_sync_jobs_collection(request)
    connections_collection = await get_connections_collection(request)
    
    try:
        config_doc = await configs_collection.find_one({
            "_id": ObjectId(config_id),
            "user_id": user.id
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid config ID")
    
    if not config_doc:
        raise HTTPException(status_code=404, detail="Sync configuration not found")
    
    # Check for already running job
    running_job = await jobs_collection.find_one({
        "config_id": config_id,
        "status": SyncJobStatus.RUNNING.value
    })
    if running_job:
        raise HTTPException(
            status_code=409,
            detail="A sync job is already running for this configuration"
        )
    
    # Create job
    now = datetime.utcnow()
    job_type = SyncJobType.FULL if run_request.force_full else run_request.type
    
    job_doc = {
        "config_id": config_id,
        "config_name": config_doc["name"],
        "user_id": user.id,
        "connection_id": config_doc["connection_id"],
        "type": job_type.value,
        "status": SyncJobStatus.PENDING.value,
        "progress": {
            "phase": "initializing",
            "files_discovered": 0,
            "files_processed": 0,
            "files_skipped": 0,
            "files_failed": 0,
            "bytes_processed": 0,
        },
        "errors": [],
        "started_at": now,
        "completed_at": None,
    }
    
    result = await jobs_collection.insert_one(job_doc)
    job_id = str(result.inserted_id)
    
    # Start sync in background
    background_tasks.add_task(
        run_sync_job,
        request.app.state.db,
        job_id,
        config_doc,
        job_type
    )
    
    job_doc["_id"] = result.inserted_id
    return sync_job_doc_to_response(job_doc)


async def run_sync_job(db, job_id: str, config_doc: dict, job_type: SyncJobType):
    """Execute a sync job in the background."""
    jobs_collection = db.db[SYNC_JOBS_COLLECTION]
    configs_collection = db.db[SYNC_CONFIGS_COLLECTION]
    
    try:
        # Update status to running
        await jobs_collection.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {"status": SyncJobStatus.RUNNING.value}}
        )
        
        # Track in active jobs
        _active_jobs[job_id] = {
            "status": SyncJobStatus.RUNNING.value,
            "progress": {"phase": "listing"}
        }
        
        # TODO: Actually perform sync using the provider
        # This is a placeholder for the real sync logic:
        # 1. Load connection credentials
        # 2. Initialize provider
        # 3. List files (or get delta)
        # 4. Download and process each file
        # 5. Index into MongoDB
        # 6. Handle deletions if configured
        
        # Simulate sync for now
        total_files = 50
        for i in range(total_files):
            if job_id not in _active_jobs:
                # Job was cancelled
                raise asyncio.CancelledError()
            
            await jobs_collection.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {
                    "progress.phase": "processing",
                    "progress.files_discovered": total_files,
                    "progress.files_processed": i + 1,
                    "progress.current_file": f"document_{i}.pdf",
                }}
            )
            await asyncio.sleep(0.1)  # Simulate processing time
        
        # Complete
        now = datetime.utcnow()
        await jobs_collection.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {
                "status": SyncJobStatus.COMPLETED.value,
                "progress.phase": "completed",
                "completed_at": now,
            }}
        )
        
        # Update config stats
        await configs_collection.update_one(
            {"_id": ObjectId(config_doc["_id"])},
            {"$set": {
                "stats.last_sync_at": now,
                "stats.last_sync_files_processed": total_files,
                "stats.total_files": total_files,
                "updated_at": now,
            }}
        )
        
    except asyncio.CancelledError:
        await jobs_collection.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {
                "status": SyncJobStatus.CANCELLED.value,
                "completed_at": datetime.utcnow(),
            }}
        )
    except Exception as e:
        logger.error(f"Sync job {job_id} failed: {e}")
        await jobs_collection.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {
                "status": SyncJobStatus.FAILED.value,
                "completed_at": datetime.utcnow(),
                "errors": [{"error_type": "sync_error", "message": str(e), "timestamp": datetime.utcnow().isoformat()}],
            }}
        )
    finally:
        _active_jobs.pop(job_id, None)


def sync_job_doc_to_response(doc: dict) -> SyncJobResponse:
    """Convert MongoDB document to SyncJobResponse."""
    started_at = doc.get("started_at")
    completed_at = doc.get("completed_at")
    duration = None
    if started_at and completed_at:
        duration = (completed_at - started_at).total_seconds()
    
    return SyncJobResponse(
        id=str(doc["_id"]),
        config_id=doc["config_id"],
        config_name=doc.get("config_name", "Unknown"),
        user_id=str(doc["user_id"]),
        type=SyncJobType(doc["type"]),
        status=SyncJobStatus(doc["status"]),
        progress=SyncJobProgress(**doc.get("progress", {})),
        errors=[SyncJobError(**e) if isinstance(e, dict) else e for e in doc.get("errors", [])],
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration,
    )


@router.get("/sync-configs/{config_id}/status", response_model=SyncJobResponse)
async def get_sync_status(
    request: Request,
    config_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Get the status of the most recent sync job."""
    jobs_collection = await get_sync_jobs_collection(request)
    
    # Get most recent job
    doc = await jobs_collection.find_one(
        {"config_id": config_id, "user_id": user.id},
        sort=[("started_at", -1)]
    )
    
    if not doc:
        raise HTTPException(status_code=404, detail="No sync jobs found")
    
    return sync_job_doc_to_response(doc)


@router.post("/sync-configs/{config_id}/pause", response_model=SuccessResponse)
async def pause_sync(
    request: Request,
    config_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Pause a running sync job."""
    jobs_collection = await get_sync_jobs_collection(request)
    
    # Find running job
    doc = await jobs_collection.find_one({
        "config_id": config_id,
        "user_id": user.id,
        "status": SyncJobStatus.RUNNING.value
    })
    
    if not doc:
        raise HTTPException(status_code=404, detail="No running sync job found")
    
    job_id = str(doc["_id"])
    
    # Update status
    await jobs_collection.update_one(
        {"_id": doc["_id"]},
        {"$set": {"status": SyncJobStatus.PAUSED.value}}
    )
    
    if job_id in _active_jobs:
        _active_jobs[job_id]["status"] = SyncJobStatus.PAUSED.value
    
    return SuccessResponse(success=True, message="Sync paused")


@router.post("/sync-configs/{config_id}/resume", response_model=SuccessResponse)
async def resume_sync(
    request: Request,
    config_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Resume a paused sync job."""
    jobs_collection = await get_sync_jobs_collection(request)
    
    doc = await jobs_collection.find_one({
        "config_id": config_id,
        "user_id": user.id,
        "status": SyncJobStatus.PAUSED.value
    })
    
    if not doc:
        raise HTTPException(status_code=404, detail="No paused sync job found")
    
    job_id = str(doc["_id"])
    
    await jobs_collection.update_one(
        {"_id": doc["_id"]},
        {"$set": {"status": SyncJobStatus.RUNNING.value}}
    )
    
    if job_id in _active_jobs:
        _active_jobs[job_id]["status"] = SyncJobStatus.RUNNING.value
    
    return SuccessResponse(success=True, message="Sync resumed")


@router.post("/sync-configs/{config_id}/cancel", response_model=SuccessResponse)
async def cancel_sync(
    request: Request,
    config_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Cancel a running or paused sync job."""
    jobs_collection = await get_sync_jobs_collection(request)
    
    doc = await jobs_collection.find_one({
        "config_id": config_id,
        "user_id": user.id,
        "status": {"$in": [SyncJobStatus.RUNNING.value, SyncJobStatus.PAUSED.value]}
    })
    
    if not doc:
        raise HTTPException(status_code=404, detail="No active sync job found")
    
    job_id = str(doc["_id"])
    
    # Remove from active jobs to trigger cancellation
    _active_jobs.pop(job_id, None)
    
    await jobs_collection.update_one(
        {"_id": doc["_id"]},
        {"$set": {
            "status": SyncJobStatus.CANCELLED.value,
            "completed_at": datetime.utcnow()
        }}
    )
    
    return SuccessResponse(success=True, message="Sync cancelled")


@router.get("/sync-configs/{config_id}/history", response_model=SyncJobListResponse)
async def get_sync_history(
    request: Request,
    config_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    user: UserResponse = Depends(require_auth)
):
    """Get sync job history for a configuration."""
    jobs_collection = await get_sync_jobs_collection(request)
    
    # Count total
    total = await jobs_collection.count_documents({
        "config_id": config_id,
        "user_id": user.id
    })
    
    total_pages = max(1, (total + page_size - 1) // page_size)
    skip = (page - 1) * page_size
    
    # Fetch jobs
    jobs = []
    async for doc in jobs_collection.find(
        {"config_id": config_id, "user_id": user.id}
    ).sort("started_at", -1).skip(skip).limit(page_size):
        jobs.append(sync_job_doc_to_response(doc))
    
    return SyncJobListResponse(
        jobs=jobs,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages
    )


# ==================== Dashboard Endpoint ====================

@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    request: Request,
    user: UserResponse = Depends(require_auth)
):
    """Get cloud sources dashboard summary."""
    connections_collection = await get_connections_collection(request)
    configs_collection = await get_sync_configs_collection(request)
    jobs_collection = await get_sync_jobs_collection(request)
    
    # Get all user's connections
    connections = []
    async for conn in connections_collection.find({"user_id": user.id}):
        connections.append(conn)
    
    total_connections = len(connections)
    active_connections = sum(
        1 for c in connections 
        if c.get("status") == ConnectionStatus.ACTIVE.value
    )
    
    # Get all sync configs
    total_sync_configs = await configs_collection.count_documents({"user_id": user.id})
    
    # Get active jobs
    active_jobs = await jobs_collection.count_documents({
        "user_id": user.id,
        "status": SyncJobStatus.RUNNING.value
    })
    
    # Build source summaries
    sources = []
    total_files = 0
    total_size = 0
    next_sync = None
    
    for conn in connections:
        conn_id = str(conn["_id"])
        
        # Count sync configs for this connection
        config_count = await configs_collection.count_documents({
            "connection_id": conn_id
        })
        
        # Get stats from configs
        files = 0
        last_sync = None
        conn_next_sync = None
        has_errors = False
        
        async for config in configs_collection.find({"connection_id": conn_id}):
            stats = config.get("stats", {})
            files += stats.get("total_files", 0)
            
            config_last_sync = stats.get("last_sync_at")
            if config_last_sync and (not last_sync or config_last_sync > last_sync):
                last_sync = config_last_sync
            
            config_next = stats.get("next_scheduled_run")
            if config_next and (not conn_next_sync or config_next < conn_next_sync):
                conn_next_sync = config_next
        
        # Check for recent errors
        recent_error = await jobs_collection.find_one({
            "connection_id": conn_id,
            "status": SyncJobStatus.FAILED.value
        }, sort=[("started_at", -1)])
        has_errors = recent_error is not None
        
        sources.append(SourceSummary(
            connection_id=conn_id,
            provider=conn["provider"],
            display_name=conn["display_name"],
            status=ConnectionStatus(conn.get("status", "active")),
            sync_configs_count=config_count,
            total_files_indexed=files,
            last_sync_at=last_sync,
            next_sync_at=conn_next_sync,
            has_errors=has_errors,
        ))
        
        total_files += files
        if conn_next_sync and (not next_sync or conn_next_sync < next_sync):
            next_sync = conn_next_sync
    
    # Get recent errors
    recent_errors = []
    async for job in jobs_collection.find({
        "user_id": user.id,
        "status": SyncJobStatus.FAILED.value
    }).sort("started_at", -1).limit(5):
        for err in job.get("errors", [])[:1]:
            if isinstance(err, dict):
                recent_errors.append(SyncJobError(
                    file_path=err.get("file_path", ""),
                    error_type=err.get("error_type", "unknown"),
                    message=err.get("message", "Unknown error"),
                    timestamp=datetime.fromisoformat(err["timestamp"]) if isinstance(err.get("timestamp"), str) else err.get("timestamp", datetime.utcnow())
                ))
    
    return DashboardResponse(
        total_connections=total_connections,
        active_connections=active_connections,
        total_sync_configs=total_sync_configs,
        total_files_indexed=total_files,
        total_size_bytes=total_size,
        active_jobs=active_jobs,
        sources=sources,
        recent_errors=recent_errors,
        next_scheduled_sync=next_sync,
    )


# ==================== Job Details Endpoint ====================

@router.get("/jobs/{job_id}", response_model=SyncJobResponse)
async def get_job(
    request: Request,
    job_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Get details for a specific sync job."""
    jobs_collection = await get_sync_jobs_collection(request)
    
    try:
        doc = await jobs_collection.find_one({
            "_id": ObjectId(job_id),
            "user_id": user.id
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid job ID")
    
    if not doc:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return sync_job_doc_to_response(doc)


@router.get("/jobs/{job_id}/logs")
async def stream_job_logs(
    request: Request,
    job_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Stream job logs via Server-Sent Events."""
    
    async def generate():
        jobs_collection = await get_sync_jobs_collection(request)
        
        while True:
            doc = await jobs_collection.find_one({
                "_id": ObjectId(job_id),
                "user_id": user.id
            })
            
            if not doc:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break
            
            # Send current status
            yield f"data: {json.dumps({'progress': doc.get('progress', {}), 'status': doc.get('status')})}\n\n"
            
            # Stop streaming if job is done
            if doc.get("status") not in [SyncJobStatus.RUNNING.value, SyncJobStatus.PAUSED.value]:
                break
            
            await asyncio.sleep(1)
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )
