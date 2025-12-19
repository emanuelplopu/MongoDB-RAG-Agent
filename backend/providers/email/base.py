"""
Base types and classes for email synchronization.

Provides common data structures and abstract base class for all email sync providers.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any, AsyncIterator, Callable
import json

logger = logging.getLogger(__name__)


class SyncStatus(str, Enum):
    """Status of a sync operation."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class EmailFolder:
    """Represents an email folder/mailbox."""
    id: str  # Unique folder identifier
    name: str  # Display name
    path: str  # Full path (e.g., "INBOX/Work/Projects")
    parent_id: Optional[str] = None
    depth: int = 0  # Nesting level (0 = root)
    message_count: int = 0
    unread_count: int = 0
    flags: List[str] = field(default_factory=list)
    children: List["EmailFolder"] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "parent_id": self.parent_id,
            "depth": self.depth,
            "message_count": self.message_count,
            "unread_count": self.unread_count,
            "flags": self.flags,
            "children": [c.to_dict() for c in self.children],
            "metadata": self.metadata,
        }


@dataclass 
class EmailAttachment:
    """Represents an email attachment."""
    id: str
    filename: str
    content_type: str
    size_bytes: int
    content_id: Optional[str] = None  # For inline attachments
    content: Optional[bytes] = None  # Raw content if fetched


@dataclass
class EmailMessage:
    """Represents an email message."""
    id: str  # Unique message ID (UID or message-id)
    folder_id: str
    folder_path: str
    subject: str
    from_address: str
    from_name: str = ""
    to_addresses: List[str] = field(default_factory=list)
    cc_addresses: List[str] = field(default_factory=list)
    bcc_addresses: List[str] = field(default_factory=list)
    date: Optional[datetime] = None
    received_date: Optional[datetime] = None
    body_plain: str = ""
    body_html: str = ""
    snippet: str = ""  # First 200 chars for preview
    flags: List[str] = field(default_factory=list)  # \Seen, \Answered, etc.
    is_read: bool = False
    is_starred: bool = False
    has_attachments: bool = False
    attachments: List[EmailAttachment] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    thread_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: List[str] = field(default_factory=list)
    size_bytes: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "folder_id": self.folder_id,
            "folder_path": self.folder_path,
            "subject": self.subject,
            "from_address": self.from_address,
            "from_name": self.from_name,
            "to_addresses": self.to_addresses,
            "cc_addresses": self.cc_addresses,
            "date": self.date.isoformat() if self.date else None,
            "body_plain": self.body_plain,
            "body_html": self.body_html,
            "snippet": self.snippet,
            "is_read": self.is_read,
            "is_starred": self.is_starred,
            "has_attachments": self.has_attachments,
            "attachment_count": len(self.attachments),
            "size_bytes": self.size_bytes,
            "thread_id": self.thread_id,
            "metadata": self.metadata,
        }
    
    def to_content(self) -> str:
        """Convert to searchable content string."""
        parts = [
            f"# {self.subject}",
            f"\n**From:** {self.from_name} <{self.from_address}>",
            f"**To:** {', '.join(self.to_addresses)}",
        ]
        if self.cc_addresses:
            parts.append(f"**CC:** {', '.join(self.cc_addresses)}")
        if self.date:
            parts.append(f"**Date:** {self.date.isoformat()}")
        parts.append(f"**Folder:** {self.folder_path}")
        parts.append(f"\n---\n\n{self.body_plain or self.body_html}")
        return "\n".join(parts)


@dataclass
class FolderSyncState:
    """Tracks sync state for a single folder."""
    folder_id: str
    folder_path: str
    last_uid: int = 0  # Last synced UID
    uid_validity: int = 0  # UIDVALIDITY for cache invalidation
    last_sync: Optional[datetime] = None
    message_count: int = 0
    synced_count: int = 0
    status: SyncStatus = SyncStatus.PENDING
    error: Optional[str] = None


