"""
WebDAV Provider Implementation

Provides access to OwnCloud, Nextcloud, and other WebDAV-compatible
file servers. Supports password and app token authentication.
"""

import logging
import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import AsyncIterator, Optional, Any
from urllib.parse import urljoin, quote, unquote
import mimetypes

from backend.providers.base import (
    CloudSourceProvider,
    ProviderType,
    ProviderCapabilities,
    AuthType,
    ConnectionCredentials,
    RemoteFile,
    RemoteFolder,
    SyncDelta,
    AuthenticationError,
    RateLimitError,
    FileNotFoundError,
    PermissionDeniedError,
)
from backend.providers.registry import register_provider

logger = logging.getLogger(__name__)

# WebDAV namespace
DAV_NS = "{DAV:}"
OC_NS = "{http://owncloud.org/ns}"
NC_NS = "{http://nextcloud.org/ns}"


def parse_webdav_datetime(dt_str: str) -> Optional[datetime]:
    """Parse WebDAV datetime string."""
    if not dt_str:
        return None
    
    # Try RFC 2822 format (most common)
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(dt_str)
    except (ValueError, TypeError):
        pass
    
    # Try ISO format
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        pass
    
    return None


class WebDAVProvider(CloudSourceProvider):
    """
    WebDAV provider for OwnCloud, Nextcloud, and compatible servers.
    
    Features:
    - Password and app token authentication
    - Full file listing via PROPFIND
    - File downloads
    - Works with any WebDAV-compatible server
    """
    
    def __init__(self, credentials: Optional[ConnectionCredentials] = None):
        super().__init__(credentials)
        self._http_client = None
        self._base_url: Optional[str] = None
        self._auth: Optional[tuple[str, str]] = None
        self._webdav_path = "/remote.php/dav/files/"  # Default for OwnCloud/Nextcloud
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.OWNCLOUD
    
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type=self.provider_type,
            display_name="OwnCloud / Nextcloud",
            description="Connect to OwnCloud or Nextcloud via WebDAV",
            icon="owncloud",
            supported_auth_types=[AuthType.PASSWORD, AuthType.APP_TOKEN],
            supports_delta_sync=False,  # WebDAV doesn't have native delta support
            supports_webhooks=False,
            supports_file_streaming=True,
            documentation_url="https://doc.owncloud.com/",
        )
    
    async def _get_client(self):
        """Get or create HTTP client."""
        if self._http_client is None:
            import httpx
            self._http_client = httpx.AsyncClient(
                timeout=60.0,
                auth=self._auth,
                follow_redirects=True,
            )
        return self._http_client
    
    def _build_url(self, path: str) -> str:
        """Build full URL for a path."""
        if not self._base_url:
            raise AuthenticationError("Not configured - no base URL")
        
        # Encode path components
        encoded_path = "/".join(quote(p, safe="") for p in path.split("/") if p)
        return urljoin(self._base_url, f"{self._webdav_path}{encoded_path}")
    
    async def _propfind(
        self,
        path: str,
        depth: str = "1",
        properties: Optional[list[str]] = None
    ) -> list[dict]:
        """Execute PROPFIND request."""
        client = await self._get_client()
        url = self._build_url(path)
        
        # Build PROPFIND body
        if properties:
            props = "\n".join(f"<d:{p}/>" for p in properties)
            body = f"""<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
    <d:prop>
        {props}
    </d:prop>
</d:propfind>"""
        else:
            body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
    <d:allprop/>
