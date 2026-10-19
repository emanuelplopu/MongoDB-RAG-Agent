"""Admin telemetry management endpoints.

Includes remote viewer API for the Electron telemetry viewer app.
CORS middleware is configured in backend/main.py.

The viewer only shows this backend's tenant: its own folder
(``data/telemetry/<tenant>``) plus the records of this tenant in files written
before telemetry was split per tenant (docs/27-TENANT_ISOLATION.md).
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel
from typing import Iterator, Optional

from backend.routers.auth import require_admin, UserResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/telemetry", tags=["admin-telemetry"])


class TelemetryStatus(BaseModel):
    enabled: bool
    mode: str
    pii_mode: str
    retention_days: int
    storage_path: str
    pseudonymizer_engines: list[str] = []


class TelemetryConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[str] = None
    pii_mode: Optional[str] = None
    retention_days: Optional[int] = None


@router.get("/status", response_model=TelemetryStatus)
async def get_telemetry_status(request: Request, user: UserResponse = Depends(require_admin)):
    """Get current telemetry configuration and status."""
    telemetry = getattr(request.app.state, "telemetry", None)
    if not telemetry:
        raise HTTPException(status_code=503, detail="Telemetry service not initialized")

    engines = []
    if telemetry._pseudonymizer:
        if telemetry._pseudonymizer._regex:
            engines.append("regex")
        if telemetry._pseudonymizer._spacy and telemetry._pseudonymizer._spacy.available:
            engines.append("spacy")
        if telemetry._pseudonymizer._presidio and telemetry._pseudonymizer._presidio.available:
            engines.append("presidio")

    return TelemetryStatus(
        enabled=telemetry._enabled,
        mode=telemetry._mode,
        pii_mode=telemetry._pii_mode,
        retention_days=telemetry._retention_days,
        storage_path=str(telemetry._storage_path),
        pseudonymizer_engines=engines,
    )


@router.post("/config", response_model=TelemetryStatus)
async def update_telemetry_config(
    request: Request, update: TelemetryConfigUpdate, user: UserResponse = Depends(require_admin)
):
    """Update telemetry configuration at runtime (no restart needed)."""
    telemetry = getattr(request.app.state, "telemetry", None)
    if not telemetry:
        raise HTTPException(status_code=503, detail="Telemetry service not initialized")

    if update.enabled is not None:
        telemetry._enabled = update.enabled
        logger.info(f"Telemetry enabled={update.enabled} (changed by admin {user.email})")

    if update.mode is not None:
        if update.mode not in ("dev", "beta", "production", "disabled"):
            raise HTTPException(status_code=400, detail="Invalid mode. Use: dev, beta, production, disabled")
        telemetry._mode = update.mode
        logger.info(f"Telemetry mode={update.mode}")

    if update.pii_mode is not None:
        if update.pii_mode not in ("both", "protected_only", "raw_only", "disabled"):
            raise HTTPException(status_code=400, detail="Invalid pii_mode. Use: both, protected_only, raw_only, disabled")
        telemetry._pii_mode = update.pii_mode
        # Re-create raw directory if needed
        if update.pii_mode in ("both", "raw_only"):
            (telemetry._storage_path / "raw").mkdir(parents=True, exist_ok=True)
        logger.info(f"Telemetry pii_mode={update.pii_mode}")

    if update.retention_days is not None:
        if update.retention_days < 1:
            raise HTTPException(status_code=400, detail="retention_days must be >= 1")
        telemetry._retention_days = update.retention_days
        logger.info(f"Telemetry retention_days={update.retention_days}")

    # Return updated status
    return await get_telemetry_status(request, user)


# ---------------------------------------------------------------------------
# Remote Viewer Endpoints
# ---------------------------------------------------------------------------


class TelemetryFileInfo(BaseModel):
    date: str  # YYYY-MM-DD
    size_bytes: int
    record_count: int
    has_raw: bool


class TelemetrySearchRequest(BaseModel):
    session_id: Optional[str] = None
    model: Optional[str] = None
    date_from: Optional[str] = None  # YYYY-MM-DD
    date_to: Optional[str] = None
    min_latency: Optional[int] = None  # ms
    query_text: Optional[str] = None  # search in prompt text
    limit: int = 100


class TelemetryStats(BaseModel):
    range: str
    records_count: int
    avg_latency_ms: Optional[float] = None
    total_tokens: int = 0
    error_rate: float = 0.0
    model_distribution: dict[str, int] = {}


def _get_telemetry_or_fail(request: Request):
    """Get telemetry service from app state or raise 503."""
    telemetry = getattr(request.app.state, "telemetry", None)
    if not telemetry:
        raise HTTPException(status_code=503, detail="Telemetry service not initialized")
    return telemetry


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _day_sources(telemetry, source: str) -> list[tuple[Path, Optional[str]]]:
    """Folders holding this backend's records, each with the tenant to filter on.

    The service's own folder (``data/telemetry/<tenant>``) needs no filter.
    Files written before telemetry was split per tenant sit directly in the
    base folder and mix both tenants; only records of this tenant count there.
    """
    sub = "raw" if source == "raw" else None
    own: Path = telemetry._storage_path
    sources: list[tuple[Path, Optional[str]]] = [(own / sub if sub else own, None)]
    legacy: Optional[Path] = getattr(telemetry, "_legacy_path", None)
    tenant: Optional[str] = getattr(telemetry, "_tenant", None)
    if legacy is not None and tenant:
        sources.append((legacy / sub if sub else legacy, tenant))
    return sources


def _dates(telemetry, source: str) -> list[str]:
    """Dates (YYYY-MM-DD) with a telemetry file in any source folder, newest first."""
    dates: set[str] = set()
    for folder, _tenant in _day_sources(telemetry, source):
        if folder.is_dir():
            dates.update(p.stem for p in folder.glob("*.jsonl") if _DATE_RE.match(p.stem))
    return sorted(dates, reverse=True)


def _dates_in_range(telemetry, source: str, date_from: Optional[str], date_to: Optional[str]) -> list[str]:
    """Dates within the given range, newest first."""
    return [
        d for d in _dates(telemetry, source)
        if not (date_from and d < date_from) and not (date_to and d > date_to)
    ]


def _iter_day(telemetry, date: str, source: str) -> Iterator[tuple[str, Optional[dict]]]:
    """Yield ``(line, record)`` for one day's lines visible to this tenant.

    ``record`` is None for a line that is not valid JSON; such lines only
    count in the tenant's own files, since legacy lines cannot be attributed.
    """
    for folder, tenant in _day_sources(telemetry, source):
        path = folder / f"{date}.jsonl"
        if not path.is_file():
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    record = None
                if tenant is not None and (
                    not isinstance(record, dict) or str(record.get("tenant") or "").strip().lower() != tenant
                ):
                    continue
                yield line, record


def _check_date(date: str) -> None:
    """Reject anything but YYYY-MM-DD (the value becomes part of a file name)."""
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")


@router.get("/files")
async def list_telemetry_files(
    request: Request, user: UserResponse = Depends(require_admin)
):
    """List available telemetry log files with metadata (this tenant's records only)."""
    telemetry = _get_telemetry_or_fail(request)

    files: list[TelemetryFileInfo] = []
    for date in _dates(telemetry, "protected"):
        lines = [line for line, _record in _iter_day(telemetry, date, "protected")]
        if not lines:
            continue
        size = sum(len(line.encode("utf-8")) for line in lines)
        has_raw = next(_iter_day(telemetry, date, "raw"), None) is not None
        files.append(TelemetryFileInfo(date=date, size_bytes=size, record_count=len(lines), has_raw=has_raw))

    return {"files": files}


@router.get("/records/{record_id}")
async def get_telemetry_record(
    request: Request,
    record_id: str,
    source: str = "protected",
    user: UserResponse = Depends(require_admin),
):
    """Get a single telemetry record by ID."""
    telemetry = _get_telemetry_or_fail(request)

    for date in _dates(telemetry, source):
        for _line, record in _iter_day(telemetry, date, source):
            if isinstance(record, dict) and record.get("record_id") == record_id:
                return record

    raise HTTPException(status_code=404, detail=f"Record {record_id} not found")


@router.get("/records")
async def get_telemetry_records(
    request: Request,
    date: str,
    source: str = "protected",
    offset: int = 0,
    limit: int = 50,
    user: UserResponse = Depends(require_admin),
):
    """Get paginated telemetry records for a specific date."""
    telemetry = _get_telemetry_or_fail(request)
    _check_date(date)

    records: list[dict] = []
    total = 0
    for _line, record in _iter_day(telemetry, date, source):
        total += 1
        if total > offset and len(records) < limit and record is not None:
            records.append(record)

    if total == 0:
        raise HTTPException(status_code=404, detail=f"No telemetry file for {date} ({source})")

    return {"records": records, "total": total, "has_more": (offset + limit) < total}


@router.post("/search")
async def search_telemetry_records(
    request: Request,
    search: TelemetrySearchRequest,
    user: UserResponse = Depends(require_admin),
):
    """Search telemetry records with filters across date range."""
    telemetry = _get_telemetry_or_fail(request)

    results: list[dict] = []

    for date in _dates_in_range(telemetry, "protected", search.date_from, search.date_to):
        if len(results) >= search.limit:
            break
        for _line, record in _iter_day(telemetry, date, "protected"):
            if len(results) >= search.limit:
                break
            if record is None:
                continue

            # Apply filters
            if search.session_id and record.get("session_id") != search.session_id:
                continue
            if search.model and record.get("model") != search.model:
                continue
            if search.min_latency is not None:
                latency = record.get("latency_ms") or record.get("duration_ms") or 0
                if latency < search.min_latency:
                    continue
            if search.query_text:
                prompt = record.get("prompt", "") or record.get("query", "") or ""
                if search.query_text.lower() not in prompt.lower():
                    continue

            results.append(record)

    return {"records": results, "total": len(results)}


@router.get("/stats", response_model=TelemetryStats)
async def get_telemetry_stats(
    request: Request,
    range: str = "7d",
    user: UserResponse = Depends(require_admin),
):
    """Get aggregated telemetry statistics."""
    telemetry = _get_telemetry_or_fail(request)

    # Determine date cutoff
    today = datetime.utcnow().date()
    if range == "30d":
        cutoff = (today - timedelta(days=30)).isoformat()
    elif range == "all":
        cutoff = None
    else:  # default 7d
        cutoff = (today - timedelta(days=7)).isoformat()

    records_count = 0
    total_latency = 0
    latency_count = 0
    total_tokens = 0
    error_count = 0
    model_distribution: dict[str, int] = {}

    for date in _dates_in_range(telemetry, "protected", cutoff, None):
        for _line, record in _iter_day(telemetry, date, "protected"):
            if record is None:
                continue

            records_count += 1

            # Latency
            latency = record.get("latency_ms") or record.get("duration_ms")
            if latency is not None:
                total_latency += latency
                latency_count += 1

            # Tokens
            tokens = record.get("total_tokens") or record.get("tokens") or 0
            total_tokens += tokens

            # Errors
            if record.get("error") or record.get("status") == "error":
                error_count += 1

            # Model distribution
            model = record.get("model")
            if model:
                model_distribution[model] = model_distribution.get(model, 0) + 1

    avg_latency = (total_latency / latency_count) if latency_count > 0 else None
    error_rate = (error_count / records_count) if records_count > 0 else 0.0

    return TelemetryStats(
        range=range,
        records_count=records_count,
        avg_latency_ms=round(avg_latency, 2) if avg_latency is not None else None,
        total_tokens=total_tokens,
        error_rate=round(error_rate, 4),
        model_distribution=model_distribution,
    )
