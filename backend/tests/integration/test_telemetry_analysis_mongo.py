"""Telemetry analysis (history, job report, anomaly rules) against a real MongoDB.

Seeds one hour of host-agent samples and activity events with known problems and checks that
every rule finds them. Runs only when ``RECALLHUB_TEST_MONGO_URI`` points at a throwaway server.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pymongo import AsyncMongoClient

from backend.services import telemetry_analysis as ta
from backend.services.telemetry_store import TelemetryStore

MONGO_URI = os.environ.get("RECALLHUB_TEST_MONGO_URI")
pytestmark = pytest.mark.skipif(not MONGO_URI, reason="RECALLHUB_TEST_MONGO_URI not set")

T0 = datetime(2026, 10, 18, 10, 0, 0, tzinfo=timezone.utc)
JOB = "job-it-1"
BOOT = T0 - timedelta(days=2)
RESTART = T0 + timedelta(minutes=40)


def _sample(i: int) -> dict:
    """Sample i (every 5 s) with scripted problems."""
    ts = T0 + timedelta(seconds=5 * i)
    minute = i * 5 / 60
    mem_pct = 93.0 if 10 <= minute < 12 else 40.0                     # memory pressure 10-12 min
    reasons = ["sw_thermal"] if 20 <= minute < 21 else []              # GPU thermal throttle 20-21 min
    backend_ok = not (30 <= minute < 31)                               # backend down 30-31 min
    backend_started = RESTART if ts >= RESTART else BOOT               # backend restarted at 40 min
    return {
        "ts": ts,
        "meta": {"host": "TEST", "source": "host-agent", "schema": 1},
        "interval_s": 5,
        "cpu": {"pct": 60.0},
        "mem": {"pct": mem_pct, "used_bytes": int(mem_pct * 1e9), "total_bytes": int(100e9)},
        "power": {"cpu_pkg_w": 40.0, "gpu_w": 50.0},
        "temp": {"gpu_c": 70.0, "cpu_pkg_c": 65.0},
        "gpu": {"util_pct": 80.0, "reasons": reasons},
        "health": [{"name": "backend-quellex", "ms": 4.0, "ok": backend_ok}],
        "containers": [
            {"name": "rag-backend-quellex", "cpu_cores": 3.0, "mem_bytes": int(5e9), "mem_limit_bytes": int(25e9),
             "started_at": backend_started, "restart_count": 0},
            {"name": "rag-mongodb", "cpu_cores": 0.5, "mem_bytes": int(2e9), "mem_limit_bytes": int(25e9),
             "started_at": BOOT, "restart_count": 0},
        ],
    }


def _event(seconds: float, kind: str, job_id=JOB, **fields) -> dict:
    return {"ts": T0 + timedelta(seconds=seconds),
            "meta": {"kind": kind, "source": "api", "tenant": "quellex", "job_id": job_id}, **fields}


def _file(seconds: float, name: str, outcome: str, duration_ms: float, **extra) -> list[dict]:
    file_info = {"path": f"/app/mounts/akten/{name}", "name": name, "ext": name.rsplit(".", 1)[1], "size_bytes": 1000}
    start = _event(seconds - duration_ms / 1000, "file.start", file=file_info)
    end = _event(seconds, "file.end", file=file_info, outcome=outcome, duration_ms=duration_ms,
                 steps={"convert_ms": duration_ms * 0.7, "embed_ms": duration_ms * 0.2, "save_ms": duration_ms * 0.1}, **extra)
    return [start, end]


@pytest.fixture
async def store():
    client = AsyncMongoClient(MONGO_URI, tz_aware=True, tzinfo=timezone.utc)
    name = f"telemetry_an_{uuid.uuid4().hex[:8]}"
    s = TelemetryStore(client, name, host_samples_ttl_seconds=86400 * 3650, activity_events_ttl_seconds=86400 * 3650,
                       owns_client=False)
    await s.ensure_collections()
    # One hour at 5 s, with an agent gap from 50:00 to 52:00 (24 samples missing).
    samples = [_sample(i) for i in range(720) if not (600 <= i < 624)]
    await s.db["host_samples"].insert_many(samples)

    events = [
        _event(0, "job.status", status="running"),
        _event(1, "job.phase", phase="initializing"),
        _event(5, "job.phase", phase="discovering"),
        _event(60, "job.phase", phase="processing", files=40),
        _event(900, "job.status", status="paused"),
        _event(960, "job.status", status="running", reason="resumed"),
        _event(3000, "job.phase", phase="finalizing"),
        _event(3010, "job.phase", phase="completed"),
        _event(3010, "job.status", status="completed", files=40),
    ]
    for k in range(30):  # ordinary PDFs around 2 s each
        events += _file(100 + k * 60, f"brief_{k}.pdf", "ok", 2000 + k * 10, pages=2, chunks=4)
    events += _file(2500, "scan_huge.pdf", "ok", 120_000, pages=80, chunks=200)      # slow outlier
    events += _file(2600, "broken_1.docx", "error", 300, error_type="BadZipFile", error="File is not a zip file")
    events += _file(2610, "broken_2.docx", "error", 310, error_type="BadZipFile", error="File is not a zip file")
    events += _file(2700, "photo.jpg", "empty", 900)
    events += _file(2800, "timeout.pdf", "cancelled", 300_000)
    events.append(_event(2900, "file.start", file={"path": "/app/mounts/akten/hang.msg", "name": "hang.msg", "ext": "msg"}))
    events.append(_event(1500, "chat.request", job_id=None, outcome="ok", duration_ms=4200.0, session_id="s1"))
    events.append(_event(1600, "chat.request", job_id=None, outcome="error", duration_ms=800.0, session_id="s2"))
    await s.insert_events(events)
    yield s
    await client.drop_database(name)
    await client.close()


def _kinds(anomalies) -> dict:
    return {a.kind: a for a in anomalies}


@pytest.mark.asyncio
async def test_host_anomaly_rules(store):
    anomalies = _kinds(await ta.host_anomalies(store, T0, T0 + timedelta(hours=1)))

    mem = anomalies["memory_pressure"]
    assert mem.severity == "warning" and len(mem.episodes) == 1
    assert mem.episodes[0].peak == 93.0
    assert mem.episodes[0].start == T0 + timedelta(minutes=10)

    assert anomalies["gpu_throttling"].episodes[0].detail == "sw_thermal"
    assert anomalies["backend_down"].severity == "critical"
    assert anomalies["backend_down"].episodes[0].detail == "backend-quellex"

    gap = anomalies["sampling_gap"].episodes[0]
    assert gap.start == T0 + timedelta(seconds=5 * 599) and gap.end == T0 + timedelta(seconds=5 * 624)

    restart = anomalies["container_restart"].examples
    assert restart == [{"container": "rag-backend-quellex", "at": RESTART.isoformat()}]
    assert "cpu_saturation" not in anomalies  # 60 % never saturates


@pytest.mark.asyncio
async def test_file_anomaly_rules(store):
    anomalies = _kinds(await ta.file_anomalies(store, None, None, JOB))

    failed = anomalies["failed_files"]
    assert failed.count == 2
    assert failed.examples[0]["error_type"] == "BadZipFile" and failed.examples[0]["files"] == 2

    slow = anomalies["slow_files"].examples
    assert [s["path"].rsplit("/", 1)[1] for s in slow] == ["scan_huge.pdf"]  # the cancelled 300 s file is not "ok"
    assert slow[0]["ratio"] > 10

    assert anomalies["empty_files"].count == 1
    assert anomalies["cancelled_files"].count == 1
    unfinished = anomalies["unfinished_files"]
    assert unfinished.severity == "critical"
    assert [e["path"] for e in unfinished.examples] == ["/app/mounts/akten/hang.msg"]


@pytest.mark.asyncio
async def test_history(store):
    h = await ta.history(store, T0, T0 + timedelta(hours=1))

    assert h.bucket_seconds == 10
    assert h.agent_samples == 696
    assert h.host[0].cpu_pct == 60.0
    assert set(h.containers) == {"rag-backend-quellex", "rag-mongodb"}
    assert [j.job_id for j in h.jobs] == [JOB] and h.jobs[0].status == "completed"
    assert h.jobs[0].files == 35 and h.jobs[0].failed == 2

    phases = [(p.phase, int((p.start - T0).total_seconds()), int((p.end - T0).total_seconds())) for p in h.phases]
    assert phases == [
        ("initializing", 1, 5), ("discovering", 5, 60), ("processing", 60, 900), ("paused", 900, 960),
        ("processing", 960, 3000), ("finalizing", 3000, 3010),
    ]
    chats = sum(p.chats for p in h.activity)
    chat_errors = sum(p.chat_errors for p in h.activity)
    assert (chats, chat_errors) == (2, 1)
    assert sum(p.files_ok for p in h.activity) == 31


@pytest.mark.asyncio
async def test_job_report(store):
    report = await ta.job_report(store, JOB, now=T0 + timedelta(hours=2))

    assert report is not None
    assert report.status == "completed" and report.running is False
    assert report.files == 35
    assert report.outcomes == {"ok": 31, "error": 2, "empty": 1, "cancelled": 1}
    assert report.total_pages == 60 + 80
    assert [s.step for s in report.steps][:1] == ["convert"]  # convert dominates
    assert report.steps[0].share_pct == pytest.approx(70.0, abs=0.5)
    assert report.by_ext[0].ext == "pdf" and report.by_ext[0].files == 32
    assert report.slowest[0].path.endswith("timeout.pdf")  # slowest regardless of outcome
    assert [p.phase for p in report.phases] == ["initializing", "discovering", "processing", "paused", "processing", "finalizing"]
    processing = report.phases[2]
    assert processing.host["mem_pct_max"] == 93.0
    assert processing.containers["rag-backend-quellex"]["cpu_cores"] == 3.0
    kinds = {a.kind for a in report.anomalies}
    assert {"memory_pressure", "gpu_throttling", "backend_down", "failed_files", "slow_files", "unfinished_files"} <= kinds
    assert report.anomalies[0].severity == "critical"
    assert report.failures and report.failures[0]["error_type"] == "BadZipFile"
    assert report.agent_samples == 600  # 0..3010 s at 5 s, the job ends inside the 50:00 agent gap
    assert sum(p.files_ok for p in report.throughput) == 31


@pytest.mark.asyncio
async def test_unknown_job(store):
    assert await ta.job_report(store, "no-such-job") is None


@pytest.mark.asyncio
async def test_range_anomalies_sorted_by_severity(store):
    report = await ta.range_anomalies(store, T0, T0 + timedelta(hours=1))
    severities = [a.severity for a in report.anomalies]
    assert severities == sorted(severities, key=lambda s: {"critical": 0, "warning": 1, "info": 2}[s])


@pytest.mark.asyncio
async def test_html_report(store):
    from backend.services.telemetry_report_html import render_job_report_html

    page = await render_job_report_html(store, JOB, now=T0 + timedelta(hours=2))

    assert page is not None and page.lstrip().lower().startswith("<!doctype html")
    for expected in ("Ingestion job job-it-1", "Host CPU", "Container CPU", "Files processed",
                     "Files that never finished", "Where the time goes", "convert", "scan_huge.pdf", "GPU thermal throttling"):
        assert expected in page, expected
    assert page.count("</body>") == 1
    assert await render_job_report_html(store, "no-such-job") is None
