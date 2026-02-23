"""Backup service for creating and restoring database backups.

This service handles:
- Full database backups
- Incremental backups (delta changes)
- Checkpoints (lightweight state snapshots)
- Post-ingestion automatic backups
- Restore operations with multiple modes
"""

import os
import json
import gzip
import shutil
import hashlib
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from bson import json_util, ObjectId
import uuid

from backend.models.backup_schemas import (
    BackupType, BackupStatus, RestoreMode,
    BackupMetadata, BackupConfig, StorageStats, RestoreResult,
    BackupProgress, CheckpointData, IngestionDelta
)

logger = logging.getLogger(__name__)

# Default backup configuration
DEFAULT_BACKUP_CONFIG = {
    "auto_backup_after_ingestion": True,
    "retention_days": 30,
    "max_backups_per_profile": 10,
    "backup_location": "data/backups",
    "compression_enabled": True,
    "include_embeddings": True,
    "backup_schedule": None
}

# Collections to backup per profile database
PROFILE_COLLECTIONS = [
    "documents",
    "chunks",
    "ingestion_jobs",
    "ingestion_stats",
    "failed_documents"
]

# System-wide collections (from main database)
SYSTEM_COLLECTIONS = [
    "users",
    "chat_sessions",
    "offline_config",
    "saved_config",
    "backup_config",
    "backups_metadata"
]

# Backup metadata collection name
BACKUPS_COLLECTION = "backups_metadata"
BACKUP_CONFIG_COLLECTION = "backup_config"


