"""Chat sessions router - Manage chat sessions, folders, and message history."""

import logging
import time
import uuid
import json
import asyncio
import traceback
from datetime import datetime
from typing import Optional, List, Dict, Any, AsyncGenerator
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.core.config import settings
from backend.routers.auth import get_current_user, UserResponse
from backend.agent.schemas import AgentMode, AgentModeConfig
from backend.agent.coordinator import FederatedAgent

logger = logging.getLogger(__name__)

router = APIRouter()


# ============== Model Pricing Data (per 1M tokens) ==============

MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # GPT-5 series
    "gpt-5.2": {"input": 5.00, "output": 15.00},
    "gpt-5.2-pro": {"input": 10.00, "output": 30.00},
    "gpt-5.1": {"input": 4.00, "output": 12.00},
    "gpt-5": {"input": 3.00, "output": 10.00},
    "gpt-5-mini": {"input": 0.50, "output": 1.50},
    "gpt-5-nano": {"input": 0.10, "output": 0.30},
    # GPT-4o series
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o-2024-11-20": {"input": 2.50, "output": 10.00},
    # GPT-4.1 series  
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    # GPT-4 series
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-4": {"input": 30.00, "output": 60.00},
    # GPT-3.5 series
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    # O1 series
    "o1-preview": {"input": 15.00, "output": 60.00},
    "o1-pro": {"input": 150.00, "output": 600.00},
    "o1-mini": {"input": 3.00, "output": 12.00},
    # Default fallback
    "default": {"input": 2.50, "output": 10.00},
}


def get_model_pricing(model_id: str) -> Dict[str, float]:
    """Get pricing for a model."""
    # Try exact match first
    if model_id in MODEL_PRICING:
        return MODEL_PRICING[model_id]
    # Try prefix match
    for key in MODEL_PRICING:
        if model_id.startswith(key):
            return MODEL_PRICING[key]
    return MODEL_PRICING["default"]


def calculate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for token usage."""
    pricing = get_model_pricing(model_id)
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost


def estimate_image_tokens(width: int = 1024, height: int = 1024, detail: str = "auto") -> int:
    """
    Estimate tokens for an image based on OpenAI's vision token calculation.
    
    OpenAI Vision token calculation:
    - Low detail: 85 tokens fixed
    - High detail: 85 base + 170 tokens per 512x512 tile
    
    For auto detail, we estimate based on image size.
    """
    if detail == "low":
        return 85
    
    # High detail calculation
    # Scale image to fit in 2048x2048 while maintaining aspect ratio
    max_size = 2048
    scale = min(max_size / max(width, height), 1.0)
    scaled_width = int(width * scale)
    scaled_height = int(height * scale)
    
    # Scale shortest side to 768px
    if min(scaled_width, scaled_height) > 768:
        scale_768 = 768 / min(scaled_width, scaled_height)
        scaled_width = int(scaled_width * scale_768)
        scaled_height = int(scaled_height * scale_768)
    
    # Calculate number of 512x512 tiles
    tiles_x = (scaled_width + 511) // 512
    tiles_y = (scaled_height + 511) // 512
    total_tiles = tiles_x * tiles_y
    
    # 85 base + 170 per tile
    return 85 + (170 * total_tiles)


def estimate_attachment_tokens(attachment: 'AttachmentInfo') -> int:
    """
    Estimate tokens for an attachment.
    
    For images: Uses OpenAI vision token calculation
    For documents: Estimates based on typical compression ratios
    For audio: Estimates based on duration
    """
    content_type = attachment.content_type
    size_bytes = attachment.size_bytes
    
    if content_type.startswith("image/"):
        # Default estimate for images without dimensions
        # Assume 1024x1024 for typical images
        return estimate_image_tokens(1024, 1024, "auto")
    
    elif content_type.startswith("audio/"):
        # Whisper: ~1 minute of audio = ~1500 tokens transcribed
        # Estimate duration from file size (rough estimate: 1MB = 1 minute for MP3)
        estimated_minutes = size_bytes / (1024 * 1024)
        return int(estimated_minutes * 1500)
    
    elif content_type in ["application/pdf", "application/msword", 
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]:
        # Estimate ~4 chars per token, documents are often text-heavy
        # Estimate text ratio from file size
        estimated_chars = size_bytes * 0.5  # Rough estimate of text content
        return int(estimated_chars / 4)
    
    elif content_type.startswith("text/"):
        # Text files: ~4 chars per token
        return size_bytes // 4
    
    else:
        # Unknown type - rough estimate
        return size_bytes // 100


# ============== Pydantic Models ==============

class MessageStats(BaseModel):
    """Statistics for a single message."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    tokens_per_second: float = 0.0
    latency_ms: float = 0.0


