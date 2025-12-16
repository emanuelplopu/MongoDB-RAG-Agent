"""
Confluence Provider via Airbyte

Provides Confluence Cloud/Server integration using Airbyte's
Confluence connector. Syncs pages, blog posts, and attachments.
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
from backend.providers.airbyte.base import AirbyteProvider
from backend.providers.registry import register_provider

logger = logging.getLogger(__name__)


@register_provider(ProviderType.CONFLUENCE)
class ConfluenceProvider(AirbyteProvider):
    """
    Confluence integration via Airbyte.
    
    Supports both Confluence Cloud (OAuth 2.0 / API Token) and
    Confluence Server/Data Center (Personal Access Token).
    
    Syncs:
    - Pages (with content)
    - Blog posts
    - Attachments
    - Comments (optional)
    """
    
    # Airbyte Confluence source definition ID
    CONFLUENCE_SOURCE_DEFINITION_ID = "d67e91a1-a6e6-476e-8596-9e16931fb7d3"
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.CONFLUENCE
    
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type=ProviderType.CONFLUENCE,
            display_name="Confluence",
            description="Atlassian Confluence wiki and knowledge base",
            icon="confluence",
            supported_auth_types=[AuthType.OAUTH2, AuthType.API_KEY],
            oauth_scopes=[
                "read:confluence-content.all",
                "read:confluence-space.summary",
                "read:confluence-props",
            ],
            supports_delta_sync=True,
            supports_webhooks=False,
            supports_file_streaming=False,
            supports_folders=True,  # Spaces
            supports_files=True,    # Pages
            supports_attachments=True,
            rate_limit_requests_per_minute=100,
            documentation_url="https://support.atlassian.com/confluence-cloud/",
            setup_instructions=(
                "1. Go to Atlassian Account Settings\n"
                "2. Create an API token or set up OAuth 2.0\n"
                "3. Enter your site URL and credentials"
            ),
        )
    
    @property
    def source_definition_id(self) -> str:
        return self.CONFLUENCE_SOURCE_DEFINITION_ID
    
    @property
    def source_display_name(self) -> str:
        return "Confluence"
    
    def build_source_config(
        self, 
        credentials: ConnectionCredentials
    ) -> dict[str, Any]:
        """
        Build Airbyte Confluence source configuration.
        
        Confluence Cloud requires:
        - domain: your-domain.atlassian.net
        - email: your-email@example.com
        - api_token: API token from Atlassian account
        
        Or OAuth 2.0:
        - domain: your-domain.atlassian.net
        - cloud_id: OAuth cloud ID
        - access_token: OAuth access token
        - refresh_token: OAuth refresh token
        """
        config: dict[str, Any] = {}
        
        # Extract domain from server URL
        if credentials.server_url:
            domain = credentials.server_url.replace("https://", "").replace("http://", "").rstrip("/")
            config["domain"] = domain
        
        if credentials.auth_type == AuthType.API_KEY or credentials.api_key:
            # API Token authentication
            config["email"] = credentials.username or credentials.extra.get("email", "")
            config["api_token"] = credentials.api_key
        elif credentials.auth_type == AuthType.OAUTH2 and credentials.oauth_tokens:
            # OAuth 2.0 authentication
            config["credentials"] = {
                "auth_type": "oauth2.0",
                "access_token": credentials.oauth_tokens.access_token,
                "refresh_token": credentials.oauth_tokens.refresh_token,
            }
            if credentials.extra.get("cloud_id"):
                config["cloud_id"] = credentials.extra["cloud_id"]
        
        return config
    
    def get_default_streams(self) -> list[str]:
        """Return default Confluence streams to sync."""
        return [
            "pages",
            "blog_posts", 
            "spaces",
            "page_attachments",
        ]
    
    def transform_record(
        self, 
        stream_name: str, 
        record: dict[str, Any]
    ) -> Optional[RemoteFile]:
        """Transform Confluence records to RemoteFile format."""
        
        if stream_name == "pages":
            return self._transform_page(record)
        elif stream_name == "blog_posts":
            return self._transform_blog_post(record)
        elif stream_name == "page_attachments":
            return self._transform_attachment(record)
        
        return None
    
    def _transform_page(self, record: dict[str, Any]) -> RemoteFile:
        """Transform a Confluence page record."""
        page_id = record.get("id", "")
        title = record.get("title", "Untitled")
        space_key = record.get("space", {}).get("key", "")
        
        # Build path from space and ancestor hierarchy
        ancestors = record.get("ancestors", [])
        path_parts = [space_key] + [a.get("title", "") for a in ancestors] + [title]
        path = "/" + "/".join(filter(None, path_parts))
        
        # Get content - could be in body.storage.value or body.view.value
        body = record.get("body", {})
        content = body.get("storage", {}).get("value", "") or body.get("view", {}).get("value", "")
        
        # Parse dates
        created_str = record.get("history", {}).get("createdDate")
        modified_str = record.get("version", {}).get("when") or created_str
        
        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00")) if created_str else None
        modified_at = datetime.fromisoformat(modified_str.replace("Z", "+00:00")) if modified_str else datetime.now()
        
        return RemoteFile(
            id=f"confluence_page_{page_id}",
            name=f"{title}.html",
            path=path,
            mime_type="text/html",
            size_bytes=len(content.encode("utf-8")) if content else 0,
            modified_at=modified_at,
            created_at=created_at,
            web_view_url=record.get("_links", {}).get("webui"),
            version_id=str(record.get("version", {}).get("number", 1)),
            provider_metadata={
                "type": "page",
                "space_key": space_key,
                "confluence_id": page_id,
                "content": content,
                "labels": [l.get("name") for l in record.get("metadata", {}).get("labels", {}).get("results", [])],
            }
        )
    
    def _transform_blog_post(self, record: dict[str, Any]) -> RemoteFile:
        """Transform a Confluence blog post record."""
        post_id = record.get("id", "")
        title = record.get("title", "Untitled")
        space_key = record.get("space", {}).get("key", "")
        
        path = f"/{space_key}/blog/{title}"
        
        body = record.get("body", {})
        content = body.get("storage", {}).get("value", "") or body.get("view", {}).get("value", "")
        
        created_str = record.get("history", {}).get("createdDate")
        modified_str = record.get("version", {}).get("when") or created_str
        
        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00")) if created_str else None
        modified_at = datetime.fromisoformat(modified_str.replace("Z", "+00:00")) if modified_str else datetime.now()
        
        return RemoteFile(
            id=f"confluence_blog_{post_id}",
            name=f"{title}.html",
            path=path,
            mime_type="text/html",
            size_bytes=len(content.encode("utf-8")) if content else 0,
            modified_at=modified_at,
            created_at=created_at,
            web_view_url=record.get("_links", {}).get("webui"),
            version_id=str(record.get("version", {}).get("number", 1)),
            provider_metadata={
                "type": "blog_post",
                "space_key": space_key,
                "confluence_id": post_id,
                "content": content,
                "labels": [l.get("name") for l in record.get("metadata", {}).get("labels", {}).get("results", [])],
            }
        )
    
    def _transform_attachment(self, record: dict[str, Any]) -> RemoteFile:
        """Transform a Confluence attachment record."""
        att_id = record.get("id", "")
        title = record.get("title", "unknown")
        media_type = record.get("mediaType", "application/octet-stream")
        
        # Get parent page info
        container = record.get("container", {})
        page_title = container.get("title", "")
        space_key = record.get("space", {}).get("key", "")
        
        path = f"/{space_key}/{page_title}/attachments/{title}"
        
        created_str = record.get("history", {}).get("createdDate")
        modified_str = record.get("version", {}).get("when") or created_str
        
        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00")) if created_str else None
        modified_at = datetime.fromisoformat(modified_str.replace("Z", "+00:00")) if modified_str else datetime.now()
        
        return RemoteFile(
            id=f"confluence_attachment_{att_id}",
            name=title,
            path=path,
            mime_type=media_type,
            size_bytes=record.get("extensions", {}).get("fileSize", 0),
            modified_at=modified_at,
            created_at=created_at,
            download_url=record.get("_links", {}).get("download"),
            version_id=str(record.get("version", {}).get("number", 1)),
            provider_metadata={
                "type": "attachment",
                "space_key": space_key,
                "confluence_id": att_id,
                "parent_page_id": container.get("id"),
                "parent_page_title": page_title,
            }
        )
    
    # ==================== Browsing Methods ====================
    
    async def list_root_folders(self) -> list[RemoteFolder]:
        """List Confluence spaces as root folders."""
        # This would query MongoDB for synced spaces
        # For now, return empty - will be populated after sync
        logger.info("Listing Confluence spaces (requires sync to be completed)")
        return []
    
    async def list_folder_contents(
        self,
        folder_id: str,
        include_files: bool = True,
        include_folders: bool = True
    ) -> tuple[list[RemoteFolder], list[RemoteFile]]:
        """List pages in a Confluence space."""
        # Query MongoDB for synced content in this space
        logger.info(f"Listing contents of space: {folder_id}")
        return [], []
    
    async def get_file_metadata(self, file_id: str) -> RemoteFile:
        """Get metadata for a Confluence page or attachment."""
        # Query MongoDB for the specific item
        raise NotImplementedError("Query MongoDB for synced content")
    
    async def download_file(self, file_id: str) -> AsyncIterator[bytes]:
        """Download page content or attachment."""
        # For pages: return HTML content from MongoDB
        # For attachments: download from Confluence
        raise NotImplementedError("Implement content retrieval")
        yield b""  # Required for AsyncIterator
    
    async def list_all_files(
        self,
        folder_id: str,
        recursive: bool = True,
        file_types: Optional[list[str]] = None
    ) -> AsyncIterator[RemoteFile]:
        """List all pages in a space."""
        # Query MongoDB for all synced pages in this space
        logger.info(f"Listing all files in space: {folder_id}")
        return
        yield  # Required for AsyncIterator
    
    async def get_changes(
        self,
        delta_token: Optional[str] = None,
        folder_id: Optional[str] = None
    ) -> SyncDelta:
        """
        Get changes since last sync.
        
        For Airbyte-based providers, this triggers a sync job
        and returns the delta from Airbyte's incremental sync.
        """
        if not self._connection_id:
            raise ValueError("Connection not established. Call authenticate() first.")
        
        # Trigger Airbyte sync
        job = await self.trigger_sync()
        completed_job = await self.wait_for_sync(job.job_id)
        
        # Return sync stats as delta
        return SyncDelta(
            added=[],  # Would need to query MongoDB for new records
            modified=[],
            deleted=[],
            next_delta_token=str(completed_job.job_id),
            has_more=False,
            total_changes=completed_job.records_synced,
        )
