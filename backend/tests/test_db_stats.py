"""Unit tests for the MongoDB statistics sampler (:mod:`backend.services.db_stats`).

The summarisers are pure; the sampler and the collector run against an in-memory fake of the
``AsyncMongoClient`` surface they use (``command``, ``list_collection_names``, ``aggregate``,
``find_one``, ``estimated_document_count``). The real pipelines were checked once against the
live Atlas Local server (500 ms, no failed probes).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from backend.services import db_stats
from backend.services.db_stats import (
    LEASE_NAME,
    DbStatsCollector,
    collect_db_sample,
    oplog_summary,
    search_index_entries,
    server_summary,
    sum_totals,
    summarize_chunk_rows,
    summarize_document_rows,
)
from backend.services.telemetry_store import slot_due_at

T0 = datetime(2026, 10, 24, 15, 0, 0, tzinfo=timezone.utc)


# ── pure summarisers ─────────────────────────────────────────────────────────


class TestSummarizers:
    def test_chunk_rows_cover_arrays_binary_and_missing(self):
        rows = [
            {"embedding_type": "array", "dims": 768, "model": "embeddinggemma-2", "method": "hybrid", "has_context": "True", "token_count": "300"},
            {"embedding_type": "array", "dims": 768, "model": "embeddinggemma-2", "method": "hybrid", "has_context": True, "token_count": 100},
            {"embedding_type": "array", "dims": 3072, "model": "text-embedding-3-large", "method": "simple_fallback", "has_context": None, "token_count": 500},
            {"embedding_type": "binData", "dims": None, "model": "x", "method": "hybrid", "has_context": "False", "token_count": None},
            {"embedding_type": "missing", "dims": None, "model": None, "method": None, "has_context": None, "token_count": None},
        ]
        out = summarize_chunk_rows(rows)
        assert out["size"] == 5 and out["with_embedding"] == 4
        assert out["embedding_coverage_pct"] == 80.0
        assert out["embedding_dims"] == {"768": 2, "3072": 1, "binData": 1}
        assert out["embedding_models"] == {"embeddinggemma-2": 2, "text-embedding-3-large": 1, "x": 1}
        assert out["chunk_methods"] == {"hybrid": 3, "simple_fallback": 1}
        assert out["with_context_pct"] == 40.0
        assert out["token_p50"] == 300.0 and out["token_p95"] == 500.0

    def test_empty_chunk_sample(self):
        out = summarize_chunk_rows([])
        assert out["size"] == 0 and out["embedding_coverage_pct"] is None and out["token_p50"] is None

    def test_document_rows_count_zero_chunks_in_strings_too(self):
        out = summarize_document_rows([{"chunks_count": 5}, {"chunks_count": "0"}, {"chunks_count": 0}, {"chunks_count": None}])
        assert out == {"size": 4, "zero_chunks": 2, "zero_chunks_pct": 50.0}

    def test_server_summary_extracts_the_charted_fields(self):
        status = {
            "version": "8.0.16", "uptime": 3600.5,
            "connections": {"current": 71, "active": 21, "available": 32129, "totalCreated": 28916},
            "wiredTiger": {"cache": {"bytes currently in the cache": 10, "maximum bytes configured": 100, "tracked dirty bytes in the cache": 1}},
            "opcounters": {"insert": 5, "query": 6, "update": 7, "delete": 8, "getmore": 9, "command": 10, "other": 11},
            "mem": {"resident": 2},
        }
        out = server_summary(status)
        assert out["version"] == "8.0.16" and out["uptime_s"] == 3600.5
        assert out["connections"] == {"current": 71, "active": 21, "available": 32129, "total_created": 28916}
        assert out["wt_cache"] == {"used_bytes": 10, "max_bytes": 100, "dirty_bytes": 1}
        assert "other" not in out["opcounters"] and out["opcounters"]["insert"] == 5
        assert out["mem_resident_bytes"] == 2 * 1024 * 1024

    def test_oplog_summary_window(self):
        out = oplog_summary({"size": 10, "storageSize": 8, "maxSize": 50}, {"wall": T0}, {"wall": T0 + timedelta(hours=2)})
        assert out["data_bytes"] == 10 and out["storage_bytes"] == 8 and out["max_bytes"] == 50
        assert out["window_s"] == 7200.0 and out["first_at"] == T0
        assert oplog_summary({}, None, None)["window_s"] is None

    def test_search_index_entries_pick_the_vector_field(self):
        rows = [
            {"name": "vector_index", "type": "vectorSearch", "status": "READY", "queryable": True,
             "latestDefinition": {"fields": [{"type": "filter", "path": "metadata.x"},
                                             {"type": "vector", "path": "embedding", "numDimensions": 768, "similarity": "cosine"}]}},
            {"name": "text_index", "status": "BUILDING", "queryable": False, "latestDefinition": {"mappings": {"dynamic": True}}},
        ]
        out = search_index_entries(rows)
        assert out[0] == {"name": "vector_index", "type": "vectorSearch", "status": "READY", "queryable": True,
                          "dimensions": 768, "similarity": "cosine", "quantization": None}
        assert out[1]["type"] == "search" and out[1]["status"] == "BUILDING" and out[1]["dimensions"] is None

    def test_sum_totals(self):
        dbs = [
            {"documents": {"count": 10}, "chunks": {"count": 100}, "data_bytes": 5, "storage_bytes": 3, "index_bytes": 1, "size_on_disk_bytes": 4},
            {"documents": None, "chunks": {"count": 1}, "data_bytes": None, "storage_bytes": 1},
        ]
        assert sum_totals(dbs) == {"databases": 2, "documents": 10, "chunks": 101, "data_bytes": 5, "storage_bytes": 4,
                                   "index_bytes": 1, "size_on_disk_bytes": 4}


def test_slot_due_at_is_clock_aligned():
    assert slot_due_at(datetime(2026, 10, 24, 15, 7, 12, tzinfo=timezone.utc), 1800) == datetime(2026, 10, 24, 15, 30, tzinfo=timezone.utc)
    assert slot_due_at(datetime(2026, 10, 24, 15, 30, 0, tzinfo=timezone.utc), 1800) == datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)
    assert slot_due_at(datetime(2026, 10, 24, 23, 59, 59, tzinfo=timezone.utc), 3600) == datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)


# ── fake client ──────────────────────────────────────────────────────────────


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = list(rows)

    async def to_list(self, length: Optional[int] = None) -> list[dict[str, Any]]:
        return self._rows if length is None else self._rows[:length]


class _Collection:
    def __init__(self, name: str):
        self.name = name
        self.storage: dict[str, Any] = {}
        self.sample_rows: list[dict[str, Any]] = []
        self.indexed: Optional[int] = None
        self.group_rows: list[dict[str, Any]] = []
        self.newest: Optional[dict[str, Any]] = None
        self.oplog_first: Optional[dict[str, Any]] = None
        self.oplog_last: Optional[dict[str, Any]] = None
        self.estimated = 0
        self.pipelines: list[list[dict[str, Any]]] = []

    async def aggregate(self, pipeline: list[dict[str, Any]], maxTimeMS: Optional[int] = None) -> _Cursor:
        assert maxTimeMS, "every aggregation must carry a timeout"
        self.pipelines.append(pipeline)
        stage = pipeline[0]
        if "$collStats" in stage:
            return _Cursor([{"storageStats": dict(self.storage)}])
        if "$sample" in stage:
            assert "$project" in pipeline[1], "samples must be projected server-side"
            return _Cursor(self.sample_rows)
        if "$searchMeta" in stage:
            return _Cursor([{"count": {"total": self.indexed}}])
        if "$group" in stage:
            return _Cursor(self.group_rows)
        raise AssertionError(f"unexpected pipeline {pipeline}")

    async def find_one(self, filter=None, sort=None, projection=None):
        if sort and sort[0][0] == "$natural":
            return self.oplog_first if sort[0][1] == 1 else self.oplog_last
        return self.newest

    async def estimated_document_count(self) -> int:
        return self.estimated


class _Database:
    def __init__(self, name: str):
        self.name = name
        self.commands: dict[str, Any] = {}
        self.collections: dict[str, _Collection] = {}

    async def command(self, *args: Any, **kwargs: Any) -> Any:
        key = args[0] if isinstance(args[0], str) else next(iter(args[0]))
        result = self.commands.get(key)
        if isinstance(result, Exception):
            raise result
        return result

    async def list_collection_names(self) -> list[str]:
        return list(self.collections)

    def __getitem__(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection(name))


class _Client:
    def __init__(self):
        self.dbs: dict[str, _Database] = {}

    def __getitem__(self, name: str) -> _Database:
        return self.dbs.setdefault(name, _Database(name))

    @property
    def admin(self) -> _Database:
        return self["admin"]


def _fake_cluster() -> _Client:
    client = _Client()
    client["admin"].commands["serverStatus"] = {"version": "8.0.16", "uptime": 10, "connections": {"current": 3, "totalCreated": 9}}
    client["admin"].commands["listDatabases"] = {"databases": [
        {"name": "admin", "sizeOnDisk": 1}, {"name": "local", "sizeOnDisk": 2000}, {"name": "config", "sizeOnDisk": 1},
        {"name": "recallhub_telemetry", "sizeOnDisk": 30}, {"name": "rag_a", "sizeOnDisk": 500}, {"name": "rag_empty", "sizeOnDisk": 5},
    ]}
    oplog = client["local"]["oplog.rs"]
    oplog.storage = {"size": 1500, "storageSize": 1000, "maxSize": 4000}
    oplog.oplog_first, oplog.oplog_last = {"wall": T0 - timedelta(hours=3)}, {"wall": T0}
    client["recallhub_telemetry"].commands["dbStats"] = {"dataSize": 20, "storageSize": 10, "indexSize": 2,
                                                         "fsUsedSize": 600, "fsTotalSize": 1000}
    a = client["rag_a"]
    a.commands["dbStats"] = {"dataSize": 300, "storageSize": 200, "indexSize": 8, "collections": 4, "objects": 110}
    a.commands["listSearchIndexes"] = {"cursor": {"firstBatch": [
        {"name": "text_index", "type": "search", "status": "READY", "queryable": True, "latestDefinition": {}},
        {"name": "vector_index", "type": "vectorSearch", "status": "READY", "queryable": True,
         "latestDefinition": {"fields": [{"type": "vector", "path": "embedding", "numDimensions": 768, "similarity": "cosine"}]}},
    ]}}
    a["documents"].storage = {"count": 10, "size": 50, "storageSize": 30, "totalIndexSize": 1, "avgObjSize": 5.0}
    a["documents"].sample_rows = [{"chunks_count": 3}] * 9 + [{"chunks_count": 0}]
    a["chunks"].storage = {"count": 100, "size": 250, "storageSize": 170, "totalIndexSize": 7, "avgObjSize": 2.5}
    a["chunks"].sample_rows = [{"embedding_type": "array", "dims": 768, "model": "m", "method": "hybrid", "has_context": "True", "token_count": 10}] * 99 \
        + [{"embedding_type": "missing", "dims": None, "model": None, "method": None, "has_context": None, "token_count": None}]
    a["chunks"].indexed = 90
    a["ingestion_jobs"].group_rows = [{"_id": "COMPLETED", "n": 2}, {"_id": None, "n": 1}]
    a["backups_metadata"].newest = {"created_at": T0 - timedelta(days=9), "status": "completed", "backup_type": "full", "size_bytes": 0}
    a["backups_metadata"].estimated = 2
    a["failed_documents"].estimated = 4
    e = client["rag_empty"]
    e.commands["dbStats"] = {"dataSize": 0, "storageSize": 0, "indexSize": 0, "collections": 0, "objects": 0}
    return client


class TestCollectDbSample:
    @pytest.mark.asyncio
    async def test_full_sample(self):
        client = _fake_cluster()
        sample = await collect_db_sample(client, telemetry_database="recallhub_telemetry",
                                         profile_databases={"rag_a": "alpha", "rag_new": "fresh"}, collector="t:api", now=T0)
        assert sample["ts"] == T0 and sample["collector"] == "t:api" and sample["forced"] is False
        assert sample["meta"] == {"source": "db-stats", "collector": "t:api", "schema": 1}
        assert sample["errors"] == {}
        assert sample["server"]["connections"]["total_created"] == 9
        assert sample["oplog"] == {"data_bytes": 1500, "storage_bytes": 1000, "max_bytes": 4000, "window_s": 10800.0,
                                   "first_at": T0 - timedelta(hours=3), "last_at": T0}
        assert sample["fs"] == {"used_bytes": 600, "total_bytes": 1000, "pct": 60.0}
        assert sample["telemetry"] == {"data_bytes": 20, "storage_bytes": 10, "index_bytes": 2, "size_on_disk_bytes": 30}
        names = [d["name"] for d in sample["databases"]]
        assert names == ["rag_a", "rag_empty", "rag_new"]  # system + telemetry databases excluded, configured one added
        a = sample["databases"][0]
        assert a["profile_key"] == "alpha" and a["size_on_disk_bytes"] == 500 and a["collections"] == 4
        assert a["documents"] == {"count": 10, "data_bytes": 50, "storage_bytes": 30, "index_bytes": 1, "avg_bytes": 5.0}
        assert a["chunks"]["count"] == 100
        assert a["sample"]["size"] == 100 and a["sample"]["with_embedding"] == 99 and a["sample"]["embedding_dims"] == {"768": 99}
        assert a["document_sample"] == {"size": 10, "zero_chunks": 1, "zero_chunks_pct": 10.0}
        assert [i["name"] for i in a["search_indexes"]] == ["text_index", "vector_index"]
        assert a["search_lag"] == {"index": "text_index", "indexed_count": 90, "collection_count": 100, "lag": 10}
        assert a["jobs_by_status"] == {"completed": 2, "unknown": 1}
        assert a["backup"]["count"] == 2 and a["backup"]["newest_size_bytes"] == 0 and a["backup"]["newest_at"] == T0 - timedelta(days=9)
        assert a["failed_documents"] == 4 and a["file_registry"] is None
        empty = sample["databases"][1]
        assert empty["documents"] is None and empty["sample"] is None and empty["search_indexes"] == []
        new = sample["databases"][2]
        assert new["profile_key"] == "fresh" and new["data_bytes"] is None
        assert sample["totals"] == {"databases": 3, "documents": 10, "chunks": 100, "data_bytes": 300, "storage_bytes": 200,
                                    "index_bytes": 8, "size_on_disk_bytes": 505}

    @pytest.mark.asyncio
    async def test_sample_sizes_follow_the_collection_and_skip_embeddings(self):
        client = _fake_cluster()
        await collect_db_sample(client, telemetry_database="recallhub_telemetry", sample_size=40, now=T0)
        chunks = client["rag_a"]["chunks"]
        sample_stage = next(p for p in chunks.pipelines if "$sample" in p[0])
        assert sample_stage[0]["$sample"]["size"] == 40
        assert "embedding" not in sample_stage[1]["$project"] or sample_stage[1]["$project"].get("embedding") in (None, 0)
        docs = client["rag_a"]["documents"]
        assert next(p for p in docs.pipelines if "$sample" in p[0])[0]["$sample"]["size"] == 10  # min(20, 10 documents)

    @pytest.mark.asyncio
    async def test_failed_probe_is_recorded_not_raised(self):
        client = _fake_cluster()
        client["rag_a"].commands["listSearchIndexes"] = RuntimeError("mongot down")
        client["admin"].commands["serverStatus"] = RuntimeError("no")
        sample = await collect_db_sample(client, telemetry_database="recallhub_telemetry", now=T0)
        assert sample["server"] is None
        assert sample["errors"]["serverStatus"].startswith("RuntimeError: no")
        assert "rag_a.listSearchIndexes" in sample["errors"]
        a = sample["databases"][0]
        assert a["search_indexes"] == [] and a["search_lag"] is None and a["chunks"]["count"] == 100

    @pytest.mark.asyncio
    async def test_probe_timeout_is_recorded(self):
        client = _fake_cluster()

        async def slow(*_args, **_kwargs):
            await asyncio.sleep(0.2)
            return {}

        client["rag_a"].command = slow  # type: ignore[method-assign]
        sample = await collect_db_sample(client, telemetry_database="recallhub_telemetry", now=T0, command_timeout_s=0.01)
        assert sample["errors"]["rag_a.dbStats"].startswith("TimeoutError") or "Timeout" in sample["errors"]["rag_a.dbStats"]


# ── collector ────────────────────────────────────────────────────────────────


class _FakeStore:
    def __init__(self, client: _Client, grants: int = 1):
        self.client = client
        self.database_name = "recallhub_telemetry"
        self.inserted: list[dict[str, Any]] = []
        self.lease_calls: list[tuple[str, int, str]] = []
        self._grants = grants

    async def acquire_lease(self, name, interval_seconds, holder, *, now=None):
        self.lease_calls.append((name, interval_seconds, holder))
        if self._grants > 0:
            self._grants -= 1
            return True
        return False

    async def insert_db_sample(self, doc):
        self.inserted.append(doc)


class TestDbStatsCollector:
    @pytest.mark.asyncio
    async def test_collect_and_store_marks_forced_and_labels_profiles(self):
        store = _FakeStore(_fake_cluster())
        collector = DbStatsCollector(store, holder="quellex:api", interval_seconds=1800,  # type: ignore[arg-type]
                                     profile_databases=lambda: {"rag_a": "alpha"})
        sample = await collector.collect_and_store(forced=True)
        assert sample["forced"] is True and sample["collector"] == "quellex:api"
        assert store.inserted[0] is sample
        assert collector.last_sample_at == sample["ts"] and collector.last_error is None
        assert sample["databases"][0]["profile_key"] == "alpha"

    @pytest.mark.asyncio
    async def test_profile_lookup_failure_only_drops_labels(self):
        store = _FakeStore(_fake_cluster())

        def boom():
            raise RuntimeError("profiles.yaml unreadable")

        collector = DbStatsCollector(store, holder="shared:worker", interval_seconds=1800, profile_databases=boom)  # type: ignore[arg-type]
        sample = await collector.collect_and_store()
        assert sample["databases"][0]["profile_key"] is None

    @pytest.mark.asyncio
    async def test_loop_samples_only_when_the_lease_is_granted(self):
        store = _FakeStore(_fake_cluster(), grants=1)
        collector = DbStatsCollector(store, holder="quellex:api", interval_seconds=60, max_poll_s=0.05)  # type: ignore[arg-type]
        await collector.start()
        await asyncio.sleep(0.3)
        await collector.stop()
        assert len(store.inserted) == 1
        assert len(store.lease_calls) >= 2 and store.lease_calls[0] == (LEASE_NAME, 60, "quellex:api")

    def test_next_slot_wait_is_bounded(self):
        store = _FakeStore(_fake_cluster())
        collector = DbStatsCollector(store, holder="x", interval_seconds=1800, max_poll_s=100.0)  # type: ignore[arg-type]
        wait = collector.seconds_until_next_slot(datetime(2026, 10, 24, 15, 29, 0, tzinfo=timezone.utc))
        assert 60.5 <= wait <= 65.0  # until 15:30 plus jitter
        assert collector.seconds_until_next_slot(datetime(2026, 10, 24, 15, 0, 1, tzinfo=timezone.utc)) == 100.0  # capped
        assert collector.interval_seconds == 1800 and collector.holder == "x"

    @pytest.mark.asyncio
    async def test_loop_survives_a_failing_run(self, monkeypatch):
        store = _FakeStore(_fake_cluster(), grants=5)

        async def boom(*_a, **_k):
            raise RuntimeError("mongo hiccup")

        monkeypatch.setattr(db_stats, "collect_db_sample", boom)
        collector = DbStatsCollector(store, holder="x", interval_seconds=60, max_poll_s=0.02)  # type: ignore[arg-type]
        await collector.start()
        await asyncio.sleep(0.15)
        await collector.stop()
        assert store.inserted == [] and collector.last_error and "mongo hiccup" in collector.last_error
        assert len(store.lease_calls) >= 2  # kept looping after the failure
