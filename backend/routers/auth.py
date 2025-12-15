"""Authentication router - User registration, login, and JWT management."""

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Depends, status
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


async def get_users_collection(request: Request):
    """Get users collection."""
    return request.app.state.db.db["users"]


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[UserResponse]:
    """Get current user from JWT token."""
    if not credentials:
        return None
    
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
    except JWTError:
        return None
    
    collection = await get_users_collection(request)
    user_doc = await collection.find_one({"_id": user_id})
    
    if not user_doc:
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
