"""Security middleware and utilities for RecallHub.

This module provides:
- Rate limiting for authentication endpoints
- Security headers middleware
- JWT signing key validation (refuses public or missing keys where required)
- Registration control
"""

import os
import time
import hashlib
import logging
import secrets
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from functools import wraps

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


# ============================================
# Rate Limiting
# ============================================

class RateLimiter:
    """In-memory rate limiter with sliding window.
    
    Tracks requests per client IP with configurable limits and windows.
    """
    
    def __init__(self):
        # Store: {key: [(timestamp, count), ...]}
        self._requests: Dict[str, list] = defaultdict(list)
        self._lock_until: Dict[str, float] = {}  # Temporary lockouts
    
    def _clean_old_requests(self, key: str, window_seconds: int):
        """Remove requests outside the time window."""
        cutoff = time.time() - window_seconds
        self._requests[key] = [
            (ts, count) for ts, count in self._requests[key]
            if ts > cutoff
        ]
    
    def is_rate_limited(
        self,
        key: str,
        max_requests: int,
        window_seconds: int
    ) -> Tuple[bool, Optional[int]]:
        """Check if a key is rate limited.
        
        Args:
            key: Unique identifier (e.g., IP + endpoint)
            max_requests: Maximum requests allowed in window
            window_seconds: Time window in seconds
        
        Returns:
            Tuple of (is_limited, retry_after_seconds)
        """
        now = time.time()
        
        # Check for lockout
        if key in self._lock_until:
            if now < self._lock_until[key]:
                return True, int(self._lock_until[key] - now)
            else:
                del self._lock_until[key]
        
        # Clean old requests
        self._clean_old_requests(key, window_seconds)
        
        # Count requests in window
        total_requests = sum(count for _, count in self._requests[key])
        
        if total_requests >= max_requests:
            # Calculate retry-after
            if self._requests[key]:
                oldest = min(ts for ts, _ in self._requests[key])
                retry_after = int(oldest + window_seconds - now) + 1
            else:
                retry_after = window_seconds
            return True, retry_after
        
        return False, None
    
    def record_request(self, key: str):
        """Record a request for the given key."""
        now = time.time()
        self._requests[key].append((now, 1))
    
    def record_failed_attempt(self, key: str, lockout_seconds: int = 0):
        """Record a failed attempt, optionally applying a lockout.
        
        Args:
            key: Unique identifier
            lockout_seconds: If > 0, lock out the key for this many seconds
        """
        self.record_request(key)
        if lockout_seconds > 0:
            self._lock_until[key] = time.time() + lockout_seconds


# Global rate limiter instance
rate_limiter = RateLimiter()


# Rate limit configurations per endpoint type
RATE_LIMITS = {
    # Auth endpoints - strict limits to prevent brute force
    "auth_login": {"max_requests": 5, "window_seconds": 60},  # 5 per minute
    "auth_register": {"max_requests": 3, "window_seconds": 300},  # 3 per 5 minutes
    "auth_password": {"max_requests": 3, "window_seconds": 300},  # 3 per 5 minutes
    
    # API endpoints - more permissive
    "api_general": {"max_requests": 100, "window_seconds": 60},  # 100 per minute
    "api_search": {"max_requests": 30, "window_seconds": 60},  # 30 per minute
    "api_chat": {"max_requests": 20, "window_seconds": 60},  # 20 per minute
    "api_ingestion": {"max_requests": 60, "window_seconds": 60},  # 60 per minute (increased for UI polling during long ingestion)
    "api_ingestion_read": {"max_requests": 200, "window_seconds": 60},  # 200 per minute for read-only operations (lookup, list)
}


