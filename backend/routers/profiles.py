"""Profile management router."""

import logging
from fastapi import APIRouter, HTTPException

from backend.models.schemas import (
    ProfileConfig, ProfileListResponse, ProfileSwitchRequest,
    ProfileCreateRequest, SuccessResponse
)
from backend.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


def get_profile_manager():
    """Get profile manager instance."""
    from src.profile import get_profile_manager as get_pm
    return get_pm(settings.profiles_path)


@router.get("", response_model=ProfileListResponse)
@router.get("/", response_model=ProfileListResponse)
async def list_profiles():
    """
    List all available profiles.
    
    Returns all configured profiles and the currently active one.
    """
    try:
        pm = get_profile_manager()
        profiles_dict = pm.list_profiles()
        
        profiles = {}
        for key, profile in profiles_dict.items():
            profiles[key] = ProfileConfig(
                name=profile.name,
                description=profile.description,
                documents_folders=profile.documents_folders,
                database=profile.database,
                collection_documents=profile.collection_documents,
                collection_chunks=profile.collection_chunks,
                vector_index=profile.vector_index,
                text_index=profile.text_index,
                embedding_model=profile.embedding_model,
                llm_model=profile.llm_model
            )
        
        return ProfileListResponse(
            profiles=profiles,
            active_profile=pm.active_profile_key
        )
        
    except Exception as e:
        logger.error(f"Failed to list profiles: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/active")
async def get_active_profile():
    """Get the currently active profile."""
    try:
        pm = get_profile_manager()
        profile = pm.active_profile
        
        return {
            "key": pm.active_profile_key,
            "profile": ProfileConfig(
                name=profile.name,
                description=profile.description,
                documents_folders=profile.documents_folders,
                database=profile.database,
                collection_documents=profile.collection_documents,
                collection_chunks=profile.collection_chunks,
                vector_index=profile.vector_index,
                text_index=profile.text_index,
                embedding_model=profile.embedding_model,
                llm_model=profile.llm_model
            )
        }
        
    except Exception as e:
        logger.error(f"Failed to get active profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/switch", response_model=SuccessResponse)
async def switch_profile(request: ProfileSwitchRequest):
    """
    Switch to a different profile.
    
    Changes the active profile which affects database and document folder settings.
    """
    try:
        pm = get_profile_manager()
        
        if request.profile_key not in pm.list_profiles():
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{request.profile_key}' not found"
            )
        
        success = pm.switch_profile(request.profile_key)
        
        if success:
            return SuccessResponse(
                success=True,
                message=f"Switched to profile: {request.profile_key}"
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to switch profile"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to switch profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create", response_model=SuccessResponse)
async def create_profile(request: ProfileCreateRequest):
    """
    Create a new profile.
    
    Creates a new profile with the specified configuration.
    """
    try:
        pm = get_profile_manager()
        
        # Check if profile already exists
        if request.key in pm.list_profiles():
            raise HTTPException(
                status_code=400,
                detail=f"Profile '{request.key}' already exists"
            )
        
        # Create profile
        success = pm.create_profile(
            key=request.key,
            name=request.name,
            description=request.description,
            documents_folders=request.documents_folders,
            database=request.database
        )
        
        if success:
            return SuccessResponse(
                success=True,
                message=f"Created profile: {request.key}"
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to create profile"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{profile_key}", response_model=SuccessResponse)
async def delete_profile(profile_key: str):
    """
    Delete a profile.
    
    Removes the profile configuration. Cannot delete the currently active profile.
    """
    try:
        pm = get_profile_manager()
        
        if profile_key not in pm.list_profiles():
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{profile_key}' not found"
            )
        
        if profile_key == pm.active_profile_key:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the currently active profile"
            )
        
        if profile_key == "default":
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the default profile"
            )
        
        success = pm.delete_profile(profile_key)
        
        if success:
            return SuccessResponse(
                success=True,
                message=f"Deleted profile: {profile_key}"
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to delete profile"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{profile_key}")
async def get_profile(profile_key: str):
    """Get a specific profile by key."""
    try:
        pm = get_profile_manager()
        profiles = pm.list_profiles()
        
        if profile_key not in profiles:
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{profile_key}' not found"
            )
        
        profile = profiles[profile_key]
        
        return {
            "key": profile_key,
            "profile": ProfileConfig(
                name=profile.name,
                description=profile.description,
                documents_folders=profile.documents_folders,
                database=profile.database,
                collection_documents=profile.collection_documents,
                collection_chunks=profile.collection_chunks,
                vector_index=profile.vector_index,
                text_index=profile.text_index,
                embedding_model=profile.embedding_model,
                llm_model=profile.llm_model
            ),
            "is_active": profile_key == pm.active_profile_key
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))
