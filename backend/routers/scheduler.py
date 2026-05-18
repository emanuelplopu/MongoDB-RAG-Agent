"""Overnight exploration scheduler HTTP endpoints.

Phase 5 / Task 61 deliverable. Replaces the in-memory dict-based registry
with dependency-injected :class:`SchedulerStore` access so schedule
definitions, circuit-breaker state, and pause flags survive restarts.
A new ``run-now`` endpoint triggers an immediate execution regardless of
cron, returning a trace id while the run executes asynchronously.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.agent.strategy.scheduler_store import (
    InMemorySchedulerStore,
    MongoSchedulerStore,
    SchedulerStore,
)
from backend.scheduler.models import (
    NightlyReport,
    SchedulerRunState,
    StrategySchedule,
)
from backend.scheduler.scheduler_daemon import (
    CircuitBreakerOpenError,
    ScheduleNotFoundError,
    SchedulePausedError,
    SchedulerDaemon,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Module-level fallback state (used only when the FastAPI app does not
# expose ``app.state.db`` / ``app.state.scheduler_daemon`` \u2014 e.g. in
# unit tests of unrelated routers and in local dev without Mongo). ──────
_default_in_memory_store: InMemorySchedulerStore = InMemorySchedulerStore()
_default_daemon: SchedulerDaemon = SchedulerDaemon(store=_default_in_memory_store)
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


class RunNowResponse(BaseModel):
    """Response payload for the run-now endpoint."""

    trace_id: str = Field(..., description="Trace id of the spawned run")
    schedule_id: str = Field(..., description="Schedule that was triggered")


# ═══════════════════════════════════════════════════════════════════════════════
# Dependency injection
# ═══════════════════════════════════════════════════════════════════════════════


def _resolve_mongo_db(request: Request) -> Any:
    """Return an async Mongo database handle from ``app.state.db`` (or None).

    Supports both the project's :class:`DatabaseManager` wrapper (exposing
    ``.db``) and a raw async database handle.
    """
    db_state = (
        getattr(request.app.state, "db", None)
        if hasattr(request, "app")
        else None
    )
    if db_state is None:
        return None
    return getattr(db_state, "db", db_state)


def get_scheduler_store(request: Request) -> SchedulerStore:
    """FastAPI dependency that resolves the active :class:`SchedulerStore`.

    Resolution order:
        1. ``request.app.state.scheduler_store`` if pre-built by the
           application lifespan (preferred so the same instance is shared
           with the daemon).
        2. A fresh :class:`MongoSchedulerStore` over ``app.state.db``.
        3. The module-level :class:`InMemorySchedulerStore` fallback.
    """
    state = getattr(request.app, "state", None) if hasattr(request, "app") else None
    if state is not None:
        store = getattr(state, "scheduler_store", None)
        if isinstance(store, SchedulerStore):
            return store
    db = _resolve_mongo_db(request)
    if db is None:
        return _default_in_memory_store
    return MongoSchedulerStore(db)


def get_scheduler_daemon(request: Request) -> SchedulerDaemon:
    """Return the active :class:`SchedulerDaemon` for the current app.

    Falls back to the module-level default daemon when the lifespan has
    not registered one on ``app.state``.
    """
    state = getattr(request.app, "state", None) if hasattr(request, "app") else None
    if state is not None:
        daemon = getattr(state, "scheduler_daemon", None)
        if isinstance(daemon, SchedulerDaemon):
            return daemon
    return _default_daemon


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/schedules", response_model=ScheduleListResponse)
async def list_schedules(
    store: SchedulerStore = Depends(get_scheduler_store),
) -> ScheduleListResponse:
    """List all configured schedules."""
    try:
        schedules = await store.list()
        return ScheduleListResponse(schedules=schedules)
    except Exception as e:  # noqa: BLE001 - convert to 500 with context
        logger.error("Failed to list schedules: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Failed to list schedules: {str(e)}"
        )


@router.post("/schedules", response_model=StrategySchedule)
async def create_or_update_schedule(
    request: CreateScheduleRequest,
    store: SchedulerStore = Depends(get_scheduler_store),
) -> StrategySchedule:
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
        persisted = await store.upsert(schedule)
        logger.info(
            "Created/updated schedule: %s (%s)", persisted.name, persisted.id
        )
        return persisted
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to create/update schedule: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Failed to create schedule: {str(e)}"
        )


@router.get("/schedules/{schedule_id}", response_model=StrategySchedule)
async def get_schedule(
    schedule_id: str,
    store: SchedulerStore = Depends(get_scheduler_store),
) -> StrategySchedule:
    """Get details of a specific schedule."""
    schedule = await store.get(schedule_id)
    if schedule is None:
        raise HTTPException(
            status_code=404, detail=f"Schedule not found: {schedule_id}"
        )
    return schedule


@router.delete("/schedules/{schedule_id}")
async def disable_schedule(
    schedule_id: str,
    store: SchedulerStore = Depends(get_scheduler_store),
) -> dict[str, str]:
    """Disable a schedule (soft-delete \u2014 sets ``status='disabled'``)."""
    schedule = await store.get(schedule_id)
    if schedule is None:
        raise HTTPException(
            status_code=404, detail=f"Schedule not found: {schedule_id}"
        )

    try:
        schedule.status = "disabled"
        await store.upsert(schedule)
        logger.info("Disabled schedule: %s (%s)", schedule.name, schedule_id)
        return {"status": "disabled"}
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to disable schedule %s: %s", schedule_id, e)
        raise HTTPException(
            status_code=500, detail=f"Failed to disable schedule: {str(e)}"
        )


@router.post(
    "/schedules/{schedule_id}/run-now",
    response_model=RunNowResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_schedule_now(
    schedule_id: str,
    daemon: SchedulerDaemon = Depends(get_scheduler_daemon),
) -> RunNowResponse:
    """Trigger an immediate exploration run for ``schedule_id``.

    Returns ``202 Accepted`` with the run trace id while the actual
    exploration executes in the background. Returns ``404`` when the
    schedule does not exist, ``409`` when it is currently paused, and
    ``503`` when the persisted circuit breaker state is OPEN.
    """
    try:
        trace_id = await daemon.run_now(schedule_id)
    except ScheduleNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Schedule not found: {schedule_id}"
        )
    except SchedulePausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except CircuitBreakerOpenError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("run-now failed for schedule %s: %s", schedule_id, exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to trigger run-now: {exc}"
        )
    return RunNowResponse(trace_id=trace_id, schedule_id=schedule_id)


@router.get("/status", response_model=SchedulerStatusResponse)
async def get_scheduler_status(
    daemon: SchedulerDaemon = Depends(get_scheduler_daemon),
) -> SchedulerStatusResponse:
    """Get current scheduler daemon run status."""
    try:
        return SchedulerStatusResponse(
            running=daemon._running,
            current_run=daemon._current_run,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to get scheduler status: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Failed to get status: {str(e)}"
        )


@router.post("/pause")
async def pause_scheduler(
    daemon: SchedulerDaemon = Depends(get_scheduler_daemon),
) -> dict[str, str]:
    """Manually pause the scheduler daemon."""
    global _manually_paused
    try:
        _manually_paused = True
        daemon._shutdown_requested = True
        logger.info("Scheduler manually paused")
        return {"status": "paused"}
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to pause scheduler: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Failed to pause: {str(e)}"
        )


@router.post("/resume")
async def resume_scheduler(
    daemon: SchedulerDaemon = Depends(get_scheduler_daemon),
) -> dict[str, str]:
    """Resume the scheduler from manual pause."""
    global _manually_paused
    try:
        _manually_paused = False
        daemon._shutdown_requested = False
        logger.info("Scheduler resumed from manual pause")
        return {"status": "resumed"}
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to resume scheduler: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Failed to resume: {str(e)}"
        )


@router.get("/reports", response_model=ReportsResponse)
async def get_reports(limit: int = 10) -> ReportsResponse:
    """Get recent nightly reports."""
    try:
        sorted_reports = sorted(
            _reports,
            key=lambda r: r.started_at or r.completed_at or "",
            reverse=True,
        )
        return ReportsResponse(reports=sorted_reports[:limit])
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to get reports: %s", e)
        raise HTTPException(
            status_code=500, detail=f"Failed to get reports: {str(e)}"
        )
