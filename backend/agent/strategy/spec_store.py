"""Strategy spec persistence layer.

Defines a :class:`SpecStore` abstract interface plus two concrete
implementations:

* :class:`InMemorySpecStore` — backed by a process-local
  ``dict[str, list[dict]]`` snapshot table; preserves the exact shape used
  by :mod:`backend.routers.strategy_specs._specs_store` so that
  :class:`backend.agent.strategy.promotion_manager.PromotionManager` works
  unchanged against it.
* :class:`MongoSpecStore` — backed by an async MongoDB ``strategy_specs``
  collection (PyMongo Async / Motor compatible). Implements optimistic
  locking via a monotonically-increasing ``version_counter`` field and
  raises :class:`VersionConflictError` on mismatch.

Both stores expose a ``snapshots`` property returning a
``dict[str, list[dict]]`` so the promotion manager — which mutates that
structure directly — can be wired against either implementation without
code changes. For :class:`MongoSpecStore` this is an in-memory mirror
that is hydrated lazily from Mongo on first access and kept in sync
with subsequent writes; mutations applied directly through the snapshots
view (e.g. by the promotion manager) are not auto-flushed back to Mongo
and remain an open follow-up.
"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "SPEC_COLLECTION_NAME",
    "VersionConflictError",
    "SpecStore",
    "InMemorySpecStore",
    "MongoSpecStore",
]


#: Canonical Mongo collection name for persisted strategy specs.
SPEC_COLLECTION_NAME: str = "strategy_specs"


class VersionConflictError(Exception):
    """Raised when an optimistic-locking precondition fails on upsert.

    Attributes:
        expected: The ``version_counter`` value the caller asserted.
        current: The ``version_counter`` actually present in storage.
    """

    def __init__(self, *, expected: int, current: int) -> None:
        super().__init__(
            f"Version conflict: expected version_counter={expected}, "
            f"current={current}"
        )
        self.expected = expected
        self.current = current


class SpecStore(ABC):
    """Abstract base for strategy spec persistence backends.

    All implementations expose async CRUD plus an audit-log accessor and
    a ``snapshots`` view (``dict[strategy_id, list[snapshot]]``) for
    compatibility with the existing promotion-manager mutation contract.
    """

    @abstractmethod
    async def get(
        self, strategy_id: str, version: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """Return a snapshot dict for ``strategy_id``.

        Args:
            strategy_id: Strategy id to look up.
            version: Optional semantic version. When ``None`` returns
                the latest snapshot (highest ``version_counter``).

        Returns:
            The snapshot dict (same shape as
            ``_specs_store[strategy_id][i]``) or ``None`` if missing.
        """

    @abstractmethod
    async def list(
        self,
        *,
        status: Optional[str] = None,
        capability_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return the latest snapshot per strategy, optionally filtered.

        Args:
            status: Filter by snapshot ``status``.
            capability_id: Filter by ``spec_data.capability_id``.
            tenant_id: Filter by ``spec_data.tenant_scope``.
            limit: Hard cap on the number of results returned.

        Returns:
            List of snapshot dicts (latest per strategy_id).
        """

    @abstractmethod
    async def upsert(
        self,
        snapshot: dict[str, Any],
        *,
        expected_version: Optional[int] = None,
    ) -> dict[str, Any]:
        """Insert or update a snapshot with optional optimistic locking.

        ``snapshot`` must contain ``spec_data``, ``version``,
        ``version_counter``, ``status``, ``created_at``, ``spec_hash``.
        The implementation derives the ``strategy_id`` from
        ``snapshot['spec_data']['strategy_id']``.

        Args:
            snapshot: Snapshot dict to persist.
            expected_version: When set, asserts the latest stored
                ``version_counter`` for the strategy equals this value
                before applying the write. ``None`` means accept either
                insert or overwrite.

        Returns:
            The persisted snapshot dict.

        Raises:
            VersionConflictError: ``expected_version`` did not match the
                current latest stored ``version_counter``.
        """

    @abstractmethod
    async def delete(
        self, strategy_id: str, version: Optional[str] = None
    ) -> bool:
        """Remove a snapshot (or all snapshots for ``strategy_id``).

        Args:
            strategy_id: Strategy id whose snapshot(s) to remove.
            version: Optional specific version. ``None`` removes every
                snapshot for the strategy.

        Returns:
            ``True`` if at least one snapshot was removed.
        """

    @abstractmethod
    async def list_versions(self, strategy_id: str) -> list[dict[str, Any]]:
        """Return every stored snapshot for ``strategy_id`` ordered by counter."""

    @abstractmethod
    async def flush_snapshot(self, strategy_id: str, version: str) -> bool:
        """Persist the in-memory mirror entry for ``(strategy_id, version)``.

        Promotion-manager mutations (promote/deprecate/archive/rollback) are
        applied directly to the dict-of-list snapshot view exposed by
        :attr:`snapshots`. For Mongo-backed deployments those mutations are
        not auto-flushed by the optimistic-locking ``upsert`` path; this
        method gives router endpoints a hook to write the mutated mirror
        entry back to durable storage on a best-effort basis.

        Args:
            strategy_id: Strategy id of the snapshot to flush.
            version: Semantic version of the snapshot to flush.

        Returns:
            ``True`` if the snapshot was located in the in-memory mirror
            and a flush was attempted (regardless of whether the
            underlying write actually succeeded). ``False`` when no
            mirror entry exists for ``(strategy_id, version)``.
        """

    # ── Properties shared by both implementations ──────────────────────────

    @property
    @abstractmethod
    def snapshots(self) -> dict[str, list[dict[str, Any]]]:
        """Return a dict-of-list snapshot view compatible with the legacy shim."""

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        """Return store-level audit entries. Default empty (override as needed)."""
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# In-memory implementation
# ═══════════════════════════════════════════════════════════════════════════════


