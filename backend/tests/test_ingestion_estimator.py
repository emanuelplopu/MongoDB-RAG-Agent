"""Tests for the pre-ingestion estimator.

Covers the estimator math over a mock corpus and MongoDB responses, and the
``GET /api/v1/profiles/{key}/ingest/estimate`` endpoint (access control + shape).
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.services.ingestion_estimator import (
    IngestionEstimator,
    human_duration,
)


# --------------------------------------------------------------------------- fakes


class _FakeCursor:
    """Async cursor supporting both ``async for`` and ``.sort().limit().to_list()``."""

    def __init__(self, documents: Optional[List[Dict[str, Any]]] = None):
        self._documents = list(documents or [])

    def sort(self, *_args, **_kwargs) -> "_FakeCursor":
        return self

    def limit(self, *_args, **_kwargs) -> "_FakeCursor":
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        if length is None:
            return list(self._documents)
        return list(self._documents[:length])

    def __aiter__(self) -> "_FakeCursor":
        self._iter = iter(self._documents)
        return self

    async def __anext__(self) -> Dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as exc:  # pragma: no cover - iterator protocol
            raise StopAsyncIteration from exc


class _FakeCollection:
    """A canned collection: ``find`` / ``aggregate`` / ``find_one`` return fixtures."""

    def __init__(
        self,
        *,
        find_docs: Optional[List[Dict[str, Any]]] = None,
        aggregate_rows: Optional[List[Dict[str, Any]]] = None,
        one: Optional[Dict[str, Any]] = None,
    ):
        self._find_docs = find_docs or []
        self._aggregate_rows = aggregate_rows or []
        self._one = one

    def find(self, *_args, **_kwargs) -> _FakeCursor:
        return _FakeCursor(self._find_docs)

    def aggregate(self, *_args, **_kwargs) -> _FakeCursor:
        return _FakeCursor(self._aggregate_rows)

    async def find_one(self, *_args, **_kwargs) -> Optional[Dict[str, Any]]:
        return self._one


class _PyMongoStyleCollection(_FakeCollection):
    """Like ``_FakeCollection`` but ``aggregate`` is a coroutine (PyMongo Async style)."""

    async def aggregate(self, *_args, **_kwargs) -> _FakeCursor:  # type: ignore[override]
        return _FakeCursor(self._aggregate_rows)


class _FakeDB:
    """Maps collection name -> :class:`_FakeCollection`; unknown names are empty."""

    def __init__(self, collections: Dict[str, _FakeCollection]):
        self._collections = collections

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._collections.get(name, _FakeCollection())


# --------------------------------------------------------------------------- corpus


def _make_corpus(tmp_path) -> str:
    """Create a mixed corpus and return the folder path.

    7 supported files: 3x .txt (4000 B), 2x .pdf (1,000,000 B), 1x .png
    (500,000 B), 1x .md in a subfolder (2000 B); plus an ignored .zip.
    Total supported bytes = 2,514,000.
    """
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "a.txt").write_bytes(b"x" * 4000)
    (root / "b.txt").write_bytes(b"y" * 4000)
    (root / "c.txt").write_bytes(b"z" * 4000)
    (root / "doc1.pdf").write_bytes(b"%PDF" + b"0" * (1_000_000 - 4))
    (root / "doc2.pdf").write_bytes(b"%PDF" + b"1" * (1_000_000 - 4))
    (root / "scan.png").write_bytes(b"\x89PNG" + b"0" * (500_000 - 4))
    (root / "ignore.zip").write_bytes(b"PK" + b"0" * 1000)
    sub = root / "sub"
    sub.mkdir()
    (sub / "note.md").write_bytes(b"# " + b"m" * 1998)
    return str(root)


EXPECTED_SUPPORTED = 7
EXPECTED_BYTES = 3 * 4000 + 2 * 1_000_000 + 500_000 + 2000  # 2,514,000


# --------------------------------------------------------------------------- unit tests


@pytest.mark.asyncio
async def test_discovery_counts_and_bytes(tmp_path):
    """Full-mode scan counts exactly the supported files and sums their bytes."""
    folder = _make_corpus(tmp_path)
    estimator = IngestionEstimator(_FakeDB({}), embedding_model="text-embedding-3-small")

    est = await estimator.estimate([folder], "p1", incremental=False, sample_files=0)

    assert est.scanned_files == EXPECTED_SUPPORTED
    assert est.file_count == EXPECTED_SUPPORTED
    assert est.skipped_existing == 0
    assert est.total_bytes == EXPECTED_BYTES
    assert est.estimated_tokens > 0
    assert est.estimated_chunks > 0
    # No history / no benchmark -> rough, default-based.
    assert est.token_method in {"heuristic", "sampled"}
    assert est.time_method == "defaults"
    assert est.is_rough is True
    # Ranges bracket the central value.
    assert est.estimated_tokens_low <= est.estimated_tokens <= est.estimated_tokens_high
    assert est.estimated_time_low_seconds <= est.estimated_time_seconds <= est.estimated_time_high_seconds
    # by_extension covers every discovered type.
    exts = {b.extension for b in est.by_extension}
    assert exts == {"txt", "pdf", "png", "md"}


@pytest.mark.asyncio
async def test_incremental_skips_existing(tmp_path):
    """Incremental mode skips files whose relative source is already ingested."""
    folder = _make_corpus(tmp_path)
    skip_source = os.path.relpath(os.path.join(folder, "a.txt"), folder)
    db = _FakeDB({"documents": _FakeCollection(find_docs=[{"source": skip_source}])})
    estimator = IngestionEstimator(db, embedding_model="text-embedding-3-small")

    est = await estimator.estimate([folder], "p1", incremental=True, sample_files=0)

    assert est.incremental is True
    assert est.scanned_files == EXPECTED_SUPPORTED
    assert est.skipped_existing == 1
    assert est.file_count == EXPECTED_SUPPORTED - 1
    assert est.total_bytes == EXPECTED_BYTES - 4000


@pytest.mark.asyncio
async def test_history_drives_methods_and_time(tmp_path):
    """Measured history makes token/time methods 'history' and uses real per-file times."""
    folder = _make_corpus(tmp_path)
    stats_rows = [
        {"_id": "txt", "count": 50, "avg_time_ms": 500, "max_time_ms": 900, "total_chunks": 200, "total_bytes": 200_000},
        {"_id": "pdf", "count": 40, "avg_time_ms": 20000, "max_time_ms": 60000, "total_chunks": 4000, "total_bytes": 40_000_000},
        {"_id": "png", "count": 10, "avg_time_ms": 8000, "max_time_ms": 15000, "total_chunks": 20, "total_bytes": 5_000_000},
        {"_id": "md", "count": 15, "avg_time_ms": 400, "max_time_ms": 700, "total_chunks": 60, "total_bytes": 60_000},
    ]
    db = _FakeDB({
        "ingestion_stats": _FakeCollection(aggregate_rows=stats_rows),
        "chunks": _FakeCollection(aggregate_rows=[{"_id": None, "avg": 220.0}]),
        "ingestion_config": _FakeCollection(one={"_id": "performance_config", "max_concurrent_files": 4}),
    })
    estimator = IngestionEstimator(db, embedding_model="text-embedding-3-small")

    est = await estimator.estimate([folder], "p1", incremental=False, sample_files=0)

    assert est.token_method == "history"
    assert est.time_method == "history"
    assert est.concurrency == 4
    # PDF per-file avg time should dominate: its by_extension row carries the measured avg.
    pdf_row = next(b for b in est.by_extension if b.extension == "pdf")
    assert pdf_row.avg_processing_time_ms == 20000
    # Sequential pdf time = 2 files * 20s = 40s, /4 concurrency contributes ~10s.
    assert est.estimated_time_seconds > 0
    assert est.estimated_embedding_seconds is not None


@pytest.mark.asyncio
async def test_history_works_with_pymongo_async_aggregate(tmp_path):
    """Aggregates resolve whether the driver's aggregate is a cursor (Motor) or a coroutine (PyMongo Async)."""
    folder = _make_corpus(tmp_path)
    stats_rows = [
        {"_id": "pdf", "count": 40, "avg_time_ms": 20000, "max_time_ms": 60000, "total_chunks": 4000, "total_bytes": 40_000_000},
        {"_id": "txt", "count": 50, "avg_time_ms": 500, "max_time_ms": 900, "total_chunks": 200, "total_bytes": 200_000},
    ]
    db = _FakeDB({
        "ingestion_stats": _PyMongoStyleCollection(aggregate_rows=stats_rows),
        "chunks": _PyMongoStyleCollection(aggregate_rows=[{"_id": None, "avg": 210.0}]),
    })
    estimator = IngestionEstimator(db, embedding_model="text-embedding-3-small")

    est = await estimator.estimate([folder], "p1", incremental=False, sample_files=0)

    # If the coroutine-style aggregate were not awaited, history would be empty
    # and the method would fall back to 'heuristic'/'defaults'.
    assert est.token_method == "history"
    assert est.time_method == "history"


