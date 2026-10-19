"""Tenant isolation on a host running RecallHub and Quellex (docs/27-TENANT_ISOLATION.md).

Covers profile ownership and tenant-bound profile managers, safe concurrent
``profiles.yaml`` writes, the per-process active profile, the tenant-scoped
profile endpoints, per-tenant chat telemetry, personal database names and
tenant-scoped API backups.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.core.config import settings
from backend.routers import profiles as profiles_module
from backend.routers.auth import UserResponse, require_org_admin
from src.profile import LEGACY_PROFILE_TENANTS, ProfileManager, profile_tenant

PROFILES = """active_profile: default
profiles:
  default:
    name: Default
    database: rag_db
    documents_folders: [/app/documents]
  diejuristin:
    name: Die Juristin
    database: rag_diejuristin
    documents_folders: [/app/mounts/akten]
  parhelion:
    name: Parhelion
    database: rag_parhelion
    documents_folders: [/app/mounts/parhelion]
  synthetic-kanzlei:
    name: Synthetic
    database: rag_synth
    documents_folders: [/app/mounts/synth]
  tagged-rh:
    name: Tagged RecallHub
    tenant: RecallHub
    database: rag_tagged_rh
    documents_folders: [/app/mounts/rh]
"""


@pytest.fixture
def profiles_path(tmp_path: Path) -> Path:
    path = tmp_path / "profiles.yaml"
    path.write_text(PROFILES, encoding="utf-8")
    return path


def _on_disk(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ==================== Profile ownership ====================


class TestProfileOwnership:
    def test_effective_tenant_uses_field_then_legacy_keys(self, profiles_path: Path):
        pm = ProfileManager(str(profiles_path))
        assert pm.profile_tenant("tagged-rh") == "recallhub"  # field, normalised
        assert pm.profile_tenant("diejuristin") == "quellex"  # legacy key
        assert pm.profile_tenant("parhelion") == "recallhub"
        assert pm.profile_tenant("synthetic-kanzlei") is None  # unassigned
        assert pm.profile_tenant("default") is None
        assert LEGACY_PROFILE_TENANTS["diejuristin"] == "quellex"

    def test_bound_managers_see_own_and_unassigned_profiles(self, profiles_path: Path):
        quellex = ProfileManager(str(profiles_path), tenant_id="quellex")
        recallhub = ProfileManager(str(profiles_path), tenant_id="recallhub")
        unbound = ProfileManager(str(profiles_path))

        assert set(quellex.list_profiles()) == {"default", "diejuristin", "synthetic-kanzlei"}
        assert set(recallhub.list_profiles()) == {"default", "parhelion", "synthetic-kanzlei", "tagged-rh"}
        assert len(unbound.list_profiles()) == 5
        assert quellex.get_profile("parhelion") is None
        assert recallhub.get_profile("diejuristin") is None
        assert quellex.profile_exists("parhelion")  # the key namespace is shared

    def test_switch_to_another_tenants_profile_is_refused_unless_forced(self, profiles_path: Path):
        pm = ProfileManager(str(profiles_path), tenant_id="quellex")
        assert pm.switch_profile("parhelion", persist=False) is False
        assert pm.active_profile_key == "default"
        assert pm.switch_profile("parhelion", persist=False, force=True) is True  # ACTIVE_PROFILE pin
        assert pm.active_profile.database == "rag_parhelion"

    def test_created_profiles_belong_to_the_manager_tenant(self, profiles_path: Path):
        pm = ProfileManager(str(profiles_path), tenant_id="quellex")
        assert pm.create_profile(key="kanzlei-neu", name="Neu", documents_folders=["/app/mounts/neu"])
        assert _on_disk(profiles_path)["profiles"]["kanzlei-neu"]["tenant"] == "quellex"

        unbound = ProfileManager(str(profiles_path))
        assert unbound.create_profile(key="cli-made", name="CLI", documents_folders=["/x"])
        assert "tenant" not in _on_disk(profiles_path)["profiles"]["cli-made"]

    def test_edits_and_deletes_of_hidden_profiles_are_refused(self, profiles_path: Path):
        pm = ProfileManager(str(profiles_path), tenant_id="quellex")
        before = profiles_path.read_text(encoding="utf-8")
        assert pm.update_profile("parhelion", name="Mine") is False
        assert pm.delete_profile("tagged-rh") is False
        assert pm.add_cloud_source("parhelion", "c1", provider_type=_cloud_type()) is None
        assert profiles_path.read_text(encoding="utf-8") == before

    def test_claim_and_release(self, profiles_path: Path):
        pm = ProfileManager(str(profiles_path), tenant_id="quellex")
        assert pm.update_profile("synthetic-kanzlei", tenant="quellex")
        assert _on_disk(profiles_path)["profiles"]["synthetic-kanzlei"]["tenant"] == "quellex"
        assert ProfileManager(str(profiles_path), tenant_id="recallhub").get_profile("synthetic-kanzlei") is None
        assert pm.update_profile("synthetic-kanzlei", tenant="")
        assert "tenant" not in _on_disk(profiles_path)["profiles"]["synthetic-kanzlei"]

    def test_legacy_file_without_tenant_fields_loads_unchanged(self, profiles_path: Path):
        """A Quellex-only install with untagged profiles keeps working."""
        pm = ProfileManager(str(profiles_path), tenant_id="quellex")
        assert pm.switch_profile("synthetic-kanzlei", persist=False)
        assert pm.switch_profile("diejuristin", persist=False)
        assert profile_tenant("diejuristin", pm.get_profile("diejuristin")) == "quellex"


def _cloud_type():
    from src.profile import CloudSourceType

    return CloudSourceType.WEBDAV


def _ps_function(source: str, name: str) -> str:
    """Text of a PowerShell function (brace-matched) from a script."""
    start = source.index(f"function {name} {{")
    depth = 0
    for i in range(source.index("{", start), len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError(f"unbalanced function {name}")


class TestOfflineScriptsFollowTheProfileRules:
    """scripts/full-backup.ps1 and full-restore.ps1 classify profiles like src/profile.py."""

    def _scripts(self) -> List[str]:
        root = Path(__file__).resolve().parents[2] / "scripts"
        return [
            (root / name).read_text(encoding="utf-8").replace("\r\n", "\n")
            for name in ("full-backup.ps1", "full-restore.ps1")
        ]

    def test_both_scripts_share_one_parser(self):
        backup, restore = self._scripts()
        assert _ps_function(backup, "Get-ProfileTenants") == _ps_function(restore, "Get-ProfileTenants")

    def test_legacy_owners_match_the_python_table(self):
        import re

        function = _ps_function(self._scripts()[0], "Get-ProfileTenants")
        table = re.search(r"\$legacy = @\{(.*?)\}", function).group(1)
        owners = dict(re.findall(r'"([^"]+)"\s*=\s*"([^"]+)"', table))
        assert owners == LEGACY_PROFILE_TENANTS

    def test_quellex_maps_to_diejuristin(self):
        for script in self._scripts():
            assert "rag_parhelion" not in script
            assert 'QUELLEX_ACTIVE_PROFILE   = "diejuristin"' in script


# ==================== Safe profiles.yaml writes ====================


class TestConcurrentWrites:
    def test_two_backends_keep_each_others_edits(self, profiles_path: Path):
        recallhub = ProfileManager(str(profiles_path), tenant_id="recallhub")
        quellex = ProfileManager(str(profiles_path), tenant_id="quellex")

        assert recallhub.create_profile(key="rh-new", name="RH new", documents_folders=["/a"])
        # quellex still holds the old file in memory; its write must not drop rh-new
        assert quellex.update_profile("diejuristin", documents_folders=["/app/mounts/akten", "/app/mounts/texte"])

        on_disk = _on_disk(profiles_path)["profiles"]
        assert "rh-new" in on_disk
        assert on_disk["diejuristin"]["documents_folders"] == ["/app/mounts/akten", "/app/mounts/texte"]
        assert "rh-new" in quellex.list_all_profiles()  # the write refreshed its view

    def test_mutation_does_not_overwrite_an_unparseable_file(self, profiles_path: Path):
        pm = ProfileManager(str(profiles_path))
        profiles_path.write_text("profiles: [unclosed\n", encoding="utf-8")
        assert pm.create_profile(key="x", name="X", documents_folders=["/x"]) is False
        assert profiles_path.read_text(encoding="utf-8") == "profiles: [unclosed\n"

    def test_empty_read_keeps_the_loaded_profiles(self, profiles_path: Path):
        pm = ProfileManager(str(profiles_path))
        profiles_path.write_text("", encoding="utf-8")
        pm.reload(force=True)
        assert "diejuristin" in pm.list_profiles()

    def test_save_profile_replaces_one_profile(self, profiles_path: Path):
        pm = ProfileManager(str(profiles_path), tenant_id="quellex")
        profile = pm.get_profile("diejuristin").model_copy(deep=True)
        profile.llm_model = "gemma4:26b"
        assert pm.save_profile("diejuristin", profile)
        assert _on_disk(profiles_path)["profiles"]["diejuristin"]["llm_model"] == "gemma4:26b"
        assert pm.save_profile("parhelion", pm.list_all_profiles()["parhelion"]) is False


class TestActiveProfile:
    def test_in_memory_switch_does_not_touch_the_file(self, profiles_path: Path):
        pm = ProfileManager(str(profiles_path), tenant_id="quellex")
        before = profiles_path.read_text(encoding="utf-8")
        assert pm.switch_profile("diejuristin", persist=False)
        assert profiles_path.read_text(encoding="utf-8") == before

    def test_persisted_switch_writes_the_tenant_slot_only(self, profiles_path: Path):
        quellex = ProfileManager(str(profiles_path), tenant_id="quellex")
        recallhub = ProfileManager(str(profiles_path), tenant_id="recallhub")
        assert quellex.switch_profile("diejuristin")
        assert recallhub.switch_profile("parhelion")

        on_disk = _on_disk(profiles_path)
        assert on_disk["active_profile"] == "default"
        assert on_disk["active_profiles"] == {"quellex": "diejuristin", "recallhub": "parhelion"}
        # a fresh backend of each tenant starts on its own slot
        assert ProfileManager(str(profiles_path), tenant_id="quellex").active_profile_key == "diejuristin"
        assert ProfileManager(str(profiles_path), tenant_id="recallhub").active_profile_key == "parhelion"
        assert ProfileManager(str(profiles_path)).active_profile_key == "default"

    def test_reload_keeps_the_process_active_profile(self, profiles_path: Path):
        quellex = ProfileManager(str(profiles_path), tenant_id="quellex")
        assert quellex.switch_profile("diejuristin", persist=False)
        # another process changes the file-wide default and the profiles
        ProfileManager(str(profiles_path)).switch_profile("parhelion")
        assert quellex.reload(force=True)
        assert quellex.active_profile_key == "diejuristin"

    def test_inherited_active_profile_of_another_tenant_is_skipped(self, profiles_path: Path):
        text = profiles_path.read_text(encoding="utf-8").replace("active_profile: default", "active_profile: parhelion")
        profiles_path.write_text(text, encoding="utf-8")
        assert ProfileManager(str(profiles_path), tenant_id="quellex").active_profile_key == "default"
        assert ProfileManager(str(profiles_path), tenant_id="recallhub").active_profile_key == "parhelion"

    def test_env_pin_is_not_written_to_the_file(self, profiles_path: Path, monkeypatch: pytest.MonkeyPatch):
        settings_mod = pytest.importorskip("src.settings")
        profile_mod = pytest.importorskip("src.profile")
        monkeypatch.setenv("PROFILES_PATH", str(profiles_path))
        monkeypatch.setenv("ACTIVE_PROFILE", "diejuristin")
        monkeypatch.setenv("TENANT_ID", "quellex")
        monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
        monkeypatch.setenv("LLM_API_KEY", "test")
        monkeypatch.setenv("EMBEDDING_API_KEY", "test")
        monkeypatch.setattr(settings_mod, "_env_profile_applied", False)
        profile_mod.reset_profile_manager()
        before = profiles_path.read_text(encoding="utf-8")
        try:
            loaded = settings_mod.load_settings()
            assert loaded.mongodb_database == "rag_diejuristin"
            pm = profile_mod.get_profile_manager()
            assert pm.tenant_id == "quellex"  # bound from TENANT_ID
            assert "parhelion" not in pm.list_profiles()
            assert profiles_path.read_text(encoding="utf-8") == before
        finally:
            profile_mod.reset_profile_manager()


# ==================== Profile endpoints ====================


def _user(**overrides: Any) -> UserResponse:
    base: Dict[str, Any] = {
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
def quellex_pm(profiles_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProfileManager:
    pm = ProfileManager(str(profiles_path), tenant_id="quellex")
    monkeypatch.setattr(profiles_module, "get_profile_manager", lambda: pm)
    monkeypatch.setattr(settings, "tenant_id", "quellex")

    async def accessible(_request, _user_id):
        return list(pm.list_profiles())

    monkeypatch.setattr(profiles_module, "get_user_accessible_profiles", accessible)
    return pm


@pytest.fixture
def super_client(app, quellex_pm) -> TestClient:
    app.dependency_overrides[require_org_admin] = lambda: SUPER
    return TestClient(app)


class TestProfileEndpoints:
    def test_listing_shows_own_and_shared_profiles_with_tenant(self, super_client: TestClient):
        response = super_client.get("/api/v1/profiles")
        assert response.status_code == 200, response.text
        profiles = response.json()["profiles"]
        assert set(profiles) == {"default", "diejuristin", "synthetic-kanzlei"}
        assert profiles["diejuristin"]["tenant"] == "quellex"
        assert profiles["synthetic-kanzlei"]["tenant"] is None

    def test_other_tenants_profile_is_404(self, super_client: TestClient):
        assert super_client.get("/api/v1/profiles/parhelion").status_code == 404
        assert super_client.put("/api/v1/profiles/parhelion", json={"name": "x"}).status_code == 404
        assert super_client.delete("/api/v1/profiles/tagged-rh").status_code == 404
        assert super_client.post("/api/v1/profiles/switch", json={"profile_key": "parhelion"}).status_code == 404

    def test_create_stores_the_tenant_and_rejects_taken_keys(
        self, super_client: TestClient, profiles_path: Path
    ):
        taken = super_client.post(
            "/api/v1/profiles/create",
            json={"key": "parhelion", "name": "Mine", "documents_folders": ["/x"]},
        )
        assert taken.status_code == 400
        created = super_client.post(
            "/api/v1/profiles/create",
            json={"key": "neu", "name": "Neu", "documents_folders": ["/x"]},
        )
        assert created.status_code == 200, created.text
        assert _on_disk(profiles_path)["profiles"]["neu"]["tenant"] == "quellex"

    def test_superadmin_claims_but_cannot_give_away(self, super_client: TestClient, profiles_path: Path):
        claimed = super_client.put("/api/v1/profiles/synthetic-kanzlei", json={"tenant": "quellex"})
        assert claimed.status_code == 200, claimed.text
        assert _on_disk(profiles_path)["profiles"]["synthetic-kanzlei"]["tenant"] == "quellex"
        given = super_client.put("/api/v1/profiles/synthetic-kanzlei", json={"tenant": "recallhub"})
        assert given.status_code == 400

    def test_org_admin_cannot_change_the_tenant(self, app, quellex_pm, profiles_path: Path):
        app.dependency_overrides[require_org_admin] = lambda: ORG
        response = TestClient(app).put("/api/v1/profiles/synthetic-kanzlei", json={"tenant": "quellex"})
        assert response.status_code == 403
        assert "tenant" not in _on_disk(profiles_path)["profiles"]["synthetic-kanzlei"]


class TestAccessHelpers:
    @pytest.mark.asyncio
    async def test_grants_for_hidden_profiles_are_ignored(self, profiles_path: Path, monkeypatch: pytest.MonkeyPatch):
        from backend.routers import auth as auth_module
        import src.profile as profile_mod

        pm = ProfileManager(str(profiles_path), tenant_id="quellex")
        monkeypatch.setattr(profile_mod, "get_profile_manager", lambda *a, **k: pm)
        users = SimpleNamespace(find_one=AsyncMock(return_value={"_id": "org", "is_admin": False}))
        grants = [{"user_id": "org", "profile_key": "diejuristin"}, {"user_id": "org", "profile_key": "parhelion"}]

        class _Cursor:
            def __init__(self, docs):
                self._docs = iter(docs)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self._docs)
                except StopIteration:
                    raise StopAsyncIteration

        access = SimpleNamespace(
            find=lambda query: _Cursor(grants),
            find_one=AsyncMock(return_value={"user_id": "org", "profile_key": "parhelion"}),
        )
        monkeypatch.setattr(auth_module, "get_users_collection", AsyncMock(return_value=users))
        monkeypatch.setattr(auth_module, "get_profile_access_collection", AsyncMock(return_value=access))

        assert await auth_module.get_user_accessible_profiles(SimpleNamespace(), "org") == ["diejuristin"]
        assert await auth_module.user_has_profile_access(SimpleNamespace(), "org", "parhelion") is False


# ==================== Chat telemetry ====================


class TestTelemetryPerTenant:
    def _records(self, tenant: str, prefix: str) -> List[Dict[str, Any]]:
        return [
            {"record_id": f"{prefix}-1", "tenant": tenant, "session_id": "s", "model": "m", "latency_ms": 10, "prompt": f"{prefix} prompt"},
            {"record_id": f"{prefix}-2", "tenant": tenant, "session_id": "s", "model": "m", "latency_ms": 20, "prompt": f"{prefix} prompt"},
        ]

    @pytest.mark.asyncio
    async def test_service_writes_into_the_tenant_folder(self, tmp_path: Path):
        from backend.models.telemetry import TelemetryRecord
        from backend.services.telemetry_service import TelemetryService

        service = TelemetryService(storage_path=str(tmp_path), pii_mode="raw_only", tenant="quellex")
        today = datetime.utcnow().strftime("%Y-%m-%d")
        record = TelemetryRecord(
            session_id="s", user_id="u", tenant="quellex", prompt_original="Hallo",
            prompt_pseudonymized="Hallo", response_pseudonymized="Antwort",
        )
        await service._write_raw_record(record)
        await service._write_record(record)
        assert (tmp_path / "quellex" / "raw" / f"{today}.jsonl").is_file()
        assert (tmp_path / "quellex" / f"{today}.jsonl").is_file()
        assert not (tmp_path / f"{today}.jsonl").exists()

    @pytest.mark.asyncio
    async def test_viewer_shows_only_own_records_including_legacy_files(self, tmp_path: Path):
        from backend.routers import telemetry as telemetry_router
        from backend.services.telemetry_service import TelemetryService

        today = datetime.utcnow().date().isoformat()
        legacy = self._records("quellex", "q-old") + self._records("recallhub", "r-old")
        (tmp_path / "raw").mkdir()
        (tmp_path / f"{today}.jsonl").write_text("\n".join(json.dumps(r) for r in legacy) + "\n", encoding="utf-8")
        (tmp_path / "raw" / f"{today}.jsonl").write_text(json.dumps(self._records("recallhub", "r-raw")[0]) + "\n", encoding="utf-8")
        service = TelemetryService(storage_path=str(tmp_path), tenant="quellex")
        (tmp_path / "quellex" / f"{today}.jsonl").write_text(
            json.dumps(self._records("quellex", "q-new")[0]) + "\n", encoding="utf-8"
        )

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(telemetry=service)))
        user = SUPER

        files = (await telemetry_router.list_telemetry_files(request, user))["files"]
        assert [(f.date, f.record_count, f.has_raw) for f in files] == [(today, 3, False)]

        page = await telemetry_router.get_telemetry_records(request, today, "protected", 0, 50, user)
        assert {r["record_id"] for r in page["records"]} == {"q-new-1", "q-old-1", "q-old-2"}

        with pytest.raises(HTTPException) as other:
            await telemetry_router.get_telemetry_record(request, "r-old-1", "protected", user)
        assert other.value.status_code == 404
        with pytest.raises(HTTPException) as raw_other:
            await telemetry_router.get_telemetry_records(request, today, "raw", 0, 50, user)
        assert raw_other.value.status_code == 404

        found = await telemetry_router.search_telemetry_records(
            request, telemetry_router.TelemetrySearchRequest(query_text="old", limit=10), user
        )
        assert {r["record_id"] for r in found["records"]} == {"q-old-1", "q-old-2"}
        stats = await telemetry_router.get_telemetry_stats(request, range="all", user=user)
        assert stats.records_count == 3

    @pytest.mark.asyncio
    async def test_records_date_must_be_a_date(self, tmp_path: Path):
        from backend.routers import telemetry as telemetry_router
        from backend.services.telemetry_service import TelemetryService

        service = TelemetryService(storage_path=str(tmp_path), tenant="quellex")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(telemetry=service)))
        with pytest.raises(HTTPException) as bad:
            await telemetry_router.get_telemetry_records(request, "../recallhub/2026-10-19", "protected", 0, 50, SUPER)
        assert bad.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rotation_covers_tenant_and_legacy_folders(self, tmp_path: Path):
        from backend.services.telemetry_service import TelemetryService

        service = TelemetryService(storage_path=str(tmp_path), tenant="quellex", retention_days=7)
        old = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        for folder in (tmp_path, tmp_path / "raw", tmp_path / "quellex", tmp_path / "quellex" / "raw"):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{old}.jsonl").write_text("{}\n", encoding="utf-8")
        await service.rotate_old_files()
        assert not list(tmp_path.rglob(f"{old}.jsonl"))


# ==================== Personal databases ====================


class TestPersonalDatabases:
    def test_names_carry_tenant_and_user_id(self):
        from backend.agent.federated_search import personal_database_name

        assert personal_database_name("user_rag", "quellex", "64f0c0ffee") == "user_rag_quellex_64f0c0ffee"
        assert personal_database_name("user_rag", "quellex", "u1") != personal_database_name("user_rag", "recallhub", "u1")
        assert personal_database_name("cloud", "Quellex", "a.b@c/d") == "cloud_quellex_a_b_c_d"

    def test_long_names_are_cut_with_a_hash(self):
        from backend.agent.federated_search import personal_database_name

        first = personal_database_name("user_rag", "quellex", "x" * 80)
        second = personal_database_name("user_rag", "quellex", "x" * 79 + "y")
        assert len(first.encode("utf-8")) <= 63
        assert first != second

    def test_accessible_sources_use_the_backend_tenant(self, monkeypatch: pytest.MonkeyPatch):
        from unittest.mock import MagicMock

        from backend.agent import federated_search as fs_module
        from backend.agent.schemas import DataSourceType

        monkeypatch.setattr(fs_module.settings, "tenant_id", "quellex")
        fs = fs_module.FederatedSearch(mongo_client=MagicMock())
        sources = fs.get_accessible_sources(user_id="u42", user_email="anna@kanzlei.at")
        by_type = {s.type: s.database for s in sources}
        assert by_type[DataSourceType.PERSONAL] == "user_rag_quellex_u42"
        assert by_type[DataSourceType.CLOUD_PRIVATE] == "cloud_quellex_u42"


# ==================== API backups ====================


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, cond in query.items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in cond):
                return False
        elif isinstance(cond, dict) and "$exists" in cond:
            if (key in doc) != cond["$exists"]:
                return False
        elif cond is None:
            if doc.get(key) is not None:
                return False
        elif doc.get(key) != cond:
            return False
    return True


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, key: str, direction: int = 1) -> "_Cursor":
        self._docs = sorted(self._docs, key=lambda d: d.get(key), reverse=direction == -1)
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        docs = [dict(d) for d in self._docs]
        return docs if length is None else docs[:length]

    def __aiter__(self):
        self._iter = iter([dict(d) for d in self._docs])
        return self

    async def __anext__(self) -> Dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _Collection:
    def __init__(self) -> None:
        self.docs: Dict[Any, Dict[str, Any]] = {}

    def find(self, query: Optional[Dict[str, Any]] = None, *_args: Any) -> _Cursor:
        return _Cursor([d for d in self.docs.values() if _matches(d, query or {})])

    async def find_one(self, query: Dict[str, Any], sort: Any = None) -> Optional[Dict[str, Any]]:
        for doc in self.docs.values():
            if _matches(doc, query):
                return dict(doc)
        return None

    async def replace_one(self, flt: Dict[str, Any], doc: Dict[str, Any], upsert: bool = False) -> None:
        self.docs[flt["_id"]] = dict(doc)

    async def insert_one(self, doc: Dict[str, Any]) -> None:
        self.docs[doc["_id"]] = dict(doc)

    async def update_one(self, flt: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> None:
        for doc in self.docs.values():
            if _matches(doc, flt):
                doc.update(update.get("$set", {}))

    async def delete_one(self, flt: Dict[str, Any]) -> None:
        for key, doc in list(self.docs.items()):
            if _matches(doc, flt):
                del self.docs[key]
                return


class _Database(dict):
    def __missing__(self, name: str) -> _Collection:
        self[name] = _Collection()
        return self[name]


class _Client(dict):
    def __missing__(self, name: str) -> _Database:
        self[name] = _Database()
        return self[name]


def _metadata(backup_id: str, profile_key: str, tenant: Optional[str], file_path: str = "", **extra: Any) -> Dict[str, Any]:
    doc = {
        "_id": backup_id,
        "backup_id": backup_id,
        "backup_type": "full",
        "status": "completed",
        "profile_key": profile_key,
        "database_name": f"rag_{profile_key}",
        "created_at": datetime(2026, 10, 1, 12, 0, 0),
        "file_path": file_path,
        "size_bytes": 7,
        "collections_included": [],
    }
    if tenant is not None:
        doc["tenant"] = tenant
    doc.update(extra)
    return doc


@pytest.fixture
def backup_service(profiles_path: Path, tmp_path: Path):
    from backend.services.backup_service import BackupService

    client = _Client()
    db_manager = SimpleNamespace(client=client, db=client["rag_diejuristin"], _current_database="rag_diejuristin")
    pm = ProfileManager(str(profiles_path), tenant_id="quellex")
    pm.switch_profile("diejuristin", persist=False)
    return BackupService(db_manager, profiles_manager=pm, tenant_id="quellex", backup_location=str(tmp_path / "backups"))


class TestBackupsPerTenant:
    @pytest.mark.asyncio
    async def test_full_backup_resolves_the_active_profile_into_the_tenant_folder(self, backup_service, tmp_path: Path):
        tasks: List[Any] = []
        backup = await backup_service.start_full_backup(
            background_tasks=SimpleNamespace(add_task=lambda *a, **k: tasks.append((a, k)))
        )
        assert backup.profile_key == "diejuristin"  # not the old "default" label
        assert backup.database_name == "rag_diejuristin"
        assert backup.tenant == "quellex"
        assert Path(backup.file_path).parent == tmp_path / "backups" / "quellex" / "full"
        assert tasks, "the export runs as a background task"

    @pytest.mark.asyncio
    async def test_other_tenants_profile_cannot_be_backed_up(self, backup_service):
        from backend.services.backup_service import BackupScopeError

        with pytest.raises(BackupScopeError):
            await backup_service.start_full_backup(profile_key="parhelion", background_tasks=SimpleNamespace(add_task=lambda *a, **k: None))

    @pytest.mark.asyncio
    async def test_listing_and_lookup_cover_own_and_legacy_backups(self, backup_service):
        meta = backup_service.db_manager.db["backups_metadata"]
        for doc in (
            _metadata("own", "diejuristin", "quellex"),
            _metadata("theirs", "parhelion", "recallhub"),
            _metadata("legacy-own", "diejuristin", None),
            _metadata("legacy-shared", "synthetic-kanzlei", None),
            _metadata("legacy-theirs", "tagged-rh", None),
        ):
            await meta.insert_one(doc)

        listed = {b.backup_id for b in await backup_service.list_backups()}
        assert listed == {"own", "legacy-own", "legacy-shared"}
        assert await backup_service.get_backup("theirs") is None
        assert await backup_service.get_backup("legacy-theirs") is None
        assert await backup_service.delete_backup("theirs") is False
        assert "theirs" in meta.docs

    @pytest.mark.asyncio
    async def test_restore_target_must_be_a_tenant_database(self, backup_service, tmp_path: Path):
        backup_dir = tmp_path / "backups" / "quellex" / "full" / "own"
        backup_dir.mkdir(parents=True)
        await backup_service.db_manager.db["backups_metadata"].insert_one(
            _metadata("own", "diejuristin", "quellex", file_path=str(backup_dir), database_name="rag_diejuristin")
        )

        refused = await backup_service.restore_from_backup("own", target_database="rag_parhelion")
        assert refused.success is False
        assert "does not belong" in (refused.error_message or "")

        allowed = await backup_service.restore_from_backup("own", target_database="rag_synth")
        assert "does not belong" not in (allowed.error_message or "")

    @pytest.mark.asyncio
    async def test_storage_stats_count_only_own_backups(self, backup_service, tmp_path: Path):
        own_dir = tmp_path / "backups" / "quellex" / "full" / "own"
        theirs_dir = tmp_path / "backups" / "recallhub" / "full" / "theirs"
        for folder, size in ((own_dir, 10), (theirs_dir, 1000)):
            folder.mkdir(parents=True)
            (folder / "chunks.json.gz").write_bytes(b"x" * size)
        meta = backup_service.db_manager.db["backups_metadata"]
        await meta.insert_one(_metadata("own", "diejuristin", "quellex", file_path=str(own_dir)))
        await meta.insert_one(_metadata("theirs", "parhelion", "recallhub", file_path=str(theirs_dir)))

        stats = await backup_service.get_storage_stats()
        assert stats.total_backups == 1
        assert stats.total_size_bytes == 10
        assert stats.backups_by_profile == {"diejuristin": 1}

    @pytest.mark.asyncio
    async def test_initialize_creates_the_tenant_folders(self, backup_service, tmp_path: Path):
        await backup_service.initialize_config()
        assert (tmp_path / "backups" / "quellex" / "checkpoint").is_dir()
        assert not (tmp_path / "backups" / "checkpoint").exists()
