"""Pydantic models for API requests and responses."""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


# ============== Enums ==============

class SearchType(str, Enum):
    """Search type options."""
    SEMANTIC = "semantic"
    TEXT = "text"
    HYBRID = "hybrid"


class IngestionStatus(str, Enum):
    """Ingestion job status."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STOPPED = "stopped"


# ============== Chat Models ==============

class ChatMessage(BaseModel):
    """Single chat message."""
    role: str = Field(..., description="Message role: user or assistant")
    content: str = Field(..., description="Message content")
    timestamp: Optional[datetime] = Field(default_factory=datetime.now)


class ChatRequest(BaseModel):
    """Chat request model."""
    message: str = Field(..., description="User message", min_length=1, max_length=10000)
    conversation_id: Optional[str] = Field(None, description="Conversation ID for context")
    search_type: SearchType = Field(default=SearchType.HYBRID, description="Search type to use")
    match_count: int = Field(default=10, ge=1, le=50, description="Number of search results")
    include_sources: bool = Field(default=True, description="Include source documents")
    stream: bool = Field(default=False, description="Stream response")


class ChatResponse(BaseModel):
    """Chat response model."""
    message: str = Field(..., description="Assistant response")
    conversation_id: str = Field(..., description="Conversation ID")
    sources: Optional[List[Dict[str, Any]]] = Field(None, description="Source documents used")
    search_performed: bool = Field(default=False, description="Whether search was performed")
    model: str = Field(..., description="Model used for response")
    tokens_used: Optional[int] = Field(None, description="Tokens used")
    processing_time_ms: float = Field(..., description="Processing time in milliseconds")


# ============== Search Models ==============

class SearchRequest(BaseModel):
    """Search request model."""
    query: str = Field(..., description="Search query", min_length=1, max_length=5000)
    search_type: SearchType = Field(default=SearchType.HYBRID)
    match_count: int = Field(default=10, ge=1, le=50)
    text_weight: Optional[float] = Field(default=0.3, ge=0, le=1)


class SearchResultItem(BaseModel):
    """Single search result."""
    chunk_id: str = Field(..., description="Chunk ID")
    document_id: str = Field(..., description="Document ID")
    document_title: str = Field(..., description="Document title")
    document_source: str = Field(..., description="Document source path")
    content: str = Field(..., description="Chunk content")
    similarity: float = Field(..., description="Relevance score")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """Search response model."""
    query: str = Field(..., description="Original query")
    search_type: str = Field(..., description="Search type used")
    results: List[SearchResultItem] = Field(default_factory=list)
    total_results: int = Field(..., description="Number of results")
    processing_time_ms: float = Field(..., description="Processing time")


# ============== Cloud Source Enums ==============

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


class SyncStatus(str, Enum):
    """Cloud source sync status."""
    PENDING = "pending"
    SYNCING = "syncing"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


# ============== Profile Models ==============

class ProfileConfig(BaseModel):
    """Profile configuration."""
    name: str = Field(..., description="Profile display name")
    description: Optional[str] = Field(None, description="Profile description")
    owner_user_id: Optional[str] = Field(None, description="User ID of profile owner")
    documents_folders: List[str] = Field(default_factory=list)
    database: str = Field(default="rag_db")
    collection_documents: str = Field(default="documents")
    collection_chunks: str = Field(default="chunks")
    vector_index: str = Field(default="vector_index")
    text_index: str = Field(default="text_index")
    embedding_model: Optional[str] = None
    llm_model: Optional[str] = None
    
    # Model version configuration
    orchestrator_model: Optional[str] = None
    orchestrator_provider: Optional[str] = None
    worker_model: Optional[str] = None
    worker_provider: Optional[str] = None
    embedding_provider: Optional[str] = None
    
    airbyte: Optional["AirbyteConfig"] = None
    cloud_sources: List["CloudSourceAssociation"] = Field(default_factory=list)


class ProfileListResponse(BaseModel):
    """List of profiles response."""
    profiles: Dict[str, ProfileConfig] = Field(default_factory=dict)
    active_profile: str = Field(..., description="Currently active profile key")


class ProfileSwitchRequest(BaseModel):
    """Switch profile request."""
    profile_key: str = Field(..., description="Profile key to switch to")


class ProfileCreateRequest(BaseModel):
    """Create profile request."""
    key: str = Field(..., description="Unique profile key", pattern="^[a-z0-9-_]+$")
    name: str = Field(..., description="Display name")
    description: Optional[str] = None
    documents_folders: List[str] = Field(..., min_length=1)
    database: Optional[str] = None
    owner_user_id: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    """Update profile request."""
    name: Optional[str] = Field(None, description="Display name")
    description: Optional[str] = None
    documents_folders: Optional[List[str]] = Field(None, min_length=1)
    database: Optional[str] = None
    owner_user_id: Optional[str] = None


# ============== Cloud Source Models ==============

class AirbyteConfig(BaseModel):
    """Airbyte-specific configuration for a profile."""
    workspace_id: Optional[str] = Field(None, description="Airbyte workspace ID")
    workspace_name: Optional[str] = Field(None, description="Airbyte workspace name")
    destination_id: Optional[str] = Field(None, description="Airbyte MongoDB destination ID")
    default_sync_mode: str = Field(default="incremental", description="Default sync mode")
    default_schedule_type: str = Field(default="manual", description="Default schedule type")
    default_schedule_cron: Optional[str] = Field(None, description="Default cron expression")


class CloudSourceAssociation(BaseModel):
    """Association between a profile and a cloud source connection."""
    connection_id: str = Field(..., description="Unique connection ID")
    provider_type: CloudSourceType = Field(..., description="Type of cloud provider")
    display_name: str = Field(default="", description="User-friendly name")
    airbyte_source_id: Optional[str] = Field(None, description="Airbyte source ID")
    airbyte_connection_id: Optional[str] = Field(None, description="Airbyte sync connection ID")
    enabled: bool = Field(default=True, description="Whether sync is enabled")
    sync_schedule: Optional[str] = Field(None, description="Cron schedule for sync")
    last_sync_at: Optional[datetime] = Field(None, description="Last sync timestamp")
    last_sync_status: Optional[SyncStatus] = Field(None, description="Last sync status")
    include_paths: List[str] = Field(default_factory=list, description="Paths to include")
    exclude_paths: List[str] = Field(default_factory=list, description="Paths to exclude")
    collection_prefix: str = Field(default="", description="Prefix for MongoDB collections")


class CloudSourceCreateRequest(BaseModel):
    """Request to add a cloud source to a profile."""
    connection_id: str = Field(..., description="Unique connection ID")
    provider_type: CloudSourceType = Field(..., description="Type of cloud provider")
    display_name: Optional[str] = Field(None, description="User-friendly name")
    airbyte_source_id: Optional[str] = None
    airbyte_connection_id: Optional[str] = None
    collection_prefix: Optional[str] = None
    enabled: bool = True
    sync_schedule: Optional[str] = None
    include_paths: List[str] = Field(default_factory=list)
    exclude_paths: List[str] = Field(default_factory=list)


class CloudSourceUpdateRequest(BaseModel):
    """Request to update a cloud source."""
    display_name: Optional[str] = None
    enabled: Optional[bool] = None
    sync_schedule: Optional[str] = None
    airbyte_source_id: Optional[str] = None
    airbyte_connection_id: Optional[str] = None
    collection_prefix: Optional[str] = None
    include_paths: Optional[List[str]] = None
    exclude_paths: Optional[List[str]] = None


class CloudSourceListResponse(BaseModel):
    """List of cloud sources response."""
    cloud_sources: List[CloudSourceAssociation]
    profile_key: str
    total: int


class AirbyteConfigUpdateRequest(BaseModel):
    """Request to update Airbyte configuration for a profile."""
    workspace_id: Optional[str] = None
    workspace_name: Optional[str] = None
    destination_id: Optional[str] = None
    default_sync_mode: Optional[str] = None
    default_schedule_type: Optional[str] = None
    default_schedule_cron: Optional[str] = None


# ============== Ingestion Models ==============

class IngestionStartRequest(BaseModel):
    """Start ingestion request."""
    profile: Optional[str] = Field(None, description="Profile to use")
    documents_folder: Optional[str] = Field(None, description="Override documents folder")
    clean_before_ingest: bool = Field(default=False, description="Clean existing data")
    incremental: bool = Field(default=True, description="Skip already-ingested files")
    chunk_size: int = Field(default=1000, ge=100, le=5000)
    chunk_overlap: int = Field(default=200, ge=0, le=1000)
    max_tokens: int = Field(default=512, ge=128, le=2048)


class IngestionStatusResponse(BaseModel):
    """Ingestion status response."""
    status: IngestionStatus
    job_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_files: int = 0
    processed_files: int = 0
    failed_files: int = 0
    duplicates_skipped: int = 0
    excluded_files: int = 0
    document_count: int = 0
    image_count: int = 0
    audio_count: int = 0
    video_count: int = 0
    chunks_created: int = 0
    current_file: Optional[str] = None
    errors: List[str] = Field(default_factory=list)
    progress_percent: float = 0.0
    elapsed_seconds: float = 0.0
    estimated_remaining_seconds: Optional[float] = None
    is_paused: bool = False
    can_pause: bool = True
    can_stop: bool = True


class IngestionRunSummary(BaseModel):
    """Summary of an ingestion run for history display."""
    job_id: str
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_files: int = 0
    processed_files: int = 0
    failed_files: int = 0
    excluded_files: int = 0
    document_count: int = 0
    image_count: int = 0
    audio_count: int = 0
    video_count: int = 0
    chunks_created: int = 0
    elapsed_seconds: float = 0.0
    profile: Optional[str] = None


class IngestionRunsResponse(BaseModel):
    """Paginated list of ingestion runs."""
    runs: List[IngestionRunSummary]
    total: int
    page: int
    page_size: int
    total_pages: int


class DocumentInfo(BaseModel):
    """Document information."""
    id: str
    title: str
    source: str
    chunks_count: int
    created_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DocumentListResponse(BaseModel):
    """List of documents response."""
    documents: List[DocumentInfo]
    total: int
    page: int
    page_size: int
    total_pages: int


# ============== System Models ==============

class HealthResponse(BaseModel):
    """Health check response."""
    status: str = Field(..., description="Health status")
    database: str = Field(..., description="Database connection status")
    version: str = Field(..., description="API version")
    uptime_seconds: Optional[float] = None


class SystemStatsResponse(BaseModel):
    """System statistics response."""
    database: Dict[str, Any]
    indexes: Dict[str, Any]
    config: Dict[str, Any]


class ConfigResponse(BaseModel):
    """Configuration response (non-sensitive)."""
    llm_provider: str
    llm_model: str
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    default_match_count: int
    active_profile: str
    database: str


# ============== Prompt Template Models ==============

class ToolParameterSchema(BaseModel):
    """Schema for a tool parameter."""
    name: str = Field(..., description="Parameter name")
    type: str = Field(default="string", description="Parameter type")
    description: str = Field(default="", description="Parameter description")
    required: bool = Field(default=False, description="Whether parameter is required")


class ToolSchema(BaseModel):
    """Schema for an agent tool."""
    name: str = Field(..., description="Tool name")
    description: str = Field(..., description="Tool description for the LLM")
    parameters: List[ToolParameterSchema] = Field(default_factory=list)
    enabled: bool = Field(default=True, description="Whether tool is enabled")


class PromptVersion(BaseModel):
    """A version of a prompt template."""
    version: int = Field(..., description="Version number")
    system_prompt: str = Field(..., description="System prompt content")
    tools: List[ToolSchema] = Field(default_factory=list, description="Tool definitions")
    created_at: datetime = Field(default_factory=datetime.now)
    created_by: Optional[str] = Field(None, description="User who created this version")
    notes: str = Field(default="", description="Version notes/changelog")
    is_active: bool = Field(default=False, description="Whether this version is active")


class PromptTemplate(BaseModel):
    """Prompt template with version history."""
    id: Optional[str] = Field(None, description="Template ID")
    name: str = Field(..., description="Template name")
    description: str = Field(default="", description="Template description")
    category: str = Field(default="chat", description="Category: chat, search, etc.")
    versions: List[PromptVersion] = Field(default_factory=list)
    active_version: int = Field(default=1, description="Currently active version number")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    created_by: Optional[str] = Field(None, description="User who created the template")


class PromptTemplateCreate(BaseModel):
    """Request to create a new prompt template."""
    name: str = Field(..., description="Template name", min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    category: str = Field(default="chat")
    system_prompt: str = Field(..., description="Initial system prompt", min_length=1)
    tools: List[ToolSchema] = Field(default_factory=list)
    notes: str = Field(default="Initial version")


class PromptTemplateUpdate(BaseModel):
    """Request to update prompt template metadata."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    category: Optional[str] = None


