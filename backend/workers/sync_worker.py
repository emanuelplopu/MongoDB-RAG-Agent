"""
Cloud Source Sync Worker

Manages the synchronization of files from cloud sources into the
RecallHub document store. Integrates with the existing ingestion
pipeline for document processing.
"""

import logging
import asyncio
import os
import tempfile
from datetime import datetime
from typing import Optional, Callable, Any
from pathlib import Path

from bson import ObjectId

from backend.providers.base import (
    CloudSourceProvider,
    RemoteFile,
    SyncDelta,
    AuthenticationError,
    RateLimitError,
)
from backend.providers.registry import create_provider
from backend.core.credential_vault import decrypt_credentials
from backend.routers.cloud_sources.schemas import (
    ProviderType,
    AuthType,
    SyncJobStatus,
    SyncJobType,
)

logger = logging.getLogger(__name__)

# Collection names
CONNECTIONS_COLLECTION = "cloud_source_connections"
SYNC_CONFIGS_COLLECTION = "cloud_source_sync_configs"
SYNC_JOBS_COLLECTION = "cloud_source_sync_jobs"
SYNC_STATE_COLLECTION = "cloud_source_sync_state"


class SyncWorker:
    """
    Worker for syncing cloud source files.
    
    Handles:
    - Downloading files from cloud sources
    - Processing through ingestion pipeline
    - Tracking sync state and delta tokens
    - Error handling and retries
    """
    
    def __init__(self, db):
        self.db = db
        self._active_jobs: dict[str, dict] = {}
        self._stop_requested: set[str] = set()
    
    async def run_sync_job(
        self,
        job_id: str,
        config: dict,
        connection: dict,
        job_type: SyncJobType = SyncJobType.INCREMENTAL,
        progress_callback: Optional[Callable] = None
    ):
        """
        Execute a sync job.
        
        Args:
            job_id: Unique job identifier
            config: Sync configuration document
            connection: Connection document with credentials
            job_type: Type of sync (full or incremental)
            progress_callback: Optional callback for progress updates
        """
        jobs_collection = self.db[SYNC_JOBS_COLLECTION]
        state_collection = self.db[SYNC_STATE_COLLECTION]
        
        # Track active job
        self._active_jobs[job_id] = {
            "status": SyncJobStatus.RUNNING,
            "started_at": datetime.utcnow(),
        }
        
        provider: Optional[CloudSourceProvider] = None
        temp_dir: Optional[str] = None
        
        try:
            # Update job status to running
            await jobs_collection.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {
                    "status": SyncJobStatus.RUNNING.value,
                    "progress.phase": "initializing",
                }}
            )
            
            # Initialize provider
            provider_type = ProviderType(connection["provider"])
            provider = create_provider(provider_type)
            
            # Decrypt and load credentials
            credentials = await self._load_credentials(connection)
            await provider.authenticate(credentials)
            
            # Get sync state for incremental sync
            config_id = str(config["_id"])
            sync_state = await state_collection.find_one({"config_id": config_id})
            delta_token = sync_state.get("delta_token") if sync_state else None
            
            # Determine files to process
            if job_type == SyncJobType.FULL or not delta_token:
                # Full sync - list all files
                files_to_process = await self._list_all_files(
                    provider, config, progress_callback
                )
                files_to_delete = []
                new_delta_token = None
                
                # Try to get a delta token for future syncs
                if provider.capabilities.supports_delta_sync:
                    try:
                        delta = await provider.get_changes()
                        new_delta_token = delta.next_delta_token
                    except NotImplementedError:
                        pass
            else:
                # Incremental sync - get changes
                try:
                    delta = await provider.get_changes(delta_token)
                    files_to_process = delta.added + delta.modified
                    files_to_delete = delta.deleted
                    new_delta_token = delta.next_delta_token
                except Exception as e:
                    logger.warning(f"Delta sync failed, falling back to full sync: {e}")
                    files_to_process = await self._list_all_files(
                        provider, config, progress_callback
                    )
                    files_to_delete = []
                    new_delta_token = None
            
            # Update progress
            total_files = len(files_to_process)
            await jobs_collection.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {
                    "progress.phase": "processing",
                    "progress.files_discovered": total_files,
                }}
            )
            
            # Create temp directory for downloads
            temp_dir = tempfile.mkdtemp(prefix="cloud_sync_")
            
            # Process files
            processed = 0
            failed = 0
            skipped = 0
            errors = []
            
            for file in files_to_process:
                # Check for stop request
                if job_id in self._stop_requested:
                    logger.info(f"Sync job {job_id} cancelled by user")
                    raise asyncio.CancelledError("User cancelled")
                
                try:
                    # Check filters
                    if not self._matches_filters(file, config.get("filters", {})):
                        skipped += 1
                        continue
                    
                    # Download and process file
                    await self._process_file(
                        provider, file, config, temp_dir
                    )
                    processed += 1
                    
                except RateLimitError as e:
                    logger.warning(f"Rate limited, waiting {e.retry_after}s")
                    await asyncio.sleep(e.retry_after or 60)
                    # Retry this file
                    try:
                        await self._process_file(provider, file, config, temp_dir)
                        processed += 1
                    except Exception as retry_e:
                        failed += 1
                        errors.append({
                            "file_path": file.path,
                            "error_type": "retry_failed",
                            "message": str(retry_e),
                            "timestamp": datetime.utcnow().isoformat(),
                        })
                        
                except Exception as e:
                    logger.error(f"Failed to process {file.path}: {e}")
                    failed += 1
                    errors.append({
                        "file_path": file.path,
                        "error_type": type(e).__name__,
                        "message": str(e),
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                
                # Update progress
                await jobs_collection.update_one(
                    {"_id": ObjectId(job_id)},
                    {"$set": {
                        "progress.files_processed": processed,
                        "progress.files_failed": failed,
                        "progress.files_skipped": skipped,
                        "progress.current_file": file.name,
                    }}
                )
                
                if progress_callback:
                    await progress_callback(processed, total_files, file.name)
            
            # Handle deletions
            if files_to_delete and config.get("delete_removed", True):
                await self._handle_deletions(files_to_delete, config)
            
            # Save sync state
            if new_delta_token:
                await state_collection.update_one(
                    {"config_id": config_id},
                    {"$set": {
                        "delta_token": new_delta_token,
                        "last_sync_at": datetime.utcnow(),
                    }},
                    upsert=True
                )
            
            # Complete job
            now = datetime.utcnow()
            await jobs_collection.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {
                    "status": SyncJobStatus.COMPLETED.value,
                    "progress.phase": "completed",
                    "completed_at": now,
                    "errors": errors[:20],  # Keep max 20 errors
                }}
            )
            
            # Update config stats
            configs_collection = self.db[SYNC_CONFIGS_COLLECTION]
            await configs_collection.update_one(
                {"_id": config["_id"]},
                {"$set": {
                    "stats.last_sync_at": now,
                    "stats.last_sync_files_processed": processed,
                    "stats.total_files": processed + skipped,
                    "updated_at": now,
                }}
            )
            
            logger.info(
                f"Sync job {job_id} completed: "
                f"{processed} processed, {skipped} skipped, {failed} failed"
            )
            
        except asyncio.CancelledError:
            await jobs_collection.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {
                    "status": SyncJobStatus.CANCELLED.value,
                    "completed_at": datetime.utcnow(),
                }}
            )
            
        except AuthenticationError as e:
            logger.error(f"Authentication failed for job {job_id}: {e}")
            await jobs_collection.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {
                    "status": SyncJobStatus.FAILED.value,
                    "completed_at": datetime.utcnow(),
                    "errors": [{"error_type": "authentication", "message": str(e)}],
                }}
            )
            
        except Exception as e:
            logger.error(f"Sync job {job_id} failed: {e}")
            await jobs_collection.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {
                    "status": SyncJobStatus.FAILED.value,
                    "completed_at": datetime.utcnow(),
                    "errors": [{"error_type": type(e).__name__, "message": str(e)}],
                }}
            )
            
        finally:
            # Cleanup
            self._active_jobs.pop(job_id, None)
            self._stop_requested.discard(job_id)
            
            if provider:
                await provider.close()
            
            if temp_dir and os.path.exists(temp_dir):
                import shutil
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"Failed to cleanup temp dir: {e}")
    
    async def _load_credentials(self, connection: dict):
        """Load and decrypt credentials from connection document."""
        from backend.providers.base import ConnectionCredentials, OAuthTokens, AuthType
        
        creds = connection.get("credentials", {})
        auth_type = AuthType(connection.get("auth_type", "oauth2"))
        
        # TODO: Decrypt credentials using vault
        # For now, return raw credentials
        
        if auth_type == AuthType.OAUTH2:
            oauth_meta = connection.get("oauth_metadata", {})
            return ConnectionCredentials(
                auth_type=auth_type,
                oauth_tokens=OAuthTokens(
                    access_token=creds.get("access_token", ""),
                    refresh_token=creds.get("refresh_token"),
                    expires_at=oauth_meta.get("expires_at"),
                ),
            )
        else:
            return ConnectionCredentials(
                auth_type=auth_type,
                server_url=connection.get("server_url"),
                username=creds.get("username"),
                password=creds.get("password"),
                api_key=creds.get("api_key"),
                app_token=creds.get("app_token"),
            )
    
    async def _list_all_files(
        self,
        provider: CloudSourceProvider,
        config: dict,
        progress_callback: Optional[Callable]
    ) -> list[RemoteFile]:
        """List all files from configured source paths."""
        all_files = []
        
        for source_path in config.get("source_paths", []):
            folder_id = source_path.get("remote_id", source_path.get("path", "/"))
            recursive = source_path.get("include_subfolders", True)
            
            async for file in provider.list_all_files(folder_id, recursive=recursive):
                all_files.append(file)
        
        return all_files
    
    def _matches_filters(self, file: RemoteFile, filters: dict) -> bool:
        """Check if file matches sync filters."""
        # File type filter
        file_types = filters.get("file_types", [])
        if file_types:
            ext = file.name.rsplit(".", 1)[-1].lower() if "." in file.name else ""
            if ext not in [ft.lower().lstrip(".") for ft in file_types]:
                return False
        
        # Size filter
        max_size_mb = filters.get("max_file_size_mb", 100)
        if file.size_bytes > max_size_mb * 1024 * 1024:
            return False
        
        # Exclude patterns
        exclude_patterns = filters.get("exclude_patterns", [])
        for pattern in exclude_patterns:
            import fnmatch
            if fnmatch.fnmatch(file.path, pattern):
                return False
        
        # Modified after filter
        modified_after = filters.get("modified_after")
        if modified_after:
            if isinstance(modified_after, str):
                modified_after = datetime.fromisoformat(modified_after)
            if file.modified_at < modified_after:
                return False
        
        return True
    
    async def _process_file(
        self,
        provider: CloudSourceProvider,
        file: RemoteFile,
        config: dict,
        temp_dir: str
    ):
        """Download and process a single file through the ingestion pipeline."""
        # Download file
        file_path = Path(temp_dir) / file.name
        
        async with open(file_path, "wb") as f:
            async for chunk in provider.download_file(file.id):
                await f.write(chunk)
        
        # Process through ingestion pipeline
        from src.ingestion.ingest import DocumentIngestionPipeline, IngestionConfig
        from src.profile import get_profile_manager
        
        # Switch to target profile
        profile_key = config.get("profile_key", "default")
        pm = get_profile_manager()
        pm.switch_profile(profile_key)
        
        # Create mini-pipeline for single file
        ing_config = IngestionConfig()
        pipeline = DocumentIngestionPipeline(
            config=ing_config,
            use_profile=True
        )
        
        try:
            await pipeline.initialize()
            
            # Process the file
            result = await pipeline.ingest_file(str(file_path))
            
            # Add cloud source metadata
            if result:
                # Update document with source metadata
                documents_collection = pipeline.db[pipeline.settings.mongodb_collection_documents]
                await documents_collection.update_one(
                    {"_id": ObjectId(result.document_id)},
                    {"$set": {
                        "cloud_source": {
                            "provider": config.get("provider"),
                            "connection_id": config.get("connection_id"),
                            "config_id": str(config.get("_id")),
                            "remote_id": file.id,
                            "remote_path": file.path,
                            "web_view_url": file.web_view_url,
                            "synced_at": datetime.utcnow(),
                        }
                    }}
                )
            
            logger.info(f"Processed cloud file: {file.name}")
            
        finally:
            await pipeline.close()
            
            # Cleanup temp file
            if file_path.exists():
                file_path.unlink()
    
    async def _handle_deletions(self, file_ids: list[str], config: dict):
        """Remove documents that were deleted from the source."""
        from src.profile import get_profile_manager
        
        profile_key = config.get("profile_key", "default")
        pm = get_profile_manager()
        profile = pm.get_profile(profile_key)
        
        # Get collection names from profile
        db_name = profile.get("database", "rag_db")
        docs_collection = profile.get("collection_documents", "documents")
        chunks_collection = profile.get("collection_chunks", "chunks")
        
        db = self.db.client[db_name]
        
        for remote_id in file_ids:
            # Find and delete document
            doc = await db[docs_collection].find_one(
                {"cloud_source.remote_id": remote_id}
            )
            
            if doc:
                doc_id = doc["_id"]
                
                # Delete chunks
                await db[chunks_collection].delete_many({"document_id": str(doc_id)})
                
                # Delete document
                await db[docs_collection].delete_one({"_id": doc_id})
                
                logger.info(f"Deleted document for removed cloud file: {remote_id}")
    
    def request_stop(self, job_id: str):
        """Request a job to stop."""
        self._stop_requested.add(job_id)
    
    def is_job_active(self, job_id: str) -> bool:
        """Check if a job is currently active."""
        return job_id in self._active_jobs


# Global worker instance
_sync_worker: Optional[SyncWorker] = None


def get_sync_worker(db) -> SyncWorker:
    """Get or create the global sync worker."""
    global _sync_worker
    if _sync_worker is None:
        _sync_worker = SyncWorker(db)
    return _sync_worker