</d:propfind>"""
        
        response = await client.request(
            "PROPFIND",
            url,
            content=body.encode(),
            headers={
                "Content-Type": "application/xml",
                "Depth": depth,
            }
        )
        
        if response.status_code == 401:
            raise AuthenticationError("Invalid credentials")
        elif response.status_code == 403:
            raise PermissionDeniedError("Access denied")
        elif response.status_code == 404:
            raise FileNotFoundError(f"Path not found: {path}")
        elif response.status_code not in (200, 207):  # 207 = Multi-Status
            raise Exception(f"PROPFIND failed with status {response.status_code}")
        
        # Parse XML response
        return self._parse_propfind_response(response.text, path)
    
    def _parse_propfind_response(self, xml_text: str, base_path: str) -> list[dict]:
        """Parse PROPFIND XML response."""
        results = []
        
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error(f"Failed to parse PROPFIND response: {e}")
            return []
        
        for response in root.findall(f".//{DAV_NS}response"):
            href_elem = response.find(f"{DAV_NS}href")
            if href_elem is None:
                continue
            
            href = unquote(href_elem.text or "")
            propstat = response.find(f"{DAV_NS}propstat")
            if propstat is None:
                continue
            
            prop = propstat.find(f"{DAV_NS}prop")
            if prop is None:
                continue
            
            # Extract properties
            item = {
                "href": href,
                "path": self._extract_path_from_href(href),
            }
            
            # Check if directory
            resourcetype = prop.find(f"{DAV_NS}resourcetype")
            if resourcetype is not None:
                item["is_collection"] = resourcetype.find(f"{DAV_NS}collection") is not None
            else:
                item["is_collection"] = href.endswith("/")
            
            # Display name
            displayname = prop.find(f"{DAV_NS}displayname")
            if displayname is not None and displayname.text:
                item["name"] = displayname.text
            else:
                # Extract name from path
                path_parts = [p for p in item["path"].rstrip("/").split("/") if p]
                item["name"] = path_parts[-1] if path_parts else "/"
            
            # Content length
            contentlength = prop.find(f"{DAV_NS}getcontentlength")
            if contentlength is not None and contentlength.text:
                item["size"] = int(contentlength.text)
            else:
                item["size"] = 0
            
            # Content type
            contenttype = prop.find(f"{DAV_NS}getcontenttype")
            if contenttype is not None and contenttype.text:
                item["mime_type"] = contenttype.text
            else:
                item["mime_type"] = "application/octet-stream"
            
            # Last modified
            lastmodified = prop.find(f"{DAV_NS}getlastmodified")
            if lastmodified is not None and lastmodified.text:
                item["modified_at"] = parse_webdav_datetime(lastmodified.text)
            
            # ETag
            etag = prop.find(f"{DAV_NS}getetag")
            if etag is not None and etag.text:
                item["etag"] = etag.text.strip('"')
            
            # OwnCloud/Nextcloud specific properties
            fileid = prop.find(f"{OC_NS}fileid")
            if fileid is not None and fileid.text:
                item["id"] = fileid.text
            else:
                # Use path as ID
                item["id"] = item["path"]
            
            results.append(item)
        
        return results
    
    def _extract_path_from_href(self, href: str) -> str:
        """Extract the file path from a WebDAV href."""
        # Remove the webdav prefix
        if self._webdav_path in href:
            path = href.split(self._webdav_path, 1)[-1]
            # Remove username from path if present
            parts = path.split("/", 1)
            if len(parts) > 1:
                return "/" + parts[1]
            return "/"
        return href
    
    # ==================== Authentication ====================
    
    async def authenticate(self, credentials: ConnectionCredentials) -> bool:
        """Authenticate using username/password or app token."""
        self.credentials = credentials
        
        if credentials.auth_type not in [AuthType.PASSWORD, AuthType.APP_TOKEN]:
            raise AuthenticationError("WebDAV requires password or app token authentication")
        
        if not credentials.server_url:
            raise AuthenticationError("Server URL is required")
        
        self._base_url = credentials.server_url.rstrip("/")
        
        # Set up authentication
        username = credentials.username
        password = credentials.password or credentials.app_token
        
        if not username or not password:
            raise AuthenticationError("Username and password/app token required")
        
        self._auth = (username, password)
        self._webdav_path = f"/remote.php/dav/files/{username}/"
        
        # Validate by making a PROPFIND request
        try:
            await self.validate_credentials()
            self._authenticated = True
            return True
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            self._authenticated = False
            raise AuthenticationError(f"Authentication failed: {e}")
    
    async def validate_credentials(self) -> bool:
        """Validate that credentials are still valid."""
        try:
            # Try to list root
            await self._propfind("/", depth="0")
            return True
        except AuthenticationError:
            return False
        except Exception as e:
            logger.warning(f"Credential validation failed: {e}")
            return False
    
    async def refresh_credentials(self) -> ConnectionCredentials:
        """WebDAV doesn't need token refresh - return existing credentials."""
        return self.credentials
    
    # ==================== Folder Operations ====================
    
    async def list_root_folders(self) -> list[RemoteFolder]:
        """List root folder."""
        return [RemoteFolder(
            id="/",
            name="Files",
            path="/",
            is_root=True,
            has_children=True,
        )]
    
    async def list_folder_contents(
        self,
        folder_id: str,
        include_files: bool = True,
        include_folders: bool = True
    ) -> tuple[list[RemoteFolder], list[RemoteFile]]:
        """List contents of a folder."""
        folders = []
        files = []
        
        # folder_id is the path
        path = folder_id if folder_id.startswith("/") else f"/{folder_id}"
        
        items = await self._propfind(path, depth="1")
        
        for item in items:
            # Skip the folder itself (first result is usually the requested folder)
            if item["path"].rstrip("/") == path.rstrip("/"):
                continue
            
            if item["is_collection"]:
                if include_folders:
                    folders.append(RemoteFolder(
                        id=item["path"],
                        name=item["name"],
                        path=item["path"],
                        parent_id=path,
                        has_children=True,
                        modified_at=item.get("modified_at"),
                        provider_metadata={"etag": item.get("etag")}
                    ))
            else:
                if include_files:
                    # Guess MIME type from extension if not provided
                    mime_type = item.get("mime_type", "application/octet-stream")
                    if mime_type == "application/octet-stream":
                        guessed = mimetypes.guess_type(item["name"])[0]
                        if guessed:
                            mime_type = guessed
                    
                    files.append(RemoteFile(
                        id=item.get("id", item["path"]),
                        name=item["name"],
                        path=item["path"],
                        mime_type=mime_type,
                        size_bytes=item.get("size", 0),
                        modified_at=item.get("modified_at") or datetime.utcnow(),
                        etag=item.get("etag"),
                        parent_id=path,
                        provider_metadata={"webdav_href": item["href"]}
                    ))
        
        return folders, files
    
    # ==================== File Operations ====================
    
    async def get_file_metadata(self, file_id: str) -> RemoteFile:
        """Get metadata for a single file."""
        path = file_id if file_id.startswith("/") else f"/{file_id}"
        
        items = await self._propfind(path, depth="0")
        
        if not items:
            raise FileNotFoundError(f"File not found: {path}")
        
        item = items[0]
        
        mime_type = item.get("mime_type", "application/octet-stream")
        if mime_type == "application/octet-stream":
            guessed = mimetypes.guess_type(item["name"])[0]
            if guessed:
                mime_type = guessed
        
        return RemoteFile(
            id=item.get("id", item["path"]),
            name=item["name"],
            path=item["path"],
            mime_type=mime_type,
            size_bytes=item.get("size", 0),
            modified_at=item.get("modified_at") or datetime.utcnow(),
            etag=item.get("etag"),
        )
    
    async def download_file(self, file_id: str) -> AsyncIterator[bytes]:
        """Download file content as a stream."""
        path = file_id if file_id.startswith("/") else f"/{file_id}"
        url = self._build_url(path)
        
        client = await self._get_client()
        
        async with client.stream("GET", url) as response:
            if response.status_code == 401:
                raise AuthenticationError("Authentication failed")
            elif response.status_code == 404:
                raise FileNotFoundError(f"File not found: {path}")
            elif response.status_code != 200:
                raise Exception(f"Download failed with status {response.status_code}")
            
            async for chunk in response.aiter_bytes(chunk_size=8192):
                yield chunk
    
    async def list_all_files(
        self,
        folder_id: str,
        recursive: bool = True,
        file_types: Optional[list[str]] = None
    ) -> AsyncIterator[RemoteFile]:
        """List all files in a folder (optionally recursive)."""
        folders, files = await self.list_folder_contents(
            folder_id,
            include_files=True,
            include_folders=recursive
        )
        
        # Yield files that match type filter
        for file in files:
            if file_types is None or self._matches_type_filter(file, file_types):
                yield file
        
        # Recursively process subfolders
        if recursive:
            for folder in folders:
                async for file in self.list_all_files(folder.id, recursive=True, file_types=file_types):
                    yield file
    
    def _matches_type_filter(self, file: RemoteFile, file_types: list[str]) -> bool:
        """Check if file matches the type filter."""
        ext = file.name.rsplit(".", 1)[-1].lower() if "." in file.name else ""
        
        for ft in file_types:
            if ft.lower() == ext:
                return True
            if ft.lower() in file.mime_type.lower():
                return True
        
        return False
    
    # ==================== Delta Sync ====================
    
    async def get_changes(
        self,
        delta_token: Optional[str] = None,
        folder_id: Optional[str] = None
    ) -> SyncDelta:
        """
        WebDAV doesn't have native delta support.
        
        For incremental sync, we rely on the sync engine to compare
        ETags and modification times from full listings.
        """
        raise NotImplementedError(
            "WebDAV doesn't support delta sync. Use full listing with ETag comparison."
        )
    
    # ==================== Utility Methods ====================
    
    async def get_storage_quota(self) -> dict[str, Any]:
        """Get storage quota information."""
        try:
            items = await self._propfind("/", depth="0", properties=[
                "quota-available-bytes",
                "quota-used-bytes",
            ])
            
            if items:
                item = items[0]
                used = item.get("quota-used-bytes", 0)
                available = item.get("quota-available-bytes", 0)
                total = used + available if available > 0 else 0
                
                return {
                    "used": used,
                    "total": total,
                    "remaining": available,
                }
        except Exception as e:
            logger.warning(f"Could not get quota: {e}")
        
        return {"used": 0, "total": 0, "remaining": 0}
    
    async def get_user_info(self) -> dict[str, Any]:
        """Get authenticated user information."""
        return {
            "email": self.credentials.username if self.credentials else None,
            "name": self.credentials.username if self.credentials else None,
        }
    
    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


# Register for both OwnCloud and Nextcloud
@register_provider(ProviderType.OWNCLOUD)
class OwnCloudProvider(WebDAVProvider):
    """OwnCloud provider."""
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.OWNCLOUD
    
    @property
    def capabilities(self) -> ProviderCapabilities:
        caps = super().capabilities
        caps.provider_type = ProviderType.OWNCLOUD
        caps.display_name = "OwnCloud"
        caps.description = "Connect to OwnCloud file storage"
        caps.icon = "owncloud"
        caps.documentation_url = "https://doc.owncloud.com/"
        return caps


@register_provider(ProviderType.NEXTCLOUD)
class NextcloudProvider(WebDAVProvider):
    """Nextcloud provider."""
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.NEXTCLOUD
    
    @property
    def capabilities(self) -> ProviderCapabilities:
        caps = super().capabilities
        caps.provider_type = ProviderType.NEXTCLOUD
        caps.display_name = "Nextcloud"
        caps.description = "Connect to Nextcloud file storage"
        caps.icon = "nextcloud"
        caps.documentation_url = "https://docs.nextcloud.com/"
        return caps
