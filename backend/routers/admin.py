"""
RecallHub Admin Panel API Routes
================================
Administrative endpoints for system management, configuration, and monitoring.
These routes are protected and only accessible to admin users.
"""

import os
import sys
import json
import psutil
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field

from backend.core.config import settings
from backend.core.database import get_database
from backend.routers.auth import get_current_user, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


# =============================================================================
# Pydantic Models
# =============================================================================

class SystemHealth(BaseModel):
    """System health information."""
    status: str
    uptime_seconds: float
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    mongodb_status: str
    ollama_status: str
    services: Dict[str, str]


class ConfigUpdate(BaseModel):
    """Configuration update request."""
    key: str
    value: Any
    description: Optional[str] = None


class AdminConfig(BaseModel):
    """Admin configuration model."""
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    embedding_provider: Optional[str] = None
    embedding_model: Optional[str] = None
    enable_cloud_sources: Optional[bool] = None
    enable_remote_access: Optional[bool] = None
    backup_retention_days: Optional[int] = None
    log_level: Optional[str] = None


class LogEntry(BaseModel):
    """Log entry model."""
    timestamp: datetime
    level: str
    message: str
    logger: Optional[str] = None


class BackupInfo(BaseModel):
    """Backup information model."""
    id: str
    filename: str
    created_at: datetime
    size_bytes: int
    components: List[str]
    checksum: Optional[str] = None


class BackupCreateRequest(BaseModel):
    """Backup creation request."""
    components: List[str] = Field(default=["database", "documents", "config"])
    password: Optional[str] = None
    description: Optional[str] = None


class UpdateInfo(BaseModel):
    """Update package information."""
    id: str
    filename: str
    version: str
    detected_at: datetime
    size_bytes: int
    signed: bool


class UpdateResult(BaseModel):
    """Update result model."""
    success: bool
    message: str
    previous_version: Optional[str] = None
    new_version: Optional[str] = None


class SystemVersion(BaseModel):
    """System version information."""
    version: str
    installed_at: datetime
    previous_version: Optional[str] = None
    update_history: List[Dict[str, Any]] = []


# =============================================================================
# Dashboard & Health Endpoints
# =============================================================================

@router.get("/dashboard", response_model=SystemHealth)
async def admin_dashboard(current_user: dict = Depends(require_admin)):
    """
    Get admin dashboard with system health information.
    
    Returns comprehensive system status including:
    - CPU, memory, and disk usage
    - Service health status
    - Database connectivity
    - LLM service status
    """
    try:
        # Get system metrics
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Calculate uptime
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = (datetime.now() - boot_time).total_seconds()
        
        # Check MongoDB status
        try:
            db = await get_database()
            await db.command("ping")
            mongodb_status = "healthy"
        except Exception as e:
            mongodb_status = f"error: {str(e)[:50]}"
        
        # Check Ollama status
        ollama_status = await _check_ollama_status()
        
        # Check individual services
        services = {
            "mongodb": mongodb_status,
            "ollama": ollama_status,
            "backend": "healthy",  # We're running, so backend is healthy
        }
        
        return SystemHealth(
            status="healthy" if all(s == "healthy" for s in services.values()) else "degraded",
            uptime_seconds=uptime,
            cpu_percent=cpu_percent,
            memory_percent=memory.percent,
            disk_percent=disk.percent,
            mongodb_status=mongodb_status,
            ollama_status=ollama_status,
            services=services
        )
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health/detailed")
async def detailed_health_check(current_user: dict = Depends(require_admin)):
    """Get detailed health check for all components."""
    checks = {}
    
    # MongoDB check
    try:
        db = await get_database()
        result = await db.command("serverStatus")
        checks["mongodb"] = {
            "status": "healthy",
            "version": result.get("version", "unknown"),
            "connections": result.get("connections", {}).get("current", 0)
        }
    except Exception as e:
        checks["mongodb"] = {"status": "error", "error": str(e)}
    
    # Ollama check
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{settings.LLM_BASE_URL.replace('/v1', '')}/api/tags", timeout=5.0)
            if response.status_code == 200:
                data = response.json()
                checks["ollama"] = {
                    "status": "healthy",
                    "models": [m["name"] for m in data.get("models", [])]
                }
            else:
                checks["ollama"] = {"status": "error", "error": f"HTTP {response.status_code}"}
    except Exception as e:
        checks["ollama"] = {"status": "unavailable", "error": str(e)}
    
    # Disk space check
    disk = psutil.disk_usage('/')
    checks["disk"] = {
        "status": "healthy" if disk.percent < 90 else "warning",
        "total_gb": round(disk.total / (1024**3), 2),
        "used_gb": round(disk.used / (1024**3), 2),
        "free_gb": round(disk.free / (1024**3), 2),
        "percent_used": disk.percent
    }
    
    # Memory check
    memory = psutil.virtual_memory()
    checks["memory"] = {
        "status": "healthy" if memory.percent < 90 else "warning",
        "total_gb": round(memory.total / (1024**3), 2),
        "used_gb": round(memory.used / (1024**3), 2),
        "available_gb": round(memory.available / (1024**3), 2),
        "percent_used": memory.percent
    }
    
    return checks


