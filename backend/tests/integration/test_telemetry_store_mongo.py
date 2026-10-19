"""TelemetryStore against a real MongoDB (time-series collections, $dateTrunc series, summaries).

Runs only when ``RECALLHUB_TEST_MONGO_URI`` points at a throwaway server — never the stack's
database. Each test uses its own random database and drops it afterwards::

    $env:RECALLHUB_TEST_MONGO_URI = "mongodb://127.0.0.1:27999/?directConnection=true"
    uv run pytest backend/tests/integration/test_telemetry_store_mongo.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pymongo import AsyncMongoClient

from backend.services.telemetry_store import TelemetryStore

MONGO_URI = os.environ.get("RECALLHUB_TEST_MONGO_URI")

pytestmark = pytest.mark.skipif(not MONGO_URI, reason="RECALLHUB_TEST_MONGO_URI not set")

T0 = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)


def _sample(seconds: int, cpu: float, backend_ok: bool = True, reasons: list[str] | None = None) -> dict:
    return {
        "ts": T0 + timedelta(seconds=seconds),
        "meta": {"host": "TEST", "source": "host-agent", "schema": 1},
        "cpu": {"pct": cpu},
        "mem": {"pct": 50.0, "used_bytes": 100, "total_bytes": 200},
        "power": {"cpu_pkg_w": 20.0 + cpu / 10, "gpu_w": 10.0},
        "temp": {"gpu_c": 50.0},
        "gpu": {"util_pct": 5.0, "reasons": reasons or []},
        "health": [{"name": "backend", "ms": 3.0 + seconds, "ok": backend_ok}],
        "containers": [
            {"name": "rag-backend-quellex", "cpu_cores": cpu / 100, "mem_bytes": 1000},
            {"name": "rag-mongodb", "cpu_cores": 0.1, "mem_bytes": 2000},
        ],
    }


@pytest.fixture
async def store():
    client = AsyncMongoClient(MONGO_URI, tz_aware=True, tzinfo=timezone.utc)
    name = f"telemetry_it_{uuid.uuid4().hex[:8]}"
    s = TelemetryStore(client, name, host_samples_ttl_seconds=86400, activity_events_ttl_seconds=86400, owns_client=False)
    await s.ensure_collections()
    yield s
    await client.drop_database(name)
    await client.close()


@pytest.mark.asyncio
async def test_series_summary_and_containers(store):
    samples = [_sample(0, 10.0), _sample(5, 30.0), _sample(10, 50.0, backend_ok=False), _sample(15, 70.0, reasons=["sw_thermal"])]
    await store.db["host_samples"].insert_many(samples)
    start, end = T0, T0 + timedelta(seconds=20)

    rows = await store.host_series(start, end, 10)
    assert [r["samples"] for r in rows] == [2, 2]
    assert rows[0]["cpu_pct"] == pytest.approx(20.0)
    assert rows[1]["cpu_pct_max"] == 70.0
    assert rows[1]["backend_down"] == 1
    assert rows[1]["gpu_throttled"] == 1
    assert rows[1]["backend_ms_max"] == 18.0

    summary = await store.host_summary(start, end)
    assert summary["samples"] == 4
    assert summary["cpu_pct"] == pytest.approx(40.0)
    assert summary["cpu_pct_max"] == 70.0

    containers = await store.container_series(start, end, 10)
    assert set(containers) == {"rag-backend-quellex", "rag-mongodb"}
    assert containers["rag-backend-quellex"][1]["cpu_cores_max"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_latest_sample_respects_max_age(store):
    now = datetime.now(timezone.utc)
    await store.db["host_samples"].insert_one({**_sample(0, 10.0), "ts": now - timedelta(seconds=30)})

    assert await store.latest_host_sample(max_age_seconds=10) is None
    latest = await store.latest_host_sample(max_age_seconds=60)
    assert latest is not None and latest["cpu"]["pct"] == 10.0


@pytest.mark.asyncio
async def test_events_and_recent_jobs(store):
    events = [
        {"ts": T0, "meta": {"kind": "job.status", "source": "api", "tenant": "quellex", "job_id": "j1"}, "status": "running"},
        {"ts": T0 + timedelta(seconds=1), "meta": {"kind": "job.phase", "source": "api", "tenant": "quellex", "job_id": "j1"}, "phase": "processing"},
        {"ts": T0 + timedelta(seconds=2), "meta": {"kind": "file.end", "source": "api", "tenant": "quellex", "job_id": "j1"}, "outcome": "ok"},
        {"ts": T0 + timedelta(seconds=3), "meta": {"kind": "file.end", "source": "api", "tenant": "quellex", "job_id": "j1"}, "outcome": "error"},
        {"ts": T0 + timedelta(seconds=4), "meta": {"kind": "job.status", "source": "api", "tenant": "quellex", "job_id": "j1"}, "status": "completed"},
        {"ts": T0 + timedelta(seconds=5), "meta": {"kind": "chat.request", "source": "api", "tenant": "quellex", "job_id": None}},
    ]
    assert await store.insert_events(events) == 6

    j1 = await store.events(job_id="j1")
    assert [e["meta"]["kind"] for e in j1][:2] == ["job.status", "job.phase"]
    chats = await store.events(kinds=["chat.request"])
    assert len(chats) == 1

    jobs = await store.recent_jobs(T0 - timedelta(minutes=1), T0 + timedelta(minutes=1))
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == "j1"
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["files"] == 2
    assert jobs[0]["failed"] == 1


@pytest.mark.asyncio
async def test_activity_current_upsert(store):
    await store.upsert_activity_current("quellex:api", {"job_id": "j1", "phase": "processing"})
    await store.upsert_activity_current("quellex:api", {"job_id": "j2", "phase": "finalizing"})
    docs = [d async for d in store.db["activity_current"].find({})]
    assert docs == [{"_id": "quellex:api", "job_id": "j2", "phase": "finalizing"}]


@pytest.mark.asyncio
async def test_tenant_scope_reads_own_and_shared_activity_only(store):
    def event(tenant: str, job: str, seconds: int, kind: str = "file.end") -> dict:
        fields = {"status": "completed"} if kind == "job.status" else {"outcome": "ok", "file": {"path": f"/app/mounts/{tenant}/{job}.pdf"}}
        return {"ts": T0 + timedelta(seconds=seconds), "meta": {"kind": kind, "source": "api", "tenant": tenant, "job_id": job}, **fields}

    await store.insert_events([
        event("quellex", "q1", 1), event("quellex", "q1", 2, "job.status"),
        event("recallhub", "r1", 3), event("recallhub", "r1", 4, "job.status"),
        event("shared", "w1", 5),
    ])
    quellex = TelemetryStore(store._client, store.database_name, host_samples_ttl_seconds=86400,
                             activity_events_ttl_seconds=86400, owns_client=False, tenant_scope="quellex")
    start, end = T0, T0 + timedelta(minutes=1)

    assert {e["meta"]["job_id"] for e in await quellex.events(start=start, end=end)} == {"q1", "w1"}
    assert await quellex.events(job_id="r1") == []  # another tenant's job is not found at all
    assert {j["job_id"] for j in await quellex.recent_jobs(start, end)} == {"q1", "w1"}
    counts = await quellex.aggregate_events([{"$group": {"_id": "$meta.tenant", "n": {"$sum": 1}}}])
    assert {c["_id"]: c["n"] for c in counts} == {"quellex": 2, "shared": 1}
    assert len(await store.events(start=start, end=end)) == 5  # the unscoped store (operator tools) sees all
