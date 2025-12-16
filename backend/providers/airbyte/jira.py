"""
Jira Provider via Airbyte

Provides Jira Cloud/Server integration using Airbyte's
Jira connector. Syncs issues, projects, and attachments.
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


@register_provider(ProviderType.JIRA)
class JiraProvider(AirbyteProvider):
    """
    Jira integration via Airbyte.
    
    Supports both Jira Cloud (OAuth 2.0 / API Token) and
    Jira Server/Data Center (Personal Access Token).
    
    Syncs:
    - Issues (with descriptions and custom fields)
    - Projects
    - Issue attachments
    - Comments
    - Sprints (for Jira Software)
    """
    
    # Airbyte Jira source definition ID
    JIRA_SOURCE_DEFINITION_ID = "68e63de2-bb83-4c7e-93fa-a8a9051f3d29"
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.JIRA
    
    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type=ProviderType.JIRA,
            display_name="Jira",
            description="Atlassian Jira issue tracking and project management",
            icon="jira",
            supported_auth_types=[AuthType.OAUTH2, AuthType.API_KEY],
            oauth_scopes=[
                "read:jira-work",
                "read:jira-user",
                "read:sprint:jira-software",
            ],
            supports_delta_sync=True,
            supports_webhooks=False,
            supports_file_streaming=False,
            supports_folders=True,  # Projects
            supports_files=True,    # Issues
            supports_attachments=True,
            rate_limit_requests_per_minute=100,
            documentation_url="https://support.atlassian.com/jira-cloud/",
            setup_instructions=(
                "1. Go to Atlassian Account Settings\n"
                "2. Create an API token or set up OAuth 2.0\n"
                "3. Enter your site URL and credentials"
            ),
        )
    
    @property
    def source_definition_id(self) -> str:
        return self.JIRA_SOURCE_DEFINITION_ID
    
    @property
    def source_display_name(self) -> str:
        return "Jira"
    
    def build_source_config(
        self, 
        credentials: ConnectionCredentials
    ) -> dict[str, Any]:
        """
        Build Airbyte Jira source configuration.
        
        Jira Cloud requires:
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
        
        # Optional: Filter to specific projects
        if credentials.extra.get("projects"):
            config["projects"] = credentials.extra["projects"]
        
        # Enable expanded fields for richer data
        config["expand_issue_changelog"] = credentials.extra.get("expand_changelog", False)
        config["render_fields"] = credentials.extra.get("render_fields", True)
        
        return config
    
    def get_default_streams(self) -> list[str]:
        """Return default Jira streams to sync."""
        return [
            "issues",
            "projects",
            "issue_comments",
            "issue_fields",
            "users",
            "sprints",
        ]
    
    def transform_record(
        self, 
        stream_name: str, 
        record: dict[str, Any]
    ) -> Optional[RemoteFile]:
        """Transform Jira records to RemoteFile format."""
        
        if stream_name == "issues":
            return self._transform_issue(record)
        elif stream_name == "projects":
            return self._transform_project(record)
        elif stream_name == "issue_comments":
            return self._transform_comment(record)
        
        return None
    
    def _transform_issue(self, record: dict[str, Any]) -> RemoteFile:
        """Transform a Jira issue record."""
        issue_key = record.get("key", "")
        issue_id = record.get("id", "")
        
        fields = record.get("fields", {})
        summary = fields.get("summary", "Untitled")
        description = fields.get("description", "")
        project = fields.get("project", {})
        project_key = project.get("key", "")
        project_name = project.get("name", "")
        issue_type = fields.get("issuetype", {}).get("name", "Issue")
        status = fields.get("status", {}).get("name", "Unknown")
        priority = fields.get("priority", {}).get("name", "Medium")
        assignee = fields.get("assignee", {})
        reporter = fields.get("reporter", {})
        
        # Build path
        path = f"/{project_key}/{issue_key}"
        
        # Build content from issue details
        content_parts = [
            f"# {issue_key}: {summary}",
            f"\n**Type:** {issue_type}",
            f"**Status:** {status}",
            f"**Priority:** {priority}",
            f"**Project:** {project_name}",
        ]
        
        if assignee:
            content_parts.append(f"**Assignee:** {assignee.get('displayName', 'Unassigned')}")
        if reporter:
            content_parts.append(f"**Reporter:** {reporter.get('displayName', 'Unknown')}")
        
        content_parts.append(f"\n## Description\n{description or 'No description'}")
        
        # Add custom fields
        custom_fields = {k: v for k, v in fields.items() if k.startswith("customfield_") and v}
        if custom_fields:
            content_parts.append("\n## Custom Fields")
            for field_key, value in custom_fields.items():
                if isinstance(value, dict):
                    value = value.get("value") or value.get("name") or str(value)
                content_parts.append(f"- **{field_key}:** {value}")
        
        content = "\n".join(content_parts)
        
        # Parse dates
        created_str = fields.get("created")
        updated_str = fields.get("updated")
        
        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00")) if created_str else None
        modified_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00")) if updated_str else datetime.now()
        
        return RemoteFile(
            id=f"jira_issue_{issue_id}",
            name=f"{issue_key}.md",
            path=path,
            mime_type="text/markdown",
            size_bytes=len(content.encode("utf-8")),
            modified_at=modified_at,
            created_at=created_at,
            web_view_url=record.get("self", "").replace("/rest/api/", "/browse/").replace(f"/{issue_id}", f"/{issue_key}"),
            version_id=str(fields.get("updated", "")),
            provider_metadata={
                "type": "issue",
                "issue_key": issue_key,
                "jira_id": issue_id,
                "project_key": project_key,
                "issue_type": issue_type,
                "status": status,
                "priority": priority,
                "content": content,
                "labels": fields.get("labels", []),
                "components": [c.get("name") for c in fields.get("components", [])],
            }
        )
    
    def _transform_project(self, record: dict[str, Any]) -> RemoteFile:
        """Transform a Jira project record."""
        project_id = record.get("id", "")
        project_key = record.get("key", "")
        name = record.get("name", "Untitled Project")
        description = record.get("description", "")
        project_type = record.get("projectTypeKey", "software")
        lead = record.get("lead", {})
        
        content_parts = [
            f"# {name} ({project_key})",
            f"\n**Type:** {project_type}",
        ]
        
        if lead:
            content_parts.append(f"**Lead:** {lead.get('displayName', 'Unknown')}")
        
        if description:
            content_parts.append(f"\n## Description\n{description}")
        
        content = "\n".join(content_parts)
        
        return RemoteFile(
            id=f"jira_project_{project_id}",
            name=f"{project_key}_project.md",
            path=f"/{project_key}",
            mime_type="text/markdown",
            size_bytes=len(content.encode("utf-8")),
            modified_at=datetime.now(),  # Projects don't have modified date
            web_view_url=record.get("self", "").replace("/rest/api/", "/browse/").replace(f"/project/{project_id}", f"/{project_key}"),
            provider_metadata={
                "type": "project",
                "project_key": project_key,
                "jira_id": project_id,
                "project_type": project_type,
                "content": content,
            }
        )
    
    def _transform_comment(self, record: dict[str, Any]) -> RemoteFile:
        """Transform a Jira comment record."""
        comment_id = record.get("id", "")
        issue_id = record.get("issueId", "")
        body = record.get("body", "")
        author = record.get("author", {})
        
        created_str = record.get("created")
        updated_str = record.get("updated")
        
        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00")) if created_str else None
        modified_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00")) if updated_str else datetime.now()
        
        author_name = author.get("displayName", "Unknown")
        
        content = f"**Comment by {author_name}:**\n\n{body}"
        
        return RemoteFile(
            id=f"jira_comment_{comment_id}",
            name=f"comment_{comment_id}.md",
            path=f"/comments/{issue_id}/{comment_id}",
            mime_type="text/markdown",
            size_bytes=len(content.encode("utf-8")),
            modified_at=modified_at,
            created_at=created_at,
            provider_metadata={
                "type": "comment",
                "comment_id": comment_id,
                "issue_id": issue_id,
                "author": author_name,
                "content": content,
            }
        )
    
    # ==================== Browsing Methods ====================
    
    async def list_root_folders(self) -> list[RemoteFolder]:
        """List Jira projects as root folders."""
        logger.info("Listing Jira projects (requires sync to be completed)")
        return []
    
    async def list_folder_contents(
        self,
        folder_id: str,
        include_files: bool = True,
        include_folders: bool = True
    ) -> tuple[list[RemoteFolder], list[RemoteFile]]:
        """List issues in a Jira project."""
        logger.info(f"Listing contents of project: {folder_id}")
        return [], []
    
    async def get_file_metadata(self, file_id: str) -> RemoteFile:
        """Get metadata for a Jira issue."""
        raise NotImplementedError("Query MongoDB for synced content")
    
    async def download_file(self, file_id: str) -> AsyncIterator[bytes]:
        """Download issue content."""
        raise NotImplementedError("Implement content retrieval")
        yield b""
    
    async def list_all_files(
        self,
        folder_id: str,
        recursive: bool = True,
        file_types: Optional[list[str]] = None
    ) -> AsyncIterator[RemoteFile]:
        """List all issues in a project."""
        logger.info(f"Listing all issues in project: {folder_id}")
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
