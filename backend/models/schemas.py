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


# ============== Profile Models ==============

class ProfileConfig(BaseModel):
    """Profile configuration."""
    name: str = Field(..., description="Profile display name")
    description: Optional[str] = Field(None, description="Profile description")
    documents_folders: List[str] = Field(default_factory=list)
    database: str = Field(default="rag_db")
    collection_documents: str = Field(default="documents")
    collection_chunks: str = Field(default="chunks")
    vector_index: str = Field(default="vector_index")
    text_index: str = Field(default="text_index")
    embedding_model: Optional[str] = None
    llm_model: Optional[str] = None


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


class ProfileUpdateRequest(BaseModel):
    """Update profile request."""
    name: Optional[str] = Field(None, description="Display name")
    description: Optional[str] = None
    documents_folders: Optional[List[str]] = Field(None, min_length=1)
    database: Optional[str] = None


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
