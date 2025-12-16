"""
Cloud Source Connections Router

Manages user connections to cloud sources including creation,
testing, and deletion of connections.
"""

import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Depends, Query
from bson import ObjectId

from backend.routers.cloud_sources.schemas import (
    ProviderType,
    AuthType,
    ConnectionStatus,
    ConnectionCreateRequest,
    ConnectionUpdateRequest,
    ConnectionResponse,
    ConnectionListResponse,
    ConnectionTestResponse,
    FolderContentsResponse,
    RemoteFolderResponse,
    RemoteFileResponse,
    SuccessResponse,
)
from backend.routers.auth import require_auth, UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connections", tags=["cloud-sources-connections"])

# Collection names
CONNECTIONS_COLLECTION = "cloud_source_connections"


async def get_connections_collection(request: Request):
    """Get the connections collection from the database."""
    return request.app.state.db.db[CONNECTIONS_COLLECTION]


def connection_doc_to_response(doc: dict) -> ConnectionResponse:
    """Convert a MongoDB document to a ConnectionResponse."""
    return ConnectionResponse(
        id=str(doc["_id"]),
        user_id=str(doc["user_id"]),
        provider=doc["provider"],
        display_name=doc["display_name"],
        auth_type=doc["auth_type"],
        status=doc.get("status", ConnectionStatus.ACTIVE),
        server_url=doc.get("server_url"),
        oauth_email=doc.get("oauth_metadata", {}).get("email"),
        oauth_expires_at=doc.get("oauth_metadata", {}).get("expires_at"),
        oauth_scopes=doc.get("oauth_metadata", {}).get("scopes"),
        last_validated_at=doc.get("last_validated_at"),
        error_message=doc.get("error_message"),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


@router.get("", response_model=ConnectionListResponse)
async def list_connections(
    request: Request,
    provider: Optional[ProviderType] = None,
    status: Optional[ConnectionStatus] = None,
    user: UserResponse = Depends(require_auth)
):
    """
    List all connections for the authenticated user.
    
    Args:
        provider: Optional filter by provider type
        status: Optional filter by connection status
    """
    collection = await get_connections_collection(request)
    
    # Build query
    query = {"user_id": user.id}
    if provider:
        query["provider"] = provider.value
    if status:
        query["status"] = status.value
    
    # Fetch connections
    connections = []
    async for doc in collection.find(query).sort("created_at", -1):
        connections.append(connection_doc_to_response(doc))
    
    return ConnectionListResponse(
        connections=connections,
        total=len(connections)
    )


@router.post("", response_model=ConnectionResponse)
async def create_connection(
    request: Request,
    connection_request: ConnectionCreateRequest,
    user: UserResponse = Depends(require_auth)
):
    """
    Create a new cloud source connection.
    
    For OAuth providers, use the /oauth endpoints instead.
    This endpoint is for password, API key, or app token auth.
    """
    collection = await get_connections_collection(request)
    
    # Determine auth type based on provided credentials
    if connection_request.api_key:
        auth_type = AuthType.API_KEY
    elif connection_request.app_token:
        auth_type = AuthType.APP_TOKEN
    elif connection_request.username and connection_request.password:
        auth_type = AuthType.PASSWORD
    else:
        raise HTTPException(
            status_code=400,
            detail="Must provide api_key, app_token, or username/password"
        )
    
    # Validate required fields for password auth
    if auth_type == AuthType.PASSWORD and not connection_request.server_url:
        raise HTTPException(
            status_code=400,
            detail="server_url is required for password authentication"
        )
    
    now = datetime.utcnow()
    
    # Create connection document
    # NOTE: In production, credentials should be encrypted before storage
    doc = {
        "user_id": user.id,
        "provider": connection_request.provider.value,
        "display_name": connection_request.display_name,
        "auth_type": auth_type.value,
        "server_url": connection_request.server_url,
        "credentials": {
            # TODO: Encrypt these values using CredentialVault
            "username": connection_request.username,
            "password": connection_request.password,
            "api_key": connection_request.api_key,
            "app_token": connection_request.app_token,
        },
        "status": ConnectionStatus.PENDING.value,
        "created_at": now,
        "updated_at": now,
    }
    
    result = await collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    
    # Test the connection
    test_result = await test_connection_internal(request, str(result.inserted_id), user)
    if test_result.success:
        await collection.update_one(
            {"_id": result.inserted_id},
            {"$set": {
                "status": ConnectionStatus.ACTIVE.value,
                "last_validated_at": datetime.utcnow()
            }}
        )
        doc["status"] = ConnectionStatus.ACTIVE.value
        doc["last_validated_at"] = datetime.utcnow()
    else:
        await collection.update_one(
            {"_id": result.inserted_id},
            {"$set": {
                "status": ConnectionStatus.ERROR.value,
                "error_message": test_result.message
            }}
        )
        doc["status"] = ConnectionStatus.ERROR.value
        doc["error_message"] = test_result.message
    
    return connection_doc_to_response(doc)


@router.get("/{connection_id}", response_model=ConnectionResponse)
async def get_connection(
    request: Request,
    connection_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Get details for a specific connection."""
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
    
    return connection_doc_to_response(doc)


@router.put("/{connection_id}", response_model=ConnectionResponse)
async def update_connection(
    request: Request,
    connection_id: str,
    update_request: ConnectionUpdateRequest,
    user: UserResponse = Depends(require_auth)
):
    """Update a connection's display name or credentials."""
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
    
    # Build update
    update = {"updated_at": datetime.utcnow()}
    
    if update_request.display_name:
        update["display_name"] = update_request.display_name
    
    # Update credentials if provided
    # NOTE: Should be encrypted in production
    if update_request.password:
        update["credentials.password"] = update_request.password
    if update_request.api_key:
        update["credentials.api_key"] = update_request.api_key
    if update_request.app_token:
        update["credentials.app_token"] = update_request.app_token
    
    await collection.update_one(
        {"_id": ObjectId(connection_id)},
        {"$set": update}
    )
    
    doc = await collection.find_one({"_id": ObjectId(connection_id)})
    return connection_doc_to_response(doc)


@router.delete("/{connection_id}", response_model=SuccessResponse)
async def delete_connection(
    request: Request,
    connection_id: str,
    user: UserResponse = Depends(require_auth)
):
    """
    Delete a connection and all associated sync configurations.
    
    This also removes any indexed documents from this source.
    """
    collection = await get_connections_collection(request)
    
    try:
        result = await collection.delete_one({
            "_id": ObjectId(connection_id),
            "user_id": user.id
        })
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid connection ID")
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Connection not found")
    
    # TODO: Also delete associated sync configs and indexed documents
    
    return SuccessResponse(
        success=True,
        message="Connection deleted successfully"
    )


@router.post("/{connection_id}/test", response_model=ConnectionTestResponse)
async def test_connection(
    request: Request,
    connection_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Test that a connection is valid and working."""
    return await test_connection_internal(request, connection_id, user)


async def test_connection_internal(
    request: Request,
    connection_id: str,
    user: UserResponse
) -> ConnectionTestResponse:
    """Internal helper to test a connection."""
    collection = await get_connections_collection(request)
    
    try:
        doc = await collection.find_one({
            "_id": ObjectId(connection_id),
            "user_id": user.id
        })
    except Exception:
        return ConnectionTestResponse(
            success=False,
            message="Invalid connection ID"
        )
    
    if not doc:
        return ConnectionTestResponse(
            success=False,
            message="Connection not found"
        )
    
    # TODO: Actually test the connection using the provider
    # For now, return a mock success
    provider = doc["provider"]
    
    # Placeholder for actual provider testing
    # In real implementation:
    # 1. Load credentials
    # 2. Create provider instance
    # 3. Call provider.validate_credentials()
    # 4. Get user info and quota
    
    return ConnectionTestResponse(
        success=True,
        message=f"Successfully connected to {provider}",
        user_info={
            "email": doc.get("oauth_metadata", {}).get("email", "test@example.com"),
            "name": "Test User"
        },
        storage_quota={
            "used": 1073741824,  # 1 GB
            "total": 16106127360,  # 15 GB
            "remaining": 15032385536
        }
    )


@router.get("/{connection_id}/browse", response_model=FolderContentsResponse)
async def browse_folder(
    request: Request,
    connection_id: str,
    path: str = Query("/", description="Folder path to browse"),
    folder_id: Optional[str] = Query(None, description="Folder ID (preferred over path)"),
    user: UserResponse = Depends(require_auth)
):
    """
    Browse folders and files for folder selection.
    
    Used by the folder picker component to let users select
    which folders to sync.
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
    
    # TODO: Actually browse using the provider
    # For now, return mock data for UI development
    
    mock_folders = [
        RemoteFolderResponse(
            id=f"folder_{i}",
            name=f"Folder {i}",
            path=f"{path}Folder {i}/",
            has_children=i < 3,
            children_count=5 if i < 3 else 0
        )
        for i in range(1, 6)
    ]
    
    mock_files = [
        RemoteFileResponse(
            id=f"file_{i}",
            name=f"Document {i}.pdf",
            path=f"{path}Document {i}.pdf",
            mime_type="application/pdf",
            size_bytes=1024 * 1024 * i,
            modified_at=datetime.utcnow()
        )
        for i in range(1, 4)
    ]
    
    return FolderContentsResponse(
        current_folder=RemoteFolderResponse(
            id=folder_id or "root",
            name="Root" if path == "/" else path.split("/")[-2],
            path=path,
            has_children=True
        ),
        folders=mock_folders,
        files=mock_files,
        has_more=False
    )
