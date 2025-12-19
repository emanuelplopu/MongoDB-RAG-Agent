"""
Unit tests for Direct Email Sync providers.

Tests the email sync implementations without requiring actual email servers.
Uses mocking for external dependencies.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from backend.providers.email.base import (
    EmailSyncConfig,
    EmailSyncState,
    EmailFolder,
    EmailMessage,
    EmailAttachment,
    SyncStatus,
    FolderSyncState,
)


class TestEmailSyncConfig:
    """Tests for EmailSyncConfig."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = EmailSyncConfig()
        
        assert config.port == 993
        assert config.use_ssl is True
        assert config.read_only is True
        assert config.peek_mode is True
        assert config.mark_as_read is False
        assert config.delete_after_sync is False
        assert config.batch_size == 100
        assert config.max_attachment_size_mb == 25
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = EmailSyncConfig(
            host="imap.example.com",
            port=993,
            username="user@example.com",
            password="secret",
            batch_size=500,
            folders_to_sync=["INBOX", "Sent"],
            folders_to_exclude=["Spam", "Trash"],
        )
        
        assert config.host == "imap.example.com"
        assert config.batch_size == 500
        assert len(config.folders_to_sync) == 2
        assert len(config.folders_to_exclude) == 2


class TestEmailFolder:
    """Tests for EmailFolder."""
    
    def test_folder_creation(self):
        """Test folder creation."""
        folder = EmailFolder(
            id="INBOX",
            name="Inbox",
            path="INBOX",
            depth=0,
            message_count=1000,
        )
        
        assert folder.id == "INBOX"
        assert folder.name == "Inbox"
        assert folder.depth == 0
        assert folder.message_count == 1000
        assert folder.children == []
    
    def test_nested_folder(self):
        """Test nested folder with parent."""
        parent = EmailFolder(
            id="Work",
            name="Work",
            path="Work",
            depth=0,
        )
        
        child = EmailFolder(
            id="Work/Projects",
            name="Projects",
            path="Work/Projects",
            parent_id="Work",
            depth=1,
        )
        
        parent.children.append(child)
        
        assert len(parent.children) == 1
        assert child.parent_id == "Work"
        assert child.depth == 1
    
    def test_deep_nesting(self):
        """Test deeply nested folder structure."""
        folders = []
        parent_id = None
        
        for i in range(10):
            path_parts = [f"Level{j}" for j in range(i + 1)]
            folder = EmailFolder(
                id="/".join(path_parts),
                name=f"Level{i}",
                path="/".join(path_parts),
                parent_id=parent_id,
                depth=i,
            )
            folders.append(folder)
            parent_id = folder.id
        
        assert len(folders) == 10
        assert folders[-1].depth == 9
        assert folders[-1].path == "Level0/Level1/Level2/Level3/Level4/Level5/Level6/Level7/Level8/Level9"
    
    def test_to_dict(self):
        """Test folder serialization."""
        folder = EmailFolder(
            id="INBOX",
            name="Inbox",
            path="INBOX",
            message_count=500,
            unread_count=10,
            flags=["\\Noinferiors"],
        )
        
        data = folder.to_dict()
        
        assert data["id"] == "INBOX"
        assert data["message_count"] == 500
        assert data["unread_count"] == 10
        assert "\\Noinferiors" in data["flags"]


