"""
Airbyte Integration Module

This package provides Airbyte-based providers for complex API integrations
like Confluence, Jira, and other services that benefit from Airbyte's
pre-built connectors.
"""

from backend.providers.airbyte.client import (
    AirbyteClient,
    AirbyteError,
    AirbyteConnectionError,
    AirbyteNotAvailableError,
    AirbyteAPIError,
    AirbyteRateLimitError,
    AirbyteResourceNotFoundError,
    AirbyteValidationError,
    AirbyteTimeoutError,
    AirbyteSyncStatus,
    AirbyteConnectionStatus,
    AirbyteSourceConfig,
    AirbyteDestinationConfig,
    AirbyteSource,
    AirbyteDestination,
    AirbyteConnection,
    AirbyteSyncJob,
)
from backend.providers.airbyte.base import AirbyteProvider, AirbyteProviderError
from backend.providers.airbyte.confluence import ConfluenceProvider
from backend.providers.airbyte.jira import JiraProvider
from backend.providers.airbyte.email_gmail import GmailProvider
from backend.providers.airbyte.email_outlook import OutlookProvider
from backend.providers.airbyte.email_imap import ImapProvider

__all__ = [
    # Client
    "AirbyteClient",
    
    # Exceptions
    "AirbyteError",
    "AirbyteConnectionError",
    "AirbyteNotAvailableError",
    "AirbyteAPIError",
    "AirbyteRateLimitError",
    "AirbyteResourceNotFoundError",
    "AirbyteValidationError",
    "AirbyteTimeoutError",
    "AirbyteProviderError",
    
    # Enums
    "AirbyteSyncStatus",
    "AirbyteConnectionStatus",
    
    # Data classes
    "AirbyteSourceConfig",
    "AirbyteDestinationConfig",
    "AirbyteSource",
    "AirbyteDestination",
    "AirbyteConnection",
    "AirbyteSyncJob",
    
    # Base Provider
    "AirbyteProvider",
    
    # Concrete Providers
    "ConfluenceProvider",
    "JiraProvider",
    "GmailProvider",
    "OutlookProvider",
    "ImapProvider",
]
