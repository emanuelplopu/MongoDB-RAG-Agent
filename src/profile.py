"""Profile management for multi-project support.

Each profile defines a separate project with its own:
- Document folder(s)
- MongoDB database
- Collection names
- Search index names
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Default profiles configuration file path
DEFAULT_PROFILES_PATH = "profiles.yaml"


class ProfileConfig(BaseModel):
    """Configuration for a single profile."""
    
    name: str = Field(..., description="Display name for the profile")
    description: str = Field(default="", description="Profile description")
    
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
