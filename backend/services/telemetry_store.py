"""MongoDB store for host-agent samples and activity (checkpoint) events.

All collections live in ``settings.telemetry_database`` (default
``recallhub_telemetry``) — independent of the active profile database, so the
history survives profile switches and is shared by every tenant on the host:

* ``host_samples`` — **time-series** (timeField ``ts``, metaField ``meta``,
  granularity ``seconds``), written every few seconds by the Windows host agent
  (``installer/host-agent``). TTL ``host_samples_ttl_days``. The document layout
  is the contract described in docs/12-TELEMETRY_SYSTEM.md ("host_samples").
* ``activity_events`` — **time-series**, checkpoint events emitted by the API and
  the ingestion worker (job phases/status, per-file start/end and step timings,
  chat requests). ``meta`` carries ``kind``, ``source``, ``tenant`` and
  ``job_id`` so events of one job share buckets. TTL ``activity_events_ttl_days``.
* ``activity_current`` — one small regular document per ``<tenant>:<source>``
  describing what is running right now; the host agent stamps it onto each
  sample so samples are self-describing. Refreshed on a heartbeat while idle,
  so ``updated_at`` doubles as "last seen" for the API and the worker.
* ``db_samples`` — **time-series** (granularity ``minutes``), one document per
  :mod:`backend.services.db_stats` run (every ``db_stats_interval_minutes``):
  MongoDB server, oplog and per-database statistics. TTL ``db_samples_ttl_seconds``.
* ``telemetry_leases`` — one regular document per periodic task (``db_stats``)
  so that exactly one of the API and worker processes on a host runs each slot.

Host samples describe the machine and are visible to every tenant. Activity is
tenant data (it holds file paths): ``meta.tenant`` is the tenant the job or chat
belongs to, and :data:`SHARED_TENANT` marks activity of shared infrastructure
that no tenant owns (the ingestion worker while idle, or a job queued without a
tenant). A store built with ``tenant_scope`` reads only that tenant's and shared
activity; the API scopes to ``settings.tenant_id``.

Every timestamp written or read here is timezone-aware UTC. The store uses
``pymongo.AsyncMongoClient`` (repo rule: no new motor usage) with its own client,
so it never depends on the profile-switching ``DatabaseManager``.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

from pymongo import AsyncMongoClient
from pymongo.errors import CollectionInvalid, OperationFailure, PyMongoError

logger = logging.getLogger(__name__)

__all__ = [
    "HOST_SAMPLES",
    "ACTIVITY_EVENTS",
    "ACTIVITY_CURRENT",
    "DB_SAMPLES",
    "LEASES",
    "SHARED_TENANT",
    "TelemetryStore",
    "choose_bucket_seconds",
    "slot_due_at",
    "tenant_visible",
]

HOST_SAMPLES = "host_samples"
ACTIVITY_EVENTS = "activity_events"
ACTIVITY_CURRENT = "activity_current"
DB_SAMPLES = "db_samples"
LEASES = "telemetry_leases"

#: Default retention of ``db_samples`` when a store is built without settings (CLI, tests).
DEFAULT_DB_SAMPLES_TTL_SECONDS = 400 * 86400

#: ``meta.tenant`` of activity that belongs to no single tenant (visible to all of them).
SHARED_TENANT = "shared"


def tenant_visible(tenant: Optional[str], scope: Optional[str]) -> bool:
    """Whether activity labelled ``tenant`` may be shown to a reader scoped to ``scope``.

    Args:
        tenant: The activity's tenant label (``None`` when missing).
        scope: The reader's tenant, or ``None`` for an unscoped reader (operator tools).

    Returns:
        True for an unscoped reader, for the reader's own tenant and for shared activity.
    """
    return scope is None or tenant in (scope, SHARED_TENANT)

def _timeseries_options(granularity: str = "seconds") -> dict[str, str]:
    """Time-series collection options shared by every telemetry collection."""
    return {"timeField": "ts", "metaField": "meta", "granularity": granularity}


def slot_due_at(now: datetime, interval_seconds: int) -> datetime:
    """Start of the next clock-aligned slot after ``now`` (e.g. the next :00 or :30 for 30 min).

    Args:
        now: Current time (aware UTC).
        interval_seconds: Slot length; slots start at multiples of it since the Unix epoch.

    Returns:
        The first slot boundary strictly after ``now``.
    """
    step = max(int(interval_seconds), 1)
    epoch = int(now.timestamp())
    return datetime.fromtimestamp((epoch // step + 1) * step, tz=timezone.utc)

#: Bucket sizes (seconds) the history API snaps to, smallest first.
_NICE_BUCKETS: tuple[int, ...] = (5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 21600, 43200, 86400)

#: Error codes meaning "collection already exists" (race between API, worker and agent).
_NAMESPACE_EXISTS = 48

#: ``$group`` accumulators shared by the downsampled series and the range summary.
_HOST_ACCUMULATORS: dict[str, Any] = {
    "samples": {"$sum": 1},
    "cpu_pct": {"$avg": "$cpu.pct"},
    "cpu_pct_max": {"$max": "$cpu.pct"},
    "mem_pct": {"$avg": "$mem.pct"},
    "mem_pct_max": {"$max": "$mem.pct"},
    "mem_used_bytes": {"$avg": "$mem.used_bytes"},
    "mem_total_bytes": {"$max": "$mem.total_bytes"},
    "gpu_util_pct": {"$avg": "$gpu.util_pct"},
    "gpu_util_pct_max": {"$max": "$gpu.util_pct"},
    "vram_used_bytes": {"$avg": "$gpu.vram_used_bytes"},
    "vram_total_bytes": {"$max": "$gpu.vram_total_bytes"},
    "gpu_temp_c": {"$avg": "$temp.gpu_c"},
    "gpu_temp_c_max": {"$max": "$temp.gpu_c"},
    "cpu_temp_c": {"$avg": "$temp.cpu_pkg_c"},
    "cpu_temp_c_max": {"$max": "$temp.cpu_pkg_c"},
    "gpu_power_w": {"$avg": "$power.gpu_w"},
    "gpu_power_w_max": {"$max": "$power.gpu_w"},
    "cpu_power_w": {"$avg": "$power.cpu_pkg_w"},
    "cpu_power_w_max": {"$max": "$power.cpu_pkg_w"},
    "disk_read_bps": {"$avg": "$disk_io.read_bps"},
    "disk_write_bps": {"$avg": "$disk_io.write_bps"},
    "net_rx_bps": {"$avg": "$net.rx_bps"},
    "net_tx_bps": {"$avg": "$net.tx_bps"},
    "backend_ms_max": {"$max": {"$max": "$health.ms"}},
    "backend_down": {"$sum": {"$cond": [{"$in": [False, {"$ifNull": ["$health.ok", []]}]}, 1, 0]}},
    "gpu_throttled": {"$sum": {"$cond": [{"$gt": [{"$size": {"$ifNull": ["$gpu.reasons", []]}}, 0]}, 1, 0]}},
}


def choose_bucket_seconds(start: datetime, end: datetime, *, target_points: int = 360, minimum: int = 5) -> int:
    """Pick a "nice" aggregation bucket so a range renders as roughly ``target_points`` points.

    Args:
        start: Range start (aware UTC).
        end: Range end (aware UTC).
        target_points: Desired number of buckets across the range.
        minimum: Smallest allowed bucket (the agent's sampling interval).

    Returns:
        Bucket size in seconds from a fixed ladder (5 s ... 1 day).
    """
    span = max((end - start).total_seconds(), 1.0)
    wanted = max(minimum, math.ceil(span / max(target_points, 1)))
    for size in _NICE_BUCKETS:
        if size >= wanted:
            return size
    return _NICE_BUCKETS[-1]


def utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(timezone.utc)


class TelemetryStore:
    """Async access to the telemetry database (host samples + activity events).

    Args:
        client: A ``pymongo.AsyncMongoClient`` (created ``tz_aware=True``).
        database: Telemetry database name.
        host_samples_ttl_seconds: TTL applied to ``host_samples``.
        activity_events_ttl_seconds: TTL applied to ``activity_events``.
        owns_client: Close the client in :meth:`close` (True when built by
            :meth:`from_settings`).
        tenant_scope: When set, activity reads (:meth:`events`, :meth:`aggregate_events`,
            :meth:`recent_jobs`, :meth:`activity_current_docs`) return only this tenant's and
            shared activity. Writes, host-sample and db-sample reads are never scoped.
        db_samples_ttl_seconds: TTL applied to ``db_samples``.
    """

    def __init__(
        self,
        client: Any,
        database: str,
        *,
        host_samples_ttl_seconds: int,
        activity_events_ttl_seconds: int,
        owns_client: bool = True,
        tenant_scope: Optional[str] = None,
        db_samples_ttl_seconds: int = DEFAULT_DB_SAMPLES_TTL_SECONDS,
    ) -> None:
        self._client = client
        self._database = database
        self._host_ttl = int(host_samples_ttl_seconds)
        self._event_ttl = int(activity_events_ttl_seconds)
        self._db_sample_ttl = int(db_samples_ttl_seconds)
        self._owns_client = owns_client
        self._tenant_scope = tenant_scope or None
        self._ready = False

    @classmethod
    def from_settings(cls, settings: Any, *, tenant_scope: Optional[str] = None) -> "TelemetryStore":
        """Build a store with its own client from ``BackendSettings``.

        Args:
            settings: ``BackendSettings``.
            tenant_scope: Tenant whose activity this store reads (``None``: every tenant).
        """
        client = AsyncMongoClient(
            settings.mongodb_uri,
            tz_aware=True,
            tzinfo=timezone.utc,
            serverSelectionTimeoutMS=5000,
            appname="recallhub-telemetry",
        )
        return cls(
            client,
            settings.telemetry_database,
            host_samples_ttl_seconds=settings.host_samples_ttl_days * 86400,
            activity_events_ttl_seconds=settings.activity_events_ttl_days * 86400,
            tenant_scope=tenant_scope,
            db_samples_ttl_seconds=int(getattr(settings, "db_stats_ttl_days", 400)) * 86400,
        )

    @property
    def tenant_scope(self) -> Optional[str]:
        """Tenant whose activity this store reads, or ``None`` for every tenant."""
        return self._tenant_scope

    @property
    def client(self) -> Any:
        """The underlying ``AsyncMongoClient`` (the db-stats collector reads other databases through it)."""
        return self._client

    def tenant_filter(self) -> dict[str, Any]:
        """``activity_events`` filter limiting reads to the scoped tenant (empty when unscoped)."""
        if self._tenant_scope is None:
            return {}
        return {"meta.tenant": {"$in": [self._tenant_scope, SHARED_TENANT]}}

    @property
    def db(self) -> Any:
        """The telemetry database handle."""
        return self._client[self._database]

    @property
    def database_name(self) -> str:
        """Name of the telemetry database."""
        return self._database

    async def close(self) -> None:
        """Close the underlying client when this store owns it."""
        if self._owns_client:
            try:
                await self._client.close()
            except Exception as exc:  # noqa: BLE001 - shutdown must not fail
                logger.debug("telemetry client close failed: %s", exc)

    # ── schema ───────────────────────────────────────────────────────────

    async def ensure_collections(self) -> None:
        """Create the time-series collections and indexes idempotently.

        Safe to call from the API, the worker and concurrently with the host
        agent (which creates ``host_samples`` itself when it starts first).
        Errors are logged, never raised — telemetry must not block startup.
        """
        if self._ready:
            return
        try:
            await self._ensure_timeseries(HOST_SAMPLES, self._host_ttl)
            await self._ensure_timeseries(ACTIVITY_EVENTS, self._event_ttl)
            await self._ensure_timeseries(DB_SAMPLES, self._db_sample_ttl, granularity="minutes")
            events = self.db[ACTIVITY_EVENTS]
            await events.create_index([("meta.job_id", 1), ("ts", 1)], name="job_ts")
            await events.create_index([("meta.kind", 1), ("ts", 1)], name="kind_ts")
            self._ready = True
        except PyMongoError as exc:
            logger.warning("telemetry collections not ready (non-fatal): %s", exc)

    async def _ensure_timeseries(self, name: str, ttl_seconds: int, *, granularity: str = "seconds") -> None:
        info = await self._collection_info(name)
        if info is None:
            try:
                await self.db.create_collection(name, timeseries=_timeseries_options(granularity),
                                                expireAfterSeconds=ttl_seconds)
                logger.info("created time-series collection %s.%s (ttl %ss)", self._database, name, ttl_seconds)
                return
            except (CollectionInvalid, OperationFailure) as exc:
                if getattr(exc, "code", None) not in (None, _NAMESPACE_EXISTS) and "already exists" not in str(exc):
                    raise
                info = await self._collection_info(name)
        if info is None:
            return
        if info.get("type") != "timeseries":
            logger.warning(
                "%s.%s exists but is not a time-series collection; leaving it untouched "
                "(drop it manually to let the store recreate it)", self._database, name,
            )
            return
        current = (info.get("options") or {}).get("expireAfterSeconds")
        if current != ttl_seconds:
            await self.db.command("collMod", name, expireAfterSeconds=ttl_seconds)
            logger.info("set %s.%s TTL to %ss (was %s)", self._database, name, ttl_seconds, current)

    async def _collection_info(self, name: str) -> Optional[dict[str, Any]]:
        cursor = await self.db.list_collections(filter={"name": name})
        async for info in cursor:
            return dict(info)
        return None

    # ── host samples ─────────────────────────────────────────────────────

    async def latest_host_sample(self, *, max_age_seconds: float = 300.0) -> Optional[dict[str, Any]]:
        """Most recent host-agent sample no older than ``max_age_seconds`` (None otherwise)."""
        since = utcnow() - timedelta(seconds=max_age_seconds)
        return await self.db[HOST_SAMPLES].find_one({"ts": {"$gte": since}}, sort=[("ts", -1)], projection={"_id": 0})

    async def host_samples_between(
        self,
        start: datetime,
        end: datetime,
        *,
        extra_filter: Optional[dict[str, Any]] = None,
        projection: Optional[dict[str, Any]] = None,
        limit: int = 20000,
    ) -> list[dict[str, Any]]:
        """Raw host samples in ``[start, end)`` ordered by time (capped at ``limit``)."""
        query: dict[str, Any] = {"ts": {"$gte": start, "$lt": end}}
        if extra_filter:
            query.update(extra_filter)
        proj = {"_id": 0, **(projection or {})} if projection else {"_id": 0}
        cursor = self.db[HOST_SAMPLES].find(query, projection=proj).sort("ts", 1).limit(limit)
        return [doc async for doc in cursor]

    async def host_series(self, start: datetime, end: datetime, bucket_seconds: int) -> list[dict[str, Any]]:
        """Downsampled host metrics: one row per bucket with averages and maxima."""
        pipeline: list[dict[str, Any]] = [
            {"$match": {"ts": {"$gte": start, "$lt": end}}},
            {"$group": {
                "_id": {"$dateTrunc": {"date": "$ts", "unit": "second", "binSize": bucket_seconds}},
                **_HOST_ACCUMULATORS,
            }},
            {"$sort": {"_id": 1}},
        ]
        rows = await (await self.db[HOST_SAMPLES].aggregate(pipeline)).to_list(length=None)
        return [{"t": row.pop("_id"), **row} for row in rows]

    async def container_series(self, start: datetime, end: datetime, bucket_seconds: int) -> dict[str, list[dict[str, Any]]]:
        """Per-container CPU (cores) and memory, downsampled, keyed by container name."""
        pipeline: list[dict[str, Any]] = [
            {"$match": {"ts": {"$gte": start, "$lt": end}}},
            {"$project": {"ts": 1, "containers": 1}},
            {"$unwind": "$containers"},
            {"$group": {
                "_id": {
                    "t": {"$dateTrunc": {"date": "$ts", "unit": "second", "binSize": bucket_seconds}},
                    "name": "$containers.name",
                },
                "cpu_cores": {"$avg": "$containers.cpu_cores"},
                "cpu_cores_max": {"$max": "$containers.cpu_cores"},
                "mem_bytes": {"$avg": "$containers.mem_bytes"},
                "mem_bytes_max": {"$max": "$containers.mem_bytes"},
            }},
            {"$sort": {"_id.t": 1}},
        ]
        rows = await (await self.db[HOST_SAMPLES].aggregate(pipeline)).to_list(length=None)
        out: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key = row.pop("_id")
            out.setdefault(key["name"], []).append({"t": key["t"], **row})
        return out

    async def host_summary(self, start: datetime, end: datetime) -> dict[str, Any]:
        """Averages and maxima of the key host metrics over ``[start, end)`` (empty dict when no samples)."""
        pipeline: list[dict[str, Any]] = [
            {"$match": {"ts": {"$gte": start, "$lt": end}}},
            {"$group": {"_id": None, **_HOST_ACCUMULATORS}},
        ]
        rows = await (await self.db[HOST_SAMPLES].aggregate(pipeline)).to_list(length=None)
        if not rows:
            return {}
        row = dict(rows[0])
        row.pop("_id", None)
        return row

    async def container_summary(self, start: datetime, end: datetime) -> dict[str, dict[str, Any]]:
        """Per container over ``[start, end)``: average/peak CPU cores and peak memory."""
        pipeline: list[dict[str, Any]] = [
            {"$match": {"ts": {"$gte": start, "$lt": end}}},
            {"$project": {"containers": 1}},
            {"$unwind": "$containers"},
            {"$group": {
                "_id": "$containers.name",
                "cpu_cores": {"$avg": "$containers.cpu_cores"},
                "cpu_cores_max": {"$max": "$containers.cpu_cores"},
                "mem_bytes_max": {"$max": "$containers.mem_bytes"},
            }},
        ]
        rows = await (await self.db[HOST_SAMPLES].aggregate(pipeline)).to_list(length=None)
        return {row.pop("_id"): row for row in rows if row.get("_id")}

    async def count_host_samples(self, start: datetime, end: datetime) -> int:
        """Number of host-agent samples in ``[start, end)``."""
        return await self.db[HOST_SAMPLES].count_documents({"ts": {"$gte": start, "$lt": end}})

    # ── activity events ──────────────────────────────────────────────────

    async def insert_events(self, docs: Sequence[dict[str, Any]]) -> int:
        """Insert a batch of events (unordered); returns how many were sent."""
        if not docs:
            return 0
        await self.db[ACTIVITY_EVENTS].insert_many(list(docs), ordered=False)
        return len(docs)

    async def upsert_activity_current(self, key: str, doc: dict[str, Any]) -> None:
        """Replace the "what is running now" document for ``key`` (``<tenant>:<source>``)."""
        await self.db[ACTIVITY_CURRENT].replace_one({"_id": key}, {"_id": key, **doc}, upsert=True)

    async def activity_current_docs(self) -> list[dict[str, Any]]:
        """Every process's live-state document visible to this store's tenant scope."""
        cursor = self.db[ACTIVITY_CURRENT].find({})
        docs = [doc async for doc in cursor]
        return [d for d in docs if tenant_visible(d.get("tenant"), self._tenant_scope)]

    # ── db samples (MongoDB statistics) ──────────────────────────────────

    async def insert_db_sample(self, doc: dict[str, Any]) -> None:
        """Store one db-stats sample (layout in :mod:`backend.services.db_stats`)."""
        await self.db[DB_SAMPLES].insert_one(dict(doc))

    async def latest_db_sample(self, *, max_age_seconds: Optional[float] = None) -> Optional[dict[str, Any]]:
        """The most recent db-stats sample (optionally no older than ``max_age_seconds``)."""
        query: dict[str, Any] = {}
        if max_age_seconds is not None:
            query["ts"] = {"$gte": utcnow() - timedelta(seconds=max_age_seconds)}
        return await self.db[DB_SAMPLES].find_one(query, sort=[("ts", -1)], projection={"_id": 0})

    async def db_samples_between(self, start: datetime, end: datetime, *, limit: int = 5000) -> list[dict[str, Any]]:
        """Raw db-stats samples in ``[start, end)`` ordered by time."""
        cursor = self.db[DB_SAMPLES].find({"ts": {"$gte": start, "$lt": end}}, projection={"_id": 0}).sort("ts", 1).limit(limit)
        return [doc async for doc in cursor]

    async def db_series(self, start: datetime, end: datetime, bucket_seconds: int) -> list[dict[str, Any]]:
        """Compact db-stats points in ``[start, end)``: the last sample of each bucket, charting fields only.

        The full sample (per-chunk distributions, search indexes, backups) stays out of the
        series; :meth:`latest_db_sample` serves it once.
        """
        pipeline: list[dict[str, Any]] = [
            {"$match": {"ts": {"$gte": start, "$lt": end}}},
            {"$sort": {"ts": 1}},
            {"$project": {
                "_id": 0,
                "ts": 1,
                "connections_current": "$server.connections.current",
                "connections_total_created": "$server.connections.total_created",
                "wt_cache_used_bytes": "$server.wt_cache.used_bytes",
                "wt_cache_max_bytes": "$server.wt_cache.max_bytes",
                "ops_insert": "$server.opcounters.insert",
                "ops_query": "$server.opcounters.query",
                "oplog_bytes": "$oplog.storage_bytes",
                "oplog_max_bytes": "$oplog.max_bytes",
                "oplog_window_s": "$oplog.window_s",
                "fs_used_bytes": "$fs.used_bytes",
                "fs_total_bytes": "$fs.total_bytes",
                "size_on_disk_bytes": "$totals.size_on_disk_bytes",
                "data_bytes": "$totals.data_bytes",
                "storage_bytes": "$totals.storage_bytes",
                "index_bytes": "$totals.index_bytes",
                "chunks": "$totals.chunks",
                "documents": "$totals.documents",
                "databases": {"$map": {"input": {"$ifNull": ["$databases", []]}, "as": "d", "in": {
                    "name": "$$d.name",
                    "documents": "$$d.documents.count",
                    "chunks": "$$d.chunks.count",
                    "storage_bytes": "$$d.storage_bytes",
                    "data_bytes": "$$d.data_bytes",
                    "index_bytes": "$$d.index_bytes",
                    "embedding_coverage_pct": "$$d.sample.embedding_coverage_pct",
                    "search_lag": "$$d.search_lag.lag",
                }}},
            }},
            {"$group": {
                "_id": {"$dateTrunc": {"date": "$ts", "unit": "second", "binSize": bucket_seconds}},
                "point": {"$last": "$$ROOT"},
            }},
            {"$replaceRoot": {"newRoot": "$point"}},
            {"$sort": {"ts": 1}},
        ]
        rows = await (await self.db[DB_SAMPLES].aggregate(pipeline)).to_list(length=None)
        return [{"t": row.pop("ts"), **row} for row in rows]

    # ── leases (one process per host runs a periodic task) ───────────────

    async def acquire_lease(self, name: str, interval_seconds: int, holder: str, *, now: Optional[datetime] = None) -> bool:
        """Take the lease ``name`` for the slot that is due, if any.

        The lease document holds ``due_at``, the start of the next slot. Whoever first moves a
        due lease forward (an atomic conditional update) runs the slot; everyone else sees a
        future ``due_at`` and waits. Slots are clock-aligned (:func:`slot_due_at`), so samples
        land on the same minutes whichever process takes them.

        Args:
            name: Lease id (e.g. ``"db_stats"``).
            interval_seconds: Slot length.
            holder: Who is asking (``<tenant>:<source>``), recorded for diagnostics.
            now: Current time (aware UTC, defaults to now).

        Returns:
            True when this caller owns the slot and should run the task now.
        """
        moment = now or utcnow()
        leases = self.db[LEASES]
        await leases.update_one({"_id": name}, {"$setOnInsert": {"due_at": datetime(1970, 1, 1, tzinfo=timezone.utc)}},
                                upsert=True)
        taken = await leases.find_one_and_update(
            {"_id": name, "due_at": {"$lte": moment}},
            {"$set": {"due_at": slot_due_at(moment, interval_seconds), "holder": holder, "taken_at": moment}},
        )
        return taken is not None

    async def lease_status(self, name: str) -> Optional[dict[str, Any]]:
        """The lease document (``due_at``, ``holder``, ``taken_at``) or None when never taken."""
        return await self.db[LEASES].find_one({"_id": name}, projection={"_id": 0})

    async def events(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        kinds: Optional[Iterable[str]] = None,
        job_id: Optional[str] = None,
        extra_filter: Optional[dict[str, Any]] = None,
        limit: int = 5000,
        newest_first: bool = False,
    ) -> list[dict[str, Any]]:
        """Events filtered by time range, kinds and job id (and the tenant scope), ordered by time."""
        query: dict[str, Any] = {}
        if start is not None or end is not None:
            query["ts"] = {}
            if start is not None:
                query["ts"]["$gte"] = start
            if end is not None:
                query["ts"]["$lt"] = end
        if kinds:
            query["meta.kind"] = {"$in": list(kinds)}
        if job_id is not None:
            query["meta.job_id"] = job_id
        if extra_filter:
            query.update(extra_filter)
        query.update(self.tenant_filter())  # last, so a caller's filter cannot widen the scope
        cursor = self.db[ACTIVITY_EVENTS].find(query, projection={"_id": 0}).sort("ts", -1 if newest_first else 1).limit(limit)
        return [doc async for doc in cursor]

    async def aggregate_events(self, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Run an aggregation over ``activity_events`` (prefixed with the tenant scope's ``$match``)."""
        scope = self.tenant_filter()
        stages = [{"$match": scope}, *pipeline] if scope else pipeline
        return await (await self.db[ACTIVITY_EVENTS].aggregate(stages)).to_list(length=None)

    async def aggregate_samples(self, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Run an aggregation over ``host_samples``."""
        return await (await self.db[HOST_SAMPLES].aggregate(pipeline)).to_list(length=None)

    async def recent_jobs(self, start: datetime, end: datetime, limit: int = 50) -> list[dict[str, Any]]:
        """Ingestion jobs seen in ``[start, end)`` with their first/last event, files and errors."""
        pipeline: list[dict[str, Any]] = [
            {"$match": {"ts": {"$gte": start, "$lt": end}, "meta.job_id": {"$ne": None},
                        "meta.kind": {"$in": ["job.phase", "job.status", "file.end"]}}},
            {"$group": {
                "_id": "$meta.job_id",
                "tenant": {"$last": "$meta.tenant"},
                "source": {"$last": "$meta.source"},
                "first_ts": {"$min": "$ts"},
                "last_ts": {"$max": "$ts"},
                "files": {"$sum": {"$cond": [{"$eq": ["$meta.kind", "file.end"]}, 1, 0]}},
                "failed": {"$sum": {"$cond": [{"$and": [
                    {"$eq": ["$meta.kind", "file.end"]}, {"$eq": ["$outcome", "error"]}]}, 1, 0]}},
                "statuses": {"$push": {"$cond": [{"$eq": ["$meta.kind", "job.status"]}, {"ts": "$ts", "status": "$status"}, "$$REMOVE"]}},
            }},
            {"$sort": {"last_ts": -1}},
            {"$limit": limit},
        ]
        rows = await self.aggregate_events(pipeline)
        out: list[dict[str, Any]] = []
        for row in rows:
            statuses = sorted(row.pop("statuses") or [], key=lambda s: s["ts"])
            out.append({"job_id": row.pop("_id"), "status": statuses[-1].get("status") if statuses else None, **row})
        return out
