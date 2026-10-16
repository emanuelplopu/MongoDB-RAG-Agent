"""Tests for the organisation-admin role.

Covers the ``require_org_admin`` dependency, the guards that keep
organisation admins away from superadmin accounts, and the user-management
endpoints when called by an organisation admin.
"""

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.routers import auth as auth_module
from backend.routers.auth import (
    UserResponse,
    assert_can_grant_superadmin,
    assert_can_manage_target,
    require_org_admin,
    visible_users_filter,
)


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


class TestRequireOrgAdmin:
    @pytest.mark.parametrize("actor", [SUPER, ORG], ids=["superadmin", "org_admin"])
    async def test_allows_superadmins_and_org_admins(self, monkeypatch, actor: UserResponse):
        monkeypatch.setattr(auth_module, "require_auth", AsyncMock(return_value=actor))
        assert await require_org_admin(MagicMock(), None, None) is actor

    async def test_rejects_ordinary_members(self, monkeypatch):
        monkeypatch.setattr(auth_module, "require_auth", AsyncMock(return_value=MEMBER))
        with pytest.raises(HTTPException) as exc:
            await require_org_admin(MagicMock(), None, None)
        assert exc.value.status_code == 403


class TestGuards:
    def test_superadmin_may_manage_anyone(self):
        assert_can_manage_target(SUPER, {"is_admin": True})
        assert_can_manage_target(SUPER, {"is_org_admin": True})
        assert_can_manage_target(SUPER, {})

    def test_org_admin_may_manage_team_but_not_superadmins(self):
        assert_can_manage_target(ORG, {"is_admin": False, "is_org_admin": True})
        assert_can_manage_target(ORG, {})
        with pytest.raises(HTTPException) as exc:
            assert_can_manage_target(ORG, {"is_admin": True})
        assert exc.value.status_code == 404

    def test_only_superadmins_grant_superadmin(self):
        assert_can_grant_superadmin(SUPER, True)
        assert_can_grant_superadmin(ORG, False)
        assert_can_grant_superadmin(ORG, None)
        with pytest.raises(HTTPException) as exc:
            assert_can_grant_superadmin(ORG, True)
        assert exc.value.status_code == 403

    def test_visible_users_filter_hides_superadmins_from_org_admins(self):
        assert visible_users_filter(SUPER) == {}
        assert visible_users_filter(ORG) == {"is_admin": {"$ne": True}}


@pytest.fixture
def org_client(app, mock_db) -> TestClient:
    """Test client whose caller is an organisation admin."""
    app.dependency_overrides[require_org_admin] = lambda: ORG
    return TestClient(app)


class TestOrgAdminEndpoints:
    def test_create_superadmin_is_refused(self, org_client: TestClient, mock_db):
        response = org_client.post(
            "/api/v1/auth/users/create",
            json={"email": "new@example.com", "name": "New User", "password": "secret1", "is_admin": True},
        )
        assert response.status_code == 403
        mock_db.db["users"].insert_one.assert_not_awaited()

    def test_create_org_admin_colleague(self, org_client: TestClient, mock_db):
        response = org_client.post(
            "/api/v1/auth/users/create",
            json={"email": "New@Example.com", "name": "New User", "password": "secret1", "is_org_admin": True},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["is_admin"] is False
        assert body["is_org_admin"] is True
        inserted = mock_db.db["users"].insert_one.await_args.args[0]
        assert inserted["email"] == "new@example.com"
        assert inserted["is_admin"] is False
        assert inserted["is_org_admin"] is True

    def test_list_users_applies_team_filter(self, org_client: TestClient, mock_db):
        response = org_client.get("/api/v1/auth/users")
        assert response.status_code == 200
        mock_db.db["users"].find.assert_called_with({"is_admin": {"$ne": True}})

    def test_update_superadmin_is_invisible(self, org_client: TestClient, mock_db):
        mock_db.db["users"].find_one = AsyncMock(return_value={
            "_id": "super", "email": "super@example.com", "name": "Super", "is_admin": True,
            "is_active": True, "created_at": datetime(2025, 1, 1),
        })
        response = org_client.put("/api/v1/auth/users/super", json={"name": "Renamed"})
        assert response.status_code == 404
        mock_db.db["users"].update_one.assert_not_awaited()

    def test_update_member_cannot_grant_superadmin(self, org_client: TestClient, mock_db):
        mock_db.db["users"].find_one = AsyncMock(return_value={
            "_id": "member", "email": "member@example.com", "name": "Member", "is_admin": False,
            "is_active": True, "created_at": datetime(2025, 1, 1),
        })
        response = org_client.put("/api/v1/auth/users/member", json={"is_admin": True})
        assert response.status_code == 403
        mock_db.db["users"].update_one.assert_not_awaited()

    def test_org_admin_cannot_demote_self(self, org_client: TestClient, mock_db):
        mock_db.db["users"].find_one = AsyncMock(return_value={
            "_id": "org", "email": "org@example.com", "name": "Org", "is_admin": False, "is_org_admin": True,
            "is_active": True, "created_at": datetime(2025, 1, 1),
        })
        response = org_client.put("/api/v1/auth/users/org", json={"is_org_admin": False})
        assert response.status_code == 400

    def test_delete_and_deactivate_superadmin_are_refused(self, org_client: TestClient, mock_db):
        mock_db.db["users"].find_one = AsyncMock(return_value={
            "_id": "super", "email": "super@example.com", "name": "Super", "is_admin": True,
            "is_active": True, "created_at": datetime(2025, 1, 1),
        })
        assert org_client.delete("/api/v1/auth/users/super").status_code == 404
        assert org_client.put("/api/v1/auth/users/super/status", json={"is_active": False}).status_code == 404
        mock_db.db["users"].delete_one.assert_not_awaited()
        mock_db.db["users"].update_one.assert_not_awaited()


class TestSuperadminEndpointsStillWork:
    def test_superadmin_lists_everyone_and_creates_superadmins(self, client: TestClient, app, mock_db):
        app.dependency_overrides[require_org_admin] = lambda: SUPER
        assert client.get("/api/v1/auth/users").status_code == 200
        mock_db.db["users"].find.assert_called_with({})
        response = client.post(
            "/api/v1/auth/users/create",
            json={"email": "boss@example.com", "name": "Boss", "password": "secret1", "is_admin": True},
        )
        assert response.status_code == 200, response.text
        assert response.json()["is_admin"] is True
