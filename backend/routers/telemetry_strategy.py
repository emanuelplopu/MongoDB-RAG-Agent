"""Strategy OS telemetry endpoints for the Electron viewer.

Read-only, admin-only. Returns data from:

* ``strategy_runs`` collection (per-run trace docs).
* ``strategy_llm_calls`` collection (per-LLM-invocation docs).
* ``adaptive_selection_decisions`` collection (adaptive routing decisions).

Repo rule #3: this module uses ``pymongo.AsyncMongoClient`` collection
APIs (``await coll.find(...).to_list(...)``). No Motor.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.routers.auth import UserResponse, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/telemetry/strategy",
    tags=["admin-telemetry-strategy"],
)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TopStrategy(BaseModel):
    """Aggregate count of runs grouped by strategy_id."""

    strategy_id: str
    count: int


class StrategyStats(BaseModel):
    """Aggregate stats over a time range."""

    range: str
    total_runs: int = 0
    success_rate: float = 0.0
    avg_duration_ms: float = 0.0
    avg_llm_calls: float = 0.0
    top_strategies: list[TopStrategy] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


_RUNS_COLLECTION = "strategy_runs"
_LLM_CALLS_COLLECTION = "strategy_llm_calls"
_DECISIONS_COLLECTION = "adaptive_selection_decisions"


def _resolve_db(request: Request) -> Any:
    """Return the active async MongoDB database handle, or ``None``.

    Supports both the project's :class:`DatabaseManager` wrapper (which
    exposes ``.db`` as the underlying async database) and a raw async
    database handle on ``app.state.db``.
    """

    db_state = getattr(request.app.state, "db", None)
    if db_state is None:
        return None
    return getattr(db_state, "db", db_state)


def _get_collection(request: Request, name: str) -> Any:
    """Return the requested collection or raise 503 when DB is missing."""

    db = _resolve_db(request)
    if db is None:
        raise HTTPException(
            status_code=503,
            detail="Database not initialized",
        )
    try:
        return db[name]
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to access collection %s: %s", name, exc)
        raise HTTPException(
            status_code=503,
            detail=f"Collection {name} not available",
        ) from exc


def _parse_range_to_cutoff(range_value: str) -> datetime:
    """Translate a human range token (``7d``, ``30d``, ``24h``) to a cutoff."""

    now = datetime.now(timezone.utc)
    mapping = {
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    delta = mapping.get(range_value)
    if delta is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid range. Use one of: 24h, 7d, 30d",
        )
    return now - delta


def _build_time_filter(
    field: str,
    since: Optional[datetime],
    until: Optional[datetime],
) -> dict[str, Any]:
    """Build a Mongo time-range filter clause for ``field``."""

    bounds: dict[str, Any] = {}
    if since is not None:
        bounds["$gte"] = since
    if until is not None:
        bounds["$lte"] = until
    return {field: bounds} if bounds else {}


def _serialize_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Drop Mongo internals and stringify datetimes for JSON output."""

    if not isinstance(doc, dict):
        return doc
    out = dict(doc)
    out.pop("_id", None)
    for key, value in list(out.items()):
        if isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/runs")
async def list_strategy_runs(
    request: Request,
    since: Optional[datetime] = Query(default=None),
    until: Optional[datetime] = Query(default=None),
    strategy_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: UserResponse = Depends(require_admin),
) -> dict[str, Any]:
    """List strategy run trace documents (most recent first).

    When ``since`` is omitted the window defaults to the last 7 days so
    that the viewer's first paint is bounded.
    """

    if since is None and until is None:
        since = datetime.now(timezone.utc) - timedelta(days=7)

    query: dict[str, Any] = {}
    query.update(_build_time_filter("started_at", since, until))
    if strategy_id:
        query["strategy_id"] = strategy_id
    if status:
        query["status"] = status

    try:
        collection = _get_collection(request, _RUNS_COLLECTION)
        cursor = collection.find(query).sort("started_at", -1).skip(offset).limit(limit)
        docs = await cursor.to_list(length=limit)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to list strategy runs: %s", exc)
        return {"runs": [], "total": 0, "limit": limit, "offset": offset}

    runs = [_serialize_doc(doc) for doc in docs]
    return {
        "runs": runs,
        "total": len(runs),
        "limit": limit,
        "offset": offset,
    }


