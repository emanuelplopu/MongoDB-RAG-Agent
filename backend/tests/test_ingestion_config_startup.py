"""The stored ingestion performance/timeout config is applied at API startup.

Until 2026-10-24 ``load_ingestion_config_from_db`` existed but nothing called it, so
the per-file timeout saved from the Configuration page (or the timeout endpoint)
silently fell back to the 120 s default after every backend restart.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

system = pytest.importorskip("backend.routers.system")
ingest = pytest.importorskip("src.ingestion.ingest")
from backend.core.config import settings  # noqa: E402


@pytest.fixture
def restore_globals():
    """Put the module-level settings and timeout config back after the test."""
    keep = {
        name: getattr(settings, name)
        for name in ("ingestion_max_concurrent_files", "ingestion_thread_pool_workers", "ingestion_process_isolation")
    }
    cfg = ingest.get_timeout_config()
    timeout_keep = dict(base_timeout=cfg.base_timeout, max_timeout=cfg.max_timeout, pdf_multiplier=cfg.pdf_multiplier)
    yield
    for name, value in keep.items():
        setattr(settings, name, value)
    ingest.update_timeout_config(**timeout_keep)


async def test_stored_config_is_applied(restore_globals) -> None:
    doc = {
        "_id": system.INGESTION_CONFIG_DOC_ID,
        "max_concurrent_files": 16,
        "thread_pool_workers": 16,
        "timeout_config": {"base_timeout": 600, "max_timeout": 1800, "pdf_multiplier": 1.5},
    }
    collection = SimpleNamespace(find_one=AsyncMock(return_value=doc))
    db = SimpleNamespace(db={system.INGESTION_CONFIG_COLLECTION: collection})
    assert await system.load_ingestion_config_from_db(db) is True
    assert settings.ingestion_max_concurrent_files == 16
    assert settings.ingestion_thread_pool_workers == 16
    cfg = ingest.get_timeout_config()
    assert cfg.base_timeout == 600
    assert ingest.calculate_file_timeout(100 * 1024, file_path="/x/extract.pdf") == pytest.approx((600 + 100 / 1024 * 30) * 1.5)


async def test_missing_config_leaves_defaults(restore_globals) -> None:
    collection = SimpleNamespace(find_one=AsyncMock(return_value=None))
    db = SimpleNamespace(db={system.INGESTION_CONFIG_COLLECTION: collection})
    before = ingest.get_timeout_config().base_timeout
    assert await system.load_ingestion_config_from_db(db) is False
    assert ingest.get_timeout_config().base_timeout == before


def test_api_startup_calls_the_loader() -> None:
    """Guard against the regression: the lifespan must call the loader before resuming jobs."""
    main = pytest.importorskip("backend.main")
    source = inspect.getsource(main.lifespan)
    assert "load_ingestion_config_from_db(" in source
    assert source.index("load_ingestion_config_from_db(") < source.index("check_and_resume_interrupted_jobs(")
