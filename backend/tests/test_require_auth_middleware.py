"""Tests for the server-side login enforcement (REQUIRE_AUTH).

Covers backend/core/auth_middleware.py in isolation with a tiny FastAPI app
and the tenant config endpoint that advertises the flag to the frontend.
"""

from __future__ import annotations

from typing import Optional

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.core.auth_middleware import (
    DEFAULT_PUBLIC_PATHS,
    RequireAuthMiddleware,
    extract_bearer_credentials,
)


VALID_TOKEN = "jwt-for-alex"
VALID_API_KEY = "rh_api_key_123"


async def fake_resolver(request: Request) -> Optional[dict]:
    """Resolve a user the way the auth router would, from header or SSE token."""
    credentials = extract_bearer_credentials(request)
    if credentials and credentials.credentials == VALID_TOKEN:
        return {"email": "alex@example.com", "via": "jwt"}
    if request.headers.get("x-api-key") == VALID_API_KEY:
        return {"email": "alex@example.com", "via": "api-key"}
    return None


async def failing_resolver(request: Request) -> Optional[dict]:
    raise RuntimeError("database down")


def build_app(enabled: bool = True, resolver=fake_resolver) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequireAuthMiddleware, resolve_user=resolver, enabled=enabled)

    @app.get("/")
    async def root():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/api/v1/system/health")
    async def system_health():
        return {"status": "healthy"}

    @app.get("/api/v1/tenant/config")
    async def tenant_config():
        return {"tenant_id": "quellex"}

    @app.post("/api/v1/auth/login")
    async def login():
        return {"access_token": VALID_TOKEN}

    @app.get("/api/v1/search")
    async def search(request: Request):
        return {"user": getattr(request.state, "user", None)}

    @app.get("/api/v1/ingestion/logs/stream")
    async def logs_stream(request: Request):
        return {"user": request.state.user}

    @app.get("/docs")
    async def docs():
        return {"docs": True}

    @app.get("/openapi.json")
    async def openapi():
        return {"openapi": "3.1.0"}

    @app.get("/status/page")
    async def status_page():
        return {"status": "page"}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_app())


class TestPublicAllowlist:
    @pytest.mark.parametrize(
        "path",
        ["/", "/health", "/api/v1/system/health", "/api/v1/tenant/config"],
    )
    def test_public_paths_need_no_session(self, client: TestClient, path: str):
        assert client.get(path).status_code == 200

    @pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
    def test_api_docs_are_not_public(self, client: TestClient, path: str):
        assert client.get(path).status_code == 401

    def test_custom_public_prefix(self):
        app = FastAPI()
        app.add_middleware(
            RequireAuthMiddleware, resolve_user=fake_resolver, public_prefixes=("/status",)
        )

        @app.get("/status/page")
        async def status_page():
            return {"status": "page"}

        @app.get("/statusx")
        async def statusx():
            return {"status": "x"}

        client = TestClient(app)
        assert client.get("/status/page").status_code == 200
        assert client.get("/statusx").status_code == 401

    def test_login_is_public(self, client: TestClient):
        assert client.post("/api/v1/auth/login").status_code == 200

    def test_trailing_slash_does_not_bypass_or_break_allowlist(self, client: TestClient):
        assert client.get("/health/").status_code in (200, 307)

    def test_allowlist_contains_the_endpoints_the_login_screen_needs(self):
        assert {"/api/v1/auth/login", "/api/v1/tenant/config", "/api/v1/system/health"} <= set(
            DEFAULT_PUBLIC_PATHS
        )