def get_client_ip(request: Request) -> str:
    """Extract client IP from request, handling proxies."""
    # Check for forwarded headers (when behind proxy/load balancer)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP (original client)
        return forwarded_for.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    # Fall back to direct connection
    if request.client:
        return request.client.host
    
    return "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce rate limits on API endpoints."""
    
    # Endpoints and their rate limit categories
    ENDPOINT_CATEGORIES = {
        "/api/v1/auth/login": "auth_login",
        "/api/v1/auth/register": "auth_register",
        "/api/v1/auth/me/password": "auth_password",
        "/api/v1/search": "api_search",
        "/api/v1/chat": "api_chat",
        "/api/v1/ingestion": "api_ingestion",
    }
    
    # Read-only ingestion paths that get higher rate limits
    INGESTION_READ_PATHS = {
        "/api/v1/ingestion/documents/lookup",
        "/api/v1/ingestion/documents/list",
        "/api/v1/ingestion/documents",
        "/api/v1/ingestion/stats",
        "/api/v1/ingestion/status",
    }
    
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks
        if request.url.path in {"/health", "/", "/api/v1/system/health"}:
            return await call_next(request)
        
        # Get client IP
        client_ip = get_client_ip(request)
        
        # Determine rate limit category
        category = "api_general"
        
        # Check for read-only ingestion paths first (higher limits)
        path = request.url.path
        if path.startswith("/api/v1/ingestion") and request.method == "GET":
            # Check if it's a read-only endpoint
            for read_path in self.INGESTION_READ_PATHS:
                if path.startswith(read_path):
                    category = "api_ingestion_read"
                    break
            else:
                category = "api_ingestion"
        else:
            for endpoint_prefix, cat in self.ENDPOINT_CATEGORIES.items():
                if path.startswith(endpoint_prefix):
                    category = cat
                    break
        
        # Get rate limit config
        config = RATE_LIMITS.get(category, RATE_LIMITS["api_general"])
        
        # Create rate limit key
        rate_key = f"{client_ip}:{category}"
        
        # Check rate limit
        is_limited, retry_after = rate_limiter.is_rate_limited(
            rate_key,
            config["max_requests"],
            config["window_seconds"]
        )
        
        if is_limited:
            logger.warning(
                f"Rate limit exceeded for {client_ip} on {category} "
                f"(retry after {retry_after}s)"
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "rate_limit_exceeded",
                    "message": "Too many requests. Please try again later.",
                    "retry_after": retry_after,
                },
                headers={"Retry-After": str(retry_after)}
            )
        
        # Record the request
        rate_limiter.record_request(rate_key)
        
        # Process request
        response = await call_next(request)
        
        # On auth failures, record failed attempt
        if category.startswith("auth_") and response.status_code == 401:
            # Apply progressive lockout for repeated failures
            fail_key = f"{client_ip}:auth_failures"
            rate_limiter.record_request(fail_key)
            
            # Check for brute force (>10 failures in 10 minutes)
            is_brute_force, _ = rate_limiter.is_rate_limited(
                fail_key, max_requests=10, window_seconds=600
            )
            if is_brute_force:
                logger.warning(f"Brute force detected from {client_ip}, applying 5min lockout")
                rate_limiter.record_failed_attempt(rate_key, lockout_seconds=300)
        
        return response


