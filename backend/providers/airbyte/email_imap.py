"""
IMAP Email Provider via Airbyte

Provides generic IMAP email integration using Airbyte's IMAP connector.
Works with any IMAP-compatible email server.
"""

import logging
from datetime import datetime
from typing import AsyncIterator, Optional, Any

from backend.providers.base import (
    ProviderType,
    ProviderCapabilities,
    AuthType,
    ConnectionCredentials,
    RemoteFile,
    RemoteFolder,
    SyncDelta,
)
from backend.providers.airbyte.base import AirbyteProvider, AirbyteProviderError
from backend.providers.registry import register_provider

logger = logging.getLogger(__name__)


@register_provider(ProviderType.EMAIL_IMAP)
class ImapProvider(AirbyteProvider):
    """
    Generic IMAP email integration via Airbyte.
    
    Supports any IMAP server with username/password authentication.
    
    Syncs:
    - Emails/Messages (with body content)
    - Folders (IMAP mailboxes)
    - Attachments
    
    NON-DESTRUCTIVE SYNC (Default Behavior):
    =====================================
    By default, this provider operates in read-only mode:
    
    1. Emails STAY on server - IMAP sync does not delete emails
    2. Unread emails REMAIN unread - Uses BODY.PEEK to avoid setting \\Seen flag
    3. Read emails REMAIN read - No flags are modified
    4. Other email clients see NO CHANGE - Email state is preserved exactly
    
    This behavior is controlled by these options (all default to safe values):
    - read_only: True (don't mark as read)
    - peek_mode: True (use BODY.PEEK)
    - preserve_flags: True (don't modify flags)
    - mark_as_read: False (don't mark as read after fetch)
    - delete_after_sync: False (NEVER delete from server)
    
    To override, pass options in credentials.extra:
        credentials.extra = {
            "read_only": False,      # Allow marking as read
            "mark_as_read": True,    # Mark synced emails as read
        }
    """
    
    # Airbyte IMAP source definition ID (placeholder - actual ID from Airbyte)
    IMAP_SOURCE_DEFINITION_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.EMAIL_IMAP
    
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type=ProviderType.EMAIL_IMAP,
            display_name="Email (IMAP)",
            description="Connect to any IMAP email server",
            icon="email",
            supported_auth_types=[AuthType.PASSWORD],
            oauth_scopes=[],  # IMAP uses password auth
            supports_delta_sync=True,  # Via UIDVALIDITY
            supports_webhooks=False,
            supports_file_streaming=False,
            supports_folders=True,  # Mailboxes
            supports_files=True,    # Emails
            supports_attachments=True,
            rate_limit_requests_per_minute=60,
            documentation_url=None,
            setup_instructions=(
                "1. Enter your IMAP server address\n"
                "2. Enter your email and password\n"
                "3. Select folders to sync"
            ),
        )
    
    @property
    def source_definition_id(self) -> str:
        return self.IMAP_SOURCE_DEFINITION_ID
    
    @property
    def source_display_name(self) -> str:
        return "Email (IMAP)"
    
    def build_source_config(
        self, 
        credentials: ConnectionCredentials
    ) -> dict[str, Any]:
        """
        Build Airbyte IMAP source configuration.
        
        IMAP requires:
        - host: IMAP server hostname
        - port: IMAP port (993 for SSL, 143 for plain)
        - username: Email address
        - password: Email password or app password
        - ssl: Whether to use SSL (default True)
        
        Read-only behavior (configurable):
        - read_only: If True, emails are NOT marked as read (default: True)
        - peek_mode: If True, use BODY.PEEK to avoid changing \\Seen flag (default: True)
        - preserve_flags: If True, don't modify any email flags (default: True)
        
        This ensures:
        - Emails remain on the server (IMAP never deletes by default)
        - Unread emails stay unread after sync
        - Read emails stay read
        - Other email clients see no change in email state
        """
        config: dict[str, Any] = {}
        
        if credentials.auth_type != AuthType.PASSWORD:
            raise AirbyteProviderError(
                "IMAP requires password authentication. "
                "Please provide email credentials."
            )
        
        if not credentials.server_url:
            raise AirbyteProviderError(
                "IMAP server URL is required. "
                "Please provide the IMAP server address."
            )
        
        if not credentials.username or not credentials.password:
            raise AirbyteProviderError(
                "Email credentials required. "
                "Please provide username and password."
            )
        
        # Parse server URL to extract host and port
        server_url = credentials.server_url
        host = server_url
        port = 993  # Default SSL port
        use_ssl = True
        
        # Handle different URL formats
        if "://" in server_url:
            # Remove protocol
            host = server_url.split("://", 1)[1]
        
        if ":" in host:
            # Extract port
            parts = host.rsplit(":", 1)
            host = parts[0]
            try:
                port = int(parts[1])
            except ValueError:
                port = 993
        
        # Use port 143 typically means no SSL
        if port == 143:
            use_ssl = False
        
        config["host"] = host
        config["port"] = port
        config["username"] = credentials.username
        config["password"] = credentials.password
        config["ssl"] = credentials.extra.get("ssl", use_ssl)
        config["tls"] = credentials.extra.get("tls", False)
        
        # Optional: specific folders to sync
        folders = credentials.extra.get("folders", ["INBOX"])
        config["folders"] = folders
        
        # ============================================================
        # READ-ONLY / NON-DESTRUCTIVE SYNC OPTIONS
        # ============================================================
        # These options ensure emails are left exactly as they were:
        # - Emails stay on server (IMAP default, no deletion)
        # - Unread emails remain unread
        # - Read emails remain read
        # - Flags are not modified
        # ============================================================
        
        # read_only: Don't mark messages as read when fetching
        # Default: True (preserve unread state)
        config["read_only"] = credentials.extra.get("read_only", True)
        
        # peek_mode: Use BODY.PEEK instead of BODY to avoid setting \\Seen flag
        # This is the IMAP-standard way to read without marking as read
        # Default: True
        config["peek_mode"] = credentials.extra.get("peek_mode", True)
        
        # preserve_flags: Don't modify any flags (\\Seen, \\Answered, etc.)
        # Default: True
        config["preserve_flags"] = credentials.extra.get("preserve_flags", True)
        
        # mark_as_read: Explicitly mark emails as read after fetching
        # Default: False (to preserve state)
        config["mark_as_read"] = credentials.extra.get("mark_as_read", False)
        
        # delete_after_sync: Delete emails from server after syncing
        # Default: False (NEVER delete, keep all emails on server)
        config["delete_after_sync"] = credentials.extra.get("delete_after_sync", False)
        
        return config
    
    def get_default_streams(self) -> list[str]:
        """Return default IMAP streams to sync."""
        return [
            "messages",
            "folders",
        ]
    
    def transform_record(
        self, 
        stream_name: str, 
        record: dict[str, Any]
    ) -> Optional[RemoteFile]:
        """Transform IMAP records to RemoteFile format."""
        
        if stream_name == "messages":
            return self._transform_message(record)
        elif stream_name == "folders":
            # Folders are used for organization, not content
            return None
        
        return None
    
    def _transform_message(self, record: dict[str, Any]) -> RemoteFile:
        """Transform an IMAP message record."""
        # IMAP messages typically have these fields
        message_id = record.get("message_id", record.get("uid", ""))
        uid = record.get("uid", message_id)
        folder = record.get("folder", "INBOX")
        
        # Headers
        subject = record.get("subject", "No Subject")
        from_addr = record.get("from", "")
        to_addr = record.get("to", "")
        cc_addr = record.get("cc", "")
        date_str = record.get("date", "")
        
        # Build path
        path = f"/{folder}/{subject[:50]}"
        
        # Get body content
        body_plain = record.get("body_plain", record.get("body", ""))
        body_html = record.get("body_html", "")
        
        # Prefer plain text, fall back to HTML
        body_content = body_plain if body_plain else body_html
        
        # Build formatted content
        content_parts = [
            f"# {subject}",
            f"\n**From:** {from_addr}",
            f"**To:** {to_addr}",
        ]
        
        if cc_addr:
            content_parts.append(f"**CC:** {cc_addr}")
        
        content_parts.extend([
            f"**Date:** {date_str}",
            f"**Folder:** {folder}",
            f"\n---\n\n{body_content}",
        ])
        content = "\n".join(content_parts)
        
        # Parse date
        modified_at = self._parse_email_date(date_str)
        
        # Check for attachments
        attachments = record.get("attachments", [])
        attachment_names = [a.get("filename", "") for a in attachments if a.get("filename")]
        
        return RemoteFile(
            id=f"imap_message_{folder}_{uid}",
            name=f"{subject[:50]}.eml",
            path=path,
            mime_type="message/rfc822",
            size_bytes=record.get("size", len(content.encode("utf-8"))),
            modified_at=modified_at,
            provider_metadata={
                "type": "email",
                "uid": uid,
                "message_id": message_id,
                "folder": folder,
                "from": from_addr,
                "to": to_addr,
                "cc": cc_addr,
                "subject": subject,
                "content": content,
                "has_attachments": len(attachments) > 0,
                "attachment_names": attachment_names,
                "flags": record.get("flags", []),
            }
        )
    
    def _parse_email_date(self, date_str: str) -> datetime:
        """Parse email date string in various formats."""
        if not date_str:
            return datetime.now()
        
        # Try common email date formats
        formats = [
            "%a, %d %b %Y %H:%M:%S %z",      # RFC 2822
            "%a, %d %b %Y %H:%M:%S %Z",      # RFC 2822 with timezone name
            "%d %b %Y %H:%M:%S %z",          # Without day name
            "%Y-%m-%d %H:%M:%S",             # ISO-ish
            "%Y-%m-%dT%H:%M:%S%z",           # ISO 8601
            "%Y-%m-%dT%H:%M:%SZ",            # ISO 8601 UTC
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue
        
        # Try to parse with dateutil if available
        try:
            from dateutil import parser
            return parser.parse(date_str)
        except (ImportError, ValueError):
            pass
        
        return datetime.now()
    
    # ==================== Browsing Methods ====================
    
    async def list_root_folders(self) -> list[RemoteFolder]:
        """List IMAP mailbox folders."""
        logger.info("Listing IMAP mailboxes (requires sync to be completed)")
        return []
    
    async def list_folder_contents(
        self,
        folder_id: str,
        include_files: bool = True,
        include_folders: bool = True
    ) -> tuple[list[RemoteFolder], list[RemoteFile]]:
        """List emails in an IMAP folder."""
        logger.info(f"Listing contents of mailbox: {folder_id}")
        return [], []
    
    async def get_file_metadata(self, file_id: str) -> RemoteFile:
        """Get metadata for an IMAP message."""
        raise NotImplementedError("Query MongoDB for synced content")
    
    async def download_file(self, file_id: str) -> AsyncIterator[bytes]:
        """Download email content."""
        raise NotImplementedError("Implement content retrieval")
        yield b""
    
    async def list_all_files(
        self,
        folder_id: str,
        recursive: bool = True,
        file_types: Optional[list[str]] = None
    ) -> AsyncIterator[RemoteFile]:
        """List all emails in a mailbox."""
        logger.info(f"Listing all emails in mailbox: {folder_id}")
        return
        yield
    
    async def get_changes(
        self,
        delta_token: Optional[str] = None,
        folder_id: Optional[str] = None
    ) -> SyncDelta:
        """Get changes since last sync via Airbyte."""
        if not self._connection_id:
            raise ValueError("Connection not established. Call authenticate() first.")
        
        job = await self.trigger_sync()
        completed_job = await self.wait_for_sync(job.job_id)
        
        return SyncDelta(
            added=[],
            modified=[],
            deleted=[],
            next_delta_token=str(completed_job.job_id),
            has_more=False,
            total_changes=completed_job.records_synced,
        )
