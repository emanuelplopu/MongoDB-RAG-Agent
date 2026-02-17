"""Unit tests for security module.

Tests the security utilities:
- Rate limiter
- JWT secret validation
- Registration controls
- API docs protection
"""

import os
import pytest
import time
from unittest.mock import patch, MagicMock

from backend.core.security import (
    RateLimiter,
    rate_limiter,
    RATE_LIMITS,
    get_client_ip,
    validate_jwt_secret,
    generate_secure_secret,
    is_registration_enabled,
    get_registration_mode,
    validate_invite_code,
    should_expose_docs,
    get_docs_urls,
)


class TestRateLimiter:
    """Test the RateLimiter class."""
    
    def test_init(self):
        """Test rate limiter initialization."""
        limiter = RateLimiter()
        assert limiter._requests == {}
        assert limiter._lock_until == {}
    
    def test_is_rate_limited_under_limit(self):
        """Test that requests under limit are allowed."""
        limiter = RateLimiter()
        
        # Make 4 requests (limit is 5)
        for _ in range(4):
            is_limited, _ = limiter.is_rate_limited("test_key", max_requests=5, window_seconds=60)
            assert not is_limited
            limiter.record_request("test_key")
    
    def test_is_rate_limited_at_limit(self):
        """Test that requests at limit are blocked."""
        limiter = RateLimiter()
        
        # Make 5 requests (limit is 5)
        for _ in range(5):
            limiter.record_request("test_key")
        
        is_limited, retry_after = limiter.is_rate_limited("test_key", max_requests=5, window_seconds=60)
        assert is_limited
        assert retry_after is not None
        assert retry_after > 0
    
    def test_rate_limit_different_keys(self):
        """Test that different keys have independent limits."""
        limiter = RateLimiter()
        
        # Fill up key1
        for _ in range(5):
            limiter.record_request("key1")
        
        # key1 should be limited
        is_limited_1, _ = limiter.is_rate_limited("key1", max_requests=5, window_seconds=60)
        assert is_limited_1
        
        # key2 should not be limited
        is_limited_2, _ = limiter.is_rate_limited("key2", max_requests=5, window_seconds=60)
        assert not is_limited_2
    
    def test_record_failed_attempt_lockout(self):
        """Test that failed attempts can trigger lockout."""
        limiter = RateLimiter()
        
        # Record failed attempt with lockout
        limiter.record_failed_attempt("test_key", lockout_seconds=5)
        
        # Should be locked out
        is_limited, retry_after = limiter.is_rate_limited("test_key", max_requests=100, window_seconds=60)
        assert is_limited
        assert retry_after is not None
        assert retry_after <= 5
    
    def test_rate_limit_expires(self):
        """Test that rate limits expire after window."""
        limiter = RateLimiter()
        
        # Use a very short window
        for _ in range(3):
            limiter.record_request("test_key")
        
        is_limited, _ = limiter.is_rate_limited("test_key", max_requests=3, window_seconds=1)
        assert is_limited
        
        # Wait for window to expire
        time.sleep(1.1)
        
        is_limited, _ = limiter.is_rate_limited("test_key", max_requests=3, window_seconds=1)
        assert not is_limited


class TestClientIPExtraction:
    """Test client IP extraction from requests."""
    
    def test_get_client_ip_direct(self):
        """Test IP extraction from direct connection."""
        mock_request = MagicMock()
        mock_request.headers = {}
        mock_request.client.host = "192.168.1.100"
        
        ip = get_client_ip(mock_request)
        assert ip == "192.168.1.100"
    
    def test_get_client_ip_x_forwarded_for(self):
        """Test IP extraction from X-Forwarded-For header."""
        mock_request = MagicMock()
        mock_request.headers = {"X-Forwarded-For": "10.0.0.1, 192.168.1.1"}
        mock_request.client.host = "127.0.0.1"
        
        ip = get_client_ip(mock_request)
        assert ip == "10.0.0.1"
    
    def test_get_client_ip_x_real_ip(self):
        """Test IP extraction from X-Real-IP header."""
        mock_request = MagicMock()
        mock_request.headers = {"X-Real-IP": "10.0.0.2"}
        mock_request.client.host = "127.0.0.1"
        
        ip = get_client_ip(mock_request)
        assert ip == "10.0.0.2"
    
    def test_get_client_ip_no_client(self):
        """Test IP extraction when client is None."""
        mock_request = MagicMock()
        mock_request.headers = {}
        mock_request.client = None
        
        ip = get_client_ip(mock_request)
        assert ip == "unknown"


class TestJWTSecretValidation:
    """Test JWT secret validation."""
    
    def test_validate_jwt_secret_default_in_dev(self):
        """Test that default secret is allowed in development."""
        with patch.dict(os.environ, {"APP_ENV": "development", "JWT_SECRET_KEY": ""}, clear=False):
            secret = validate_jwt_secret()
            assert secret == "recallhub-secret-key-change-in-production"
    
    def test_validate_jwt_secret_custom(self):
        """Test that custom secret is returned."""
        custom_secret = "my-super-secure-secret-key-that-is-long-enough"
        with patch.dict(os.environ, {"JWT_SECRET_KEY": custom_secret}, clear=False):
            secret = validate_jwt_secret()
            assert secret == custom_secret
    
    def test_validate_jwt_secret_production_requires_custom(self):
        """Test that production requires a custom secret."""
        with patch.dict(os.environ, {"APP_ENV": "production", "JWT_SECRET_KEY": ""}, clear=False):
            with pytest.raises(ValueError, match="JWT_SECRET_KEY must be set"):
                validate_jwt_secret()
    
    def test_generate_secure_secret(self):
        """Test secure secret generation."""
        secret = generate_secure_secret()
        
        assert len(secret) == 64  # 32 bytes = 64 hex chars
        assert secret.isalnum()  # Only alphanumeric
        
        # Generate another and ensure they're different
        secret2 = generate_secure_secret()
        assert secret != secret2


