"""Tests for profile document folders.

Covers the folder root validation and browsing service, the organisation
admin rules on ``PUT /api/v1/profiles/{key}``, the folder browse endpoint,
the multi-folder validation of queued ingestion jobs, and the profile
manager reload used by ingestion jobs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.core.config import settings
from backend.routers import profiles as profiles_module
from backend.routers.auth import UserResponse, require_org_admin
from backend.routers.ingestion_queue import validate_profile_folder
from backend.services.document_folders import (
    FolderNotAllowedError,
    FolderNotFoundError,
    MAX_BROWSE_ENTRIES,
    assert_folders_allowed,
    browse_folders,
    normalize_folder,
)
from src.profile import ProfileManager


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
ORG = _user(id="org", email="office@example.com", is_org_admin=True)


@pytest.fixture
def roots(tmp_path: Path) -> list[str]:
    """Two document roots with a few sub-folders, mirroring /app/mounts."""
    mounts = tmp_path / "mounts"
    (mounts / "kanzlei" / "Akten").mkdir(parents=True)
    (mounts / "kanzlei" / "Vorlagen").mkdir()
    (mounts / "kanzlei" / ".git").mkdir()
    (mounts / "kanzlei" / "$RECYCLE.BIN").mkdir()
    (mounts / "kanzlei" / "notes.txt").write_text("x", encoding="utf-8")
    (mounts / "archiv").mkdir()
    documents = tmp_path / "documents"
    documents.mkdir()
    return [str(mounts), str(documents)]


class TestAssertFoldersAllowed:
    def test_accepts_and_normalises_folders_below_a_root(self, roots: list[str]):
        picked = os.path.join(roots[0], "kanzlei", "Akten", "..", "Akten")
        assert assert_folders_allowed([picked, roots[1]], roots) == [
            normalize_folder(os.path.join(roots[0], "kanzlei", "Akten")),
            normalize_folder(roots[1]),
        ]

    def test_deduplicates(self, roots: list[str]):
        folder = os.path.join(roots[0], "kanzlei")
        assert assert_folders_allowed([folder, folder + os.sep], roots) == [normalize_folder(folder)]

    def test_rejects_relative_paths(self, roots: list[str]):
        with pytest.raises(FolderNotAllowedError, match="absolute"):
            assert_folders_allowed(["./documents"], roots)

    def test_rejects_blank_paths(self, roots: list[str]):
        with pytest.raises(FolderNotAllowedError, match="empty"):
            assert_folders_allowed(["   "], roots)

    def test_rejects_paths_outside_roots(self, roots: list[str], tmp_path: Path):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        with pytest.raises(FolderNotAllowedError, match="outside the document roots"):
            assert_folders_allowed([str(outside)], roots)

    def test_rejects_traversal_out_of_a_root(self, roots: list[str], tmp_path: Path):
        sneaky = os.path.join(roots[0], "..", "documents")
        # Normalises to the documents root, which is allowed...
        assert assert_folders_allowed([sneaky], roots) == [normalize_folder(roots[1])]
        # ...but climbing above every root is not.
        with pytest.raises(FolderNotAllowedError):
            assert_folders_allowed([os.path.join(roots[0], "..")], roots)

    def test_rejects_sibling_with_root_prefix(self, roots: list[str], tmp_path: Path):
        sibling = Path(roots[0] + "-other")
        sibling.mkdir()
        with pytest.raises(FolderNotAllowedError):
            assert_folders_allowed([str(sibling)], roots)

    def test_rejects_missing_directory(self, roots: list[str]):
        with pytest.raises(FolderNotFoundError, match="not reachable"):
            assert_folders_allowed([os.path.join(roots[0], "kanzlei", "Nope")], roots)

    def test_rejects_file(self, roots: list[str]):
        with pytest.raises(FolderNotFoundError):
            assert_folders_allowed([os.path.join(roots[0], "kanzlei", "notes.txt")], roots)

    def test_rejects_everything_without_roots(self, roots: list[str]):
        with pytest.raises(FolderNotAllowedError, match="No document roots"):
            assert_folders_allowed([roots[0]], [])


class TestBrowseFolders:
    def test_lists_roots_with_mount_state(self, roots: list[str]):
        missing = os.path.join(roots[0], "..", "not-mounted")
        listing = browse_folders(None, roots + [missing])
        assert listing.path is None and listing.parent is None
        assert [e.path for e in listing.entries] == [normalize_folder(r) for r in roots + [missing]]
        assert [e.exists for e in listing.entries] == [True, True, False]

    def test_lists_subfolders_sorted_without_hidden_or_files(self, roots: list[str]):
        listing = browse_folders(os.path.join(roots[0], "kanzlei"), roots)
        assert [e.name for e in listing.entries] == ["Akten", "Vorlagen"]
        assert listing.parent == normalize_folder(roots[0])
        assert listing.truncated is False
        assert all(e.path.startswith(normalize_folder(roots[0])) for e in listing.entries)

    def test_root_itself_has_no_parent(self, roots: list[str]):
        listing = browse_folders(roots[0], roots)
        assert listing.parent is None
        assert [e.name for e in listing.entries] == ["archiv", "kanzlei"]

    def test_outside_roots_is_refused(self, roots: list[str], tmp_path: Path):
        with pytest.raises(FolderNotAllowedError):
            browse_folders(str(tmp_path), roots)

    def test_missing_folder_is_not_found(self, roots: list[str]):
        with pytest.raises(FolderNotFoundError):
            browse_folders(os.path.join(roots[0], "gone"), roots)

    def test_listing_is_capped(self, roots: list[str], monkeypatch):
        monkeypatch.setattr("backend.services.document_folders.MAX_BROWSE_ENTRIES", 1)
        listing = browse_folders(os.path.join(roots[0], "kanzlei"), roots)
        assert len(listing.entries) == 1 and listing.truncated is True
        assert MAX_BROWSE_ENTRIES >= 1


@pytest.fixture
def profile_manager(tmp_path: Path, roots: list[str], monkeypatch) -> ProfileManager:
    """A ProfileManager on a temporary profiles.yaml, wired into the router."""
    pm = ProfileManager(str(tmp_path / "profiles.yaml"))
    pm.create_profile(
        key="kanzlei",
        name="Kanzlei",
        documents_folders=[os.path.join(roots[0], "kanzlei")],
        database="rag_kanzlei",
        description="Law firm",
    )
    pm.create_profile(key="other", name="Other", documents_folders=[roots[1]], database="rag_other")
    monkeypatch.setattr(profiles_module, "get_profile_manager", lambda: pm)
    monkeypatch.setattr(settings, "document_roots", ",".join(roots))
    return pm


@pytest.fixture
def org_client(app, profile_manager) -> TestClient:
    """Client acting as an organisation admin with access to 'kanzlei' only."""
    app.dependency_overrides[require_org_admin] = lambda: ORG
    return TestClient(app)


@pytest.fixture
def super_client(app, profile_manager) -> TestClient:
    app.dependency_overrides[require_org_admin] = lambda: SUPER
    return TestClient(app)


@pytest.fixture(autouse=True)
def accessible_profiles(monkeypatch):
    monkeypatch.setattr(
        profiles_module, "get_user_accessible_profiles", AsyncMock(return_value=["kanzlei"])
    )


class TestOrgAdminProfileUpdate:
    def test_org_admin_sets_several_folders_of_own_profile(
        self, org_client: TestClient, profile_manager: ProfileManager, roots: list[str]
    ):
        folders = [
            os.path.join(roots[0], "kanzlei", "Akten"),
            os.path.join(roots[0], "kanzlei", "Vorlagen"),
            os.path.join(roots[0], "archiv"),
        ]
        response = org_client.put(
            "/api/v1/profiles/kanzlei",
            json={"name": "Kanzlei Fischerlehner", "documents_folders": folders},
        )
        assert response.status_code == 200, response.text
        stored = profile_manager.get_profile("kanzlei")
        assert stored.name == "Kanzlei Fischerlehner"
        assert stored.documents_folders == [normalize_folder(f) for f in folders]
        assert stored.database == "rag_kanzlei"

    def test_org_admin_cannot_touch_profiles_without_access(self, org_client: TestClient):
        response = org_client.put("/api/v1/profiles/other", json={"name": "Mine now"})
        assert response.status_code == 403

    def test_org_admin_cannot_change_database(self, org_client: TestClient, profile_manager: ProfileManager):
        response = org_client.put("/api/v1/profiles/kanzlei", json={"database": "rag_other"})
        assert response.status_code == 403
        assert profile_manager.get_profile("kanzlei").database == "rag_kanzlei"

    def test_org_admin_folder_outside_roots_is_refused(
        self, org_client: TestClient, profile_manager: ProfileManager, tmp_path: Path
    ):
        outside = tmp_path / "secret"
        outside.mkdir()
        response = org_client.put(
            "/api/v1/profiles/kanzlei", json={"documents_folders": [str(outside)]}
        )
        assert response.status_code == 400
        assert "outside the document roots" in response.json()["message"]
        assert profile_manager.get_profile("kanzlei").documents_folders != [str(outside)]

    def test_org_admin_missing_folder_is_refused(self, org_client: TestClient, roots: list[str]):
        response = org_client.put(
            "/api/v1/profiles/kanzlei",
            json={"documents_folders": [os.path.join(roots[0], "kanzlei", "Typo")]},
        )
        assert response.status_code == 400
        assert "not reachable" in response.json()["message"]

    def test_unknown_profile_is_404(self, org_client: TestClient):
        response = org_client.put("/api/v1/profiles/nope", json={"name": "x"})
        assert response.status_code == 404

    def test_superadmin_keeps_free_paths_and_database(
        self, super_client: TestClient, profile_manager: ProfileManager
    ):
        response = super_client.put(
            "/api/v1/profiles/other",
            json={"documents_folders": ["./test_docs"], "database": "rag_renamed"},
        )
        assert response.status_code == 200, response.text
        stored = profile_manager.get_profile("other")
        assert stored.documents_folders == ["./test_docs"]
        assert stored.database == "rag_renamed"


class TestBrowseEndpoint:
    def test_roots_listing(self, org_client: TestClient, roots: list[str]):
        response = org_client.get("/api/v1/profiles/folders/browse")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["path"] is None
        assert [e["path"] for e in body["entries"]] == [normalize_folder(r) for r in roots]

    def test_subfolder_listing(self, org_client: TestClient, roots: list[str]):
        response = org_client.get(
            "/api/v1/profiles/folders/browse", params={"path": os.path.join(roots[0], "kanzlei")}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert [e["name"] for e in body["entries"]] == ["Akten", "Vorlagen"]
        assert body["parent"] == normalize_folder(roots[0])

    def test_outside_roots_is_400(self, org_client: TestClient, tmp_path: Path):
        response = org_client.get("/api/v1/profiles/folders/browse", params={"path": str(tmp_path)})
        assert response.status_code == 400

    def test_missing_folder_is_404(self, org_client: TestClient, roots: list[str]):
        response = org_client.get(
            "/api/v1/profiles/folders/browse", params={"path": os.path.join(roots[0], "gone")}
        )
        assert response.status_code == 404

    def test_members_are_refused(self, app, profile_manager):
        from fastapi import HTTPException

        async def _deny() -> UserResponse:
            raise HTTPException(status_code=403, detail="Admin or organisation admin access required")

        app.dependency_overrides[require_org_admin] = _deny
        response = TestClient(app).get("/api/v1/profiles/folders/browse")
        assert response.status_code == 403


class TestQueueFolderValidation:
    class _Profile:
        def __init__(self, folders: list[str]):
            self.documents_folders = folders

    def test_all_folders_must_exist(self, roots: list[str]):
        ok = os.path.join(roots[0], "kanzlei")
        missing = os.path.join(roots[0], "missing")
        primary, accessible, error = validate_profile_folder(self._Profile([ok, missing]))
        assert primary == ok
        assert accessible is False
        assert "missing" in error

    def test_every_folder_present_is_accessible(self, roots: list[str]):
        folders = [os.path.join(roots[0], "kanzlei"), os.path.join(roots[0], "archiv")]
        primary, accessible, error = validate_profile_folder(self._Profile(folders))
        assert (primary, accessible, error) == (folders[0], True, "")

    def test_file_instead_of_directory_is_reported(self, roots: list[str]):
        _, accessible, error = validate_profile_folder(
            self._Profile([os.path.join(roots[0], "kanzlei", "notes.txt")])
        )
        assert accessible is False and "not a directory" in error


class TestProfileManagerReload:
    def test_reload_picks_up_external_edits(self, tmp_path: Path):
        path = tmp_path / "profiles.yaml"
        writer = ProfileManager(str(path))
        writer.create_profile(key="kanzlei", name="Kanzlei", documents_folders=["/app/mounts/kanzlei"], database="rag_kanzlei")

        reader = ProfileManager(str(path))
        assert reader.get_profile("kanzlei").documents_folders == ["/app/mounts/kanzlei"]
        assert reader.reload() is False  # unchanged since it was read

        # Make the write land on a later mtime even on coarse clocks (Windows: 15.6 ms).
        writer.update_profile("kanzlei", documents_folders=["/app/mounts/kanzlei", "/app/mounts/archiv"])
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))

        assert reader.reload() is True
        assert reader.get_profile("kanzlei").documents_folders == ["/app/mounts/kanzlei", "/app/mounts/archiv"]

    def test_switch_without_persist_leaves_file_alone(self, tmp_path: Path):
        path = tmp_path / "profiles.yaml"
        pm = ProfileManager(str(path))
        pm.create_profile(key="kanzlei", name="Kanzlei", documents_folders=["/app/mounts/kanzlei"], database="rag_kanzlei")
        before = path.read_text(encoding="utf-8")

        assert pm.switch_profile("kanzlei", persist=False) is True
        assert pm.active_profile_key == "kanzlei"
        assert path.read_text(encoding="utf-8") == before
        assert pm.switch_profile("missing", persist=False) is False
