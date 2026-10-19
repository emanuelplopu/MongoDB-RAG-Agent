"""Role checks on the ingestion and system-configuration endpoints.

Ingestion is system configuration: only superadmins (``is_admin``) may start,
pause, stop or clean runs, delete documents, rebuild metadata, read job logs or
touch other databases. Organisation admins and members get 403, guests 401.
Document browsing stays open to every caller.

The ``app`` fixture in ``conftest.py`` overrides ``require_admin`` with a
superadmin; ``caller`` removes those overrides so the real dependencies run and
only the user lookup (``get_current_user``) is replaced.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, Optional
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.core import security as security_module
from backend.routers import auth as auth_module
from backend.routers import ingestion as ingestion_module
from backend.routers import ingestion_queue as ingestion_queue_module
from backend.routers import system as system_module
from backend.routers.auth import (
    UserResponse,
    get_current_user,
    require_admin,
    require_admin_stream,
    require_auth,
)

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: Routers under test with the prefix ``backend/main.py`` mounts them at.
#: Walked directly because the app only holds lazy ``_IncludedRouter`` wrappers.
ROUTERS = {
    "/api/v1/ingestion": ingestion_module.router,
    "/api/v1/ingestion-queue": ingestion_queue_module.router,
    "/api/v1/system": system_module.router,
}


def _user(**overrides: Any) -> UserResponse:
    base: dict[str, Any] = {
        "id": "u1",
        "email": "u@example.com",
        "name": "User",
        "is_active": True,
        "created_at": "2025-01-01T00:00:00",
    }
    base.update(overrides)
    return UserResponse(**base)


SUPER = _user(id="super", email="super@example.com", is_admin=True)
ORG = _user(id="org", email="org@example.com", is_org_admin=True)
MEMBER = _user(id="member", email="member@example.com")

DOC_ID = "507f1f77bcf86cd799439011"

#: Every state-changing ingestion endpoint (method, path, JSON body).
INGESTION_WRITES: list[tuple[str, str, Optional[dict]]] = [
    ("POST", "/api/v1/ingestion/start", {"profile": "other", "clean_before_ingest": True}),
    ("POST", "/api/v1/ingestion/cancel/job-1", None),
    ("POST", "/api/v1/ingestion/pause", None),
    ("POST", "/api/v1/ingestion/resume", None),
    ("POST", "/api/v1/ingestion/stop", None),
    ("POST", "/api/v1/ingestion/rebuild-metadata", None),
    ("DELETE", "/api/v1/ingestion/rebuild-metadata", None),
    ("POST", "/api/v1/ingestion/setup-indexes", None),
    ("POST", f"/api/v1/ingestion/documents/{DOC_ID}/open-explorer", None),
    ("DELETE", f"/api/v1/ingestion/documents/{DOC_ID}", None),
    ("DELETE", "/api/v1/ingestion/logs", None),
    ("POST", f"/api/v1/ingestion/failed-documents/{DOC_ID}/resolve", None),
    ("DELETE", f"/api/v1/ingestion/failed-documents/{DOC_ID}", None),
    ("DELETE", "/api/v1/ingestion/failed-documents", None),
    ("DELETE", "/api/v1/ingestion/analytics/clear", None),
    ("DELETE", "/api/v1/ingestion/jobs/job-1", None),
]

#: Read endpoints that expose job state, process logs or server paths of every profile.
INGESTION_ADMIN_READS: list[str] = [
    "/api/v1/ingestion/status",
    "/api/v1/ingestion/status/job-1",
    "/api/v1/ingestion/jobs",
    "/api/v1/ingestion/runs",
    "/api/v1/ingestion/pending-files",
    "/api/v1/ingestion/logs",
    "/api/v1/ingestion/failed-documents",
    "/api/v1/ingestion/failed-documents/summary",
    "/api/v1/ingestion/analytics/overview",
    "/api/v1/ingestion/analytics/outliers",
    "/api/v1/ingestion/analytics/by-extension",
    "/api/v1/ingestion/analytics/no-chunks",
    "/api/v1/ingestion/analytics/timeline",
    "/api/v1/ingestion/jobs/job-1",
    "/api/v1/ingestion/jobs/job-1/stats",
    "/api/v1/ingestion/jobs/job-1/logs",
    "/api/v1/ingestion/jobs/job-1/failed",
    "/api/v1/ingestion/jobs/stats/summary",
]

INGESTION_STREAMS: list[str] = [
    "/api/v1/ingestion/logs/stream",
    "/api/v1/ingestion/events/stream",
]

#: System endpoints that change configuration or touch other databases.
SYSTEM_ADMIN_CALLS: list[tuple[str, str, Optional[dict]]] = [
    ("GET", "/api/v1/system/database/check/quellex_other", None),
    ("POST", "/api/v1/system/database/create", {"database": "quellex_other"}),
    ("POST", "/api/v1/system/indexes/create", None),
    ("POST", "/api/v1/system/reload-settings", None),
    ("POST", "/api/v1/system/agent-performance", {}),
    ("POST", "/api/v1/system/ingestion-performance", {}),
    ("POST", "/api/v1/system/ingestion-timeout-config", {}),
    ("POST", "/api/v1/system/ingestion-accelerator-config", {}),
    ("POST", "/api/v1/system/tools/test", {"tool_name": "search", "parameters": {}}),
    ("POST", "/api/v1/system/models/m1/test?role=worker", None),
    ("POST", "/api/v1/system/models/m1/approve?role=worker&approved=true", None),
]

#: System write routes that stay open, with the reason.
OPEN_SYSTEM_WRITES: dict[str, str] = {
    # Read-only ping; the Email/Cloud page calls it on load for every user.
    "/api/v1/system/database/test-connection": "read-only connection ping",
}

#: System write routes that check ``is_admin`` inside the handler instead of a dependency.
INLINE_ADMIN_SYSTEM_WRITES: set[str] = {
    "/api/v1/system/models/{model_id:path}/test",
    "/api/v1/system/models/{model_id:path}/approve",
}


def _dependency_calls(route: APIRoute) -> set[Callable[..., Any]]:
    """All callables in a route's dependency tree."""
    calls: set[Callable[..., Any]] = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        calls.add(dep.call)
        stack.extend(dep.dependencies)
    return calls