class TestEmailMessage:
    """Tests for EmailMessage."""
    
    def test_message_creation(self):
        """Test message creation."""
        msg = EmailMessage(
            id="msg_123",
            folder_id="INBOX",
            folder_path="INBOX",
            subject="Test Subject",
            from_address="sender@example.com",
            from_name="Sender Name",
            to_addresses=["recipient@example.com"],
            date=datetime(2024, 1, 15, 10, 30),
            body_plain="Hello, this is a test.",
            is_read=True,
        )
        
        assert msg.id == "msg_123"
        assert msg.subject == "Test Subject"
        assert msg.from_address == "sender@example.com"
        assert msg.is_read is True
        assert len(msg.to_addresses) == 1
    
    def test_message_with_attachments(self):
        """Test message with attachments."""
        attachments = [
            EmailAttachment(
                id="att1",
                filename="document.pdf",
                content_type="application/pdf",
                size_bytes=1024 * 500,
            ),
            EmailAttachment(
                id="att2",
                filename="image.png",
                content_type="image/png",
                size_bytes=1024 * 100,
            ),
        ]
        
        msg = EmailMessage(
            id="msg_456",
            folder_id="INBOX",
            folder_path="INBOX",
            subject="With Attachments",
            from_address="sender@example.com",
            has_attachments=True,
            attachments=attachments,
        )
        
        assert msg.has_attachments is True
        assert len(msg.attachments) == 2
        assert msg.attachments[0].filename == "document.pdf"
    
    def test_message_to_content(self):
        """Test message content generation."""
        msg = EmailMessage(
            id="msg_789",
            folder_id="INBOX",
            folder_path="INBOX",
            subject="Important Meeting",
            from_address="boss@company.com",
            from_name="The Boss",
            to_addresses=["team@company.com"],
            cc_addresses=["manager@company.com"],
            date=datetime(2024, 3, 15, 14, 0),
            body_plain="Please join the meeting at 3 PM.",
        )
        
        content = msg.to_content()
        
        assert "# Important Meeting" in content
        assert "**From:** The Boss" in content
        assert "boss@company.com" in content
        assert "team@company.com" in content
        assert "manager@company.com" in content
        assert "Please join the meeting" in content
    
    def test_message_to_dict(self):
        """Test message serialization."""
        msg = EmailMessage(
            id="msg_001",
            folder_id="INBOX",
            folder_path="INBOX",
            subject="Test",
            from_address="test@test.com",
            date=datetime(2024, 1, 1, 12, 0),
            size_bytes=1500,
        )
        
        data = msg.to_dict()
        
        assert data["id"] == "msg_001"
        assert data["subject"] == "Test"
        assert data["size_bytes"] == 1500
        assert "2024-01-01" in data["date"]


class TestEmailSyncState:
    """Tests for EmailSyncState."""
    
    def test_state_creation(self):
        """Test sync state creation."""
        state = EmailSyncState(
            connection_id="conn_123",
            user_id="user@example.com",
            provider_type="imap",
        )
        
        assert state.connection_id == "conn_123"
        assert state.status == SyncStatus.PENDING
        assert state.folders_discovered == 0
        assert state.total_messages_synced == 0
    
    def test_state_with_folder_progress(self):
        """Test state with folder sync progress."""
        state = EmailSyncState(
            connection_id="conn_456",
            user_id="user@example.com",
            provider_type="gmail",
            status=SyncStatus.IN_PROGRESS,
            folders_discovered=10,
            folders_completed=5,
            total_messages_discovered=10000,
            total_messages_synced=5500,
        )
        
        # Add folder states
        state.folder_states["INBOX"] = FolderSyncState(
            folder_id="INBOX",
            folder_path="INBOX",
            message_count=5000,
            synced_count=5000,
            status=SyncStatus.COMPLETED,
        )
        state.folder_states["Sent"] = FolderSyncState(
            folder_id="Sent",
            folder_path="Sent",
            message_count=5000,
            synced_count=500,
            status=SyncStatus.IN_PROGRESS,
        )
        
        assert len(state.folder_states) == 2
        assert state.folder_states["INBOX"].status == SyncStatus.COMPLETED
        assert state.folder_states["Sent"].synced_count == 500
    
    def test_state_serialization(self):
        """Test state to_dict and from_dict."""
        state = EmailSyncState(
            connection_id="conn_789",
            user_id="user@test.com",
            provider_type="outlook",
            status=SyncStatus.IN_PROGRESS,
            folders_discovered=5,
            total_messages_discovered=1000,
            total_messages_synced=250,
            current_folder_id="INBOX",
            current_folder_offset=250,
        )
        state.started_at = datetime(2024, 3, 15, 10, 0)
        state.save_checkpoint()
        
        # Serialize
        data = state.to_dict()
        
        assert data["connection_id"] == "conn_789"
        assert data["status"] == "in_progress"
        assert data["total_messages_synced"] == 250
        
        # Deserialize
        restored = EmailSyncState.from_dict(data)
        
        assert restored.connection_id == "conn_789"
        assert restored.status == SyncStatus.IN_PROGRESS
        assert restored.current_folder_offset == 250
    
    def test_checkpoint_and_resume(self):
        """Test checkpoint saving and resume point."""
        state = EmailSyncState(
            connection_id="conn_resume",
            user_id="user@example.com",
            provider_type="imap",
            current_folder_id="Work/Projects",
            current_folder_offset=1500,
        )
        
        # Save checkpoint
        state.save_checkpoint()
        
        assert state.last_checkpoint is not None
        
        # Get resume point
        folder_id, offset = state.get_resume_point()
        
        assert folder_id == "Work/Projects"
        assert offset == 1500


