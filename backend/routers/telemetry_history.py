"""Telemetry history API: host-agent samples correlated with ingestion and chat activity.

Superadmin only. Backed by :mod:`backend.services.telemetry_analysis`; returns 503 when telemetry
history is disabled (``TELEMETRY_HISTORY_ENABLED=false``) or its store failed to start.

* ``GET /api/v1/telemetry/history`` — host metrics, per-container use, activity and job phases.
* ``GET /api/v1/telemetry/jobs`` — ingestion jobs seen in a range.
* ``GET /api/v1/telemetry/jobs/{job_id}`` — correlated job report with anomalies.
* ``GET /api/v1/telemetry/jobs/{job_id}/report.html`` — the same as a self-contained HTML page.
* ``GET /api/v1/telemetry/anomalies`` — anomalies of every job and host condition in a range.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from backend.models.telemetry_history import AnomalyReport, HistoryResponse, JobReport, JobSummary
from backend.routers.auth import UserResponse, require_admin
from backend.services import telemetry_analysis
from backend.services.telemetry_store import TelemetryStore

router = APIRouter(prefix="/api/v1/telemetry", tags=["Telemetry History"])

#: Longest range one request may cover.
MAX_RANGE = timedelta(days=90)

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _store(request: Request) -> TelemetryStore:
    store = getattr(request.app.state, "telemetry_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Telemetry history is not enabled")
    return store


def resolve_range(hours: float, start: Optional[datetime], end: Optional[datetime]) -> tuple[datetime, datetime]:
    """Explicit ``start``/``end`` win over ``hours`` (back from ``end`` or now); naive times are UTC.

    Raises:
        HTTPException: 422 for an empty, inverted or too long range.
    """
    def aware(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    stop = aware(end) if end is not None else datetime.now(timezone.utc)
    begin = aware(start) if start is not None else stop - timedelta(hours=hours)
    if begin >= stop:
        raise HTTPException(status_code=422, detail="start must be before end")
    if stop - begin > MAX_RANGE:
        raise HTTPException(status_code=422, detail=f"Range too long (max {MAX_RANGE.days} days)")
    return begin, stop


@router.get("/history", response_model=HistoryResponse)
async def get_history(
    request: Request,
    hours: float = Query(6.0, gt=0, le=MAX_RANGE.days * 24, description="Range back from end (or now)."),
    start: Optional[datetime] = Query(None, description="Range start (ISO, UTC if naive)."),
    end: Optional[datetime] = Query(None, description="Range end (ISO, UTC if naive)."),
    bucket: Optional[int] = Query(None, ge=5, le=86400, description="Aggregation bucket in seconds (auto by default)."),
    admin: UserResponse = Depends(require_admin),
) -> HistoryResponse:
    """Host metrics, per-container use, finished files, chat requests and job phases over a range."""
    begin, stop = resolve_range(hours, start, end)
    return await telemetry_analysis.history(_store(request), begin, stop, bucket)


@router.get("/jobs", response_model=List[JobSummary])
async def list_jobs(
    request: Request,
    hours: float = Query(168.0, gt=0, le=MAX_RANGE.days * 24),
    limit: int = Query(50, ge=1, le=500),
    admin: UserResponse = Depends(require_admin),
) -> List[JobSummary]:
    """Ingestion jobs with activity in the range, newest first."""
    begin, stop = resolve_range(hours, None, None)
    rows = await _store(request).recent_jobs(begin, stop, limit=limit)
    return [JobSummary(**row) for row in rows]


@router.get("/jobs/{job_id}", response_model=JobReport)
async def get_job_report(job_id: str, request: Request, admin: UserResponse = Depends(require_admin)) -> JobReport:
    """Phases with resource use, step timings, file types, slowest/failed/unfinished files, anomalies."""
    report = await telemetry_analysis.job_report(_store(request), job_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"No telemetry for job {job_id}")
    return report


@router.get("/jobs/{job_id}/report.html", response_class=HTMLResponse)
async def get_job_report_html(job_id: str, request: Request, admin: UserResponse = Depends(require_admin)) -> HTMLResponse:
    """The job report as one self-contained HTML page (charts included), served as a download."""
    from backend.services.telemetry_report_html import render_job_report_html

    html = await render_job_report_html(_store(request), job_id)
    if html is None:
        raise HTTPException(status_code=404, detail=f"No telemetry for job {job_id}")
    filename = f"telemetry-{_SAFE_FILENAME.sub('_', job_id)[:80]}.html"
    return HTMLResponse(html, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/anomalies", response_model=AnomalyReport)
async def get_anomalies(
    request: Request,
    hours: float = Query(24.0, gt=0, le=MAX_RANGE.days * 24),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    admin: UserResponse = Depends(require_admin),
) -> AnomalyReport:
    """Host, GPU, backend, container and file anomalies in the range, most severe first."""
    begin, stop = resolve_range(hours, start, end)
    return await telemetry_analysis.range_anomalies(_store(request), begin, stop)
