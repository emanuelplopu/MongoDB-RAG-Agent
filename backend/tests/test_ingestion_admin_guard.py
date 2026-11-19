"""The ingestion write endpoints are reserved for superadmins.

Every endpoint of ``backend/routers/ingestion.py`` that starts, steers or
deletes something depends on ``require_admin``. The login middleware only
checks that a session exists, so without the dependency any signed-in member
could wipe the index or stop a running job.
"""

from typing import Set, Tuple

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.routers import ingestion
from backend.routers.auth import require_admin

PREFIX = "/api/v1/ingestion"

# (method, path below the ingestion prefix) of every guarded endpoint.
GUARDED: Tuple[Tuple[str, str], ...] = (
    ("POST", "/start"),
    ("POST", "/cancel/job-1"),
    ("POST", "/rebuild-metadata"),
    ("DELETE", "/rebuild-metadata"),
    ("POST", "/pause"),
    ("POST", "/resume"),
    ("POST", "/stop"),
    ("DELETE", "/logs"),
    ("DELETE", "/documents/doc-1"),
    ("POST", "/setup-indexes"),
    ("POST", "/failed-documents/doc-1/resolve"),
    ("DELETE", "/failed-documents/doc-1"),
    ("DELETE", "/failed-documents"),
    ("DELETE", "/analytics/clear"),
    ("DELETE", "/jobs/job-1"),
)

# Write-method endpoints a member may call: opening the folder of a document
# they can already read changes nothing in the index.
MEMBER_WRITES: Set[Tuple[str, str]] = {
    ("POST", "/documents/{document_id}/open-explorer"),
}

READ_METHODS: Set[str] = {"GET", "HEAD", "OPTIONS"}


def _depends_on_require_admin(route: APIRoute) -> bool:
    """Tell whether the route resolves ``require_admin`` before its handler."""
    return any(dep.call is require_admin for dep in route.dependant.dependencies)


@pytest.fixture
def member_client(app) -> TestClient:
    """A client whose user is signed in but is not a superadmin."""

    async def _forbidden() -> None:
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = _forbidden
    return TestClient(app)


@pytest.mark.parametrize("method,path", GUARDED)
def test_member_gets_403(member_client: TestClient, method: str, path: str) -> None:
    body = {"json": {}} if method == "POST" else {}
    response = member_client.request(method, f"{PREFIX}{path}", **body)
    assert response.status_code == 403, response.text


def test_member_can_still_read_status(member_client: TestClient) -> None:
    response = member_client.get(f"{PREFIX}/status")
    assert response.status_code != 403


def test_every_write_route_is_guarded_or_listed() -> None:
    """A new POST/PUT/PATCH/DELETE route must take the guard or be listed above."""
    unguarded = []
    for route in ingestion.router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - READ_METHODS:
            if (method, route.path) in MEMBER_WRITES:
                continue
            if not _depends_on_require_admin(route):
                unguarded.append(f"{method} {route.path}")
    assert unguarded == []


def test_read_routes_stay_open_to_members() -> None:
    """The document viewer and the status widgets read these as ordinary users."""
    guarded_reads = [
        route.path
        for route in ingestion.router.routes
        if isinstance(route, APIRoute)
        and route.methods <= READ_METHODS
        and _depends_on_require_admin(route)
    ]
    assert guarded_reads == []
