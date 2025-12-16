"""
Cloud Source Providers Router

Provides endpoints for discovering available cloud source providers
and their capabilities.
"""

import logging
from fastapi import APIRouter, Depends

from backend.routers.cloud_sources.schemas import (
    ProviderType,
    AuthType,
    ProviderCapabilitiesResponse,
    ProvidersListResponse,
)
from backend.routers.auth import require_auth, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["cloud-sources-providers"])


# Provider definitions - static configuration
PROVIDERS = {
    ProviderType.GOOGLE_DRIVE: ProviderCapabilitiesResponse(
        provider_type=ProviderType.GOOGLE_DRIVE,
        display_name="Google Drive",
        description="Connect to Google Drive to index documents, spreadsheets, and other files",
        icon="google-drive",
        supported_auth_types=[AuthType.OAUTH2],
        supports_delta_sync=True,
        supports_webhooks=True,
        documentation_url="https://developers.google.com/drive",
        setup_instructions="Sign in with your Google account to grant access to your Drive files.",
    ),
    ProviderType.ONEDRIVE: ProviderCapabilitiesResponse(
        provider_type=ProviderType.ONEDRIVE,
        display_name="OneDrive",
        description="Connect to Microsoft OneDrive personal or business accounts",
        icon="microsoft-onedrive",
        supported_auth_types=[AuthType.OAUTH2],
        supports_delta_sync=True,
        supports_webhooks=True,
        documentation_url="https://docs.microsoft.com/en-us/graph/",
        setup_instructions="Sign in with your Microsoft account to access OneDrive files.",
    ),
    ProviderType.SHAREPOINT: ProviderCapabilitiesResponse(
        provider_type=ProviderType.SHAREPOINT,
        display_name="SharePoint",
        description="Connect to Microsoft SharePoint document libraries",
        icon="microsoft-sharepoint",
        supported_auth_types=[AuthType.OAUTH2],
        supports_delta_sync=True,
        supports_webhooks=True,
        documentation_url="https://docs.microsoft.com/en-us/graph/",
        setup_instructions="Sign in with your Microsoft work account to access SharePoint sites.",
    ),
    ProviderType.DROPBOX: ProviderCapabilitiesResponse(
        provider_type=ProviderType.DROPBOX,
        display_name="Dropbox",
        description="Connect to Dropbox personal or business accounts",
        icon="dropbox",
        supported_auth_types=[AuthType.OAUTH2],
        supports_delta_sync=True,
        supports_webhooks=True,
        documentation_url="https://www.dropbox.com/developers",
        setup_instructions="Sign in with your Dropbox account to access your files.",
    ),
    ProviderType.OWNCLOUD: ProviderCapabilitiesResponse(
        provider_type=ProviderType.OWNCLOUD,
        display_name="OwnCloud",
        description="Connect to self-hosted OwnCloud instances via WebDAV",
        icon="owncloud",
        supported_auth_types=[AuthType.PASSWORD, AuthType.APP_TOKEN],
        supports_delta_sync=False,
        supports_webhooks=False,
        documentation_url="https://doc.owncloud.com/",
        setup_instructions="Enter your OwnCloud server URL and credentials or app password.",
    ),
    ProviderType.NEXTCLOUD: ProviderCapabilitiesResponse(
        provider_type=ProviderType.NEXTCLOUD,
        display_name="Nextcloud",
        description="Connect to self-hosted Nextcloud instances via WebDAV",
        icon="nextcloud",
        supported_auth_types=[AuthType.PASSWORD, AuthType.APP_TOKEN],
        supports_delta_sync=False,
        supports_webhooks=False,
        documentation_url="https://docs.nextcloud.com/",
        setup_instructions="Enter your Nextcloud server URL and app password.",
    ),
    ProviderType.CONFLUENCE: ProviderCapabilitiesResponse(
        provider_type=ProviderType.CONFLUENCE,
        display_name="Confluence",
        description="Connect to Atlassian Confluence for wiki and documentation",
        icon="atlassian-confluence",
        supported_auth_types=[AuthType.OAUTH2, AuthType.API_KEY],
        supports_delta_sync=True,
        supports_webhooks=True,
        documentation_url="https://developer.atlassian.com/cloud/confluence/",
        setup_instructions="Connect via OAuth or use an API token with your Atlassian account.",
    ),
    ProviderType.JIRA: ProviderCapabilitiesResponse(
        provider_type=ProviderType.JIRA,
        display_name="Jira",
        description="Connect to Atlassian Jira to index issues and attachments",
        icon="atlassian-jira",
        supported_auth_types=[AuthType.OAUTH2, AuthType.API_KEY],
        supports_delta_sync=True,
        supports_webhooks=True,
        documentation_url="https://developer.atlassian.com/cloud/jira/",
        setup_instructions="Connect via OAuth or use an API token with your Atlassian account.",
    ),
    ProviderType.EMAIL_IMAP: ProviderCapabilitiesResponse(
        provider_type=ProviderType.EMAIL_IMAP,
        display_name="Email (IMAP)",
        description="Connect to any email server via IMAP to index emails",
        icon="email",
        supported_auth_types=[AuthType.PASSWORD],
        supports_delta_sync=True,
        supports_webhooks=False,
        documentation_url=None,
        setup_instructions="Enter your IMAP server settings and credentials.",
    ),
    ProviderType.EMAIL_GMAIL: ProviderCapabilitiesResponse(
        provider_type=ProviderType.EMAIL_GMAIL,
        display_name="Gmail",
        description="Connect to Gmail to index emails and attachments",
        icon="google-gmail",
        supported_auth_types=[AuthType.OAUTH2],
        supports_delta_sync=True,
        supports_webhooks=True,
        documentation_url="https://developers.google.com/gmail/api",
        setup_instructions="Sign in with your Google account to access Gmail.",
    ),
    ProviderType.EMAIL_OUTLOOK: ProviderCapabilitiesResponse(
        provider_type=ProviderType.EMAIL_OUTLOOK,
        display_name="Outlook",
        description="Connect to Outlook.com or Microsoft 365 email",
        icon="microsoft-outlook",
        supported_auth_types=[AuthType.OAUTH2],
        supports_delta_sync=True,
        supports_webhooks=True,
        documentation_url="https://docs.microsoft.com/en-us/graph/",
        setup_instructions="Sign in with your Microsoft account to access Outlook.",
    ),
}


@router.get("", response_model=ProvidersListResponse)
async def list_providers(
    user: UserResponse = Depends(require_auth)
):
    """
    List all available cloud source providers.
    
    Returns provider metadata including supported authentication methods,
    capabilities, and setup instructions.
    """
    return ProvidersListResponse(providers=list(PROVIDERS.values()))


@router.get("/{provider_type}", response_model=ProviderCapabilitiesResponse)
async def get_provider(
    provider_type: ProviderType,
    user: UserResponse = Depends(require_auth)
):
    """
    Get details for a specific provider.
    
    Args:
        provider_type: The provider type identifier
    """
    if provider_type not in PROVIDERS:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Provider {provider_type} not found")
    
    return PROVIDERS[provider_type]


def get_provider_config(provider_type: ProviderType) -> ProviderCapabilitiesResponse:
    """Helper function to get provider config internally."""
    return PROVIDERS.get(provider_type)
