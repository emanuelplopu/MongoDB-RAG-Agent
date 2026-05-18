"""Tests for :mod:`backend.services.resource_snapshot` (Task 71)."""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from backend.agent.strategy.scheduler_models import ResourceLimits
from backend.services import resource_snapshot as rs_mod
from backend.services.resource_snapshot import (
    RESOURCE_SNAPSHOT_COLLECTION_NAME,
    ResourceSnapshot,
    ResourceSnapshotCollector,
)
from backend.tests.strategy._fakes import _FakeAsyncDB


# ─────────────────────────────────────────────────────────────────────────────
# psutil fake
# ─────────────────────────────────────────────────────────────────────────────


class _FakeVM:
    def __init__(self, used: int, total: int, percent: float) -> None:
        self.used = used
        self.total = total
        self.percent = percent


class _FakeProc:
    def __init__(self, cmdline: list[str]) -> None:
        self.info = {"name": cmdline[0] if cmdline else "", "cmdline": cmdline}


def _make_fake_psutil(
    *,
    cpu_percent: float = 12.5,
    used_gb: float = 4.0,
    total_gb: float = 16.0,
    ram_pct: float = 25.0,
    cmdlines: list[list[str]] | None = None,
) -> types.SimpleNamespace:
    """Build a minimal ``psutil`` stand-in covering the surface we use."""
    procs = [
        _FakeProc(c) for c in (cmdlines or [["python", "manage.py"], ["bash"]])
    ]
    fake = types.SimpleNamespace(
        cpu_percent=lambda interval=0.5: cpu_percent,
        virtual_memory=lambda: _FakeVM(
            used=int(used_gb * (1024**3)),
            total=int(total_gb * (1024**3)),
            percent=ram_pct,
        ),
        process_iter=lambda attrs=None: iter(procs),
    )
    return fake


