"""Unit tests for the telemetry store's schema management and bucket selection.

The aggregation pipelines run against a real MongoDB in
``backend/tests/integration/test_telemetry_store_mongo.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.services.telemetry_store import (
    ACTIVITY_EVENTS,
    HOST_SAMPLES,
    SHARED_TENANT,
    TelemetryStore,
    choose_bucket_seconds,
    tenant_visible,
)


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]):
        self._docs = list(docs)

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Collection:
    def __init__(self):
        self.indexes: list[tuple[Any, dict[str, Any]]] = []

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))
        return kwargs.get("name", "idx")


class _Database:
    """Fake AsyncDatabase recording collection creation and collMod calls."""

    def __init__(self, existing: dict[str, dict[str, Any]] | None = None):
        self.existing = dict(existing or {})
        self.created: list[tuple[str, dict[str, Any]]] = []
        self.commands: list[tuple[Any, ...]] = []
        self.collections: dict[str, _Collection] = {}

    async def list_collections(self, filter=None):
        name = (filter or {}).get("name")
        docs = [info for n, info in self.existing.items() if name is None or n == name]
        return _Cursor(docs)

    async def create_collection(self, name, **kwargs):
        self.created.append((name, kwargs))
        self.existing[name] = {"name": name, "type": "timeseries", "options": {"expireAfterSeconds": kwargs.get("expireAfterSeconds")}}

    async def command(self, *args, **kwargs):
        self.commands.append((*args, kwargs))
        return {"ok": 1}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())


class _Client:
    def __init__(self, db: _Database):
        self.db = db

    def __getitem__(self, _name):
        return self.db


def _store(db: _Database) -> TelemetryStore:
    return TelemetryStore(_Client(db), "recallhub_telemetry", host_samples_ttl_seconds=30 * 86400,
                          activity_events_ttl_seconds=90 * 86400, owns_client=False)


class TestChooseBucketSeconds:
    @pytest.mark.parametrize(
        ("span", "expected"),
        [
            (timedelta(minutes=10), 5),        # floor = agent interval
            (timedelta(hours=1), 10),
            (timedelta(hours=6), 60),
            (timedelta(days=1), 300),
            (timedelta(days=7), 1800),
            (timedelta(days=30), 7200),
            (timedelta(days=3650), 86400),     # capped at one day
        ],
    )
    def test_snaps_to_nice_buckets(self, span, expected):
        end = datetime(2026, 10, 18, tzinfo=timezone.utc)
        assert choose_bucket_seconds(end - span, end) == expected

    def test_respects_minimum(self):
        end = datetime(2026, 10, 18, tzinfo=timezone.utc)
        assert choose_bucket_seconds(end - timedelta(minutes=1), end, minimum=30) == 30


class TestEnsureCollections:
    @pytest.mark.asyncio
    async def test_creates_both_time_series_collections_with_ttl(self):
        db = _Database()
        await _store(db).ensure_collections()

        created = dict(db.created)
        assert set(created) == {HOST_SAMPLES, ACTIVITY_EVENTS}
        assert created[HOST_SAMPLES]["timeseries"] == {"timeField": "ts", "metaField": "meta", "granularity": "seconds"}
        assert created[HOST_SAMPLES]["expireAfterSeconds"] == 30 * 86400
        assert created[ACTIVITY_EVENTS]["expireAfterSeconds"] == 90 * 86400
        index_names = {kwargs["name"] for _, kwargs in db.collections[ACTIVITY_EVENTS].indexes}
        assert index_names == {"job_ts", "kind_ts"}

    @pytest.mark.asyncio
    async def test_updates_ttl_when_setting_changed(self):
        db = _Database(existing={
            HOST_SAMPLES: {"name": HOST_SAMPLES, "type": "timeseries", "options": {"expireAfterSeconds": 7 * 86400}},
            ACTIVITY_EVENTS: {"name": ACTIVITY_EVENTS, "type": "timeseries", "options": {"expireAfterSeconds": 90 * 86400}},
        })
        await _store(db).ensure_collections()

        assert db.created == []
        assert db.commands == [("collMod", HOST_SAMPLES, {"expireAfterSeconds": 30 * 86400})]

    @pytest.mark.asyncio
    async def test_leaves_non_timeseries_collection_untouched(self, caplog):
        db = _Database(existing={HOST_SAMPLES: {"name": HOST_SAMPLES, "type": "collection", "options": {}}})
        await _store(db).ensure_collections()

        assert all(name != HOST_SAMPLES for name, _ in db.created)
        assert not any(cmd[0] == "collMod" and cmd[1] == HOST_SAMPLES for cmd in db.commands)
        assert "not a time-series collection" in caplog.text


class _QueryCursor(_Cursor):
    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, _n):
        return self

    async def to_list(self, length=None):
        return list(self._docs)


class _QueryCollection:
    """Records the filters and pipelines the store sends to ``activity_events``."""

    def __init__(self):
        self.queries: list[dict[str, Any]] = []
        self.pipelines: list[list[dict[str, Any]]] = []

    def find(self, query, projection=None):
        self.queries.append(query)
        return _QueryCursor([])

    async def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return _QueryCursor([])


def _scoped_store(scope: str | None) -> tuple[TelemetryStore, _QueryCollection]:
    db = _Database()
    events = _QueryCollection()
    db.collections[ACTIVITY_EVENTS] = events
    store = TelemetryStore(_Client(db), "recallhub_telemetry", host_samples_ttl_seconds=86400,
                           activity_events_ttl_seconds=86400, owns_client=False, tenant_scope=scope)
    return store, events


class TestTenantScope:
    """Activity holds tenant data; a scoped store reads only its tenant's and shared activity."""

    @pytest.mark.asyncio
    async def test_unscoped_store_reads_every_tenant(self):
        store, events = _scoped_store(None)
        await store.events(kinds=["file.end"])
        await store.aggregate_events([{"$match": {"meta.kind": "file.end"}}])

        assert "meta.tenant" not in events.queries[0]
        assert events.pipelines[0] == [{"$match": {"meta.kind": "file.end"}}]

    @pytest.mark.asyncio
    async def test_scoped_find_cannot_be_widened_by_a_caller_filter(self):
        store, events = _scoped_store("quellex")
        await store.events(job_id="j1", extra_filter={"meta.tenant": "recallhub"})

        assert events.queries[0]["meta.tenant"] == {"$in": ["quellex", SHARED_TENANT]}
        assert events.queries[0]["meta.job_id"] == "j1"

    @pytest.mark.asyncio
    async def test_scoped_aggregations_start_with_the_tenant_match(self):
        store, events = _scoped_store("quellex")
        now = datetime.now(timezone.utc)
        await store.aggregate_events([{"$group": {"_id": None}}])
        await store.recent_jobs(now - timedelta(hours=1), now)

        scope = {"$match": {"meta.tenant": {"$in": ["quellex", SHARED_TENANT]}}}
        assert events.pipelines[0] == [scope, {"$group": {"_id": None}}]
        assert events.pipelines[1][0] == scope  # job lists and reports inherit the scope

    @pytest.mark.parametrize("tenant, scope, visible", [
        ("quellex", "quellex", True),
        (SHARED_TENANT, "quellex", True),
        ("recallhub", "quellex", False),
        (None, "quellex", False),
        ("recallhub", None, True),
    ])
    def test_tenant_visible(self, tenant, scope, visible):
        assert tenant_visible(tenant, scope) is visible
