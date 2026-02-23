"""Backup management router for creating, listing, and restoring backups."""

import logging
from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse

from backend.models.backup_schemas import (
    BackupType, BackupStatus, RestoreMode,
    CreateBackupRequest, CreateCheckpointRequest, RestoreBackupRequest,
    UpdateBackupConfigRequest,
    BackupMetadata, BackupListResponse, BackupConfig, StorageStats,
    RestoreResult, BackupProgress
)
from backend.services.backup_service import BackupService
from backend.routers.auth import require_admin, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Global backup service instance (initialized on first use)
_backup_service: Optional[BackupService] = None


def get_backup_service(request: Request) -> BackupService:
    """Get or create backup service instance."""
    global _backup_service
    
    if _backup_service is None:
        _backup_service = BackupService(request.app.state.db)
    
    return _backup_service


# ==================== Backup Creation Endpoints ====================

@router.post("/create", response_model=BackupMetadata)
async def create_backup(
    request: Request,
    backup_request: CreateBackupRequest,
    background_tasks: BackgroundTasks,
    _user: UserResponse = Depends(require_admin)
):
    """
    Create a new backup.
    
    Backup types:
    - full: Complete database backup
    - incremental: Only changes since last backup
    - checkpoint: Lightweight state snapshot
    - post_ingestion: Automatic backup after ingestion (internal use)
    
    Requires admin privileges.
    """
    service = get_backup_service(request)
    
    try:
        if backup_request.backup_type == BackupType.FULL:
            backup = await service.create_full_backup(
                profile_key=backup_request.profile_key,
                name=backup_request.name,
                include_embeddings=backup_request.include_embeddings,
                include_system_collections=backup_request.include_system_collections
            )
        elif backup_request.backup_type == BackupType.INCREMENTAL:
            backup = await service.create_incremental_backup(
                profile_key=backup_request.profile_key
            )
        elif backup_request.backup_type == BackupType.CHECKPOINT:
            backup = await service.create_checkpoint(
                profile_key=backup_request.profile_key,
                name=backup_request.name or "Checkpoint"
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid backup type: {backup_request.backup_type}"
            )
        
        return backup
        
    except Exception as e:
        logger.error(f"Backup creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/checkpoint", response_model=BackupMetadata)
