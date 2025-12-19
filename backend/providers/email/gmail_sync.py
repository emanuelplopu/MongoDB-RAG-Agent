"""
Direct Gmail Synchronization

Provides direct Gmail sync using Gmail API without Airbyte dependency.

Features:
- Label enumeration (including nested labels)
- Large-scale support (1M+ emails with pagination)
- History-based incremental sync
- Batched API requests
- Thread grouping
"""

import asyncio
import base64
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, AsyncIterator

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GMAIL_API_AVAILABLE = True
except ImportError:
    GMAIL_API_AVAILABLE = False
    Credentials = None

from backend.providers.email.base import (
    BaseEmailSync,
    EmailSyncConfig,
    EmailFolder,
    EmailMessage,
    EmailAttachment,
)

logger = logging.getLogger(__name__)


class DirectGmailSync(BaseEmailSync):
    """
    Direct Gmail synchronization using Gmail API.
    
    Uses OAuth 2.0 for authentication.
    Supports history-based incremental sync.
    """
    
    def __init__(self, config: EmailSyncConfig):
        super().__init__(config)
        self._service = None
        self._credentials = None
        self._connected = False
        self._user_email = "me"  # Use 'me' for authenticated user
        
        if not GMAIL_API_AVAILABLE:
            raise ImportError(
                "Gmail API libraries not installed. "
                "Install with: pip install google-api-python-client google-auth-oauthlib"
            )
    
    def _get_provider_type(self) -> str:
        return "gmail"
    
    async def connect(self) -> bool:
        """Connect to Gmail API."""
        try:
            # Build credentials from config
            if not self.config.access_token:
                logger.error("Gmail requires OAuth access token")
                return False
            
            self._credentials = Credentials(
                token=self.config.access_token,
                refresh_token=self.config.refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.config.client_id,
                client_secret=self.config.client_secret,
            )
            
            # Refresh if expired
            if self._credentials.expired and self._credentials.refresh_token:
                self._credentials.refresh(Request())
            
            # Build Gmail service
            self._service = build("gmail", "v1", credentials=self._credentials)
            
            # Verify connection
            profile = self._service.users().getProfile(userId="me").execute()
            self._user_email = profile.get("emailAddress", "me")
            
            logger.info(f"Connected to Gmail as: {self._user_email}")
            self._connected = True
            return True
            
        except Exception as e:
            logger.error(f"Gmail connection error: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from Gmail API."""
        self._service = None
        self._credentials = None
        self._connected = False
    
    async def enumerate_folders(
        self, 
        max_depth: int = 20
    ) -> List[EmailFolder]:
        """
        Enumerate Gmail labels.
        
        Gmail uses labels instead of folders.
        Nested labels use "/" separator (e.g., "Work/Projects/Active").
        
        Args:
            max_depth: Maximum label nesting depth
        
        Returns:
            List of labels as EmailFolder objects
        """
        if not self._connected or not self._service:
            raise RuntimeError("Not connected to Gmail")
        
        folders: List[EmailFolder] = []
        
        try:
            # List all labels
            result = self._service.users().labels().list(userId="me").execute()
            labels = result.get("labels", [])
            
            for label in labels:
                label_id = label.get("id", "")
                label_name = label.get("name", "")
                label_type = label.get("type", "user")
                
                # Calculate depth from label path
                depth = label_name.count("/")
                if depth > max_depth:
                    continue
                
                # Determine parent
                parent_id = None
                if "/" in label_name:
                    parent_name = "/".join(label_name.split("/")[:-1])
                    # Find parent label ID
                    for l in labels:
                        if l.get("name") == parent_name:
                            parent_id = l.get("id")
                            break
                
                # Get message counts (separate API call)
                try:
                    label_detail = self._service.users().labels().get(
                        userId="me", 
                        id=label_id
                    ).execute()
                    message_count = label_detail.get("messagesTotal", 0)
                    unread_count = label_detail.get("messagesUnread", 0)
                except Exception:
                    message_count = 0
                    unread_count = 0
                
                folder = EmailFolder(
                    id=label_id,
                    name=label_name.split("/")[-1],
                    path=label_name,
                    parent_id=parent_id,
                    depth=depth,
                    message_count=message_count,
                    unread_count=unread_count,
                    flags=["system"] if label_type == "system" else ["user"],
                    metadata={
                        "label_type": label_type,
                        "raw_id": label_id,
                    }
                )
                folders.append(folder)
            
            # Build hierarchy
            folder_map = {f.id: f for f in folders}
            for folder in folders:
                if folder.parent_id and folder.parent_id in folder_map:
                    folder_map[folder.parent_id].children.append(folder)
            
            logger.info(f"Enumerated {len(folders)} Gmail labels")
            return folders
            
        except Exception as e:
            logger.error(f"Error enumerating labels: {e}")
            raise
    
    async def get_folder_message_count(self, folder: EmailFolder) -> int:
        """Get total message count for a label."""
        if not self._connected or not self._service:
            raise RuntimeError("Not connected to Gmail")
        
        try:
            label = self._service.users().labels().get(
                userId="me",
                id=folder.id
            ).execute()
            return label.get("messagesTotal", 0)
        except Exception as e:
            logger.error(f"Error getting message count for {folder.path}: {e}")
            return 0
    
    async def fetch_messages(
        self,
        folder: EmailFolder,
        offset: int = 0,
        batch_size: int = 100,
        since_uid: int = 0
    ) -> AsyncIterator[EmailMessage]:
        """
        Fetch messages from a Gmail label.
        
        Uses list/get pattern with pagination.
        Fetches in batches to avoid memory issues.
        
        Args:
            folder: Label to fetch from
            offset: Page offset
            batch_size: Messages per batch
            since_uid: History ID for incremental sync
        
        Yields:
            EmailMessage objects
        """
        if not self._connected or not self._service:
            raise RuntimeError("Not connected to Gmail")
        
        try:
            page_token = None
            messages_fetched = 0
            skipped = 0
            
            while True:
                # List messages in label
                query_params = {
                    "userId": "me",
                    "labelIds": [folder.id],
                    "maxResults": min(batch_size, 500),  # Gmail max is 500
                }
                if page_token:
                    query_params["pageToken"] = page_token
                
                result = self._service.users().messages().list(**query_params).execute()
                messages = result.get("messages", [])
                
                if not messages:
                    break
                
                # Handle offset
                for msg_ref in messages:
                    # Skip to offset
                    if skipped < offset:
                        skipped += 1
                        continue
                    
                    if self._cancel_requested:
                        return
                    
                    # Fetch full message
                    message = await self._fetch_message(folder, msg_ref["id"])
                    if message:
                        yield message
                        messages_fetched += 1
                    
                    # Allow event loop to process
                    if messages_fetched % 10 == 0:
                        await asyncio.sleep(0)
                
                # Check for more pages
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
                
                logger.debug(f"Fetched {messages_fetched} messages from {folder.path}")
        
        except HttpError as e:
            logger.error(f"Gmail API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Error fetching messages: {e}")
            raise
    
    async def _fetch_message(
        self, 
        folder: EmailFolder, 
        message_id: str
    ) -> Optional[EmailMessage]:
        """Fetch a single message by ID."""
        try:
            # Get full message
            msg = self._service.users().messages().get(
                userId="me",
                id=message_id,
                format="full"
            ).execute()
            
            return self._parse_gmail_message(folder, msg)
            
        except Exception as e:
            logger.warning(f"Error fetching message {message_id}: {e}")
            return None
    
    def _parse_gmail_message(
        self, 
        folder: EmailFolder, 
        msg: Dict[str, Any]
    ) -> EmailMessage:
        """Parse Gmail API message format."""
        msg_id = msg.get("id", "")
        thread_id = msg.get("threadId", "")
        labels = msg.get("labelIds", [])
        
        # Parse headers
        headers = {}
        payload = msg.get("payload", {})
        for header in payload.get("headers", []):
            name = header.get("name", "").lower()
            value = header.get("value", "")
            headers[name] = value
        
        subject = headers.get("subject", "No Subject")
        from_header = headers.get("from", "")
        to_header = headers.get("to", "")
        cc_header = headers.get("cc", "")
        date_header = headers.get("date", "")
        
        # Parse from address
        from_name = ""
        from_addr = from_header
        if "<" in from_header:
            parts = from_header.rsplit("<", 1)
            from_name = parts[0].strip().strip('"')
            from_addr = parts[1].rstrip(">")
        
        # Parse recipients
        to_addrs = [a.strip() for a in to_header.split(",") if a.strip()]
        cc_addrs = [a.strip() for a in cc_header.split(",") if a.strip()] if cc_header else []
        
        # Parse date
        parsed_date = None
        internal_date = msg.get("internalDate")
        if internal_date:
            try:
                parsed_date = datetime.fromtimestamp(int(internal_date) / 1000)
            except Exception:
                pass
        
        # Extract body
        body_plain, body_html = self._extract_gmail_body(payload)
        snippet = msg.get("snippet", body_plain[:200] if body_plain else "")
        
        # Extract attachments
        attachments = self._extract_gmail_attachments(payload, msg_id)
        
        # Flags
        is_read = "UNREAD" not in labels
        is_starred = "STARRED" in labels
        
        return EmailMessage(
            id=f"gmail_{msg_id}",
            folder_id=folder.id,
            folder_path=folder.path,
            subject=subject,
            from_address=from_addr,
            from_name=from_name,
            to_addresses=to_addrs,
            cc_addresses=cc_addrs,
            date=parsed_date,
            body_plain=body_plain,
            body_html=body_html,
            snippet=snippet,
            flags=labels,
            is_read=is_read,
            is_starred=is_starred,
            has_attachments=len(attachments) > 0,
            attachments=attachments,
            headers=headers,
            thread_id=thread_id,
            in_reply_to=headers.get("in-reply-to", ""),
            references=headers.get("references", "").split(),
            size_bytes=int(msg.get("sizeEstimate", 0)),
            metadata={
                "gmail_id": msg_id,
                "labels": labels,
                "history_id": msg.get("historyId"),
            }
        )
    
    def _extract_gmail_body(
        self, 
        payload: Dict[str, Any]
    ) -> tuple[str, str]:
        """Extract body from Gmail payload."""
        body_plain = ""
        body_html = ""
        
        mime_type = payload.get("mimeType", "")
        
        # Simple message
        if "body" in payload and payload["body"].get("data"):
            text = self._decode_base64(payload["body"]["data"])
            if mime_type == "text/html":
                body_html = text
            else:
                body_plain = text
            return body_plain, body_html
        
        # Multipart message
        for part in payload.get("parts", []):
            part_mime = part.get("mimeType", "")
            
            if part_mime == "text/plain" and not body_plain:
                if part.get("body", {}).get("data"):
                    body_plain = self._decode_base64(part["body"]["data"])
            elif part_mime == "text/html" and not body_html:
                if part.get("body", {}).get("data"):
                    body_html = self._decode_base64(part["body"]["data"])
            elif part_mime.startswith("multipart/"):
                # Recursively check nested parts
                nested_plain, nested_html = self._extract_gmail_body(part)
                if not body_plain:
                    body_plain = nested_plain
                if not body_html:
                    body_html = nested_html
        
        return body_plain, body_html
    
    def _decode_base64(self, data: str) -> str:
        """Decode URL-safe base64."""
        try:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        except Exception:
            return ""
    
    def _extract_gmail_attachments(
        self, 
        payload: Dict[str, Any],
        message_id: str
    ) -> List[EmailAttachment]:
        """Extract attachments from Gmail payload."""
        attachments = []
        
        for part in payload.get("parts", []):
            filename = part.get("filename", "")
            if filename:
                attachment = EmailAttachment(
                    id=f"att_{message_id}_{part.get('partId', '')}",
                    filename=filename,
                    content_type=part.get("mimeType", "application/octet-stream"),
                    size_bytes=part.get("body", {}).get("size", 0),
                    content_id=part.get("headers", [{}])[0].get("value") 
                        if part.get("headers") else None,
                )
                attachments.append(attachment)
            
            # Check nested parts
            if part.get("parts"):
                nested_payload = {"parts": part["parts"]}
                attachments.extend(self._extract_gmail_attachments(nested_payload, message_id))
        
        return attachments
    
    async def get_history(
        self, 
        start_history_id: str
    ) -> AsyncIterator[EmailMessage]:
        """
        Get message changes since history ID.
        
        Enables efficient incremental sync.
        
        Args:
            start_history_id: Starting history ID
        
        Yields:
            Changed/new messages
        """
        if not self._connected or not self._service:
            raise RuntimeError("Not connected to Gmail")
        
        try:
            page_token = None
            
            while True:
                result = self._service.users().history().list(
                    userId="me",
                    startHistoryId=start_history_id,
                    pageToken=page_token
                ).execute()
                
                history = result.get("history", [])
                
                for record in history:
                    # Process added messages
                    for added in record.get("messagesAdded", []):
                        msg_ref = added.get("message", {})
                        if msg_ref.get("id"):
                            # Create temporary folder
                            temp_folder = EmailFolder(
                                id="INBOX",
                                name="INBOX",
                                path="INBOX"
                            )
                            message = await self._fetch_message(temp_folder, msg_ref["id"])
                            if message:
                                yield message
                
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
        
        except HttpError as e:
            if e.resp.status == 404:
                # History ID too old, need full sync
                logger.warning("History ID expired, full sync required")
            raise
