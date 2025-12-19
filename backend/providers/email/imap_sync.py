"""
Direct IMAP Email Synchronization

Provides direct IMAP sync without Airbyte dependency.

Features:
- Deep folder enumeration (unlimited depth)
- Large-scale support (1M+ emails)
- Batched fetching with streaming
- Checkpoint/resume capability
- Non-destructive read-only sync
- Connection pooling and retry logic
"""

import asyncio
import imaplib
import email
import email.utils
import email.header
import logging
import re
import ssl
from datetime import datetime
from typing import Optional, List, Dict, Any, AsyncIterator, Tuple
from email.message import Message as EmailMessageObj

try:
    import aioimaplib
    AIOIMAPLIB_AVAILABLE = True
except ImportError:
    AIOIMAPLIB_AVAILABLE = False
    aioimaplib = None

from backend.providers.email.base import (
    BaseEmailSync,
    EmailSyncConfig,
    EmailFolder,
    EmailMessage,
    EmailAttachment,
)

logger = logging.getLogger(__name__)


class DirectImapSync(BaseEmailSync):
    """
    Direct IMAP synchronization implementation.
    
    Supports any IMAP-compliant email server.
    Uses aioimaplib for async operations.
    """
    
    def __init__(self, config: EmailSyncConfig):
        super().__init__(config)
        self._client: Optional[aioimaplib.IMAP4_SSL] = None
        self._connected: bool = False
    
    def _get_provider_type(self) -> str:
        return "imap"
    
    async def connect(self) -> bool:
        """Connect to IMAP server."""
        try:
            if self.config.use_ssl:
                ssl_context = ssl.create_default_context()
                self._client = aioimaplib.IMAP4_SSL(
                    host=self.config.host,
                    port=self.config.port,
                    ssl_context=ssl_context,
                    timeout=self.config.timeout_seconds
                )
            else:
                self._client = aioimaplib.IMAP4(
                    host=self.config.host,
                    port=self.config.port,
                    timeout=self.config.timeout_seconds
                )
            
            await self._client.wait_hello_from_server()
            
            # Login
            response = await self._client.login(
                self.config.username,
                self.config.password
            )
            
            if response.result != "OK":
                logger.error(f"IMAP login failed: {response}")
                return False
            
            self._connected = True
            logger.info(f"Connected to IMAP server: {self.config.host}")
            return True
            
        except Exception as e:
            logger.error(f"IMAP connection error: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from IMAP server."""
        if self._client and self._connected:
            try:
                await self._client.logout()
            except Exception as e:
                logger.warning(f"Error during logout: {e}")
            finally:
                self._connected = False
                self._client = None
    
    async def enumerate_folders(
        self, 
        max_depth: int = 20
    ) -> List[EmailFolder]:
        """
        Enumerate all IMAP folders recursively.
        
        Uses LIST command to discover all mailboxes.
        Handles nested folders with various separators (/, ., etc.)
        
        Args:
            max_depth: Maximum folder nesting depth (default 20)
        
        Returns:
            Flat list of all folders with hierarchy info
        """
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to IMAP server")
        
        folders: List[EmailFolder] = []
        folder_map: Dict[str, EmailFolder] = {}
        
        try:
            # Get list of all folders
            # Pattern "*" lists all folders, "%" lists only top-level
            response = await self._client.list('""', "*")
            
            if response.result != "OK":
                logger.error(f"LIST command failed: {response}")
                return folders
            
            # Parse folder list
            # Response lines like: (\HasNoChildren) "/" "INBOX"
            folder_pattern = re.compile(
                r'\((?P<flags>[^)]*)\)\s+"(?P<delimiter>[^"]+)"\s+"?(?P<name>[^"]+)"?'
            )
            
            for line in response.lines:
                if not line:
                    continue
                
                line_str = line.decode() if isinstance(line, bytes) else str(line)
                
                match = folder_pattern.match(line_str)
                if not match:
                    # Try alternative format
                    parts = line_str.split(' ', 2)
                    if len(parts) >= 3:
                        flags_str = parts[0].strip('()')
                        delimiter = parts[1].strip('"')
                        name = parts[2].strip('"')
                    else:
                        continue
                else:
                    flags_str = match.group("flags")
                    delimiter = match.group("delimiter")
                    name = match.group("name")
                
                # Parse flags
                flags = [f.strip() for f in flags_str.split() if f.strip()]
                
                # Skip non-selectable folders
                if "\\Noselect" in flags:
                    continue
                
                # Calculate depth
                depth = name.count(delimiter)
                if depth > max_depth:
                    continue
                
                # Determine parent
                parent_id = None
                if delimiter in name:
                    parent_path = delimiter.join(name.split(delimiter)[:-1])
                    parent_id = parent_path
                
                # Create folder object
                folder = EmailFolder(
                    id=name,
                    name=name.split(delimiter)[-1],
                    path=name.replace(delimiter, "/"),
                    parent_id=parent_id,
                    depth=depth,
                    flags=flags,
                    metadata={
                        "delimiter": delimiter,
                        "raw_name": name,
                    }
                )
                
                folders.append(folder)
                folder_map[name] = folder
            
            # Build hierarchy
            for folder in folders:
                if folder.parent_id and folder.parent_id in folder_map:
                    folder_map[folder.parent_id].children.append(folder)
            
            logger.info(f"Enumerated {len(folders)} folders (max depth: {max(f.depth for f in folders) if folders else 0})")
            return folders
            
        except Exception as e:
            logger.error(f"Error enumerating folders: {e}")
            raise
    
    async def get_folder_message_count(self, folder: EmailFolder) -> int:
        """Get total message count in a folder."""
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to IMAP server")
        
        try:
            # Select folder in read-only mode
            raw_name = folder.metadata.get("raw_name", folder.id)
            response = await self._client.select(raw_name, readonly=True)
            
            if response.result != "OK":
                logger.warning(f"Could not select folder {folder.path}: {response}")
                return 0
            
            # Parse EXISTS response
            for line in response.lines:
                line_str = line.decode() if isinstance(line, bytes) else str(line)
                if "EXISTS" in line_str:
                    match = re.search(r'(\d+)\s+EXISTS', line_str)
                    if match:
                        return int(match.group(1))
            
            return 0
            
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
        Fetch messages from a folder with pagination.
        
        Uses UID FETCH for efficient incremental sync.
        Streams messages to avoid memory issues with large folders.
        
        Args:
            folder: Folder to fetch from
            offset: Starting offset (message sequence number)
            batch_size: Number of messages per batch
            since_uid: Only fetch messages with UID > this value
        
        Yields:
            EmailMessage objects
        """
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to IMAP server")
        
        try:
            # Select folder
            raw_name = folder.metadata.get("raw_name", folder.id)
            response = await self._client.select(
                raw_name, 
                readonly=self.config.read_only
            )
            
            if response.result != "OK":
                logger.error(f"Could not select folder {folder.path}")
                return
            
            # Get total messages
            total_messages = 0
            uid_validity = 0
            for line in response.lines:
                line_str = line.decode() if isinstance(line, bytes) else str(line)
                if "EXISTS" in line_str:
                    match = re.search(r'(\d+)\s+EXISTS', line_str)
                    if match:
                        total_messages = int(match.group(1))
                elif "UIDVALIDITY" in line_str:
                    match = re.search(r'UIDVALIDITY\s+(\d+)', line_str)
                    if match:
                        uid_validity = int(match.group(1))
            
            if total_messages == 0:
                logger.info(f"Folder {folder.path} is empty")
                return
            
            logger.info(f"Fetching from {folder.path}: {total_messages} messages")
            
            # Determine which messages to fetch
            if since_uid > 0:
                # Fetch by UID range
                search_criteria = f"UID {since_uid + 1}:*"
                response = await self._client.uid("search", None, search_criteria)
            else:
                # Fetch all messages using sequence numbers
                # Start from offset + 1 (IMAP is 1-indexed)
                start_seq = offset + 1
                end_seq = total_messages
                
                if start_seq > end_seq:
                    return
                
                # Process in batches
                current_start = start_seq
                
                while current_start <= end_seq:
                    current_end = min(current_start + batch_size - 1, end_seq)
                    seq_range = f"{current_start}:{current_end}"
                    
                    logger.debug(f"Fetching batch: {seq_range}")
                    
                    # Fetch batch
                    async for message in self._fetch_batch(
                        folder, 
                        seq_range, 
                        use_uid=False
                    ):
                        yield message
                    
                    current_start = current_end + 1
                    
                    # Allow event loop to process other tasks
                    await asyncio.sleep(0)
            
        except Exception as e:
            logger.error(f"Error fetching messages from {folder.path}: {e}")
            raise
    
    async def _fetch_batch(
        self,
        folder: EmailFolder,
        msg_range: str,
        use_uid: bool = False
    ) -> AsyncIterator[EmailMessage]:
        """
        Fetch a batch of messages.
        
        Args:
            folder: Source folder
            msg_range: Message range (e.g., "1:100" or "123,456,789")
            use_uid: Whether to use UID FETCH instead of FETCH
        
        Yields:
            EmailMessage objects
        """
        # Build fetch command
        # Use BODY.PEEK to avoid marking as read
        fetch_items = "(FLAGS UID ENVELOPE RFC822.SIZE"
        if self.config.peek_mode:
            fetch_items += " BODY.PEEK[])"
        else:
            fetch_items += " BODY[])"
        
        try:
            if use_uid:
                response = await self._client.uid("fetch", msg_range, fetch_items)
            else:
                response = await self._client.fetch(msg_range, fetch_items)
            
            if response.result != "OK":
                logger.warning(f"Fetch failed for {msg_range}: {response}")
                return
            
            # Parse responses
            current_data: Dict[str, Any] = {}
            
            for line in response.lines:
                if isinstance(line, bytes):
                    # This is message content
                    if current_data.get("expecting_body"):
                        current_data["body_bytes"] = line
                        # Parse and yield message
                        try:
                            message = self._parse_message(folder, current_data)
                            if message:
                                yield message
                        except Exception as e:
                            logger.warning(f"Error parsing message: {e}")
                        current_data = {}
                    continue
                
                line_str = str(line)
                
                # Parse FETCH response
                if "FETCH" in line_str:
                    current_data = self._parse_fetch_response(line_str)
                    if "BODY[]" in line_str or "BODY.PEEK[]" in line_str:
                        current_data["expecting_body"] = True
        
        except Exception as e:
            logger.error(f"Error in _fetch_batch: {e}")
            raise
    
    def _parse_fetch_response(self, line: str) -> Dict[str, Any]:
        """Parse FETCH response metadata."""
        data: Dict[str, Any] = {}
        
        # Extract UID
        uid_match = re.search(r'UID\s+(\d+)', line)
        if uid_match:
            data["uid"] = int(uid_match.group(1))
        
        # Extract FLAGS
        flags_match = re.search(r'FLAGS\s+\(([^)]*)\)', line)
        if flags_match:
            data["flags"] = flags_match.group(1).split()
        
        # Extract size
        size_match = re.search(r'RFC822\.SIZE\s+(\d+)', line)
        if size_match:
            data["size"] = int(size_match.group(1))
        
        # Extract sequence number
        seq_match = re.match(r'(\d+)\s+FETCH', line)
        if seq_match:
            data["seq"] = int(seq_match.group(1))
        
        return data
    
    def _parse_message(
        self, 
        folder: EmailFolder, 
        data: Dict[str, Any]
    ) -> Optional[EmailMessage]:
        """Parse raw message data into EmailMessage."""
        if "body_bytes" not in data:
            return None
        
        try:
            # Parse email
            msg_bytes = data["body_bytes"]
            msg = email.message_from_bytes(msg_bytes)
            
            uid = data.get("uid", data.get("seq", 0))
            flags = data.get("flags", [])
            
            # Extract headers
            subject = self._decode_header(msg.get("Subject", "No Subject"))
            from_header = self._decode_header(msg.get("From", ""))
            to_header = self._decode_header(msg.get("To", ""))
            cc_header = self._decode_header(msg.get("Cc", ""))
            date_header = msg.get("Date", "")
            message_id = msg.get("Message-ID", "")
            in_reply_to = msg.get("In-Reply-To", "")
            references = msg.get("References", "").split()
            
            # Parse from address
            from_name, from_addr = email.utils.parseaddr(from_header)
            
            # Parse recipients
            to_addrs = [addr for _, addr in email.utils.getaddresses([to_header])]
            cc_addrs = [addr for _, addr in email.utils.getaddresses([cc_header])]
            
            # Parse date
            parsed_date = None
            if date_header:
                try:
                    parsed_date = email.utils.parsedate_to_datetime(date_header)
                except Exception:
                    pass
            
            # Extract body
            body_plain, body_html = self._extract_body(msg)
            
            # Generate snippet
            snippet = body_plain[:200] if body_plain else ""
            
            # Check for attachments
            attachments = self._extract_attachments(msg)
            
            return EmailMessage(
                id=f"imap_{folder.id}_{uid}",
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
                flags=flags,
                is_read="\\Seen" in flags,
                is_starred="\\Flagged" in flags,
                has_attachments=len(attachments) > 0,
                attachments=attachments,
                headers={
                    "message_id": message_id,
                    "in_reply_to": in_reply_to,
                },
                in_reply_to=in_reply_to,
                references=references,
                size_bytes=data.get("size", len(msg_bytes)),
                metadata={
                    "uid": uid,
                    "uid_validity": data.get("uid_validity", 0),
                }
            )
            
        except Exception as e:
            logger.warning(f"Error parsing message: {e}")
            return None
    
    def _decode_header(self, header: str) -> str:
        """Decode MIME-encoded header."""
        if not header:
            return ""
        
        try:
            decoded_parts = email.header.decode_header(header)
            parts = []
            for content, charset in decoded_parts:
                if isinstance(content, bytes):
                    if charset:
                        try:
                            parts.append(content.decode(charset))
                        except Exception:
                            parts.append(content.decode("utf-8", errors="replace"))
                    else:
                        parts.append(content.decode("utf-8", errors="replace"))
                else:
                    parts.append(str(content))
            return " ".join(parts)
        except Exception:
            return str(header)
    
    def _extract_body(self, msg: EmailMessageObj) -> Tuple[str, str]:
        """Extract plain text and HTML body from message."""
        body_plain = ""
        body_html = ""
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))
                
                # Skip attachments
                if "attachment" in content_disposition:
                    continue
                
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        text = payload.decode(charset, errors="replace")
                        
                        if content_type == "text/plain" and not body_plain:
                            body_plain = text
                        elif content_type == "text/html" and not body_html:
                            body_html = text
                except Exception:
                    continue
        else:
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                    
                    if msg.get_content_type() == "text/html":
                        body_html = text
                    else:
                        body_plain = text
            except Exception:
                pass
        
        return body_plain, body_html
    
    def _extract_attachments(
        self, 
        msg: EmailMessageObj,
        include_content: bool = False
    ) -> List[EmailAttachment]:
        """Extract attachments from message."""
        attachments = []
        
        if not msg.is_multipart():
            return attachments
        
        for part in msg.walk():
            content_disposition = str(part.get("Content-Disposition", ""))
            
            if "attachment" in content_disposition or part.get_filename():
                filename = part.get_filename()
                if filename:
                    filename = self._decode_header(filename)
                else:
                    filename = "attachment"
                
                content_type = part.get_content_type()
                
                # Get size
                payload = part.get_payload(decode=True)
                size = len(payload) if payload else 0
                
                # Check size limit
                max_size = self.config.max_attachment_size_mb * 1024 * 1024
                
                attachment = EmailAttachment(
                    id=f"att_{hash(filename)}",
                    filename=filename,
                    content_type=content_type,
                    size_bytes=size,
                    content_id=part.get("Content-ID"),
                    content=payload if include_content and size <= max_size else None
                )
                attachments.append(attachment)
        
        return attachments


# Synchronous wrapper for non-async contexts
class SyncImapSync:
    """Synchronous wrapper for DirectImapSync."""
    
    def __init__(self, config: EmailSyncConfig):
        self._async_sync = DirectImapSync(config)
    
    def full_sync(self, **kwargs):
        """Run full sync synchronously."""
        return asyncio.run(self._async_sync.full_sync(**kwargs))
    
    def enumerate_folders(self, max_depth: int = 20):
        """Enumerate folders synchronously."""
        async def _enum():
            await self._async_sync.connect()
            try:
                return await self._async_sync.enumerate_folders(max_depth)
            finally:
                await self._async_sync.disconnect()
        
        return asyncio.run(_enum())
