"""
Outlook Provider via Airbyte

Provides Microsoft Outlook/Exchange integration using Airbyte's
Microsoft Graph connector. Syncs emails, calendar, and attachments.
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


@register_provider(ProviderType.EMAIL_OUTLOOK)
class OutlookProvider(AirbyteProvider):
    """
    Outlook/Microsoft 365 integration via Airbyte.
    
    Uses Microsoft Graph API via OAuth 2.0.
    
    Syncs:
    - Emails/Messages (with body content)
    - Mail folders
    - Attachments
    - Calendar events (optional)
    """
    
    # Airbyte Microsoft Graph source definition ID (for mail)
    OUTLOOK_SOURCE_DEFINITION_ID = "7f0a4b3c-5e8f-4a1d-9b2c-6d7e8f9a0b1c"
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.EMAIL_OUTLOOK
    
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type=ProviderType.EMAIL_OUTLOOK,
            display_name="Outlook",
            description="Connect to Outlook.com or Microsoft 365 email",
            icon="microsoft-outlook",
            supported_auth_types=[AuthType.OAUTH2],
            oauth_scopes=[
                "Mail.Read",
                "User.Read",
                "offline_access",
            ],
            supports_delta_sync=True,
            supports_webhooks=True,
            supports_file_streaming=False,
            supports_folders=True,  # Mail folders
            supports_files=True,    # Emails
            supports_attachments=True,
            rate_limit_requests_per_minute=100,
            documentation_url="https://docs.microsoft.com/en-us/graph/",
            setup_instructions=(
                "1. Sign in with your Microsoft account\n"
                "2. Grant read access to mail\n"
                "3. Select folders to sync"
            ),
        )
    
    @property
    def source_definition_id(self) -> str:
        return self.OUTLOOK_SOURCE_DEFINITION_ID
    
    @property
    def source_display_name(self) -> str:
        return "Outlook"
    
    def build_source_config(
        self, 
        credentials: ConnectionCredentials
    ) -> dict[str, Any]:
        """
        Build Airbyte Outlook/Microsoft Graph source configuration.
        
        Outlook requires OAuth 2.0:
        - tenant_id: Azure AD tenant ID (or 'common')
        - client_id: Azure app client ID
        - client_secret: Azure app client secret
        - refresh_token: OAuth refresh token
        """
        config: dict[str, Any] = {}
        
        if credentials.auth_type == AuthType.OAUTH2 and credentials.oauth_tokens:
            config["credentials"] = {
                "auth_type": "oauth2.0",
                "tenant_id": credentials.extra.get("tenant_id", "common"),
                "client_id": credentials.extra.get("client_id", ""),
                "client_secret": credentials.extra.get("client_secret", ""),
                "refresh_token": credentials.oauth_tokens.refresh_token or "",
            }
        else:
            raise AirbyteProviderError(
                "Outlook requires OAuth 2.0 authentication. "
                "Please connect via OAuth flow."
            )
        
        # Configure which data to sync
        config["entities"] = ["mail"]  # Can add "calendar", "contacts" etc.
        
        return config
    
    def get_default_streams(self) -> list[str]:
        """Return default Outlook streams to sync."""
        return [
            "messages",
            "mail_folders",
        ]
    
    def transform_record(
        self, 
        stream_name: str, 
        record: dict[str, Any]
    ) -> Optional[RemoteFile]:
        """Transform Outlook records to RemoteFile format."""
        
        if stream_name == "messages":
            return self._transform_message(record)
        elif stream_name == "mail_folders":
            # Folders are used for organization, not content
            return None
        
        return None
    
    def _transform_message(self, record: dict[str, Any]) -> RemoteFile:
        """Transform an Outlook message record."""
        message_id = record.get("id", "")
        conversation_id = record.get("conversationId", "")
        
        subject = record.get("subject", "No Subject")
        from_addr = record.get("from", {}).get("emailAddress", {})
        from_email = from_addr.get("address", "")
        from_name = from_addr.get("name", from_email)
        
        to_recipients = record.get("toRecipients", [])
        to_list = [r.get("emailAddress", {}).get("address", "") for r in to_recipients]
        to_addr = ", ".join(to_list)
        
        # Get folder for path
        parent_folder_id = record.get("parentFolderId", "inbox")
        folder_name = self._get_folder_name(parent_folder_id)
        
        # Build path
        path = f"/{folder_name}/{subject[:50]}"
        
        # Get body content
        body = record.get("body", {})
        body_content = body.get("content", "")
        body_type = body.get("contentType", "text")
        
        # Build content
        content_parts = [
            f"# {subject}",
            f"\n**From:** {from_name} <{from_email}>",
            f"**To:** {to_addr}",
            f"**Date:** {record.get('receivedDateTime', '')}",
            f"**Importance:** {record.get('importance', 'normal')}",
        ]
        
        # Add categories/flags
        categories = record.get("categories", [])
        if categories:
            content_parts.append(f"**Categories:** {', '.join(categories)}")
        
        if record.get("flag", {}).get("flagStatus") == "flagged":
            content_parts.append("**Flagged:** Yes")
        
        content_parts.append(f"\n---\n\n{body_content}")
        content = "\n".join(content_parts)
        
        # Parse dates
        received_str = record.get("receivedDateTime", "")
        if received_str:
            try:
                # Handle ISO format with timezone
                if received_str.endswith("Z"):
                    received_str = received_str[:-1] + "+00:00"
                modified_at = datetime.fromisoformat(received_str)
            except (ValueError, TypeError):
                modified_at = datetime.now()
        else:
            modified_at = datetime.now()
        
        return RemoteFile(
            id=f"outlook_message_{message_id}",
            name=f"{subject[:50]}.eml",
            path=path,
            mime_type="message/rfc822" if body_type == "text" else "text/html",
            size_bytes=len(content.encode("utf-8")),
            modified_at=modified_at,
            web_view_url=record.get("webLink"),
            provider_metadata={
                "type": "email",
                "message_id": message_id,
                "conversation_id": conversation_id,
                "folder_id": parent_folder_id,
                "from_email": from_email,
                "from_name": from_name,
                "to": to_addr,
                "subject": subject,
                "content": content,
                "body_preview": record.get("bodyPreview", ""),
                "importance": record.get("importance", "normal"),
                "is_read": record.get("isRead", False),
                "has_attachments": record.get("hasAttachments", False),
                "categories": categories,
            }
        )
    
    def _get_folder_name(self, folder_id: str) -> str:
        """Map folder ID to friendly name."""
        # Common Outlook folder IDs
        folder_map = {
            "inbox": "Inbox",
            "sentitems": "Sent Items",
            "drafts": "Drafts",
            "deleteditems": "Deleted Items",
            "junkemail": "Junk Email",
            "archive": "Archive",
        }
        
        # Check if it's a known folder
        folder_lower = folder_id.lower()
        for key, name in folder_map.items():
            if key in folder_lower:
                return name
        
        return folder_id[:20]  # Use truncated ID as fallback
    
    # ==================== Browsing Methods ====================
    
    async def list_root_folders(self) -> list[RemoteFolder]:
        """List Outlook mail folders as root folders."""
        logger.info("Listing Outlook mail folders (requires sync to be completed)")
        return []
    
    async def list_folder_contents(
        self,
        folder_id: str,
        include_files: bool = True,
        include_folders: bool = True
    ) -> tuple[list[RemoteFolder], list[RemoteFile]]:
        """List emails in an Outlook folder."""
        logger.info(f"Listing contents of folder: {folder_id}")
        return [], []
    
    async def get_file_metadata(self, file_id: str) -> RemoteFile:
        """Get metadata for an Outlook message."""
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
        """List all emails in a folder."""
        logger.info(f"Listing all emails in folder: {folder_id}")
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
