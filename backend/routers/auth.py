"""Authentication router - User registration, login, JWT management, and API keys."""

import logging
import os
import uuid
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request, Depends, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, EmailStr
from passlib.context import CryptContext
from jose import JWTError, jwt
import bcrypt

logger = logging.getLogger(__name__)

router = APIRouter()

# Password hashing using bcrypt directly to avoid passlib backend issues
def _bcrypt_hash(password: str) -> str:
    """Hash password using bcrypt directly."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def _bcrypt_verify(password: str, hashed: str) -> bool:
    """Verify password against bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

# Fallback CryptContext (used if bcrypt direct fails)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "mongodb-rag-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24 * 7  # 7 days

# Security scheme
security = HTTPBearer(auto_error=False)


# ============== Pydantic Models ==============

class User(BaseModel):
    """User model."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: str
    name: str
    password_hash: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    is_active: bool = True
    is_admin: bool = False


class UserResponse(BaseModel):
    """User response (without password)."""
    id: str
    email: str
    name: str
    created_at: datetime
    is_active: bool
    is_admin: bool = False


class RegisterRequest(BaseModel):
    """Registration request."""
    email: EmailStr
    name: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=6, max_length=100)


class LoginRequest(BaseModel):
    """Login request."""
    email: EmailStr
    password: str


class PasswordChangeRequest(BaseModel):
    """Password change request."""
    current_password: str
    new_password: str = Field(..., min_length=6, max_length=100)


class TokenResponse(BaseModel):
    """Token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


# ============== API Key Models ==============