@dataclass
class EmailSyncState:
    """
    Complete sync state for resumable operations.
    
    Supports:
    - Folder-level checkpoints
    - Message-level progress within folders
    - Resume from any point after interruption
    """
    connection_id: str
    user_id: str
    provider_type: str  # "imap", "gmail", "outlook"
    
    # Overall state
    status: SyncStatus = SyncStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_checkpoint: Optional[datetime] = None
    
    # Folder enumeration
    folders_discovered: int = 0
    folders_completed: int = 0
    folder_states: Dict[str, FolderSyncState] = field(default_factory=dict)
    
    # Message counts
    total_messages_discovered: int = 0
    total_messages_synced: int = 0
    total_messages_failed: int = 0
    
    # Current position for resume
    current_folder_id: Optional[str] = None
    current_folder_offset: int = 0
    
    # Error tracking
    errors: List[Dict[str, Any]] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 3
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "connection_id": self.connection_id,
            "user_id": self.user_id,
            "provider_type": self.provider_type,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "last_checkpoint": self.last_checkpoint.isoformat() if self.last_checkpoint else None,
            "folders_discovered": self.folders_discovered,
            "folders_completed": self.folders_completed,
            "folder_states": {k: v.__dict__ for k, v in self.folder_states.items()},
            "total_messages_discovered": self.total_messages_discovered,
            "total_messages_synced": self.total_messages_synced,
            "total_messages_failed": self.total_messages_failed,
            "current_folder_id": self.current_folder_id,
            "current_folder_offset": self.current_folder_offset,
            "errors": self.errors[-100:],  # Keep last 100 errors
            "retry_count": self.retry_count,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmailSyncState":
        """Reconstruct state from dictionary."""
        state = cls(
            connection_id=data["connection_id"],
            user_id=data["user_id"],
            provider_type=data["provider_type"],
            status=SyncStatus(data.get("status", "pending")),
            folders_discovered=data.get("folders_discovered", 0),
            folders_completed=data.get("folders_completed", 0),
            total_messages_discovered=data.get("total_messages_discovered", 0),
            total_messages_synced=data.get("total_messages_synced", 0),
            total_messages_failed=data.get("total_messages_failed", 0),
            current_folder_id=data.get("current_folder_id"),
            current_folder_offset=data.get("current_folder_offset", 0),
            errors=data.get("errors", []),
            retry_count=data.get("retry_count", 0),
        )
        
        if data.get("started_at"):
            state.started_at = datetime.fromisoformat(data["started_at"])
        if data.get("completed_at"):
            state.completed_at = datetime.fromisoformat(data["completed_at"])
        if data.get("last_checkpoint"):
            state.last_checkpoint = datetime.fromisoformat(data["last_checkpoint"])
        
        # Reconstruct folder states
        for folder_id, fs_data in data.get("folder_states", {}).items():
            state.folder_states[folder_id] = FolderSyncState(**fs_data)
        
        return state
    
    def save_checkpoint(self):
        """Mark current point as checkpoint."""
        self.last_checkpoint = datetime.now()
    
    def get_resume_point(self) -> tuple[Optional[str], int]:
        """Get folder and offset to resume from."""
        return self.current_folder_id, self.current_folder_offset


@dataclass
class EmailSyncConfig:
    """Configuration for email sync."""
    # Connection
    host: str = ""
    port: int = 993
    username: str = ""
    password: str = ""
    use_ssl: bool = True
    use_tls: bool = False
    
    # OAuth (for Gmail/Outlook)
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    
    # Sync behavior
    batch_size: int = 100  # Messages per batch
    max_messages_per_folder: int = 0  # 0 = unlimited
    include_attachments: bool = True
    max_attachment_size_mb: int = 25
    folders_to_sync: List[str] = field(default_factory=list)  # Empty = all
    folders_to_exclude: List[str] = field(default_factory=list)
    sync_since_date: Optional[datetime] = None  # Only sync after this date
    
    # Read-only (non-destructive) settings
    read_only: bool = True
    peek_mode: bool = True  # Use BODY.PEEK
    mark_as_read: bool = False
    delete_after_sync: bool = False
    
    # Performance
    concurrent_folders: int = 3
    checkpoint_interval: int = 500  # Save state every N messages
    timeout_seconds: int = 60
    retry_attempts: int = 3
    retry_delay_seconds: int = 5
    
    # Storage
    target_database: str = ""
    target_collection: str = "email_messages"


@dataclass
class EmailSyncProgress:
    """Real-time sync progress for UI updates."""
    status: SyncStatus
    current_folder: str = ""
    current_folder_progress: float = 0.0  # 0-100%
    overall_progress: float = 0.0  # 0-100%
    folders_total: int = 0
    folders_completed: int = 0
    messages_total: int = 0
    messages_synced: int = 0
    messages_failed: int = 0
    current_rate: float = 0.0  # Messages per second
    estimated_remaining_seconds: int = 0
    errors: List[str] = field(default_factory=list)


