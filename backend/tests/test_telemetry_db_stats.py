"""Tests for the database side of the telemetry history: anomaly rules on ``db_samples``, the
per-minute rate derivation, process liveness, the overview assembly and the db-stats / overview
endpoints of ``/api/v1/telemetry``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.models.telemetry_history import AnomalyReport, DbSample, OverviewAnomalies, OverviewChat, TelemetryOverview
from backend.services import telemetry_analysis as analysis_module
from backend.services import telemetry_overview as tov
from backend.services.telemetry_analysis import db_anomalies_from, db_rates, liveness_anomalies_from

T0 = datetime(2026, 10, 18, 10, 0, 0, tzinfo=timezone.utc)


def _db_sample(**overrides) -> dict:
    sample = {
        "ts": T0,
        "collector": "quellex:api",
        "forced": False,
        "duration_ms": 500.0,
        "server": {"version": "8.0.16", "connections": {"current": 70, "total_created": 100}},
        "oplog": {"storage_bytes": 46 * 1024 ** 3, "max_bytes": 50 * 1024 ** 3, "window_s": 3600.0},
        "fs": {"used_bytes": 950, "total_bytes": 1000, "pct": 95.0},
        "totals": {"databases": 1, "documents": 10, "chunks": 100, "data_bytes": 3, "storage_bytes": 2, "index_bytes": 1,
                   "size_on_disk_bytes": 2},
        "databases": [{
            "name": "rag_a", "profile_key": "a", "storage_bytes": 2, "data_bytes": 3, "index_bytes": 1,
            "documents": {"count": 10}, "chunks": {"count": 100},
            "sample": {"size": 100, "with_embedding": 95, "embedding_coverage_pct": 95.0,
                       "embedding_dims": {"768": 90, "3072": 5}, "embedding_models": {"a": 90, "b": 5}},
            "search_indexes": [{"name": "vector_index", "type": "vectorSearch", "status": "BUILDING", "queryable": False},
                               {"name": "text_index", "type": "search", "status": "READY", "queryable": True}],
            "search_lag": {"index": "text_index", "indexed_count": 50, "collection_count": 100, "lag": 50},
            "backup": {"count": 1, "newest_at": T0 - timedelta(days=10), "newest_status": "completed", "newest_size_bytes": 5},
        }],
        "errors": {},
    }
    sample.update(overrides)
    return sample


class TestDbAnomalyRules:
    def test_state_rules_fire_on_a_bad_sample(self):
        kinds = {a.kind: a for a in db_anomalies_from(_db_sample(), [])}
        assert kinds["search_index_unhealthy"].severity == "critical"
        assert kinds["search_index_unhealthy"].examples[0]["index"] == "vector_index"
        assert kinds["embedding_coverage"].examples[0]["coverage_pct"] == 95.0
        assert kinds["embedding_mix"].severity == "critical"  # two vector lengths
        assert kinds["search_lag"].examples[0]["lag"] == 50
        assert kinds["oplog_near_cap"].severity == "info"
        assert "10 days old" in kinds["backup_stale"].examples[0]["reason"]
        assert kinds["filesystem_full"].severity == "warning"

    def test_healthy_sample_is_quiet(self):
        healthy = _db_sample(fs={"used_bytes": 1, "total_bytes": 1000, "pct": 0.1},
                             oplog={"storage_bytes": 1, "max_bytes": 100, "window_s": 1.0})
        healthy["databases"][0].update({
            "sample": {"size": 100, "with_embedding": 100, "embedding_coverage_pct": 100.0, "embedding_dims": {"768": 100},
                       "embedding_models": {"a": 100}},
            "search_indexes": [{"name": "vector_index", "type": "vectorSearch", "status": "READY", "queryable": True}],
            "search_lag": {"index": "text_index", "indexed_count": 100, "collection_count": 100, "lag": 0},
            "backup": {"count": 1, "newest_at": T0 - timedelta(hours=5), "newest_status": "completed", "newest_size_bytes": 5},
        })
        assert db_anomalies_from(healthy, []) == []

    def test_empty_backup_and_mixed_models_only(self):
        sample = _db_sample()
        sample["databases"][0]["sample"]["embedding_dims"] = {"768": 95}
        sample["databases"][0]["backup"] = {"count": 1, "newest_at": T0 - timedelta(hours=1), "newest_status": "completed",
                                            "newest_size_bytes": 0}
        kinds = {a.kind: a for a in db_anomalies_from(sample, [])}
        assert kinds["embedding_mix"].severity == "warning"
        assert "empty" in kinds["backup_stale"].examples[0]["reason"]

    def test_no_backup_recorded(self):
        sample = _db_sample()
        sample["databases"][0]["backup"] = None
        kinds = {a.kind: a for a in db_anomalies_from(sample, [])}
        assert kinds["backup_stale"].examples[0]["reason"] == "no backup recorded"

    def test_trend_rules(self):
        h = timedelta(hours=1)
        points = [
            {"t": T0, "connections_total_created": 0, "fs_used_bytes": 100 * 1024 ** 3, "fs_total_bytes": 200 * 1024 ** 3,
             "databases": [{"name": "rag_a", "documents": 100, "chunks": 1000}]},
            {"t": T0 + 6 * h, "connections_total_created": 6 * 60 * 25, "fs_used_bytes": 120 * 1024 ** 3,
             "fs_total_bytes": 200 * 1024 ** 3, "databases": [{"name": "rag_a", "documents": 200, "chunks": 1000}]},
        ]
        kinds = {a.kind: a for a in db_anomalies_from(None, points)}
        assert kinds["connection_churn"].examples[0]["per_min"] == 25.0
        assert kinds["chunks_flat"].examples[0] == {"database": "rag_a", "documents_added": 100, "chunks_added": 0}
        # 20 GB in 6 h = 80 GB/day; headroom to 90 % of 200 GB is 60 GB -> under a day
        assert kinds["disk_forecast"].severity == "critical"
        assert kinds["disk_forecast"].examples[0]["days_left"] < 1

    def test_trend_rules_need_two_points_and_ignore_counter_resets(self):
        assert db_anomalies_from(None, [{"t": T0, "connections_total_created": 5}]) == []
        points = [{"t": T0, "connections_total_created": 100_000},
                  {"t": T0 + timedelta(minutes=30), "connections_total_created": 10}]
        assert db_anomalies_from(None, points) == []


def test_db_rates_derive_per_minute_values():
    points = [
        {"t": T0, "connections_total_created": 100, "ops_insert": 1000},
        {"t": T0 + timedelta(minutes=30), "connections_total_created": 700, "ops_insert": 4000},
        {"t": T0 + timedelta(minutes=60), "connections_total_created": 5, "ops_insert": None},  # restart
    ]
    out = db_rates(points)
    assert "connections_created_per_min" not in out[0]
    assert out[1]["connections_created_per_min"] == 20.0 and out[1]["inserts_per_min"] == 100.0
    assert out[2]["connections_created_per_min"] is None and out[2]["inserts_per_min"] is None
    assert points[1].get("inserts_per_min") is None  # inputs untouched


class TestLivenessRules:
    def test_silent_processes(self):
        now = T0 + timedelta(minutes=10)
        docs = [
            {"_id": "quellex:api", "updated_at": now - timedelta(seconds=20)},
            {"_id": "shared:worker", "updated_at": T0, "job_id": None},
            {"_id": "recallhub:api", "updated_at": T0, "job_id": "j1", "job_status": "running"},
        ]
        (anomaly,) = liveness_anomalies_from(docs, now)
        assert anomaly.kind == "process_silent" and anomaly.severity == "critical" and anomaly.count == 2
        assert {e["process"] for e in anomaly.examples} == {"shared:worker", "recallhub:api"}
        assert next(e for e in anomaly.examples if e["process"] == "recallhub:api")["job_id"] == "j1"

    def test_idle_silent_process_is_a_warning_and_fresh_ones_are_quiet(self):
        now = T0 + timedelta(minutes=10)
        silent_done = [{"_id": "shared:worker", "updated_at": T0, "job_id": "j1", "job_status": "completed"}]
        assert liveness_anomalies_from(silent_done, now)[0].severity == "warning"
        assert liveness_anomalies_from([{"_id": "quellex:api", "updated_at": now}], now) == []
        assert liveness_anomalies_from([{"_id": "x", "updated_at": None}], now)[0].examples[0]["last_seen"] is None


class TestOverviewHelpers:
    def test_overview_db_condenses_the_sample(self):
        now = T0 + timedelta(minutes=5)
        db = tov.overview_db(_db_sample(), now)
        assert db is not None and db.age_seconds == 300.0 and db.chunks == 100 and db.documents == 10
        assert db.oplog_bytes == 46 * 1024 ** 3
        assert db.embedding_coverage_pct == 95.0 and db.search_indexes_ok is False and db.search_lag == 50
        assert tov.overview_db(None, now) is None

    def test_process_rows_with_pace_and_eta(self):
        now = T0
        docs = [
            {"_id": "shared:worker", "tenant": "shared", "source": "worker", "updated_at": now - timedelta(hours=6)},
            {"_id": "quellex:api", "tenant": "quellex", "source": "api", "updated_at": now - timedelta(seconds=10),
             "job_id": "j1", "job_status": "running", "phase": "processing", "step": "embed",
             "processed_files": 9000, "total_files": 10000},
        ]
        rows = tov.process_rows(docs, now, online_within_s=90.0, pace={"j1": 500})
        assert [r.key for r in rows] == ["quellex:api", "shared:worker"]  # api first
        api, worker = rows
        assert api.online is True and api.files_per_hour == 500.0 and api.eta_seconds == 7200.0
        assert worker.online is False and worker.age_seconds == 6 * 3600.0 and worker.eta_seconds is None

    def test_anomaly_cache_respects_hours_and_ttl(self):
        cache = tov.AnomalyCache(ttl_seconds=60.0)
        report = AnomalyReport(start=T0, end=T0 + timedelta(hours=24), anomalies=[])
        assert cache.get(24.0) is None
        cache.put(24.0, report)
        assert cache.get(24.0) is report and cache.get(6.0) is None
        assert tov.top_titles(report) == []

    @pytest.mark.asyncio
    async def test_overview_assembles_sections_and_degrades(self, monkeypatch):
        class _Store:
            tenant_scope = "quellex"

            async def latest_db_sample(self):
                return _db_sample()

            async def activity_current_docs(self):
                return [{"_id": "quellex:api", "tenant": "quellex", "source": "api", "updated_at": T0, "job_id": "j1",
                         "job_status": "running", "processed_files": 1, "total_files": 3}]

            async def aggregate_events(self, pipeline):
                match = pipeline[0]["$match"]
                if match.get("meta.kind") == "file.end":
                    return [{"_id": "j1", "n": 2}]
                return [{"_id": None, "n": 4, "errors": 1, "p": [1000.0, 9000.0]}]

            async def latest_host_sample(self, *, max_age_seconds):
                return {"ts": T0, "ollama": {"models": [{"name": "gemma4:12b", "vram_bytes": 7 * 1024 ** 3, "ctx": 32768}]}}

        async def fake_anomalies(store, start, end, *, now=None):
            raise RuntimeError("rules exploded")

        monkeypatch.setattr(analysis_module, "range_anomalies", fake_anomalies)
        out = await tov.overview(_Store(), hours=24.0, now=T0)  # type: ignore[arg-type]
        assert isinstance(out, TelemetryOverview)
        assert out.anomalies == OverviewAnomalies(hours=24.0)  # degraded to empty
        assert out.db is not None and out.db.chunks == 100
        assert out.processes[0].files_per_hour == 2.0 and out.processes[0].eta_seconds == 3600.0
        assert out.chat == OverviewChat(hours=24.0, requests=4, errors=1, p50_ms=1000.0, p95_ms=9000.0)
        assert out.backups[0].database == "rag_a" and out.backups[0].age_seconds == 10 * 86400
        assert out.llm.models[0].name == "gemma4:12b" and out.llm.sampled_at == T0


class _FakeDbStore:
    tenant_scope = "quellex"

    def __init__(self):
        self.calls: list[tuple] = []

    async def db_series(self, start, end, bucket):
        self.calls.append(("series", start, end, bucket))
        return [
            {"t": T0, "connections_total_created": 100, "chunks": 10, "databases": [{"name": "rag_a", "chunks": 10}]},
            {"t": T0 + timedelta(minutes=30), "connections_total_created": 700, "chunks": 20, "databases": []},
        ]

    async def latest_db_sample(self):
        return _db_sample()

    async def lease_status(self, name):
        return {"due_at": T0 + timedelta(minutes=30), "holder": "quellex:api", "taken_at": T0}


class _FakeCollector:
    interval_seconds = 600

    async def collect_and_store(self, *, forced=False):
        return _db_sample(forced=forced)


class TestDbStatsRouter:
    @pytest.fixture
    def with_db_store(self, app):
        app.state.telemetry_store = _FakeDbStore()
        app.state.db_stats_collector = None
        yield app
        app.state.telemetry_store = None
        app.state.db_stats_collector = None

    def test_db_stats_series_and_latest(self, client: TestClient, with_db_store):
        response = client.get("/api/v1/telemetry/db-stats?hours=24")
        assert response.status_code == 200
        body = response.json()
        assert body["interval_seconds"] == 1800 and body["bucket_seconds"] == 1800 and body["samples"] == 2
        assert body["points"][1]["connections_created_per_min"] == 20.0
        assert body["points"][0]["databases"][0]["chunks"] == 10
        assert body["latest"]["totals"]["chunks"] == 100 and body["latest"]["databases"][0]["search_lag"]["lag"] == 50
        assert body["lease_holder"] == "quellex:api" and body["next_due_at"].startswith("2026-10-18T10:30")

    def test_db_stats_bucket_never_below_the_collector_interval(self, client: TestClient, with_db_store):
        with_db_store.state.db_stats_collector = _FakeCollector()
        body = client.get("/api/v1/telemetry/db-stats?hours=1").json()
        assert body["interval_seconds"] == 600 and body["bucket_seconds"] == 600
        assert with_db_store.state.telemetry_store.calls[0][3] == 600

    def test_db_stats_range_limit(self, client: TestClient, with_db_store):
        assert client.get("/api/v1/telemetry/db-stats?hours=9000").status_code == 200  # 375 days: allowed
        assert client.get("/api/v1/telemetry/db-stats?hours=9700").status_code == 422  # over 400 days

    def test_collect_without_collector_is_503(self, client: TestClient, with_db_store):
        assert client.post("/api/v1/telemetry/db-stats/collect").status_code == 503

    def test_collect_returns_the_forced_sample(self, client: TestClient, with_db_store):
        with_db_store.state.db_stats_collector = _FakeCollector()
        response = client.post("/api/v1/telemetry/db-stats/collect")
        assert response.status_code == 200
        assert response.json()["forced"] is True and response.json()["collector"] == "quellex:api"

    def test_overview_endpoint(self, client: TestClient, with_db_store, monkeypatch):
        seen = {}

        async def fake_overview(store, *, hours, online_within_s, cache, eta_lookup=None):
            seen.update(hours=hours, online=online_within_s, cache=cache, eta_lookup=eta_lookup)
            return TelemetryOverview(generated_at=T0, anomalies=OverviewAnomalies(hours=hours), chat=OverviewChat(hours=hours))

        monkeypatch.setattr(tov, "overview", fake_overview)
        body = client.get("/api/v1/telemetry/overview?hours=12").json()
        assert body["anomalies"]["hours"] == 12.0 and body["db"] is None
        assert seen["hours"] == 12.0 and seen["online"] >= 90.0 and isinstance(seen["cache"], tov.AnomalyCache)
        client.get("/api/v1/telemetry/overview")
        assert with_db_store.state.telemetry_overview_cache is seen["cache"]  # one cache per app

    def test_db_stats_disabled_history_is_503(self, client: TestClient, app):
        app.state.telemetry_store = None
        assert client.get("/api/v1/telemetry/db-stats").status_code == 503
        assert client.get("/api/v1/telemetry/overview").status_code == 503


def test_db_sample_model_ignores_storage_only_fields():
    sample = DbSample(**{**_db_sample(), "meta": {"source": "db-stats"}})
    assert sample.databases[0].search_indexes[0].status == "BUILDING"
    assert sample.databases[0].backup is not None and sample.databases[0].backup.newest_size_bytes == 5