def _write_methods(route: APIRoute) -> set[str]:
    return set(route.methods or ()) & WRITE_METHODS


def _routes(prefix: str) -> list[tuple[str, APIRoute]]:
    """``(full path, route)`` for every API route of the router mounted at ``prefix``."""
    routes = [(prefix + r.path, r) for r in ROUTERS[prefix].routes if isinstance(r, APIRoute)]
    assert routes, f"no routes found under {prefix}"
    return routes


def _is_superadmin_only(route: APIRoute) -> bool:
    return bool(_dependency_calls(route) & {require_admin, require_admin_stream})


@pytest.fixture(autouse=True)
def fresh_rate_limiter(monkeypatch) -> None:
    """These tests send far more requests than the per-minute API limits allow."""
    monkeypatch.setattr(security_module, "rate_limiter", security_module.RateLimiter())


@pytest.fixture
def caller(app, monkeypatch) -> Iterator[Callable[[Optional[UserResponse]], TestClient]]:
    """Return a factory for a client whose requests resolve to the given user (None = guest)."""
    for dependency in (require_auth, require_admin, require_admin_stream):
        app.dependency_overrides.pop(dependency, None)
    # Never start a real run from POST /start
    monkeypatch.setattr(ingestion_module, "run_ingestion", AsyncMock())
    monkeypatch.setattr(ingestion_module, "_current_job_id", None)

    def _client(user: Optional[UserResponse]) -> TestClient:
        lookup = AsyncMock(return_value=user)
        # require_auth calls the module-level function; routes that depend on it directly use the override
        monkeypatch.setattr(auth_module, "get_current_user", lookup)
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    yield _client


