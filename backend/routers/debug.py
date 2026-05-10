"""Live debug endpoints for admin observability.

Provides real-time visibility into agent activity, system state,
and request details. Admin-only access.
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.core.config import settings
from backend.routers.auth import get_current_user, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/debug", tags=["debug"])

# In-memory registry of active requests
# Key: request_id, Value: dict with metadata
active_requests: Dict[str, dict] = {}

# App start time for uptime calculation
_app_start_time: float = time.time()


def require_admin(user: UserResponse = Depends(get_current_user)):
    """Dependency that requires the user to be an admin."""
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def register_active_request(request_id: str, session_id: str, user_id: str,
                            model: str, is_admin: bool):
    """Register a request as active (called from sessions.py)."""
    active_requests[request_id] = {
        "request_id": request_id,
        "session_id": session_id,
        "user_id": user_id,
        "model": model,
        "is_admin": is_admin,
        "started_at": datetime.utcnow().isoformat(),
        "started_ts": time.time(),
    }


def unregister_active_request(request_id: str):
    """Unregister a request (called when processing completes)."""
    active_requests.pop(request_id, None)


# --- Endpoints ---

@router.get("/activity/live")
async def get_live_activity(request: Request, user: UserResponse = Depends(require_admin)):
    """Get recent activity for live debug dashboard.

    Returns last 50 completed requests from the last 30 minutes.
    """
    db = request.app.state.db.db
    cutoff = datetime.utcnow() - timedelta(minutes=30)

    cursor = db["agent_activity_log"].find(
        {"started_at": {"$gte": cutoff}},
        {  # Project only summary fields for the list view
            "_id": 1,
            "session_id": 1,
            "user_id": 1,
            "is_admin": 1,
            "started_at": 1,
            "completed_at": 1,
            "duration_ms": 1,
            "summary": 1,
        }
    ).sort("started_at", -1).limit(50)

    results = []
    async for doc in cursor:
        doc["request_id"] = str(doc.pop("_id"))
        # Convert datetime objects to ISO strings
        if isinstance(doc.get("started_at"), datetime):
            doc["started_at"] = doc["started_at"].isoformat()
        if isinstance(doc.get("completed_at"), datetime):
            doc["completed_at"] = doc["completed_at"].isoformat()
        results.append(doc)

    return {"activity": results, "count": len(results)}


@router.get("/activity/active")
async def get_active_requests(request: Request, user: UserResponse = Depends(require_admin)):
    """Get currently in-progress requests.

    Returns requests that are currently being processed (not yet completed).
    """
    now = time.time()
    active_list = []
    for req_id, req_data in list(active_requests.items()):
        entry = {**req_data}
        entry["elapsed_seconds"] = round(now - req_data["started_ts"], 1)
        active_list.append(entry)

    # Sort by most recent first
    active_list.sort(key=lambda x: x["started_ts"], reverse=True)

    return {"active": active_list, "count": len(active_list)}


@router.get("/activity/{request_id}")
async def get_request_detail(
    request_id: str,
    request: Request,
    user: UserResponse = Depends(require_admin)
):
    """Get full verbose detail for a specific request.

    Returns all logged entries including full LLM prompts/responses for admin review.
    """
    db = request.app.state.db.db

    doc = await db["agent_activity_log"].find_one({"_id": request_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")

    doc["request_id"] = doc.pop("_id")
    # Convert datetime objects
    if isinstance(doc.get("started_at"), datetime):
        doc["started_at"] = doc["started_at"].isoformat()
    if isinstance(doc.get("completed_at"), datetime):
        doc["completed_at"] = doc["completed_at"].isoformat()
    for entry in doc.get("entries", []):
        if isinstance(entry.get("ts"), datetime):
            entry["ts"] = entry["ts"].isoformat()

    return doc


@router.get("/system-state")
async def get_system_state(request: Request, user: UserResponse = Depends(require_admin)):
    """Current system configuration snapshot.

    Returns live system state for the debug dashboard header.
    """
    # Get active profile name
    active_profile_name = "unknown"
    try:
        from src.profile import get_profile_manager
        pm = get_profile_manager(settings.profiles_path)
        active_profile_name = pm.active_profile_key or "default"
    except Exception:
        pass

    return {
        "orchestrator": f"{settings.orchestrator_provider}/{settings.orchestrator_model}",
        "worker": f"{settings.worker_provider}/{settings.worker_model}",
        "ollama_url": settings.ollama_base_url,
        "active_profile": active_profile_name,
        "database": settings.mongodb_database,
        "tenant_id": settings.tenant_id,
        "uptime_seconds": round(time.time() - _app_start_time, 1),
        "active_requests_count": len(active_requests),
        "timestamp": datetime.utcnow().isoformat(),
    }
