"""Advanced ingestion router - Queue management, scheduling, selective ingestion."""

import logging
import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from enum import Enum
from fastapi import APIRouter, Request, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, Field

from backend.core.config import settings
from backend.routers.auth import require_admin, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Ingestion queue state
_ingestion_queue: List[Dict[str, Any]] = []
_scheduled_jobs: Dict[str, Dict[str, Any]] = {}
_queue_lock = asyncio.Lock()


class FileTypeFilter(str, Enum):
    """File types that can be filtered."""
    DOCUMENTS = "documents"
    IMAGES = "images"
    AUDIO = "audio"
    VIDEO = "video"
    ALL = "all"


class ScheduleFrequency(str, Enum):
    """Ingestion schedule frequencies."""
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class QueuedIngestionJob(BaseModel):
    """A queued ingestion job."""
    id: str
    profile_key: str
    profile_name: str
    file_types: List[str] = Field(default_factory=lambda: ["all"])
    incremental: bool = True
    priority: int = 0  # Higher = more priority
    created_at: str
    status: str = "queued"  # queued, running, completed, failed, cancelled
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


class ScheduledIngestionJob(BaseModel):
    """A scheduled ingestion job configuration."""
    id: str
    profile_key: str
    profile_name: str
    file_types: List[str] = Field(default_factory=lambda: ["all"])
    incremental: bool = True
    frequency: ScheduleFrequency
    hour: int = 0  # Hour of day (0-23) for daily/weekly/monthly
    day_of_week: int = 0  # Day of week (0-6) for weekly
    day_of_month: int = 1  # Day of month (1-31) for monthly
    enabled: bool = True
    last_run: Optional[str] = None
    next_run: Optional[str] = None
    created_at: str


class QueueRequest(BaseModel):
    """Request to add job to queue."""
    profile_key: str
    file_types: List[str] = Field(default=["all"])
    incremental: bool = True
    priority: int = 0


class ScheduleRequest(BaseModel):
    """Request to create a scheduled job."""
    profile_key: str
    file_types: List[str] = Field(default=["all"])
    incremental: bool = True
    frequency: ScheduleFrequency
    hour: int = 0
    day_of_week: int = 0
    day_of_month: int = 1


class QueueStatus(BaseModel):
    """Queue status response."""
    queue: List[QueuedIngestionJob]
    current_job: Optional[QueuedIngestionJob]
    total_queued: int
    is_processing: bool


def get_profile_manager():
    """Get profile manager instance."""
    from src.profile import get_profile_manager as get_pm
    return get_pm(settings.profiles_path)


# Track if queue processor is running
_queue_processor_running = False


