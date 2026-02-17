"""Profile management router."""

import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, Depends

from backend.models.schemas import (
    ProfileConfig, ProfileListResponse, ProfileSwitchRequest,
    ProfileCreateRequest, ProfileUpdateRequest, SuccessResponse,
    CloudSourceAssociation, CloudSourceCreateRequest, CloudSourceUpdateRequest,
    CloudSourceListResponse, AirbyteConfigUpdateRequest, AirbyteConfig,
    CloudSourceType
)
from backend.core.config import settings
from backend.core.profile_models import (
    ProfileModelManager, 
    GlobalModelManager,
    get_active_profile_models,
    update_profile_model_settings
)
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


# ==================== Cloud Source Management ====================


@router.get("/{profile_key}/cloud-sources", response_model=CloudSourceListResponse)
async def list_cloud_sources(
    request: Request,
    profile_key: str,
    provider_type: CloudSourceType = None,
    user: UserResponse = Depends(require_auth)
):
    """
    List cloud source connections for a profile.
    
    Optionally filter by provider type.
    """
    try:
        pm = get_profile_manager()
        
        if profile_key not in pm.list_profiles():
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
        
        # Import the CloudSourceType enum from src.profile for filtering
        from src.profile import CloudSourceType as SrcCloudSourceType
        
        src_provider_type = None
        if provider_type:
            src_provider_type = SrcCloudSourceType(provider_type.value)
        
        sources = pm.list_cloud_sources(
            profile_key=profile_key,
            provider_type=src_provider_type
        )
        
        # Convert to response schema
        cloud_sources = [
            CloudSourceAssociation(
                connection_id=s.connection_id,
                provider_type=CloudSourceType(s.provider_type.value),
                display_name=s.display_name,
                airbyte_source_id=s.airbyte_source_id,
                airbyte_connection_id=s.airbyte_connection_id,
                enabled=s.enabled,
                sync_schedule=s.sync_schedule,
                last_sync_at=s.last_sync_at,
                last_sync_status=s.last_sync_status,
                include_paths=s.include_paths,
                exclude_paths=s.exclude_paths,
                collection_prefix=s.collection_prefix,
            )
            for s in sources
        ]
        
        return CloudSourceListResponse(
            cloud_sources=cloud_sources,
            profile_key=profile_key,
            total=len(cloud_sources)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list cloud sources: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{profile_key}/cloud-sources", response_model=CloudSourceAssociation)
async def add_cloud_source(
    request: Request,
    profile_key: str,
    cloud_source: CloudSourceCreateRequest,
    user: UserResponse = Depends(require_auth)
):
    """
    Add a cloud source connection to a profile.
    
    Associates a cloud provider (Gmail, Confluence, etc.) with this profile.
    Data from this source will sync to the profile's database.
    """
    try:
        pm = get_profile_manager()
        
        if profile_key not in pm.list_profiles():
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
        
        # Import the CloudSourceType enum from src.profile
        from src.profile import CloudSourceType as SrcCloudSourceType
        
        source = pm.add_cloud_source(
            profile_key=profile_key,
            connection_id=cloud_source.connection_id,
            provider_type=SrcCloudSourceType(cloud_source.provider_type.value),
            display_name=cloud_source.display_name or "",
            airbyte_source_id=cloud_source.airbyte_source_id,
            airbyte_connection_id=cloud_source.airbyte_connection_id,
            collection_prefix=cloud_source.collection_prefix,
            enabled=cloud_source.enabled,
            sync_schedule=cloud_source.sync_schedule,
            include_paths=cloud_source.include_paths,
            exclude_paths=cloud_source.exclude_paths,
        )
        
        if not source:
            raise HTTPException(
                status_code=400,
                detail=f"Cloud source '{cloud_source.connection_id}' already exists in this profile"
            )
        
        return CloudSourceAssociation(
            connection_id=source.connection_id,
            provider_type=CloudSourceType(source.provider_type.value),
            display_name=source.display_name,
            airbyte_source_id=source.airbyte_source_id,
            airbyte_connection_id=source.airbyte_connection_id,
            enabled=source.enabled,
            sync_schedule=source.sync_schedule,
            include_paths=source.include_paths,
            exclude_paths=source.exclude_paths,
            collection_prefix=source.collection_prefix,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add cloud source: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{profile_key}/cloud-sources/{connection_id}", response_model=SuccessResponse)
async def update_cloud_source(
    request: Request,
    profile_key: str,
    connection_id: str,
    cloud_source: CloudSourceUpdateRequest,
    user: UserResponse = Depends(require_auth)
):
    """
    Update a cloud source connection.
    
    Updates the configuration for an existing cloud source.
    """
    try:
        pm = get_profile_manager()
        
        if profile_key not in pm.list_profiles():
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
        
        # Build updates dict
        updates = {}
        if cloud_source.display_name is not None:
            updates["display_name"] = cloud_source.display_name
        if cloud_source.enabled is not None:
            updates["enabled"] = cloud_source.enabled
        if cloud_source.sync_schedule is not None:
            updates["sync_schedule"] = cloud_source.sync_schedule
        if cloud_source.airbyte_source_id is not None:
            updates["airbyte_source_id"] = cloud_source.airbyte_source_id
        if cloud_source.airbyte_connection_id is not None:
            updates["airbyte_connection_id"] = cloud_source.airbyte_connection_id
        if cloud_source.collection_prefix is not None:
            updates["collection_prefix"] = cloud_source.collection_prefix
        if cloud_source.include_paths is not None:
            updates["include_paths"] = cloud_source.include_paths
        if cloud_source.exclude_paths is not None:
            updates["exclude_paths"] = cloud_source.exclude_paths
        
        success = pm.update_cloud_source(
            profile_key=profile_key,
            connection_id=connection_id,
            **updates
        )
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Cloud source '{connection_id}' not found in this profile"
            )
        
        return SuccessResponse(
            success=True,
            message=f"Updated cloud source: {connection_id}"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update cloud source: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{profile_key}/cloud-sources/{connection_id}", response_model=SuccessResponse)
async def remove_cloud_source(
    request: Request,
    profile_key: str,
    connection_id: str,
    user: UserResponse = Depends(require_auth)
):
    """
    Remove a cloud source connection from a profile.
    
    This does NOT delete the synced data, only the association.
    """
    try:
        pm = get_profile_manager()
        
        if profile_key not in pm.list_profiles():
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
        
        success = pm.remove_cloud_source(
            profile_key=profile_key,
            connection_id=connection_id
        )
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Cloud source '{connection_id}' not found in this profile"
            )
        
        return SuccessResponse(
            success=True,
            message=f"Removed cloud source: {connection_id}"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to remove cloud source: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Airbyte Configuration ====================


@router.get("/{profile_key}/airbyte", response_model=AirbyteConfig)
async def get_airbyte_config(
    request: Request,
    profile_key: str,
    user: UserResponse = Depends(require_auth)
):
    """
    Get Airbyte configuration for a profile.
    """
    try:
        pm = get_profile_manager()
        
        if profile_key not in pm.list_profiles():
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
        
        airbyte_config = pm.get_airbyte_config(profile_key)
        
        if not airbyte_config:
            return AirbyteConfig()
        
        return AirbyteConfig(
            workspace_id=airbyte_config.workspace_id,
            workspace_name=airbyte_config.workspace_name,
            destination_id=airbyte_config.destination_id,
            default_sync_mode=airbyte_config.default_sync_mode,
            default_schedule_type=airbyte_config.default_schedule_type,
            default_schedule_cron=airbyte_config.default_schedule_cron,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get Airbyte config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{profile_key}/airbyte", response_model=SuccessResponse)
async def update_airbyte_config(
    request: Request,
    profile_key: str,
    airbyte_config: AirbyteConfigUpdateRequest,
    admin: UserResponse = Depends(require_admin)
):
    """
    Update Airbyte configuration for a profile.
    
    Admin only. Sets the Airbyte workspace and destination for this profile.
    """
    try:
        pm = get_profile_manager()
        
        if profile_key not in pm.list_profiles():
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{profile_key}' not found"
            )
        
        success = pm.set_airbyte_config(
            profile_key=profile_key,
            workspace_id=airbyte_config.workspace_id,
            workspace_name=airbyte_config.workspace_name,
            destination_id=airbyte_config.destination_id,
            default_sync_mode=airbyte_config.default_sync_mode,
            default_schedule_type=airbyte_config.default_schedule_type,
            default_schedule_cron=airbyte_config.default_schedule_cron,
        )
        
        if not success:
            raise HTTPException(
                status_code=500,
                detail="Failed to update Airbyte configuration"
            )
        
        return SuccessResponse(
            success=True,
            message=f"Updated Airbyte configuration for profile: {profile_key}"
        )
        
    except Exception as e:
        logger.error(f"Failed to update Airbyte configuration for {profile_key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============== Profile Model Version Management ==============

@router.get("/{profile_key}/models", response_model=dict)
async def get_profile_models(
    profile_key: str,
    request: Request,
    user: UserResponse = Depends(require_auth)
):
    """
    Get model configuration for a specific profile.
    
    Returns the effective model configuration considering profile settings and global defaults.
    """
    try:
        pm = get_profile_manager()
        
        # Check if user has access to this profile
        accessible_profiles = await get_user_accessible_profiles(user, request)
        if profile_key not in accessible_profiles:
            raise HTTPException(
                status_code=403,
                detail="Access denied to this profile"
            )
        
        if profile_key not in pm.list_profiles():
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{profile_key}' not found"
            )
        
        profile_config = pm.get_profile(profile_key)
        model_config = get_active_profile_models(profile_config)
        
        return {
            "profile": profile_key,
            "models": model_config,
            "is_default": profile_key == pm.active_profile_key
        }
        
    except Exception as e:
        logger.error(f"Failed to get profile models for {profile_key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{profile_key}/models", response_model=SuccessResponse)
async def update_profile_models(
    profile_key: str,
    request: Request,
    model_updates: dict,
    user: UserResponse = Depends(require_auth)
):
    """
    Update model configuration for a specific profile.
    
    Only updates the specified fields. Other fields retain their current values.
    """
    try:
        pm = get_profile_manager()
        
        # Check if user has access to this profile
        accessible_profiles = await get_user_accessible_profiles(user, request)
        if profile_key not in accessible_profiles:
            raise HTTPException(
                status_code=403,
                detail="Access denied to this profile"
            )
        
        if profile_key not in pm.list_profiles():
            raise HTTPException(
                status_code=404,
                detail=f"Profile '{profile_key}' not found"
            )
        
        profile_config = pm.get_profile(profile_key)
        updates = update_profile_model_settings(profile_config, **model_updates)
        
        # Save updated profile
        pm.save_profile(profile_key, profile_config)
        
        return SuccessResponse(
            success=True,
            message=f"Updated model configuration for profile: {profile_key}",
            data=updates
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update profile models for {profile_key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/global/models", response_model=dict)
async def get_global_models(
    user: UserResponse = Depends(require_auth)
):
    """
    Get global/default model configuration.
    
    Admin only. Shows the system-wide default model settings.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Admin access required"
        )
    
    try:
        global_config = GlobalModelManager.get_global_config()
        return {
            "models": global_config,
            "active_profile": get_profile_manager().active_profile_key
        }
        
    except Exception as e:
        logger.error(f"Failed to get global models: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/global/models", response_model=SuccessResponse)
async def update_global_models(
    request: Request,
    model_updates: dict,
    user: UserResponse = Depends(require_admin)
):
    """
    Update global/default model configuration.
    
    Admin only. Updates system-wide default model settings.
    """
    try:
        updates = GlobalModelManager.update_global_models(**model_updates)
        
        # Persist to database
        db = request.app.state.db
        collection = db.db["system_config"]
        await collection.update_one(
            {"_id": "global_models"},
            {"$set": {**updates, "updated_at": datetime.now().isoformat()}},
            upsert=True
        )
        
        return SuccessResponse(
            success=True,
            message="Updated global model configuration",
            data=updates
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update global models: {e}")
        raise HTTPException(status_code=500, detail=str(e))
