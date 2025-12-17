"""Profile management for multi-project support.

Each profile defines a separate project with its own:
- Document folder(s)
- MongoDB database
- Collection names
- Search index names
- Airbyte integration settings
- Cloud source connections
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from pydantic import BaseModel, Field
from enum import Enum

logger = logging.getLogger(__name__)

# Default profiles configuration file path
DEFAULT_PROFILES_PATH = "profiles.yaml"


class CloudSourceType(str, Enum):
    """Supported cloud source types."""
    GOOGLE_DRIVE = "google_drive"
    DROPBOX = "dropbox"
    ONEDRIVE = "onedrive"
    WEBDAV = "webdav"
    CONFLUENCE = "confluence"
    JIRA = "jira"
    GMAIL = "gmail"
    OUTLOOK = "outlook"
    IMAP = "imap"


class CloudSourceAssociation(BaseModel):
    """Association between a profile and a cloud source connection.
    
    Each profile can have multiple cloud source connections,
    and each connection syncs data to the profile's database.
    """
    
    # Connection identification
    connection_id: str = Field(..., description="Unique ID of the cloud source connection")
    provider_type: CloudSourceType = Field(..., description="Type of cloud provider")
    display_name: str = Field(default="", description="User-friendly name for this connection")
    
    # Airbyte-specific IDs (for Airbyte-backed providers)
    airbyte_source_id: Optional[str] = Field(default=None, description="Airbyte source ID")
    airbyte_connection_id: Optional[str] = Field(default=None, description="Airbyte sync connection ID")
    
    # Sync configuration
    enabled: bool = Field(default=True, description="Whether sync is enabled")
    sync_schedule: Optional[str] = Field(default=None, description="Cron schedule for sync (e.g., '0 */6 * * *')")
    last_sync_at: Optional[str] = Field(default=None, description="ISO timestamp of last sync")
    last_sync_status: Optional[str] = Field(default=None, description="Status of last sync (success/error)")
    
    # Folder/path filters
    include_paths: List[str] = Field(default_factory=list, description="Paths/folders to include")
    exclude_paths: List[str] = Field(default_factory=list, description="Paths/folders to exclude")
    
    # Collection prefix for synced data
    collection_prefix: str = Field(default="", description="Prefix for MongoDB collections from this source")


class AirbyteConfig(BaseModel):
    """Airbyte-specific configuration for a profile.
    
    Each profile can have its own Airbyte workspace and destination,
    allowing complete isolation of synced data.
    """
    
    # Airbyte workspace (one per profile for isolation)
    workspace_id: Optional[str] = Field(default=None, description="Airbyte workspace ID for this profile")
    workspace_name: Optional[str] = Field(default=None, description="Airbyte workspace name")
    
    # Airbyte destination (points to profile's MongoDB database)
    destination_id: Optional[str] = Field(default=None, description="Airbyte MongoDB destination ID")
    
    # Default sync settings
    default_sync_mode: str = Field(default="incremental", description="Default sync mode (full_refresh/incremental)")
    default_schedule_type: str = Field(default="manual", description="Default schedule type (manual/scheduled)")
    default_schedule_cron: Optional[str] = Field(default=None, description="Default cron expression for scheduled syncs")


class ProfileConfig(BaseModel):
    """Configuration for a single profile.
    
    A profile represents a complete project workspace with:
    - Document storage (local folders)
    - MongoDB database for RAG data
    - Cloud source integrations (via Airbyte)
    - Search indexes
    """
    
    name: str = Field(..., description="Display name for the profile")
    description: str = Field(default="", description="Profile description")
    
    # Owner/access control (for multi-user scenarios)
    owner_user_id: Optional[str] = Field(default=None, description="User ID of the profile owner")
    
    # Document sources
    documents_folders: List[str] = Field(
        default_factory=lambda: ["documents"],
        description="List of document folders for this profile"
    )
    
    # MongoDB configuration
    database: str = Field(default="rag_db", description="MongoDB database name")
    collection_documents: str = Field(default="documents", description="Documents collection")
    collection_chunks: str = Field(default="chunks", description="Chunks collection")
    
    # Search index names
    vector_index: str = Field(default="vector_index", description="Vector search index name")
    text_index: str = Field(default="text_index", description="Text search index name")
    
    # Optional overrides
    embedding_model: Optional[str] = Field(default=None, description="Override embedding model")
    llm_model: Optional[str] = Field(default=None, description="Override LLM model")
    
    # Airbyte integration
    airbyte: Optional[AirbyteConfig] = Field(
        default=None, 
        description="Airbyte configuration for this profile"
    )
    
    # Cloud source connections
    cloud_sources: List[CloudSourceAssociation] = Field(
        default_factory=list,
        description="Cloud source connections associated with this profile"
    )
    
    def get_cloud_source(self, connection_id: str) -> Optional[CloudSourceAssociation]:
        """Get a cloud source by connection ID."""
        for source in self.cloud_sources:
            if source.connection_id == connection_id:
                return source
        return None
    
    def get_cloud_sources_by_type(self, provider_type: CloudSourceType) -> List[CloudSourceAssociation]:
        """Get all cloud sources of a specific type."""
        return [s for s in self.cloud_sources if s.provider_type == provider_type]
    
    def add_cloud_source(self, source: CloudSourceAssociation) -> bool:
        """Add a cloud source association."""
        if self.get_cloud_source(source.connection_id):
            return False  # Already exists
        self.cloud_sources.append(source)
        return True
    
    def remove_cloud_source(self, connection_id: str) -> bool:
        """Remove a cloud source association."""
        for i, source in enumerate(self.cloud_sources):
            if source.connection_id == connection_id:
                self.cloud_sources.pop(i)
                return True
        return False


class ProfilesConfig(BaseModel):
    """Root configuration containing all profiles."""
    
    active_profile: str = Field(default="default", description="Currently active profile")
    profiles: Dict[str, ProfileConfig] = Field(
        default_factory=dict,
        description="Dictionary of profile configurations"
    )


class ProfileManager:
    """Manages profile loading, switching, and persistence."""
    
    def __init__(self, profiles_path: Optional[str] = None):
        """
        Initialize profile manager.
        
        Args:
            profiles_path: Path to profiles.yaml file. Uses default if not specified.
        """
        self.profiles_path = Path(profiles_path or DEFAULT_PROFILES_PATH)
        self._config: Optional[ProfilesConfig] = None
        self._load_profiles()
    
    def _load_profiles(self) -> None:
        """Load profiles from YAML configuration file."""
        if not self.profiles_path.exists():
            logger.info(f"No profiles file found at {self.profiles_path}, creating default")
            self._config = self._create_default_config()
            self._save_profiles()
            return
        
        try:
            with open(self.profiles_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            
            # Parse profiles
            profiles_data = data.get('profiles', {})
            profiles = {}
            for key, value in profiles_data.items():
                profiles[key] = ProfileConfig(**value)
            
            self._config = ProfilesConfig(
                active_profile=data.get('active_profile', 'default'),
                profiles=profiles
            )
            
            logger.info(f"Loaded {len(profiles)} profiles, active: {self._config.active_profile}")
            
        except Exception as e:
            logger.error(f"Failed to load profiles: {e}")
            self._config = self._create_default_config()
    
    def _create_default_config(self) -> ProfilesConfig:
        """Create default configuration with a single 'default' profile."""
        default_profile = ProfileConfig(
            name="Default",
            description="Default project profile",
            documents_folders=["documents"],
            database="rag_db",
            collection_documents="documents",
            collection_chunks="chunks",
            vector_index="vector_index",
            text_index="text_index"
        )
        
        return ProfilesConfig(
            active_profile="default",
            profiles={"default": default_profile}
        )
    
    def _save_profiles(self) -> None:
        """Save current profiles to YAML file."""
        if not self._config:
            return
        
        data = {
            'active_profile': self._config.active_profile,
            'profiles': {}
        }
        
        for key, profile in self._config.profiles.items():
            data['profiles'][key] = profile.model_dump(exclude_none=True)
        
        try:
            with open(self.profiles_path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            logger.info(f"Saved profiles to {self.profiles_path}")
        except Exception as e:
            logger.error(f"Failed to save profiles: {e}")
    
    @property
    def active_profile_name(self) -> str:
        """Get the name of the currently active profile."""
        return self._config.active_profile if self._config else "default"
    
    @property
    def active_profile_key(self) -> str:
        """Get the key of the currently active profile (alias for active_profile_name)."""
        return self.active_profile_name
    
    @property
    def active_profile(self) -> ProfileConfig:
        """Get the currently active profile configuration."""
        if not self._config:
            return self._create_default_config().profiles["default"]
        
        profile = self._config.profiles.get(self._config.active_profile)
        if not profile:
            logger.warning(f"Active profile '{self._config.active_profile}' not found, using default")
            if "default" in self._config.profiles:
                return self._config.profiles["default"]
            return self._create_default_config().profiles["default"]
        
        return profile
    
    def list_profiles(self) -> Dict[str, ProfileConfig]:
        """Get all available profiles."""
        if not self._config:
            return {}
        return self._config.profiles
    
    def get_profile(self, name: str) -> Optional[ProfileConfig]:
        """Get a specific profile by name."""
        if not self._config:
            return None
        return self._config.profiles.get(name)
    
    def switch_profile(self, name: str) -> bool:
        """
        Switch to a different profile.
        
        Args:
            name: Profile name to switch to
            
        Returns:
            True if switch was successful, False if profile not found
        """
        if not self._config:
            return False
        
        if name not in self._config.profiles:
            logger.error(f"Profile '{name}' not found")
            return False
        
        self._config.active_profile = name
        self._save_profiles()
        logger.info(f"Switched to profile: {name}")
        return True
    
    def create_profile(
        self,
        key: str,
        name: str,
        documents_folders: List[str],
        database: Optional[str] = None,
        description: str = "",
        **kwargs
    ) -> bool:
        """
        Create a new profile.
        
        Args:
            key: Profile key/identifier (used in CLI)
            name: Display name
            documents_folders: List of document folder paths
            database: MongoDB database name (defaults to rag_{key})
            description: Profile description
            **kwargs: Additional ProfileConfig fields
            
        Returns:
            True if creation was successful
        """
        if not self._config:
            return False
        
        if key in self._config.profiles:
            logger.error(f"Profile '{key}' already exists")
            return False
        
        # Create profile with sensible defaults based on key
        profile = ProfileConfig(
            name=name,
            description=description,
            documents_folders=documents_folders,
            database=database or f"rag_{key}",
            collection_documents=kwargs.get('collection_documents', 'documents'),
            collection_chunks=kwargs.get('collection_chunks', 'chunks'),
            vector_index=kwargs.get('vector_index', 'vector_index'),
            text_index=kwargs.get('text_index', 'text_index'),
            embedding_model=kwargs.get('embedding_model'),
            llm_model=kwargs.get('llm_model')
        )
        
        self._config.profiles[key] = profile
        self._save_profiles()
        logger.info(f"Created profile: {key}")
        return True
    
    def update_profile(
        self,
        key: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        documents_folders: Optional[List[str]] = None,
        database: Optional[str] = None,
        **kwargs
    ) -> bool:
        """
        Update an existing profile.
        
        Args:
            key: Profile key to update
            name: New display name (optional)
            description: New description (optional)
            documents_folders: New list of document folders (optional)
            database: New database name (optional)
            **kwargs: Additional ProfileConfig fields
            
        Returns:
            True if update was successful
        """
        if not self._config:
            return False
        
        if key not in self._config.profiles:
            logger.error(f"Profile '{key}' not found")
            return False
        
        profile = self._config.profiles[key]
        
        # Update only provided fields
        if name is not None:
            profile.name = name
        if description is not None:
            profile.description = description
        if documents_folders is not None:
            profile.documents_folders = documents_folders
        if database is not None:
            profile.database = database
        
        # Handle additional kwargs
        for field in ['collection_documents', 'collection_chunks', 'vector_index', 'text_index', 'embedding_model', 'llm_model']:
            if field in kwargs and kwargs[field] is not None:
                setattr(profile, field, kwargs[field])
        
        self._save_profiles()
        logger.info(f"Updated profile: {key}")
        return True

    def delete_profile(self, name: str) -> bool:
        """
        Delete a profile.
        
        Args:
            name: Profile name to delete
            
        Returns:
            True if deletion was successful
        """
        if not self._config:
            return False
        
        if name not in self._config.profiles:
            logger.error(f"Profile '{name}' not found")
            return False
        
        if name == "default":
            logger.error("Cannot delete the default profile")
            return False
        
        if name == self._config.active_profile:
            logger.error("Cannot delete the active profile. Switch to another profile first.")
            return False
        
        del self._config.profiles[name]
        self._save_profiles()
        logger.info(f"Deleted profile: {name}")
        return True
    
    def get_all_document_folders(self) -> List[str]:
        """Get all document folders for the active profile."""
        profile = self.active_profile
        return profile.documents_folders
    
    def get_primary_document_folder(self) -> str:
        """Get the primary (first) document folder for the active profile."""
        folders = self.get_all_document_folders()
        return folders[0] if folders else "documents"
    
    # ==================== Cloud Source Management ====================
    
    def add_cloud_source(
        self,
        profile_key: str,
        connection_id: str,
        provider_type: CloudSourceType,
        display_name: str = "",
        airbyte_source_id: Optional[str] = None,
        airbyte_connection_id: Optional[str] = None,
        collection_prefix: Optional[str] = None,
        **kwargs
    ) -> Optional[CloudSourceAssociation]:
        """
        Add a cloud source connection to a profile.
        
        Args:
            profile_key: Profile to add the cloud source to
            connection_id: Unique connection identifier
            provider_type: Type of cloud provider
            display_name: User-friendly name
            airbyte_source_id: Airbyte source ID (for Airbyte-backed providers)
            airbyte_connection_id: Airbyte connection ID
            collection_prefix: Prefix for MongoDB collections
            **kwargs: Additional CloudSourceAssociation fields
            
        Returns:
            The created CloudSourceAssociation, or None if failed
        """
        if not self._config:
            return None
        
        profile = self._config.profiles.get(profile_key)
        if not profile:
            logger.error(f"Profile '{profile_key}' not found")
            return None
        
        # Default collection prefix based on provider type
        if collection_prefix is None:
            collection_prefix = f"{provider_type.value}_"
        
        source = CloudSourceAssociation(
            connection_id=connection_id,
            provider_type=provider_type,
            display_name=display_name or f"{provider_type.value} connection",
            airbyte_source_id=airbyte_source_id,
            airbyte_connection_id=airbyte_connection_id,
            collection_prefix=collection_prefix,
            enabled=kwargs.get('enabled', True),
            sync_schedule=kwargs.get('sync_schedule'),
            include_paths=kwargs.get('include_paths', []),
            exclude_paths=kwargs.get('exclude_paths', []),
        )
        
        if not profile.add_cloud_source(source):
            logger.error(f"Cloud source '{connection_id}' already exists in profile '{profile_key}'")
            return None
        
        self._save_profiles()
        logger.info(f"Added cloud source '{connection_id}' to profile '{profile_key}'")
        return source
    
    def remove_cloud_source(self, profile_key: str, connection_id: str) -> bool:
        """
        Remove a cloud source connection from a profile.
        
        Args:
            profile_key: Profile to remove from
            connection_id: Connection ID to remove
            
        Returns:
            True if removed successfully
        """
        if not self._config:
            return False
        
        profile = self._config.profiles.get(profile_key)
        if not profile:
            logger.error(f"Profile '{profile_key}' not found")
            return False
        
        if not profile.remove_cloud_source(connection_id):
            logger.error(f"Cloud source '{connection_id}' not found in profile '{profile_key}'")
            return False
        
        self._save_profiles()
        logger.info(f"Removed cloud source '{connection_id}' from profile '{profile_key}'")
        return True
    
    def get_cloud_source(
        self, 
        profile_key: str, 
        connection_id: str
    ) -> Optional[CloudSourceAssociation]:
        """Get a specific cloud source from a profile."""
        if not self._config:
            return None
        
        profile = self._config.profiles.get(profile_key)
        if not profile:
            return None
        
        return profile.get_cloud_source(connection_id)
    
    def list_cloud_sources(
        self, 
        profile_key: Optional[str] = None,
        provider_type: Optional[CloudSourceType] = None
    ) -> List[CloudSourceAssociation]:
        """
        List cloud sources, optionally filtered by profile and/or type.
        
        Args:
            profile_key: If provided, only list sources for this profile
            provider_type: If provided, only list sources of this type
            
        Returns:
            List of matching cloud source associations
        """
        if not self._config:
            return []
        
        sources = []
        
        if profile_key:
            profile = self._config.profiles.get(profile_key)
            if profile:
                if provider_type:
                    sources = profile.get_cloud_sources_by_type(provider_type)
                else:
                    sources = list(profile.cloud_sources)
        else:
            # List from all profiles
            for profile in self._config.profiles.values():
                if provider_type:
                    sources.extend(profile.get_cloud_sources_by_type(provider_type))
                else:
                    sources.extend(profile.cloud_sources)
        
        return sources
    
    def update_cloud_source(
        self,
        profile_key: str,
        connection_id: str,
        **updates
    ) -> bool:
        """
        Update a cloud source connection.
        
        Args:
            profile_key: Profile containing the cloud source
            connection_id: Connection ID to update
            **updates: Fields to update (display_name, enabled, sync_schedule, etc.)
            
        Returns:
            True if updated successfully
        """
        if not self._config:
            return False
        
        profile = self._config.profiles.get(profile_key)
        if not profile:
            logger.error(f"Profile '{profile_key}' not found")
            return False
        
        source = profile.get_cloud_source(connection_id)
        if not source:
            logger.error(f"Cloud source '{connection_id}' not found in profile '{profile_key}'")
            return False
        
        # Update allowed fields
        allowed_fields = [
            'display_name', 'enabled', 'sync_schedule', 
            'airbyte_source_id', 'airbyte_connection_id',
            'last_sync_at', 'last_sync_status',
            'include_paths', 'exclude_paths', 'collection_prefix'
        ]
        
        for field, value in updates.items():
            if field in allowed_fields and value is not None:
                setattr(source, field, value)
        
        self._save_profiles()
        logger.info(f"Updated cloud source '{connection_id}' in profile '{profile_key}'")
        return True
    
    # ==================== Airbyte Configuration ====================
    
    def set_airbyte_config(
        self,
        profile_key: str,
        workspace_id: Optional[str] = None,
        workspace_name: Optional[str] = None,
        destination_id: Optional[str] = None,
        default_sync_mode: Optional[str] = None,
        default_schedule_type: Optional[str] = None,
        default_schedule_cron: Optional[str] = None,
    ) -> bool:
        """
        Set or update Airbyte configuration for a profile.
        
        Args:
            profile_key: Profile to configure
            workspace_id: Airbyte workspace ID
            workspace_name: Airbyte workspace name
            destination_id: Airbyte MongoDB destination ID
            default_sync_mode: Default sync mode (full_refresh/incremental)
            default_schedule_type: Default schedule type (manual/scheduled)
            default_schedule_cron: Default cron expression
            
        Returns:
            True if configured successfully
        """
        if not self._config:
            return False
        
        profile = self._config.profiles.get(profile_key)
        if not profile:
            logger.error(f"Profile '{profile_key}' not found")
            return False
        
        # Create or update Airbyte config
        if profile.airbyte is None:
            profile.airbyte = AirbyteConfig()
        
        if workspace_id is not None:
            profile.airbyte.workspace_id = workspace_id
        if workspace_name is not None:
            profile.airbyte.workspace_name = workspace_name
        if destination_id is not None:
            profile.airbyte.destination_id = destination_id
        if default_sync_mode is not None:
            profile.airbyte.default_sync_mode = default_sync_mode
        if default_schedule_type is not None:
            profile.airbyte.default_schedule_type = default_schedule_type
        if default_schedule_cron is not None:
            profile.airbyte.default_schedule_cron = default_schedule_cron
        
        self._save_profiles()
        logger.info(f"Updated Airbyte config for profile '{profile_key}'")
        return True
    
    def get_airbyte_config(self, profile_key: str) -> Optional[AirbyteConfig]:
        """Get Airbyte configuration for a profile."""
        if not self._config:
            return None
        
        profile = self._config.profiles.get(profile_key)
        if not profile:
            return None
        
        return profile.airbyte


# Global profile manager instance
_profile_manager: Optional[ProfileManager] = None


def get_profile_manager(profiles_path: Optional[str] = None) -> ProfileManager:
    """
    Get or create the global profile manager instance.
    
    Args:
        profiles_path: Path to profiles.yaml. Only used on first call.
        
    Returns:
        ProfileManager instance
    """
    global _profile_manager
    
    if _profile_manager is None:
        _profile_manager = ProfileManager(profiles_path)
    
    return _profile_manager


def reset_profile_manager() -> None:
    """Reset the global profile manager (useful for testing)."""
    global _profile_manager
    _profile_manager = None


def get_active_profile() -> ProfileConfig:
    """Get the currently active profile configuration."""
    return get_profile_manager().active_profile