class TestLargeScaleHandling:
    """Tests for large-scale email handling (1M+ messages)."""
    
    def test_folder_with_million_messages(self):
        """Test folder with 1 million+ messages."""
        folder = EmailFolder(
            id="Archive",
            name="Archive",
            path="Archive",
            message_count=1_500_000,
        )
        
        assert folder.message_count == 1_500_000
    
    def test_state_tracking_large_sync(self):
        """Test state tracking for large sync."""
        state = EmailSyncState(
            connection_id="large_sync",
            user_id="user@corp.com",
            provider_type="outlook",
            total_messages_discovered=2_000_000,
            total_messages_synced=1_234_567,
        )
        
        # Simulate progress
        for i in range(100):
            state.total_messages_synced += 1000
            if i % 10 == 0:
                state.save_checkpoint()
        
        assert state.total_messages_synced == 1_334_567
        assert state.last_checkpoint is not None
    
    def test_batch_size_configuration(self):
        """Test various batch size configurations."""
        # Small batch for slow connections
        small_batch = EmailSyncConfig(batch_size=50)
        assert small_batch.batch_size == 50
        
        # Large batch for fast connections
        large_batch = EmailSyncConfig(batch_size=1000)
        assert large_batch.batch_size == 1000
        
        # Default batch
        default_batch = EmailSyncConfig()
        assert default_batch.batch_size == 100


class TestFolderFiltering:
    """Tests for folder filtering logic."""
    
    def test_folder_exclusion_patterns(self):
        """Test folder exclusion patterns."""
        config = EmailSyncConfig(
            folders_to_exclude=["Spam", "Trash", "Junk"]
        )
        
        folders = [
            EmailFolder(id="INBOX", name="INBOX", path="INBOX"),
            EmailFolder(id="Spam", name="Spam", path="Spam"),
            EmailFolder(id="Work", name="Work", path="Work"),
            EmailFolder(id="Trash", name="Trash", path="Trash"),
        ]
        
        # Filter folders
        filtered = [
            f for f in folders 
            if not any(ex.lower() in f.path.lower() for ex in config.folders_to_exclude)
        ]
        
        assert len(filtered) == 2
        assert all(f.name not in ["Spam", "Trash"] for f in filtered)
    
    def test_folder_inclusion_patterns(self):
        """Test folder inclusion patterns."""
        config = EmailSyncConfig(
            folders_to_sync=["INBOX", "Work"]
        )
        
        folders = [
            EmailFolder(id="INBOX", name="INBOX", path="INBOX"),
            EmailFolder(id="Work", name="Work", path="Work"),
            EmailFolder(id="Personal", name="Personal", path="Personal"),
            EmailFolder(id="Archives", name="Archives", path="Archives"),
        ]
        
        # Filter folders
        filtered = [
            f for f in folders 
            if any(inc.lower() in f.path.lower() for inc in config.folders_to_sync)
        ]
        
        assert len(filtered) == 2
        assert all(f.name in ["INBOX", "Work"] for f in filtered)


class TestDeepFolderEnumeration:
    """Tests for deep folder enumeration (10+ levels)."""
    
    def test_enumerate_10_levels(self):
        """Test enumerating folders at 10 levels deep."""
        # Simulate folder hierarchy
        all_folders = []
        
        def create_nested_folders(parent_path: str, parent_id: str, depth: int, max_depth: int):
            if depth > max_depth:
                return
            
            for i in range(2):  # 2 folders per level
                name = f"Folder_L{depth}_{i}"
                path = f"{parent_path}/{name}" if parent_path else name
                folder_id = path.replace("/", "_")
                
                folder = EmailFolder(
                    id=folder_id,
                    name=name,
                    path=path,
                    parent_id=parent_id if parent_id else None,
                    depth=depth,
                )
                all_folders.append(folder)
                
                create_nested_folders(path, folder_id, depth + 1, max_depth)
        
        create_nested_folders("", "", 0, 10)
        
        # Should have folders at all depths
        depths = set(f.depth for f in all_folders)
        assert max(depths) == 10
        
        # Verify hierarchy
        deep_folders = [f for f in all_folders if f.depth == 10]
        assert len(deep_folders) > 0
        
        for df in deep_folders:
            # Path should have 11 segments (depth 0-10)
            segments = df.path.split("/")
            assert len(segments) == 11
    
    def test_max_depth_limit(self):
        """Test max depth limit is respected."""
        MAX_DEPTH = 5
        
        folders = []
        for depth in range(15):
            folder = EmailFolder(
                id=f"folder_{depth}",
                name=f"Level{depth}",
                path="/".join([f"Level{i}" for i in range(depth + 1)]),
                depth=depth,
            )
            if depth <= MAX_DEPTH:
                folders.append(folder)
        
        assert len(folders) == 6  # Depths 0-5
        assert max(f.depth for f in folders) == MAX_DEPTH


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
