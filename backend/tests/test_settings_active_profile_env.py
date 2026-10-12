"""Regression: ACTIVE_PROFILE must not undo explicit profile switches (it wiped the wrong DB on clean ingestion)."""

from __future__ import annotations

from pathlib import Path

import pytest

settings_mod = pytest.importorskip("src.settings")
profile_mod = pytest.importorskip("src.profile")

PROFILES = """active_profile: alpha
profiles:
  alpha:
    name: Alpha
    documents_folders: [/data/alpha]
    database: db_alpha
  beta:
    name: Beta
    documents_folders: [/data/beta]
    database: db_beta
"""


def test_env_profile_applies_once_and_explicit_switch_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text(PROFILES, encoding="utf-8")
    monkeypatch.setenv("PROFILES_PATH", str(path))
    monkeypatch.setenv("ACTIVE_PROFILE", "alpha")
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setenv("EMBEDDING_API_KEY", "test")
    monkeypatch.setattr(settings_mod, "_env_profile_applied", False)
    profile_mod.reset_profile_manager()
    try:
        first = settings_mod.load_settings()
        assert first.mongodb_database == "db_alpha"
        pm = profile_mod.get_profile_manager(str(path))
        assert pm.switch_profile("beta")
        again = settings_mod.load_settings()
        assert again.mongodb_database == "db_beta", "ACTIVE_PROFILE re-applied and undid the explicit switch"
    finally:
        profile_mod.reset_profile_manager()


def test_ingestion_router_verifies_target_database() -> None:
    source = (Path(__file__).resolve().parents[1] / "routers" / "ingestion.py").read_text(encoding="utf-8")
    assert "aborted before changing any data" in source
    assert "if not pm.switch_profile(config[\"profile\"])" in source


def test_ingestion_stop_is_raised_inside_the_pipeline_callback() -> None:
    source = (Path(__file__).resolve().parents[1] / "routers" / "ingestion.py").read_text(encoding="utf-8")
    sync_cb = source.split("        def progress_callback(current: int", 1)[1].split("asyncio.create_task", 1)[0]
    assert "if _stop_requested:" in sync_cb and "raise asyncio.CancelledError" in sync_cb