@pytest.mark.asyncio
async def test_benchmark_throughput_and_cost(tmp_path):
    """A benchmark record sets throughput, date and a non-zero cost for a priced model."""
    folder = _make_corpus(tmp_path)
    benchmark_doc = {
        "timestamp": "2026-10-10T12:00:00",
        "results": [
            {
                "model": "text-embedding-3-small",
                "tokens_processed": 100_000,
                "embedding_time_ms": 10_000,  # -> 10,000 tok/s
                "success": True,
            }
        ],
    }
    db = _FakeDB({"benchmark_results": _FakeCollection(find_docs=[benchmark_doc])})
    estimator = IngestionEstimator(db, embedding_model="text-embedding-3-small")

    est = await estimator.estimate([folder], "p1", incremental=False, sample_files=0)

    assert est.benchmark_date == "2026-10-10T12:00:00"
    assert est.embedding_throughput_tokens_per_sec == pytest.approx(10000.0, rel=0.01)
    assert est.cost_per_million_tokens == pytest.approx(0.02, rel=0.01)
    assert est.estimated_cost_usd > 0
    assert est.estimated_cost_low_usd <= est.estimated_cost_usd <= est.estimated_cost_high_usd
    # Embedding time component = tokens / throughput.
    assert est.estimated_embedding_seconds == pytest.approx(est.estimated_tokens / 10000.0, rel=0.05)