def _assert_nothing_written(mock_db) -> None:
    collection = mock_db.db["any"]
    for method in ("insert_one", "replace_one", "update_one", "delete_one", "delete_many"):
        getattr(collection, method).assert_not_awaited()


class TestRouteTable:
    """Structural guard: new write routes must not ship without a role check."""

    @pytest.mark.parametrize("prefix", ["/api/v1/ingestion", "/api/v1/ingestion-queue"])
    def test_every_ingestion_write_route_requires_superadmin(self, prefix):
        writes = [(path, route) for path, route in _routes(prefix) if _write_methods(route)]
        assert writes
        unguarded = [f"{sorted(_write_methods(r))} {path}" for path, r in writes if not _is_superadmin_only(r)]
        assert unguarded == []

    def test_every_system_write_route_requires_superadmin(self):
        unguarded = [
            f"{sorted(_write_methods(route))} {path}"
            for path, route in _routes("/api/v1/system")
            if _write_methods(route)
            and path not in OPEN_SYSTEM_WRITES
            and path not in INLINE_ADMIN_SYSTEM_WRITES
            and require_admin not in _dependency_calls(route)
        ]
        assert unguarded == []
        # The exceptions must still exist, or the lists above are stale
        system_paths = {path for path, _ in _routes("/api/v1/system")}
        assert set(OPEN_SYSTEM_WRITES) | INLINE_ADMIN_SYSTEM_WRITES <= system_paths

    def test_database_check_requires_superadmin(self):
        routes = [r for path, r in _routes("/api/v1/system") if path == "/api/v1/system/database/check/{database_name}"]
        assert len(routes) == 1 and require_admin in _dependency_calls(routes[0])

    def test_only_document_browsing_reads_stay_open(self):
        open_reads = sorted(
            path
            for path, route in _routes("/api/v1/ingestion")
            if "GET" in (route.methods or ()) and not _is_superadmin_only(route)
        )
        assert open_reads == [
            "/api/v1/ingestion/documents",
            "/api/v1/ingestion/documents/folders",
            "/api/v1/ingestion/documents/lookup",
            "/api/v1/ingestion/documents/{document_id}",
            "/api/v1/ingestion/documents/{document_id}/file",
            "/api/v1/ingestion/documents/{document_id}/info",
            "/api/v1/ingestion/rebuild-metadata",
        ]


class TestNonAdminsAreRefused:
    @pytest.mark.parametrize("user", [MEMBER, ORG], ids=["member", "org_admin"])
    @pytest.mark.parametrize(("method", "path", "body"), INGESTION_WRITES, ids=[f"{m} {p}" for m, p, _ in INGESTION_WRITES])
    def test_ingestion_writes_return_403(self, caller, mock_db, user, method, path, body):
        response = caller(user).request(method, path, json=body)
        assert response.status_code == 403, response.text
        _assert_nothing_written(mock_db)
        ingestion_module.run_ingestion.assert_not_called()

    @pytest.mark.parametrize(("method", "path", "body"), INGESTION_WRITES, ids=[f"{m} {p}" for m, p, _ in INGESTION_WRITES])
    def test_ingestion_writes_return_401_for_guests(self, caller, mock_db, method, path, body):
        response = caller(None).request(method, path, json=body)
        assert response.status_code == 401, response.text
        _assert_nothing_written(mock_db)

    @pytest.mark.parametrize("user", [MEMBER, ORG], ids=["member", "org_admin"])
    @pytest.mark.parametrize("path", INGESTION_ADMIN_READS)
    def test_ingestion_monitoring_reads_return_403(self, caller, user, path):
        assert caller(user).get(path).status_code == 403

    @pytest.mark.parametrize("path", INGESTION_STREAMS)
    def test_streams_return_403_for_members_with_a_query_token(self, caller, path):
        assert caller(MEMBER).get(f"{path}?token=member-jwt").status_code == 403

    @pytest.mark.parametrize("path", INGESTION_STREAMS)
    def test_streams_return_401_for_guests(self, caller, path):
        assert caller(None).get(path).status_code == 401

    @pytest.mark.parametrize("user", [MEMBER, ORG], ids=["member", "org_admin"])
    @pytest.mark.parametrize(("method", "path", "body"), SYSTEM_ADMIN_CALLS, ids=[f"{m} {p}" for m, p, _ in SYSTEM_ADMIN_CALLS])
    def test_system_admin_calls_return_403(self, caller, mock_db, user, method, path, body):
        response = caller(user).request(method, path, json=body)
        assert response.status_code == 403, response.text
        _assert_nothing_written(mock_db)
        mock_db.get_sync_client.assert_not_called()


