"""
Cloud Source OAuth Router

Handles OAuth 2.0 authorization flows for cloud providers
including Google Drive, OneDrive, Dropbox, and Atlassian.
"""

import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode
from fastapi import APIRouter, HTTPException, Request, Depends, Query
from fastapi.responses import RedirectResponse
from bson import ObjectId

from backend.routers.cloud_sources.schemas import (
    ProviderType,
    AuthType,
    ConnectionStatus,
    OAuthInitRequest,
    OAuthInitResponse,
    ConnectionResponse,
)
from backend.routers.cloud_sources.connections import (
    get_connections_collection,
    connection_doc_to_response
)
from backend.routers.auth import require_auth, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oauth", tags=["cloud-sources-oauth"])

# OAuth state storage (in production, use Redis or database)
# Maps state token -> {user_id, provider, display_name, created_at}
_oauth_states: dict = {}

# OAuth configurations (in production, load from environment/secrets)
OAUTH_CONFIGS = {
    ProviderType.GOOGLE_DRIVE: {
        "client_id": "",  # Set via environment
        "client_secret": "",  # Set via environment
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": [
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        ],
    },
    ProviderType.ONEDRIVE: {
        "client_id": "",
        "client_secret": "",
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": [
            "Files.Read.All",
            "User.Read",
            "offline_access",
        ],
    },
    ProviderType.SHAREPOINT: {
        "client_id": "",
        "client_secret": "",
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": [
            "Sites.Read.All",
            "Files.Read.All",
            "User.Read",
            "offline_access",
        ],
    },
    ProviderType.DROPBOX: {
        "client_id": "",
        "client_secret": "",
        "auth_url": "https://www.dropbox.com/oauth2/authorize",
        "token_url": "https://api.dropboxapi.com/oauth2/token",
        "scopes": [],  # Dropbox uses app-level permissions
    },
    ProviderType.CONFLUENCE: {
        "client_id": "",
        "client_secret": "",
        "auth_url": "https://auth.atlassian.com/authorize",
        "token_url": "https://auth.atlassian.com/oauth/token",
        "scopes": [
            "read:confluence-content.all",
            "read:confluence-space.summary",
            "offline_access",
        ],
    },
    ProviderType.JIRA: {
        "client_id": "",
        "client_secret": "",
        "auth_url": "https://auth.atlassian.com/authorize",
        "token_url": "https://auth.atlassian.com/oauth/token",
        "scopes": [
            "read:jira-work",
            "read:jira-user",
            "offline_access",
        ],
    },
    ProviderType.EMAIL_GMAIL: {
        "client_id": "",
        "client_secret": "",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/userinfo.email",
        ],
    },
    ProviderType.EMAIL_OUTLOOK: {
        "client_id": "",
        "client_secret": "",
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": [
            "Mail.Read",
            "User.Read",
            "offline_access",
        ],
    },
}


def get_oauth_config(provider: ProviderType) -> dict:
    """Get OAuth configuration for a provider."""
    import os
    
    if provider not in OAUTH_CONFIGS:
        raise HTTPException(
            status_code=400,
            detail=f"OAuth not supported for provider: {provider}"
        )
    
    config = OAUTH_CONFIGS[provider].copy()
    
    # Load client credentials from environment
    env_prefix = provider.value.upper().replace("-", "_")
    config["client_id"] = os.environ.get(f"{env_prefix}_CLIENT_ID", config["client_id"])
    config["client_secret"] = os.environ.get(f"{env_prefix}_CLIENT_SECRET", config["client_secret"])
    
    return config


def get_redirect_uri(request: Request, provider: ProviderType) -> str:
    """Get the OAuth callback URL."""
    # Use the request's base URL to construct callback
    base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/api/v1/cloud-sources/oauth/{provider.value}/callback"