class TestRegistrationControl:
    """Test registration control functions."""
    
    def test_is_registration_enabled_default(self):
        """Test default registration enabled state."""
        with patch.dict(os.environ, {}, clear=False):
            # Remove ALLOW_REGISTRATION if present
            os.environ.pop("ALLOW_REGISTRATION", None)
            assert is_registration_enabled() == True
    
    def test_is_registration_enabled_true(self):
        """Test registration enabled when set to true."""
        with patch.dict(os.environ, {"ALLOW_REGISTRATION": "true"}, clear=False):
            assert is_registration_enabled() == True
    
    def test_is_registration_enabled_false(self):
        """Test registration disabled when set to false."""
        with patch.dict(os.environ, {"ALLOW_REGISTRATION": "false"}, clear=False):
            assert is_registration_enabled() == False
    
    def test_get_registration_mode_default(self):
        """Test default registration mode."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REGISTRATION_MODE", None)
            assert get_registration_mode() == "open"
    
    def test_get_registration_mode_invite(self):
        """Test invite registration mode."""
        with patch.dict(os.environ, {"REGISTRATION_MODE": "invite"}, clear=False):
            assert get_registration_mode() == "invite"
    
    def test_get_registration_mode_closed(self):
        """Test closed registration mode."""
        with patch.dict(os.environ, {"REGISTRATION_MODE": "closed"}, clear=False):
            assert get_registration_mode() == "closed"
    
    def test_validate_invite_code_empty(self):
        """Test that empty invite code is invalid."""
        assert validate_invite_code("") == False
        assert validate_invite_code(None) == False
    
    def test_validate_invite_code_single(self):
        """Test single invite code validation."""
        with patch.dict(os.environ, {"INVITE_CODE": "secret-code-123"}, clear=False):
            assert validate_invite_code("secret-code-123") == True
            assert validate_invite_code("wrong-code") == False
    
    def test_validate_invite_code_multiple(self):
        """Test multiple invite codes validation."""
        with patch.dict(os.environ, {"INVITE_CODES": "code1,code2,code3"}, clear=False):
            assert validate_invite_code("code1") == True
            assert validate_invite_code("code2") == True
            assert validate_invite_code("code3") == True
            assert validate_invite_code("code4") == False


class TestAPIDocsProtection:
    """Test API documentation protection."""
    
    def test_should_expose_docs_default_dev(self):
        """Test docs exposed by default in development."""
        with patch.dict(os.environ, {"APP_ENV": "development"}, clear=False):
            os.environ.pop("EXPOSE_API_DOCS", None)
            assert should_expose_docs() == True
    
    def test_should_expose_docs_default_prod(self):
        """Test docs hidden by default in production."""
        with patch.dict(os.environ, {"APP_ENV": "production"}, clear=False):
            os.environ.pop("EXPOSE_API_DOCS", None)
            assert should_expose_docs() == False
    
    def test_should_expose_docs_explicit_true(self):
        """Test explicit docs exposure."""
        with patch.dict(os.environ, {"EXPOSE_API_DOCS": "true"}, clear=False):
            assert should_expose_docs() == True
    
    def test_should_expose_docs_explicit_false(self):
        """Test explicit docs hiding."""
        with patch.dict(os.environ, {"EXPOSE_API_DOCS": "false"}, clear=False):
            assert should_expose_docs() == False
    
    def test_get_docs_urls_exposed(self):
        """Test docs URLs when exposed."""
        with patch.dict(os.environ, {"EXPOSE_API_DOCS": "true"}, clear=False):
            urls = get_docs_urls()
            assert urls["docs_url"] == "/docs"
            assert urls["redoc_url"] == "/redoc"
            assert urls["openapi_url"] == "/openapi.json"
    
    def test_get_docs_urls_hidden(self):
        """Test docs URLs when hidden."""
        with patch.dict(os.environ, {"EXPOSE_API_DOCS": "false"}, clear=False):
            urls = get_docs_urls()
            assert urls["docs_url"] is None
            assert urls["redoc_url"] is None
            assert urls["openapi_url"] is None


class TestRateLimitConfiguration:
    """Test rate limit configuration."""
    
    def test_auth_login_rate_limit(self):
        """Test auth login rate limit config."""
        config = RATE_LIMITS["auth_login"]
        assert config["max_requests"] == 5
        assert config["window_seconds"] == 60
    
    def test_auth_register_rate_limit(self):
        """Test auth register rate limit config."""
        config = RATE_LIMITS["auth_register"]
        assert config["max_requests"] == 3
        assert config["window_seconds"] == 300
    
    def test_api_general_rate_limit(self):
        """Test general API rate limit config."""
        config = RATE_LIMITS["api_general"]
        assert config["max_requests"] == 100
        assert config["window_seconds"] == 60


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
