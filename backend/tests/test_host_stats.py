"""Tests for the superadmin host-stats service and endpoint.

Covers the :class:`HostStatsSampler` sampling/caching logic with mocked probes
and the ``GET /api/v1/system/host-stats`` endpoint's response shape and
``require_admin`` gating.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.models.schemas import HostStatsResponse, HostStatsSources
from backend.routers.auth import require_admin
from backend.services import host_stats
from backend.services.host_stats import HostStatsSampler


class _FakeGpuProbe:
    """Minimal stand-in for resources.GpuProbe."""

    def __init__(self, sample: dict | None, name: str | None = "Test GPU"):
        self._sample = sample
        self.static = {"name": name} if name else {}

    def read(self) -> dict | None:
        return self._sample


class _FakeHostProbe:
    """Minimal stand-in for resources.HostProbe."""

    def __init__(self, cpu_pct: float | None):
        self._cpu_pct = cpu_pct

    def sample(self) -> dict:
        return {"cpu_pct": self._cpu_pct}


def _make_sampler(**overrides) -> HostStatsSampler:
    """Build a sampler wired entirely to in-memory fakes (no subprocess calls)."""
    defaults = dict(
        ttl_seconds=5.0,
        disk_path="/data",
        gpu_probe=_FakeGpuProbe(
            {"temp_c": 59.0, "power_w": 16.5, "util_pct": 7.0, "mem_mb": 4096.0, "mem_total_mb": 16311.0}
        ),
        host_probe=_FakeHostProbe(12.5),
        disk_usage=lambda _p: SimpleNamespace(used=580_000_000_000, total=4_000_000_000_000, percent=14.5),
        boot_time=lambda: 1_000.0,
        virtual_memory=lambda: SimpleNamespace(used=34_000_000_000, total=274_000_000_000, percent=12.4),
    )
    defaults.update(overrides)
    return HostStatsSampler(**defaults)


class TestHostStatsSampler:
    """Unit tests for the sampler with mocked probes."""

    def test_collects_all_metric_groups(self):
        sampler = _make_sampler()
        stats = asyncio.run(sampler.get_stats())

        assert isinstance(stats, HostStatsResponse)
        assert stats.cached is False
        # Container-view metrics (psutil) populated.
        assert stats.cpu_percent == 12.5
        assert stats.ram_used_bytes == 34_000_000_000
        assert stats.ram_total_bytes == 274_000_000_000
        assert stats.ram_percent == 12.4
        assert stats.disk_path == "/data"
        assert stats.disk_used_bytes == 580_000_000_000
        assert stats.disk_total_bytes == 4_000_000_000_000
        assert stats.disk_percent == 14.5
        assert stats.uptime_seconds and stats.uptime_seconds > 0
        # GPU metrics (nvidia-smi via passthrough) populated, MiB -> bytes.
        assert stats.gpu_temp_c == 59.0
        assert stats.gpu_power_watts == 16.5
        assert stats.gpu_percent == 7.0
        assert stats.gpu_name == "Test GPU"
        assert stats.vram_used_bytes == int(4096.0 * 1024 * 1024)
        assert stats.vram_total_bytes == int(16311.0 * 1024 * 1024)
        # Host-only metrics unavailable from the container.
        assert stats.cpu_power_watts is None
        # Provenance labels reflect what was actually read in the container.
        assert stats.source == "container"
        assert stats.agent_online is False
        assert stats.sources.gpu_temp == "gpu"
        assert stats.sources.cpu == "container"
        assert stats.sources.cpu_power == "unavailable"

    def test_second_call_within_ttl_is_cached(self):
        sampler = _make_sampler(ttl_seconds=60.0)
        first = asyncio.run(sampler.get_stats())
        second = asyncio.run(sampler.get_stats())

        assert first.cached is False
        assert second.cached is True
        # Same snapshot timestamp proves it was reused, not re-sampled.
        assert second.sampled_at == first.sampled_at

    def test_missing_gpu_leaves_gpu_fields_none(self):
        sampler = _make_sampler(gpu_probe=_FakeGpuProbe(None, name=None))
        stats = asyncio.run(sampler.get_stats())

        assert stats.gpu_temp_c is None
        assert stats.gpu_power_watts is None
        assert stats.gpu_percent is None
        assert stats.gpu_name is None
        assert stats.vram_used_bytes is None
        # No GPU in the container (the production case): labelled unavailable.
        assert stats.sources.gpu_temp == "unavailable"
        assert stats.sources.gpu_power == "unavailable"
        # Container metrics still populated when the GPU is absent.
        assert stats.cpu_percent == 12.5

    def test_probe_failure_does_not_raise(self):
        class _BoomHostProbe:
            def sample(self):
                raise RuntimeError("psutil exploded")

        sampler = _make_sampler(host_probe=_BoomHostProbe())
        stats = asyncio.run(sampler.get_stats())

        # CPU sample failed but the rest of the snapshot is intact.
        assert stats.cpu_percent is None
        assert stats.ram_total_bytes == 274_000_000_000


def _sample_response(cached: bool = False) -> HostStatsResponse:
    """A fully-populated response used to stub the endpoint's sampler."""
    return HostStatsResponse(
        sampled_at="2026-10-17T00:00:00Z",
        cached=cached,
        cpu_percent=11.0,
        ram_used_bytes=1_000,
        ram_total_bytes=2_000,
        ram_percent=50.0,
        disk_path="/app/mounts",
        disk_used_bytes=10,
        disk_total_bytes=100,
        disk_percent=10.0,
        cpu_temp_c=None,
        gpu_temp_c=60.0,
        gpu_power_watts=20.0,
        cpu_power_watts=None,
        uptime_seconds=123.0,
        gpu_name="Test GPU",
        gpu_percent=5.0,
        vram_used_bytes=1,
        vram_total_bytes=2,
        sources=HostStatsSources(
            cpu="container", ram="container", disk="container", cpu_temp="host",
            gpu_temp="gpu", cpu_power="host", gpu_power="gpu", uptime="container",
            note="test",
        ),
    )