@router.post("/{provider}/authorize", response_model=OAuthInitResponse)
async def initiate_oauth(
    request: Request,
    provider: ProviderType,
    init_request: OAuthInitRequest,
    user: UserResponse = Depends(require_auth)
):
    """
    Start the OAuth authorization flow.
    
    Returns an authorization URL that the frontend should redirect to.
    """
    # Verify provider matches
    if init_request.provider != provider:
        raise HTTPException(
            status_code=400,
            detail="Provider in URL must match provider in request body"
        )
    
    config = get_oauth_config(provider)
    
    if not config["client_id"]:
        raise HTTPException(
            status_code=503,
            detail=f"OAuth not configured for {provider}. Please set {provider.value.upper()}_CLIENT_ID environment variable."
        )
    
    # Generate state token for CSRF protection
    state = secrets.token_urlsafe(32)
    
    # Store state with user info (expires in 10 minutes)
    _oauth_states[state] = {
        "user_id": user.id,
        "provider": provider.value,
        "display_name": init_request.display_name,
        "created_at": datetime.utcnow(),
    }
    
    # Build authorization URL
    redirect_uri = get_redirect_uri(request, provider)
    
    params = {
        "client_id": config["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "access_type": "offline",  # For refresh tokens
        "prompt": "consent",  # Force consent to get refresh token
    }
    
    # Add scopes
    if config["scopes"]:
        params["scope"] = " ".join(config["scopes"])
    
    # Provider-specific parameters
    if provider in [ProviderType.CONFLUENCE, ProviderType.JIRA]:
        params["audience"] = "api.atlassian.com"
    
    auth_url = f"{config['auth_url']}?{urlencode(params)}"
    
    return OAuthInitResponse(
        authorization_url=auth_url,
        state=state
    )


@router.get("/{provider}/callback")
async def oauth_callback(
    request: Request,
    provider: ProviderType,
    code: str = Query(...),
    state: str = Query(...),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None)
):
    """
    Handle OAuth callback from provider.
    
    This endpoint is called by the OAuth provider after user authorization.
    It exchanges the code for tokens and creates the connection.
    """
    # Check for OAuth error
    if error:
        logger.error(f"OAuth error: {error} - {error_description}")
        # Redirect to frontend with error
        return RedirectResponse(
            url=f"/cloud-sources/connections?error={error}&message={error_description or error}"
        )
    
    # Validate state
    if state not in _oauth_states:
        logger.error(f"Invalid OAuth state: {state}")
        return RedirectResponse(
            url="/cloud-sources/connections?error=invalid_state&message=OAuth+state+invalid+or+expired"
        )
    
    state_data = _oauth_states.pop(state)
    
    # Check state expiration (10 minutes)
    if datetime.utcnow() - state_data["created_at"] > timedelta(minutes=10):
        return RedirectResponse(
            url="/cloud-sources/connections?error=expired&message=OAuth+session+expired"
        )
    
    # Exchange code for tokens
    config = get_oauth_config(provider)
    redirect_uri = get_redirect_uri(request, provider)
    
    try:
        tokens = await exchange_code_for_tokens(
            config=config,
            code=code,
            redirect_uri=redirect_uri,
            provider=provider
        )
    except Exception as e:
        logger.error(f"Token exchange failed: {e}")
        return RedirectResponse(
            url=f"/cloud-sources/connections?error=token_exchange_failed&message={str(e)}"
        )
    
    # Get user info
    try:
        user_info = await get_oauth_user_info(provider, tokens["access_token"])
    except Exception as e:
        logger.warning(f"Could not get user info: {e}")
        user_info = {}
    
    # Create connection in database
    collection = await get_connections_collection(request)
    
    now = datetime.utcnow()
    expires_at = None
    if tokens.get("expires_in"):
        expires_at = now + timedelta(seconds=tokens["expires_in"])
    
    doc = {
        "user_id": state_data["user_id"],
        "provider": provider.value,
        "display_name": state_data["display_name"],
        "auth_type": AuthType.OAUTH2.value,
        "credentials": {
            # TODO: Encrypt these values
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token"),
            "token_type": tokens.get("token_type", "Bearer"),
        },
        "oauth_metadata": {
            "email": user_info.get("email"),
            "name": user_info.get("name"),
            "expires_at": expires_at,
            "scopes": config["scopes"],
        },
        "status": ConnectionStatus.ACTIVE.value,
        "last_validated_at": now,
        "created_at": now,
        "updated_at": now,
    }
    
    result = await collection.insert_one(doc)
    
    # Redirect to frontend success page
    return RedirectResponse(
        url=f"/cloud-sources/connections?success=true&connection_id={result.inserted_id}"
    )