class BaseEmailSync(ABC):
    """
    Abstract base class for email synchronization.
    
    Provides:
    - Deep folder enumeration
    - Batched message fetching
    - Checkpoint/resume system
    - Progress callbacks
    """
    
    def __init__(self, config: EmailSyncConfig):
        self.config = config
        self.state: Optional[EmailSyncState] = None
        self._progress_callback: Optional[Callable[[EmailSyncProgress], None]] = None
        self._cancel_requested: bool = False
    
    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to email server."""
        pass
    
    @abstractmethod
    async def disconnect(self):
        """Close connection."""
        pass
    
    @abstractmethod
    async def enumerate_folders(
        self, 
        max_depth: int = 20
    ) -> List[EmailFolder]:
        """
        Enumerate all folders recursively.
        
        Args:
            max_depth: Maximum folder nesting depth (default 20)
        
        Returns:
            Flat list of all folders with hierarchy info
        """
        pass
    
    @abstractmethod
    async def get_folder_message_count(self, folder: EmailFolder) -> int:
        """Get total message count in a folder."""
        pass
    
    @abstractmethod
    async def fetch_messages(
        self,
        folder: EmailFolder,
        offset: int = 0,
        batch_size: int = 100,
        since_uid: int = 0
    ) -> AsyncIterator[EmailMessage]:
        """
        Fetch messages from a folder with pagination.
        
        Args:
            folder: Folder to fetch from
            offset: Starting offset for pagination
            batch_size: Number of messages per batch
            since_uid: Only fetch messages with UID > this value
        
        Yields:
            EmailMessage objects
        """
        pass
    
    def set_progress_callback(self, callback: Callable[[EmailSyncProgress], None]):
        """Set callback for progress updates."""
        self._progress_callback = callback
    
    def request_cancel(self):
        """Request cancellation of current sync."""
        self._cancel_requested = True
    
    def _report_progress(self, progress: EmailSyncProgress):
        """Report progress to callback if set."""
        if self._progress_callback:
            try:
                self._progress_callback(progress)
            except Exception as e:
                logger.warning(f"Progress callback error: {e}")
    
    async def full_sync(
        self,
        state: Optional[EmailSyncState] = None,
        message_handler: Optional[Callable[[EmailMessage], None]] = None
    ) -> EmailSyncState:
        """
        Perform full synchronization with resume support.
        
        Args:
            state: Previous state to resume from (optional)
            message_handler: Callback for each synced message
        
        Returns:
            Final sync state
        """
        # Initialize or resume state
        if state and state.status in [SyncStatus.PAUSED, SyncStatus.FAILED]:
            self.state = state
            self.state.status = SyncStatus.IN_PROGRESS
            self.state.retry_count += 1
            logger.info(f"Resuming sync from checkpoint: {self.state.current_folder_id}")
        else:
            self.state = EmailSyncState(
                connection_id=f"sync_{datetime.now().timestamp()}",
                user_id=self.config.username,
                provider_type=self._get_provider_type(),
                status=SyncStatus.IN_PROGRESS,
                started_at=datetime.now()
            )
        
        self._cancel_requested = False
        
        try:
            # Connect
            if not await self.connect():
                self.state.status = SyncStatus.FAILED
                self.state.errors.append({"error": "Connection failed", "time": datetime.now().isoformat()})
                return self.state
            
            # Phase 1: Enumerate folders (if not already done)
            if self.state.folders_discovered == 0:
                logger.info("Phase 1: Enumerating folders...")
                folders = await self.enumerate_folders(max_depth=20)
                self.state.folders_discovered = len(folders)
                
                # Initialize folder states
                for folder in folders:
                    if self._should_sync_folder(folder):
                        msg_count = await self.get_folder_message_count(folder)
                        self.state.folder_states[folder.id] = FolderSyncState(
                            folder_id=folder.id,
                            folder_path=folder.path,
                            message_count=msg_count,
                            status=SyncStatus.PENDING
                        )
                        self.state.total_messages_discovered += msg_count
                
                logger.info(f"Found {len(folders)} folders, {len(self.state.folder_states)} to sync, "
                           f"{self.state.total_messages_discovered:,} total messages")
            
            # Phase 2: Sync messages
            logger.info("Phase 2: Syncing messages...")
            await self._sync_all_folders(message_handler)
            
            # Complete
            if not self._cancel_requested:
                self.state.status = SyncStatus.COMPLETED
                self.state.completed_at = datetime.now()
            else:
                self.state.status = SyncStatus.PAUSED
            
        except Exception as e:
            logger.error(f"Sync error: {e}", exc_info=True)
            self.state.status = SyncStatus.FAILED
            self.state.errors.append({
                "error": str(e),
                "folder": self.state.current_folder_id,
                "time": datetime.now().isoformat()
            })
        finally:
            await self.disconnect()
        
        return self.state
    
    async def _sync_all_folders(
        self, 
        message_handler: Optional[Callable[[EmailMessage], None]] = None
    ):
        """Sync all pending folders."""
        # Get folders to process (respecting resume point)
        folders_to_sync = [
            fs for fs in self.state.folder_states.values()
            if fs.status in [SyncStatus.PENDING, SyncStatus.IN_PROGRESS]
        ]
        
        # Sort by message count (sync smaller folders first for quick wins)
        folders_to_sync.sort(key=lambda f: f.message_count)
        
        # If resuming, start from current folder
        if self.state.current_folder_id:
            idx = next(
                (i for i, f in enumerate(folders_to_sync) if f.folder_id == self.state.current_folder_id),
                0
            )
            folders_to_sync = folders_to_sync[idx:]
        
        for folder_state in folders_to_sync:
            if self._cancel_requested:
                break
            
            await self._sync_folder(folder_state, message_handler)
            self.state.folders_completed += 1
            self.state.save_checkpoint()
    
    async def _sync_folder(
        self,
        folder_state: FolderSyncState,
        message_handler: Optional[Callable[[EmailMessage], None]] = None
    ):
        """Sync a single folder."""
        folder_state.status = SyncStatus.IN_PROGRESS
        self.state.current_folder_id = folder_state.folder_id
        
        # Create folder object for fetching
        folder = EmailFolder(
            id=folder_state.folder_id,
            name=folder_state.folder_path.split("/")[-1],
            path=folder_state.folder_path
        )
        
        offset = self.state.current_folder_offset
        batch_size = self.config.batch_size
        checkpoint_counter = 0
        
        logger.info(f"Syncing folder: {folder.path} ({folder_state.message_count:,} messages, offset={offset})")
        
        try:
            async for message in self.fetch_messages(
                folder, 
                offset=offset, 
                batch_size=batch_size,
                since_uid=folder_state.last_uid
            ):
                if self._cancel_requested:
                    break
                
                # Process message
                if message_handler:
                    try:
                        message_handler(message)
                        folder_state.synced_count += 1
                        self.state.total_messages_synced += 1
                    except Exception as e:
                        logger.warning(f"Message handler error for {message.id}: {e}")
                        self.state.total_messages_failed += 1
                else:
                    folder_state.synced_count += 1
                    self.state.total_messages_synced += 1
                
                # Update last UID
                try:
                    uid = int(message.id.split("_")[-1])
                    folder_state.last_uid = max(folder_state.last_uid, uid)
                except (ValueError, IndexError):
                    pass
                
                # Update offset
                self.state.current_folder_offset = offset + folder_state.synced_count
                
                # Checkpoint
                checkpoint_counter += 1
                if checkpoint_counter >= self.config.checkpoint_interval:
                    self.state.save_checkpoint()
                    checkpoint_counter = 0
                    self._report_progress(self._get_progress())
            
            # Folder complete
            if not self._cancel_requested:
                folder_state.status = SyncStatus.COMPLETED
                folder_state.last_sync = datetime.now()
                self.state.current_folder_offset = 0
        
        except Exception as e:
            folder_state.status = SyncStatus.FAILED
            folder_state.error = str(e)
            logger.error(f"Error syncing folder {folder.path}: {e}")
            raise
    
    def _should_sync_folder(self, folder: EmailFolder) -> bool:
        """Check if folder should be synced based on config."""
        # Check exclusions
        for pattern in self.config.folders_to_exclude:
            if pattern.lower() in folder.path.lower():
                return False
        
        # Check inclusions (if specified)
        if self.config.folders_to_sync:
            for pattern in self.config.folders_to_sync:
                if pattern.lower() in folder.path.lower():
                    return True
            return False
        
        return True
    
    def _get_progress(self) -> EmailSyncProgress:
        """Calculate current progress."""
        if not self.state:
            return EmailSyncProgress(status=SyncStatus.PENDING)
        
        total = self.state.total_messages_discovered or 1
        synced = self.state.total_messages_synced
        
        # Calculate rate
        if self.state.started_at:
            elapsed = (datetime.now() - self.state.started_at).total_seconds() or 1
            rate = synced / elapsed
        else:
            rate = 0
        
        # Estimate remaining
        remaining = total - synced
        eta = int(remaining / rate) if rate > 0 else 0
        
        return EmailSyncProgress(
            status=self.state.status,
            current_folder=self.state.current_folder_id or "",
            current_folder_progress=0,  # Would need per-folder tracking
            overall_progress=(synced / total) * 100 if total > 0 else 0,
            folders_total=self.state.folders_discovered,
            folders_completed=self.state.folders_completed,
            messages_total=total,
            messages_synced=synced,
            messages_failed=self.state.total_messages_failed,
            current_rate=round(rate, 1),
            estimated_remaining_seconds=eta,
            errors=[e.get("error", "") for e in self.state.errors[-5:]]
        )
    
    @abstractmethod
    def _get_provider_type(self) -> str:
        """Return provider type string."""
        pass
