"""Profile management router."""

import logging
from fastapi import APIRouter, HTTPException, Request, Depends

from backend.models.schemas import (
    ProfileConfig, ProfileListResponse, ProfileSwitchRequest,
    ProfileCreateRequest, ProfileUpdateRequest, SuccessResponse
)
from backend.core.config import settings
from backend.routers.auth import require_auth, require_admin, UserResponse, get_user_accessible_profiles

logger = logging.getLogger(__name__)

router = APIRouter()


def get_profile_manager():
    """Get profile manager instance."""
    from src.profile import get_profile_manager as get_pm
    return get_pm(settings.profiles_path)


@router.get("", response_model=ProfileListResponse)
@router.get("/", response_model=ProfileListResponse)
async def list_profiles(
    request: Request,
    user: UserResponse = Depends(require_auth)
):
    """
    List available profiles based on user access rights.
    
    Returns profiles the user has access to and the currently active one.
    Admin users can see all profiles.
    """
    try:
        pm = get_profile_manager()
        all_profiles = pm.list_profiles()
        
        # Get user's accessible profile keys
        accessible_keys = await get_user_accessible_profiles(request, user.id)
        
        profiles = {}
        for key, profile in all_profiles.items():
            # Only include profiles the user has access to
            if key in accessible_keys:
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
async def get_active_profile(user: UserResponse = Depends(require_auth)):
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
async def switch_profile(
    request: Request,
    switch_request: ProfileSwitchRequest,
    user: UserResponse = Depends(require_auth)
):
    """
    Switch to a different profile.
    
    Changes the active profile which affects database and document folder settings.
    Also switches the database connection to use the new profile's database.
    User must have access to the profile.
    """
    try:
        pm = get_profile_manager()
        
        if switch_request.profile_key not in pm.list_profiles():
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{switch_request.profile_key}' not found"
            )
        
        # Check user has access to the profile
        accessible_keys = await get_user_accessible_profiles(request, user.id)
        if switch_request.profile_key not in accessible_keys:
            raise HTTPException(
                status_code=403,
                detail="You don't have access to this profile"
            )
        
        success = pm.switch_profile(switch_request.profile_key)
        
        if success:
            # Get the new profile's database settings
            new_profile = pm.active_profile
            
            # Switch the database connection
            db = request.app.state.db
            await db.switch_database(
                database=new_profile.database,
                docs_collection=new_profile.collection_documents,
                chunks_collection=new_profile.collection_chunks
            )
            
            logger.info(f"Switched to profile '{switch_request.profile_key}' with database '{new_profile.database}'")
            
            return SuccessResponse(
                success=True,
                message=f"Switched to profile: {switch_request.profile_key} (database: {new_profile.database})"
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
async def create_profile(
    request: ProfileCreateRequest,
    admin: UserResponse = Depends(require_admin)
):
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
async def delete_profile(
    profile_key: str,
    admin: UserResponse = Depends(require_admin)
):
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


@router.put("/{profile_key}", response_model=SuccessResponse)
async def update_profile(
    profile_key: str,
    request: ProfileUpdateRequest,
    admin: UserResponse = Depends(require_admin)
):
    """
    Update an existing profile.
    
    Updates the profile configuration with the provided values.
    Only non-null fields will be updated.
    """
    try:
        pm = get_profile_manager()
        
        if profile_key not in pm.list_profiles():
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{profile_key}' not found"
            )
        
        # Update profile with provided values
        success = pm.update_profile(
            key=profile_key,
            name=request.name,
            description=request.description,
            documents_folders=request.documents_folders,
            database=request.database
        )
        
        if success:
            return SuccessResponse(
                success=True,
                message=f"Updated profile: {profile_key}"
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to update profile"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{profile_key}")
async def get_profile(
    request: Request,
    profile_key: str,
    user: UserResponse = Depends(require_auth)
):
    """Get a specific profile by key (requires access)."""
    try:
        pm = get_profile_manager()
        profiles = pm.list_profiles()
        
        if profile_key not in profiles:
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{profile_key}' not found"
            )
        
        # Check user has access to the profile
        accessible_keys = await get_user_accessible_profiles(request, user.id)
        if profile_key not in accessible_keys:
            raise HTTPException(
                status_code=403,
                detail="You don't have access to this profile"
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