class APIKey(BaseModel):
    """API key model."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    name: str
    key_hash: str  # Store hashed key, not plain text
    key_prefix: str  # First 8 chars for identification
    created_at: datetime = Field(default_factory=datetime.now)
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    is_active: bool = True
    scopes: List[str] = Field(default_factory=lambda: ["read", "write"])  # Permissions


class APIKeyCreate(BaseModel):
    """Request to create an API key."""
    name: str = Field(..., min_length=1, max_length=100)
    expires_in_days: Optional[int] = Field(None, ge=1, le=365)  # Optional expiration
    scopes: List[str] = Field(default_factory=lambda: ["read", "write"])


class APIKeyResponse(BaseModel):
    """API key response (without full key)."""
    id: str
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    is_active: bool
    scopes: List[str]


class APIKeyCreatedResponse(BaseModel):
    """Response when API key is created - includes full key (only shown once)."""
    id: str
    name: str
    key: str  # Full API key - only returned on creation!
    key_prefix: str
    created_at: datetime
    expires_at: Optional[datetime]
    scopes: List[str]
    warning: str = "Save this key now. It will not be shown again."


# ============== Helpers ==============

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash."""
    return _bcrypt_verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password."""
    return _bcrypt_hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def generate_api_key() -> str:
    """Generate a secure random API key."""
    # Format: rag_xxxx...xxxx (40 chars total)
    return "rag_" + secrets.token_hex(18)


def hash_api_key(key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(key.encode()).hexdigest()


def get_api_key_prefix(key: str) -> str:
    """Get the prefix of an API key for display."""
    return key[:12] + "..." + key[-4:]


async def get_users_collection(request: Request):
    """Get users collection."""
    return request.app.state.db.db["users"]


async def get_api_keys_collection(request: Request):
    """Get API keys collection."""
    return request.app.state.db.db["api_keys"]


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
) -> Optional[UserResponse]:
    """Get current user from JWT token or API key.
    
    Supports two authentication methods:
    1. JWT Bearer token in Authorization header
    2. API key in X-API-Key header
    """
    user_id = None
    
    # Try API key first
    if x_api_key:
        api_keys_collection = await get_api_keys_collection(request)
        key_hash = hash_api_key(x_api_key)
        
        api_key_doc = await api_keys_collection.find_one({
            "key_hash": key_hash,
            "is_active": True
        })
        
        if api_key_doc:
            # Check expiration
            if api_key_doc.get("expires_at") and api_key_doc["expires_at"] < datetime.now():
                return None
            
            # Update last used
            await api_keys_collection.update_one(
                {"_id": api_key_doc["_id"]},
                {"$set": {"last_used_at": datetime.now()}}
            )
            
            user_id = api_key_doc["user_id"]
    
    # Try JWT token
    if not user_id and credentials:
        try:
            payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("sub")
        except JWTError:
            pass
    
    if not user_id:
        return None
    
    collection = await get_users_collection(request)
    user_doc = await collection.find_one({"_id": user_id})
    
    if not user_doc:
        return None
    
    if not user_doc.get("is_active", True):
        return None
    
    return UserResponse(
        id=str(user_doc["_id"]),
        email=user_doc["email"],
        name=user_doc["name"],
        created_at=user_doc["created_at"],
        is_active=user_doc.get("is_active", True),
        is_admin=user_doc.get("is_admin", False)
    )


async def require_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> UserResponse:
    """Require authentication - raises 401 if not authenticated."""
    user = await get_current_user(request, credentials)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_admin(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> UserResponse:
    """Require admin authentication - raises 403 if not admin."""
    user = await require_auth(request, credentials)
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return user


# ============== Auth Endpoints ==============

@router.post("/register", response_model=TokenResponse)
async def register(request: Request, reg_request: RegisterRequest):
    """Register a new user."""
    collection = await get_users_collection(request)
    
    # Check if email already exists
    existing = await collection.find_one({"email": reg_request.email.lower()})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create user
    user = User(
        email=reg_request.email.lower(),
        name=reg_request.name,
        password_hash=get_password_hash(reg_request.password)
    )
    
    doc = user.model_dump()
    doc["_id"] = doc.pop("id")
    
    await collection.insert_one(doc)
    
    # Create access token
    access_token = create_access_token(data={"sub": user.id})
    
    user_response = UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        created_at=user.created_at,
        is_active=user.is_active,
        is_admin=user.is_admin
    )
    
    return TokenResponse(
        access_token=access_token,
        expires_in=ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        user=user_response
    )


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, login_request: LoginRequest):
    """Login with email and password."""
    collection = await get_users_collection(request)
    
    # Find user
    user_doc = await collection.find_one({"email": login_request.email.lower()})
    if not user_doc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    # Verify password
    if not verify_password(login_request.password, user_doc["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    # Check if active
    if not user_doc.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is disabled"
        )
    
    # Create access token
    user_id = str(user_doc["_id"])
    access_token = create_access_token(data={"sub": user_id})
    
    user_response = UserResponse(
        id=user_id,
        email=user_doc["email"],
        name=user_doc["name"],
        created_at=user_doc["created_at"],
        is_active=user_doc.get("is_active", True),
        is_admin=user_doc.get("is_admin", False)
    )
    
    return TokenResponse(
        access_token=access_token,
        expires_in=ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        user=user_response
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: UserResponse = Depends(require_auth)):
    """Get current user info."""
    return user


@router.post("/logout")
async def logout():
    """Logout - client should discard the token."""
    return {"success": True, "message": "Logged out successfully"}


@router.put("/me")
async def update_me(
    request: Request,
    name: Optional[str] = None,
    user: UserResponse = Depends(require_auth)
):
    """Update current user info."""
    collection = await get_users_collection(request)
    
    update_dict = {"updated_at": datetime.now()}
    if name:
        update_dict["name"] = name
    
    await collection.update_one(
        {"_id": user.id},
        {"$set": update_dict}
    )
    
    return {"success": True}


@router.put("/me/password")
async def change_password(
    request: Request,
    password_request: PasswordChangeRequest,
    user: UserResponse = Depends(require_auth)
):
    """Change password."""
    collection = await get_users_collection(request)
    
    # Get current user with password hash
    user_doc = await collection.find_one({"_id": user.id})
    if not user_doc:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Verify current password
    if not verify_password(password_request.current_password, user_doc["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )
    
    # Update password
    await collection.update_one(
        {"_id": user.id},
        {"$set": {
            "password_hash": get_password_hash(password_request.new_password),
            "updated_at": datetime.now()
        }}
    )
    
    return {"success": True}


# ============== Admin Endpoints ==============

class UserListResponse(BaseModel):
    """User list response."""
    id: str
    email: str
    name: str
    is_active: bool
    is_admin: bool
    created_at: datetime


class ProfileAccessEntry(BaseModel):
    """Profile access entry."""
    user_id: str
    profile_key: str
    granted_at: datetime = Field(default_factory=datetime.now)
    granted_by: str


class ProfileAccessMatrix(BaseModel):
    """Profile access matrix response."""
    users: list[UserListResponse]
    profiles: list[str]
    access: dict[str, list[str]]  # user_id -> list of profile_keys


class SetAccessRequest(BaseModel):
    """Set profile access request."""
    user_id: str
    profile_key: str
    has_access: bool


async def get_profile_access_collection(request: Request):
    """Get profile access collection."""
    return request.app.state.db.db["profile_access"]


@router.get("/users", response_model=list[UserListResponse])
async def list_users(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """List all users (admin only)."""
    collection = await get_users_collection(request)
    users = []
    async for doc in collection.find({}):
        users.append(UserListResponse(
            id=str(doc["_id"]),
            email=doc["email"],
            name=doc["name"],
            is_active=doc.get("is_active", True),
            is_admin=doc.get("is_admin", False),
            created_at=doc["created_at"]
        ))
    return users


@router.get("/access-matrix", response_model=ProfileAccessMatrix)
async def get_access_matrix(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """Get profile access matrix (admin only)."""
    users_collection = await get_users_collection(request)
    access_collection = await get_profile_access_collection(request)
    
    # Get all users
    users = []
    async for doc in users_collection.find({}):
        users.append(UserListResponse(
            id=str(doc["_id"]),
            email=doc["email"],
            name=doc["name"],
            is_active=doc.get("is_active", True),
            is_admin=doc.get("is_admin", False),
            created_at=doc["created_at"]
        ))
    
    # Get all profiles from profile manager
    from src.profile import get_profile_manager
    profiles = list(get_profile_manager().list_profiles().keys())
    
    # Get access entries
    access: dict[str, list[str]] = {}
    async for entry in access_collection.find({}):
        user_id = entry["user_id"]
        if user_id not in access:
            access[user_id] = []
        access[user_id].append(entry["profile_key"])
    
    # Admin users have access to all profiles
    for user in users:
        if user.is_admin:
            access[user.id] = profiles.copy()
    
    return ProfileAccessMatrix(
        users=users,
        profiles=profiles,
        access=access
    )


@router.post("/access")
async def set_profile_access(
    request: Request,
    access_request: SetAccessRequest,
    admin: UserResponse = Depends(require_admin)
):
    """Set profile access for a user (admin only)."""
    access_collection = await get_profile_access_collection(request)
    users_collection = await get_users_collection(request)
    
    # Verify user exists
    user_doc = await users_collection.find_one({"_id": access_request.user_id})
    if not user_doc:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Verify profile exists
    from src.profile import get_profile_manager
    profiles = get_profile_manager().list_profiles()
    if access_request.profile_key not in profiles:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    if access_request.has_access:
        # Grant access (upsert)
        await access_collection.update_one(
            {"user_id": access_request.user_id, "profile_key": access_request.profile_key},
            {"$set": {
                "user_id": access_request.user_id,
                "profile_key": access_request.profile_key,
                "granted_at": datetime.now(),
                "granted_by": admin.id
            }},
            upsert=True
        )
    else:
        # Revoke access
        await access_collection.delete_one({
            "user_id": access_request.user_id,
            "profile_key": access_request.profile_key
        })
    
    return {"success": True}


async def user_has_profile_access(request: Request, user_id: str, profile_key: str) -> bool:
    """Check if user has access to a profile."""
    # Get user to check admin status
    users_collection = await get_users_collection(request)
    user_doc = await users_collection.find_one({"_id": user_id})
    
    # Admins have access to all profiles
    if user_doc and user_doc.get("is_admin", False):
        return True
    
    # Check profile_access collection
    access_collection = await get_profile_access_collection(request)
    access = await access_collection.find_one({
        "user_id": user_id,
        "profile_key": profile_key
    })
    
    return access is not None


async def get_user_accessible_profiles(request: Request, user_id: str) -> list[str]:
    """Get list of profile keys the user has access to."""
    # Get user to check admin status
    users_collection = await get_users_collection(request)
    user_doc = await users_collection.find_one({"_id": user_id})
    
    # Admins have access to all profiles
    if user_doc and user_doc.get("is_admin", False):
        from src.profile import get_profile_manager
        return list(get_profile_manager().list_profiles().keys())
    
    # Get from profile_access collection
    access_collection = await get_profile_access_collection(request)
    profiles = []
    async for entry in access_collection.find({"user_id": user_id}):
        profiles.append(entry["profile_key"])
    
    return profiles


# ============== User Management Endpoints (Admin) ==============

class AdminCreateUserRequest(BaseModel):
    """Admin request to create a new user."""
    email: EmailStr
    name: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=6, max_length=100)
    is_admin: bool = False


class AdminUpdateUserRequest(BaseModel):
    """Admin request to update a user."""
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    is_admin: Optional[bool] = None
    new_password: Optional[str] = Field(None, min_length=6, max_length=100)


class UserStatusRequest(BaseModel):
    """Request to change user active status."""
    is_active: bool


@router.post("/users/create", response_model=UserListResponse)
async def admin_create_user(
    request: Request,
    create_request: AdminCreateUserRequest,
    admin: UserResponse = Depends(require_admin)
):
    """
    Create a new user (admin only).
    
    Allows admin to create users with email and password.
    """
    collection = await get_users_collection(request)
    
    # Check if email already exists
    existing = await collection.find_one({"email": create_request.email.lower()})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create user
    user = User(
        email=create_request.email.lower(),
        name=create_request.name,
        password_hash=get_password_hash(create_request.password),
        is_admin=create_request.is_admin
    )
    
    doc = user.model_dump()
    doc["_id"] = doc.pop("id")
    
    await collection.insert_one(doc)
    
    logger.info(f"Admin {admin.email} created user: {user.email}")
    
    return UserListResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        is_active=user.is_active,
        is_admin=user.is_admin,
        created_at=user.created_at
    )


@router.put("/users/{user_id}", response_model=UserListResponse)
async def admin_update_user(
    request: Request,
    user_id: str,
    update_request: AdminUpdateUserRequest,
    admin: UserResponse = Depends(require_admin)
):
    """
    Update a user (admin only).
    
    Allows admin to update user name, email, admin status, and password.
    """
    collection = await get_users_collection(request)
    
    # Get existing user
    user_doc = await collection.find_one({"_id": user_id})
    if not user_doc:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Prevent admin from removing their own admin status
    if user_id == admin.id and update_request.is_admin is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove your own admin status"
        )
    
    # Build update dict
    update_dict = {"updated_at": datetime.now()}
    
    if update_request.name is not None:
        update_dict["name"] = update_request.name
    
    if update_request.email is not None:
        # Check if email is already taken by another user
        existing = await collection.find_one({
            "email": update_request.email.lower(),
            "_id": {"$ne": user_id}
        })
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use by another user"
            )
        update_dict["email"] = update_request.email.lower()
    
    if update_request.is_admin is not None:
        update_dict["is_admin"] = update_request.is_admin
    
    if update_request.new_password is not None:
        update_dict["password_hash"] = get_password_hash(update_request.new_password)
    
    await collection.update_one({"_id": user_id}, {"$set": update_dict})
    
    # Get updated user
    updated_doc = await collection.find_one({"_id": user_id})
    
    logger.info(f"Admin {admin.email} updated user: {updated_doc['email']}")
    
    return UserListResponse(
        id=str(updated_doc["_id"]),
        email=updated_doc["email"],
        name=updated_doc["name"],
        is_active=updated_doc.get("is_active", True),
        is_admin=updated_doc.get("is_admin", False),
        created_at=updated_doc["created_at"]
    )


@router.put("/users/{user_id}/status")
async def admin_set_user_status(
    request: Request,
    user_id: str,
    status_request: UserStatusRequest,
    admin: UserResponse = Depends(require_admin)
):
    """
    Activate or deactivate a user account (admin only).
    
    Deactivated users cannot log in but their data is preserved.
    """
    collection = await get_users_collection(request)
    
    # Get user
    user_doc = await collection.find_one({"_id": user_id})
    if not user_doc:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Prevent admin from deactivating themselves
    if user_id == admin.id and not status_request.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account"
        )
    
    await collection.update_one(
        {"_id": user_id},
        {"$set": {
            "is_active": status_request.is_active,
            "updated_at": datetime.now()
        }}
    )
    
    action = "activated" if status_request.is_active else "deactivated"
    logger.info(f"Admin {admin.email} {action} user: {user_doc['email']}")
    
    return {"success": True, "message": f"User {action} successfully"}


@router.delete("/users/{user_id}")
async def admin_delete_user(
    request: Request,
    user_id: str,
    admin: UserResponse = Depends(require_admin)
):
    """
    Delete a user (admin only).
    
    Permanently removes the user account and their profile access.
    """
    collection = await get_users_collection(request)
    access_collection = await get_profile_access_collection(request)
    
    # Get user
    user_doc = await collection.find_one({"_id": user_id})
    if not user_doc:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Prevent admin from deleting themselves
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )
    
    # Delete user's profile access entries
    await access_collection.delete_many({"user_id": user_id})
    
    # Delete user
    await collection.delete_one({"_id": user_id})
    
    logger.info(f"Admin {admin.email} deleted user: {user_doc['email']}")
    
    return {"success": True, "message": "User deleted successfully"}


# ============== API Key Management Endpoints ==============

@router.get("/api-keys", response_model=List[APIKeyResponse])
async def list_api_keys(
    request: Request,
    user: UserResponse = Depends(require_auth)
):
    """
    List all API keys for the current user.
    
    Returns key metadata without the actual key value.
    """
    collection = await get_api_keys_collection(request)
    
    keys = []
    async for doc in collection.find({"user_id": user.id}).sort("created_at", -1):
        keys.append(APIKeyResponse(
            id=str(doc["_id"]),
            name=doc["name"],
            key_prefix=doc["key_prefix"],
            created_at=doc["created_at"],
            last_used_at=doc.get("last_used_at"),
            expires_at=doc.get("expires_at"),
            is_active=doc.get("is_active", True),
            scopes=doc.get("scopes", ["read", "write"])
        ))
    
    return keys


@router.post("/api-keys", response_model=APIKeyCreatedResponse)
async def create_api_key(
    request: Request,
    key_request: APIKeyCreate,
    user: UserResponse = Depends(require_auth)
):
    """
    Create a new API key.
    
    The full key is only returned once at creation time.
    Store it securely - it cannot be retrieved later.
    """
    collection = await get_api_keys_collection(request)
    
    # Generate a secure API key
    plain_key = generate_api_key()
    key_hash = hash_api_key(plain_key)
    key_prefix = get_api_key_prefix(plain_key)
    
    # Calculate expiration if specified
    expires_at = None
    if key_request.expires_in_days:
        expires_at = datetime.now() + timedelta(days=key_request.expires_in_days)
    
    # Create the API key record
    api_key = APIKey(
        user_id=user.id,
        name=key_request.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        expires_at=expires_at,
        scopes=key_request.scopes
    )
    
    doc = api_key.model_dump()
    doc["_id"] = doc.pop("id")
    
    await collection.insert_one(doc)
    
    logger.info(f"User {user.email} created API key: {key_request.name}")
    
    return APIKeyCreatedResponse(
        id=api_key.id,
        name=api_key.name,
        key=plain_key,  # Only returned once!
        key_prefix=key_prefix,
        created_at=api_key.created_at,
        expires_at=expires_at,
        scopes=api_key.scopes
    )


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    request: Request,
    key_id: str,
    user: UserResponse = Depends(require_auth)
):
    """
    Revoke (delete) an API key.
    
    The key will immediately stop working.
    """
    collection = await get_api_keys_collection(request)
    
    # Find the key and verify ownership
    key_doc = await collection.find_one({"_id": key_id})
    if not key_doc:
        raise HTTPException(status_code=404, detail="API key not found")
    
    if key_doc["user_id"] != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized to revoke this key")
    
    await collection.delete_one({"_id": key_id})
    
    logger.info(f"User {user.email} revoked API key: {key_doc['name']}")
    
    return {"success": True, "message": "API key revoked successfully"}


@router.put("/api-keys/{key_id}/toggle")
async def toggle_api_key(
    request: Request,
    key_id: str,
    user: UserResponse = Depends(require_auth)
):
    """
    Enable or disable an API key without deleting it.
    """
    collection = await get_api_keys_collection(request)
    
    # Find the key and verify ownership
    key_doc = await collection.find_one({"_id": key_id})
    if not key_doc:
        raise HTTPException(status_code=404, detail="API key not found")
    
    if key_doc["user_id"] != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized to modify this key")
    
    new_status = not key_doc.get("is_active", True)
    await collection.update_one(
        {"_id": key_id},
        {"$set": {"is_active": new_status}}
    )
    
    action = "enabled" if new_status else "disabled"
    logger.info(f"User {user.email} {action} API key: {key_doc['name']}")
    
    return {"success": True, "is_active": new_status, "message": f"API key {action}"}


# Admin endpoint to list all API keys
@router.get("/admin/api-keys", response_model=List[APIKeyResponse])
async def admin_list_all_api_keys(
    request: Request,
    admin: UserResponse = Depends(require_admin)
):
    """
    List all API keys in the system (admin only).
    """
    collection = await get_api_keys_collection(request)
    users_collection = await get_users_collection(request)
    
    keys = []
    async for doc in collection.find({}).sort("created_at", -1):
        # Get user email for context
        user_doc = await users_collection.find_one({"_id": doc["user_id"]})
        user_email = user_doc["email"] if user_doc else "unknown"
        
        keys.append(APIKeyResponse(
            id=str(doc["_id"]),
            name=f"{doc['name']} ({user_email})",
            key_prefix=doc["key_prefix"],
            created_at=doc["created_at"],
            last_used_at=doc.get("last_used_at"),
            expires_at=doc.get("expires_at"),
            is_active=doc.get("is_active", True),
            scopes=doc.get("scopes", ["read", "write"])
        ))
    
    return keys
