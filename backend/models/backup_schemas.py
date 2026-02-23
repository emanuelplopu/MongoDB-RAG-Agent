"""Backup system schemas and models."""

from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class BackupType(str, Enum):
    """Types of backups supported by the system."""
    FULL = "full"
    INCREMENTAL = "incremental"
    CHECKPOINT = "checkpoint"
    POST_INGESTION = "post_ingestion"


class BackupStatus(str, Enum):
    """Status of a backup operation."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class RestoreMode(str, Enum):
    """Restore operation modes."""
    FULL = "full"  # Replace all data
    MERGE = "merge"  # Add missing documents only
    SELECTIVE = "selective"  # Choose specific collections


# ==================== Request Models ====================

class CreateBackupRequest(BaseModel):
    """Request to create a new backup."""
    backup_type: BackupType = Field(default=BackupType.FULL, description="Type of backup to create")
    profile_key: Optional[str] = Field(default=None, description="Profile to backup (None = active profile)")
    name: Optional[str] = Field(default=None, description="Optional name for the backup (used for checkpoints)")
    include_embeddings: bool = Field(default=True, description="Include chunk embeddings in backup")
    include_system_collections: bool = Field(default=True, description="Include users, sessions, config")


class CreateCheckpointRequest(BaseModel):
    """Request to create a checkpoint."""
    name: str = Field(..., description="Name for the checkpoint")
    profile_key: Optional[str] = Field(default=None, description="Profile to checkpoint (None = active profile)")
    description: Optional[str] = Field(default=None, description="Optional description")


class RestoreBackupRequest(BaseModel):
    """Request to restore from a backup."""
    backup_id: str = Field(..., description="ID of backup to restore")
    restore_mode: RestoreMode = Field(default=RestoreMode.FULL, description="How to restore the backup")
    collections: Optional[List[str]] = Field(default=None, description="Collections to restore (for selective mode)")
    skip_users: bool = Field(default=False, description="Skip restoring users collection")
    skip_sessions: bool = Field(default=False, description="Skip restoring chat sessions")


class UpdateBackupConfigRequest(BaseModel):
    """Request to update backup configuration."""
    auto_backup_after_ingestion: Optional[bool] = Field(default=None)
    retention_days: Optional[int] = Field(default=None, ge=1, le=365)
    max_backups_per_profile: Optional[int] = Field(default=None, ge=1, le=100)
    compression_enabled: Optional[bool] = Field(default=None)
    include_embeddings: Optional[bool] = Field(default=None)
    backup_schedule: Optional[str] = Field(default=None, description="Cron expression for scheduled backups")


# ==================== Response Models ====================

class BackupMetadata(BaseModel):
    """Metadata for a backup."""
    backup_id: str = Field(..., description="Unique backup identifier")
    backup_type: BackupType = Field(..., description="Type of backup")
    status: BackupStatus = Field(..., description="Current status")
    profile_key: str = Field(..., description="Profile this backup belongs to")
    database_name: str = Field(..., description="MongoDB database name")
    name: Optional[str] = Field(default=None, description="User-provided name (for checkpoints)")
    description: Optional[str] = Field(default=None, description="Optional description")
    
    created_at: datetime = Field(..., description="When backup was started")
    completed_at: Optional[datetime] = Field(default=None, description="When backup completed")
    
    size_bytes: int = Field(default=0, description="Total backup size in bytes")
    file_path: str = Field(default="", description="Path to backup files")
    
    collections_included: List[str] = Field(default_factory=list, description="Collections in this backup")
    document_counts: Dict[str, int] = Field(default_factory=dict, description="Document count per collection")
    
    # For incremental backup chains
    parent_backup_id: Optional[str] = Field(default=None, description="Parent backup for incremental")
    
    # For checkpoints
    checkpoint_data: Optional[Dict[str, Any]] = Field(default=None, description="Checkpoint state snapshot")
    
    # For post-ingestion backups
    ingestion_job_id: Optional[str] = Field(default=None, description="Related ingestion job ID")
    ingestion_stats: Optional[Dict[str, Any]] = Field(default=None, description="Ingestion statistics")
    
    # Error info
    error_message: Optional[str] = Field(default=None, description="Error message if failed")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class BackupListResponse(BaseModel):
    """Response containing list of backups."""
    backups: List[BackupMetadata] = Field(default_factory=list)
    total: int = Field(default=0)
    profile_key: Optional[str] = Field(default=None)


class BackupConfig(BaseModel):
    """Backup system configuration."""
    auto_backup_after_ingestion: bool = Field(default=True, description="Auto-backup after ingestion completes")
    retention_days: int = Field(default=30, description="Days to retain backups")
    max_backups_per_profile: int = Field(default=10, description="Maximum backups per profile")
    backup_location: str = Field(default="data/backups", description="Backup storage directory")
    compression_enabled: bool = Field(default=True, description="Enable GZIP compression")
    include_embeddings: bool = Field(default=True, description="Include embeddings in backups")
    backup_schedule: Optional[str] = Field(default=None, description="Cron schedule for auto-backups")


class StorageStats(BaseModel):
    """Backup storage statistics."""
    total_size_bytes: int = Field(default=0)
    total_backups: int = Field(default=0)
    backups_by_type: Dict[str, int] = Field(default_factory=dict)
    backups_by_profile: Dict[str, int] = Field(default_factory=dict)
    oldest_backup: Optional[datetime] = Field(default=None)
    newest_backup: Optional[datetime] = Field(default=None)
    available_space_bytes: Optional[int] = Field(default=None)


class RestoreResult(BaseModel):
    """Result of a restore operation."""
    success: bool = Field(default=False)
    backup_id: str = Field(...)
    restore_mode: RestoreMode = Field(...)
    collections_restored: List[str] = Field(default_factory=list)
    documents_restored: Dict[str, int] = Field(default_factory=dict)
    started_at: datetime = Field(...)
    completed_at: Optional[datetime] = Field(default=None)
    duration_seconds: float = Field(default=0)
    error_message: Optional[str] = Field(default=None)
    warnings: List[str] = Field(default_factory=list)


class BackupProgress(BaseModel):
    """Progress of an ongoing backup operation."""
    backup_id: str = Field(...)
    status: BackupStatus = Field(...)
    progress_percent: float = Field(default=0, ge=0, le=100)
    current_collection: Optional[str] = Field(default=None)
    collections_completed: int = Field(default=0)
    total_collections: int = Field(default=0)
    documents_processed: int = Field(default=0)
    bytes_written: int = Field(default=0)
    started_at: datetime = Field(...)
    estimated_completion: Optional[datetime] = Field(default=None)
    message: Optional[str] = Field(default=None)


# ==================== Checkpoint Data ====================

class CheckpointData(BaseModel):
    """Data stored in a checkpoint."""
    collection_counts: Dict[str, int] = Field(default_factory=dict, description="Document counts per collection")
    collection_hashes: Dict[str, str] = Field(default_factory=dict, description="MD5 hash of collection data")
    last_document_ids: Dict[str, str] = Field(default_factory=dict, description="Last document ID per collection")
    last_full_backup_id: Optional[str] = Field(default=None, description="Reference to last full backup")
    database_size_bytes: int = Field(default=0, description="Total database size")
    index_info: Dict[str, Any] = Field(default_factory=dict, description="Index information")


# ==================== Delta Tracking ====================

class IngestionDelta(BaseModel):
    """Delta information for post-ingestion backups."""
    job_id: str = Field(...)
    profile_key: str = Field(...)
    documents_added: List[str] = Field(default_factory=list, description="Document IDs added")
    chunks_added: List[str] = Field(default_factory=list, description="Chunk IDs added")
    documents_updated: List[str] = Field(default_factory=list, description="Document IDs updated")
    total_new_documents: int = Field(default=0)
    total_new_chunks: int = Field(default=0)
    total_size_bytes: int = Field(default=0)
    ingestion_completed_at: datetime = Field(...)