class TestSuperadminsStillSucceed:
    def test_start_schedules_the_run(self, caller, mock_db):
        response = caller(SUPER).post("/api/v1/ingestion/start", json={"profile": "other"})
        assert response.status_code == 200, response.text
        ingestion_module.run_ingestion.assert_called_once()
        mock_db.db["ingestion_jobs"].replace_one.assert_awaited_once()

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("DELETE", "/api/v1/ingestion/logs"),
            ("DELETE", "/api/v1/ingestion/failed-documents"),
            ("DELETE", "/api/v1/ingestion/analytics/clear"),
            ("GET", "/api/v1/ingestion/status"),
            ("GET", "/api/v1/ingestion/logs"),
            ("GET", "/api/v1/ingestion/failed-documents/summary"),
            ("GET", "/api/v1/system/database/check/quellex_other"),
            ("POST", "/api/v1/system/reload-settings"),
        ],
    )
    def test_admin_calls_return_200(self, caller, method, path):
        response = caller(SUPER).request(method, path)
        assert response.status_code == 200, response.text

    def test_pause_without_a_run_reaches_the_handler(self, caller):
        response = caller(SUPER).post("/api/v1/ingestion/pause")
        assert response.status_code == 400
        assert response.json()["message"] == "No ingestion job is running"


class TestDocumentBrowsingStaysOpen:
    @pytest.mark.parametrize("path", ["/api/v1/ingestion/documents", "/api/v1/ingestion/rebuild-metadata"])
    def test_members_can_still_browse(self, caller, path):
        assert caller(MEMBER).get(path).status_code == 200


class TestRequireAdminStream:
    @staticmethod
    def _request(path: str, query: str = "", headers: Optional[dict[str, str]] = None) -> Request:
        raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        return Request({"type": "http", "method": "GET", "path": path, "query_string": query.encode(), "headers": raw_headers})

    async def test_accepts_the_query_token_on_stream_paths(self, monkeypatch):
        lookup = AsyncMock(return_value=SUPER)
        monkeypatch.setattr(auth_module, "get_current_user", lookup)
        user = await require_admin_stream(self._request("/api/v1/ingestion/logs/stream", "token=abc"))
        assert user is SUPER
        credentials = lookup.await_args.args[1]
        assert credentials.credentials == "abc"

    async def test_ignores_the_query_token_elsewhere(self, monkeypatch):
        lookup = AsyncMock(return_value=None)
        monkeypatch.setattr(auth_module, "get_current_user", lookup)
        with pytest.raises(HTTPException) as exc:
            await require_admin_stream(self._request("/api/v1/ingestion/logs", "token=abc"))
        assert exc.value.status_code == 401
        assert lookup.await_args.args[1] is None

    async def test_passes_the_api_key_header(self, monkeypatch):
        lookup = AsyncMock(return_value=SUPER)
        monkeypatch.setattr(auth_module, "get_current_user", lookup)
        await require_admin_stream(self._request("/api/v1/ingestion/events/stream", headers={"X-API-Key": "k1"}))
        assert lookup.await_args.args[2] == "k1"
