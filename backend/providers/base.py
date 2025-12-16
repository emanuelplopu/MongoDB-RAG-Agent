"""
Base Provider Interface and Data Classes

This module defines the abstract interface that all cloud source providers
must implement, along with common data structures.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import AsyncIterator, Optional, Any
import logging

logger = logging.getLogger(__name__)


class ProviderType(str, Enum):
    """Supported cloud source provider types."""
    GOOGLE_DRIVE = "google_drive"
    ONEDRIVE = "onedrive"
    SHAREPOINT = "sharepoint"
    DROPBOX = "dropbox"
    OWNCLOUD = "owncloud"
    NEXTCLOUD = "nextcloud"
    CONFLUENCE = "confluence"
    JIRA = "jira"
    EMAIL_IMAP = "email_imap"
    EMAIL_GMAIL = "email_gmail"
    EMAIL_OUTLOOK = "email_outlook"
    NOTION = "notion"
    SLACK = "slack"


class AuthType(str, Enum):
    """Authentication method types."""
    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    PASSWORD = "password"
    APP_TOKEN = "app_token"
    CERTIFICATE = "certificate"


@dataclass
class ProviderCapabilities:
    """Describes the capabilities of a provider."""
    provider_type: ProviderType
    display_name: str
    description: str
    icon: str  # Icon name or URL
    
    # Authentication
    supported_auth_types: list[AuthType] = field(default_factory=list)
    oauth_scopes: list[str] = field(default_factory=list)
    
    # Sync capabilities
    supports_delta_sync: bool = False
    supports_webhooks: bool = False
    supports_file_streaming: bool = True
    
    # File types
    supports_folders: bool = True
    supports_files: bool = True
    supports_attachments: bool = False  # For Confluence/Jira/Email
    
    # Rate limits
    rate_limit_requests_per_minute: int = 100
    rate_limit_bytes_per_day: Optional[int] = None
    
    # Additional metadata
    documentation_url: Optional[str] = None
    setup_instructions: Optional[str] = None


@dataclass
class RemoteFile:
    """Represents a file in a cloud source."""
    id: str
    name: str
    path: str
    mime_type: str
    size_bytes: int
    modified_at: datetime
    created_at: Optional[datetime] = None
    checksum: Optional[str] = None
    download_url: Optional[str] = None
    parent_id: Optional[str] = None
    web_view_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    
    # For deduplication and change detection
    version_id: Optional[str] = None
    etag: Optional[str] = None
    
    # Provider-specific metadata
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        # Normalize path separators
        if self.path:
            self.path = self.path.replace("\\", "/")


@dataclass
class RemoteFolder:
    """Represents a folder in a cloud source."""
    id: str
    name: str
    path: str
    parent_id: Optional[str] = None
    children_count: Optional[int] = None
    modified_at: Optional[datetime] = None
    
    # For folder picker UI
    has_children: bool = True
    is_root: bool = False
    
    # Provider-specific metadata
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SyncDelta:
    """Represents changes since last sync."""
    added: list[RemoteFile] = field(default_factory=list)
    modified: list[RemoteFile] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)  # file IDs
    
    # For pagination/continuation
    next_delta_token: Optional[str] = None
    has_more: bool = False
    
    # Stats
    total_changes: int = 0
    
    def __post_init__(self):
        self.total_changes = len(self.added) + len(self.modified) + len(self.deleted)


@dataclass
class OAuthTokens:
    """OAuth 2.0 token set."""
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    token_type: str = "Bearer"
    scope: Optional[str] = None


@dataclass
class ConnectionCredentials:
    """Unified credentials container for all auth types."""
    auth_type: AuthType
    
    # OAuth2
    oauth_tokens: Optional[OAuthTokens] = None
    
    # API Key
    api_key: Optional[str] = None
    
    # Password
    username: Optional[str] = None
    password: Optional[str] = None
    
    # App Token (OwnCloud, etc.)
    app_token: Optional[str] = None
    
    # Certificate
    certificate_path: Optional[str] = None
    certificate_password: Optional[str] = None
    
    # Server URL (for self-hosted services)
    server_url: Optional[str] = None
    
    # Additional provider-specific fields
    extra: dict[str, Any] = field(default_factory=dict)


class CloudSourceProvider(ABC):
    """
    Abstract base class for cloud source providers.
    
    All cloud storage and collaboration platform integrations must implement
    this interface to work with the unified sync engine.
    """
    
    def __init__(self, credentials: Optional[ConnectionCredentials] = None):
        self.credentials = credentials
        self._authenticated = False
    
    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Return the provider type identifier."""
        pass
    
    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        """Return the provider's capabilities."""
        pass
    
    # ==================== Authentication ====================
    
    @abstractmethod
    async def authenticate(self, credentials: ConnectionCredentials) -> bool:
        """
        Authenticate with the provider using the given credentials.
        
        Args:
            credentials: Connection credentials
            
        Returns:
            True if authentication successful
            
        Raises:
            AuthenticationError: If authentication fails
        """
        pass
    
    @abstractmethod
    async def validate_credentials(self) -> bool:
        """
        Validate that current credentials are still valid.
        
        Returns:
            True if credentials are valid
        """
        pass
    
    @abstractmethod
    async def refresh_credentials(self) -> ConnectionCredentials:
        """
        Refresh credentials (typically for OAuth token refresh).
        
        Returns:
            New/refreshed credentials
            
        Raises:
            AuthenticationError: If refresh fails
        """
        pass
    
    # ==================== OAuth Flow ====================
    
    async def get_oauth_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        scopes: Optional[list[str]] = None
    ) -> str:
        """
        Generate OAuth authorization URL for user consent.
        
        Args:
            redirect_uri: Callback URL after authorization
            state: CSRF protection state token
            scopes: Optional override for requested scopes
            
        Returns:
            Authorization URL to redirect user to
        """
        raise NotImplementedError("OAuth not supported for this provider")
    
    async def exchange_oauth_code(
        self,
        code: str,
        redirect_uri: str
    ) -> ConnectionCredentials:
        """
        Exchange OAuth authorization code for tokens.
        
        Args:
            code: Authorization code from callback
            redirect_uri: Same redirect URI used for authorization
            
        Returns:
            Connection credentials with tokens
        """
        raise NotImplementedError("OAuth not supported for this provider")
    
    async def revoke_oauth_tokens(self) -> bool:
        """
        Revoke OAuth tokens at the provider.
        
        Returns:
            True if revocation successful
        """
        raise NotImplementedError("OAuth not supported for this provider")
    
    # ==================== File/Folder Browsing ====================
    
    @abstractmethod
    async def list_root_folders(self) -> list[RemoteFolder]:
        """
        List root-level folders/drives.
        
        For Google Drive, this includes shared drives.
        For OneDrive, this includes the root and shared folders.
        
        Returns:
            List of root folders
        """
        pass
    
    @abstractmethod
    async def list_folder_contents(
        self,
        folder_id: str,
        include_files: bool = True,
        include_folders: bool = True
    ) -> tuple[list[RemoteFolder], list[RemoteFile]]:
        """
        List contents of a folder.
        
        Args:
            folder_id: Folder identifier
            include_files: Include files in response
            include_folders: Include subfolders in response
            
        Returns:
            Tuple of (folders, files)
        """
        pass
    
    async def get_folder_tree(
        self,
        folder_id: str,
        max_depth: int = 2
    ) -> RemoteFolder:
        """
        Get folder tree structure for UI display.
        
        Args:
            folder_id: Root folder ID
            max_depth: Maximum depth to traverse
            
        Returns:
            Folder with nested children
        """
        # Default implementation - providers can override for efficiency
        folders, _ = await self.list_folder_contents(
            folder_id, 
            include_files=False, 
            include_folders=True
        )
        return folders
    
    # ==================== File Operations ====================
    
    @abstractmethod
    async def get_file_metadata(self, file_id: str) -> RemoteFile:
        """
        Get metadata for a single file.
        
        Args:
            file_id: File identifier
            
        Returns:
            File metadata
        """
        pass
    
    @abstractmethod
    async def download_file(self, file_id: str) -> AsyncIterator[bytes]:
        """
        Download file content as a stream.
        
        Args:
            file_id: File identifier
            
        Yields:
            File content chunks
        """
        pass
    
    async def download_file_to_bytes(self, file_id: str) -> bytes:
        """
        Download file content as bytes (convenience method).
        
        Args:
            file_id: File identifier
            
        Returns:
            Complete file content
        """
        chunks = []
        async for chunk in self.download_file(file_id):
            chunks.append(chunk)
        return b"".join(chunks)
    
    # ==================== Sync Operations ====================
    
    @abstractmethod
    async def list_all_files(
        self,
        folder_id: str,
        recursive: bool = True,
        file_types: Optional[list[str]] = None
    ) -> AsyncIterator[RemoteFile]:
        """
        List all files in a folder (optionally recursive).
        
        Args:
            folder_id: Root folder ID
            recursive: Include files in subfolders
            file_types: Filter by MIME types or extensions
            
        Yields:
            Remote files
        """
        pass
    
    async def get_changes(
        self,
        delta_token: Optional[str] = None,
        folder_id: Optional[str] = None
    ) -> SyncDelta:
        """
        Get changes since last sync (for incremental sync).
        
        Args:
            delta_token: Token from previous sync
            folder_id: Optional folder to scope changes to
            
        Returns:
            Changes since last sync
        """
        raise NotImplementedError("Delta sync not supported for this provider")
    
    async def subscribe_to_changes(
        self,
        folder_id: str,
        webhook_url: str
    ) -> str:
        """
        Subscribe to real-time change notifications.
        
        Args:
            folder_id: Folder to watch
            webhook_url: URL to receive notifications
            
        Returns:
            Subscription ID
        """
        raise NotImplementedError("Webhooks not supported for this provider")
    
    async def unsubscribe_from_changes(self, subscription_id: str) -> bool:
        """
        Unsubscribe from change notifications.
        
        Args:
            subscription_id: Subscription to cancel
            
        Returns:
            True if successful
        """
        raise NotImplementedError("Webhooks not supported for this provider")
    
    # ==================== Utility Methods ====================
    
    async def get_storage_quota(self) -> dict[str, Any]:
        """
        Get storage quota information.
        
        Returns:
            Dict with 'used', 'total', 'remaining' in bytes
        """
        return {"used": 0, "total": 0, "remaining": 0}
    
    async def get_user_info(self) -> dict[str, Any]:
        """
        Get information about the authenticated user.
        
        Returns:
            User info dict with 'email', 'name', etc.
        """
        return {}
    
    async def close(self) -> None:
        """Clean up any resources (HTTP clients, etc.)."""
        pass
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()


# ==================== Custom Exceptions ====================

class CloudSourceError(Exception):
    """Base exception for cloud source operations."""
    pass


class AuthenticationError(CloudSourceError):
    """Authentication failed or credentials expired."""
    pass


class RateLimitError(CloudSourceError):
    """Rate limit exceeded."""
    def __init__(self, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after


class FileNotFoundError(CloudSourceError):
    """Requested file not found."""
    pass


class PermissionDeniedError(CloudSourceError):
    """Access denied to resource."""
    pass


class QuotaExceededError(CloudSourceError):
    """Storage quota exceeded."""
    pass