@pytest.mark.asyncio
async def test_local_model_has_zero_cost(tmp_path):
    """A local/free model has no per-token cost."""
    folder = _make_corpus(tmp_path)
    estimator = IngestionEstimator(_FakeDB({}), embedding_model="embeddinggemma-2", embedding_provider="llamacpp")

    est = await estimator.estimate([folder], "p1", incremental=False, sample_files=0)

    assert est.cost_per_million_tokens in (None, 0)
    assert est.estimated_cost_usd == 0
    assert est.estimated_cost_high_usd == 0


@pytest.mark.asyncio
async def test_missing_folder_reported(tmp_path):
    """A non-existent folder is reported and does not crash the estimate."""
    folder = _make_corpus(tmp_path)
    missing = str(tmp_path / "does_not_exist")
    estimator = IngestionEstimator(_FakeDB({}), embedding_model="text-embedding-3-small")

    est = await estimator.estimate([folder, missing], "p1", incremental=False, sample_files=0)

    assert missing in est.missing_folders
    assert est.file_count == EXPECTED_SUPPORTED


def test_human_duration_formatting():
    """Durations render as short, honest strings."""
    assert human_duration(0) == "<1 s"
    assert human_duration(45).endswith("s")
    assert human_duration(600).endswith("m")  # 10 minutes
    assert "h" in human_duration(3 * 3600 + 900)  # 3h15m
    assert "d" in human_duration(2 * 86400 + 3600)  # 2d1h


# --------------------------------------------------------------------------- endpoint tests


def _fake_profile_manager(folder: str):
    """Build a profile-manager stand-in exposing a single 'kanzlei' profile."""
    profile = SimpleNamespace(
        name="Kanzlei",
        description="",
        documents_folders=[folder],
        database="rag_kanzlei",
        collection_documents="documents",
        collection_chunks="chunks",
        embedding_model=None,
        llm_model=None,
        vector_index="vector_index",
        text_index="text_index",
    )
    pm = MagicMock()
    pm.list_profiles.return_value = {"kanzlei": profile}
    pm.get_profile.return_value = profile
    pm.active_profile_key = "kanzlei"
    return pm


def test_estimate_endpoint_returns_estimate(client: TestClient, tmp_path):
    """The endpoint returns a 200 estimate for an accessible profile (admin)."""
    folder = _make_corpus(tmp_path)
    with patch("backend.routers.profiles.get_profile_manager", return_value=_fake_profile_manager(folder)):
        response = client.get("/api/v1/profiles/kanzlei/ingest/estimate", params={"incremental": "false", "sample": 0})

    assert response.status_code == 200
    data = response.json()
    assert data["profile_key"] == "kanzlei"
    assert data["scanned_files"] == EXPECTED_SUPPORTED
    assert data["file_count"] == EXPECTED_SUPPORTED
    assert data["total_bytes"] == EXPECTED_BYTES
    assert data["is_rough"] is True
    assert isinstance(data["assumptions"], list) and data["assumptions"]


def test_estimate_endpoint_unknown_profile(client: TestClient, tmp_path):
    """An unknown profile key yields 404."""
    folder = _make_corpus(tmp_path)
    with patch("backend.routers.profiles.get_profile_manager", return_value=_fake_profile_manager(folder)):
        response = client.get("/api/v1/profiles/nope/ingest/estimate")
    assert response.status_code == 404


def test_estimate_endpoint_forbidden_without_access(app, tmp_path):
    """A non-admin without access to the profile is rejected with 403."""
    from backend.routers.auth import UserResponse, require_auth

    folder = _make_corpus(tmp_path)

    async def _non_admin_user() -> UserResponse:
        return UserResponse(
            id="user-2",
            email="user2@example.com",
            name="User Two",
            is_active=True,
            is_admin=False,
            created_at="2025-01-01T00:00:00",
        )

    app.dependency_overrides[require_auth] = _non_admin_user
    local_client = TestClient(app)
    try:
        with patch("backend.routers.profiles.get_profile_manager", return_value=_fake_profile_manager(folder)), \
                patch(
                    "backend.routers.profiles.get_user_accessible_profiles",
                    new=AsyncMock(return_value=[]),
                ):
            response = local_client.get("/api/v1/profiles/kanzlei/ingest/estimate")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(require_auth, None)
