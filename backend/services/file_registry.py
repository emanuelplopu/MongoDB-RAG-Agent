"""
File Registry Service

Manages a persistent registry of files with their metadata and classification,
enabling intelligent selective re-ingestion based on file characteristics.
"""

import logging
import hashlib
import os
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.models.schemas import (
    FileClassification, FileRegistryEntry, FileRegistryStats
)

logger = logging.getLogger(__name__)

# Collection name
FILE_REGISTRY_COLLECTION = "file_registry"


def compute_file_hash(file_path: str) -> str:
    """
    Compute SHA256 hash of a file for change detection.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Hex digest of the file's SHA256 hash
    """
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()
    except Exception as e:
        logger.warning(f"Could not compute hash for {file_path}: {e}")
        return ""


def get_file_modified_time(file_path: str) -> datetime:
    """Get the modification time of a file."""
    try:
        mtime = os.path.getmtime(file_path)
        return datetime.fromtimestamp(mtime)
    except Exception:
        return datetime.now()


def get_file_size(file_path: str) -> int:
    """Get the size of a file in bytes."""
    try:
        return os.path.getsize(file_path)
    except Exception:
        return 0


class FileRegistryService:
    """
    Service for managing file registry entries.
    
    The file registry tracks:
    - File metadata (path, size, hash, modification time)
    - Classification (normal, image_only_pdf, no_chunks, timeout, error)
    - Processing history (last job, chunks created, processing time)
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        """
        Initialize the file registry service.
        
        Args:
            db: MongoDB database instance
        """
        self.db = db
        self.collection = db[FILE_REGISTRY_COLLECTION]
    
    async def ensure_indexes(self) -> None:
        """Create indexes for efficient querying."""
        try:
            # Unique index on file_path
            await self.collection.create_index("file_path", unique=True)
            # Index for content hash lookups
            await self.collection.create_index("content_hash")
            # Index for classification filtering
            await self.collection.create_index("classification")
            # Index for profile filtering
            await self.collection.create_index("profile_key")
            # Compound index for common queries
            await self.collection.create_index([
                ("profile_key", 1),
                ("classification", 1)
            ])
            logger.info("File registry indexes created")
        except Exception as e:
            logger.warning(f"Failed to create file registry indexes: {e}")
    
    async def register_file(
        self,
        file_path: str,
        profile_key: str,
        classification: FileClassification = FileClassification.PENDING,
        job_id: Optional[str] = None,
        chunks_created: int = 0,
        processing_time_ms: float = 0,
        error_message: Optional[str] = None,
        compute_hash: bool = True
    ) -> FileRegistryEntry:
        """
        Register or update a file in the registry.
        
        Args:
            file_path: Absolute path to the file
            profile_key: Profile this file belongs to
            classification: File classification
            job_id: ID of the job that processed this file
            chunks_created: Number of chunks created
            processing_time_ms: Processing time in milliseconds
            error_message: Error message if failed
            compute_hash: Whether to compute file hash (can be slow for large files)
            
        Returns:
            The created or updated FileRegistryEntry
        """
        now = datetime.now()
        file_name = os.path.basename(file_path)
        file_size = get_file_size(file_path)
        file_modified = get_file_modified_time(file_path)
        content_hash = compute_file_hash(file_path) if compute_hash else ""
        
        # Check if entry exists
        existing = await self.collection.find_one({"file_path": file_path})
        
        if existing:
            # Update existing entry
            retry_count = existing.get("retry_count", 0)
            if classification in [FileClassification.TIMEOUT, FileClassification.ERROR]:
                retry_count += 1
            
            update_doc = {
                "$set": {
                    "file_name": file_name,
                    "file_size_bytes": file_size,
                    "content_hash": content_hash if compute_hash else existing.get("content_hash", ""),
                    "file_modified_at": file_modified.isoformat(),
                    "classification": classification.value,
                    "last_processed_at": now.isoformat(),
                    "last_job_id": job_id,
                    "chunks_created": chunks_created,
                    "processing_time_ms": processing_time_ms,
                    "error_message": error_message,
                    "retry_count": retry_count,
                    "updated_at": now.isoformat()
                }
            }
            
            await self.collection.update_one(
                {"file_path": file_path},
                update_doc
            )
            
            # Return updated entry
            updated = await self.collection.find_one({"file_path": file_path})
            return self._doc_to_entry(updated)
        else:
            # Create new entry
            doc = {
                "file_path": file_path,
                "file_name": file_name,
                "file_size_bytes": file_size,
                "content_hash": content_hash,
                "file_modified_at": file_modified.isoformat(),
                "classification": classification.value,
                "last_processed_at": now.isoformat() if job_id else None,
                "last_job_id": job_id,
                "chunks_created": chunks_created,
                "processing_time_ms": processing_time_ms,
                "error_message": error_message,
                "retry_count": 0,
                "profile_key": profile_key,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat()
            }
            
            result = await self.collection.insert_one(doc)
            doc["_id"] = result.inserted_id
            return self._doc_to_entry(doc)
    
    async def get_file_by_path(self, file_path: str) -> Optional[FileRegistryEntry]:
        """
        Get a file registry entry by path.
        
        Args:
            file_path: Absolute path to the file
            
        Returns:
            FileRegistryEntry or None if not found
        """
        doc = await self.collection.find_one({"file_path": file_path})
        return self._doc_to_entry(doc) if doc else None
    
    async def get_files_by_classification(
        self,
        profile_key: str,
        classifications: List[FileClassification],
        limit: int = 1000
    ) -> List[FileRegistryEntry]:
        """
        Get files by classification.
        
        Args:
            profile_key: Profile to filter by
            classifications: List of classifications to include
            limit: Maximum number of files to return
            
        Returns:
            List of matching FileRegistryEntry objects
        """
        query = {
            "profile_key": profile_key,
            "classification": {"$in": [c.value for c in classifications]}
        }
        
        cursor = self.collection.find(query).limit(limit)
        entries = []
        async for doc in cursor:
            entries.append(self._doc_to_entry(doc))
        return entries
    
    async def get_files_for_retry(
        self,
        profile_key: str,
        retry_image_only_pdfs: bool = False,
        retry_timeouts: bool = False,
        retry_errors: bool = False,
        retry_no_chunks: bool = False,
        limit: int = 10000
    ) -> List[str]:
        """
        Get file paths that should be retried based on filter settings.
        
        Args:
            profile_key: Profile to filter by
            retry_image_only_pdfs: Include image-only PDFs
            retry_timeouts: Include timed-out files
            retry_errors: Include errored files
            retry_no_chunks: Include files with no chunks
            limit: Maximum number of files to return
            
        Returns:
            List of file paths to retry
        """
        classifications = []
        if retry_image_only_pdfs:
            classifications.append(FileClassification.IMAGE_ONLY_PDF.value)
        if retry_timeouts:
            classifications.append(FileClassification.TIMEOUT.value)
        if retry_errors:
            classifications.append(FileClassification.ERROR.value)
        if retry_no_chunks:
            classifications.append(FileClassification.NO_CHUNKS.value)
        
        if not classifications:
            return []
        
        query = {
            "profile_key": profile_key,
            "classification": {"$in": classifications}
        }
        
        cursor = self.collection.find(query, {"file_path": 1}).limit(limit)
        paths = []
        async for doc in cursor:
            paths.append(doc["file_path"])
        return paths
    
    async def get_files_to_skip(
        self,
        profile_key: str,
        skip_image_only_pdfs: bool = False,
        skip_normal: bool = False
    ) -> set:
        """
        Get file paths that should be skipped based on filter settings.
        
        Args:
            profile_key: Profile to filter by
            skip_image_only_pdfs: Skip image-only PDFs
            skip_normal: Skip successfully processed files
            
        Returns:
            Set of file paths to skip
        """
        classifications = []
        if skip_image_only_pdfs:
            classifications.append(FileClassification.IMAGE_ONLY_PDF.value)
        if skip_normal:
            classifications.append(FileClassification.NORMAL.value)
        
        if not classifications:
            return set()
        
        query = {
            "profile_key": profile_key,
            "classification": {"$in": classifications}
        }
        
        cursor = self.collection.find(query, {"file_path": 1})
        paths = set()
        async for doc in cursor:
            paths.add(doc["file_path"])
        return paths
    
    async def check_file_modified(
        self,
        file_path: str,
        current_hash: Optional[str] = None
    ) -> Tuple[bool, Optional[FileRegistryEntry]]:
        """
        Check if a file has been modified since last processing.
        
        Args:
            file_path: Path to the file
            current_hash: Pre-computed hash (optional, will compute if not provided)
            
        Returns:
            Tuple of (is_modified, existing_entry)
        """
        entry = await self.get_file_by_path(file_path)
        
        if not entry:
            return True, None  # New file, needs processing
        
        if not current_hash:
            current_hash = compute_file_hash(file_path)
        
        if not current_hash or not entry.content_hash:
            return True, entry  # Can't compare, assume modified
        
        is_modified = current_hash != entry.content_hash
        return is_modified, entry
    
    async def update_classification(
        self,
        file_path: str,
        classification: FileClassification,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Update the classification of a file.
        
        Args:
            file_path: Path to the file
            classification: New classification
            error_message: Error message if applicable
            
        Returns:
            True if updated, False if not found
        """
        result = await self.collection.update_one(
            {"file_path": file_path},
            {
                "$set": {
                    "classification": classification.value,
                    "error_message": error_message,
                    "updated_at": datetime.now().isoformat()
                }
            }
        )
        return result.modified_count > 0
    
    async def get_registry_stats(self, profile_key: str) -> FileRegistryStats:
        """
        Get statistics for the file registry.
        
        Args:
            profile_key: Profile to get stats for
            
        Returns:
            FileRegistryStats with counts per classification
        """
        pipeline = [
            {"$match": {"profile_key": profile_key}},
            {
                "$group": {
                    "_id": "$classification",
                    "count": {"$sum": 1},
                    "total_size": {"$sum": "$file_size_bytes"}
                }
            }
        ]
        
        results = await self.collection.aggregate(pipeline).to_list(length=100)
        
        stats = FileRegistryStats()
        for result in results:
            classification = result["_id"]
            count = result["count"]
            
            stats.total_files += count
            stats.total_size_bytes += result.get("total_size", 0)
            
            if classification == FileClassification.NORMAL.value:
                stats.normal = count
            elif classification == FileClassification.IMAGE_ONLY_PDF.value:
                stats.image_only_pdf = count
            elif classification == FileClassification.NO_CHUNKS.value:
                stats.no_chunks = count
            elif classification == FileClassification.TIMEOUT.value:
                stats.timeout = count
            elif classification == FileClassification.ERROR.value:
                stats.error = count
            elif classification == FileClassification.PENDING.value:
                stats.pending = count
        
        return stats
    
    async def list_files(
        self,
        profile_key: str,
        classification: Optional[FileClassification] = None,
        page: int = 1,
        page_size: int = 50,
        sort_by: str = "updated_at",
        sort_order: str = "desc"
    ) -> Tuple[List[FileRegistryEntry], int]:
        """
        List file registry entries with pagination.
        
        Args:
            profile_key: Profile to filter by
            classification: Optional classification filter
            page: Page number (1-based)
            page_size: Items per page
            sort_by: Field to sort by
            sort_order: Sort order (asc or desc)
            
        Returns:
            Tuple of (entries list, total count)
        """
        query = {"profile_key": profile_key}
        if classification:
            query["classification"] = classification.value
        
        total = await self.collection.count_documents(query)
        
        skip = (page - 1) * page_size
        sort_direction = -1 if sort_order == "desc" else 1
        
        cursor = self.collection.find(query).skip(skip).limit(page_size).sort(sort_by, sort_direction)
        
        entries = []
        async for doc in cursor:
            entries.append(self._doc_to_entry(doc))
        
        return entries, total
    
    async def clear_registry(self, profile_key: str) -> int:
        """
        Clear all registry entries for a profile.
        
        Args:
            profile_key: Profile to clear
            
        Returns:
            Number of entries deleted
        """
        result = await self.collection.delete_many({"profile_key": profile_key})
        logger.info(f"Cleared {result.deleted_count} file registry entries for profile {profile_key}")
        return result.deleted_count
    
    async def bulk_register_files(
        self,
        file_entries: List[Dict[str, Any]]
    ) -> int:
        """
        Bulk register multiple files.
        
        Args:
            file_entries: List of file entry dictionaries
            
        Returns:
            Number of files registered
        """
        if not file_entries:
            return 0
        
        # Use upsert for each entry to handle duplicates
        count = 0
        for entry in file_entries:
            try:
                await self.collection.update_one(
                    {"file_path": entry["file_path"]},
                    {"$set": entry},
                    upsert=True
                )
                count += 1
            except Exception as e:
                logger.warning(f"Failed to register file {entry.get('file_path')}: {e}")
        
        return count
    
    def _doc_to_entry(self, doc: Dict[str, Any]) -> FileRegistryEntry:
        """Convert MongoDB document to FileRegistryEntry."""
        return FileRegistryEntry(
            id=str(doc.get("_id", "")),
            file_path=doc.get("file_path", ""),
            file_name=doc.get("file_name", ""),
            file_size_bytes=doc.get("file_size_bytes", 0),
            content_hash=doc.get("content_hash", ""),
            file_modified_at=datetime.fromisoformat(doc["file_modified_at"]) if doc.get("file_modified_at") else datetime.now(),
            classification=FileClassification(doc.get("classification", "pending")),
            last_processed_at=datetime.fromisoformat(doc["last_processed_at"]) if doc.get("last_processed_at") else None,
            last_job_id=doc.get("last_job_id"),
            chunks_created=doc.get("chunks_created", 0),
            processing_time_ms=doc.get("processing_time_ms", 0),
            error_message=doc.get("error_message"),
            retry_count=doc.get("retry_count", 0),
            profile_key=doc.get("profile_key", ""),
            created_at=datetime.fromisoformat(doc["created_at"]) if doc.get("created_at") else None,
            updated_at=datetime.fromisoformat(doc["updated_at"]) if doc.get("updated_at") else None
        )


# Singleton instance for service access
_file_registry_service: Optional[FileRegistryService] = None


def get_file_registry_service(db: AsyncIOMotorDatabase) -> FileRegistryService:
    """Get or create file registry service instance."""
    global _file_registry_service
    if _file_registry_service is None:
        _file_registry_service = FileRegistryService(db)
    return _file_registry_service