# =============================================================================
# Configuration Endpoints
# =============================================================================

@router.get("/config")
async def get_config(current_user: dict = Depends(require_admin)):
    """Get current system configuration (sensitive values redacted)."""
    config = {
        "app_env": os.getenv("APP_ENV", "development"),
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": os.getenv("LLM_MODEL", "unknown"),
        "embedding_provider": settings.EMBEDDING_PROVIDER,
        "embedding_model": os.getenv("EMBEDDING_MODEL", "unknown"),
        "enable_cloud_sources": os.getenv("ENABLE_CLOUD_SOURCES", "true").lower() == "true",
        "enable_remote_access": os.getenv("ENABLE_REMOTE_ACCESS", "false").lower() == "true",
        "backup_retention_days": int(os.getenv("BACKUP_RETENTION_DAYS", "30")),
        "log_level": os.getenv("APP_LOG_LEVEL", "INFO"),
        "expose_api_docs": os.getenv("EXPOSE_API_DOCS", "true").lower() == "true",
    }
    return config


@router.post("/config")
async def update_config(
    config: AdminConfig,
    current_user: dict = Depends(require_admin)
):
    """
    Update system configuration.
    
    Note: Some changes may require a service restart to take effect.
    """
    updates = {}
    restart_required = False
    
    if config.llm_provider is not None:
        updates["LLM_PROVIDER"] = config.llm_provider
        restart_required = True
    
    if config.llm_model is not None:
        updates["LLM_MODEL"] = config.llm_model
    
    if config.embedding_provider is not None:
        updates["EMBEDDING_PROVIDER"] = config.embedding_provider
        restart_required = True
    
    if config.embedding_model is not None:
        updates["EMBEDDING_MODEL"] = config.embedding_model
    
    if config.enable_cloud_sources is not None:
        updates["ENABLE_CLOUD_SOURCES"] = str(config.enable_cloud_sources).lower()
    
    if config.enable_remote_access is not None:
        updates["ENABLE_REMOTE_ACCESS"] = str(config.enable_remote_access).lower()
    
    if config.backup_retention_days is not None:
        updates["BACKUP_RETENTION_DAYS"] = str(config.backup_retention_days)
    
    if config.log_level is not None:
        updates["APP_LOG_LEVEL"] = config.log_level
        logging.getLogger().setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
    
    # Store configuration updates
    try:
        db = await get_database()
        await db.system_config.update_one(
            {"_id": "config"},
            {"$set": {"values": updates, "updated_at": datetime.utcnow()}},
            upsert=True
        )
    except Exception as e:
        logger.error(f"Failed to store config: {e}")
    
    return {
        "success": True,
        "updated_keys": list(updates.keys()),
        "restart_required": restart_required,
        "message": "Configuration updated. " + ("Restart required for some changes to take effect." if restart_required else "")
    }


# =============================================================================
# Logs Endpoints
# =============================================================================

@router.get("/logs", response_model=List[LogEntry])
async def get_logs(
    lines: int = Query(default=100, ge=1, le=1000),
    level: Optional[str] = Query(default=None),
    since: Optional[datetime] = Query(default=None),
    current_user: dict = Depends(require_admin)
):
    """
    Retrieve recent log entries.
    
    Args:
        lines: Number of log lines to retrieve (max 1000)
        level: Filter by log level (DEBUG, INFO, WARNING, ERROR)
        since: Only return logs after this timestamp
    """
    log_entries = []
    log_file = Path(os.getenv("LOG_FILE", "/app/logs/recallhub.log"))
    
    if not log_file.exists():
        # Try alternative log locations
        alt_paths = [
            Path("logs/recallhub.log"),
            Path("/var/log/recallhub.log"),
            Path.home() / ".recallhub" / "logs" / "recallhub.log"
        ]
        for alt_path in alt_paths:
            if alt_path.exists():
                log_file = alt_path
                break
    
    if log_file.exists():
        try:
            with open(log_file, 'r') as f:
                all_lines = f.readlines()
                recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
                
                for line in recent_lines:
                    try:
                        # Parse log line (assuming standard format)
                        entry = _parse_log_line(line)
                        if entry:
                            if level and entry.level != level.upper():
                                continue
                            if since and entry.timestamp < since:
                                continue
                            log_entries.append(entry)
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"Error reading log file: {e}")
    
    # Also get logs from database if stored there
    try:
        db = await get_database()
        query = {}
        if level:
            query["level"] = level.upper()
        if since:
            query["timestamp"] = {"$gte": since}
        
        db_logs = await db.logs.find(query).sort("timestamp", -1).limit(lines).to_list(lines)
        for log in db_logs:
            log_entries.append(LogEntry(
                timestamp=log.get("timestamp", datetime.utcnow()),
                level=log.get("level", "INFO"),
                message=log.get("message", ""),
                logger=log.get("logger")
            ))
    except Exception:
        pass
    
    # Sort by timestamp descending
    log_entries.sort(key=lambda x: x.timestamp, reverse=True)
    
    return log_entries[:lines]