# ============================================
# Security Headers Middleware
# ============================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # Only add headers if not already set (nginx might set some)
        headers_to_add = {
            # Prevent clickjacking
            "X-Frame-Options": "DENY",
            # Prevent MIME type sniffing
            "X-Content-Type-Options": "nosniff",
            # XSS Protection (legacy browsers)
            "X-XSS-Protection": "1; mode=block",
            # Referrer policy
            "Referrer-Policy": "strict-origin-when-cross-origin",
            # Permissions policy
            "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        }
        
        for header, value in headers_to_add.items():
            if header not in response.headers:
                response.headers[header] = value
        
        return response


# ============================================
# JWT Secret Validation
# ============================================

#: Signing keys that are public and must never sign a login token: the fallback
#: that releases before 2026-10 used (it is in the public repository).
PUBLIC_JWT_SECRETS: frozenset[str] = frozenset({"recallhub-secret-key-change-in-production"})

#: Shortest accepted signing key. The installers generate 64 hex characters.
MIN_JWT_SECRET_LENGTH = 32


class InsecureJwtSecretError(ValueError):
    """The JWT signing key is missing, public or too short where one is required."""


def jwt_secret_required() -> bool:
    """Return True when the API must not run without a signing key of its own.

    That is the case for ``APP_ENV=production`` and whenever
    ``JWT_SECRET_REQUIRED`` is true; docker-compose.yml sets the latter for both
    tenant backends.
    """
    if os.getenv("APP_ENV", "development").strip().lower() == "production":
        return True
    return os.getenv("JWT_SECRET_REQUIRED", "false").strip().lower() in ("true", "1", "yes", "on")


def validate_jwt_secret() -> str:
    """Validate and return the JWT signing key from ``JWT_SECRET_KEY``.

    Where a key is required (``jwt_secret_required``), a missing, public or
    short key raises. Elsewhere (local development, tests) a missing or public
    key is replaced by a random key for this process, so no token ever verifies
    against a value published in the repository; such sessions end when the
    process restarts.

    Returns:
        The signing key to use.

    Raises:
        InsecureJwtSecretError: If a key is required and the configured one is
            missing, public or shorter than ``MIN_JWT_SECRET_LENGTH``.
    """
    secret = os.getenv("JWT_SECRET_KEY", "").strip()
    required = jwt_secret_required()
    tenant_variable = f"{os.getenv('TENANT_ID', 'recallhub').strip().upper()}_JWT_SECRET_KEY"
    how_to_fix = (
        f"Set {tenant_variable} in the install's .env (docker-compose.yml passes it to the "
        "backend as JWT_SECRET_KEY; installer/scripts/Initialize-JwtSecrets.ps1 generates it, "
        "-Hardened writes secrets.env of the hardened installer) and recreate the backend "
        "container. By hand: python -c \"import secrets; print(secrets.token_hex(32))\""
    )

    if not secret or secret in PUBLIC_JWT_SECRETS:
        if required:
            logger.critical(
                "SECURITY: JWT_SECRET_KEY is not set or is the public default; "
                "refusing to sign login tokens with it."
            )
            raise InsecureJwtSecretError(
                f"JWT_SECRET_KEY must be set to a secure value of its own. {how_to_fix}"
            )
        logger.warning(
            "JWT_SECRET_KEY is not set or is the public default: signing login tokens with a "
            "random key for this process, so sessions end when the API restarts. %s",
            how_to_fix,
        )
        return generate_secure_secret()

    if len(secret) < MIN_JWT_SECRET_LENGTH:
        if required:
            raise InsecureJwtSecretError(
                f"JWT_SECRET_KEY must be at least {MIN_JWT_SECRET_LENGTH} characters long. {how_to_fix}"
            )
        logger.warning("JWT_SECRET_KEY is shorter than recommended (%d+ chars)", MIN_JWT_SECRET_LENGTH)

    return secret


_jwt_secret: Optional[str] = None


def get_jwt_secret() -> str:
    """Return this process's JWT signing key, validating it on first use.

    Raises:
        InsecureJwtSecretError: See ``validate_jwt_secret``.
    """
    global _jwt_secret
    if _jwt_secret is None:
        _jwt_secret = validate_jwt_secret()
    return _jwt_secret


def reset_jwt_secret() -> None:
    """Forget the cached signing key so the next use re-reads ``JWT_SECRET_KEY`` (tests)."""
    global _jwt_secret
    _jwt_secret = None


def generate_secure_secret() -> str:
    """Generate a cryptographically secure secret key."""
    return secrets.token_hex(32)


# ============================================
# Registration Control
# ============================================

def is_registration_enabled() -> bool:
    """Check if public registration is enabled.
    
    Controlled by ALLOW_REGISTRATION environment variable.
    Default: True (open registration)
    """
    value = os.getenv("ALLOW_REGISTRATION", "true").lower()
    return value in ("true", "1", "yes", "on")


def get_registration_mode() -> str:
    """Get the current registration mode.
    
    Modes:
    - "open": Anyone can register
    - "invite": Requires invite code
    - "closed": No public registration (admin creates users)
    """
    return os.getenv("REGISTRATION_MODE", "open").lower()


def validate_invite_code(code: str) -> bool:
    """Validate an invite code for registration.
    
    Invite codes can be:
    - Set via INVITE_CODES environment variable (comma-separated)
    - Or a single INVITE_CODE for simplicity
    """
    if not code:
        return False
    
    # Check single invite code
    single_code = os.getenv("INVITE_CODE", "")
    if single_code and secrets.compare_digest(code, single_code):
        return True
    
    # Check list of invite codes
    codes_str = os.getenv("INVITE_CODES", "")
    if codes_str:
        valid_codes = [c.strip() for c in codes_str.split(",") if c.strip()]
        for valid_code in valid_codes:
            if secrets.compare_digest(code, valid_code):
                return True
    
    return False


# ============================================
# API Documentation Protection
# ============================================

def should_expose_docs() -> bool:
    """Check if API documentation should be exposed.
    
    Controlled by EXPOSE_API_DOCS environment variable.
    Default: True in development, False in production
    """
    is_production = os.getenv("APP_ENV", "development").lower() == "production"
    default = "false" if is_production else "true"
    value = os.getenv("EXPOSE_API_DOCS", default).lower()
    return value in ("true", "1", "yes", "on")


def get_docs_urls() -> dict:
    """Get documentation URLs based on environment.
    
    Returns:
        Dict with docs_url, redoc_url, openapi_url (None if disabled)
    """
    if should_expose_docs():
        return {
            "docs_url": "/docs",
            "redoc_url": "/redoc",
            "openapi_url": "/openapi.json",
        }
    else:
        return {
            "docs_url": None,
            "redoc_url": None,
            "openapi_url": None,
        }