class TestHostStatsEndpoint:
    """Tests for GET /api/v1/system/host-stats."""

    def test_returns_stats_for_admin(self, client: TestClient, monkeypatch):
        async def _fake_collect(*_args) -> HostStatsResponse:
            return _sample_response()

        monkeypatch.setattr(host_stats, "collect_host_stats", _fake_collect)

        response = client.get("/api/v1/system/host-stats")
        assert response.status_code == 200
        data = response.json()
        # Requested metric groups present in the payload.
        for key in (
            "cpu_percent", "ram_used_bytes", "ram_total_bytes", "ram_percent",
            "disk_used_bytes", "disk_total_bytes", "disk_percent",
            "gpu_temp_c", "cpu_temp_c", "gpu_power_watts", "cpu_power_watts",
            "uptime_seconds", "sources",
        ):
            assert key in data
        assert data["gpu_temp_c"] == 60.0
        assert data["cpu_power_watts"] is None
        assert data["sources"]["gpu_temp"] == "gpu"

    def test_requires_admin(self, app, monkeypatch):
        """Non-admins are rejected before any sampling happens."""
        called = False

        async def _should_not_run(*_args) -> HostStatsResponse:
            nonlocal called
            called = True
            return _sample_response()

        monkeypatch.setattr(host_stats, "collect_host_stats", _should_not_run)

        async def _forbidden() -> None:
            raise HTTPException(status_code=403, detail="Admin access required")

        app.dependency_overrides[require_admin] = _forbidden
        # No context manager: skip the lifespan so no real Mongo connection is attempted.
        forbidden_client = TestClient(app)
        response = forbidden_client.get("/api/v1/system/host-stats")

        assert response.status_code == 403
        assert called is False


# ── host-agent samples (Phase 1) ────────────────────────────────────────────

from datetime import datetime, timedelta, timezone  # noqa: E402

from backend.services.host_stats import collect_host_stats, response_from_agent_sample  # noqa: E402


def _agent_sample(**overrides) -> dict:
    """A host_samples document as written by the Windows host agent."""
    sample = {
        "ts": datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc),
        "meta": {"host": "TESTHOST", "source": "host-agent", "agent_version": "1.0.0", "schema": 1},
        "uptime_s": 214603.0,
        "cpu": {"pct": 37.5, "logical": 24},
        "mem": {"used_bytes": 64 * 1024**3, "total_bytes": 256 * 1024**3, "pct": 25.0},
        "disks": [
            {"path": "D:\\", "used_bytes": 1, "total_bytes": 2, "pct": 50.0, "primary": False},
            {"path": "C:\\", "used_bytes": 580 * 10**9, "total_bytes": 4 * 10**12, "pct": 14.5, "primary": True},
        ],
        "power": {"cpu_pkg_w": 22.1, "cpu_cores_w": 15.8, "gpu_w": 16.2},
        "temp": {"gpu_c": 57.0, "cpu_pkg_c": 61.0},
        "gpu": {"name": "NVIDIA RTX PRO 2000", "util_pct": 7.0, "vram_used_bytes": 4 * 1024**3,
                "vram_total_bytes": 16 * 1024**3},
        "containers": [
            {"name": "rag-mongodb", "cpu_cores": 0.2, "mem_bytes": 900_000_000},
            {"name": "rag-backend-quellex", "cpu_cores": 3.4, "mem_bytes": 5_000_000_000, "mem_limit_bytes": 25_000_000_000},
        ],
        "activity": [{"tenant": "quellex", "source": "api", "job_id": "job-1", "phase": "processing",
                      "step": "embed", "current_file": "/app/mounts/a.pdf", "processed_files": 3, "total_files": 10,
                      "unexpected_key": "ignored"}],
        "sensors": {"status": "ok"},
    }
    sample.update(overrides)
    return sample


