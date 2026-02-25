"""File Registry API router for managing file classification and selective ingestion."""

import logging
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Query, Depends

from backend.models.schemas import FileClassification
from backend.services.file_registry import FileRegistryService
from backend.routers.auth import require_admin, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/stats")
async def get_registry_stats(
    request: Request,
    profile_key: Optional[str] = Query(None, description="Filter by profile key"),
    admin: UserResponse = Depends(require_admin)
):
    """
    Get file registry statistics by classification.
    
    Returns counts of files in each classification category.
    """
    db = request.app.state.db
    registry_service = FileRegistryService(db)
    
    stats = await registry_service.get_registry_stats(profile_key)
    return stats


@router.get("/files")
async def list_files(
    request: Request,
    profile_key: Optional[str] = Query(None, description="Filter by profile key"),
    classification: Optional[str] = Query(None, description="Filter by classification"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of files to return"),
    skip: int = Query(0, ge=0, description="Number of files to skip"),
    admin: UserResponse = Depends(require_admin)
):
    """
    List files in the registry with optional filters.
    """
    db = request.app.state.db
    registry_service = FileRegistryService(db)
    
    # Validate classification if provided
    if classification:
        valid_classifications = [c.value for c in FileClassification]
        if classification not in valid_classifications:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid classification. Must be one of: {', '.join(valid_classifications)}"
            )
    
    files = await registry_service.list_files(
        profile_key=profile_key,
        classification=classification,
        limit=limit,
        skip=skip
    )
    
    return {
        "files": files,
        "count": len(files),
        "limit": limit,
        "skip": skip
    }


@router.post("/reclassify/{file_path:path}")
async def reclassify_file(
    request: Request,
    file_path: str,
    new_classification: str = Query(..., description="New classification to apply"),
    admin: UserResponse = Depends(require_admin)
):
    """
    Manually reclassify a file in the registry.
    
    Use this to correct misclassified files or to mark files for retry.
    """
    db = request.app.state.db
    registry_service = FileRegistryService(db)
    
    # Validate classification
    valid_classifications = [c.value for c in FileClassification]
    if new_classification not in valid_classifications:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid classification. Must be one of: {', '.join(valid_classifications)}"
        )
    
    # Get existing entry
    existing = await registry_service.get_file_by_path(file_path)
    if not existing:
        raise HTTPException(status_code=404, detail="File not found in registry")
    
    # Update classification
    await registry_service.update_classification(
        file_path=file_path,
        classification=new_classification
    )
    
    return {
        "success": True,
        "file_path": file_path,
        "old_classification": existing.get("classification"),
        "new_classification": new_classification
    }


@router.delete("/clear")
async def clear_registry(
    request: Request,
    profile_key: Optional[str] = Query(None, description="Clear only files for this profile"),
    classification: Optional[str] = Query(None, description="Clear only files with this classification"),
    admin: UserResponse = Depends(require_admin)
):
    """
    Clear entries from the file registry.
    
    WARNING: This action cannot be undone. Use filters to target specific entries.
    """
    db = request.app.state.db
    
    # Build filter
    filter_query = {}
    if profile_key:
        filter_query["profile_key"] = profile_key
    if classification:
        valid_classifications = [c.value for c in FileClassification]
        if classification not in valid_classifications:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid classification. Must be one of: {', '.join(valid_classifications)}"
            )
        filter_query["classification"] = classification
    
    collection = db.db["file_registry"]
    result = await collection.delete_many(filter_query)
    
    logger.info(f"Cleared {result.deleted_count} entries from file registry (filter: {filter_query})")
    
    return {
        "success": True,
        "deleted_count": result.deleted_count,
        "filter_applied": filter_query or "all entries"
    }


@router.post("/retry-category")
async def retry_category(
    request: Request,
    profile_key: str = Query(..., description="Profile key to process"),
    classification: str = Query(..., description="Classification to retry"),
    admin: UserResponse = Depends(require_admin)
):
    """
    Mark all files of a specific classification as pending for retry.
    
    This resets the classification to 'pending' so they will be reprocessed
    on the next ingestion run.
    """
    db = request.app.state.db
    
    # Validate classification
    valid_classifications = [c.value for c in FileClassification]
    if classification not in valid_classifications:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid classification. Must be one of: {', '.join(valid_classifications)}"
        )
    
    collection = db.db["file_registry"]
    
    # Update all matching files to pending
    result = await collection.update_many(
        {"profile_key": profile_key, "classification": classification},
        {"$set": {"classification": FileClassification.PENDING.value}}
    )
    
    logger.info(f"Marked {result.modified_count} {classification} files as pending for retry")
    
    return {
        "success": True,
        "modified_count": result.modified_count,
        "profile_key": profile_key,
        "from_classification": classification,
        "to_classification": FileClassification.PENDING.value
    }