async def exchange_code_for_tokens(
    config: dict,
    code: str,
    redirect_uri: str,
    provider: ProviderType
) -> dict:
    """Exchange authorization code for access/refresh tokens."""
    import httpx
    
    data = {
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            config["token_url"],
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        if response.status_code != 200:
            error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            raise Exception(
                error_data.get("error_description") or 
                error_data.get("error") or 
                f"Token exchange failed with status {response.status_code}"
            )
        
        return response.json()


async def get_oauth_user_info(provider: ProviderType, access_token: str) -> dict:
    """Get user info from OAuth provider."""
    import httpx
    
    # Provider-specific user info endpoints
    user_info_urls = {
        ProviderType.GOOGLE_DRIVE: "https://www.googleapis.com/oauth2/v2/userinfo",
        ProviderType.EMAIL_GMAIL: "https://www.googleapis.com/oauth2/v2/userinfo",
        ProviderType.ONEDRIVE: "https://graph.microsoft.com/v1.0/me",
        ProviderType.SHAREPOINT: "https://graph.microsoft.com/v1.0/me",
        ProviderType.EMAIL_OUTLOOK: "https://graph.microsoft.com/v1.0/me",
        ProviderType.DROPBOX: "https://api.dropboxapi.com/2/users/get_current_account",
    }
    
    url = user_info_urls.get(provider)
    if not url:
        return {}
    
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Dropbox uses POST
        if provider == ProviderType.DROPBOX:
            response = await client.post(url, headers=headers)
        else:
            response = await client.get(url, headers=headers)
        
        if response.status_code != 200:
            return {}
        
        data = response.json()
        
        # Normalize user info
        if provider in [ProviderType.GOOGLE_DRIVE, ProviderType.EMAIL_GMAIL]:
            return {"email": data.get("email"), "name": data.get("name")}
        elif provider in [ProviderType.ONEDRIVE, ProviderType.SHAREPOINT, ProviderType.EMAIL_OUTLOOK]:
            return {"email": data.get("mail") or data.get("userPrincipalName"), "name": data.get("displayName")}
        elif provider == ProviderType.DROPBOX:
            return {"email": data.get("email"), "name": data.get("name", {}).get("display_name")}
        
        return data


@router.post("/{connection_id}/refresh", response_model=ConnectionResponse)
async def refresh_oauth_tokens(
    request: Request,
    connection_id: str,
    user: UserResponse = Depends(require_auth)
):
    """
    Manually refresh OAuth tokens for a connection.
    
    This is typically done automatically, but can be triggered manually
    if tokens are near expiration.
    """
    collection = await get_connections_collection(request)
    
    try:
        doc = await collection.find_one({
            "_id": ObjectId(connection_id),
            "user_id": user.id
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid connection ID")
    
    if not doc:
        raise HTTPException(status_code=404, detail="Connection not found")
    
    if doc["auth_type"] != AuthType.OAUTH2.value:
        raise HTTPException(status_code=400, detail="Connection does not use OAuth")
    
    refresh_token = doc.get("credentials", {}).get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="No refresh token available")
    
    provider = ProviderType(doc["provider"])
    config = get_oauth_config(provider)
    
    try:
        tokens = await refresh_access_token(config, refresh_token)
    except Exception as e:
        await collection.update_one(
            {"_id": ObjectId(connection_id)},
            {"$set": {
                "status": ConnectionStatus.EXPIRED.value,
                "error_message": str(e),
                "updated_at": datetime.utcnow()
            }}
        )
        raise HTTPException(status_code=400, detail=f"Token refresh failed: {str(e)}")
    
    # Update connection with new tokens
    now = datetime.utcnow()
    expires_at = None
    if tokens.get("expires_in"):
        expires_at = now + timedelta(seconds=tokens["expires_in"])
    
    await collection.update_one(
        {"_id": ObjectId(connection_id)},
        {"$set": {
            "credentials.access_token": tokens["access_token"],
            "credentials.refresh_token": tokens.get("refresh_token", refresh_token),
            "oauth_metadata.expires_at": expires_at,
            "status": ConnectionStatus.ACTIVE.value,
            "last_validated_at": now,
            "error_message": None,
            "updated_at": now
        }}
    )
    
    doc = await collection.find_one({"_id": ObjectId(connection_id)})
    return connection_doc_to_response(doc)


async def refresh_access_token(config: dict, refresh_token: str) -> dict:
    """Refresh an expired access token."""
    import httpx
    
    data = {
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            config["token_url"],
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        if response.status_code != 200:
            error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            raise Exception(
                error_data.get("error_description") or 
                error_data.get("error") or 
                "Token refresh failed"
            )
        
        return response.json()


@router.delete("/{connection_id}/revoke")
async def revoke_oauth_tokens(
    request: Request,
    connection_id: str,
    user: UserResponse = Depends(require_auth)
):
    """
    Revoke OAuth tokens at the provider.
    
    This disconnects the application from the user's account at the provider level.
    """
    collection = await get_connections_collection(request)
    
    try:
        doc = await collection.find_one({
            "_id": ObjectId(connection_id),
            "user_id": user.id
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid connection ID")
    
    if not doc:
        raise HTTPException(status_code=404, detail="Connection not found")
    
    # TODO: Actually revoke tokens at provider
    # Each provider has different revocation endpoints
    
    # Update connection status
    await collection.update_one(
        {"_id": ObjectId(connection_id)},
        {"$set": {
            "status": ConnectionStatus.REVOKED.value,
            "updated_at": datetime.utcnow()
        }}
    )
    
    return {"success": True, "message": "OAuth tokens revoked"}