class PromptVersionCreate(BaseModel):
    """Request to create a new version of a prompt template."""
    system_prompt: str = Field(..., description="System prompt content", min_length=1)
    tools: List[ToolSchema] = Field(default_factory=list)
    notes: str = Field(default="")


class PromptTestRequest(BaseModel):
    """Request to test a prompt template."""
    template_id: str = Field(..., description="Template ID to test")
    version: Optional[int] = Field(None, description="Version to test (default: active)")
    test_message: str = Field(..., description="Test message to send")
    mock_tool_responses: Dict[str, str] = Field(
        default_factory=dict, 
        description="Mock responses for tools (tool_name -> response)"
    )


class PromptTestResponse(BaseModel):
    """Response from testing a prompt."""
    success: bool
    response: str = Field(default="", description="LLM response")
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list, description="Tools called")
    tokens_used: int = Field(default=0)
    duration_ms: float = Field(default=0)
    error: Optional[str] = None


class PromptCompareRequest(BaseModel):
    """Request to compare two prompt versions."""
    template_id: str
    version_a: int
    version_b: int


class PromptCompareResponse(BaseModel):
    """Response comparing two prompt versions."""
    version_a: PromptVersion
    version_b: PromptVersion
    prompt_diff: str = Field(default="", description="Diff of system prompts")
    tools_added: List[str] = Field(default_factory=list)
    tools_removed: List[str] = Field(default_factory=list)
    tools_modified: List[str] = Field(default_factory=list)


class PromptTemplateListResponse(BaseModel):
    """List of prompt templates."""
    templates: List[PromptTemplate]
    total: int


# ============== Generic Response Models ==============

class SuccessResponse(BaseModel):
    """Generic success response."""
    success: bool = True
    message: str


class ErrorResponse(BaseModel):
    """Generic error response."""
    error: str
    message: str
    detail: Optional[str] = None


# Rebuild models with forward references
ProfileConfig.model_rebuild()