@router.get("/logs/stream")
async def stream_logs(current_user: dict = Depends(require_admin)):
    """Stream logs in real-time using Server-Sent Events."""
    async def log_generator():
        log_file = Path(os.getenv("LOG_FILE", "/app/logs/recallhub.log"))
        
        if not log_file.exists():
            yield f"data: {json.dumps({'error': 'Log file not found'})}\n\n"
            return
        
        with open(log_file, 'r') as f:
            # Go to end of file
            f.seek(0, 2)
            
            while True:
                line = f.readline()
                if line:
                    entry = _parse_log_line(line)
                    if entry:
                        yield f"data: {json.dumps(entry.dict(), default=str)}\n\n"
                else:
                    await asyncio.sleep(0.5)
    
    return StreamingResponse(
        log_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    )


# =============================================================================
# User Management Endpoints
# =============================================================================

@router.get("/users")
async def list_users(current_user: dict = Depends(require_admin)):
    """List all users."""
    try:
        db = await get_database()
        users = await db.users.find({}, {"password_hash": 0}).to_list(100)
        
        return [
            {
                "id": str(user.get("_id")),
                "username": user.get("username"),
                "email": user.get("email"),
                "role": user.get("role", "user"),
                "created_at": user.get("created_at"),
                "last_login": user.get("last_login")
            }
            for user in users
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/role")
async def update_user_role(
    user_id: str,
    role: str = Query(..., regex="^(user|admin)$"),
    current_user: dict = Depends(require_admin)
):
    """Update a user's role."""
    from bson import ObjectId
    
    try:
        db = await get_database()
        result = await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"role": role, "updated_at": datetime.utcnow()}}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
        
        return {"success": True, "message": f"User role updated to {role}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Statistics Endpoints
# =============================================================================

@router.get("/stats")
async def get_statistics(current_user: dict = Depends(require_admin)):
    """Get system statistics."""
    try:
        db = await get_database()
        
        stats = {
            "documents": await db.files.count_documents({}),
            "chunks": await db.chunks.count_documents({}),
            "sessions": await db.sessions.count_documents({}),
            "users": await db.users.count_documents({}),
            "profiles": await db.profiles.count_documents({})
        }
        
        # Get storage statistics
        db_stats = await db.command("dbStats")
        stats["database_size_mb"] = round(db_stats.get("dataSize", 0) / (1024 * 1024), 2)
        stats["index_size_mb"] = round(db_stats.get("indexSize", 0) / (1024 * 1024), 2)
        
        # Get recent activity
        one_day_ago = datetime.utcnow() - timedelta(days=1)
        stats["sessions_last_24h"] = await db.sessions.count_documents({
            "created_at": {"$gte": one_day_ago}
        })
        stats["documents_last_24h"] = await db.files.count_documents({
            "created_at": {"$gte": one_day_ago}
        })
        
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Version Endpoints
# =============================================================================

@router.get("/version", response_model=SystemVersion)
async def get_version(current_user: dict = Depends(require_admin)):
    """Get current system version information."""
    try:
        db = await get_database()
        version_doc = await db.system_config.find_one({"_id": "system_version"})
        
        if version_doc:
            return SystemVersion(
                version=version_doc.get("version", "1.0.0"),
                installed_at=version_doc.get("installed_at", datetime.utcnow()),
                previous_version=version_doc.get("previous_version"),
                update_history=version_doc.get("update_history", [])
            )
        else:
            return SystemVersion(
                version="1.0.0",
                installed_at=datetime.utcnow(),
                update_history=[]
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Helper Functions
# =============================================================================

async def _check_ollama_status() -> str:
    """Check if Ollama service is available."""
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{settings.LLM_BASE_URL.replace('/v1', '')}/api/tags",
                timeout=5.0
            )
            return "healthy" if response.status_code == 200 else f"error: HTTP {response.status_code}"
    except Exception as e:
        return f"unavailable: {str(e)[:30]}"


def _parse_log_line(line: str) -> Optional[LogEntry]:
    """Parse a log line into a LogEntry object."""
    try:
        # Try JSON format first
        if line.strip().startswith('{'):
            data = json.loads(line)
            return LogEntry(
                timestamp=datetime.fromisoformat(data.get("timestamp", datetime.utcnow().isoformat())),
                level=data.get("level", "INFO"),
                message=data.get("message", line),
                logger=data.get("logger")
            )
        
        # Try standard log format: 2024-01-01 12:00:00,000 - INFO - message
        parts = line.split(" - ", 2)
        if len(parts) >= 3:
            timestamp_str = parts[0].strip()
            level = parts[1].strip()
            message = parts[2].strip()
            
            try:
                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S,%f")
            except ValueError:
                timestamp = datetime.utcnow()
            
            return LogEntry(
                timestamp=timestamp,
                level=level,
                message=message
            )
        
        # Fallback: return as-is
        return LogEntry(
            timestamp=datetime.utcnow(),
            level="INFO",
            message=line.strip()
        )
    except Exception:
        return None
