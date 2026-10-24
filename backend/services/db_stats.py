"""MongoDB statistics sampler: one ``db_samples`` document per interval, cheaply.

What a sample holds (layout mirrored by :class:`backend.models.telemetry_history.DbSample`):

* ``server`` — ``serverStatus`` extract: version, uptime, connections (``total_created`` lets the
  history show connection churn), WiredTiger cache use, opcounters, resident memory.
* ``oplog`` — size, cap and time window of ``local.oplog.rs``. On a single-node replica set
  (Atlas Local) the oplog is the largest thing on disk, so "database size" is reported apart
  from it.
* ``fs`` — the filesystem under the data directory (``dbStats.fsUsedSize``/``fsTotalSize``).
* ``databases`` — per application database (every database except ``admin``, ``local``,
  ``config`` and the telemetry database): ``dbStats`` sizes, ``$collStats`` of the documents and
  chunks collections, a random sample of chunks (embedding coverage, vector lengths, embedding
  models, chunking method, contextual share, token percentiles), a random sample of documents
  (share without chunks), the Atlas Search / Vector Search indexes with their state, the text
  index's lag behind the collection (``$searchMeta`` count vs ``$collStats`` count), ingestion
  jobs by status, the newest backup, failed-document and file-registry counts.
* ``totals`` — sums over those databases; ``telemetry`` — the telemetry database's own size.

Cost: metadata commands, estimated counts and two ``$sample`` stages whose projection strips the
embeddings before they leave the server. A run takes one to two seconds and moves well under a
megabyte. Nothing here scans a chunks collection.

Scheduling: a :class:`DbStatsCollector` runs in every API backend and in the ingestion worker.
Slots are clock-aligned (``db_stats_interval_minutes``) and a lease in the telemetry database
(:meth:`TelemetryStore.acquire_lease`) makes exactly one process sample each slot, so a host with
two tenants and a worker still writes one sample per slot, and the samples keep coming while any
one of them is up.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from backend.services.telemetry_store import TelemetryStore, slot_due_at

logger = logging.getLogger(__name__)

__all__ = [
    "LEASE_NAME",
    "SCHEMA_VERSION",
    "DbStatsCollector",
    "collect_db_sample",
    "oplog_summary",
    "search_index_entries",
    "server_summary",
    "summarize_chunk_rows",
    "summarize_document_rows",
    "sum_totals",
]

LEASE_NAME = "db_stats"
SCHEMA_VERSION = 1

#: Databases that are not application data.
SYSTEM_DATABASES = frozenset({"admin", "local", "config"})

#: Collection names the sampler looks at in every application database.
DOCUMENTS_COLLECTION = "documents"
CHUNKS_COLLECTION = "chunks"
JOBS_COLLECTION = "ingestion_jobs"
BACKUPS_COLLECTION = "backups_metadata"
FAILED_DOCUMENTS_COLLECTION = "failed_documents"
FILE_REGISTRY_COLLECTION = "file_registry"

#: Search index statuses that count as healthy.
READY_STATUSES = frozenset({"READY"})

#: Maps ``database name -> profile key`` (resolved at sample time, profiles can change).
ProfileLookup = Callable[[], Mapping[str, str]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _pct(part: int, whole: int) -> Optional[float]:
    return round(100.0 * part / whole, 2) if whole > 0 else None


def _truthy(value: Any) -> bool:
    """Metadata values are often stored as strings ("True", "1")."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("true", "1", "yes")


def _percentile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))
    return float(sorted_values[index])


# ── pure summarisers (unit-tested without MongoDB) ────────────────────────────


