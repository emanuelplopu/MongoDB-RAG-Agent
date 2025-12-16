"""
Cloud Source Providers Package

This module provides unified interfaces for accessing various cloud storage
and collaboration platforms for document ingestion.

Supported Providers:
- Google Drive (OAuth 2.0)
- OneDrive/SharePoint (OAuth 2.0 via Microsoft Graph)
- Dropbox (OAuth 2.0)
- OwnCloud/NextCloud (WebDAV with password/app token)
- Confluence (OAuth 2.0 / API Token)
- Jira (OAuth 2.0 / API Token)
- Email (IMAP with password/OAuth)
"""

from backend.providers.base import (
    CloudSourceProvider,
    RemoteFile,
    RemoteFolder,
    SyncDelta,
    ProviderCapabilities,
    AuthType,
    ProviderType,
)

__all__ = [
    "CloudSourceProvider",
    "RemoteFile",
    "RemoteFolder", 
    "SyncDelta",
    "ProviderCapabilities",
    "AuthType",
    "ProviderType",
]
