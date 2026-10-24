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
    DB_SAMPLES,
    HOST_SAMPLES,
    LEASES,
    SHARED_TENANT,
    TelemetryStore,
    choose_bucket_seconds,
    slot_due_at,
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
        assert set(created) == {HOST_SAMPLES, ACTIVITY_EVENTS, DB_SAMPLES}
        assert created[HOST_SAMPLES]["timeseries"] == {"timeField": "ts", "metaField": "meta", "granularity": "seconds"}
        assert created[HOST_SAMPLES]["expireAfterSeconds"] == 30 * 86400
        assert created[ACTIVITY_EVENTS]["expireAfterSeconds"] == 90 * 86400
        assert created[DB_SAMPLES]["timeseries"] == {"timeField": "ts", "metaField": "meta", "granularity": "minutes"}
        assert created[DB_SAMPLES]["expireAfterSeconds"] == 400 * 86400  # default when built without settings
        index_names = {kwargs["name"] for _, kwargs in db.collections[ACTIVITY_EVENTS].indexes}
        assert index_names == {"job_ts", "kind_ts"}

    @pytest.mark.asyncio
    async def test_db_samples_ttl_comes_from_settings(self):
        db = _Database()
        store = TelemetryStore(_Client(db), "recallhub_telemetry", host_samples_ttl_seconds=1, activity_events_ttl_seconds=1,
                               owns_client=False, db_samples_ttl_seconds=30 * 86400)
        await store.ensure_collections()
        assert dict(db.created)[DB_SAMPLES]["expireAfterSeconds"] == 30 * 86400

    @pytest.mark.asyncio
    async def test_updates_ttl_when_setting_changed(self):
        db = _Database(existing={
            HOST_SAMPLES: {"name": HOST_SAMPLES, "type": "timeseries", "options": {"expireAfterSeconds": 7 * 86400}},
            ACTIVITY_EVENTS: {"name": ACTIVITY_EVENTS, "type": "timeseries", "options": {"expireAfterSeconds": 90 * 86400}},
            DB_SAMPLES: {"name": DB_SAMPLES, "type": "timeseries", "options": {"expireAfterSeconds": 400 * 86400}},
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

    @pytest.mark.asyncio
    async def test_activity_current_docs_are_scoped(self):
        docs = [{"_id": "quellex:api", "tenant": "quellex"}, {"_id": "shared:worker", "tenant": SHARED_TENANT},
                {"_id": "recallhub:api", "tenant": "recallhub"}]

        class _Current:
            def find(self, _filter):
                return _Cursor(docs)

        class _Db:
            def __getitem__(self, name):
                assert name == "activity_current"
                return _Current()

        class _C:
            def __getitem__(self, _name):
                return _Db()

        scoped = TelemetryStore(_C(), "t", host_samples_ttl_seconds=1, activity_events_ttl_seconds=1, owns_client=False,
                                tenant_scope="quellex")
        assert [d["_id"] for d in await scoped.activity_current_docs()] == ["quellex:api", "shared:worker"]
        unscoped = TelemetryStore(_C(), "t", host_samples_ttl_seconds=1, activity_events_ttl_seconds=1, owns_client=False)
        assert len(await unscoped.activity_current_docs()) == 3


# ── leases ───────────────────────────────────────────────────────────────────


class _LeaseCollection:
    """In-memory stand-in for the lease collection: one document, conditional update semantics."""

    def __init__(self):
        self.doc: dict[str, Any] | None = None

    async def update_one(self, filter, update, upsert=False):
        if self.doc is None and upsert:
            self.doc = {"_id": filter["_id"], **update["$setOnInsert"]}

    async def find_one_and_update(self, filter, update):
        assert self.doc is not None
        due = self.doc.get("due_at")
        if due is not None and due <= filter["due_at"]["$lte"]:
            before = dict(self.doc)
            self.doc.update(update["$set"])
            return before
        return None

    async def find_one(self, _filter, projection=None):
        return None if self.doc is None else {k: v for k, v in self.doc.items() if k != "_id"}


class _LeaseDb:
    def __init__(self):
        self.leases = _LeaseCollection()

    def __getitem__(self, name):
        assert name == LEASES
        return self.leases


class _LeaseClient:
    def __init__(self):
        self.db = _LeaseDb()

    def __getitem__(self, _name):
        return self.db


class TestLeases:
    def test_slot_due_at(self):
        now = datetime(2026, 10, 24, 15, 7, 0, tzinfo=timezone.utc)
        assert slot_due_at(now, 1800) == datetime(2026, 10, 24, 15, 30, tzinfo=timezone.utc)
        assert slot_due_at(now, 60) == datetime(2026, 10, 24, 15, 8, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_first_caller_takes_a_fresh_lease_and_moves_it_to_the_next_slot(self):
        client = _LeaseClient()
        store = TelemetryStore(client, "t", host_samples_ttl_seconds=1, activity_events_ttl_seconds=1, owns_client=False)
        now = datetime(2026, 10, 24, 15, 7, 0, tzinfo=timezone.utc)

        assert await store.acquire_lease("db_stats", 1800, "quellex:api", now=now) is True
        assert await store.acquire_lease("db_stats", 1800, "shared:worker", now=now + timedelta(seconds=3)) is False
        status = await store.lease_status("db_stats")
        assert status == {"due_at": datetime(2026, 10, 24, 15, 30, tzinfo=timezone.utc), "holder": "quellex:api", "taken_at": now}

    @pytest.mark.asyncio
    async def test_lease_reopens_at_the_slot_boundary(self):
        client = _LeaseClient()
        store = TelemetryStore(client, "t", host_samples_ttl_seconds=1, activity_events_ttl_seconds=1, owns_client=False)
        now = datetime(2026, 10, 24, 15, 7, 0, tzinfo=timezone.utc)
        await store.acquire_lease("db_stats", 1800, "a", now=now)

        assert await store.acquire_lease("db_stats", 1800, "b", now=datetime(2026, 10, 24, 15, 29, 59, tzinfo=timezone.utc)) is False
        assert await store.acquire_lease("db_stats", 1800, "b", now=datetime(2026, 10, 24, 15, 30, 2, tzinfo=timezone.utc)) is True
        assert (await store.lease_status("db_stats"))["holder"] == "b"
        assert (await store.lease_status("db_stats"))["due_at"] == datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_lease_status_before_any_take(self):
        store = TelemetryStore(_LeaseClient(), "t", host_samples_ttl_seconds=1, activity_events_ttl_seconds=1, owns_client=False)
        assert await store.lease_status("db_stats") is None