async def create_checkpoint(
    request: Request,
    checkpoint_request: CreateCheckpointRequest,
    _user: UserResponse = Depends(require_admin)
):
    """
    Create a checkpoint (lightweight state snapshot).
    
    Checkpoints are fast to create and store:
    - Document counts per collection
    - Hash signatures for integrity verification
    - Reference to last full backup
    
    Requires admin privileges.
    """
    service = get_backup_service(request)
    
    try:
        checkpoint = await service.create_checkpoint(
            profile_key=checkpoint_request.profile_key,
            name=checkpoint_request.name,
            description=checkpoint_request.description
        )
        return checkpoint
        
    except Exception as e:
        logger.error(f"Checkpoint creation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Backup Listing Endpoints ====================

@router.get("/", response_model=BackupListResponse)
async def list_backups(
    request: Request,
    profile_key: Optional[str] = None,
    backup_type: Optional[BackupType] = None,
    limit: int = 50,
    skip: int = 0,
    _user: UserResponse = Depends(require_admin)
):
    """
    List all backups with optional filters.
    
    Requires admin privileges.
    """
    service = get_backup_service(request)
    
    try:
        backups = await service.list_backups(
            profile_key=profile_key,
            backup_type=backup_type,
            limit=limit,
            skip=skip
        )
        
        return BackupListResponse(
            backups=backups,
            total=len(backups),
            profile_key=profile_key
        )
        
    except Exception as e:
        logger.error(f"Error listing backups: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/checkpoints", response_model=BackupListResponse)
async def list_checkpoints(
    request: Request,
    profile_key: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
    _user: UserResponse = Depends(require_admin)
):
    """
    List all checkpoints.
    
    Requires admin privileges.
    """
    service = get_backup_service(request)
    
    try:
        backups = await service.list_backups(
            profile_key=profile_key,
            backup_type=BackupType.CHECKPOINT,
            limit=limit,
            skip=skip
        )
        
        return BackupListResponse(
            backups=backups,
            total=len(backups),
            profile_key=profile_key
        )
        
    except Exception as e:
        logger.error(f"Error listing checkpoints: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{backup_id}", response_model=BackupMetadata)
async def get_backup_details(
    request: Request,
    backup_id: str,
    _user: UserResponse = Depends(require_admin)
):
    """
    Get detailed information about a specific backup.
    
    Requires admin privileges.
    """
    service = get_backup_service(request)
    
    backup = await service.get_backup(backup_id)
    
    if not backup:
        raise HTTPException(status_code=404, detail=f"Backup {backup_id} not found")
    
    return backup


@router.get("/{backup_id}/chain", response_model=List[BackupMetadata])
async def get_backup_chain(
    request: Request,
    backup_id: str,
    _user: UserResponse = Depends(require_admin)
):
    """
    Get the backup chain for an incremental backup.
    
    Returns the list of backups from the full backup to the specified backup,
    in order. Useful for understanding restore requirements.
    
    Requires admin privileges.
    """
    service = get_backup_service(request)
    
    chain = await service.get_backup_chain(backup_id)
    
    if not chain:
        raise HTTPException(status_code=404, detail=f"Backup {backup_id} not found")
    
    return chain


# ==================== Restore Endpoints ====================

@router.post("/{backup_id}/restore", response_model=RestoreResult)
async def restore_from_backup(
    request: Request,
    backup_id: str,
    restore_request: RestoreBackupRequest,
    _user: UserResponse = Depends(require_admin)
):
    """
    Restore database from a backup.
    
    Restore modes:
    - full: Replace all data with backup data
    - merge: Add missing documents only (preserves existing)
    - selective: Restore specific collections only
    
    WARNING: Full restore will delete existing data!
    
    Requires admin privileges.
    """
    service = get_backup_service(request)
    
    # Verify backup_id matches
    if backup_id != restore_request.backup_id:
        raise HTTPException(
            status_code=400,
            detail="Backup ID in URL doesn't match request body"
        )
    
    try:
        result = await service.restore_from_backup(
            backup_id=backup_id,
            restore_mode=restore_request.restore_mode,
            collections=restore_request.collections,
            skip_users=restore_request.skip_users,
            skip_sessions=restore_request.skip_sessions
        )
        
        if not result.success and result.error_message:
            raise HTTPException(status_code=400, detail=result.error_message)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Restore failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Delete Endpoints ====================

@router.delete("/{backup_id}")
async def delete_backup(
    request: Request,
    backup_id: str,
    _user: UserResponse = Depends(require_admin)
):
    """
    Delete a backup and its files.
    
    This permanently removes the backup data and cannot be undone.
    
    Requires admin privileges.
    """
    service = get_backup_service(request)
    
    success = await service.delete_backup(backup_id)
    
    if not success:
        raise HTTPException(status_code=404, detail=f"Backup {backup_id} not found or could not be deleted")
    
    return {"success": True, "message": f"Backup {backup_id} deleted"}


# ==================== Configuration Endpoints ====================

@router.get("/config", response_model=BackupConfig)
async def get_backup_config(
    request: Request,
    _user: UserResponse = Depends(require_admin)
):
    """
    Get backup system configuration.
    
    Requires admin privileges.
    """
    service = get_backup_service(request)
    
    try:
        config = await service.get_config()
        return config
    except Exception as e:
        logger.error(f"Error getting backup config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config", response_model=BackupConfig)
async def update_backup_config(
    request: Request,
    config_update: UpdateBackupConfigRequest,
    _user: UserResponse = Depends(require_admin)
):
    """
    Update backup system configuration.
    
    Configuration options:
    - auto_backup_after_ingestion: Automatically backup after ingestion completes
    - retention_days: Number of days to keep backups
    - max_backups_per_profile: Maximum backups to keep per profile
    - compression_enabled: Enable GZIP compression
    - include_embeddings: Include embedding vectors in backups
    - backup_schedule: Cron expression for scheduled backups
    
    Requires admin privileges.
    """
    service = get_backup_service(request)
    
    try:
        # Convert to dict, excluding None values
        updates = {k: v for k, v in config_update.model_dump().items() if v is not None}
        
        config = await service.update_config(updates)
        return config
        
    except Exception as e:
        logger.error(f"Error updating backup config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Status Endpoints ====================

@router.get("/status", response_model=Optional[BackupProgress])
async def get_backup_status(
    request: Request,
    _user: UserResponse = Depends(require_admin)
):
    """
    Get current backup operation status.
    
    Returns None if no backup is currently in progress.
    
    Requires admin privileges.
    """
    service = get_backup_service(request)
    
    progress = service.get_current_progress()
    return progress


@router.get("/storage", response_model=StorageStats)
async def get_storage_stats(
    request: Request,
    _user: UserResponse = Depends(require_admin)
):
    """
    Get backup storage statistics.
    
    Returns:
    - Total storage used by backups
    - Number of backups by type
    - Number of backups by profile
    - Oldest and newest backup dates
    - Available disk space
    
    Requires admin privileges.
    """
    service = get_backup_service(request)
    
    try:
        stats = await service.get_storage_stats()
        return stats
    except Exception as e:
        logger.error(f"Error getting storage stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Utility Functions ====================

async def trigger_post_ingestion_backup(
    db_manager,
    profile_key: str,
    job_id: str,
    documents_added: int = 0,
    chunks_added: int = 0
) -> Optional[BackupMetadata]:
    """
    Trigger a post-ingestion backup.
    
    This is called automatically after ingestion completes if auto-backup is enabled.
    """
    global _backup_service
    
    if _backup_service is None:
        _backup_service = BackupService(db_manager)
    
    try:
        backup = await _backup_service.create_post_ingestion_backup(
            profile_key=profile_key,
            job_id=job_id,
            documents_added=documents_added,
            chunks_added=chunks_added
        )
        return backup
    except Exception as e:
        logger.error(f"Post-ingestion backup failed: {e}")
        return None