class TestProtectedRoutes:
    def test_guest_is_rejected_with_401(self, client: TestClient):
        response = client.get("/api/v1/search")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json() == {"detail": "Not authenticated", "error": "authentication_required"}

    def test_invalid_token_is_rejected(self, client: TestClient):
        response = client.get("/api/v1/search", headers={"Authorization": "Bearer nope"})
        assert response.status_code == 401

    def test_valid_bearer_token_passes_and_user_is_attached(self, client: TestClient):
        response = client.get("/api/v1/search", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        assert response.status_code == 200
        assert response.json()["user"]["via"] == "jwt"

    def test_valid_api_key_passes(self, client: TestClient):
        response = client.get("/api/v1/search", headers={"X-API-Key": VALID_API_KEY})
        assert response.status_code == 200
        assert response.json()["user"]["via"] == "api-key"

    def test_sse_endpoint_accepts_token_query_parameter(self, client: TestClient):
        response = client.get(f"/api/v1/ingestion/logs/stream?token={VALID_TOKEN}")
        assert response.status_code == 200
        assert response.json()["user"]["via"] == "jwt"

    def test_query_token_is_not_accepted_elsewhere(self, client: TestClient):
        response = client.get(f"/api/v1/search?token={VALID_TOKEN}")
        assert response.status_code == 401

    def test_cors_preflight_is_not_blocked(self, client: TestClient):
        response = client.options(
            "/api/v1/search",
            headers={"Origin": "https://quellex.local", "Access-Control-Request-Method": "GET"},
        )
        assert response.status_code != 401

    def test_resolver_failure_yields_503_not_access(self):
        client = TestClient(build_app(resolver=failing_resolver))
        response = client.get("/api/v1/search", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
        assert response.status_code == 503
        assert response.json()["error"] == "authentication_unavailable"

    def test_disabled_middleware_lets_guests_through(self):
        client = TestClient(build_app(enabled=False))
        response = client.get("/api/v1/search")
        assert response.status_code == 200
        assert response.json()["user"] is None


class TestIsPublic:
    def test_prefix_matching_is_segment_aware(self):
        middleware = RequireAuthMiddleware(
            FastAPI(), resolve_user=fake_resolver, public_prefixes=("/docs", "/redoc")
        )
        assert middleware.is_public("/docs")
        assert middleware.is_public("/docs/")
        assert middleware.is_public("/redoc/static/x.js")
        assert not middleware.is_public("/docsx")
        assert not middleware.is_public("/api/v1/auth/me")
        assert not middleware.is_public("/api/v1/auth/login/extra")

    def test_defaults_keep_only_the_login_screen_endpoints_public(self):
        middleware = RequireAuthMiddleware(FastAPI(), resolve_user=fake_resolver)
        assert middleware.is_public("/api/v1/auth/login")
        assert middleware.is_public("/api/v1/auth/register")
        assert middleware.is_public("/api/v1/tenant/config")
        assert not middleware.is_public("/docs")
        assert not middleware.is_public("/openapi.json")
        assert not middleware.is_public("/api/v1/search/text")
        assert not middleware.is_public("/api/v1/ingestion/documents")


class TestExtractBearerCredentials:
    def _request(self, path: str, headers: dict | None = None) -> Request:
        raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        if "?" in path:
            path, query = path.split("?", 1)
        else:
            query = ""
        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": raw_headers,
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
        }
        return Request(scope)

    def test_bearer_header(self):
        creds = extract_bearer_credentials(self._request("/api/v1/x", {"Authorization": "Bearer abc"}))
        assert creds is not None and creds.credentials == "abc"

    def test_scheme_is_case_insensitive_and_token_trimmed(self):
        creds = extract_bearer_credentials(self._request("/api/v1/x", {"Authorization": "bearer   abc  "}))
        assert creds is not None and creds.credentials == "abc"

    def test_other_schemes_are_ignored(self):
        assert extract_bearer_credentials(self._request("/api/v1/x", {"Authorization": "Basic abc"})) is None

    def test_query_token_only_for_stream_paths(self):
        assert extract_bearer_credentials(self._request("/api/v1/ingestion/events/stream?token=abc")) is not None
        assert extract_bearer_credentials(self._request("/api/v1/search?token=abc")) is None

    def test_header_wins_over_query_token(self):
        creds = extract_bearer_credentials(
            self._request("/api/v1/ingestion/logs/stream?token=fromquery", {"Authorization": "Bearer fromheader"})
        )
        assert creds is not None and creds.credentials == "fromheader"


class TestTenantConfigAdvertisesFlag:
    @pytest.mark.asyncio
    async def test_require_auth_is_part_of_tenant_config(self, monkeypatch):
        from backend.routers import tenant as tenant_router

        monkeypatch.setattr(tenant_router.settings, "tenant_id", "quellex")
        monkeypatch.setattr(tenant_router.settings, "require_auth", True)
        payload = await tenant_router.get_tenant_config()
        assert payload["tenant_id"] == "quellex"
        assert payload["require_auth"] is True

        monkeypatch.setattr(tenant_router.settings, "require_auth", False)
        payload = await tenant_router.get_tenant_config()
        assert payload["require_auth"] is False
