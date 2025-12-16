"""
Cloud Sources Cache API Router

Provides endpoints for managing the local file cache for cloud source documents.
"""

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from backend.routers.auth import get_current_user
from backend.core.file_cache import get_file_cache, CachedFileInfo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cache", tags=["Cloud Sources Cache"])


# ==================== Response Schemas ====================

class CachedFileResponse(BaseModel):
    """Cached file information."""
    remote_id: str
    remote_path: str
    local_path: str
    file_name: str
    size_bytes: int
    mime_type: str
    cached_at: str
    last_accessed_at: str
    access_count: int
    web_view_url: Optional[str] = None


class CacheStatsResponse(BaseModel):
    """Cache statistics."""
    connection_id: str
    cache_dir: str
    total_files: int
    total_size_bytes: int
    cache_limit_bytes: int
    usage_percent: float
    files: list[dict]


class CacheFileRequest(BaseModel):
    """Request to cache/get a file."""
    document_id: str
    connection_id: str


# ==================== Endpoints ====================

@router.post("/get-file", response_model=CachedFileResponse)
async def get_or_cache_file(
    request_data: CacheFileRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    Get a cached file or download it from the cloud source.
    
    If the file is not in cache, it will be downloaded and cached.
    Returns the cached file info including local path for preview.
    """
    db = request.app.state.db.db
    file_cache = get_file_cache(db)
    
    # Verify user owns this connection
    from bson import ObjectId
    connections = db["cloud_source_connections"]
    conn = await connections.find_one({
        "_id": ObjectId(request_data.connection_id),
        "user_id": current_user["id"],
    })
    
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    
    # Get or download file
    cached_info = await file_cache.get_or_download(
        connection_id=request_data.connection_id,
        document_id=request_data.document_id,
    )
    
    if not cached_info:
        raise HTTPException(
            status_code=404,
            detail="Failed to retrieve file from cloud source"
        )
    
    return CachedFileResponse(
        remote_id=cached_info.remote_id,
        remote_path=cached_info.remote_path,
        local_path=cached_info.local_path,
        file_name=cached_info.file_name,
        size_bytes=cached_info.size_bytes,
        mime_type=cached_info.mime_type,
        cached_at=cached_info.cached_at,
        last_accessed_at=cached_info.last_accessed_at,
        access_count=cached_info.access_count,
        web_view_url=cached_info.web_view_url,
    )


@router.get("/serve/{connection_id}/{document_id}")
async def serve_cached_file(
    connection_id: str,
    document_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    Serve a cached file for preview in the browser.
    
    If not cached, downloads and caches first.
    Returns the file directly with appropriate content type.
    """
    db = request.app.state.db.db
    file_cache = get_file_cache(db)
    
    # Verify user owns this connection
    from bson import ObjectId
    connections = db["cloud_source_connections"]
    conn = await connections.find_one({
        "_id": ObjectId(connection_id),
        "user_id": current_user["id"],
    })
    
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    
    # Get or download file
    cached_info = await file_cache.get_or_download(
        connection_id=connection_id,
        document_id=document_id,
    )
    
    if not cached_info:
        raise HTTPException(
            status_code=404,
            detail="Failed to retrieve file from cloud source"
        )
    
    local_path = Path(cached_info.local_path)
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="Cached file not found")
    
    return FileResponse(
        path=local_path,
        filename=cached_info.file_name,
        media_type=cached_info.mime_type,
    )


@router.get("/stats/{connection_id}", response_model=CacheStatsResponse)
async def get_cache_stats(
    connection_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    Get cache statistics for a connection.
    
    Returns total files, size, usage percentage, and most accessed files.
    """
    db = request.app.state.db.db
    file_cache = get_file_cache(db)
    
    # Verify user owns this connection
    from bson import ObjectId
    connections = db["cloud_source_connections"]
    conn = await connections.find_one({
        "_id": ObjectId(connection_id),
        "user_id": current_user["id"],
    })
    
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    
    stats = await file_cache.get_cache_stats(connection_id)
    
    return CacheStatsResponse(**stats)


@router.delete("/clear/{connection_id}")
async def clear_cache(
    connection_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    Clear all cached files for a connection.
    
    This removes all locally cached files but does not affect
    the indexed documents in MongoDB.
    """
    db = request.app.state.db.db
    file_cache = get_file_cache(db)
    
    # Verify user owns this connection
    from bson import ObjectId
    connections = db["cloud_source_connections"]
    conn = await connections.find_one({
        "_id": ObjectId(connection_id),
        "user_id": current_user["id"],
    })
    
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    
    await file_cache.clear_cache(connection_id)
    
    return {"message": "Cache cleared successfully"}


@router.get("/info/{document_id}")
async def get_cloud_source_info(
    document_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    Get cloud source information for a document.
    
    Returns provider info, web view URL, and cache status.
    """
    db = request.app.state.db.db
    
    from bson import ObjectId
    documents = db["documents"]
    
    doc = await documents.find_one({"_id": ObjectId(document_id)})
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    if "cloud_source" not in doc:
        return {
            "is_cloud_source": False,
            "document_id": document_id,
        }
    
    cloud_source = doc["cloud_source"]
    connection_id = cloud_source.get("connection_id")
    
    # Check if user owns the connection
    if connection_id:
        connections = db["cloud_source_connections"]
        conn = await connections.find_one({
            "_id": ObjectId(connection_id),
            "user_id": current_user["id"],
        })
        if not conn:
            raise HTTPException(status_code=403, detail="Access denied")
    
    # Check cache status
    file_cache = get_file_cache(db)
    cached = await file_cache.get_cached_file(
        connection_id=connection_id,
        remote_id=cloud_source.get("remote_id", ""),
    ) if connection_id else None
    
    return {
        "is_cloud_source": True,
        "document_id": document_id,
        "provider": cloud_source.get("provider"),
        "connection_id": connection_id,
        "remote_id": cloud_source.get("remote_id"),
        "remote_path": cloud_source.get("remote_path"),
        "web_view_url": cloud_source.get("web_view_url"),
        "synced_at": cloud_source.get("synced_at"),
        "is_cached": cached is not None,
        "cached_path": cached.local_path if cached else None,
        "cache_access_count": cached.access_count if cached else 0,
    }
