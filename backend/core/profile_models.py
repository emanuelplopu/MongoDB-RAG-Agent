"""
Profile-Based Model Version Management

Manages model versions at the profile level, allowing different projects
to use different model configurations while maintaining system-wide defaults.
"""

import logging
from typing import Optional, Dict, Any
from backend.models.schemas import ProfileConfig
from backend.core.model_versions import get_model_by_id
from backend.core.config import settings

logger = logging.getLogger(__name__)


class ProfileModelManager:
    """Manages model versions for individual profiles."""
    
    def __init__(self, profile_config: ProfileConfig):
        """Initialize with profile configuration."""
        self.profile = profile_config
        self._resolved_models = {}
    
    def get_orchestrator_model(self) -> str:
        """Get orchestrator model for this profile."""
        # Priority: Profile setting > Global setting > Default
        model = (
            self.profile.orchestrator_model or
            settings.orchestrator_model or
            "gpt-4o"
        )
        
        # Validate model exists
        if get_model_by_id(model):
            return model
        else:
            logger.warning(f"Invalid orchestrator model '{model}' for profile '{self.profile.name}', falling back to default")
            return "gpt-4o"
    
    def get_orchestrator_provider(self) -> str:
        """Get orchestrator provider for this profile."""
        return (
            self.profile.orchestrator_provider or
            settings.orchestrator_provider or
            "openai"
        )
    
    def get_worker_model(self) -> str:
        """Get worker model for this profile."""
        # Priority: Profile setting > Global setting > Default
        model = (
            self.profile.worker_model or
            settings.worker_model or
            "gpt-4o-mini"
        )
        
        # Validate model exists
        if get_model_by_id(model):
            return model
        else:
            logger.warning(f"Invalid worker model '{model}' for profile '{self.profile.name}', falling back to default")
            return "gpt-4o-mini"
    
    def get_worker_provider(self) -> str:
        """Get worker provider for this profile."""
        return (
            self.profile.worker_provider or
            settings.worker_provider or
            "openai"
        )
    
    def get_embedding_model(self) -> str:
        """Get embedding model for this profile."""
        # Priority: Profile setting > Global setting > Default
        model = (
            self.profile.embedding_model or
            settings.embedding_model or
            "text-embedding-3-small"
        )
        
        # Validate model exists
        if get_model_by_id(model):
            return model
        else:
            logger.warning(f"Invalid embedding model '{model}' for profile '{self.profile.name}', falling back to default")
            return "text-embedding-3-small"
    
    def get_embedding_provider(self) -> str:
        """Get embedding provider for this profile."""
        return (
            self.profile.embedding_provider or
            settings.embedding_provider or
            "openai"
        )
    
    def get_full_config(self) -> Dict[str, Any]:
        """Get complete model configuration for this profile."""
        return {
            "orchestrator": {
                "model": self.get_orchestrator_model(),
                "provider": self.get_orchestrator_provider()
            },
            "worker": {
                "model": self.get_worker_model(),
                "provider": self.get_worker_provider()
            },
            "embedding": {
                "model": self.get_embedding_model(),
                "provider": self.get_embedding_provider()
            }
        }
    
    def update_profile_models(self, 
                            orchestrator_model: Optional[str] = None,
                            orchestrator_provider: Optional[str] = None,
                            worker_model: Optional[str] = None,
                            worker_provider: Optional[str] = None,
                            embedding_model: Optional[str] = None,
                            embedding_provider: Optional[str] = None) -> Dict[str, Any]:
        """Update model settings for this profile."""
        updates = {}
        
        if orchestrator_model is not None:
            if get_model_by_id(orchestrator_model):
                self.profile.orchestrator_model = orchestrator_model
                updates["orchestrator_model"] = orchestrator_model
            else:
                raise ValueError(f"Invalid orchestrator model: {orchestrator_model}")
        
        if orchestrator_provider is not None:
            self.profile.orchestrator_provider = orchestrator_provider
            updates["orchestrator_provider"] = orchestrator_provider
        
        if worker_model is not None:
            if get_model_by_id(worker_model):
                self.profile.worker_model = worker_model
                updates["worker_model"] = worker_model
            else:
                raise ValueError(f"Invalid worker model: {worker_model}")
        
        if worker_provider is not None:
            self.profile.worker_provider = worker_provider
            updates["worker_provider"] = worker_provider
        
        if embedding_model is not None:
            if get_model_by_id(embedding_model):
                self.profile.embedding_model = embedding_model
                updates["embedding_model"] = embedding_model
            else:
                raise ValueError(f"Invalid embedding model: {embedding_model}")
        
        if embedding_provider is not None:
            self.profile.embedding_provider = embedding_provider
            updates["embedding_provider"] = embedding_provider
        
        logger.info(f"Updated profile '{self.profile.name}' model settings: {updates}")
        return updates


class GlobalModelManager:
    """Manages global/default model versions."""
    
    @staticmethod
    def get_global_config() -> Dict[str, Any]:
        """Get global model configuration."""
        return {
            "orchestrator": {
                "model": settings.orchestrator_model,
                "provider": settings.orchestrator_provider
            },
            "worker": {
                "model": settings.worker_model,
                "provider": settings.worker_provider
            },
            "embedding": {
                "model": settings.embedding_model,
                "provider": settings.embedding_provider
            }
        }
    
    @staticmethod
    def update_global_models(orchestrator_model: Optional[str] = None,
                           worker_model: Optional[str] = None,
                           embedding_model: Optional[str] = None) -> Dict[str, Any]:
        """Update global model settings."""
        updates = {}
        
        if orchestrator_model is not None:
            if get_model_by_id(orchestrator_model):
                settings.orchestrator_model = orchestrator_model
                updates["orchestrator_model"] = orchestrator_model
            else:
                raise ValueError(f"Invalid orchestrator model: {orchestrator_model}")
        
        if worker_model is not None:
            if get_model_by_id(worker_model):
                settings.worker_model = worker_model
                updates["worker_model"] = worker_model
            else:
                raise ValueError(f"Invalid worker model: {worker_model}")
        
        if embedding_model is not None:
            if get_model_by_id(embedding_model):
                settings.embedding_model = embedding_model
                updates["embedding_model"] = embedding_model
            else:
                raise ValueError(f"Invalid embedding model: {embedding_model}")
        
        logger.info(f"Updated global model settings: {updates}")
        return updates


# Convenience functions
def get_active_profile_models(profile_config: ProfileConfig) -> Dict[str, Any]:
    """Get model configuration for the active profile."""
    manager = ProfileModelManager(profile_config)
    return manager.get_full_config()


def update_profile_model_settings(profile_config: ProfileConfig,
                                **kwargs) -> Dict[str, Any]:
    """Update model settings for a profile."""
    manager = ProfileModelManager(profile_config)
    return manager.update_profile_models(**kwargs)