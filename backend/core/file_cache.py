"""
Cloud File Cache Manager

Manages a local file cache for cloud source documents with:
- LFU (Least Frequently Used) eviction policy
- Configurable cache size per connection
- Persistent access tracking across restarts
"""

import os
import json
import logging
import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, AsyncIterator
from dataclasses import dataclass, asdict
from collections import defaultdict

from bson import ObjectId

logger = logging.getLogger(__name__)

# Default cache settings
DEFAULT_CACHE_SIZE_MB = 1024  # 1 GB default
CACHE_METADATA_FILE = ".cache_metadata.json"
CACHE_BASE_PATH = Path("./data/cloud_cache")


@dataclass
class CachedFileInfo:
    """Information about a cached file."""
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
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "CachedFileInfo":
        return cls(**data)


class CacheMetadata:
    """Persistent metadata store for cache tracking."""
    
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.metadata_file = cache_dir / CACHE_METADATA_FILE
        self.files: dict[str, CachedFileInfo] = {}
        self._lock = asyncio.Lock()
        self._load()
    
    def _load(self):
        """Load metadata from disk."""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for remote_id, file_data in data.get("files", {}).items():
                        self.files[remote_id] = CachedFileInfo.from_dict(file_data)
            except Exception as e:
                logger.warning(f"Failed to load cache metadata: {e}")
                self.files = {}
    
    async def _save(self):
        """Save metadata to disk."""
        async with self._lock:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "files": {rid: info.to_dict() for rid, info in self.files.items()},
                "updated_at": datetime.utcnow().isoformat(),
            }
            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
    
    async def add_file(self, info: CachedFileInfo):
        """Add or update a cached file entry."""
        self.files[info.remote_id] = info
        await self._save()
    
    async def remove_file(self, remote_id: str) -> Optional[CachedFileInfo]:
        """Remove a cached file entry."""
        info = self.files.pop(remote_id, None)
        if info:
            await self._save()
        return info
    
    async def update_access(self, remote_id: str):
        """Update access stats for a file."""
        if remote_id in self.files:
            info = self.files[remote_id]
            info.access_count += 1
            info.last_accessed_at = datetime.utcnow().isoformat()
            await self._save()
    
    def get_file(self, remote_id: str) -> Optional[CachedFileInfo]:
        """Get cached file info."""
        return self.files.get(remote_id)
    
    def get_total_size(self) -> int:
        """Get total cache size in bytes."""
        return sum(info.size_bytes for info in self.files.values())
    
    def get_lfu_candidates(self, count: int = 10) -> list[CachedFileInfo]:
        """Get least frequently used files for eviction."""
        sorted_files = sorted(
            self.files.values(),
            key=lambda x: (x.access_count, x.last_accessed_at)
        )
        return sorted_files[:count]