@router.get("/queue", response_model=QueueStatus)
async def get_ingestion_queue(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """Get current ingestion queue status."""
    async with _queue_lock:
        current = None
        queued = []
        
        for job in _ingestion_queue:
            if job["status"] == "running":
                current = QueuedIngestionJob(**job)
            elif job["status"] == "queued":
                queued.append(QueuedIngestionJob(**job))
        
        return QueueStatus(
            queue=queued,
            current_job=current,
            total_queued=len(queued),
            is_processing=current is not None
        )


@router.post("/queue/add")
async def add_to_queue(
    request: Request,
    queue_request: QueueRequest,
    background_tasks: BackgroundTasks,
    admin: UserResponse = Depends(require_admin)
):
    """Add an ingestion job to the queue."""
    global _queue_processor_running
    
    pm = get_profile_manager()
    profiles = pm.list_profiles()
    
    if queue_request.profile_key not in profiles:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    profile = profiles[queue_request.profile_key]
    
    job = {
        "id": str(uuid.uuid4()),
        "profile_key": queue_request.profile_key,
        "profile_name": profile.name,
        "file_types": queue_request.file_types,
        "incremental": queue_request.incremental,
        "priority": queue_request.priority,
        "created_at": datetime.now().isoformat(),
        "status": "queued"
    }
    
    async with _queue_lock:
        _ingestion_queue.append(job)
        # Sort by priority (higher first), then by creation time
        _ingestion_queue.sort(key=lambda x: (-x["priority"], x["created_at"]))
    
    # Start queue processor if not running
    if not _queue_processor_running:
        background_tasks.add_task(_process_queue, request.app.state.db)
    
    return {"success": True, "job": QueuedIngestionJob(**job)}


@router.post("/queue/add-multiple")
async def add_multiple_to_queue(
    request: Request,
    jobs: List[QueueRequest],
    background_tasks: BackgroundTasks,
    admin: UserResponse = Depends(require_admin)
):
    """Add multiple ingestion jobs to the queue."""
    global _queue_processor_running
    
    pm = get_profile_manager()
    profiles = pm.list_profiles()
    
    added_jobs = []
    
    async with _queue_lock:
        for queue_request in jobs:
            if queue_request.profile_key not in profiles:
                continue
            
            profile = profiles[queue_request.profile_key]
            
            job = {
                "id": str(uuid.uuid4()),
                "profile_key": queue_request.profile_key,
                "profile_name": profile.name,
                "file_types": queue_request.file_types,
                "incremental": queue_request.incremental,
                "priority": queue_request.priority,
                "created_at": datetime.now().isoformat(),
                "status": "queued"
            }
            
            _ingestion_queue.append(job)
            added_jobs.append(QueuedIngestionJob(**job))
        
        _ingestion_queue.sort(key=lambda x: (-x["priority"], x["created_at"]))
    
    if not _queue_processor_running and added_jobs:
        background_tasks.add_task(_process_queue, request.app.state.db)
    
    return {"success": True, "added": len(added_jobs), "jobs": added_jobs}


@router.delete("/queue/{job_id}")
async def remove_from_queue(
    request: Request,
    job_id: str,
    admin: UserResponse = Depends(require_admin)
):
    """Remove a job from the queue."""
    async with _queue_lock:
        for i, job in enumerate(_ingestion_queue):
            if job["id"] == job_id:
                if job["status"] == "running":
                    raise HTTPException(status_code=400, detail="Cannot remove running job")
                removed = _ingestion_queue.pop(i)
                return {"success": True, "removed": QueuedIngestionJob(**removed)}
    
    raise HTTPException(status_code=404, detail="Job not found")


@router.delete("/queue")
async def clear_queue(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """Clear all queued (non-running) jobs."""
    async with _queue_lock:
        removed = [j for j in _ingestion_queue if j["status"] == "queued"]
        _ingestion_queue[:] = [j for j in _ingestion_queue if j["status"] != "queued"]
    
    return {"success": True, "removed_count": len(removed)}


@router.post("/queue/reorder")
async def reorder_queue(
    request: Request,
    job_ids: List[str],
    admin: UserResponse = Depends(require_admin)
):
    """Reorder the queue by providing job IDs in desired order."""
    async with _queue_lock:
        queued_jobs = {j["id"]: j for j in _ingestion_queue if j["status"] == "queued"}
        running_jobs = [j for j in _ingestion_queue if j["status"] == "running"]
        
        new_order = []
        for job_id in job_ids:
            if job_id in queued_jobs:
                new_order.append(queued_jobs.pop(job_id))
        
        # Add any remaining jobs not in the order list
        new_order.extend(queued_jobs.values())
        
        _ingestion_queue[:] = running_jobs + new_order
    
    return {"success": True, "new_order": [j["id"] for j in _ingestion_queue]}


async def _process_queue(db):
    """Process the ingestion queue."""
    global _queue_processor_running
    
    if _queue_processor_running:
        return
    
    _queue_processor_running = True
    logger.info("Queue processor started")
    
    try:
        while True:
            # Get next job
            next_job = None
            
            async with _queue_lock:
                for job in _ingestion_queue:
                    if job["status"] == "queued":
                        job["status"] = "running"
                        job["started_at"] = datetime.now().isoformat()
                        next_job = job
                        break
            
            if not next_job:
                break
            
            logger.info(f"Processing queue job: {next_job['id']} for profile {next_job['profile_key']}")
            
            try:
                await _run_ingestion_job(next_job, db)
                
                async with _queue_lock:
                    next_job["status"] = "completed"
                    next_job["completed_at"] = datetime.now().isoformat()
                    
            except Exception as e:
                logger.error(f"Queue job {next_job['id']} failed: {e}")
                async with _queue_lock:
                    next_job["status"] = "failed"
                    next_job["error"] = str(e)
                    next_job["completed_at"] = datetime.now().isoformat()
            
            # Small delay between jobs
            await asyncio.sleep(1)
    
    finally:
        _queue_processor_running = False
        logger.info("Queue processor stopped")


async def _run_ingestion_job(job: Dict[str, Any], db):
    """Run a single ingestion job."""
    from src.ingestion.ingest import DocumentIngestionPipeline, IngestionConfig
    from src.profile import get_profile_manager
    
    pm = get_profile_manager()
    pm.switch_profile(job["profile_key"])
    
    # Create config based on file types
    file_types = job.get("file_types", ["all"])
    
    # Build file type filters
    include_documents = "all" in file_types or "documents" in file_types
    include_images = "all" in file_types or "images" in file_types
    include_audio = "all" in file_types or "audio" in file_types
    include_video = "all" in file_types or "video" in file_types
    
    # Build patterns based on file types
    patterns = []
    if include_documents:
        patterns.extend(["*.md", "*.txt", "*.pdf", "*.docx", "*.doc", "*.html", "*.htm", "*.xlsx", "*.xls", "*.pptx", "*.ppt"])
    if include_images:
        patterns.extend(["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.bmp"])
    if include_audio:
        patterns.extend(["*.mp3", "*.wav", "*.m4a", "*.flac"])
    if include_video:
        patterns.extend(["*.mp4", "*.avi", "*.mkv", "*.mov", "*.webm"])
    
    config = IngestionConfig()
    
    # Create pipeline
    loop = asyncio.get_running_loop()
    pipeline = await loop.run_in_executor(
        None,
        lambda: DocumentIngestionPipeline(config=config, use_profile=True)
    )
    
    # Set custom patterns if not "all"
    if "all" not in file_types:
        pipeline._custom_patterns = patterns
    
    # Run ingestion
    results = await pipeline.ingest_documents(
        incremental=job.get("incremental", True)
    )
    
    logger.info(f"Ingestion job completed: {len(results)} files processed")


# ============== Scheduled Jobs ==============

@router.get("/schedules")
async def list_scheduled_jobs(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """List all scheduled ingestion jobs."""
    db = request.app.state.db
    
    # Get from database
    schedules = await _get_schedules_from_db(db)
    
    return {"schedules": schedules}


@router.post("/schedules")
async def create_schedule(
    request: Request,
    schedule_request: ScheduleRequest,
    admin: UserResponse = Depends(require_admin)
):
    """Create a scheduled ingestion job."""
    db = request.app.state.db
    pm = get_profile_manager()
    profiles = pm.list_profiles()
    
    if schedule_request.profile_key not in profiles:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    profile = profiles[schedule_request.profile_key]
    
    schedule_id = str(uuid.uuid4())
    next_run = _calculate_next_run(schedule_request)
    
    schedule = {
        "id": schedule_id,
        "profile_key": schedule_request.profile_key,
        "profile_name": profile.name,
        "file_types": schedule_request.file_types,
        "incremental": schedule_request.incremental,
        "frequency": schedule_request.frequency.value,
        "hour": schedule_request.hour,
        "day_of_week": schedule_request.day_of_week,
        "day_of_month": schedule_request.day_of_month,
        "enabled": True,
        "last_run": None,
        "next_run": next_run.isoformat(),
        "created_at": datetime.now().isoformat()
    }
    
    # Save to database
    await _save_schedule_to_db(db, schedule)
    
    return {"success": True, "schedule": ScheduledIngestionJob(**schedule)}


@router.put("/schedules/{schedule_id}")
async def update_schedule(
    request: Request,
    schedule_id: str,
    schedule_request: ScheduleRequest,
    admin: UserResponse = Depends(require_admin)
):
    """Update a scheduled ingestion job."""
    db = request.app.state.db
    pm = get_profile_manager()
    profiles = pm.list_profiles()
    
    if schedule_request.profile_key not in profiles:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    # Get existing schedule
    existing = await _get_schedule_from_db(db, schedule_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    profile = profiles[schedule_request.profile_key]
    next_run = _calculate_next_run(schedule_request)
    
    schedule = {
        **existing,
        "profile_key": schedule_request.profile_key,
        "profile_name": profile.name,
        "file_types": schedule_request.file_types,
        "incremental": schedule_request.incremental,
        "frequency": schedule_request.frequency.value,
        "hour": schedule_request.hour,
        "day_of_week": schedule_request.day_of_week,
        "day_of_month": schedule_request.day_of_month,
        "next_run": next_run.isoformat()
    }
    
    await _save_schedule_to_db(db, schedule)
    
    return {"success": True, "schedule": ScheduledIngestionJob(**schedule)}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(
    request: Request,
    schedule_id: str,
    admin: UserResponse = Depends(require_admin)
):
    """Delete a scheduled ingestion job."""
    db = request.app.state.db
    
    result = await _delete_schedule_from_db(db, schedule_id)
    if not result:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    return {"success": True}


@router.post("/schedules/{schedule_id}/toggle")
async def toggle_schedule(
    request: Request,
    schedule_id: str,
    admin: UserResponse = Depends(require_admin)
):
    """Enable or disable a scheduled job."""
    db = request.app.state.db
    
    schedule = await _get_schedule_from_db(db, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    schedule["enabled"] = not schedule.get("enabled", True)
    await _save_schedule_to_db(db, schedule)
    
    return {"success": True, "enabled": schedule["enabled"]}


@router.post("/schedules/{schedule_id}/run-now")
async def run_schedule_now(
    request: Request,
    schedule_id: str,
    background_tasks: BackgroundTasks,
    admin: UserResponse = Depends(require_admin)
):
    """Manually trigger a scheduled job to run now."""
    db = request.app.state.db
    
    schedule = await _get_schedule_from_db(db, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    # Add to queue
    queue_request = QueueRequest(
        profile_key=schedule["profile_key"],
        file_types=schedule.get("file_types", ["all"]),
        incremental=schedule.get("incremental", True),
        priority=5  # Higher priority for manual runs
    )
    
    return await add_to_queue(request, queue_request, background_tasks, admin)


def _calculate_next_run(schedule: ScheduleRequest) -> datetime:
    """Calculate the next run time for a schedule."""
    now = datetime.now()
    
    if schedule.frequency == ScheduleFrequency.HOURLY:
        next_run = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    elif schedule.frequency == ScheduleFrequency.DAILY:
        next_run = now.replace(hour=schedule.hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
    elif schedule.frequency == ScheduleFrequency.WEEKLY:
        days_ahead = schedule.day_of_week - now.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        next_run = now.replace(hour=schedule.hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    elif schedule.frequency == ScheduleFrequency.MONTHLY:
        next_run = now.replace(day=min(schedule.day_of_month, 28), hour=schedule.hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            if now.month == 12:
                next_run = next_run.replace(year=now.year + 1, month=1)
            else:
                next_run = next_run.replace(month=now.month + 1)
    else:
        next_run = now + timedelta(days=1)
    
    return next_run


# Database helpers for schedules
SCHEDULES_COLLECTION = "ingestion_schedules"


async def _get_schedules_from_db(db) -> List[Dict]:
    collection = db.db[SCHEDULES_COLLECTION]
    schedules = []
    async for doc in collection.find():
        doc["id"] = doc.pop("_id")
        schedules.append(doc)
    return schedules


async def _get_schedule_from_db(db, schedule_id: str) -> Optional[Dict]:
    collection = db.db[SCHEDULES_COLLECTION]
    doc = await collection.find_one({"_id": schedule_id})
    if doc:
        doc["id"] = doc.pop("_id")
    return doc


async def _save_schedule_to_db(db, schedule: Dict):
    collection = db.db[SCHEDULES_COLLECTION]
    schedule_doc = {**schedule, "_id": schedule["id"]}
    del schedule_doc["id"]
    await collection.replace_one({"_id": schedule_doc["_id"]}, schedule_doc, upsert=True)


async def _delete_schedule_from_db(db, schedule_id: str) -> bool:
    collection = db.db[SCHEDULES_COLLECTION]
    result = await collection.delete_one({"_id": schedule_id})
    return result.deleted_count > 0


# Background scheduler checker
async def check_scheduled_jobs(db):
    """Check and run due scheduled jobs. Call periodically."""
    now = datetime.now()
    
    schedules = await _get_schedules_from_db(db)
    
    for schedule in schedules:
        if not schedule.get("enabled", True):
            continue
        
        next_run_str = schedule.get("next_run")
        if not next_run_str:
            continue
        
        next_run = datetime.fromisoformat(next_run_str)
        
        if next_run <= now:
            # Check if ingestion is already running
            from backend.routers.ingestion import _current_job_id
            if _current_job_id:
                logger.info(f"Skipping scheduled job {schedule['id']} - ingestion already running")
                continue
            
            # Add to queue
            async with _queue_lock:
                job = {
                    "id": str(uuid.uuid4()),
                    "profile_key": schedule["profile_key"],
                    "profile_name": schedule["profile_name"],
                    "file_types": schedule.get("file_types", ["all"]),
                    "incremental": schedule.get("incremental", True),
                    "priority": 1,  # Normal priority for scheduled
                    "created_at": datetime.now().isoformat(),
                    "status": "queued"
                }
                _ingestion_queue.append(job)
            
            # Update schedule
            schedule["last_run"] = now.isoformat()
            schedule["next_run"] = _calculate_next_run(ScheduleRequest(
                profile_key=schedule["profile_key"],
                file_types=schedule.get("file_types", ["all"]),
                incremental=schedule.get("incremental", True),
                frequency=ScheduleFrequency(schedule["frequency"]),
                hour=schedule.get("hour", 0),
                day_of_week=schedule.get("day_of_week", 0),
                day_of_month=schedule.get("day_of_month", 1)
            )).isoformat()
            
            await _save_schedule_to_db(db, schedule)
            
            logger.info(f"Scheduled job {schedule['id']} added to queue")
