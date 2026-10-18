"""Unit tests for the telemetry history: pure analysis helpers, the HTML converter and the API router."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.models.telemetry_history import AnomalyReport, HistoryResponse, JobReport
from backend.routers import telemetry_history as router_module
from backend.routers.auth import require_admin
from backend.services import telemetry_analysis as ta
from backend.services.telemetry_analysis import FlaggedSample, activity_points, build_phase_intervals, clip_intervals, find_episodes
from backend.services.telemetry_report_html import samples_to_v2

T0 = datetime(2026, 10, 18, 10, 0, 0, tzinfo=timezone.utc)


def _ev(seconds: float, kind: str, job="j1", **fields) -> dict:
    return {"ts": T0 + timedelta(seconds=seconds), "meta": {"kind": kind, "job_id": job}, **fields}


class TestPhaseIntervals:
    def test_pause_resumes_previous_phase_and_terminal_closes(self):
        events = [
            _ev(0, "job.status", status="running"),
            _ev(1, "job.phase", phase="discovering"),
            _ev(10, "job.phase", phase="processing"),
            _ev(20, "job.status", status="paused"),
            _ev(30, "job.status", status="running"),
            _ev(50, "job.phase", phase="completed"),  # ignored: the status closes it
            _ev(50, "job.status", status="completed"),
        ]
        out = build_phase_intervals(events, range_end=T0 + timedelta(hours=1))
        assert [(i.phase, (i.start - T0).seconds, (i.end - T0).seconds, i.open) for i in out] == [
            ("discovering", 1, 10, False), ("processing", 10, 20, False), ("paused", 20, 30, False),
            ("processing", 30, 50, False),
        ]

    def test_running_job_stays_open(self):
        events = [_ev(0, "job.phase", phase="processing")]
        end = T0 + timedelta(seconds=100)
        out = build_phase_intervals(events, range_end=end, last_seen={"j1": T0 + timedelta(seconds=90)})
        assert out[0].open is True and out[0].end == end

    def test_silent_job_without_final_status_closes_at_last_event(self):
        events = [_ev(0, "job.phase", phase="processing")]
        last = T0 + timedelta(minutes=5)
        out = build_phase_intervals(events, range_end=T0 + timedelta(hours=2), last_seen={"j1": last})
        assert out[0].open is False and out[0].end == last

    def test_clip(self):
        events = [_ev(0, "job.phase", phase="processing"), _ev(100, "job.status", status="completed")]
        out = clip_intervals(build_phase_intervals(events, range_end=T0 + timedelta(hours=1)),
                             T0 + timedelta(seconds=50), T0 + timedelta(seconds=200))
        assert [(i.start - T0).seconds for i in out] == [50]


class TestEpisodes:
    def test_groups_by_gap_and_reports_peak(self):
        samples = [FlaggedSample(T0 + timedelta(seconds=s), v) for s, v in ((0, 91), (5, 95), (10, 92), (60, 90))]
        episodes = find_episodes(samples, max_gap_s=15)
        assert [(e.samples, e.peak) for e in episodes] == [(3, 95.0), (1, 90.0)]

    def test_details_are_collected(self):
        samples = [FlaggedSample(T0, None, "sw_thermal"), FlaggedSample(T0 + timedelta(seconds=5), None, "hw_thermal")]
        assert find_episodes(samples, max_gap_s=15)[0].detail == "hw_thermal, sw_thermal"


def test_activity_points_reshape():
    rows = [
        {"_id": {"t": T0, "kind": "file.end", "outcome": "ok"}, "n": 3, "ms": 3000.0},
        {"_id": {"t": T0, "kind": "file.end", "outcome": "error"}, "n": 1, "ms": 100.0},
        {"_id": {"t": T0, "kind": "chat.request", "outcome": "error"}, "n": 1, "ms": 500.0},
    ]
    (point,) = activity_points(rows)
    assert (point.files_ok, point.files_failed, point.chats, point.chat_errors) == (3, 1, 1, 1)
    assert point.file_ms_avg == 775.0 and point.chat_ms_avg == 500.0


class TestSamplesToV2:
    def test_units_and_integrated_counters(self):
        raw = [
            {"ts": T0, "meta": {"host": "H"}, "cpu": {"pct": 10.0, "per_core": [10.0, 20.0]},
             "mem": {"used_bytes": 64 * 1024**3, "total_bytes": 256 * 1024**3, "pct": 25.0},
             "disk_io": {"read_bps": 2e6, "write_bps": 0}, "net": {"rx_bps": 1e6, "tx_bps": 0},
             "temp": {"gpu_c": 60.0, "gpu_headroom_c": 30.0, "cpu_pkg_c": 70.0}, "power": {"gpu_w": 50.0},
             "gpu": {"util_pct": 80.0, "vram_used_bytes": 4 * 1024**2 * 1024, "reasons": ["power_cap"]},
             "containers": [{"name": "rag-backend-quellex", "cpu_cores": 2.5, "mem_bytes": 5e9}],
             "health": [{"name": "a", "ms": 3.0, "ok": True}, {"name": "b", "ms": 9.0, "ok": False}],
             "ollama": {"models": [{"name": "gemma", "vram_bytes": 2 * 1024**3, "ctx": 8192}]}},
            {"ts": T0 + timedelta(seconds=5), "cpu": {"pct": 20.0}, "mem": {},
             "disk_io": {"read_bps": 2e6}, "net": {"rx_bps": 1e6}},
            {"ts": T0 + timedelta(seconds=605), "cpu": {"pct": 30.0},  # 10 min gap: integrates nothing
             "disk_io": {"read_bps": 2e6}, "net": {"rx_bps": 1e6}},
        ]
        out = samples_to_v2(raw)
        first, second, third = out
        assert first["t"] == 0.0 and second["t"] == 5.0
        assert first["host"]["ram_used_gb"] == 64.0 and first["host"]["cpu_temp_c"] == 70.0
        assert first["gpu"]["mem_mb"] == 4096 and first["gpu"]["tlimit_c"] == 30.0 and first["gpu"]["power_w"] == 50.0
        assert first["containers"]["rag-backend-quellex"]["cpu_pct"] == 250.0  # % of one core
        assert first["containers"]["rag-backend-quellex"]["mem_mb"] == 5000.0
        assert first["health"] == {"ms": 9.0, "ok": False}
        assert first["ollama"][0]["vram_gib"] == 2.0
        assert second["host"]["disk_read_mb"] == 10.0  # 2 MB/s x 5 s
        assert second["host"]["net_recv_mb"] == 5.0
        assert third["host"]["disk_read_mb"] == 10.0  # unchanged across the gap


class TestResolveRange:
    def test_hours_back_from_end(self):
        end = T0 + timedelta(hours=6)
        start, stop = router_module.resolve_range(6, None, end)
        assert (start, stop) == (T0, end)

    def test_naive_times_are_utc(self):
        start, _ = router_module.resolve_range(1, datetime(2026, 10, 18, 10), datetime(2026, 10, 18, 11))
        assert start == T0

    @pytest.mark.parametrize("start, end", [(T0, T0), (T0 + timedelta(hours=1), T0), (T0, T0 + timedelta(days=91))])
    def test_rejects_bad_ranges(self, start, end):
        with pytest.raises(HTTPException) as err:
            router_module.resolve_range(1, start, end)
        assert err.value.status_code == 422


class TestRouter:
    @pytest.fixture
    def with_store(self, app):
        app.state.telemetry_store = object()
        yield app
        app.state.telemetry_store = None

    def test_history_returns_model(self, client: TestClient, with_store, monkeypatch):
        async def fake_history(store, start, end, bucket):
            return HistoryResponse(start=start, end=end, bucket_seconds=bucket or 10, agent_samples=0, host=[],
                                   containers={}, activity=[], phases=[], jobs=[])

        monkeypatch.setattr(ta, "history", fake_history)
        response = client.get("/api/v1/telemetry/history?hours=1&bucket=30")
        assert response.status_code == 200
        assert response.json()["bucket_seconds"] == 30

    def test_unknown_job_is_404(self, client: TestClient, with_store, monkeypatch):
        async def none_report(store, job_id, **_):
            return None

        monkeypatch.setattr(ta, "job_report", none_report)
        assert client.get("/api/v1/telemetry/jobs/nope").status_code == 404

    def test_anomalies(self, client: TestClient, with_store, monkeypatch):
        async def fake(store, start, end):
            return AnomalyReport(start=start, end=end, anomalies=[])

        monkeypatch.setattr(ta, "range_anomalies", fake)
        assert client.get("/api/v1/telemetry/anomalies?hours=24").json()["anomalies"] == []

    def test_disabled_history_is_503(self, client: TestClient, app):
        app.state.telemetry_store = None
        assert client.get("/api/v1/telemetry/history").status_code == 503

    def test_requires_admin(self, app, with_store):
        async def forbidden():
            raise HTTPException(status_code=403, detail="Admin access required")

        app.dependency_overrides[require_admin] = forbidden
        response = TestClient(app).get("/api/v1/telemetry/jobs")
        assert response.status_code == 403

    def test_bad_range_is_422(self, client: TestClient, with_store):
        response = client.get("/api/v1/telemetry/history?start=2026-10-18T11:00:00&end=2026-10-18T10:00:00")
        assert response.status_code == 422
