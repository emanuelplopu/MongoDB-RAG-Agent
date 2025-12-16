"""
Dropbox Provider Implementation

Provides access to Dropbox files and folders using the Dropbox API v2.
Supports OAuth 2.0 authentication, file listing, downloading, and delta sync.
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import AsyncIterator, Optional, Any

from backend.providers.base import (
    CloudSourceProvider,
    ProviderType,
    ProviderCapabilities,
    AuthType,
    ConnectionCredentials,
    OAuthTokens,
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

# Dropbox API constants
DROPBOX_API_BASE = "https://api.dropboxapi.com/2"
DROPBOX_CONTENT_BASE = "https://content.dropboxapi.com/2"
DROPBOX_OAUTH_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"


@register_provider(ProviderType.DROPBOX)
class DropboxProvider(CloudSourceProvider):
    """
    Dropbox provider using Dropbox API v2.
    
    Features:
    - OAuth 2.0 authentication
    - Full file listing with cursor-based pagination
    - Delta sync using list_folder/continue
    - Streaming file downloads
    """
    
    def __init__(self, credentials: Optional[ConnectionCredentials] = None):
        super().__init__(credentials)
        self._http_client = None
        self._access_token: Optional[str] = None
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.DROPBOX
    
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type=ProviderType.DROPBOX,
            display_name="Dropbox",
            description="Connect to Dropbox personal or business accounts",
            icon="dropbox",
            supported_auth_types=[AuthType.OAUTH2],
            oauth_scopes=[],  # Dropbox uses app-level permissions
            supports_delta_sync=True,
            supports_webhooks=True,
            supports_file_streaming=True,
            rate_limit_requests_per_minute=600,
            documentation_url="https://www.dropbox.com/developers",
        )
    
    async def _get_client(self):
        """Get or create HTTP client."""
        if self._http_client is None:
            import httpx
            self._http_client = httpx.AsyncClient(timeout=60.0)
        return self._http_client
    
    async def _make_request(
        self,
        endpoint: str,
        data: Optional[dict] = None,
        content_endpoint: bool = False
    ) -> dict:
        """Make authenticated request to Dropbox API."""
        if not self._access_token:
            raise AuthenticationError("Not authenticated")
        
        client = await self._get_client()
        base = DROPBOX_CONTENT_BASE if content_endpoint else DROPBOX_API_BASE
        url = f"{base}/{endpoint}"
        
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        
        response = await client.post(url, json=data or {}, headers=headers)
        
        if response.status_code == 401:
            raise AuthenticationError("Access token expired or invalid")
        elif response.status_code == 403:
            raise PermissionDeniedError("Access denied to resource")
        elif response.status_code == 409:
            # Dropbox uses 409 for path errors
            error_data = response.json()
            error_tag = error_data.get("error", {}).get(".tag", "")
            if "not_found" in error_tag:
                raise FileNotFoundError("Resource not found")
            raise Exception(f"Dropbox error: {error_data}")
        elif response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            raise RateLimitError("Rate limit exceeded", retry_after=retry_after)
        elif response.status_code >= 400:
            error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            raise Exception(f"API error {response.status_code}: {error_data.get('error_summary', 'Unknown error')}")
        
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return {}
    
    # ==================== Authentication ====================
    
    async def authenticate(self, credentials: ConnectionCredentials) -> bool:
        """Authenticate using OAuth tokens."""
        self.credentials = credentials
        
        if credentials.auth_type != AuthType.OAUTH2:
            raise AuthenticationError("Dropbox requires OAuth 2.0 authentication")
        
        if not credentials.oauth_tokens:
            raise AuthenticationError("No OAuth tokens provided")
        
        self._access_token = credentials.oauth_tokens.access_token
        
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
            await self._make_request("users/get_current_account")
            return True
        except AuthenticationError:
            return False
        except Exception as e:
            logger.warning(f"Credential validation failed: {e}")
            return False
    
    async def refresh_credentials(self) -> ConnectionCredentials:
        """Refresh OAuth access token using refresh token."""
        if not self.credentials or not self.credentials.oauth_tokens:
            raise AuthenticationError("No credentials to refresh")
        
        refresh_token = self.credentials.oauth_tokens.refresh_token
        if not refresh_token:
            raise AuthenticationError("No refresh token available")
        
        import os
        client_id = os.environ.get("DROPBOX_CLIENT_ID")
        client_secret = os.environ.get("DROPBOX_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise AuthenticationError("Dropbox OAuth client credentials not configured")
        
        client = await self._get_client()
        response = await client.post(
            DROPBOX_OAUTH_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        )
        
        if response.status_code != 200:
            raise AuthenticationError("Token refresh failed")
        
        tokens = response.json()
        
        new_tokens = OAuthTokens(
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token", refresh_token),
            expires_at=datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 14400)),
            token_type=tokens.get("token_type", "bearer"),
        )
        
        self.credentials.oauth_tokens = new_tokens
        self._access_token = new_tokens.access_token
        
        return self.credentials
    
    # ==================== OAuth Flow ====================
    
    async def get_oauth_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        scopes: Optional[list[str]] = None
    ) -> str:
        """Generate Dropbox OAuth authorization URL."""
        import os
        from urllib.parse import urlencode
        
        client_id = os.environ.get("DROPBOX_CLIENT_ID")
        if not client_id:
            raise AuthenticationError("DROPBOX_CLIENT_ID not configured")
        
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "token_access_type": "offline",  # For refresh tokens
        }
        
        return f"https://www.dropbox.com/oauth2/authorize?{urlencode(params)}"
    
    async def exchange_oauth_code(
        self,
        code: str,
        redirect_uri: str
    ) -> ConnectionCredentials:
        """Exchange authorization code for tokens."""
        import os
        
        client_id = os.environ.get("DROPBOX_CLIENT_ID")
        client_secret = os.environ.get("DROPBOX_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise AuthenticationError("Dropbox OAuth client credentials not configured")
        
        client = await self._get_client()
        response = await client.post(
            DROPBOX_OAUTH_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
        )
        
        if response.status_code != 200:
            error = response.json().get("error_description", "Token exchange failed")
            raise AuthenticationError(error)
        
        tokens = response.json()
        
        oauth_tokens = OAuthTokens(
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            expires_at=datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 14400)),
            token_type=tokens.get("token_type", "bearer"),
        )
        
        return ConnectionCredentials(
            auth_type=AuthType.OAUTH2,
            oauth_tokens=oauth_tokens,
        )
    
    # ==================== Folder Operations ====================
    
    async def list_root_folders(self) -> list[RemoteFolder]:
        """List root folder."""
        return [RemoteFolder(
            id="",
            name="Dropbox",
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
        
        # Normalize path
        path = folder_id if folder_id else ""
        if path and not path.startswith("/"):
            path = "/" + path
        if path == "/":
            path = ""
        
        # Initial request
        result = await self._make_request("files/list_folder", {
            "path": path,
            "recursive": False,
            "include_deleted": False,
            "include_has_explicit_shared_members": False,
            "include_mounted_folders": True,
        })
        
        # Process all pages
        while True:
            for entry in result.get("entries", []):
                tag = entry.get(".tag")
                
                if tag == "folder":
                    if include_folders:
                        folders.append(self._parse_folder(entry))
                elif tag == "file":
                    if include_files:
                        files.append(self._parse_file(entry))
            
            if not result.get("has_more"):
                break
            
            # Get next page
            result = await self._make_request("files/list_folder/continue", {
                "cursor": result["cursor"]
            })
        
        return folders, files
    
    def _parse_folder(self, entry: dict) -> RemoteFolder:
        """Parse Dropbox folder entry."""
        path = entry.get("path_display", entry.get("path_lower", "/"))
        name = entry.get("name", path.split("/")[-1])
        
        return RemoteFolder(
            id=entry.get("id", path),
            name=name,
            path=path,
            parent_id=self._get_parent_path(path),
            has_children=True,
            provider_metadata={"tag": "folder"}
        )
    
    def _parse_file(self, entry: dict) -> RemoteFile:
        """Parse Dropbox file entry."""
        path = entry.get("path_display", entry.get("path_lower", "/"))
        name = entry.get("name", path.split("/")[-1])
        
        # Parse dates
        modified_at = datetime.utcnow()
        if "client_modified" in entry:
            try:
                modified_at = datetime.fromisoformat(entry["client_modified"].replace("Z", "+00:00"))
            except ValueError:
                pass
        
        # Determine MIME type
        import mimetypes
        mime_type, _ = mimetypes.guess_type(name)
        if not mime_type:
            mime_type = "application/octet-stream"
        
        return RemoteFile(
            id=entry.get("id", path),
            name=name,
            path=path,
            mime_type=mime_type,
            size_bytes=entry.get("size", 0),
            modified_at=modified_at,
            checksum=entry.get("content_hash"),
            parent_id=self._get_parent_path(path),
            version_id=entry.get("rev"),
            provider_metadata={
                "is_downloadable": entry.get("is_downloadable", True),
            }
        )
    
    def _get_parent_path(self, path: str) -> Optional[str]:
        """Get parent path from a path."""
        if not path or path == "/":
            return None
        parts = path.rstrip("/").rsplit("/", 1)
        if len(parts) > 1:
            return parts[0] or "/"
        return None
    
    # ==================== File Operations ====================
    
    async def get_file_metadata(self, file_id: str) -> RemoteFile:
        """Get metadata for a single file."""
        # file_id can be path or ID
        path = file_id if file_id.startswith("/") else f"id:{file_id}"
        
        result = await self._make_request("files/get_metadata", {
            "path": path,
            "include_has_explicit_shared_members": False,
        })
        
        return self._parse_file(result)
    
    async def download_file(self, file_id: str) -> AsyncIterator[bytes]:
        """Download file content as a stream."""
        if not self._access_token:
            raise AuthenticationError("Not authenticated")
        
        path = file_id if file_id.startswith("/") else f"id:{file_id}"
        
        client = await self._get_client()
        
        import json
        async with client.stream(
            "POST",
            f"{DROPBOX_CONTENT_BASE}/files/download",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Dropbox-API-Arg": json.dumps({"path": path}),
            }
        ) as response:
            if response.status_code == 409:
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
        path = folder_id if folder_id else ""
        if path and not path.startswith("/"):
            path = "/" + path
        if path == "/":
            path = ""
        
        # Use recursive listing if requested
        result = await self._make_request("files/list_folder", {
            "path": path,
            "recursive": recursive,
            "include_deleted": False,
        })
        
        while True:
            for entry in result.get("entries", []):
                if entry.get(".tag") == "file":
                    file = self._parse_file(entry)
                    if file_types is None or self._matches_type_filter(file, file_types):
                        yield file
            
            if not result.get("has_more"):
                break
            
            result = await self._make_request("files/list_folder/continue", {
                "cursor": result["cursor"]
            })
    
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
        """Get changes since last sync using cursor-based pagination."""
        added = []
        modified = []
        deleted = []
        
        if not delta_token:
            # Get initial cursor
            path = folder_id if folder_id else ""
            if path and not path.startswith("/"):
                path = "/" + path
            if path == "/":
                path = ""
            
            result = await self._make_request("files/list_folder/get_latest_cursor", {
                "path": path,
                "recursive": True,
                "include_deleted": True,
            })
            
            return SyncDelta(
                added=[],
                modified=[],
                deleted=[],
                next_delta_token=result["cursor"],
                has_more=False,
            )
        
        # Get changes since cursor
        try:
            result = await self._make_request("files/list_folder/continue", {
                "cursor": delta_token
            })
        except Exception as e:
            # Cursor might be expired, need to do full sync
            logger.warning(f"Cursor expired, need full sync: {e}")
            raise
        
        while True:
            for entry in result.get("entries", []):
                tag = entry.get(".tag")
                
                if tag == "deleted":
                    deleted.append(entry.get("id", entry.get("path_lower", "")))
                elif tag == "file":
                    file = self._parse_file(entry)
                    modified.append(file)
            
            if not result.get("has_more"):
                break
            
            result = await self._make_request("files/list_folder/continue", {
                "cursor": result["cursor"]
            })
        
        return SyncDelta(
            added=added,
            modified=modified,
            deleted=deleted,
            next_delta_token=result.get("cursor", delta_token),
            has_more=False,
        )
    
    # ==================== Utility Methods ====================
    
    async def get_storage_quota(self) -> dict[str, Any]:
        """Get storage quota information."""
        result = await self._make_request("users/get_space_usage")
        
        used = result.get("used", 0)
        allocation = result.get("allocation", {})
        
        if allocation.get(".tag") == "individual":
            total = allocation.get("allocated", 0)
        elif allocation.get(".tag") == "team":
            total = allocation.get("allocated", 0)
        else:
            total = 0
        
        return {
            "used": used,
            "total": total,
            "remaining": max(0, total - used),
        }
    
    async def get_user_info(self) -> dict[str, Any]:
        """Get authenticated user information."""
        result = await self._make_request("users/get_current_account")
        
        return {
            "email": result.get("email"),
            "name": result.get("name", {}).get("display_name"),
            "account_id": result.get("account_id"),
        }
    
    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