class SearchResultExcerpt(BaseModel):
    """Brief excerpt from a search result."""
    title: str
    excerpt: str
    score: Optional[float] = None


class SearchOperationResponse(BaseModel):
    """Details of a single search operation."""
    index_type: str  # "vector" or "text"
    index_name: str
    query: str
    results_count: int
    duration_ms: float
    top_score: Optional[float] = None
    top_results: Optional[List[SearchResultExcerpt]] = None


class SearchThinkingResponse(BaseModel):
    """Captures the agent's search thought process."""
    search_type: str  # "hybrid", "semantic", "text"
    query: str
    total_results: int
    operations: List[SearchOperationResponse] = []
    total_duration_ms: float = 0.0


class ToolOperationResponse(BaseModel):
    """Details of a tool operation (e.g., browser, code execution)."""
    tool_name: str  # "browse_web", "execute_code", etc.
    tool_input: Dict[str, Any] = {}  # Input parameters
    success: bool
    result_summary: str = ""  # Brief summary of result
    duration_ms: float = 0.0
    error: Optional[str] = None


class AgentThinkingResponse(BaseModel):
    """Complete thinking/reasoning response including search and tool usage."""
    search: Optional[SearchThinkingResponse] = None
    tool_calls: List[ToolOperationResponse] = []
    total_duration_ms: float = 0.0


class FederatedAgentTraceResponse(BaseModel):
    """Federated agent trace for transparency."""
    id: str
    mode: str
    models: Dict[str, str]  # {"orchestrator": "...", "worker": "..."}
    iterations: int
    orchestrator_steps: List[Dict[str, Any]] = []
    worker_steps: List[Dict[str, Any]] = []
    sources: Dict[str, Any] = {}  # {"documents": [...], "web_links": [...]}
    timing: Dict[str, float] = {}  # {"total_ms": ..., "orchestrator_ms": ..., "worker_ms": ...}
    tokens: Dict[str, int] = {}  # {"total": ..., "orchestrator": ..., "worker": ...}
    cost_usd: float = 0.0


class Message(BaseModel):
    """Chat message with stats."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    stats: Optional[MessageStats] = None
    model: Optional[str] = None
    sources: Optional[List[Dict[str, Any]]] = None
    attachments: Optional[List[Dict[str, Any]]] = None  # Attached files for multimodal
    thinking: Optional[AgentThinkingResponse] = None  # Search and tool operations performed
    agent_trace: Optional[Dict[str, Any]] = None  # Federated agent full trace


class SessionStats(BaseModel):
    """Aggregated statistics for a session."""
    total_messages: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    avg_tokens_per_second: float = 0.0
    avg_latency_ms: float = 0.0


class ChatSession(BaseModel):
    """Chat session with folder support."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = "New Chat"
    folder_id: Optional[str] = None
    user_id: Optional[str] = None  # Owner of the session
    model: str = Field(default_factory=lambda: settings.llm_model)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    messages: List[Message] = Field(default_factory=list)
    stats: SessionStats = Field(default_factory=SessionStats)
    is_pinned: bool = False
    is_archived: bool = False
    archived_at: Optional[datetime] = None
    profile: Optional[str] = None


