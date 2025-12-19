"""
Direct Outlook/Microsoft 365 Synchronization

Provides direct Outlook sync using Microsoft Graph API without Airbyte dependency.

Features:
- Mail folder enumeration (including nested folders)
- Large-scale support (1M+ emails with pagination)
- Delta sync for incremental updates
- Batched API requests
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, AsyncIterator

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from backend.providers.email.base import (
    BaseEmailSync,
    EmailSyncConfig,
    EmailFolder,
    EmailMessage,
    EmailAttachment,
)

logger = logging.getLogger(__name__)


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class DirectOutlookSync(BaseEmailSync):
    """
    Direct Outlook/Microsoft 365 synchronization using Microsoft Graph API.
    
    Uses OAuth 2.0 for authentication.
    Supports delta queries for incremental sync.
    """
    
    def __init__(self, config: EmailSyncConfig):
        super().__init__(config)
        self._client: Optional[httpx.AsyncClient] = None
        self._connected = False
        self._delta_links: Dict[str, str] = {}  # folder_id -> delta link
        
        if not HTTPX_AVAILABLE:
            raise ImportError(
                "httpx library not installed. "
                "Install with: pip install httpx"
            )
    
    def _get_provider_type(self) -> str:
        return "outlook"
    
    async def connect(self) -> bool:
        """Connect to Microsoft Graph API."""
        try:
            if not self.config.access_token:
                logger.error("Outlook requires OAuth access token")
                return False
            
            # Create HTTP client with auth headers
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.config.access_token}",
                    "Content-Type": "application/json",
                },
                timeout=self.config.timeout_seconds,
            )
            
            # Verify connection by getting user profile
            response = await self._client.get(f"{GRAPH_BASE_URL}/me")
            
            if response.status_code != 200:
                logger.error(f"Graph API error: {response.status_code} - {response.text}")
                return False
            
            user_data = response.json()
            logger.info(f"Connected to Outlook as: {user_data.get('mail', user_data.get('userPrincipalName'))}")
            
            self._connected = True
            return True
            
        except Exception as e:
            logger.error(f"Outlook connection error: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from Microsoft Graph API."""
        if self._client:
            await self._client.aclose()
        self._client = None
        self._connected = False
    
    async def enumerate_folders(
        self, 
        max_depth: int = 20
    ) -> List[EmailFolder]:
        """
        Enumerate all mail folders recursively.
        
        Uses childFolders endpoint to traverse hierarchy.
        
        Args:
            max_depth: Maximum folder nesting depth
        
        Returns:
            Flat list of all folders with hierarchy info
        """
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to Outlook")
        
        folders: List[EmailFolder] = []
        
        try:
            # Get root folders
            root_folders = await self._get_child_folders(None)
            
            # BFS to enumerate all folders
            queue = [(f, 0, None, "") for f in root_folders]  # (folder_data, depth, parent_id, parent_path)
            
            while queue:
                folder_data, depth, parent_id, parent_path = queue.pop(0)
                
                if depth > max_depth:
                    continue
                
                folder_id = folder_data.get("id", "")
                folder_name = folder_data.get("displayName", "")
                full_path = f"{parent_path}/{folder_name}" if parent_path else folder_name
                
                folder = EmailFolder(
                    id=folder_id,
                    name=folder_name,
                    path=full_path,
                    parent_id=parent_id,
                    depth=depth,
                    message_count=folder_data.get("totalItemCount", 0),
                    unread_count=folder_data.get("unreadItemCount", 0),
                    metadata={
                        "is_hidden": folder_data.get("isHidden", False),
                        "well_known_name": folder_data.get("wellKnownName"),
                    }
                )
                folders.append(folder)
                
                # Get child folders
                child_count = folder_data.get("childFolderCount", 0)
                if child_count > 0:
                    children = await self._get_child_folders(folder_id)
                    for child in children:
                        queue.append((child, depth + 1, folder_id, full_path))
            
            # Build hierarchy in folder objects
            folder_map = {f.id: f for f in folders}
            for folder in folders:
                if folder.parent_id and folder.parent_id in folder_map:
                    folder_map[folder.parent_id].children.append(folder)
            
            logger.info(f"Enumerated {len(folders)} Outlook folders (max depth: {max(f.depth for f in folders) if folders else 0})")
            return folders
            
        except Exception as e:
            logger.error(f"Error enumerating folders: {e}")
            raise
    
    async def _get_child_folders(
        self, 
        parent_folder_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Get child folders of a parent (or root if None)."""
        if parent_folder_id:
            url = f"{GRAPH_BASE_URL}/me/mailFolders/{parent_folder_id}/childFolders"
        else:
            url = f"{GRAPH_BASE_URL}/me/mailFolders"
        
        folders = []
        
        while url:
            response = await self._client.get(url)
            
            if response.status_code != 200:
                logger.warning(f"Error getting folders: {response.status_code}")
                break
            
            data = response.json()
            folders.extend(data.get("value", []))
            
            # Handle pagination
            url = data.get("@odata.nextLink")
        
        return folders
    
    async def get_folder_message_count(self, folder: EmailFolder) -> int:
        """Get total message count for a folder."""
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to Outlook")
        
        try:
            response = await self._client.get(
                f"{GRAPH_BASE_URL}/me/mailFolders/{folder.id}"
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("totalItemCount", 0)
            
            return 0
        except Exception as e:
            logger.error(f"Error getting message count: {e}")
            return 0
    
    async def fetch_messages(
        self,
        folder: EmailFolder,
        offset: int = 0,
        batch_size: int = 100,
        since_uid: int = 0
    ) -> AsyncIterator[EmailMessage]:
        """
        Fetch messages from an Outlook folder.
        
        Uses OData pagination ($skip, $top).
        Supports delta queries for incremental sync.
        
        Args:
            folder: Folder to fetch from
            offset: Skip first N messages
            batch_size: Messages per request
            since_uid: Not used for Outlook (use delta links instead)
        
        Yields:
            EmailMessage objects
        """
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to Outlook")
        
        try:
            # Build initial URL with OData parameters
            # Sort by receivedDateTime descending (newest first)
            select_fields = (
                "id,subject,bodyPreview,body,from,toRecipients,ccRecipients,"
                "receivedDateTime,sentDateTime,hasAttachments,isRead,flag,"
                "importance,conversationId,parentFolderId,webLink"
            )
            
            url = (
                f"{GRAPH_BASE_URL}/me/mailFolders/{folder.id}/messages"
                f"?$select={select_fields}"
                f"&$orderby=receivedDateTime desc"
                f"&$top={min(batch_size, 999)}"  # Graph API max is 999
            )
            
            if offset > 0:
                url += f"&$skip={offset}"
            
            messages_fetched = 0
            
            while url:
                if self._cancel_requested:
                    return
                
                response = await self._client.get(url)
                
                if response.status_code != 200:
                    logger.error(f"Error fetching messages: {response.status_code}")
                    break
                
                data = response.json()
                messages = data.get("value", [])
                
                if not messages:
                    break
                
                for msg_data in messages:
                    message = self._parse_outlook_message(folder, msg_data)
                    yield message
                    messages_fetched += 1
                    
                    # Allow event loop to process
                    if messages_fetched % 10 == 0:
                        await asyncio.sleep(0)
                
                # Handle pagination
                url = data.get("@odata.nextLink")
                
                logger.debug(f"Fetched {messages_fetched} messages from {folder.path}")
        
        except Exception as e:
            logger.error(f"Error fetching messages: {e}")
            raise
    
    def _parse_outlook_message(
        self, 
        folder: EmailFolder, 
        msg: Dict[str, Any]
    ) -> EmailMessage:
        """Parse Microsoft Graph message format."""
        msg_id = msg.get("id", "")
        
        # Parse from
        from_data = msg.get("from", {}).get("emailAddress", {})
        from_addr = from_data.get("address", "")
        from_name = from_data.get("name", "")
        
        # Parse recipients
        to_addrs = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("toRecipients", [])
        ]
        cc_addrs = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("ccRecipients", [])
        ]
        
        # Parse dates
        received_str = msg.get("receivedDateTime", "")
        sent_str = msg.get("sentDateTime", "")
        
        received_date = None
        if received_str:
            try:
                received_date = datetime.fromisoformat(received_str.replace("Z", "+00:00"))
            except Exception:
                pass
        
        # Get body
        body_data = msg.get("body", {})
        body_content = body_data.get("content", "")
        body_type = body_data.get("contentType", "text")
        
        if body_type == "html":
            body_html = body_content
            body_plain = ""
        else:
            body_plain = body_content
            body_html = ""
        
        # Flag status
        flag_status = msg.get("flag", {}).get("flagStatus", "")
        is_starred = flag_status == "flagged"
        
        return EmailMessage(
            id=f"outlook_{msg_id}",
            folder_id=folder.id,
            folder_path=folder.path,
            subject=msg.get("subject", "No Subject"),
            from_address=from_addr,
            from_name=from_name,
            to_addresses=to_addrs,
            cc_addresses=cc_addrs,
            date=received_date,
            body_plain=body_plain,
            body_html=body_html,
            snippet=msg.get("bodyPreview", "")[:200],
            is_read=msg.get("isRead", False),
            is_starred=is_starred,
            has_attachments=msg.get("hasAttachments", False),
            attachments=[],  # Would need separate API call
            headers={
                "importance": msg.get("importance", "normal"),
            },
            thread_id=msg.get("conversationId"),
            size_bytes=0,  # Not provided in list response
            metadata={
                "outlook_id": msg_id,
                "web_link": msg.get("webLink"),
                "importance": msg.get("importance"),
            }
        )
    
    async def fetch_messages_delta(
        self,
        folder: EmailFolder
    ) -> AsyncIterator[EmailMessage]:
        """
        Fetch message changes using delta query.
        
        More efficient than full sync for incremental updates.
        
        Args:
            folder: Folder to sync
        
        Yields:
            Changed/new messages
        """
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to Outlook")
        
        try:
            # Use stored delta link if available
            delta_link = self._delta_links.get(folder.id)
            
            if delta_link:
                url = delta_link
            else:
                # Initial delta request
                url = f"{GRAPH_BASE_URL}/me/mailFolders/{folder.id}/messages/delta"
            
            while url:
                if self._cancel_requested:
                    return
                
                response = await self._client.get(url)
                
                if response.status_code != 200:
                    logger.error(f"Delta query error: {response.status_code}")
                    break
                
                data = response.json()
                messages = data.get("value", [])
                
                for msg_data in messages:
                    # Check for deleted messages
                    if "@removed" in msg_data:
                        # Handle deletion
                        continue
                    
                    message = self._parse_outlook_message(folder, msg_data)
                    yield message
                
                # Check for more pages or delta link
                if "@odata.nextLink" in data:
                    url = data["@odata.nextLink"]
                elif "@odata.deltaLink" in data:
                    # Store delta link for next sync
                    self._delta_links[folder.id] = data["@odata.deltaLink"]
                    break
                else:
                    break
        
        except Exception as e:
            logger.error(f"Delta sync error: {e}")
            raise
    
    async def get_attachments(
        self, 
        message_id: str
    ) -> List[EmailAttachment]:
        """Fetch attachments for a message."""
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to Outlook")
        
        attachments = []
        
        try:
            # Remove provider prefix if present
            if message_id.startswith("outlook_"):
                message_id = message_id[8:]
            
            response = await self._client.get(
                f"{GRAPH_BASE_URL}/me/messages/{message_id}/attachments"
            )
            
            if response.status_code == 200:
                data = response.json()
                
                for att in data.get("value", []):
                    attachment = EmailAttachment(
                        id=att.get("id", ""),
                        filename=att.get("name", "attachment"),
                        content_type=att.get("contentType", "application/octet-stream"),
                        size_bytes=att.get("size", 0),
                        content_id=att.get("contentId"),
                    )
                    attachments.append(attachment)
        
        except Exception as e:
            logger.warning(f"Error fetching attachments: {e}")
        
        return attachments
