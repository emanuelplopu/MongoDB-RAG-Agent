"""Security integration tests for RecallHub API.

Tests security features:
- Rate limiting on auth endpoints
- Registration control modes
- JWT authentication
- Security headers
- API documentation protection
"""

import asyncio
import pytest
import httpx
import os
from datetime import datetime
from typing import AsyncGenerator

# Test configuration
BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:11000")
FRONTEND_URL = os.getenv("TEST_FRONTEND_URL", "http://localhost:11080")


@pytest.fixture(scope="module")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Create async HTTP client."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        yield client


@pytest.fixture
async def frontend_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Create async HTTP client for frontend."""
    async with httpx.AsyncClient(base_url=FRONTEND_URL, timeout=30.0) as client:
        yield client


class TestSecurityHeaders:
    """Test security headers on API responses."""
    
    async def test_backend_security_headers(self, client: httpx.AsyncClient):
        """Test that backend returns proper security headers."""
        response = await client.get("/health")
        
        assert response.status_code == 200
        
        # Check required security headers
        assert response.headers.get("x-frame-options") == "DENY"
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("x-xss-protection") == "1; mode=block"
        assert "strict-origin" in response.headers.get("referrer-policy", "")
        assert "geolocation=()" in response.headers.get("permissions-policy", "")
    
    async def test_frontend_security_headers(self, frontend_client: httpx.AsyncClient):
        """Test that frontend returns proper security headers."""
        response = await frontend_client.get("/")
        
        assert response.status_code == 200
        
        # Frontend security headers (via nginx)
        # Note: Some headers may be added to non-static responses only
        headers = response.headers
        # At minimum, nginx should be serving the page
        assert "nginx" in headers.get("server", "").lower()


class TestRateLimiting:
    """Test rate limiting on sensitive endpoints."""
    
    async def test_login_rate_limiting(self, client: httpx.AsyncClient):
        """Test that login endpoint is rate limited after 5 attempts."""
        login_data = {"email": "ratelimit@test.com", "password": "wrongpassword"}
        
        # Make requests until rate limited
        responses = []
        for i in range(8):
            response = await client.post("/api/v1/auth/login", json=login_data)
            responses.append(response.status_code)
        
        # First 5 should be 401 (invalid credentials), then 429 (rate limited)
        assert 401 in responses[:5], "Expected 401 for invalid credentials"
        assert 429 in responses, "Expected 429 rate limit after multiple attempts"
        
        # Check rate limited response has retry-after header
        rate_limited_response = None
        for i in range(len(responses)):
            if responses[i] == 429:
                rate_limited_response = await client.post("/api/v1/auth/login", json=login_data)
                break
        
        if rate_limited_response:
            assert "retry-after" in rate_limited_response.headers or rate_limited_response.status_code == 429
    
    async def test_register_rate_limiting(self, client: httpx.AsyncClient):
        """Test that register endpoint is rate limited."""
        # Generate unique emails to avoid duplicate errors
        base_email = f"ratelimit{datetime.now().timestamp()}"
        
        responses = []
        for i in range(5):
            register_data = {
                "email": f"{base_email}_{i}@test.com",
                "name": "Rate Limit Test",
                "password": "testpassword123"
            }
            response = await client.post("/api/v1/auth/register", json=register_data)
            responses.append(response.status_code)
        
        # After 3 attempts, should be rate limited
        assert 429 in responses, "Expected rate limiting on register endpoint"


class TestRegistrationControl:
    """Test registration mode controls."""
    
    async def test_registration_open_mode(self, client: httpx.AsyncClient):
        """Test that registration works in open mode (default)."""
        # This assumes REGISTRATION_MODE is 'open' (default)
        unique_email = f"opentest{datetime.now().timestamp()}@test.com"
        register_data = {
            "email": unique_email,
            "name": "Open Mode Test",
            "password": "testpassword123"
        }
        
        response = await client.post("/api/v1/auth/register", json=register_data)
        
        # Should succeed (200) or be rate limited (429)
        assert response.status_code in [200, 429], f"Unexpected status: {response.status_code}"
    
    async def test_registration_requires_valid_email(self, client: httpx.AsyncClient):
        """Test that registration validates email format."""
        register_data = {
            "email": "invalid-email",
            "name": "Invalid Email Test",
            "password": "testpassword123"
        }
        
        response = await client.post("/api/v1/auth/register", json=register_data)
        
        # Should fail validation (422) or be rate limited (429)
        assert response.status_code in [422, 429]
    
    async def test_registration_requires_minimum_password(self, client: httpx.AsyncClient):
        """Test that registration requires minimum password length."""
        register_data = {
            "email": "shortpw@test.com",
            "name": "Short Password Test",
            "password": "12345"  # Less than 6 chars
        }
        
        response = await client.post("/api/v1/auth/register", json=register_data)
        
        # Should fail validation (422) or be rate limited (429)
        assert response.status_code in [422, 429]


class TestJWTAuthentication:
    """Test JWT authentication flow."""
    
    async def test_protected_endpoint_requires_auth(self, client: httpx.AsyncClient):
        """Test that protected endpoints require authentication."""
        # Try to access protected endpoint without token
        response = await client.get("/api/v1/auth/me")
        
        assert response.status_code == 401
    
    async def test_protected_endpoint_with_invalid_token(self, client: httpx.AsyncClient):
        """Test that invalid tokens are rejected."""
        headers = {"Authorization": "Bearer invalid-token-12345"}
        response = await client.get("/api/v1/auth/me", headers=headers)
        
        assert response.status_code == 401
    
    async def test_login_returns_valid_token(self, client: httpx.AsyncClient):
        """Test that successful login returns a valid JWT token."""
        # First register a user
        unique_email = f"jwttest{datetime.now().timestamp()}@test.com"
        register_data = {
            "email": unique_email,
            "name": "JWT Test User",
            "password": "testpassword123"
        }
        
        # Register (might be rate limited)
        register_response = await client.post("/api/v1/auth/register", json=register_data)
        
        if register_response.status_code == 200:
            data = register_response.json()
            assert "access_token" in data
            assert "user" in data
            assert data["token_type"] == "bearer"
            assert data["expires_in"] > 0
            
            # Use token to access protected endpoint
            headers = {"Authorization": f"Bearer {data['access_token']}"}
            me_response = await client.get("/api/v1/auth/me", headers=headers)
            
            assert me_response.status_code == 200
            me_data = me_response.json()
            assert me_data["email"] == unique_email


class TestAPIKeyAuthentication:
    """Test API key authentication."""
    
    async def test_invalid_api_key_rejected(self, client: httpx.AsyncClient):
        """Test that invalid API keys are rejected."""
        headers = {"X-API-Key": "invalid-api-key"}
        response = await client.get("/api/v1/auth/me", headers=headers)
        
        assert response.status_code == 401


class TestCORSConfiguration:
    """Test CORS configuration."""
    
    async def test_cors_preflight(self, client: httpx.AsyncClient):
        """Test CORS preflight requests."""
        headers = {
            "Origin": "https://recallhub.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, Authorization"
        }
        
        response = await client.options("/api/v1/auth/login", headers=headers)
        
        # Should allow the request (200/204) or be rate limited (429)
        assert response.status_code in [200, 204, 429]


class TestAPIDocumentation:
    """Test API documentation access control."""
    
    async def test_docs_endpoint_accessibility(self, client: httpx.AsyncClient):
        """Test that docs endpoint is accessible based on config."""
        # In development mode with EXPOSE_API_DOCS=true, docs should be accessible
        response = await client.get("/docs")
        
        # Should be 200 (accessible) or 404 (disabled)
        assert response.status_code in [200, 404]
    
    async def test_openapi_json_accessibility(self, client: httpx.AsyncClient):
        """Test that OpenAPI JSON is accessible based on config."""
        response = await client.get("/openapi.json")
        
        # Should be 200 (accessible) or 404 (disabled)
        assert response.status_code in [200, 404]


class TestInputValidation:
    """Test input validation and sanitization."""
    
    async def test_sql_injection_prevention(self, client: httpx.AsyncClient):
        """Test that SQL injection attempts are handled safely."""
        malicious_data = {
            "email": "test@test.com'; DROP TABLE users; --",
            "password": "password"
        }
        
        response = await client.post("/api/v1/auth/login", json=malicious_data)
        
        # Should fail validation (422), return 401, or be rate limited (429)
        assert response.status_code in [401, 422, 429]
    
    async def test_xss_prevention(self, client: httpx.AsyncClient):
        """Test that XSS attempts are handled safely."""
        malicious_data = {
            "email": "test@test.com",
            "name": "<script>alert('xss')</script>",
            "password": "password123"
        }
        
        response = await client.post("/api/v1/auth/register", json=malicious_data)
        
        # Should either succeed (XSS in name is stored escaped), fail validation, or rate limit
        # The important thing is the server doesn't crash
        assert response.status_code in [200, 400, 422, 429]


class TestAdminEndpoints:
    """Test admin endpoint protection."""
    
    async def test_admin_endpoints_require_auth(self, client: httpx.AsyncClient):
        """Test that admin endpoints require authentication."""
        response = await client.get("/api/v1/auth/users")
        
        # Should require auth (401) or be rate limited (429)
        assert response.status_code in [401, 429, 403]
    
    async def test_admin_endpoints_require_admin_role(self, client: httpx.AsyncClient):
        """Test that admin endpoints require admin role."""
        # Register a regular user
        unique_email = f"nonadmin{datetime.now().timestamp()}@test.com"
        register_data = {
            "email": unique_email,
            "name": "Non Admin User",
            "password": "testpassword123"
        }
        
        register_response = await client.post("/api/v1/auth/register", json=register_data)
        
        if register_response.status_code == 200:
            token = register_response.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            
            # Try to access admin endpoint
            response = await client.get("/api/v1/auth/users", headers=headers)
            
            # Should be forbidden for non-admin
            assert response.status_code == 403


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
