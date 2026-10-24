"""db_samples, leases and the sampler against a real MongoDB.

Runs only when ``RECALLHUB_TEST_MONGO_URI`` points at a throwaway server — never the stack's
database. Each test uses its own random databases and drops them afterwards::

    $env:RECALLHUB_TEST_MONGO_URI = "mongodb://127.0.0.1:27999/?directConnection=true"
    uv run pytest backend/tests/integration/test_db_stats_mongo.py -v

A standalone server has no ``local.oplog.rs`` and no mongot, so the oplog and ``$searchMeta``
probes are expected to land in the sample's ``errors`` here; everything else runs for real
(``$collStats``, the projected ``$sample`` stages, ``listSearchIndexes`` on a server without
search returns an error too).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pymongo import AsyncMongoClient

from backend.services.db_stats import LEASE_NAME, collect_db_sample
from backend.services.telemetry_analysis import db_rates
from backend.services.telemetry_store import DB_SAMPLES, TelemetryStore

MONGO_URI = os.environ.get("RECALLHUB_TEST_MONGO_URI")

pytestmark = pytest.mark.skipif(not MONGO_URI, reason="RECALLHUB_TEST_MONGO_URI not set")

T0 = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def client():
    c = AsyncMongoClient(MONGO_URI, tz_aware=True, tzinfo=timezone.utc)
    yield c
    await c.close()


@pytest.fixture
async def store(client):
    name = f"telemetry_it_{uuid.uuid4().hex[:8]}"
    s = TelemetryStore(client, name, host_samples_ttl_seconds=86400, activity_events_ttl_seconds=86400, owns_client=False,
                       db_samples_ttl_seconds=86400, tenant_scope="quellex")
    await s.ensure_collections()
    yield s
    await client.drop_database(name)


def _sample(minutes: int, chunks: int, created: int) -> dict:
    return {
        "ts": T0 + timedelta(minutes=minutes),
        "meta": {"source": "db-stats", "collector": "test", "schema": 1},
        "server": {"connections": {"current": 10, "total_created": created}, "wt_cache": {"used_bytes": 5, "max_bytes": 10},
                   "opcounters": {"insert": chunks}},
        "oplog": {"storage_bytes": 100, "max_bytes": 1000, "window_s": 60.0},
        "fs": {"used_bytes": 1, "total_bytes": 2},
        "totals": {"databases": 1, "documents": chunks // 10, "chunks": chunks, "data_bytes": chunks * 3, "storage_bytes": chunks * 2,
                   "index_bytes": 1, "size_on_disk_bytes": chunks * 2},
        "databases": [{"name": "rag_a", "documents": {"count": chunks // 10}, "chunks": {"count": chunks}, "storage_bytes": chunks * 2,
                       "data_bytes": chunks * 3, "index_bytes": 1,
                       "sample": {"size": 10, "with_embedding": 10, "embedding_coverage_pct": 100.0},
                       "search_lag": {"index": "text_index", "indexed_count": chunks, "collection_count": chunks, "lag": 0}}],
        "errors": {},
    }


@pytest.mark.asyncio
async def test_db_samples_is_a_timeseries_collection_and_series_takes_the_last_per_bucket(store):
    info = [i async for i in await store.db.list_collections(filter={"name": DB_SAMPLES})][0]
    assert info["type"] == "timeseries" and info["options"]["timeseries"]["granularity"] == "minutes"

    for minutes, chunks, created in ((0, 100, 1000), (30, 200, 1600), (60, 300, 1900)):
        await store.insert_db_sample(_sample(minutes, chunks, created))

    latest = await store.latest_db_sample()
    assert latest["totals"]["chunks"] == 300 and "_id" not in latest
    assert await store.latest_db_sample(max_age_seconds=1) is None  # T0 is years before "now"

    points = await store.db_series(T0, T0 + timedelta(hours=2), 1800)
    assert [(p["t"], p["chunks"], p["connections_total_created"]) for p in points] == [
        (T0, 100, 1000), (T0 + timedelta(minutes=30), 200, 1600), (T0 + timedelta(hours=1), 300, 1900)]
    assert points[0]["databases"] == [{"name": "rag_a", "documents": 10, "chunks": 100, "storage_bytes": 200, "data_bytes": 300,
                                       "index_bytes": 1, "embedding_coverage_pct": 100.0, "search_lag": 0}]
    assert "sample" not in points[0] and "server" not in points[0]  # compact projection only

    hourly = await store.db_series(T0, T0 + timedelta(hours=2), 3600)
    # The last sample of each bucket, stamped with its own time (not the bucket start).
    assert [(p["t"], p["chunks"]) for p in hourly] == [(T0 + timedelta(minutes=30), 200), (T0 + timedelta(hours=1), 300)]

    rates = db_rates(points)
    assert rates[1]["connections_created_per_min"] == 20.0 and rates[2]["inserts_per_min"] == 3.33  # rounded to 2 decimals


@pytest.mark.asyncio
async def test_lease_is_taken_once_per_slot(store):
    now = datetime(2026, 10, 24, 15, 7, 0, tzinfo=timezone.utc)
    assert await store.acquire_lease(LEASE_NAME, 1800, "quellex:api", now=now) is True
    assert await store.acquire_lease(LEASE_NAME, 1800, "shared:worker", now=now + timedelta(seconds=1)) is False
    assert await store.acquire_lease(LEASE_NAME, 1800, "shared:worker", now=now + timedelta(minutes=23)) is True
    status = await store.lease_status(LEASE_NAME)
    assert status["holder"] == "shared:worker" and status["due_at"] == datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_activity_current_docs_respect_the_scope(store):
    await store.upsert_activity_current("quellex:api", {"tenant": "quellex", "source": "api", "updated_at": T0})
    await store.upsert_activity_current("shared:worker", {"tenant": "shared", "source": "worker", "updated_at": T0})
    await store.upsert_activity_current("recallhub:api", {"tenant": "recallhub", "source": "api", "updated_at": T0})
    assert sorted(d["_id"] for d in await store.activity_current_docs()) == ["quellex:api", "shared:worker"]


@pytest.mark.asyncio
async def test_collect_db_sample_against_a_real_server(client, store):
    app_db = f"rag_it_{uuid.uuid4().hex[:8]}"
    try:
        await client[app_db]["documents"].insert_many([{"source": f"/d{i}", "metadata": {"chunks_count": i % 3}} for i in range(9)])
        await client[app_db]["chunks"].insert_many([
            {"document_id": i, "content": "x", "embedding": [0.1] * 4, "token_count": 7,
             "metadata": {"embedding_model": "m", "chunk_method": "hybrid", "has_context": "True"}} for i in range(20)
        ] + [{"document_id": 99, "content": "y", "metadata": {}}])
        await client[app_db]["ingestion_jobs"].insert_many([{"status": "completed"}, {"status": "running"}])
        await client[app_db]["backups_metadata"].insert_one({"created_at": T0, "status": "completed", "backup_type": "full", "size_bytes": 5})

        sample = await collect_db_sample(client, telemetry_database=store.database_name, profile_databases={app_db: "it"},
                                         collector="it:test", sample_size=50)
    finally:
        await client.drop_database(app_db)

    assert sample["server"]["version"] and sample["server"]["connections"]["current"] >= 1
    assert sample["fs"] is not None and sample["fs"]["total_bytes"] > 0
    entry = next(d for d in sample["databases"] if d["name"] == app_db)
    assert entry["profile_key"] == "it" and entry["documents"]["count"] == 9 and entry["chunks"]["count"] == 21
    assert entry["sample"]["size"] == 21 and entry["sample"]["with_embedding"] == 20
    assert entry["sample"]["embedding_dims"] == {"4": 20} and entry["sample"]["embedding_models"] == {"m": 20}
    assert entry["sample"]["with_context_pct"] == pytest.approx(100 * 20 / 21, abs=0.01) and entry["sample"]["token_p50"] == 7.0
    assert entry["document_sample"] == {"size": 9, "zero_chunks": 3, "zero_chunks_pct": pytest.approx(33.33, abs=0.01)}
    assert entry["jobs_by_status"] == {"completed": 1, "running": 1}
    assert entry["backup"]["count"] == 1 and entry["backup"]["newest_size_bytes"] == 5 and entry["backup"]["newest_at"] == T0
    assert sample["totals"]["chunks"] >= 21
    # Standalone server: no oplog, no search indexes -> recorded, not raised.
    assert "oplog.collStats" in sample["errors"] or sample["oplog"] is not None
    assert entry["search_lag"] is None

    await store.insert_db_sample(sample)
    assert (await store.latest_db_sample())["collector"] == "it:test"
