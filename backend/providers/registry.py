"""
Provider Factory and Registry

Central registry for all cloud source provider implementations.
Handles provider instantiation and discovery.
"""

import logging
from typing import Optional, Type

from backend.providers.base import (
    CloudSourceProvider,
    ProviderType,
    ProviderCapabilities,
    ConnectionCredentials,
)

logger = logging.getLogger(__name__)

# Provider registry - maps provider types to implementation classes
_provider_registry: dict[ProviderType, Type[CloudSourceProvider]] = {}


def register_provider(provider_type: ProviderType):
    """
    Decorator to register a provider implementation.
    
    Usage:
        @register_provider(ProviderType.GOOGLE_DRIVE)
        class GoogleDriveProvider(CloudSourceProvider):
            ...
    """
    def decorator(cls: Type[CloudSourceProvider]):
        _provider_registry[provider_type] = cls
        logger.debug(f"Registered provider: {provider_type.value} -> {cls.__name__}")
        return cls
    return decorator


def get_provider_class(provider_type: ProviderType) -> Optional[Type[CloudSourceProvider]]:
    """Get the provider class for a given type."""
    return _provider_registry.get(provider_type)


def create_provider(
    provider_type: ProviderType,
    credentials: Optional[ConnectionCredentials] = None
) -> CloudSourceProvider:
    """
    Create a provider instance.
    
    Args:
        provider_type: The type of provider to create
        credentials: Optional credentials for authentication
        
    Returns:
        Initialized provider instance
        
    Raises:
        ValueError: If provider type is not registered
    """
    cls = get_provider_class(provider_type)
    if not cls:
        raise ValueError(f"No provider registered for type: {provider_type}")
    
    return cls(credentials)


def list_registered_providers() -> list[ProviderType]:
    """List all registered provider types."""
    return list(_provider_registry.keys())


def is_provider_available(provider_type: ProviderType) -> bool:
    """Check if a provider implementation is available."""
    return provider_type in _provider_registry


# Import provider implementations to trigger registration
# These imports are at the bottom to avoid circular imports
def _load_providers():
    """Load all provider implementations."""
    # Direct SDK providers (file storage)
    try:
        from backend.providers import google_drive
        logger.info("Loaded Google Drive provider")
    except ImportError as e:
        logger.warning(f"Google Drive provider not available: {e}")
    
    try:
        from backend.providers import dropbox_provider
        logger.info("Loaded Dropbox provider")
    except ImportError as e:
        logger.warning(f"Dropbox provider not available: {e}")
    
    try:
        from backend.providers import webdav
        logger.info("Loaded WebDAV provider (OwnCloud/Nextcloud)")
    except ImportError as e:
        logger.warning(f"WebDAV provider not available: {e}")
    
    try:
        from backend.providers import onedrive
        logger.info("Loaded OneDrive provider")
    except ImportError as e:
        logger.warning(f"OneDrive provider not available: {e}")
    
    # Airbyte-backed providers (complex APIs)
    try:
        from backend.providers.airbyte import confluence
        logger.info("Loaded Confluence provider (via Airbyte)")
    except ImportError as e:
        logger.warning(f"Confluence provider not available: {e}")
    
    try:
        from backend.providers.airbyte import jira
        logger.info("Loaded Jira provider (via Airbyte)")
    except ImportError as e:
        logger.warning(f"Jira provider not available: {e}")


# Auto-load providers on import
_load_providers()