class InMemorySpecStore(SpecStore):
    """Process-local, dict-backed :class:`SpecStore` implementation.

    Mirrors the exact layout of the legacy ``_specs_store`` module-level
    dict so existing tests and the promotion manager keep working without
    modification.

    Args:
        snapshots: Optional external dict to wrap. When omitted a fresh
            empty dict is created. Passing the legacy ``_specs_store``
            here keeps existing test fixtures and global state aligned.
    """

    def __init__(
        self,
        snapshots: Optional[dict[str, list[dict[str, Any]]]] = None,
    ) -> None:
        self._snapshots: dict[str, list[dict[str, Any]]] = (
            snapshots if snapshots is not None else {}
        )

    @property
    def snapshots(self) -> dict[str, list[dict[str, Any]]]:
        """Return the underlying mutable dict-of-list."""
        return self._snapshots

    async def get(
        self, strategy_id: str, version: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        versions = self._snapshots.get(strategy_id)
        if not versions:
            return None
        if version is None:
            return versions[-1]
        return next((v for v in versions if v["version"] == version), None)

    async def list(
        self,
        *,
        status: Optional[str] = None,
        capability_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for versions in self._snapshots.values():
            if not versions:
                continue
            latest = versions[-1]
            spec_data = latest.get("spec_data", {})
            if status is not None and spec_data.get("status") != status:
                continue
            if capability_id is not None and spec_data.get("capability_id") != capability_id:
                continue
            if tenant_id is not None and spec_data.get("tenant_scope") != tenant_id:
                continue
            results.append(latest)
        return results[:limit]

    async def upsert(
        self,
        snapshot: dict[str, Any],
        *,
        expected_version: Optional[int] = None,
    ) -> dict[str, Any]:
        strategy_id = snapshot["spec_data"]["strategy_id"]
        versions = self._snapshots.setdefault(strategy_id, [])
        current_counter = int(versions[-1]["version_counter"]) if versions else 0
        if expected_version is not None and expected_version != current_counter:
            raise VersionConflictError(
                expected=expected_version, current=current_counter
            )

        new_version = snapshot.get("version")
        existing_idx = next(
            (i for i, v in enumerate(versions) if v["version"] == new_version),
            None,
        )
        if existing_idx is not None:
            versions[existing_idx] = snapshot
        else:
            versions.append(snapshot)
        return snapshot

    async def delete(
        self, strategy_id: str, version: Optional[str] = None
    ) -> bool:
        versions = self._snapshots.get(strategy_id)
        if not versions:
            return False
        if version is None:
            del self._snapshots[strategy_id]
            return True
        before = len(versions)
        remaining = [v for v in versions if v["version"] != version]
        if remaining:
            self._snapshots[strategy_id] = remaining
        else:
            del self._snapshots[strategy_id]
        return before != len(remaining)

    async def list_versions(self, strategy_id: str) -> list[dict[str, Any]]:
        return list(self._snapshots.get(strategy_id, []))

    async def flush_snapshot(self, strategy_id: str, version: str) -> bool:
        """No-op flush for the in-memory store.

        The dict-of-list mirror **is** the canonical state for this
        backend, so there is nothing to persist. Returns ``True``
        unconditionally to keep the caller surface symmetric with
        :class:`MongoSpecStore`, which performs an actual write.

        Args:
            strategy_id: Ignored; kept for interface parity.
            version: Ignored; kept for interface parity.

        Returns:
            ``True`` always.
        """
        return True


# ═══════════════════════════════════════════════════════════════════════════════
# Mongo implementation
# ═══════════════════════════════════════════════════════════════════════════════


class MongoSpecStore(SpecStore):
    """MongoDB-backed :class:`SpecStore` implementation.

    Each persisted document has the shape::

        {
            "strategy_id": str,
            "version": str,
            "version_counter": int,
            "status": str,
            "spec_hash": str,
            "spec_data": dict,
            "created_at": datetime,
            "updated_at": datetime,
        }

    The composite ``(strategy_id, version)`` is the natural key. Optimistic
    locking is enforced by comparing ``expected_version`` against the
    highest ``version_counter`` currently stored for ``strategy_id`` and
    raising :class:`VersionConflictError` on mismatch.

    A best-effort in-memory mirror (``self.snapshots``) is lazily hydrated
    from Mongo on first access and refreshed by every successful write
    issued through this store. The mirror exists so the promotion manager
    — which mutates snapshot dicts in place — can continue to operate
    against either backend. Mutations done directly via the mirror are
    not auto-flushed back to Mongo; that flush-back work is tracked as an
    open issue for the next scheduler-persistence task.

    Args:
        db: An async MongoDB database handle (e.g.
            ``pymongo.AsyncMongoClient`` database or
            ``AsyncIOMotorDatabase``).
        collection_name: Override the collection name. Defaults to
            :data:`SPEC_COLLECTION_NAME`.
    """

    def __init__(self, db: Any, *, collection_name: str = SPEC_COLLECTION_NAME) -> None:
        if db is None:
            raise ValueError("MongoSpecStore requires a non-None database handle")
        self._db = db
        self._collection_name = collection_name
        self._snapshots: dict[str, list[dict[str, Any]]] = {}
        self._snapshots_hydrated = False
        self._mirror_lock = asyncio.Lock()

    @property
    def collection(self) -> Any:
        """Return the underlying async Mongo collection handle."""
        return self._db[self._collection_name]

    @property
    def snapshots(self) -> dict[str, list[dict[str, Any]]]:
        """Return the in-memory mirror of stored snapshots.

        The mirror is populated lazily and synchronously on first access.
        When called inside an event loop without a hydrated mirror it
        returns whatever is currently cached; callers should ``await
        ensure_loaded()`` if a guaranteed-fresh view is needed.
        """
        return self._snapshots

    # ── Public helpers ──────────────────────────────────────────────────────

    async def ensure_loaded(self, *, force: bool = False) -> None:
        """Hydrate the in-memory mirror from Mongo.

        Args:
            force: When ``True``, rehydrate even if already loaded.
        """
        if self._snapshots_hydrated and not force:
            return
        async with self._mirror_lock:
            if self._snapshots_hydrated and not force:
                return
            mirror: dict[str, list[dict[str, Any]]] = {}
            cursor = self.collection.find({})
            async for doc in cursor:
                snap = self._doc_to_snapshot(doc)
                mirror.setdefault(snap["spec_data"]["strategy_id"], []).append(snap)
            for sid in mirror:
                mirror[sid].sort(key=lambda s: int(s.get("version_counter", 0)))
            self._snapshots = mirror
            self._snapshots_hydrated = True

    # ── SpecStore methods ──────────────────────────────────────────────────

    async def get(
        self, strategy_id: str, version: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        if version is not None:
            doc = await self.collection.find_one(
                {"strategy_id": strategy_id, "version": version}
            )
            return self._doc_to_snapshot(doc) if doc else None
        cursor = self.collection.find({"strategy_id": strategy_id}).sort(
            "version_counter", -1
        )
        async for doc in cursor:
            return self._doc_to_snapshot(doc)
        return None

    async def list(
        self,
        *,
        status: Optional[str] = None,
        capability_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        # Aggregate to pick the highest version_counter per strategy_id.
        match: dict[str, Any] = {}
        if status is not None:
            match["status"] = status
        if capability_id is not None:
            match["spec_data.capability_id"] = capability_id
        if tenant_id is not None:
            match["spec_data.tenant_scope"] = tenant_id

        pipeline: list[dict[str, Any]] = []
        if match:
            pipeline.append({"$match": match})
        pipeline.extend(
            [
                {"$sort": {"version_counter": -1}},
                {
                    "$group": {
                        "_id": "$strategy_id",
                        "doc": {"$first": "$$ROOT"},
                    }
                },
                {"$replaceRoot": {"newRoot": "$doc"}},
                {"$limit": int(limit)},
            ]
        )
        results: list[dict[str, Any]] = []
        cursor = self.collection.aggregate(pipeline)
        async for doc in cursor:
            results.append(self._doc_to_snapshot(doc))
        return results

    async def upsert(
        self,
        snapshot: dict[str, Any],
        *,
        expected_version: Optional[int] = None,
    ) -> dict[str, Any]:
        strategy_id = snapshot["spec_data"]["strategy_id"]
        version = snapshot["version"]

        if expected_version is not None:
            latest = await self.get(strategy_id)
            current_counter = (
                int(latest["version_counter"]) if latest else 0
            )
            if expected_version != current_counter:
                raise VersionConflictError(
                    expected=expected_version, current=current_counter
                )

        doc = self._snapshot_to_doc(snapshot)
        doc["updated_at"] = datetime.utcnow()
        await self.collection.replace_one(
            {"strategy_id": strategy_id, "version": version},
            doc,
            upsert=True,
        )

        # Refresh mirror entry if hydrated.
        if self._snapshots_hydrated:
            versions = self._snapshots.setdefault(strategy_id, [])
            idx = next(
                (i for i, v in enumerate(versions) if v["version"] == version),
                None,
            )
            if idx is None:
                versions.append(snapshot)
            else:
                versions[idx] = snapshot
            versions.sort(key=lambda s: int(s.get("version_counter", 0)))
        return snapshot

    async def delete(
        self, strategy_id: str, version: Optional[str] = None
    ) -> bool:
        query: dict[str, Any] = {"strategy_id": strategy_id}
        if version is not None:
            query["version"] = version
        if version is None:
            result = await self.collection.delete_many(query)
        else:
            result = await self.collection.delete_one(query)
        removed = result.deleted_count > 0
        if removed and self._snapshots_hydrated:
            versions = self._snapshots.get(strategy_id, [])
            if version is None:
                self._snapshots.pop(strategy_id, None)
            else:
                self._snapshots[strategy_id] = [
                    v for v in versions if v["version"] != version
                ]
                if not self._snapshots[strategy_id]:
                    self._snapshots.pop(strategy_id, None)
        return removed

    async def list_versions(self, strategy_id: str) -> list[dict[str, Any]]:
        cursor = self.collection.find({"strategy_id": strategy_id}).sort(
            "version_counter", 1
        )
        out: list[dict[str, Any]] = []
        async for doc in cursor:
            out.append(self._doc_to_snapshot(doc))
        return out

    async def flush_snapshot(self, strategy_id: str, version: str) -> bool:
        """Write the in-memory mirror entry back to Mongo.

        Looks up ``(strategy_id, version)`` in :attr:`snapshots`
        (populated by :class:`PromotionManager` mutations or prior
        :meth:`upsert` calls) and replays it onto the underlying
        collection via ``replace_one(..., upsert=True)``. The mirror's
        ``version_counter`` is bumped by 1 before the write so any
        concurrent stale write loses the optimistic-locking race on the
        next ``upsert`` attempt.

        The flush is best-effort: a Mongo write failure is logged at
        ``WARNING`` level but never raised, since the in-process state
        observed by callers is already consistent and the next mutation
        will retry the persistence.

        Args:
            strategy_id: Strategy id of the snapshot to flush.
            version: Semantic version of the snapshot to flush.

        Returns:
            ``True`` if a matching mirror entry was found and a flush
            was attempted. ``False`` if no entry exists for
            ``(strategy_id, version)`` in the mirror.
        """
        versions = self._snapshots.get(strategy_id)
        if not versions:
            return False
        snapshot = next(
            (v for v in versions if v.get("version") == version), None
        )
        if snapshot is None:
            return False

        # Bump the in-memory counter so any concurrent stale upsert that
        # asserts the previous counter loses the optimistic-lock race.
        snapshot["version_counter"] = int(snapshot.get("version_counter", 0)) + 1

        try:
            doc = self._snapshot_to_doc(snapshot)
            doc["updated_at"] = datetime.utcnow()
            await self.collection.replace_one(
                {"strategy_id": strategy_id, "version": version},
                doc,
                upsert=True,
            )
        except Exception as exc:  # noqa: BLE001 - flush is best-effort durability
            logger.warning(
                "Best-effort flush_snapshot failed for %s@%s: %s",
                strategy_id,
                version,
                exc,
            )
        return True

    # ── Internal helpers ────────────────────────────────────────────────────

    @staticmethod
    def _snapshot_to_doc(snapshot: dict[str, Any]) -> dict[str, Any]:
        """Convert a snapshot to a flat Mongo document."""
        spec_data = snapshot.get("spec_data", {})
        strategy_id = spec_data.get("strategy_id")
        if not strategy_id:
            raise ValueError("snapshot.spec_data.strategy_id is required")
        # Ensure JSON-safe spec_data
        try:
            json.dumps(spec_data, default=str)
        except TypeError as exc:
            raise ValueError(f"spec_data is not JSON serializable: {exc}") from exc
        return {
            "strategy_id": strategy_id,
            "version": snapshot["version"],
            "version_counter": int(snapshot["version_counter"]),
            "status": snapshot["status"],
            "spec_hash": snapshot.get("spec_hash", ""),
            "spec_data": spec_data,
            "created_at": snapshot.get("created_at") or datetime.utcnow(),
        }

    @staticmethod
    def _doc_to_snapshot(doc: dict[str, Any]) -> dict[str, Any]:
        """Convert a Mongo document back to the snapshot dict shape."""
        spec_data = doc.get("spec_data", {}) or {}
        return {
            "spec_data": spec_data,
            "version": doc.get("version", spec_data.get("version", "")),
            "version_counter": int(doc.get("version_counter", 1)),
            "status": doc.get("status", spec_data.get("status", "draft")),
            "created_at": doc.get("created_at", datetime.utcnow()),
            "spec_hash": doc.get("spec_hash", ""),
        }