@router.get("/runs/{trace_id}")
async def get_strategy_run(
    request: Request,
    trace_id: str,
    user: UserResponse = Depends(require_admin),
) -> dict[str, Any]:
    """Return a single strategy run by ``trace_id``."""

    collection = _get_collection(request, _RUNS_COLLECTION)
    try:
        doc = await collection.find_one({"trace_id": trace_id})
    except Exception as exc:
        logger.warning("Failed to fetch strategy run %s: %s", trace_id, exc)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch strategy run",
        ) from exc

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"Strategy run {trace_id} not found",
        )
    return _serialize_doc(doc)


@router.get("/runs/{trace_id}/llm-calls")
async def list_strategy_llm_calls(
    request: Request,
    trace_id: str,
    user: UserResponse = Depends(require_admin),
) -> dict[str, Any]:
    """Return all LLM call docs for a given trace, in execution order."""

    try:
        collection = _get_collection(request, _LLM_CALLS_COLLECTION)
        cursor = collection.find({"trace_id": trace_id}).sort("created_at", 1)
        docs = await cursor.to_list(length=None)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "Failed to fetch llm-calls for trace %s: %s", trace_id, exc
        )
        return {"trace_id": trace_id, "calls": [], "total": 0}

    calls = [_serialize_doc(doc) for doc in docs]
    return {"trace_id": trace_id, "calls": calls, "total": len(calls)}


@router.get("/decisions")
async def list_adaptive_decisions(
    request: Request,
    since: Optional[datetime] = Query(default=None),
    until: Optional[datetime] = Query(default=None),
    capability_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: UserResponse = Depends(require_admin),
) -> dict[str, Any]:
    """List adaptive selector decisions (most recent first)."""

    if since is None and until is None:
        since = datetime.now(timezone.utc) - timedelta(days=7)

    query: dict[str, Any] = {}
    query.update(_build_time_filter("decided_at", since, until))
    if capability_id:
        query["capability_id"] = capability_id

    try:
        collection = _get_collection(request, _DECISIONS_COLLECTION)
        cursor = (
            collection.find(query)
            .sort("decided_at", -1)
            .skip(offset)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to list adaptive decisions: %s", exc)
        return {"decisions": [], "total": 0, "limit": limit, "offset": offset}

    decisions = [_serialize_doc(doc) for doc in docs]
    return {
        "decisions": decisions,
        "total": len(decisions),
        "limit": limit,
        "offset": offset,
    }


@router.get("/stats", response_model=StrategyStats)
async def get_strategy_stats(
    request: Request,
    range: str = Query(default="7d"),
    user: UserResponse = Depends(require_admin),
) -> StrategyStats:
    """Return aggregate strategy run stats over the requested window."""

    cutoff = _parse_range_to_cutoff(range)
    query = {"started_at": {"$gte": cutoff}}

    total = 0
    success = 0
    duration_sum = 0.0
    duration_count = 0
    llm_call_sum = 0
    strategy_counts: dict[str, int] = {}

    try:
        collection = _get_collection(request, _RUNS_COLLECTION)
        cursor = collection.find(query)
        docs = await cursor.to_list(length=None)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to compute strategy stats: %s", exc)
        return StrategyStats(range=range)

    for doc in docs:
        total += 1
        if doc.get("status") == "completed":
            success += 1

        duration = doc.get("duration_ms")
        if isinstance(duration, (int, float)):
            duration_sum += float(duration)
            duration_count += 1

        # Prefer an explicit count field when present; otherwise fall
        # back to the length of the embedded ``llm_call_ids`` list.
        llm_count = doc.get("llm_call_count")
        if not isinstance(llm_count, (int, float)):
            ids = doc.get("llm_call_ids")
            llm_count = len(ids) if isinstance(ids, list) else 0
        llm_call_sum += int(llm_count)

        sid = doc.get("strategy_id")
        if isinstance(sid, str) and sid:
            strategy_counts[sid] = strategy_counts.get(sid, 0) + 1

    success_rate = (success / total) if total else 0.0
    avg_duration_ms = (duration_sum / duration_count) if duration_count else 0.0
    avg_llm_calls = (llm_call_sum / total) if total else 0.0

    top_strategies = [
        TopStrategy(strategy_id=sid, count=count)
        for sid, count in sorted(
            strategy_counts.items(), key=lambda item: item[1], reverse=True
        )[:5]
    ]

    return StrategyStats(
        range=range,
        total_runs=total,
        success_rate=round(success_rate, 4),
        avg_duration_ms=round(avg_duration_ms, 2),
        avg_llm_calls=round(avg_llm_calls, 2),
        top_strategies=top_strategies,
    )