def summarize_chunk_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarise projected chunk rows (``embedding_type``, ``dims``, ``model``, ``method``, ``has_context``, ``token_count``).

    Args:
        rows: Rows produced by the chunk-sample projection in :func:`collect_db_sample`.

    Returns:
        The ``sample`` entry of a database: coverage, vector-length and model distributions,
        chunking methods, contextual share and token percentiles.
    """
    size = len(rows)
    with_embedding = 0
    dims: dict[str, int] = {}
    models: dict[str, int] = {}
    methods: dict[str, int] = {}
    with_context = 0
    tokens: list[float] = []
    for row in rows:
        kind = row.get("embedding_type")
        if kind in ("array", "binData"):
            with_embedding += 1
        if kind == "array" and row.get("dims") is not None:
            key = str(_int(row.get("dims")))
            dims[key] = dims.get(key, 0) + 1
        elif kind == "binData":
            dims["binData"] = dims.get("binData", 0) + 1
        model = row.get("model")
        if model:
            models[str(model)] = models.get(str(model), 0) + 1
        method = row.get("method")
        if method:
            methods[str(method)] = methods.get(str(method), 0) + 1
        if _truthy(row.get("has_context")):
            with_context += 1
        token = _float(row.get("token_count"))
        if token is not None:
            tokens.append(token)
    tokens.sort()
    return {
        "size": size,
        "with_embedding": with_embedding,
        "embedding_coverage_pct": _pct(with_embedding, size),
        "embedding_dims": dims,
        "embedding_models": models,
        "chunk_methods": methods,
        "with_context_pct": _pct(with_context, size),
        "token_p50": _percentile(tokens, 0.5),
        "token_p95": _percentile(tokens, 0.95),
    }


def summarize_document_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarise projected document rows (``chunks_count``) into the ``document_sample`` entry."""
    size = len(rows)
    zero = 0
    for row in rows:
        count = _int(row.get("chunks_count"))
        if count is not None and count <= 0:
            zero += 1
    return {"size": size, "zero_chunks": zero, "zero_chunks_pct": _pct(zero, size)}