@pytest.fixture(autouse=True)
def _patch_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    """Always-on lightweight psutil so tests never block the loop or peek
    at host state. Individual tests can opt out by patching again."""
    fake = _make_fake_psutil()
    monkeypatch.setitem(sys.modules, "psutil", fake)
    # Import path inside resource_snapshot uses ``import psutil`` at call
    # time via ``_import_psutil``, so the sys.modules patch is enough.


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeHttpClient:
    def __init__(
        self,
        *,
        payload: dict[str, Any] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._payload = payload
        self._raise = raise_exc

    async def __aenter__(self) -> "_FakeHttpClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    async def get(self, url: str) -> _FakeHttpResponse:
        if self._raise is not None:
            raise self._raise
        return _FakeHttpResponse(self._payload or {})


def _patch_httpx(
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: dict[str, Any] | None = None,
    raise_exc: Exception | None = None,
) -> None:
    """Replace ``httpx.AsyncClient`` with the fake above for the test."""
    import httpx

    def _factory(*args: Any, **kwargs: Any) -> _FakeHttpClient:
        return _FakeHttpClient(payload=payload, raise_exc=raise_exc)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


def _make_collector(db: Any | None = None) -> ResourceSnapshotCollector:
    return ResourceSnapshotCollector(
        db,
        host_id="test-host",
        ttl_hours=24,
        cpu_sample_interval=0.0,
    )


# ─────────────────────────────────────────────────────────────────────────────
# capture()
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capture_populates_cpu_and_ram(monkeypatch: pytest.MonkeyPatch) -> None:
    """capture() must populate CPU/RAM metrics from psutil."""
    monkeypatch.setattr(rs_mod.shutil, "which", lambda _binary: None)
    _patch_httpx(monkeypatch, raise_exc=ConnectionError("offline"))

    db = _FakeAsyncDB()
    collector = _make_collector(db)
    snapshot = await collector.capture()

    assert isinstance(snapshot, ResourceSnapshot)
    assert snapshot.host_id == "test-host"
    assert snapshot.cpu_pct == pytest.approx(12.5)
    assert snapshot.ram_total_gb == pytest.approx(16.0, rel=1e-6)
    assert snapshot.ram_used_gb == pytest.approx(4.0, rel=1e-6)
    assert snapshot.ram_pct == pytest.approx(25.0)
    # Persisted to Mongo.
    persisted = list(db[RESOURCE_SNAPSHOT_COLLECTION_NAME]._docs)
    assert len(persisted) == 1
    assert persisted[0]["id"] == snapshot.id


# ─────────────────────────────────────────────────────────────────────────────
# GPU sampling
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capture_gpu_fields_none_when_nvidia_smi_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPU/VRAM fields must be None when nvidia-smi is not on PATH."""
    monkeypatch.setattr(rs_mod.shutil, "which", lambda _binary: None)
    _patch_httpx(monkeypatch, raise_exc=ConnectionError("offline"))

    collector = _make_collector(_FakeAsyncDB())
    snapshot = await collector.capture()

    assert snapshot.gpu_pct is None
    assert snapshot.vram_used_gb is None
    assert snapshot.vram_total_gb is None
    assert snapshot.vram_pct is None


@pytest.mark.asyncio
async def test_capture_gpu_fields_populated_from_nvidia_smi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPU/VRAM fields must reflect parsed nvidia-smi output."""
    monkeypatch.setattr(
        rs_mod.shutil,
        "which",
        lambda binary: "/usr/bin/nvidia-smi" if binary == "nvidia-smi" else None,
    )

    class _Completed:
        returncode = 0
        stdout = "42, 2048, 8192\n"
        stderr = ""

    def _fake_run(*args: Any, **kwargs: Any) -> _Completed:
        return _Completed()

    monkeypatch.setattr(rs_mod.subprocess, "run", _fake_run)
    _patch_httpx(monkeypatch, raise_exc=ConnectionError("offline"))

    collector = _make_collector(_FakeAsyncDB())
    snapshot = await collector.capture()

    assert snapshot.gpu_pct == pytest.approx(42.0)
    assert snapshot.vram_used_gb == pytest.approx(2048 / 1024.0)
    assert snapshot.vram_total_gb == pytest.approx(8192 / 1024.0)
    assert snapshot.vram_pct == pytest.approx(25.0)


# ─────────────────────────────────────────────────────────────────────────────
# Ollama probe
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capture_ollama_residency_populated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident models / aggregate VRAM must be parsed from /api/ps."""
    monkeypatch.setattr(rs_mod.shutil, "which", lambda _binary: None)
    _patch_httpx(
        monkeypatch,
        payload={
            "models": [
                {"name": "llama3.1:8b", "size_vram": 8 * (1024**3)},
                {"name": "mxbai-embed-large", "size_vram": 1 * (1024**3)},
            ]
        },
    )

    collector = _make_collector(_FakeAsyncDB())
    snapshot = await collector.capture()

    assert snapshot.ollama_resident_models == ["llama3.1:8b", "mxbai-embed-large"]
    assert snapshot.ollama_total_vram_gb == pytest.approx(9.0)


@pytest.mark.asyncio
async def test_capture_ollama_unreachable_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Ollama is unreachable, residency must degrade to ([], None)."""
    monkeypatch.setattr(rs_mod.shutil, "which", lambda _binary: None)
    _patch_httpx(monkeypatch, raise_exc=ConnectionError("nope"))

    collector = _make_collector(_FakeAsyncDB())
    snapshot = await collector.capture()

    assert snapshot.ollama_resident_models == []
    assert snapshot.ollama_total_vram_gb is None


# ─────────────────────────────────────────────────────────────────────────────
# is_safe_to_run
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_safe_to_run_blocks_on_cpu_overrun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CPU above the configured ceiling must produce an unsafe verdict."""
    monkeypatch.setitem(
        sys.modules, "psutil", _make_fake_psutil(cpu_percent=99.0, ram_pct=10.0)
    )
    monkeypatch.setattr(rs_mod.shutil, "which", lambda _binary: None)
    _patch_httpx(monkeypatch, raise_exc=ConnectionError("offline"))

    collector = _make_collector(_FakeAsyncDB())
    limits = ResourceLimits(
        max_cpu_utilization_pct=80,
        max_ram_usage_pct=95,
        pause_if_interactive_users=False,
        pause_if_backend_chat_active=False,
    )
    safe, reasons = await collector.is_safe_to_run(limits)

    assert safe is False
    assert any("CPU" in reason for reason in reasons)


@pytest.mark.asyncio
async def test_is_safe_to_run_passes_under_normal_conditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All metrics within ceilings → ``(True, [])``."""
    monkeypatch.setitem(
        sys.modules, "psutil", _make_fake_psutil(cpu_percent=10.0, ram_pct=20.0)
    )
    monkeypatch.setattr(rs_mod.shutil, "which", lambda _binary: None)
    _patch_httpx(monkeypatch, raise_exc=ConnectionError("offline"))

    collector = _make_collector(_FakeAsyncDB())
    limits = ResourceLimits(
        max_cpu_utilization_pct=85,
        max_ram_usage_pct=90,
        max_gpu_utilization_pct=90,
        pause_if_interactive_users=True,
        pause_if_backend_chat_active=True,
    )
    safe, reasons = await collector.is_safe_to_run(limits)

    assert safe is True
    assert reasons == []


# ─────────────────────────────────────────────────────────────────────────────
# Index management + latest()
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_indexes_creates_ttl_and_is_idempotent() -> None:
    """ensure_indexes() must register the TTL + (host_id, captured_at)
    indexes and be safe to call repeatedly."""
    db = _FakeAsyncDB()
    collector = _make_collector(db)

    await collector.ensure_indexes()
    await collector.ensure_indexes()  # second call must be a no-op

    created = db[RESOURCE_SNAPSHOT_COLLECTION_NAME].created_indexes
    assert len(created) == 2  # idempotent — no duplicate creation

    ttl_call = next(
        (c for c in created if c[0] == ("created_at",)),
        None,
    )
    assert ttl_call is not None, "TTL index on created_at not registered"
    assert ttl_call[1].get("expireAfterSeconds") == 24 * 3600

    compound_call = next(
        (
            c
            for c in created
            if c[0] and isinstance(c[0][0], list) and c[0][0][0] == ("host_id", 1)
        ),
        None,
    )
    assert compound_call is not None, "(host_id, captured_at) index missing"


@pytest.mark.asyncio
async def test_latest_returns_most_recent_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """latest() must return the most recent snapshot by captured_at."""
    monkeypatch.setattr(rs_mod.shutil, "which", lambda _binary: None)
    _patch_httpx(monkeypatch, raise_exc=ConnectionError("offline"))

    db = _FakeAsyncDB()
    collector = _make_collector(db)

    older = await collector.capture()
    # Stamp an explicitly older captured_at on the persisted doc so the
    # ordering check is deterministic regardless of clock resolution.
    older_doc = db[RESOURCE_SNAPSHOT_COLLECTION_NAME]._docs[-1]
    older_doc["captured_at"] = datetime.now(timezone.utc) - timedelta(minutes=10)
    older_doc["id"] = older.id

    newer = await collector.capture()
    newer_doc = db[RESOURCE_SNAPSHOT_COLLECTION_NAME]._docs[-1]
    newer_doc["captured_at"] = datetime.now(timezone.utc)
    newer_doc["id"] = newer.id

    fetched = await collector.latest()
    assert fetched is not None
    assert fetched.id == newer.id


# ─────────────────────────────────────────────────────────────────────────────
# _detect_interactive_users / ChatActivityTracker wiring (Task 86 / F9)
# ─────────────────────────────────────────────────────────────────────────────


from backend.services.activity_tracker import ChatActivityTracker  # noqa: E402


def _make_collector_with_tracker(
    tracker: ChatActivityTracker | None,
    *,
    activity_threshold_seconds: float = 30.0,
) -> ResourceSnapshotCollector:
    return ResourceSnapshotCollector(
        _FakeAsyncDB(),
        host_id="test-host",
        ttl_hours=24,
        cpu_sample_interval=0.0,
        activity_tracker=tracker,
        activity_threshold_seconds=activity_threshold_seconds,
    )


@pytest.mark.asyncio
async def test_interactive_users_detected_when_recent_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapshot must report True when chat was marked seconds ago."""
    monkeypatch.setattr(rs_mod.shutil, "which", lambda _binary: None)
    _patch_httpx(monkeypatch, raise_exc=ConnectionError("offline"))

    tracker = ChatActivityTracker(mode="in_process", host_id="test-host")
    await tracker.mark_active()
    collector = _make_collector_with_tracker(tracker)

    snapshot = await collector.capture()
    assert snapshot.interactive_users_detected is True


@pytest.mark.asyncio
async def test_interactive_users_false_when_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Activity older than the threshold must NOT trip the flag."""
    monkeypatch.setattr(rs_mod.shutil, "which", lambda _binary: None)
    _patch_httpx(monkeypatch, raise_exc=ConnectionError("offline"))

    tracker = ChatActivityTracker(mode="in_process", host_id="test-host")
    await tracker.mark_active()
    # Pretend the mark happened 60s ago by rewinding the internal slot.
    # The tracker uses time.monotonic() under the hood.
    import time as _time
    tracker._last_active_at = _time.monotonic() - 60.0  # type: ignore[attr-defined]

    collector = _make_collector_with_tracker(tracker)
    snapshot = await collector.capture()
    assert snapshot.interactive_users_detected is False


@pytest.mark.asyncio
async def test_interactive_users_false_when_no_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stub-fallback path: a collector built without a tracker still
    returns False, preserving Task 71 behaviour for legacy call sites."""
    monkeypatch.setattr(rs_mod.shutil, "which", lambda _binary: None)
    _patch_httpx(monkeypatch, raise_exc=ConnectionError("offline"))

    collector = _make_collector(_FakeAsyncDB())
    snapshot = await collector.capture()
    assert snapshot.interactive_users_detected is False


@pytest.mark.asyncio
async def test_pause_evaluator_respects_interactive_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """is_safe_to_run must veto when pause_if_interactive_users + active chat."""
    monkeypatch.setitem(
        sys.modules, "psutil", _make_fake_psutil(cpu_percent=10.0, ram_pct=20.0)
    )
    monkeypatch.setattr(rs_mod.shutil, "which", lambda _binary: None)
    _patch_httpx(monkeypatch, raise_exc=ConnectionError("offline"))

    tracker = ChatActivityTracker(mode="in_process", host_id="test-host")
    await tracker.mark_active()
    collector = _make_collector_with_tracker(tracker)

    limits = ResourceLimits(
        max_cpu_utilization_pct=85,
        max_ram_usage_pct=90,
        pause_if_interactive_users=True,
        pause_if_backend_chat_active=False,
    )
    safe, reasons = await collector.is_safe_to_run(limits)

    assert safe is False
    assert any("Interactive users" in reason for reason in reasons)