class BackupService:
    """Service for managing database backups."""
    
    def __init__(self, db_manager, profiles_manager=None):
        """
        Initialize backup service.
        
        Args:
            db_manager: DatabaseManager instance for MongoDB access
            profiles_manager: Optional ProfileManager for profile resolution
        """
        self.db_manager = db_manager
        self.profiles_manager = profiles_manager
        self._current_backup: Optional[BackupProgress] = None
        self._backup_lock = asyncio.Lock()
    
    async def get_config(self) -> BackupConfig:
        """Get backup configuration from database or defaults."""
        try:
            db = self.db_manager.db
            config_doc = await db[BACKUP_CONFIG_COLLECTION].find_one({"_id": "config"})
            
            if config_doc:
                del config_doc["_id"]
                return BackupConfig(**{**DEFAULT_BACKUP_CONFIG, **config_doc})
            
            return BackupConfig(**DEFAULT_BACKUP_CONFIG)
        except Exception as e:
            logger.error(f"Error getting backup config: {e}")
            return BackupConfig(**DEFAULT_BACKUP_CONFIG)
    
    async def update_config(self, updates: Dict[str, Any]) -> BackupConfig:
        """Update backup configuration."""
        try:
            db = self.db_manager.db
            
            # Get current config
            current = await self.get_config()
            current_dict = current.model_dump()
            
            # Apply updates
            for key, value in updates.items():
                if value is not None and key in current_dict:
                    current_dict[key] = value
            
            # Save to database
            await db[BACKUP_CONFIG_COLLECTION].replace_one(
                {"_id": "config"},
                {"_id": "config", **current_dict},
                upsert=True
            )
            
            logger.info(f"Backup config updated: {updates}")
            return BackupConfig(**current_dict)
        except Exception as e:
            logger.error(f"Error updating backup config: {e}")
            raise
    
    def _get_backup_dir(self, backup_type: BackupType) -> Path:
        """Get the directory for a backup type."""
        base_dir = Path(DEFAULT_BACKUP_CONFIG["backup_location"])
        return base_dir / backup_type.value
    
    def _generate_backup_id(self) -> str:
        """Generate a unique backup ID."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = uuid.uuid4().hex[:8]
        return f"backup_{timestamp}_{unique_id}"
    
    async def _get_profile_database(self, profile_key: Optional[str]) -> Tuple[str, str]:
        """Get database name for a profile."""
        if self.profiles_manager:
            profile = self.profiles_manager.get_profile(profile_key)
            if profile:
                return profile_key or "default", profile.database
        
        # Fall back to current database
        return profile_key or "default", self.db_manager._current_database
    
    async def _save_backup_metadata(self, metadata: BackupMetadata) -> None:
        """Save backup metadata to database."""
        try:
            db = self.db_manager.db
            doc = metadata.model_dump()
            doc["_id"] = metadata.backup_id
            doc["created_at"] = metadata.created_at
            doc["completed_at"] = metadata.completed_at
            
            await db[BACKUPS_COLLECTION].replace_one(
                {"_id": metadata.backup_id},
                doc,
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error saving backup metadata: {e}")
            raise
    
    async def _export_collection(
        self,
        db,
        collection_name: str,
        output_path: Path,
        compress: bool = True,
        query: Optional[Dict] = None,
        exclude_fields: Optional[List[str]] = None
    ) -> Tuple[int, int]:
        """
        Export a collection to a file.
        
        Returns:
            Tuple of (document_count, bytes_written)
        """
        query = query or {}
        projection = None
        if exclude_fields:
            projection = {field: 0 for field in exclude_fields}
        
        cursor = db[collection_name].find(query, projection)
        documents = await cursor.to_list(length=None)
        
        # Convert to BSON-compatible JSON
        json_data = json_util.dumps(documents, indent=None)
        
        if compress:
            output_file = output_path / f"{collection_name}.json.gz"
            with gzip.open(output_file, "wt", encoding="utf-8") as f:
                f.write(json_data)
        else:
            output_file = output_path / f"{collection_name}.json"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(json_data)
        
        bytes_written = output_file.stat().st_size
        return len(documents), bytes_written
    
    async def _import_collection(
        self,
        db,
        collection_name: str,
        input_path: Path,
        mode: RestoreMode = RestoreMode.FULL
    ) -> int:
        """
        Import a collection from a file.
        
        Returns:
            Number of documents imported
        """
        # Try compressed first, then uncompressed
        compressed_file = input_path / f"{collection_name}.json.gz"
        uncompressed_file = input_path / f"{collection_name}.json"
        
        if compressed_file.exists():
            with gzip.open(compressed_file, "rt", encoding="utf-8") as f:
                json_data = f.read()
        elif uncompressed_file.exists():
            with open(uncompressed_file, "r", encoding="utf-8") as f:
                json_data = f.read()
        else:
            logger.warning(f"No backup file found for collection: {collection_name}")
            return 0
        
        documents = json_util.loads(json_data)
        
        if not documents:
            return 0
        
        if mode == RestoreMode.FULL:
            # Drop existing collection and insert all
            await db[collection_name].drop()
            await db[collection_name].insert_many(documents)
        elif mode == RestoreMode.MERGE:
            # Only insert documents that don't exist
            for doc in documents:
                doc_id = doc.get("_id")
                if doc_id:
                    existing = await db[collection_name].find_one({"_id": doc_id})
                    if not existing:
                        await db[collection_name].insert_one(doc)
        
        return len(documents)
    
    async def create_full_backup(
        self,
        profile_key: Optional[str] = None,
        name: Optional[str] = None,
        include_embeddings: bool = True,
        include_system_collections: bool = True
    ) -> BackupMetadata:
        """
        Create a full backup of the database.
        
        Args:
            profile_key: Profile to backup (None = active profile)
            name: Optional name for the backup
            include_embeddings: Include embedding vectors in chunks backup
            include_system_collections: Include users, sessions, etc.
        
        Returns:
            BackupMetadata with backup details
        """
        async with self._backup_lock:
            backup_id = self._generate_backup_id()
            profile_key, database_name = await self._get_profile_database(profile_key)
            
            config = await self.get_config()
            backup_dir = self._get_backup_dir(BackupType.FULL) / backup_id
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            metadata = BackupMetadata(
                backup_id=backup_id,
                backup_type=BackupType.FULL,
                status=BackupStatus.IN_PROGRESS,
                profile_key=profile_key,
                database_name=database_name,
                name=name,
                created_at=datetime.now(),
                file_path=str(backup_dir),
                collections_included=[],
                document_counts={}
            )
            
            self._current_backup = BackupProgress(
                backup_id=backup_id,
                status=BackupStatus.IN_PROGRESS,
                progress_percent=0,
                started_at=datetime.now(),
                total_collections=len(PROFILE_COLLECTIONS) + (len(SYSTEM_COLLECTIONS) if include_system_collections else 0),
                message="Starting full backup..."
            )
            
            try:
                await self._save_backup_metadata(metadata)
                
                total_size = 0
                collections_done = 0
                
                # Get profile database
                db = self.db_manager.client[database_name]
                
                # Backup profile collections
                exclude_fields = ["embedding"] if not include_embeddings else None
                
                for collection_name in PROFILE_COLLECTIONS:
                    self._current_backup.current_collection = collection_name
                    self._current_backup.message = f"Backing up {collection_name}..."
                    
                    exclude = exclude_fields if collection_name == "chunks" else None
                    doc_count, bytes_written = await self._export_collection(
                        db, collection_name, backup_dir,
                        compress=config.compression_enabled,
                        exclude_fields=exclude
                    )
                    
                    metadata.collections_included.append(collection_name)
                    metadata.document_counts[collection_name] = doc_count
                    total_size += bytes_written
                    
                    collections_done += 1
                    self._current_backup.collections_completed = collections_done
                    self._current_backup.progress_percent = (collections_done / self._current_backup.total_collections) * 100
                    self._current_backup.bytes_written = total_size
                    
                    logger.info(f"Backed up {collection_name}: {doc_count} documents, {bytes_written} bytes")
                
                # Backup system collections if requested
                if include_system_collections:
                    system_db = self.db_manager.db
                    
                    for collection_name in SYSTEM_COLLECTIONS:
                        self._current_backup.current_collection = collection_name
                        self._current_backup.message = f"Backing up system collection {collection_name}..."
                        
                        try:
                            doc_count, bytes_written = await self._export_collection(
                                system_db, collection_name, backup_dir,
                                compress=config.compression_enabled
                            )
                            
                            metadata.collections_included.append(f"system.{collection_name}")
                            metadata.document_counts[f"system.{collection_name}"] = doc_count
                            total_size += bytes_written
                        except Exception as e:
                            logger.warning(f"Could not backup system collection {collection_name}: {e}")
                        
                        collections_done += 1
                        self._current_backup.collections_completed = collections_done
                        self._current_backup.progress_percent = (collections_done / self._current_backup.total_collections) * 100
                        self._current_backup.bytes_written = total_size
                
                # Save manifest
                manifest = {
                    "backup_id": backup_id,
                    "backup_type": BackupType.FULL.value,
                    "profile_key": profile_key,
                    "database_name": database_name,
                    "created_at": datetime.now().isoformat(),
                    "collections": metadata.collections_included,
                    "document_counts": metadata.document_counts,
                    "include_embeddings": include_embeddings,
                    "compression_enabled": config.compression_enabled
                }
                
                with open(backup_dir / "manifest.json", "w") as f:
                    json.dump(manifest, f, indent=2)
                
                # Update metadata
                metadata.status = BackupStatus.COMPLETED
                metadata.completed_at = datetime.now()
                metadata.size_bytes = total_size
                
                await self._save_backup_metadata(metadata)
                
                self._current_backup.status = BackupStatus.COMPLETED
                self._current_backup.progress_percent = 100
                self._current_backup.message = "Backup completed successfully"
                
                logger.info(f"Full backup completed: {backup_id}, {total_size} bytes")
                
                # Cleanup old backups
                await self._cleanup_old_backups(profile_key, BackupType.FULL)
                
                return metadata
                
            except Exception as e:
                logger.error(f"Full backup failed: {e}")
                metadata.status = BackupStatus.FAILED
                metadata.error_message = str(e)
                await self._save_backup_metadata(metadata)
                
                self._current_backup.status = BackupStatus.FAILED
                self._current_backup.message = f"Backup failed: {e}"
                
                raise
            finally:
                self._current_backup = None
    
    async def create_incremental_backup(
        self,
        profile_key: Optional[str] = None,
        since_timestamp: Optional[datetime] = None
    ) -> BackupMetadata:
        """
        Create an incremental backup with only changes since last backup.
        
        Args:
            profile_key: Profile to backup
            since_timestamp: Only include documents modified after this time
        
        Returns:
            BackupMetadata with backup details
        """
        async with self._backup_lock:
            backup_id = self._generate_backup_id()
            profile_key, database_name = await self._get_profile_database(profile_key)
            
            # Find last backup for parent reference
            last_backup = await self._get_last_backup(profile_key)
            parent_backup_id = last_backup.backup_id if last_backup else None
            
            if since_timestamp is None and last_backup:
                since_timestamp = last_backup.created_at
            elif since_timestamp is None:
                # If no previous backup, do a full backup instead
                logger.info("No previous backup found, creating full backup instead")
                return await self.create_full_backup(profile_key)
            
            config = await self.get_config()
            backup_dir = self._get_backup_dir(BackupType.INCREMENTAL) / backup_id
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            metadata = BackupMetadata(
                backup_id=backup_id,
                backup_type=BackupType.INCREMENTAL,
                status=BackupStatus.IN_PROGRESS,
                profile_key=profile_key,
                database_name=database_name,
                created_at=datetime.now(),
                file_path=str(backup_dir),
                parent_backup_id=parent_backup_id,
                collections_included=[],
                document_counts={}
            )
            
            try:
                await self._save_backup_metadata(metadata)
                
                total_size = 0
                db = self.db_manager.client[database_name]
                
                # Query for documents modified since last backup
                time_query = {
                    "$or": [
                        {"created_at": {"$gte": since_timestamp}},
                        {"updated_at": {"$gte": since_timestamp}},
                        {"ingested_at": {"$gte": since_timestamp}}
                    ]
                }
                
                delta_info = {
                    "parent_backup_id": parent_backup_id,
                    "since_timestamp": since_timestamp.isoformat(),
                    "collections": {}
                }
                
                for collection_name in PROFILE_COLLECTIONS:
                    doc_count, bytes_written = await self._export_collection(
                        db, collection_name, backup_dir,
                        compress=config.compression_enabled,
                        query=time_query
                    )
                    
                    if doc_count > 0:
                        metadata.collections_included.append(collection_name)
                        metadata.document_counts[collection_name] = doc_count
                        total_size += bytes_written
                        delta_info["collections"][collection_name] = doc_count
                        
                        logger.info(f"Incremental backup {collection_name}: {doc_count} new/modified documents")
                
                # Save delta info
                with open(backup_dir / "delta.json", "w") as f:
                    json.dump(delta_info, f, indent=2)
                
                # Save manifest
                manifest = {
                    "backup_id": backup_id,
                    "backup_type": BackupType.INCREMENTAL.value,
                    "parent_backup_id": parent_backup_id,
                    "profile_key": profile_key,
                    "database_name": database_name,
                    "since_timestamp": since_timestamp.isoformat(),
                    "created_at": datetime.now().isoformat(),
                    "collections": metadata.collections_included,
                    "document_counts": metadata.document_counts
                }
                
                with open(backup_dir / "manifest.json", "w") as f:
                    json.dump(manifest, f, indent=2)
                
                metadata.status = BackupStatus.COMPLETED
                metadata.completed_at = datetime.now()
                metadata.size_bytes = total_size
                
                await self._save_backup_metadata(metadata)
                
                logger.info(f"Incremental backup completed: {backup_id}, {total_size} bytes")
                
                return metadata
                
            except Exception as e:
                logger.error(f"Incremental backup failed: {e}")
                metadata.status = BackupStatus.FAILED
                metadata.error_message = str(e)
                await self._save_backup_metadata(metadata)
                raise
    
    async def create_checkpoint(
        self,
        profile_key: Optional[str] = None,
        name: str = "Checkpoint",
        description: Optional[str] = None
    ) -> BackupMetadata:
        """
        Create a lightweight checkpoint (state snapshot without full data copy).
        
        Args:
            profile_key: Profile to checkpoint
            name: Name for the checkpoint
            description: Optional description
        
        Returns:
            BackupMetadata with checkpoint details
        """
        backup_id = self._generate_backup_id()
        profile_key, database_name = await self._get_profile_database(profile_key)
        
        backup_dir = self._get_backup_dir(BackupType.CHECKPOINT) / backup_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        db = self.db_manager.client[database_name]
        
        # Gather checkpoint data
        checkpoint_data = CheckpointData(
            collection_counts={},
            collection_hashes={},
            last_document_ids={}
        )
        
        for collection_name in PROFILE_COLLECTIONS:
            try:
                # Get document count
                count = await db[collection_name].count_documents({})
                checkpoint_data.collection_counts[collection_name] = count
                
                # Get last document ID
                last_doc = await db[collection_name].find_one(
                    sort=[("_id", -1)]
                )
                if last_doc:
                    checkpoint_data.last_document_ids[collection_name] = str(last_doc["_id"])
                
                # Compute hash of document IDs for integrity check
                cursor = db[collection_name].find({}, {"_id": 1}).limit(1000)
                docs = await cursor.to_list(length=1000)
                id_string = ",".join(str(d["_id"]) for d in docs)
                checkpoint_data.collection_hashes[collection_name] = hashlib.md5(
                    id_string.encode()
                ).hexdigest()
                
            except Exception as e:
                logger.warning(f"Error collecting checkpoint data for {collection_name}: {e}")
        
        # Find last full backup
        last_full = await self._get_last_backup(profile_key, BackupType.FULL)
        if last_full:
            checkpoint_data.last_full_backup_id = last_full.backup_id
        
        metadata = BackupMetadata(
            backup_id=backup_id,
            backup_type=BackupType.CHECKPOINT,
            status=BackupStatus.COMPLETED,
            profile_key=profile_key,
            database_name=database_name,
            name=name,
            description=description,
            created_at=datetime.now(),
            completed_at=datetime.now(),
            file_path=str(backup_dir),
            checkpoint_data=checkpoint_data.model_dump(),
            collections_included=list(checkpoint_data.collection_counts.keys()),
            document_counts=checkpoint_data.collection_counts
        )
        
        # Save checkpoint data
        with open(backup_dir / "checkpoint.json", "w") as f:
            json.dump({
                "backup_id": backup_id,
                "name": name,
                "description": description,
                "profile_key": profile_key,
                "database_name": database_name,
                "created_at": datetime.now().isoformat(),
                "checkpoint_data": checkpoint_data.model_dump()
            }, f, indent=2)
        
        metadata.size_bytes = (backup_dir / "checkpoint.json").stat().st_size
        
        await self._save_backup_metadata(metadata)
        
        logger.info(f"Checkpoint created: {backup_id} - {name}")
        
        return metadata
    
    async def create_post_ingestion_backup(
        self,
        profile_key: str,
        job_id: str,
        documents_added: int = 0,
        chunks_added: int = 0
    ) -> BackupMetadata:
        """
        Create a backup of newly ingested data.
        
        Args:
            profile_key: Profile that was ingested
            job_id: Ingestion job ID
            documents_added: Number of documents added
            chunks_added: Number of chunks created
        
        Returns:
            BackupMetadata with backup details
        """
        config = await self.get_config()
        
        if not config.auto_backup_after_ingestion:
            logger.info("Auto-backup after ingestion is disabled")
            return None
        
        async with self._backup_lock:
            backup_id = self._generate_backup_id()
            profile_key, database_name = await self._get_profile_database(profile_key)
            
            backup_dir = self._get_backup_dir(BackupType.POST_INGESTION) / backup_id
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            db = self.db_manager.client[database_name]
            
            # Get the ingestion job to find timestamps
            job = await db["ingestion_jobs"].find_one({"_id": job_id})
            
            if not job:
                logger.warning(f"Ingestion job {job_id} not found")
                return None
            
            job_started = job.get("started_at")
            if isinstance(job_started, str):
                job_started = datetime.fromisoformat(job_started)
            
            metadata = BackupMetadata(
                backup_id=backup_id,
                backup_type=BackupType.POST_INGESTION,
                status=BackupStatus.IN_PROGRESS,
                profile_key=profile_key,
                database_name=database_name,
                created_at=datetime.now(),
                file_path=str(backup_dir),
                ingestion_job_id=job_id,
                ingestion_stats={
                    "documents_added": documents_added,
                    "chunks_added": chunks_added
                },
                collections_included=[],
                document_counts={}
            )
            
            try:
                await self._save_backup_metadata(metadata)
                
                total_size = 0
                
                # Query for documents from this ingestion job
                ingestion_query = {
                    "$or": [
                        {"ingested_at": {"$gte": job_started}},
                        {"created_at": {"$gte": job_started}}
                    ]
                }
                
                # Backup only the newly ingested documents and chunks
                for collection_name in ["documents", "chunks"]:
                    doc_count, bytes_written = await self._export_collection(
                        db, collection_name, backup_dir,
                        compress=config.compression_enabled,
                        query=ingestion_query
                    )
                    
                    if doc_count > 0:
                        metadata.collections_included.append(collection_name)
                        metadata.document_counts[collection_name] = doc_count
                        total_size += bytes_written
                        
                        logger.info(f"Post-ingestion backup {collection_name}: {doc_count} documents")
                
                # Save ingestion delta info
                delta_info = IngestionDelta(
                    job_id=job_id,
                    profile_key=profile_key,
                    total_new_documents=documents_added,
                    total_new_chunks=chunks_added,
                    total_size_bytes=total_size,
                    ingestion_completed_at=datetime.now()
                )
                
                with open(backup_dir / "ingestion_delta.json", "w") as f:
                    json.dump(delta_info.model_dump(), f, indent=2, default=str)
                
                # Save manifest
                manifest = {
                    "backup_id": backup_id,
                    "backup_type": BackupType.POST_INGESTION.value,
                    "profile_key": profile_key,
                    "database_name": database_name,
                    "ingestion_job_id": job_id,
                    "created_at": datetime.now().isoformat(),
                    "collections": metadata.collections_included,
                    "document_counts": metadata.document_counts
                }
                
                with open(backup_dir / "manifest.json", "w") as f:
                    json.dump(manifest, f, indent=2)
                
                metadata.status = BackupStatus.COMPLETED
                metadata.completed_at = datetime.now()
                metadata.size_bytes = total_size
                
                await self._save_backup_metadata(metadata)
                
                logger.info(f"Post-ingestion backup completed: {backup_id}, {total_size} bytes")
                
                return metadata
                
            except Exception as e:
                logger.error(f"Post-ingestion backup failed: {e}")
                metadata.status = BackupStatus.FAILED
                metadata.error_message = str(e)
                await self._save_backup_metadata(metadata)
                raise
    
    async def list_backups(
        self,
        profile_key: Optional[str] = None,
        backup_type: Optional[BackupType] = None,
        limit: int = 50,
        skip: int = 0
    ) -> List[BackupMetadata]:
        """List all backups with optional filters."""
        try:
            db = self.db_manager.db
            
            query = {}
            if profile_key:
                query["profile_key"] = profile_key
            if backup_type:
                query["backup_type"] = backup_type.value
            
            cursor = db[BACKUPS_COLLECTION].find(query).sort("created_at", -1).skip(skip).limit(limit)
            docs = await cursor.to_list(length=limit)
            
            backups = []
            for doc in docs:
                if "_id" in doc:
                    del doc["_id"]
                backups.append(BackupMetadata(**doc))
            
            return backups
        except Exception as e:
            logger.error(f"Error listing backups: {e}")
            return []
    
    async def get_backup(self, backup_id: str) -> Optional[BackupMetadata]:
        """Get backup metadata by ID."""
        try:
            db = self.db_manager.db
            doc = await db[BACKUPS_COLLECTION].find_one({"backup_id": backup_id})
            
            if doc:
                if "_id" in doc:
                    del doc["_id"]
                return BackupMetadata(**doc)
            
            return None
        except Exception as e:
            logger.error(f"Error getting backup {backup_id}: {e}")
            return None
    
    async def _get_last_backup(
        self,
        profile_key: str,
        backup_type: Optional[BackupType] = None
    ) -> Optional[BackupMetadata]:
        """Get the most recent backup for a profile."""
        try:
            db = self.db_manager.db
            
            query = {
                "profile_key": profile_key,
                "status": BackupStatus.COMPLETED.value
            }
            if backup_type:
                query["backup_type"] = backup_type.value
            
            doc = await db[BACKUPS_COLLECTION].find_one(
                query,
                sort=[("created_at", -1)]
            )
            
            if doc:
                if "_id" in doc:
                    del doc["_id"]
                return BackupMetadata(**doc)
            
            return None
        except Exception as e:
            logger.error(f"Error getting last backup: {e}")
            return None
    
    async def restore_from_backup(
        self,
        backup_id: str,
        restore_mode: RestoreMode = RestoreMode.FULL,
        collections: Optional[List[str]] = None,
        skip_users: bool = False,
        skip_sessions: bool = False
    ) -> RestoreResult:
        """
        Restore database from a backup.
        
        Args:
            backup_id: ID of backup to restore
            restore_mode: How to restore (full, merge, selective)
            collections: Specific collections to restore (for selective mode)
            skip_users: Skip restoring users collection
            skip_sessions: Skip restoring chat sessions
        
        Returns:
            RestoreResult with operation details
        """
        result = RestoreResult(
            success=False,
            backup_id=backup_id,
            restore_mode=restore_mode,
            started_at=datetime.now()
        )
        
        try:
            backup = await self.get_backup(backup_id)
            
            if not backup:
                result.error_message = f"Backup {backup_id} not found"
                return result
            
            if backup.status != BackupStatus.COMPLETED:
                result.error_message = f"Backup {backup_id} is not completed (status: {backup.status})"
                return result
            
            backup_dir = Path(backup.file_path)
            
            if not backup_dir.exists():
                result.error_message = f"Backup directory not found: {backup_dir}"
                return result
            
            # Get target database
            db = self.db_manager.client[backup.database_name]
            
            # Determine which collections to restore
            collections_to_restore = collections if collections else backup.collections_included
            
            if skip_users:
                collections_to_restore = [c for c in collections_to_restore if c != "system.users" and c != "users"]
            if skip_sessions:
                collections_to_restore = [c for c in collections_to_restore if c != "system.chat_sessions" and c != "chat_sessions"]
            
            logger.info(f"Restoring from backup {backup_id}, mode: {restore_mode}, collections: {collections_to_restore}")
            
            for collection_name in collections_to_restore:
                # Handle system collections
                actual_collection = collection_name.replace("system.", "")
                target_db = self.db_manager.db if collection_name.startswith("system.") else db
                
                try:
                    doc_count = await self._import_collection(
                        target_db, actual_collection, backup_dir, restore_mode
                    )
                    
                    result.collections_restored.append(collection_name)
                    result.documents_restored[collection_name] = doc_count
                    
                    logger.info(f"Restored {collection_name}: {doc_count} documents")
                    
                except Exception as e:
                    result.warnings.append(f"Failed to restore {collection_name}: {e}")
                    logger.warning(f"Failed to restore {collection_name}: {e}")
            
            result.success = len(result.collections_restored) > 0
            result.completed_at = datetime.now()
            result.duration_seconds = (result.completed_at - result.started_at).total_seconds()
            
            logger.info(f"Restore completed: {len(result.collections_restored)} collections, "
                       f"{sum(result.documents_restored.values())} documents")
            
            return result
            
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            result.error_message = str(e)
            result.completed_at = datetime.now()
            result.duration_seconds = (result.completed_at - result.started_at).total_seconds()
            return result
    
    async def delete_backup(self, backup_id: str) -> bool:
        """Delete a backup and its files."""
        try:
            backup = await self.get_backup(backup_id)
            
            if not backup:
                logger.warning(f"Backup {backup_id} not found")
                return False
            
            # Delete files
            backup_dir = Path(backup.file_path)
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
                logger.info(f"Deleted backup files: {backup_dir}")
            
            # Delete metadata
            db = self.db_manager.db
            await db[BACKUPS_COLLECTION].delete_one({"backup_id": backup_id})
            
            logger.info(f"Deleted backup: {backup_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting backup {backup_id}: {e}")
            return False
    
    async def _cleanup_old_backups(
        self,
        profile_key: str,
        backup_type: BackupType
    ) -> int:
        """Remove old backups exceeding retention limits."""
        config = await self.get_config()
        
        # Get all backups for this profile and type
        backups = await self.list_backups(
            profile_key=profile_key,
            backup_type=backup_type,
            limit=1000
        )
        
        deleted_count = 0
        
        # Check max backups limit
        if len(backups) > config.max_backups_per_profile:
            excess_backups = backups[config.max_backups_per_profile:]
            for backup in excess_backups:
                if await self.delete_backup(backup.backup_id):
                    deleted_count += 1
        
        # Check retention period
        retention_cutoff = datetime.now() - timedelta(days=config.retention_days)
        for backup in backups:
            if backup.created_at < retention_cutoff:
                if await self.delete_backup(backup.backup_id):
                    deleted_count += 1
        
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old backups for {profile_key}")
        
        return deleted_count
    
    async def get_storage_stats(self) -> StorageStats:
        """Get backup storage statistics."""
        stats = StorageStats()
        
        try:
            base_dir = Path(DEFAULT_BACKUP_CONFIG["backup_location"])
            
            if not base_dir.exists():
                return stats
            
            # Calculate total size
            for backup_type_dir in base_dir.iterdir():
                if backup_type_dir.is_dir():
                    backup_type = backup_type_dir.name
                    
                    for backup_dir in backup_type_dir.iterdir():
                        if backup_dir.is_dir():
                            size = sum(f.stat().st_size for f in backup_dir.rglob("*") if f.is_file())
                            stats.total_size_bytes += size
                            stats.total_backups += 1
                            
                            if backup_type not in stats.backups_by_type:
                                stats.backups_by_type[backup_type] = 0
                            stats.backups_by_type[backup_type] += 1
            
            # Get backup dates from database
            db = self.db_manager.db
            
            oldest = await db[BACKUPS_COLLECTION].find_one(
                {"status": BackupStatus.COMPLETED.value},
                sort=[("created_at", 1)]
            )
            if oldest:
                stats.oldest_backup = oldest.get("created_at")
            
            newest = await db[BACKUPS_COLLECTION].find_one(
                {"status": BackupStatus.COMPLETED.value},
                sort=[("created_at", -1)]
            )
            if newest:
                stats.newest_backup = newest.get("created_at")
            
            # Get backups by profile
            pipeline = [
                {"$match": {"status": BackupStatus.COMPLETED.value}},
                {"$group": {"_id": "$profile_key", "count": {"$sum": 1}}}
            ]
            async for doc in db[BACKUPS_COLLECTION].aggregate(pipeline):
                stats.backups_by_profile[doc["_id"]] = doc["count"]
            
            # Try to get available disk space
            try:
                import shutil
                total, used, free = shutil.disk_usage(base_dir)
                stats.available_space_bytes = free
            except Exception:
                pass
            
        except Exception as e:
            logger.error(f"Error getting storage stats: {e}")
        
        return stats
    
    def get_current_progress(self) -> Optional[BackupProgress]:
        """Get progress of current backup operation."""
        return self._current_backup
    
    async def get_backup_chain(self, backup_id: str) -> List[BackupMetadata]:
        """
        Get the chain of backups for incremental restore.
        
        For incremental backups, returns the chain from the full backup
        to the specified backup.
        """
        chain = []
        current_id = backup_id
        
        while current_id:
            backup = await self.get_backup(current_id)
            if not backup:
                break
            
            chain.insert(0, backup)
            current_id = backup.parent_backup_id
        
        return chain
