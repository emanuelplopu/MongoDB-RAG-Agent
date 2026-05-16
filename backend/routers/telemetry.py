"""Admin telemetry management endpoints.

Includes remote viewer API for the Electron telemetry viewer app.
CORS middleware is configured in backend/main.py.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, Request, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

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


def _date_range_files(storage_path: Path, date_from: Optional[str], date_to: Optional[str]) -> list[Path]:
    """Return sorted JSONL files within the given date range."""
    files = []
    for filepath in sorted(storage_path.glob("*.jsonl"), reverse=True):
        date_str = filepath.stem
        if date_from and date_str < date_from:
            continue
        if date_to and date_str > date_to:
            continue
        files.append(filepath)
    return files


@router.get("/files")
async def list_telemetry_files(
    request: Request, user: UserResponse = Depends(require_admin)
):
    """List available telemetry log files with metadata."""
    telemetry = _get_telemetry_or_fail(request)
    storage_path: Path = telemetry._storage_path

    files: list[TelemetryFileInfo] = []
    for filepath in sorted(storage_path.glob("*.jsonl"), reverse=True):
        date = filepath.stem  # YYYY-MM-DD
        size = filepath.stat().st_size
        # Count non-empty lines (records)
        count = 0
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        # Check if raw version exists
        raw_path = storage_path / "raw" / filepath.name
        has_raw = raw_path.exists()
        files.append(TelemetryFileInfo(date=date, size_bytes=size, record_count=count, has_raw=has_raw))

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
    storage_path: Path = telemetry._storage_path

    search_dir = storage_path / "raw" if source == "raw" else storage_path
    if not search_dir.exists():
        raise HTTPException(status_code=404, detail=f"Record {record_id} not found")

    for filepath in search_dir.glob("*.jsonl"):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if record.get("record_id") == record_id:
                        return record
                except json.JSONDecodeError:
                    continue

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
    storage_path: Path = telemetry._storage_path

    if source == "raw":
        filepath = storage_path / "raw" / f"{date}.jsonl"
    else:
        filepath = storage_path / f"{date}.jsonl"

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"No telemetry file for {date} ({source})")

    records: list[dict] = []
    total = 0
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            total += 1
            if total > offset and len(records) < limit:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return {"records": records, "total": total, "has_more": (offset + limit) < total}


@router.post("/search")
async def search_telemetry_records(
    request: Request,
    search: TelemetrySearchRequest,
    user: UserResponse = Depends(require_admin),
):
    """Search telemetry records with filters across date range."""
    telemetry = _get_telemetry_or_fail(request)
    storage_path: Path = telemetry._storage_path

    files = _date_range_files(storage_path, search.date_from, search.date_to)
    results: list[dict] = []

    for filepath in files:
        if len(results) >= search.limit:
            break
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                if len(results) >= search.limit:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
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
    storage_path: Path = telemetry._storage_path

    # Determine date cutoff
    today = datetime.utcnow().date()
    if range == "30d":
        cutoff = (today - timedelta(days=30)).isoformat()
    elif range == "all":
        cutoff = None
    else:  # default 7d
        cutoff = (today - timedelta(days=7)).isoformat()

    files = _date_range_files(storage_path, cutoff, None)

    records_count = 0
    total_latency = 0
    latency_count = 0
    total_tokens = 0
    error_count = 0
    model_distribution: dict[str, int] = {}

    for filepath in files:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
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
