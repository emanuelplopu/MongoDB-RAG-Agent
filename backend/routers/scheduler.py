"""Overnight exploration scheduler HTTP endpoints."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.scheduler.models import StrategySchedule, SchedulerRunState, NightlyReport
from backend.scheduler.scheduler_daemon import SchedulerDaemon

logger = logging.getLogger(__name__)
router = APIRouter()

# Module-level in-memory state (no DB for now)
_daemon = SchedulerDaemon(db=None)
_schedules: dict[str, StrategySchedule] = {}
_reports: list[NightlyReport] = []
_manually_paused: bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# Request/Response Models
# ═══════════════════════════════════════════════════════════════════════════════


class ScheduleListResponse(BaseModel):
    """Response for listing schedules."""

    schedules: list[StrategySchedule]


class CreateScheduleRequest(BaseModel):
    """Request body for creating/updating a schedule."""

    name: str = Field(default="Overnight Exploration")
    cron: str = Field(default="0 22 * * 1-5", description="Cron expression (5-field)")
    timezone: str = Field(default="Europe/Vienna")
    allowed_window_start: str = Field(default="22:00")
    allowed_window_end: str = Field(default="06:00")
    dataset_id: Optional[str] = None
    candidate_strategy_ids: list[str] = Field(default_factory=list)
    status: str = Field(default="active")


class SchedulerStatusResponse(BaseModel):
    """Response for scheduler run status."""

    running: bool
    current_run: Optional[SchedulerRunState] = None


class ReportsResponse(BaseModel):
    """Response for nightly reports."""

    reports: list[NightlyReport]


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/schedules", response_model=ScheduleListResponse)
async def list_schedules():
    """List all configured schedules."""
    try:
        return ScheduleListResponse(schedules=list(_schedules.values()))
    except Exception as e:
        logger.error("Failed to list schedules: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to list schedules: {str(e)}")


@router.post("/schedules", response_model=StrategySchedule)
async def create_or_update_schedule(request: CreateScheduleRequest):
    """Create or update a schedule."""
    try:
        schedule = StrategySchedule(
            name=request.name,
            cron=request.cron,
            timezone=request.timezone,
            allowed_window_start=request.allowed_window_start,
            allowed_window_end=request.allowed_window_end,
            dataset_id=request.dataset_id,
            candidate_strategy_ids=request.candidate_strategy_ids,
            status=request.status,
        )
        _schedules[schedule.id] = schedule
        logger.info("Created/updated schedule: %s (%s)", schedule.name, schedule.id)
        return schedule
    except Exception as e:
        logger.error("Failed to create/update schedule: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to create schedule: {str(e)}")


@router.get("/schedules/{schedule_id}", response_model=StrategySchedule)
async def get_schedule(schedule_id: str):
    """Get details of a specific schedule."""
    schedule = _schedules.get(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    return schedule


@router.delete("/schedules/{schedule_id}")
async def disable_schedule(schedule_id: str):
    """Disable a schedule (soft-delete)."""
    schedule = _schedules.get(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")

    try:
        schedule.status = "disabled"
        logger.info("Disabled schedule: %s (%s)", schedule.name, schedule_id)
        return {"status": "disabled"}
    except Exception as e:
        logger.error("Failed to disable schedule %s: %s", schedule_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to disable schedule: {str(e)}")


@router.get("/status", response_model=SchedulerStatusResponse)
async def get_scheduler_status():
    """Get current scheduler daemon run status."""
    try:
        return SchedulerStatusResponse(
            running=_daemon._running,
            current_run=_daemon._current_run,
        )
    except Exception as e:
        logger.error("Failed to get scheduler status: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to get status: {str(e)}")


@router.post("/pause")
async def pause_scheduler():
    """Manually pause the scheduler daemon."""
    global _manually_paused
    try:
        _manually_paused = True
        _daemon._shutdown_requested = True
        logger.info("Scheduler manually paused")
        return {"status": "paused"}
    except Exception as e:
        logger.error("Failed to pause scheduler: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to pause: {str(e)}")


@router.post("/resume")
async def resume_scheduler():
    """Resume the scheduler from manual pause."""
    global _manually_paused
    try:
        _manually_paused = False
        _daemon._shutdown_requested = False
        logger.info("Scheduler resumed from manual pause")
        return {"status": "resumed"}
    except Exception as e:
        logger.error("Failed to resume scheduler: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to resume: {str(e)}")


@router.get("/reports", response_model=ReportsResponse)
async def get_reports(limit: int = 10):
    """Get recent nightly reports."""
    try:
        # Return most recent reports first, limited by requested count
        sorted_reports = sorted(
            _reports,
            key=lambda r: r.started_at or r.completed_at or "",
            reverse=True,
        )
        return ReportsResponse(reports=sorted_reports[:limit])
    except Exception as e:
        logger.error("Failed to get reports: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to get reports: {str(e)}")
