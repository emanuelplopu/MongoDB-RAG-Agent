"""
Pydantic schemas for Cloud Sources API.

These schemas define the request/response models for cloud source
connection management, sync configuration, and job status.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Any
from datetime import datetime
from enum import Enum


# ==================== Enums ====================

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


class AuthType(str, Enum):
    """Authentication method types."""
    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    PASSWORD = "password"
    APP_TOKEN = "app_token"


class ConnectionStatus(str, Enum):
    """Connection health status."""
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"
    PENDING = "pending"


class SyncJobStatus(str, Enum):
    """Sync job execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class SyncJobType(str, Enum):
    """Sync job type."""
    FULL = "full"
    INCREMENTAL = "incremental"
    MANUAL = "manual"


class SyncFrequency(str, Enum):
    """Scheduled sync frequency."""
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


# ==================== Provider Schemas ====================

class ProviderCapabilitiesResponse(BaseModel):
    """Provider capabilities and metadata."""
    provider_type: ProviderType
    display_name: str
    description: str
    icon: str
    supported_auth_types: list[AuthType]
    supports_delta_sync: bool = False
    supports_webhooks: bool = False
    documentation_url: Optional[str] = None
    setup_instructions: Optional[str] = None


class ProvidersListResponse(BaseModel):
    """List of available providers."""
    providers: list[ProviderCapabilitiesResponse]


# ==================== Connection Schemas ====================

class ConnectionCreateRequest(BaseModel):
    """Request to create a new connection (non-OAuth)."""
    provider: ProviderType
    display_name: str = Field(..., min_length=1, max_length=100)
    
    # For password-based auth (OwnCloud, IMAP, etc.)
    server_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    
    # For API key auth
    api_key: Optional[str] = None
    
    # For app token auth
    app_token: Optional[str] = None
    
    # Cache configuration
    cache_size_mb: int = Field(default=1024, ge=100, le=100000, description="Local file cache size in MB")
    
    @field_validator('server_url')
    @classmethod
    def validate_server_url(cls, v: Optional[str]) -> Optional[str]:
        if v and not (v.startswith('http://') or v.startswith('https://')):
            raise ValueError('server_url must start with http:// or https://')
        return v


class ConnectionUpdateRequest(BaseModel):
    """Request to update a connection."""
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)
    
    # Credential updates
    password: Optional[str] = None
    api_key: Optional[str] = None
    app_token: Optional[str] = None
    
    # Cache configuration
    cache_size_mb: Optional[int] = Field(None, ge=100, le=100000, description="Local file cache size in MB")


class ConnectionResponse(BaseModel):
    """Connection details response."""
    id: str
    user_id: str
    provider: ProviderType
    display_name: str
    auth_type: AuthType
    status: ConnectionStatus
    
    # Server info (for self-hosted)
    server_url: Optional[str] = None
    
    # OAuth metadata (without sensitive tokens)
    oauth_email: Optional[str] = None
    oauth_expires_at: Optional[datetime] = None
    oauth_scopes: Optional[list[str]] = None
    
    # Cache configuration
    cache_size_mb: int = 1024
    
    # Status info
    last_validated_at: Optional[datetime] = None
    error_message: Optional[str] = None
    
    created_at: datetime
    updated_at: datetime


class ConnectionListResponse(BaseModel):
    """List of user's connections."""
    connections: list[ConnectionResponse]
    total: int


class ConnectionTestResponse(BaseModel):
    """Connection test result."""
    success: bool
    message: str
    user_info: Optional[dict[str, Any]] = None
    storage_quota: Optional[dict[str, Any]] = None


# ==================== OAuth Schemas ====================

class OAuthInitRequest(BaseModel):
    """Request to start OAuth flow."""
    provider: ProviderType
    display_name: str = Field(..., min_length=1, max_length=100)


class OAuthInitResponse(BaseModel):
    """OAuth initialization response."""
    authorization_url: str
    state: str  # For verification on callback


class OAuthCallbackRequest(BaseModel):
    """OAuth callback parameters."""
    code: str
    state: str


# ==================== Folder Browser Schemas ====================

class RemoteFolderResponse(BaseModel):
    """Remote folder information."""
    id: str
    name: str
    path: str
    parent_id: Optional[str] = None
    has_children: bool = True
    children_count: Optional[int] = None
    modified_at: Optional[datetime] = None


class RemoteFileResponse(BaseModel):
    """Remote file information."""
    id: str
    name: str
    path: str
    mime_type: str
    size_bytes: int
    modified_at: datetime
    web_view_url: Optional[str] = None


class FolderContentsResponse(BaseModel):
    """Folder contents for browser."""
    current_folder: RemoteFolderResponse
    folders: list[RemoteFolderResponse]
    files: list[RemoteFileResponse]
    has_more: bool = False
    next_cursor: Optional[str] = None