def server_summary(status: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the few ``serverStatus`` fields the history charts."""
    connections = status.get("connections") or {}
    cache = (status.get("wiredTiger") or {}).get("cache") or {}
    ops = status.get("opcounters") or {}
    mem = status.get("mem") or {}
    resident_mb = _float(mem.get("resident"))
    return {
        "version": status.get("version"),
        "uptime_s": _float(status.get("uptime")),
        "connections": {
            "current": _int(connections.get("current")),
            "active": _int(connections.get("active")),
            "available": _int(connections.get("available")),
            "total_created": _int(connections.get("totalCreated")),
        },
        "wt_cache": {
            "used_bytes": _int(cache.get("bytes currently in the cache")),
            "max_bytes": _int(cache.get("maximum bytes configured")),
            "dirty_bytes": _int(cache.get("tracked dirty bytes in the cache")),
        },
        "opcounters": {k: int(v) for k, v in ops.items() if k in ("insert", "query", "update", "delete", "getmore", "command")
                       and _int(v) is not None},
        "mem_resident_bytes": int(resident_mb * 1024 * 1024) if resident_mb is not None else None,
    }


def oplog_summary(stats: Mapping[str, Any], first: Optional[Mapping[str, Any]], last: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Size and time window of the oplog from its storage stats and its oldest/newest entries."""
    first_at = _aware((first or {}).get("wall"))
    last_at = _aware((last or {}).get("wall"))
    window = (last_at - first_at).total_seconds() if first_at and last_at else None
    return {
        "data_bytes": _int(stats.get("size")),
        "storage_bytes": _int(stats.get("storageSize")),
        "max_bytes": _int(stats.get("maxSize")),
        "window_s": window,
        "first_at": first_at,
        "last_at": last_at,
    }


def search_index_entries(indexes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Flatten ``listSearchIndexes`` rows to name/type/status/queryable plus the vector field's definition."""
    out: list[dict[str, Any]] = []
    for idx in indexes:
        definition = idx.get("latestDefinition") or {}
        fields = definition.get("fields")
        dims = similarity = quantization = None
        if isinstance(fields, list):
            for field in fields:
                if isinstance(field, dict) and field.get("type") == "vector":
                    dims = _int(field.get("numDimensions"))
                    similarity = field.get("similarity")
                    quantization = field.get("quantization")
                    break
        out.append({
            "name": str(idx.get("name")),
            "type": idx.get("type") or "search",
            "status": idx.get("status"),
            "queryable": idx.get("queryable"),
            "dimensions": dims,
            "similarity": similarity,
            "quantization": quantization,
        })
    return out


def sum_totals(databases: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Sum the size and count figures over the application databases."""
    totals = {"databases": len(databases), "documents": 0, "chunks": 0, "data_bytes": 0, "storage_bytes": 0,
              "index_bytes": 0, "size_on_disk_bytes": 0}
    for db in databases:
        totals["documents"] += int(((db.get("documents") or {}).get("count")) or 0)
        totals["chunks"] += int(((db.get("chunks") or {}).get("count")) or 0)
        for key in ("data_bytes", "storage_bytes", "index_bytes", "size_on_disk_bytes"):
            totals[key] += int(db.get(key) or 0)
    return totals


def _coll_stats_entry(storage: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "count": _int(storage.get("count")),
        "data_bytes": _int(storage.get("size")),
        "storage_bytes": _int(storage.get("storageSize")),
        "index_bytes": _int(storage.get("totalIndexSize")),
        "avg_bytes": _float(storage.get("avgObjSize")),
    }


# ── the sampler ───────────────────────────────────────────────────────────────


_CHUNK_PROJECTION: dict[str, Any] = {
    "_id": 0,
    "embedding_type": {"$type": "$embedding"},
    "dims": {"$cond": [{"$isArray": "$embedding"}, {"$size": "$embedding"}, None]},
    "model": "$metadata.embedding_model",
    "method": "$metadata.chunk_method",
    "has_context": "$metadata.has_context",
    "token_count": {"$ifNull": ["$token_count", "$metadata.token_count"]},
}

_DOCUMENT_PROJECTION: dict[str, Any] = {"_id": 0, "chunks_count": "$metadata.chunks_count"}


class _Probes:
    """The MongoDB calls of one run, each guarded by a timeout and recorded on failure."""

    def __init__(self, client: Any, errors: dict[str, str], timeout_s: float) -> None:
        self._client = client
        self._errors = errors
        self._timeout = timeout_s

    @property
    def timeout_ms(self) -> int:
        return int(self._timeout * 1000)

    async def run(self, label: str, coro_factory: Callable[[], Any]) -> Any:
        """Await ``coro_factory()`` with the timeout; on any failure record it under ``label`` and return None."""
        try:
            return await asyncio.wait_for(coro_factory(), timeout=self._timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one failed probe must not lose the sample
            self._errors[label] = f"{type(exc).__name__}: {str(exc)[:200]}"
            return None

    async def command(self, label: str, database: str, *args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
        return await self.run(label, lambda: self._client[database].command(*args, **kwargs))

    async def aggregate(self, label: str, database: str, collection: str, pipeline: list[dict[str, Any]],
                        *, length: Optional[int] = None) -> Optional[list[dict[str, Any]]]:
        async def _go() -> list[dict[str, Any]]:
            cursor = await self._client[database][collection].aggregate(pipeline, maxTimeMS=self.timeout_ms)
            return await cursor.to_list(length=length)

        return await self.run(label, _go)

    async def storage_stats(self, database: str, collection: str) -> Optional[dict[str, Any]]:
        rows = await self.aggregate(f"{database}.{collection}.collStats", database, collection,
                                    [{"$collStats": {"storageStats": {}}}], length=1)
        if not rows:
            return None
        return _coll_stats_entry(rows[0].get("storageStats") or {})


async def _database_stats(probes: _Probes, name: str, profile_key: Optional[str], size_on_disk: Optional[int],
                          *, sample_size: int) -> dict[str, Any]:
    """Everything the sampler records about one application database."""
    entry: dict[str, Any] = {
        "name": name, "profile_key": profile_key, "size_on_disk_bytes": size_on_disk,
        "data_bytes": None, "storage_bytes": None, "index_bytes": None, "collections": None, "objects": None,
        "documents": None, "chunks": None, "sample": None, "document_sample": None,
        "search_indexes": [], "search_lag": None, "jobs_by_status": {}, "backup": None,
        "failed_documents": None, "file_registry": None,
    }
    db_stats = await probes.command(f"{name}.dbStats", name, "dbStats")
    if db_stats:
        entry.update({
            "data_bytes": _int(db_stats.get("dataSize")),
            "storage_bytes": _int(db_stats.get("storageSize")),
            "index_bytes": _int(db_stats.get("indexSize")),
            "collections": _int(db_stats.get("collections")),
            "objects": _int(db_stats.get("objects")),
        })
    names = await probes.run(f"{name}.listCollections", lambda: probes._client[name].list_collection_names())
    present = set(names or [])
    if DOCUMENTS_COLLECTION in present:
        entry["documents"] = await probes.storage_stats(name, DOCUMENTS_COLLECTION)
    if CHUNKS_COLLECTION in present:
        entry["chunks"] = await probes.storage_stats(name, CHUNKS_COLLECTION)
    chunk_count = int(((entry.get("chunks") or {}).get("count")) or 0)
    doc_count = int(((entry.get("documents") or {}).get("count")) or 0)

    if chunk_count > 0:
        rows = await probes.aggregate(f"{name}.chunks.sample", name, CHUNKS_COLLECTION,
                                      [{"$sample": {"size": min(sample_size, chunk_count)}}, {"$project": _CHUNK_PROJECTION}])
        if rows is not None:
            entry["sample"] = summarize_chunk_rows(rows)
    if doc_count > 0:
        rows = await probes.aggregate(f"{name}.documents.sample", name, DOCUMENTS_COLLECTION,
                                      [{"$sample": {"size": min(max(sample_size // 2, 1), doc_count)}},
                                       {"$project": _DOCUMENT_PROJECTION}])
        if rows is not None:
            entry["document_sample"] = summarize_document_rows(rows)

    if CHUNKS_COLLECTION in present:
        listed = await probes.command(f"{name}.listSearchIndexes", name, {"listSearchIndexes": CHUNKS_COLLECTION})
        if listed:
            batch = ((listed.get("cursor") or {}).get("firstBatch")) or []
            entry["search_indexes"] = search_index_entries(batch)
        text_index = next((i for i in entry["search_indexes"]
                           if i.get("type") == "search" and i.get("status") in READY_STATUSES), None)
        if text_index and chunk_count > 0:
            rows = await probes.aggregate(f"{name}.chunks.searchMeta", name, CHUNKS_COLLECTION, [
                {"$searchMeta": {"index": text_index["name"], "exists": {"path": "content"}, "count": {"type": "total"}}},
            ], length=1)
            if rows:
                indexed = _int(((rows[0].get("count") or {}).get("total")))
                if indexed is not None:
                    entry["search_lag"] = {"index": text_index["name"], "indexed_count": indexed,
                                           "collection_count": chunk_count, "lag": max(0, chunk_count - indexed)}

    if JOBS_COLLECTION in present:
        rows = await probes.aggregate(f"{name}.jobs", name, JOBS_COLLECTION,
                                      [{"$group": {"_id": "$status", "n": {"$sum": 1}}}])
        if rows is not None:
            entry["jobs_by_status"] = {str(r.get("_id") or "unknown").lower(): int(r.get("n") or 0) for r in rows}
    if BACKUPS_COLLECTION in present:
        newest = await probes.run(f"{name}.backups", lambda: probes._client[name][BACKUPS_COLLECTION].find_one(
            {}, sort=[("created_at", -1)],
            projection={"_id": 0, "created_at": 1, "status": 1, "backup_type": 1, "size_bytes": 1}))
        count = await probes.run(f"{name}.backups.count", lambda: probes._client[name][BACKUPS_COLLECTION].estimated_document_count())
        entry["backup"] = {
            "count": int(count or 0),
            "newest_at": _aware((newest or {}).get("created_at")),
            "newest_status": (newest or {}).get("status"),
            "newest_type": (newest or {}).get("backup_type"),
            "newest_size_bytes": _int((newest or {}).get("size_bytes")),
        }
    if FAILED_DOCUMENTS_COLLECTION in present:
        entry["failed_documents"] = _int(await probes.run(
            f"{name}.failed_documents", lambda: probes._client[name][FAILED_DOCUMENTS_COLLECTION].estimated_document_count()))
    if FILE_REGISTRY_COLLECTION in present:
        entry["file_registry"] = _int(await probes.run(
            f"{name}.file_registry", lambda: probes._client[name][FILE_REGISTRY_COLLECTION].estimated_document_count()))
    return entry


async def collect_db_sample(
    client: Any,
    *,
    telemetry_database: str,
    profile_databases: Optional[Mapping[str, str]] = None,
    collector: Optional[str] = None,
    forced: bool = False,
    now: Optional[datetime] = None,
    command_timeout_s: float = 15.0,
    sample_size: int = 1000,
) -> dict[str, Any]:
    """Take one db-stats sample through ``client`` (an ``AsyncMongoClient``).

    Args:
        client: Client with access to every database (the telemetry store's).
        telemetry_database: Name of the telemetry database (reported apart, never as application data).
        profile_databases: ``database -> profile key`` for labelling.
        collector: ``<tenant>:<source>`` of the process taking the sample.
        forced: True when taken on request rather than by the schedule.
        now: Sample time (defaults to now, UTC).
        command_timeout_s: Timeout of each individual probe.
        sample_size: Chunks to ``$sample`` per database (documents: half of it).

    Returns:
        The sample document, ready for :meth:`TelemetryStore.insert_db_sample`. Failed probes
        leave their fields ``None`` and are listed in ``errors``.
    """
    started = time.monotonic()
    ts = now or _utcnow()
    errors: dict[str, str] = {}
    probes = _Probes(client, errors, command_timeout_s)
    profiles = dict(profile_databases or {})

    status = await probes.command("serverStatus", "admin", "serverStatus")
    listed = await probes.command("listDatabases", "admin", "listDatabases")
    on_disk: dict[str, int] = {}
    if listed:
        for row in listed.get("databases") or []:
            if isinstance(row, dict) and row.get("name"):
                on_disk[str(row["name"])] = int(row.get("sizeOnDisk") or 0)

    oplog: Optional[dict[str, Any]] = None
    oplog_stats = await probes.aggregate("oplog.collStats", "local", "oplog.rs", [{"$collStats": {"storageStats": {}}}], length=1)
    if oplog_stats:
        storage = oplog_stats[0].get("storageStats") or {}
        first = await probes.run("oplog.first", lambda: client["local"]["oplog.rs"].find_one(
            {}, sort=[("$natural", 1)], projection={"_id": 0, "wall": 1}))
        last = await probes.run("oplog.last", lambda: client["local"]["oplog.rs"].find_one(
            {}, sort=[("$natural", -1)], projection={"_id": 0, "wall": 1}))
        oplog = oplog_summary(storage, first, last)

    fs: Optional[dict[str, Any]] = None
    telemetry: Optional[dict[str, Any]] = None
    telemetry_stats = await probes.command("telemetry.dbStats", telemetry_database, "dbStats")
    if telemetry_stats:
        telemetry = {
            "data_bytes": _int(telemetry_stats.get("dataSize")),
            "storage_bytes": _int(telemetry_stats.get("storageSize")),
            "index_bytes": _int(telemetry_stats.get("indexSize")),
            "size_on_disk_bytes": on_disk.get(telemetry_database),
        }
        used, total = _int(telemetry_stats.get("fsUsedSize")), _int(telemetry_stats.get("fsTotalSize"))
        if used is not None and total:
            fs = {"used_bytes": used, "total_bytes": total, "pct": round(100.0 * used / total, 2)}

    app_names = sorted(n for n in on_disk if n not in SYSTEM_DATABASES and n != telemetry_database)
    for name in profiles:  # a configured profile whose database does not exist yet still shows up
        if name not in app_names and name not in SYSTEM_DATABASES and name != telemetry_database:
            app_names.append(name)
    databases: list[dict[str, Any]] = []
    for name in app_names:
        databases.append(await _database_stats(probes, name, profiles.get(name), on_disk.get(name), sample_size=sample_size))

    return {
        "ts": ts,
        "meta": {"source": "db-stats", "collector": collector, "schema": SCHEMA_VERSION},
        "collector": collector,
        "forced": forced,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "server": server_summary(status) if status else None,
        "oplog": oplog,
        "fs": fs,
        "telemetry": telemetry,
        "totals": sum_totals(databases),
        "databases": databases,
        "errors": errors,
    }


class DbStatsCollector:
    """Background task sampling MongoDB statistics on clock-aligned slots, one process per host.

    Args:
        store: The process's :class:`TelemetryStore` (its client reaches every database).
        holder: ``<tenant>:<source>`` recorded on the lease and the sample.
        interval_seconds: Slot length (``db_stats_interval_minutes`` × 60).
        profile_databases: Returns ``database -> profile key`` when called (may raise; then unlabelled).
        telemetry_database: Name of the telemetry database.
        run_timeout_s: Upper bound for one whole run.
        max_poll_s: Never sleep longer than this between lease checks.
    """

    def __init__(
        self,
        store: TelemetryStore,
        *,
        holder: str,
        interval_seconds: int,
        profile_databases: Optional[ProfileLookup] = None,
        telemetry_database: Optional[str] = None,
        run_timeout_s: float = 120.0,
        max_poll_s: float = 3600.0,
    ) -> None:
        self._store = store
        self._holder = holder
        self._interval = max(int(interval_seconds), 60)
        self._profiles = profile_databases
        self._telemetry_database = telemetry_database or store.database_name
        self._run_timeout = run_timeout_s
        self._max_poll = max_poll_s
        self._task: Optional[asyncio.Task[None]] = None
        self._lock = asyncio.Lock()
        self._stopping = False
        self.last_sample_at: Optional[datetime] = None
        self.last_error: Optional[str] = None

    @property
    def interval_seconds(self) -> int:
        """Slot length in seconds."""
        return self._interval

    @property
    def holder(self) -> str:
        """This process's lease holder id."""
        return self._holder

    def _profile_map(self) -> dict[str, str]:
        if self._profiles is None:
            return {}
        try:
            return dict(self._profiles())
        except Exception as exc:  # noqa: BLE001 - labels are optional
            logger.debug("db-stats profile lookup failed: %s", exc)
            return {}

    def seconds_until_next_slot(self, now: Optional[datetime] = None) -> float:
        """Seconds to sleep before the next lease check: until the next slot, plus a little jitter."""
        moment = now or _utcnow()
        wait = (slot_due_at(moment, self._interval) - moment).total_seconds()
        return min(max(wait, 0.0) + random.uniform(0.5, 5.0), self._max_poll)

    async def start(self) -> None:
        """Start the background loop (idempotent)."""
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="db-stats-collector")

    async def stop(self) -> None:
        """Cancel the background loop."""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stopping:
            try:
                if await self._store.acquire_lease(LEASE_NAME, self._interval, self._holder):
                    await self.collect_and_store()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("db-stats run failed (will retry next slot): %s", exc)
            await asyncio.sleep(self.seconds_until_next_slot())

    async def collect_and_store(self, *, forced: bool = False) -> dict[str, Any]:
        """Take a sample now and store it (serialised against the scheduled run).

        Args:
            forced: Mark the sample as taken on request.

        Returns:
            The stored sample document.
        """
        async with self._lock:
            sample = await asyncio.wait_for(
                collect_db_sample(
                    self._store.client,
                    telemetry_database=self._telemetry_database,
                    profile_databases=self._profile_map(),
                    collector=self._holder,
                    forced=forced,
                ),
                timeout=self._run_timeout,
            )
            await self._store.insert_db_sample(sample)
            self.last_sample_at = sample["ts"]
            self.last_error = None
            errors = sample.get("errors") or {}
            if errors:
                logger.info("db-stats sample stored in %.0f ms with %d failed probes: %s",
                            sample["duration_ms"], len(errors), ", ".join(sorted(errors)))
            else:
                logger.info("db-stats sample stored in %.0f ms (%d databases, %d chunks)",
                            sample["duration_ms"], sample["totals"]["databases"], sample["totals"]["chunks"])
            return sample
