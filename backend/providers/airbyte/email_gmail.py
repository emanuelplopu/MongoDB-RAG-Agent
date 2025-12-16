"""
Gmail Provider via Airbyte

Provides Gmail integration using Airbyte's Gmail connector.
Syncs emails, threads, labels, and attachments.
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


@register_provider(ProviderType.EMAIL_GMAIL)
class GmailProvider(AirbyteProvider):
    """
    Gmail integration via Airbyte.
    
    Supports Gmail via OAuth 2.0 or Google Workspace Service Account.
    
    Syncs:
    - Emails/Messages (with body content)
    - Threads (conversation grouping)
    - Labels (folders/categories)
    - Attachments
    """
    
    # Airbyte Gmail source definition ID
    GMAIL_SOURCE_DEFINITION_ID = "ed9dfefa-1bbc-419d-8c5e-4d78f0ef6734"
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.EMAIL_GMAIL
    
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type=ProviderType.EMAIL_GMAIL,
            display_name="Gmail",
            description="Connect to Gmail to index emails and attachments",
            icon="google-gmail",
            supported_auth_types=[AuthType.OAUTH2],
            oauth_scopes=[
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/userinfo.email",
            ],
            supports_delta_sync=True,
            supports_webhooks=True,
            supports_file_streaming=False,
            supports_folders=True,  # Labels
            supports_files=True,    # Emails
            supports_attachments=True,
            rate_limit_requests_per_minute=250,
            documentation_url="https://developers.google.com/gmail/api",
            setup_instructions=(
                "1. Sign in with your Google account\n"
                "2. Grant read access to Gmail\n"
                "3. Select labels/folders to sync"
            ),
        )
    
    @property
    def source_definition_id(self) -> str:
        return self.GMAIL_SOURCE_DEFINITION_ID
    
    @property
    def source_display_name(self) -> str:
        return "Gmail"
    
    def build_source_config(
        self, 
        credentials: ConnectionCredentials
    ) -> dict[str, Any]:
        """
        Build Airbyte Gmail source configuration.
        
        Gmail requires OAuth 2.0:
        - client_id: Google OAuth client ID
        - client_secret: Google OAuth client secret
        - refresh_token: OAuth refresh token
        """
        config: dict[str, Any] = {}
        
        if credentials.auth_type == AuthType.OAUTH2 and credentials.oauth_tokens:
            config["credentials"] = {
                "auth_type": "Client",
                "client_id": credentials.extra.get("client_id", ""),
                "client_secret": credentials.extra.get("client_secret", ""),
                "refresh_token": credentials.oauth_tokens.refresh_token or "",
            }
        else:
            raise AirbyteProviderError(
                "Gmail requires OAuth 2.0 authentication. "
                "Please connect via OAuth flow."
            )
        
        return config
    
    def get_default_streams(self) -> list[str]:
        """Return default Gmail streams to sync."""
        return [
            "messages",
            "threads",
            "labels",
        ]
    
    def transform_record(
        self, 
        stream_name: str, 
        record: dict[str, Any]
    ) -> Optional[RemoteFile]:
        """Transform Gmail records to RemoteFile format."""
        
        if stream_name == "messages":
            return self._transform_message(record)
        elif stream_name == "threads":
            return self._transform_thread(record)
        
        return None
    
    def _transform_message(self, record: dict[str, Any]) -> RemoteFile:
        """Transform a Gmail message record."""
        message_id = record.get("id", "")
        thread_id = record.get("threadId", "")
        
        # Extract headers
        headers = {h.get("name", "").lower(): h.get("value", "") 
                   for h in record.get("payload", {}).get("headers", [])}
        
        subject = headers.get("subject", "No Subject")
        from_addr = headers.get("from", "")
        to_addr = headers.get("to", "")
        date_str = headers.get("date", "")
        
        # Extract labels for path
        labels = record.get("labelIds", [])
        primary_label = labels[0] if labels else "INBOX"
        
        # Build path
        path = f"/{primary_label}/{subject[:50]}"
        
        # Extract body content
        body = self._extract_body(record.get("payload", {}))
        
        # Build content
        content_parts = [
            f"# {subject}",
            f"\n**From:** {from_addr}",
            f"**To:** {to_addr}",
            f"**Date:** {date_str}",
            f"**Labels:** {', '.join(labels)}",
            f"\n---\n\n{body}",
        ]
        content = "\n".join(content_parts)
        
        # Parse date
        internal_date = record.get("internalDate", "")
        if internal_date:
            try:
                modified_at = datetime.fromtimestamp(int(internal_date) / 1000)
            except (ValueError, TypeError):
                modified_at = datetime.now()
        else:
            modified_at = datetime.now()
        
        return RemoteFile(
            id=f"gmail_message_{message_id}",
            name=f"{subject[:50]}.eml",
            path=path,
            mime_type="message/rfc822",
            size_bytes=int(record.get("sizeEstimate", 0)),
            modified_at=modified_at,
            provider_metadata={
                "type": "email",
                "message_id": message_id,
                "thread_id": thread_id,
                "labels": labels,
                "from": from_addr,
                "to": to_addr,
                "subject": subject,
                "content": content,
                "snippet": record.get("snippet", ""),
                "has_attachments": self._has_attachments(record.get("payload", {})),
            }
        )
    
    def _extract_body(self, payload: dict[str, Any]) -> str:
        """Extract email body from payload."""
        body = ""
        
        # Check for direct body
        if payload.get("body", {}).get("data"):
            import base64
            try:
                body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
            except Exception:
                pass
        
        # Check parts for multipart messages
        for part in payload.get("parts", []):
            mime_type = part.get("mimeType", "")
            if mime_type == "text/plain" and part.get("body", {}).get("data"):
                import base64
                try:
                    body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                    break
                except Exception:
                    pass
            elif mime_type == "text/html" and not body and part.get("body", {}).get("data"):
                import base64
                try:
                    body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                except Exception:
                    pass
            # Recursively check nested parts
            elif part.get("parts"):
                nested_body = self._extract_body(part)
                if nested_body:
                    body = nested_body
                    break
        
        return body
    
    def _has_attachments(self, payload: dict[str, Any]) -> bool:
        """Check if message has attachments."""
        for part in payload.get("parts", []):
            if part.get("filename"):
                return True
            if part.get("parts") and self._has_attachments(part):
                return True
        return False
    
    def _transform_thread(self, record: dict[str, Any]) -> RemoteFile:
        """Transform a Gmail thread record."""
        thread_id = record.get("id", "")
        messages = record.get("messages", [])
        
        if not messages:
            # Empty thread, skip
            return RemoteFile(
                id=f"gmail_thread_{thread_id}",
                name="Empty Thread",
                path="/threads",
                mime_type="text/plain",
                size_bytes=0,
                modified_at=datetime.now(),
                provider_metadata={"type": "thread", "thread_id": thread_id}
            )
        
        # Get first message for subject
        first_msg = messages[0]
        headers = {h.get("name", "").lower(): h.get("value", "") 
                   for h in first_msg.get("payload", {}).get("headers", [])}
        
        subject = headers.get("subject", "No Subject")
        
        # Build thread content
        content_parts = [f"# Thread: {subject}\n"]
        
        for msg in messages:
            msg_headers = {h.get("name", "").lower(): h.get("value", "") 
                          for h in msg.get("payload", {}).get("headers", [])}
            content_parts.append(f"\n## From: {msg_headers.get('from', '')}")
            content_parts.append(f"Date: {msg_headers.get('date', '')}")
            content_parts.append(f"\n{self._extract_body(msg.get('payload', {}))}")
            content_parts.append("\n---")
        
        content = "\n".join(content_parts)
        
        # Use latest message date
        latest_msg = messages[-1]
        internal_date = latest_msg.get("internalDate", "")
        if internal_date:
            try:
                modified_at = datetime.fromtimestamp(int(internal_date) / 1000)
            except (ValueError, TypeError):
                modified_at = datetime.now()
        else:
            modified_at = datetime.now()
        
        return RemoteFile(
            id=f"gmail_thread_{thread_id}",
            name=f"{subject[:50]}_thread.md",
            path=f"/threads/{subject[:30]}",
            mime_type="text/markdown",
            size_bytes=len(content.encode("utf-8")),
            modified_at=modified_at,
            provider_metadata={
                "type": "thread",
                "thread_id": thread_id,
                "message_count": len(messages),
                "subject": subject,
                "content": content,
            }
        )
    
    # ==================== Browsing Methods ====================
    
    async def list_root_folders(self) -> list[RemoteFolder]:
        """List Gmail labels as root folders."""
        logger.info("Listing Gmail labels (requires sync to be completed)")
        return []
    
    async def list_folder_contents(
        self,
        folder_id: str,
        include_files: bool = True,
        include_folders: bool = True
    ) -> tuple[list[RemoteFolder], list[RemoteFile]]:
        """List emails in a Gmail label."""
        logger.info(f"Listing contents of label: {folder_id}")
        return [], []
    
    async def get_file_metadata(self, file_id: str) -> RemoteFile:
        """Get metadata for a Gmail message."""
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
        """List all emails in a label."""
        logger.info(f"Listing all emails in label: {folder_id}")
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