# ==================== Sync Configuration Schemas ====================

class SourcePath(BaseModel):
    """A selected source path for syncing."""
    path: str
    remote_id: str
    include_subfolders: bool = True
    display_name: Optional[str] = None


class SyncFilters(BaseModel):
    """Filters for sync configuration."""
    file_types: list[str] = Field(default_factory=lambda: ["pdf", "docx", "md", "txt"])
    exclude_patterns: list[str] = Field(default_factory=list)
    max_file_size_mb: int = Field(default=100, ge=1, le=1000)
    modified_after: Optional[datetime] = None


class SyncSchedule(BaseModel):
    """Sync schedule configuration."""
    enabled: bool = True
    frequency: SyncFrequency = SyncFrequency.DAILY
    hour: int = Field(default=0, ge=0, le=23)
    day_of_week: Optional[int] = Field(None, ge=0, le=6)  # 0=Monday
    day_of_month: Optional[int] = Field(None, ge=1, le=28)


class SyncConfigCreateRequest(BaseModel):
    """Request to create sync configuration."""
    connection_id: str
    profile_key: str  # RAG profile to index into
    name: str = Field(..., min_length=1, max_length=100)
    source_paths: list[SourcePath] = Field(..., min_length=1)
    filters: SyncFilters = Field(default_factory=SyncFilters)
    schedule: SyncSchedule = Field(default_factory=SyncSchedule)
    delete_removed: bool = True  # Remove docs when source files are deleted


class SyncConfigUpdateRequest(BaseModel):
    """Request to update sync configuration."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    source_paths: Optional[list[SourcePath]] = Field(None, min_length=1)
    filters: Optional[SyncFilters] = None
    schedule: Optional[SyncSchedule] = None
    delete_removed: Optional[bool] = None


class SyncConfigStats(BaseModel):
    """Statistics for a sync configuration."""
    total_files: int = 0
    total_size_bytes: int = 0
    last_sync_at: Optional[datetime] = None
    last_sync_files_processed: int = 0
    last_sync_duration_seconds: float = 0
    next_scheduled_run: Optional[datetime] = None


class SyncConfigResponse(BaseModel):
    """Sync configuration response."""
    id: str
    user_id: str
    connection_id: str
    connection_display_name: str
    provider: ProviderType
    profile_key: str
    name: str
    source_paths: list[SourcePath]
    filters: SyncFilters
    schedule: SyncSchedule
    delete_removed: bool
    status: str  # active, paused, error
    stats: SyncConfigStats
    created_at: datetime
    updated_at: datetime


class SyncConfigListResponse(BaseModel):
    """List of sync configurations."""
    configs: list[SyncConfigResponse]
    total: int


# ==================== Sync Job Schemas ====================

class SyncJobProgress(BaseModel):
    """Sync job progress information."""
    phase: str  # listing, downloading, processing, indexing
    current_file: Optional[str] = None
    files_discovered: int = 0
    files_processed: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    bytes_processed: int = 0


class SyncJobError(BaseModel):
    """Individual sync job error."""
    file_path: str
    error_type: str
    message: str
    timestamp: datetime


class SyncJobRunRequest(BaseModel):
    """Request to manually run a sync."""
    type: SyncJobType = SyncJobType.INCREMENTAL
    force_full: bool = False  # Ignore delta token


class SyncJobResponse(BaseModel):
    """Sync job status response."""
    id: str
    config_id: str
    config_name: str
    user_id: str
    type: SyncJobType
    status: SyncJobStatus
    progress: SyncJobProgress
    errors: list[SyncJobError] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None


class SyncJobListResponse(BaseModel):
    """List of sync jobs (history)."""
    jobs: list[SyncJobResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


# ==================== Dashboard Schemas ====================

class SourceSummary(BaseModel):
    """Summary of a connected source for dashboard."""
    connection_id: str
    provider: ProviderType
    display_name: str
    status: ConnectionStatus
    sync_configs_count: int
    total_files_indexed: int
    last_sync_at: Optional[datetime] = None
    next_sync_at: Optional[datetime] = None
    has_errors: bool = False


class DashboardResponse(BaseModel):
    """Cloud sources dashboard data."""
    total_connections: int
    active_connections: int
    total_sync_configs: int
    total_files_indexed: int
    total_size_bytes: int
    active_jobs: int
    sources: list[SourceSummary]
    recent_errors: list[SyncJobError] = Field(default_factory=list)
    next_scheduled_sync: Optional[datetime] = None


# ==================== Generic Responses ====================

class SuccessResponse(BaseModel):
    """Generic success response."""
    success: bool = True
    message: str


class ErrorResponse(BaseModel):
    """Generic error response."""
    error: str
    message: str
    detail: Optional[str] = None