class Folder(BaseModel):
    """Folder for organizing chat sessions."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    user_id: Optional[str] = None  # Owner of the folder
    color: str = "#6366f1"
    created_at: datetime = Field(default_factory=datetime.now)
    is_expanded: bool = True


# ============== Request/Response Models ==============

class CreateSessionRequest(BaseModel):
    title: Optional[str] = "New Chat"
    folder_id: Optional[str] = None
    model: Optional[str] = None


class UpdateSessionRequest(BaseModel):
    title: Optional[str] = None
    folder_id: Optional[str] = None
    model: Optional[str] = None
    is_pinned: Optional[bool] = None


class AttachmentInfo(BaseModel):
    """Information about an attached file."""
    filename: str
    content_type: str
    size_bytes: int
    data_url: Optional[str] = None  # Base64 data URL for images
    token_estimate: int = 0  # Estimated tokens for the attachment


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1)
    search_type: str = "hybrid"
    match_count: int = 10
    include_sources: bool = True
    attachments: Optional[List[AttachmentInfo]] = None  # File attachments for multimodal
    agent_mode: Optional[str] = None  # "auto", "thinking", "fast" - overrides session default


class CreateFolderRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    color: Optional[str] = "#6366f1"


class UpdateFolderRequest(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    is_expanded: Optional[bool] = None


class BulkSessionsRequest(BaseModel):
    """Request for bulk session operations."""
    session_ids: List[str] = Field(..., min_length=1)


class ModelInfo(BaseModel):
    id: str
    pricing: Dict[str, float]


class SessionListResponse(BaseModel):
    sessions: List[ChatSession]
    folders: List[Folder]


# ============== Database Helpers ==============

async def get_sessions_collection(request: Request):
    """Get chat sessions collection."""
    return request.app.state.db.db["chat_sessions"]


async def get_folders_collection(request: Request):
    """Get folders collection."""
    return request.app.state.db.db["chat_folders"]


# ============== Folder Endpoints ==============

@router.get("/folders")
async def list_folders(
    request: Request,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """List all folders for the current user."""
    collection = await get_folders_collection(request)
    
    # Filter by user if authenticated
    query = {}
    if user:
        query["user_id"] = user.id
    else:
        query["user_id"] = None  # Only show folders without owner if not logged in
    
    folders = []
    async for doc in collection.find(query).sort("created_at", 1):
        doc["id"] = str(doc["_id"])
        del doc["_id"]
        folders.append(Folder(**doc))
    return {"folders": folders}


@router.post("/folders")
async def create_folder(
    request: Request,
    folder_request: CreateFolderRequest,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Create a new folder."""
    collection = await get_folders_collection(request)
    
    folder = Folder(
        name=folder_request.name,
        color=folder_request.color or "#6366f1",
        user_id=user.id if user else None
    )
    doc = folder.model_dump()
    doc["_id"] = doc.pop("id")
    
    await collection.insert_one(doc)
    return folder


