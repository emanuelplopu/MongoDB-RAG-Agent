"""
Google Drive Provider Implementation

Provides access to Google Drive files and folders using the Google Drive API v3.
Supports OAuth 2.0 authentication, file listing, downloading, and delta sync
using the Changes API.
"""

import logging
import asyncio
from datetime import datetime
from typing import AsyncIterator, Optional, Any
import mimetypes

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

# Google Drive API constants
GOOGLE_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

# MIME types for Google Workspace documents that need export
GOOGLE_WORKSPACE_TYPES = {
    "application/vnd.google-apps.document": ("application/pdf", "pdf"),
    "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "application/vnd.google-apps.presentation": ("application/pdf", "pdf"),
    "application/vnd.google-apps.drawing": ("application/pdf", "pdf"),
}

# Fields to request from Drive API for files
FILE_FIELDS = "id,name,mimeType,size,modifiedTime,createdTime,md5Checksum,webViewLink,thumbnailLink,parents,trashed"
LIST_FIELDS = f"nextPageToken,files({FILE_FIELDS})"


@register_provider(ProviderType.GOOGLE_DRIVE)
class GoogleDriveProvider(CloudSourceProvider):
    """
    Google Drive provider using Drive API v3.
    
    Features:
    - OAuth 2.0 authentication
    - Full file listing with pagination
    - Delta sync using Changes API
    - Google Workspace document export
    - Streaming file downloads
    """
    
    def __init__(self, credentials: Optional[ConnectionCredentials] = None):
        super().__init__(credentials)
        self._http_client = None
        self._access_token: Optional[str] = None
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.GOOGLE_DRIVE
    
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type=ProviderType.GOOGLE_DRIVE,
            display_name="Google Drive",
            description="Connect to Google Drive to index documents, spreadsheets, and other files",
            icon="google-drive",
            supported_auth_types=[AuthType.OAUTH2],
            oauth_scopes=[
                "https://www.googleapis.com/auth/drive.readonly",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
            ],
            supports_delta_sync=True,
            supports_webhooks=True,
            supports_file_streaming=True,
            rate_limit_requests_per_minute=1000,
            documentation_url="https://developers.google.com/drive",
        )
    
    async def _get_client(self):
        """Get or create HTTP client."""
        if self._http_client is None:
            import httpx
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client
    
    async def _make_request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> dict:
        """Make authenticated request to Google Drive API."""
        if not self._access_token:
            raise AuthenticationError("Not authenticated")
        
        client = await self._get_client()
        
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token}"
        
        response = await client.request(method, url, headers=headers, **kwargs)
        
        if response.status_code == 401:
            raise AuthenticationError("Access token expired or invalid")
        elif response.status_code == 403:
            raise PermissionDeniedError("Access denied to resource")
        elif response.status_code == 404:
            raise FileNotFoundError("Resource not found")
        elif response.status_code == 429:
            retry_after = response.headers.get("Retry-After", 60)
            raise RateLimitError("Rate limit exceeded", retry_after=int(retry_after))
        elif response.status_code >= 400:
            error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            raise Exception(f"API error {response.status_code}: {error_data.get('error', {}).get('message', 'Unknown error')}")
        
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return {}
    
    # ==================== Authentication ====================
    
    async def authenticate(self, credentials: ConnectionCredentials) -> bool:
        """Authenticate using OAuth tokens."""
        self.credentials = credentials
        
        if credentials.auth_type != AuthType.OAUTH2:
            raise AuthenticationError("Google Drive requires OAuth 2.0 authentication")
        
        if not credentials.oauth_tokens:
            raise AuthenticationError("No OAuth tokens provided")
        
        self._access_token = credentials.oauth_tokens.access_token
        
        # Validate by making a simple API call
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
            result = await self._make_request(
                "GET",
                f"{GOOGLE_DRIVE_API_BASE}/about",
                params={"fields": "user"}
            )
            return "user" in result
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
        client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID")
        client_secret = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise AuthenticationError("Google OAuth client credentials not configured")
        
        client = await self._get_client()
        response = await client.post(
            GOOGLE_OAUTH_TOKEN_URL,
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
        
        # Update credentials with new tokens
        new_tokens = OAuthTokens(
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token", refresh_token),
            expires_at=datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600)),
            token_type=tokens.get("token_type", "Bearer"),
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
        """Generate Google OAuth authorization URL."""
        import os
        from urllib.parse import urlencode
        
        client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID")
        if not client_id:
            raise AuthenticationError("GOOGLE_DRIVE_CLIENT_ID not configured")
        
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
            "scope": " ".join(scopes or self.capabilities.oauth_scopes),
        }
        
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    
    async def exchange_oauth_code(
        self,
        code: str,
        redirect_uri: str
    ) -> ConnectionCredentials:
        """Exchange authorization code for tokens."""
        import os
        from datetime import timedelta
        
        client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID")
        client_secret = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET")
        
        if not client_id or not client_secret:
            raise AuthenticationError("Google OAuth client credentials not configured")
        
        client = await self._get_client()
        response = await client.post(
            GOOGLE_OAUTH_TOKEN_URL,
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
            expires_at=datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 3600)),
            token_type=tokens.get("token_type", "Bearer"),
            scope=tokens.get("scope"),
        )
        
        return ConnectionCredentials(
            auth_type=AuthType.OAUTH2,
            oauth_tokens=oauth_tokens,
        )
    
    # ==================== Folder Operations ====================
    
    async def list_root_folders(self) -> list[RemoteFolder]:
        """List root-level folders and shared drives."""
        folders = []
        
        # Get "My Drive" root
        folders.append(RemoteFolder(
            id="root",
            name="My Drive",
            path="/My Drive/",
            is_root=True,
            has_children=True,
        ))
        
        # Get shared drives
        try:
            result = await self._make_request(
                "GET",
                f"{GOOGLE_DRIVE_API_BASE}/drives",
                params={"pageSize": 100}
            )
            
            for drive in result.get("drives", []):
                folders.append(RemoteFolder(
                    id=drive["id"],
                    name=drive["name"],
                    path=f"/{drive['name']}/",
                    is_root=True,
                    has_children=True,
                    provider_metadata={"kind": "shared_drive"}
                ))
        except Exception as e:
            logger.warning(f"Could not list shared drives: {e}")
        
        # Get "Shared with me" as a virtual folder
        folders.append(RemoteFolder(
            id="sharedWithMe",
            name="Shared with me",
            path="/Shared with me/",
            is_root=True,
            has_children=True,
            provider_metadata={"kind": "virtual"}
        ))
        
        return folders
    
    async def list_folder_contents(
        self,
        folder_id: str,
        include_files: bool = True,
        include_folders: bool = True
    ) -> tuple[list[RemoteFolder], list[RemoteFile]]:
        """List contents of a folder."""
        folders = []
        files = []
        page_token = None
        
        # Build query
        if folder_id == "sharedWithMe":
            query = "sharedWithMe=true and trashed=false"
        else:
            query = f"'{folder_id}' in parents and trashed=false"
        
        # Add type filter
        if include_files and not include_folders:
            query += " and mimeType!='application/vnd.google-apps.folder'"
        elif include_folders and not include_files:
            query += " and mimeType='application/vnd.google-apps.folder'"
        
        while True:
            params = {
                "q": query,
                "fields": LIST_FIELDS,
                "pageSize": 100,
                "orderBy": "folder,name",
            }
            
            if page_token:
                params["pageToken"] = page_token
            
            # Handle shared drives
            if folder_id not in ["root", "sharedWithMe"]:
                params["supportsAllDrives"] = "true"
                params["includeItemsFromAllDrives"] = "true"
            
            result = await self._make_request(
                "GET",
                f"{GOOGLE_DRIVE_API_BASE}/files",
                params=params
            )
            
            for item in result.get("files", []):
                if item["mimeType"] == "application/vnd.google-apps.folder":
                    folders.append(self._parse_folder(item, folder_id))
                else:
                    files.append(self._parse_file(item))
            
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        
        return folders, files
    
    def _parse_folder(self, item: dict, parent_id: str) -> RemoteFolder:
        """Parse Drive API folder response to RemoteFolder."""
        return RemoteFolder(
            id=item["id"],
            name=item["name"],
            path=f"{item['name']}/",  # Path will be completed by parent
            parent_id=parent_id if parent_id != "root" else None,
            has_children=True,  # Assume folders may have children
            modified_at=datetime.fromisoformat(item["modifiedTime"].replace("Z", "+00:00")) if "modifiedTime" in item else None,
            provider_metadata={"mimeType": item["mimeType"]}
        )
    
    def _parse_file(self, item: dict) -> RemoteFile:
        """Parse Drive API file response to RemoteFile."""
        mime_type = item["mimeType"]
        size = int(item.get("size", 0))
        
        # Google Workspace files don't have size, estimate based on type
        if mime_type in GOOGLE_WORKSPACE_TYPES and size == 0:
            size = 1024 * 10  # Estimate 10KB
        
        return RemoteFile(
            id=item["id"],
            name=item["name"],
            path=item["name"],
            mime_type=mime_type,
            size_bytes=size,
            modified_at=datetime.fromisoformat(item["modifiedTime"].replace("Z", "+00:00")),
            created_at=datetime.fromisoformat(item["createdTime"].replace("Z", "+00:00")) if "createdTime" in item else None,
            checksum=item.get("md5Checksum"),
            web_view_url=item.get("webViewLink"),
            thumbnail_url=item.get("thumbnailLink"),
            parent_id=item.get("parents", [None])[0],
            provider_metadata={
                "mimeType": mime_type,
                "isGoogleWorkspace": mime_type in GOOGLE_WORKSPACE_TYPES,
            }
        )
    
    # ==================== File Operations ====================
    
    async def get_file_metadata(self, file_id: str) -> RemoteFile:
        """Get metadata for a single file."""
        result = await self._make_request(
            "GET",
            f"{GOOGLE_DRIVE_API_BASE}/files/{file_id}",
            params={"fields": FILE_FIELDS, "supportsAllDrives": "true"}
        )
        return self._parse_file(result)
    
    async def download_file(self, file_id: str) -> AsyncIterator[bytes]:
        """Download file content as a stream."""
        # First get file metadata to check if it's a Google Workspace file
        file_meta = await self.get_file_metadata(file_id)
        mime_type = file_meta.mime_type
        
        if mime_type in GOOGLE_WORKSPACE_TYPES:
            # Export Google Workspace files
            export_mime = GOOGLE_WORKSPACE_TYPES[mime_type][0]
            url = f"{GOOGLE_DRIVE_API_BASE}/files/{file_id}/export"
            params = {"mimeType": export_mime}
        else:
            # Download regular files
            url = f"{GOOGLE_DRIVE_API_BASE}/files/{file_id}"
            params = {"alt": "media", "supportsAllDrives": "true"}
        
        client = await self._get_client()
        
        async with client.stream(
            "GET",
            url,
            params=params,
            headers={"Authorization": f"Bearer {self._access_token}"}
        ) as response:
            if response.status_code != 200:
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
        # Get direct files
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
            if ft == ext:
                return True
            # Also check MIME type
            if ft in file.mime_type:
                return True
        
        return False
    
    # ==================== Delta Sync ====================
    
    async def get_changes(
        self,
        delta_token: Optional[str] = None,
        folder_id: Optional[str] = None
    ) -> SyncDelta:
        """Get changes since last sync using Changes API."""
        added = []
        modified = []
        deleted = []
        
        # Get start page token if we don't have one
        if not delta_token:
            result = await self._make_request(
                "GET",
                f"{GOOGLE_DRIVE_API_BASE}/changes/startPageToken",
                params={"supportsAllDrives": "true"}
            )
            delta_token = result["startPageToken"]
            
            # On first sync, return empty changes - caller should do full listing
            return SyncDelta(
                added=[],
                modified=[],
                deleted=[],
                next_delta_token=delta_token,
                has_more=False,
            )
        
        # Fetch changes
        page_token = delta_token
        next_delta_token = None
        
        while page_token:
            result = await self._make_request(
                "GET",
                f"{GOOGLE_DRIVE_API_BASE}/changes",
                params={
                    "pageToken": page_token,
                    "fields": f"nextPageToken,newStartPageToken,changes(fileId,removed,file({FILE_FIELDS}))",
                    "pageSize": 100,
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                }
            )
            
            for change in result.get("changes", []):
                file_id = change["fileId"]
                
                if change.get("removed") or change.get("file", {}).get("trashed"):
                    deleted.append(file_id)
                elif "file" in change:
                    file_data = change["file"]
                    if file_data["mimeType"] != "application/vnd.google-apps.folder":
                        file = self._parse_file(file_data)
                        # We can't easily distinguish added vs modified without tracking
                        # So we put everything in modified - the sync engine handles dedup
                        modified.append(file)
            
            page_token = result.get("nextPageToken")
            if "newStartPageToken" in result:
                next_delta_token = result["newStartPageToken"]
        
        return SyncDelta(
            added=added,
            modified=modified,
            deleted=deleted,
            next_delta_token=next_delta_token or delta_token,
            has_more=False,
        )
    
    # ==================== Utility Methods ====================
    
    async def get_storage_quota(self) -> dict[str, Any]:
        """Get storage quota information."""
        result = await self._make_request(
            "GET",
            f"{GOOGLE_DRIVE_API_BASE}/about",
            params={"fields": "storageQuota"}
        )
        
        quota = result.get("storageQuota", {})
        return {
            "used": int(quota.get("usage", 0)),
            "total": int(quota.get("limit", 0)),
            "remaining": int(quota.get("limit", 0)) - int(quota.get("usage", 0)),
        }
    
    async def get_user_info(self) -> dict[str, Any]:
        """Get authenticated user information."""
        result = await self._make_request(
            "GET",
            f"{GOOGLE_DRIVE_API_BASE}/about",
            params={"fields": "user"}
        )
        
        user = result.get("user", {})
        return {
            "email": user.get("emailAddress"),
            "name": user.get("displayName"),
            "photo_url": user.get("photoLink"),
        }
    
    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


# Import timedelta for token expiration
from datetime import timedelta