class CloudFileCache:
    """
    Manages local file caching for cloud source documents.
    
    Features:
    - Per-connection cache directories
    - Configurable size limits
    - LFU eviction when cache is full
    - Persistent access tracking
    """
    
    def __init__(self, db, base_path: Optional[Path] = None):
        self.db = db
        self.base_path = base_path or CACHE_BASE_PATH
        self._metadata_cache: dict[str, CacheMetadata] = {}
        self._download_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    
    def _get_cache_dir(self, connection_id: str) -> Path:
        """Get cache directory for a connection."""
        return self.base_path / connection_id
    
    def _get_metadata(self, connection_id: str) -> CacheMetadata:
        """Get or create metadata manager for a connection."""
        if connection_id not in self._metadata_cache:
            cache_dir = self._get_cache_dir(connection_id)
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._metadata_cache[connection_id] = CacheMetadata(cache_dir)
        return self._metadata_cache[connection_id]
    
    async def get_cache_size_limit(self, connection_id: str) -> int:
        """Get cache size limit for a connection in bytes."""
        connections = self.db["cloud_source_connections"]
        conn = await connections.find_one({"_id": ObjectId(connection_id)})
        if conn and conn.get("cache_size_mb"):
            return conn["cache_size_mb"] * 1024 * 1024
        return DEFAULT_CACHE_SIZE_MB * 1024 * 1024
    
    async def get_cached_file(
        self,
        connection_id: str,
        remote_id: str,
    ) -> Optional[CachedFileInfo]:
        """
        Get a cached file, returning None if not cached.
        Updates access stats if file exists.
        """
        metadata = self._get_metadata(connection_id)
        info = metadata.get_file(remote_id)
        
        if info:
            # Verify file still exists
            local_path = Path(info.local_path)
            if local_path.exists():
                await metadata.update_access(remote_id)
                return info
            else:
                # File was deleted externally, clean up metadata
                await metadata.remove_file(remote_id)
        
        return None
    
    async def cache_file(
        self,
        connection_id: str,
        remote_id: str,
        remote_path: str,
        file_name: str,
        content_stream: AsyncIterator[bytes],
        mime_type: str = "application/octet-stream",
        web_view_url: Optional[str] = None,
    ) -> CachedFileInfo:
        """
        Cache a file from a cloud source.
        Performs LFU eviction if cache is full.
        """
        async with self._download_locks[f"{connection_id}:{remote_id}"]:
            # Check if already cached
            existing = await self.get_cached_file(connection_id, remote_id)
            if existing:
                return existing
            
            metadata = self._get_metadata(connection_id)
            cache_dir = self._get_cache_dir(connection_id)
            
            # Generate unique local path
            # Use remote_id as directory to handle filename collisions
            file_dir = cache_dir / remote_id[:8]
            file_dir.mkdir(parents=True, exist_ok=True)
            local_path = file_dir / file_name
            
            # Download file
            size_bytes = 0
            with open(local_path, "wb") as f:
                async for chunk in content_stream:
                    f.write(chunk)
                    size_bytes += len(chunk)
            
            # Check cache size and evict if needed
            await self._ensure_cache_space(connection_id, size_bytes)
            
            # Create metadata entry
            now = datetime.utcnow().isoformat()
            info = CachedFileInfo(
                remote_id=remote_id,
                remote_path=remote_path,
                local_path=str(local_path),
                file_name=file_name,
                size_bytes=size_bytes,
                mime_type=mime_type,
                cached_at=now,
                last_accessed_at=now,
                access_count=1,
                web_view_url=web_view_url,
            )
            
            await metadata.add_file(info)
            logger.info(f"Cached file: {file_name} ({size_bytes} bytes)")
            
            return info
    
    async def _ensure_cache_space(self, connection_id: str, needed_bytes: int):
        """Evict files using LFU until there's enough space."""
        metadata = self._get_metadata(connection_id)
        cache_limit = await self.get_cache_size_limit(connection_id)
        current_size = metadata.get_total_size()
        
        # Check if we need to evict
        if current_size + needed_bytes <= cache_limit:
            return
        
        # Calculate how much space we need to free
        # Free 20% extra to avoid constant eviction
        target_size = int(cache_limit * 0.8)
        bytes_to_free = current_size + needed_bytes - target_size
        
        if bytes_to_free <= 0:
            return
        
        logger.info(f"Cache eviction needed: freeing {bytes_to_free} bytes")
        
        # Get LFU candidates
        candidates = metadata.get_lfu_candidates(count=50)
        freed = 0
        
        for candidate in candidates:
            if freed >= bytes_to_free:
                break
            
            try:
                # Delete file
                local_path = Path(candidate.local_path)
                if local_path.exists():
                    local_path.unlink()
                
                # Clean up empty directories
                parent = local_path.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
                
                # Remove from metadata
                await metadata.remove_file(candidate.remote_id)
                freed += candidate.size_bytes
                
                logger.debug(f"Evicted: {candidate.file_name} ({candidate.size_bytes} bytes)")
                
            except Exception as e:
                logger.warning(f"Failed to evict {candidate.file_name}: {e}")
        
        logger.info(f"Cache eviction complete: freed {freed} bytes")
    
    async def get_or_download(
        self,
        connection_id: str,
        document_id: str,
    ) -> Optional[CachedFileInfo]:
        """
        Get a cached file or download it from the cloud source.
        
        Args:
            connection_id: Connection ID
            document_id: Document ID in the database
            
        Returns:
            CachedFileInfo if successful, None if failed
        """
        # Get document info
        documents = self.db["documents"]
        doc = await documents.find_one({"_id": ObjectId(document_id)})
        
        if not doc or "cloud_source" not in doc:
            logger.warning(f"Document {document_id} has no cloud source info")
            return None
        
        cloud_source = doc["cloud_source"]
        remote_id = cloud_source.get("remote_id")
        
        if not remote_id:
            return None
        
        # Check cache first
        cached = await self.get_cached_file(connection_id, remote_id)
        if cached:
            return cached
        
        # Download from cloud
        from backend.providers.registry import create_provider
        from backend.providers.base import ConnectionCredentials, OAuthTokens, AuthType
        from backend.routers.cloud_sources.schemas import ProviderType
        
        # Get connection
        connections = self.db["cloud_source_connections"]
        conn = await connections.find_one({"_id": ObjectId(connection_id)})
        
        if not conn:
            logger.error(f"Connection {connection_id} not found")
            return None
        
        try:
            # Create provider and authenticate
            provider_type = ProviderType(conn["provider"])
            provider = create_provider(provider_type)
            
            # Load credentials
            creds = conn.get("credentials", {})
            auth_type = AuthType(conn.get("auth_type", "oauth2"))
            
            if auth_type == AuthType.OAUTH2:
                oauth_meta = conn.get("oauth_metadata", {})
                credentials = ConnectionCredentials(
                    auth_type=auth_type,
                    oauth_tokens=OAuthTokens(
                        access_token=creds.get("access_token", ""),
                        refresh_token=creds.get("refresh_token"),
                        expires_at=oauth_meta.get("expires_at"),
                    ),
                )
            else:
                credentials = ConnectionCredentials(
                    auth_type=auth_type,
                    server_url=conn.get("server_url"),
                    username=creds.get("username"),
                    password=creds.get("password"),
                    api_key=creds.get("api_key"),
                    app_token=creds.get("app_token"),
                )
            
            await provider.authenticate(credentials)
            
            # Download and cache
            file_name = doc.get("file_name", cloud_source.get("remote_path", "file").split("/")[-1])
            mime_type = doc.get("mime_type", "application/octet-stream")
            
            info = await self.cache_file(
                connection_id=connection_id,
                remote_id=remote_id,
                remote_path=cloud_source.get("remote_path", ""),
                file_name=file_name,
                content_stream=provider.download_file(remote_id),
                mime_type=mime_type,
                web_view_url=cloud_source.get("web_view_url"),
            )
            
            await provider.close()
            return info
            
        except Exception as e:
            logger.error(f"Failed to download file for caching: {e}")
            return None
    
    async def get_cache_stats(self, connection_id: str) -> dict:
        """Get cache statistics for a connection."""
        metadata = self._get_metadata(connection_id)
        cache_limit = await self.get_cache_size_limit(connection_id)
        current_size = metadata.get_total_size()
        
        return {
            "connection_id": connection_id,
            "cache_dir": str(self._get_cache_dir(connection_id)),
            "total_files": len(metadata.files),
            "total_size_bytes": current_size,
            "cache_limit_bytes": cache_limit,
            "usage_percent": round(current_size / cache_limit * 100, 1) if cache_limit > 0 else 0,
            "files": [
                {
                    "remote_id": info.remote_id,
                    "file_name": info.file_name,
                    "size_bytes": info.size_bytes,
                    "access_count": info.access_count,
                    "last_accessed_at": info.last_accessed_at,
                }
                for info in sorted(
                    metadata.files.values(),
                    key=lambda x: x.access_count,
                    reverse=True
                )[:20]  # Top 20 most accessed
            ],
        }
    
    async def clear_cache(self, connection_id: str):
        """Clear all cached files for a connection."""
        cache_dir = self._get_cache_dir(connection_id)
        
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Reset metadata
        if connection_id in self._metadata_cache:
            del self._metadata_cache[connection_id]
        
        logger.info(f"Cleared cache for connection {connection_id}")


# Global cache instance
_file_cache: Optional[CloudFileCache] = None


def get_file_cache(db) -> CloudFileCache:
    """Get or create the global file cache manager."""
    global _file_cache
    if _file_cache is None:
        _file_cache = CloudFileCache(db)
    return _file_cache