@router.put("/folders/{folder_id}")
async def update_folder(
    request: Request,
    folder_id: str,
    update: UpdateFolderRequest,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Update a folder."""
    collection = await get_folders_collection(request)
    
    # Check ownership
    query = {"_id": folder_id}
    if user:
        query["user_id"] = user.id
    
    update_dict = {k: v for k, v in update.model_dump().items() if v is not None}
    if not update_dict:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    result = await collection.update_one(query, {"$set": update_dict})
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Folder not found")
    
    return {"success": True}


@router.delete("/folders/{folder_id}")
async def delete_folder(
    request: Request,
    folder_id: str,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Delete a folder and move its sessions to unfiled."""
    folders_collection = await get_folders_collection(request)
    sessions_collection = await get_sessions_collection(request)
    
    # Check ownership
    query = {"_id": folder_id}
    if user:
        query["user_id"] = user.id
    
    # Move sessions to no folder
    session_query = {"folder_id": folder_id}
    if user:
        session_query["user_id"] = user.id
    
    await sessions_collection.update_many(
        session_query,
        {"$set": {"folder_id": None}}
    )
    
    result = await folders_collection.delete_one(query)
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Folder not found")
    
    return {"success": True}


# ============== Session Endpoints ==============

@router.get("")
@router.get("/")
async def list_sessions(
    request: Request,
    folder_id: Optional[str] = None,
    include_archived: bool = False,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """List all chat sessions for the current user, optionally filtered by folder.
    
    By default, archived sessions are excluded. Pass include_archived=true to include them.
    """
    sessions_collection = await get_sessions_collection(request)
    folders_collection = await get_folders_collection(request)
    
    # Get sessions - filter by user
    query = {}
    if user:
        query["user_id"] = user.id
    else:
        query["user_id"] = None  # Only show sessions without owner if not logged in
    
    # Exclude archived by default
    if not include_archived:
        query["is_archived"] = {"$ne": True}
    
    if folder_id is not None:
        query["folder_id"] = folder_id if folder_id != "none" else None
    
    sessions = []
    async for doc in sessions_collection.find(query).sort("updated_at", -1):
        doc["id"] = str(doc["_id"])
        del doc["_id"]
        # Don't return full messages in list view
        doc["messages"] = []
        sessions.append(ChatSession(**doc))
    
    # Get folders - filter by user
    folder_query = {}
    if user:
        folder_query["user_id"] = user.id
    else:
        folder_query["user_id"] = None
    
    folders = []
    async for doc in folders_collection.find(folder_query).sort("created_at", 1):
        doc["id"] = str(doc["_id"])
        del doc["_id"]
        folders.append(Folder(**doc))
    
    return SessionListResponse(sessions=sessions, folders=folders)


@router.post("")
@router.post("/")
async def create_session(
    request: Request,
    session_request: CreateSessionRequest,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Create a new chat session."""
    collection = await get_sessions_collection(request)
    
    # Get active profile
    try:
        from src.profile import get_profile_manager
        pm = get_profile_manager()
        profile = pm.active_profile_key
    except Exception:
        profile = "default"
    
    session = ChatSession(
        title=session_request.title or "New Chat",
        folder_id=session_request.folder_id,
        user_id=user.id if user else None,
        model=session_request.model or settings.llm_model,
        profile=profile
    )
    
    doc = session.model_dump()
    doc["_id"] = doc.pop("id")
    
    await collection.insert_one(doc)
    return session


@router.get("/{session_id}")
async def get_session(
    request: Request,
    session_id: str,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Get a chat session with full message history."""
    collection = await get_sessions_collection(request)
    
    # Check ownership
    query = {"_id": session_id}
    if user:
        query["user_id"] = user.id
    
    doc = await collection.find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")
    
    doc["id"] = str(doc["_id"])
    del doc["_id"]
    
    return ChatSession(**doc)


@router.put("/{session_id}")
async def update_session(
    request: Request,
    session_id: str,
    update: UpdateSessionRequest,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Update a chat session."""
    collection = await get_sessions_collection(request)
    
    # Check ownership
    query = {"_id": session_id}
    if user:
        query["user_id"] = user.id
    
    update_dict = {k: v for k, v in update.model_dump().items() if v is not None}
    update_dict["updated_at"] = datetime.now()
    
    if not update_dict:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    result = await collection.update_one(query, {"$set": update_dict})
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {"success": True}


@router.delete("/{session_id}")
async def delete_session(
    request: Request,
    session_id: str,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Delete a chat session."""
    collection = await get_sessions_collection(request)
    
    # Check ownership
    query = {"_id": session_id}
    if user:
        query["user_id"] = user.id
    
    result = await collection.delete_one(query)
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {"success": True}


# ============== Archive Endpoints ==============

@router.get("/archived/list")
async def list_archived_sessions(
    request: Request,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """List all archived chat sessions for the current user."""
    collection = await get_sessions_collection(request)
    
    query = {"is_archived": True}
    if user:
        query["user_id"] = user.id
    else:
        query["user_id"] = None
    
    sessions = []
    async for doc in collection.find(query).sort("archived_at", -1):
        doc["id"] = str(doc["_id"])
        del doc["_id"]
        # Don't return full messages in list view
        doc["messages"] = []
        sessions.append(ChatSession(**doc))
    
    return {"sessions": sessions}


@router.post("/archive")
async def archive_sessions(
    request: Request,
    bulk_request: BulkSessionsRequest,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Archive multiple chat sessions."""
    collection = await get_sessions_collection(request)
    
    query = {"_id": {"$in": bulk_request.session_ids}}
    if user:
        query["user_id"] = user.id
    
    result = await collection.update_many(
        query,
        {
            "$set": {
                "is_archived": True,
                "archived_at": datetime.now(),
                "is_pinned": False  # Unpin when archiving
            }
        }
    )
    
    return {
        "success": True,
        "archived_count": result.modified_count
    }


@router.post("/restore")
async def restore_sessions(
    request: Request,
    bulk_request: BulkSessionsRequest,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Restore multiple archived chat sessions."""
    collection = await get_sessions_collection(request)
    
    query = {
        "_id": {"$in": bulk_request.session_ids},
        "is_archived": True
    }
    if user:
        query["user_id"] = user.id
    
    result = await collection.update_many(
        query,
        {
            "$set": {
                "is_archived": False,
                "archived_at": None
            }
        }
    )
    
    return {
        "success": True,
        "restored_count": result.modified_count
    }


@router.post("/delete-permanent")
async def delete_sessions_permanently(
    request: Request,
    bulk_request: BulkSessionsRequest,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Permanently delete multiple chat sessions (cannot be undone)."""
    collection = await get_sessions_collection(request)
    
    query = {"_id": {"$in": bulk_request.session_ids}}
    if user:
        query["user_id"] = user.id
    
    result = await collection.delete_many(query)
    
    return {
        "success": True,
        "deleted_count": result.deleted_count
    }


@router.get("/{session_id}/export")
async def export_session(
    request: Request,
    session_id: str,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Export a chat session as JSON for download."""
    collection = await get_sessions_collection(request)
    
    query = {"_id": session_id}
    if user:
        query["user_id"] = user.id
    
    doc = await collection.find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")
    
    doc["id"] = str(doc["_id"])
    del doc["_id"]
    
    # Format for export
    session = ChatSession(**doc)
    export_data = {
        "exported_at": datetime.now().isoformat(),
        "session": {
            "id": session.id,
            "title": session.title,
            "model": session.model,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp.isoformat() if msg.timestamp else None
                }
                for msg in session.messages
            ],
            "stats": session.stats.model_dump() if session.stats else None
        }
    }
    
    return export_data


# ============== Message Endpoints ==============

@router.post("/{session_id}/messages")
async def send_message(
    request: Request,
    session_id: str,
    msg_request: SendMessageRequest,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Send a message in a chat session and get AI response.
    
    This endpoint uses the Federated Agent system:
    1. Orchestrator (thinking LLM) analyzes intent and plans
    2. Workers execute parallel searches across all accessible databases
    3. Results are evaluated and refined if needed
    4. Final response is synthesized with full source attribution
    """
    start_time = time.time()
    db = request.app.state.db
    collection = await get_sessions_collection(request)
    
    # Log the incoming request
    logger.info(
        f"Chat request: session={session_id}, user={user.id if user else 'anonymous'}, "
        f"content_length={len(msg_request.content)}"
    )
    
    # Get session - check ownership
    query = {"_id": session_id}
    if user:
        query["user_id"] = user.id
    
    try:
        doc = await collection.find_one(query)
    except Exception as e:
        logger.error(
            f"Database error finding session: session_id={session_id}, "
            f"error={type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve chat session. Please try again."
        )
    
    if not doc:
        logger.warning(
            f"Session not found: session_id={session_id}, user_id={user.id if user else 'anonymous'}"
        )
        raise HTTPException(status_code=404, detail="Session not found")
    
    session_model = doc.get("model", settings.llm_model)
    logger.debug(f"Using model: {session_model} for session {session_id}")
    
    # Create user message with attachments
    attachment_list = None
    if msg_request.attachments:
        attachment_list = [a.model_dump() for a in msg_request.attachments]
    
    user_message = Message(
        role="user",
        content=msg_request.content,
        attachments=attachment_list
    )
    
    # Build conversation history for the agent
    messages = doc.get("messages", [])
    conversation_history = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in messages[-20:]  # Last 20 messages
    ]
    
    # Get active profile information
    active_profile_key = None
    active_profile_database = None
    try:
        from src.profile import get_profile_manager
        pm = get_profile_manager(settings.profiles_path)
        active_profile_key = pm.active_profile_key
        if pm.active_profile:
            active_profile_database = pm.active_profile.database
    except Exception as e:
        logger.warning(f"Could not get active profile: {e}")
    
    # Determine agent mode
    agent_mode_str = msg_request.agent_mode or settings.agent_mode
    try:
        agent_mode = AgentMode(agent_mode_str)
    except ValueError:
        agent_mode = AgentMode.AUTO
    
    # Configure federated agent
    config = AgentModeConfig(
        mode=agent_mode,
        orchestrator_model=settings.orchestrator_model,
        worker_model=settings.worker_model,
        max_iterations=settings.agent_max_iterations,
        parallel_workers=settings.agent_parallel_workers
    )
    
    # Create and run federated agent
    agent = FederatedAgent(config=config)
    generation_start = time.time()
    
    try:
        response_text, trace = await agent.process(
            user_message=msg_request.content,
            user_id=user.id if user else "anonymous",
            user_email=user.email if user else "anonymous@local",
            session_id=session_id,
            conversation_history=conversation_history,
            active_profile_key=active_profile_key,
            active_profile_database=active_profile_database,
            accessible_profile_keys=[active_profile_key] if active_profile_key else None
        )
    except Exception as e:
        logger.error(
            f"Federated agent error: session={session_id}, "
            f"error_type={type(e).__name__}, error={str(e)}\n{traceback.format_exc()}"
        )
        raise HTTPException(
            status_code=500,
            detail="An error occurred while processing your request. Please try again."
        )
    finally:
        await agent.cleanup()
    
    generation_time = time.time() - generation_start
    total_time = time.time() - start_time
    
    # Get token counts from trace
    total_tokens = trace.total_tokens
    input_tokens = trace.orchestrator_tokens  # Rough approximation
    output_tokens = trace.worker_tokens
    
    logger.info(
        f"Federated agent response: session={session_id}, mode={agent_mode}, "
        f"iterations={trace.iterations}, tokens={total_tokens}, "
        f"generation_time={generation_time:.2f}s, total_time={total_time:.2f}s"
    )
    
    # Calculate stats
    tokens_per_second = output_tokens / generation_time if generation_time > 0 else 0
    cost = trace.estimated_cost_usd or calculate_cost(session_model, input_tokens, output_tokens)
    
    # Convert trace to response format
    trace_response = trace.to_response_dict()
    
    # Build sources from trace
    sources = []
    for doc_ref in trace.all_documents[:10]:  # Top 10 documents
        sources.append({
            "title": doc_ref.title,
            "source": doc_ref.source_type,
            "database": doc_ref.source_database,
            "relevance": doc_ref.similarity_score,
            "excerpt": doc_ref.excerpt[:300]
        })
    for web_ref in trace.all_web_links[:5]:  # Top 5 web links
        sources.append({
            "title": web_ref.title,
            "source": "web",
            "url": web_ref.url,
            "excerpt": web_ref.excerpt[:300]
        })
    
    assistant_message = Message(
        role="assistant",
        content=response_text,
        model=session_model,
        sources=sources if sources else None,
        agent_trace=trace_response,
        stats=MessageStats(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            tokens_per_second=round(tokens_per_second, 1),
            latency_ms=round(total_time * 1000, 0)
        )
    )
    
    # Update session stats
    current_stats = doc.get("stats", {})
    new_stats = {
        "total_messages": current_stats.get("total_messages", 0) + 2,
        "total_input_tokens": current_stats.get("total_input_tokens", 0) + input_tokens,
        "total_output_tokens": current_stats.get("total_output_tokens", 0) + output_tokens,
        "total_tokens": current_stats.get("total_tokens", 0) + total_tokens,
        "total_cost_usd": current_stats.get("total_cost_usd", 0) + cost,
    }
    
    # Calculate averages
    msg_count = new_stats["total_messages"] // 2  # Number of exchanges
    if msg_count > 0:
        new_stats["avg_tokens_per_second"] = round(
            (current_stats.get("avg_tokens_per_second", 0) * (msg_count - 1) + tokens_per_second) / msg_count, 1
        )
        new_stats["avg_latency_ms"] = round(
            (current_stats.get("avg_latency_ms", 0) * (msg_count - 1) + total_time * 1000) / msg_count, 0
        )
    
    # Auto-generate title using LLM after 3 exchanges (6 messages)
    title_update = {}
    message_count_after_this = len(messages) + 2  # +2 for user and assistant messages being added
    
    # Generate title on first message (simple) or after 3 exchanges (LLM-based)
    if len(messages) == 0:
        # First message - use first few words as placeholder
        first_words = msg_request.content.split()[:6]
        title = " ".join(first_words)
        if len(title) > 40:
            title = title[:40] + "..."
        title_update["title"] = title
    elif message_count_after_this == 6:  # After 3 exchanges (3 user + 3 assistant = 6)
        # Generate a better title using LLM
        try:
            import litellm
            
            # Build conversation summary for title generation
            conversation_summary = []
            for msg in messages[-6:]:  # Last 6 messages
                role = "User" if msg["role"] == "user" else "Assistant"
                content_preview = msg["content"][:200] if len(msg["content"]) > 200 else msg["content"]
                conversation_summary.append(f"{role}: {content_preview}")
            conversation_summary.append(f"User: {msg_request.content[:200]}")
            
            title_prompt = [
                {
                    "role": "system",
                    "content": "Generate a short, descriptive title (max 8 words) for this conversation. Return ONLY the title, nothing else."
                },
                {
                    "role": "user",
                    "content": "\n".join(conversation_summary)
                }
            ]
            
            title_response = await litellm.acompletion(
                model=session_model,
                messages=title_prompt,
                temperature=0.7,
                max_tokens=30,
                api_key=settings.llm_api_key,
                api_base=settings.llm_base_url if settings.llm_base_url else None,
            )
            
            generated_title = title_response.choices[0].message.content.strip()
            # Clean up the title - remove quotes if present, limit length
            generated_title = generated_title.strip('"\'')
            words = generated_title.split()[:8]  # Max 8 words
            generated_title = " ".join(words)
            if generated_title:
                title_update["title"] = generated_title
                logger.info(f"Generated title for session {session_id}: {generated_title}")
        except Exception as e:
            logger.warning(f"Failed to generate title for session {session_id}: {e}")
            # Keep existing title if generation fails
    
    # Update session in database
    try:
        await collection.update_one(
            {"_id": session_id},
            {
                "$push": {
                    "messages": {
                        "$each": [user_message.model_dump(), assistant_message.model_dump()]
                    }
                },
                "$set": {
                    "updated_at": datetime.now(),
                    "stats": new_stats,
                    **title_update
                }
            }
        )
        logger.debug(f"Session updated successfully: session_id={session_id}")
    except Exception as e:
        logger.error(
            f"Failed to update session in database: session_id={session_id}, "
            f"error={type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        )
        # The response was generated successfully, so we return it even if DB update fails
        logger.warning(f"Returning response despite DB update failure for session {session_id}")
    
    return {
        "user_message": user_message,
        "assistant_message": assistant_message,
        "session_stats": SessionStats(**new_stats),
        "title": title_update.get("title"),  # Return updated title if changed
        "agent_trace": trace_response  # Full trace for transparency
    }


# ============== Streaming Message Endpoint ==============

@router.post("/{session_id}/messages/stream")
async def send_message_stream(
    session_id: str,
    msg_request: SendMessageRequest,
    request: Request,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Send a message and stream back agent operations via SSE.
    
    This endpoint streams real-time updates as the agent processes:
    - Orchestrator steps (analyze, plan, evaluate, synthesize)
    - Worker executions with search results
    - Final response with full trace
    
    Event types:
    - orchestrator_step: An orchestrator phase completed
    - worker_step: A worker task completed
    - trace_update: Updated aggregate stats
    - response: Final response with full data
    - error: An error occurred
    """
    db = request.app.state.db
    collection = await get_sessions_collection(request)
    
    # Validate session
    query = {"_id": session_id}
    if user:
        query["user_id"] = user.id
    
    doc = await collection.find_one(query)
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")
    
    async def event_generator() -> AsyncGenerator[str, None]:
        """Generate SSE events as agent processes."""
        start_time = time.time()
        
        try:
            # Get session model and profile info
            session_model = doc.get("model", settings.llm_model)
            messages = doc.get("messages", [])
            conversation_history = [
                {"role": msg["role"], "content": msg["content"]}
                for msg in messages[-20:]
            ]
            
            # Get active profile
            active_profile_key = None
            active_profile_database = None
            try:
                from src.profile import get_profile_manager
                pm = get_profile_manager(settings.profiles_path)
                active_profile_key = pm.active_profile_key
                if pm.active_profile:
                    active_profile_database = pm.active_profile.database
            except Exception as e:
                logger.warning(f"Could not get active profile: {e}")
            
            # Determine agent mode
            agent_mode_str = msg_request.agent_mode or settings.agent_mode
            try:
                agent_mode = AgentMode(agent_mode_str)
            except ValueError:
                agent_mode = AgentMode.AUTO
            
            # Configure agent
            config = AgentModeConfig(
                mode=agent_mode,
                orchestrator_model=settings.orchestrator_model,
                worker_model=settings.worker_model,
                max_iterations=settings.agent_max_iterations,
                parallel_workers=settings.agent_parallel_workers
            )
            
            agent = FederatedAgent(config=config)
            
            # Send initial event
            yield f"data: {json.dumps({'type': 'start', 'mode': agent_mode_str, 'models': {'orchestrator': settings.orchestrator_model, 'worker': settings.worker_model}})}\n\n"
            
            # Process with polling for step updates
            response_text, trace = await agent.process(
                user_message=msg_request.content,
                user_id=user.id if user else "anonymous",
                user_email=user.email if user else "anonymous@local",
                session_id=session_id,
                conversation_history=conversation_history,
                active_profile_key=active_profile_key,
                active_profile_database=active_profile_database,
                accessible_profile_keys=[active_profile_key] if active_profile_key else None
            )
            
            # Stream orchestrator steps
            for step in trace.orchestrator_steps:
                event_data = {
                    'type': 'orchestrator_step',
                    'phase': step.phase,
                    'reasoning': step.reasoning[:300] if step.reasoning else '',
                    'output': step.output_summary[:200] if step.output_summary else '',
                    'duration_ms': step.duration_ms,
                    'tokens': step.tokens_used
                }
                yield f"data: {json.dumps(event_data)}\n\n"
            
            # Stream worker steps
            for step in trace.worker_steps:
                event_data = {
                    'type': 'worker_step',
                    'task_id': step.task_id,
                    'task_type': step.task_type,
                    'tool': step.tool_name,
                    'input': step.tool_input,
                    'documents_count': len(step.documents),
                    'links_count': len(step.web_links),
                    'duration_ms': step.duration_ms,
                    'success': step.success,
                    'documents': [
                        {'title': d.title, 'score': d.similarity_score, 'excerpt': d.excerpt[:150]}
                        for d in step.documents[:3]
                    ]
                }
                yield f"data: {json.dumps(event_data)}\n\n"
            
            await agent.cleanup()
            
            # Calculate final stats
            generation_time = time.time() - start_time
            total_tokens = trace.total_tokens
            tokens_per_second = total_tokens / generation_time if generation_time > 0 and total_tokens > 0 else 0
            
            # Send final response
            trace_response = trace.to_response_dict()
            
            # Build sources
            sources = []
            for doc_ref in trace.all_documents[:10]:
                sources.append({
                    "title": doc_ref.title,
                    "source": doc_ref.source_type,
                    "database": doc_ref.source_database,
                    "relevance": doc_ref.similarity_score,
                    "excerpt": doc_ref.excerpt[:300]
                })
            
            final_event = {
                'type': 'response',
                'content': response_text,
                'sources': sources,
                'stats': {
                    'total_tokens': total_tokens,
                    'orchestrator_tokens': trace.orchestrator_tokens,
                    'worker_tokens': trace.worker_tokens,
                    'cost_usd': trace.estimated_cost_usd,
                    'tokens_per_second': round(tokens_per_second, 1),
                    'latency_ms': round(generation_time * 1000, 0)
                },
                'trace': trace_response
            }
            yield f"data: {json.dumps(final_event)}\n\n"
            
            # Update session in database (same as non-streaming)
            user_message = Message(
                role="user",
                content=msg_request.content,
                attachments=[a.model_dump() for a in msg_request.attachments] if msg_request.attachments else None
            )
            
            assistant_message = Message(
                role="assistant",
                content=response_text,
                model=session_model,
                sources=sources if sources else None,
                agent_trace=trace_response,
                stats=MessageStats(
                    input_tokens=trace.orchestrator_tokens,
                    output_tokens=trace.worker_tokens,
                    total_tokens=total_tokens,
                    cost_usd=trace.estimated_cost_usd,
                    tokens_per_second=round(tokens_per_second, 1),
                    latency_ms=round(generation_time * 1000, 0)
                )
            )
            
            current_stats = doc.get("stats", {})
            new_stats = {
                "total_messages": current_stats.get("total_messages", 0) + 2,
                "total_tokens": current_stats.get("total_tokens", 0) + total_tokens,
                "total_cost_usd": current_stats.get("total_cost_usd", 0) + trace.estimated_cost_usd,
            }
            
            await collection.update_one(
                {"_id": session_id},
                {
                    "$push": {
                        "messages": {
                            "$each": [user_message.model_dump(), assistant_message.model_dump()]
                        }
                    },
                    "$set": {
                        "updated_at": datetime.now(),
                        "stats": new_stats
                    }
                }
            )
            
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            
        except Exception as e:
            logger.error(f"Streaming error: {e}\n{traceback.format_exc()}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@router.delete("/{session_id}/messages")
async def clear_messages(
    request: Request,
    session_id: str,
    user: Optional[UserResponse] = Depends(get_current_user)
):
    """Clear all messages in a session."""
    collection = await get_sessions_collection(request)
    
    # Check ownership
    query = {"_id": session_id}
    if user:
        query["user_id"] = user.id
    
    result = await collection.update_one(
        query,
        {
            "$set": {
                "messages": [],
                "stats": SessionStats().model_dump(),
                "updated_at": datetime.now()
            }
        }
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {"success": True}


# ============== Utility Endpoints ==============
# NOTE: These endpoints use "meta/" prefix to avoid conflict with /{session_id}

@router.get("/meta/models")
async def get_available_models():
    """Get list of available models with pricing."""
    return {
        "models": [
            {"id": model_id, "pricing": pricing}
            for model_id, pricing in MODEL_PRICING.items()
            if model_id != "default"
        ]
    }


@router.get("/meta/pricing")
async def get_model_pricing_info():
    """Get pricing information for all models."""
    return {
        "models": [
            ModelInfo(id=model_id, pricing=pricing)
            for model_id, pricing in MODEL_PRICING.items()
            if model_id != "default"
        ]
    }


@router.post("/meta/estimate-tokens")
async def estimate_tokens_endpoint(
    attachments: List[AttachmentInfo]
):
    """
    Estimate tokens for a list of attachments.
    
    Returns individual token estimates and total for price calculation.
    """
    estimates = []
    total = 0
    
    for attachment in attachments:
        tokens = estimate_attachment_tokens(attachment)
        estimates.append({
            "filename": attachment.filename,
            "content_type": attachment.content_type,
            "size_bytes": attachment.size_bytes,
            "token_estimate": tokens
        })
        total += tokens
    
    return {
        "attachments": estimates,
        "total_tokens": total,
        "cost_estimates": {
            model_id: calculate_cost(model_id, total, 0)
            for model_id in ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"]
        }
    }
