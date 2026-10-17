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
        # Provenance labels present.
        assert stats.sources.gpu_temp == "gpu"
        assert stats.sources.cpu == "container"
        assert stats.sources.cpu_power == "host"

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
        async def _fake_collect() -> HostStatsResponse:
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

        async def _should_not_run() -> HostStatsResponse:
            nonlocal called
            called = True
            return _sample_response()

        monkeypatch.setattr(host_stats, "collect_host_stats", _should_not_run)

        async def _forbidden() -> None:
            raise HTTPException(status_code=403, detail="Admin access required")

        app.dependency_overrides[require_admin] = _forbidden
        with TestClient(app) as forbidden_client:
            response = forbidden_client.get("/api/v1/system/host-stats")

        assert response.status_code == 403
        assert called is False