class TestAgentSampleMapping:
    """response_from_agent_sample maps the host-agent document to the widget response."""

    def test_maps_true_host_figures(self):
        now = datetime(2026, 10, 18, 12, 0, 4, tzinfo=timezone.utc)
        resp = response_from_agent_sample(_agent_sample(), now=now)

        assert resp.source == "host-agent"
        assert resp.agent_online is True
        assert resp.agent_sample_age_seconds == 4.0
        assert resp.cpu_percent == 37.5
        assert resp.ram_total_bytes == 256 * 1024**3
        # Primary drive chosen over the first entry.
        assert resp.disk_path == "C:\\"
        assert resp.disk_percent == 14.5
        assert resp.cpu_power_watts == 22.1
        assert resp.gpu_power_watts == 16.2
        assert resp.cpu_temp_c == 61.0
        assert resp.gpu_temp_c == 57.0
        assert resp.uptime_seconds == 214603.0
        assert resp.sensors_status == "ok"
        assert resp.sources.cpu == "host"
        assert resp.sources.cpu_power == "host"
        assert resp.sources.gpu_temp == "gpu"

    def test_containers_sorted_busiest_first_and_activity_mapped(self):
        resp = response_from_agent_sample(_agent_sample())

        assert [c.name for c in resp.containers] == ["rag-backend-quellex", "rag-mongodb"]
        assert resp.containers[0].cpu_cores == 3.4
        assert resp.activity[0].job_id == "job-1"
        assert resp.activity[0].step == "embed"
        assert resp.activity[0].processed_files == 3

    def test_other_tenants_activity_is_left_out(self):
        activity = [
            {"tenant": "quellex", "source": "api", "job_id": "q1", "current_file": "/app/mounts/Akten/a.pdf"},
            {"tenant": "recallhub", "source": "api", "job_id": "r1", "current_file": "/app/mounts/other/b.pdf"},
            {"tenant": "shared", "source": "worker", "job_id": "w1"},
        ]
        scoped = response_from_agent_sample(_agent_sample(activity=activity), tenant="quellex")
        unscoped = response_from_agent_sample(_agent_sample(activity=activity))

        assert [a.job_id for a in scoped.activity] == ["q1", "w1"]
        assert [a.job_id for a in unscoped.activity] == ["q1", "r1", "w1"]
        assert scoped.containers == unscoped.containers  # machine figures stay host-wide

    def test_missing_cpu_temperature_is_marked_unavailable(self):
        sample = _agent_sample(temp={"gpu_c": 57.0}, sensors={"status": "driver_missing"})
        resp = response_from_agent_sample(sample)

        assert resp.cpu_temp_c is None
        assert resp.sources.cpu_temp == "unavailable"
        assert resp.sensors_status == "driver_missing"

    def test_naive_timestamp_treated_as_utc(self):
        sample = _agent_sample(ts=datetime(2026, 10, 18, 12, 0, 0))
        resp = response_from_agent_sample(sample, now=datetime(2026, 10, 18, 12, 0, 10, tzinfo=timezone.utc))
        assert resp.agent_sample_age_seconds == 10.0


class _FakeStore:
    def __init__(self, sample=None, error: Exception | None = None, tenant_scope: str | None = None):
        self.sample = sample
        self.error = error
        self.max_age = None
        self.tenant_scope = tenant_scope

    async def latest_host_sample(self, *, max_age_seconds: float):
        self.max_age = max_age_seconds
        if self.error:
            raise self.error
        return self.sample


class TestCollectHostStats:
    """collect_host_stats prefers a fresh agent sample and falls back to the container view."""

    @pytest.fixture(autouse=True)
    def _fallback_sampler(self, monkeypatch):
        sampler = _make_sampler()
        monkeypatch.setattr(host_stats, "get_host_stats_sampler", lambda: sampler)

    def test_uses_agent_sample_when_fresh(self):
        store = _FakeStore(sample=_agent_sample(ts=datetime.now(timezone.utc) - timedelta(seconds=3)))
        resp = asyncio.run(collect_host_stats(store))
        assert resp.source == "host-agent"
        assert store.max_age is not None and store.max_age > 0

    def test_agent_sample_activity_follows_the_store_scope(self):
        sample = _agent_sample(ts=datetime.now(timezone.utc) - timedelta(seconds=3))
        resp = asyncio.run(collect_host_stats(_FakeStore(sample=sample, tenant_scope="recallhub")))
        assert resp.activity == []  # the sample's only activity is the quellex backend's

    def test_falls_back_when_agent_offline(self):
        resp = asyncio.run(collect_host_stats(_FakeStore(sample=None)))
        assert resp.source == "container"
        assert resp.agent_online is False

    def test_falls_back_when_store_errors(self):
        resp = asyncio.run(collect_host_stats(_FakeStore(error=RuntimeError("mongo down"))))
        assert resp.source == "container"

    def test_falls_back_without_store(self):
        resp = asyncio.run(collect_host_stats(None))
        assert resp.source == "container"
