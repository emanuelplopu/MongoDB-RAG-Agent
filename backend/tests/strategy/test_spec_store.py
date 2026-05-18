"""Tests for :mod:`backend.agent.strategy.spec_store`.

Covers CRUD parity between the in-memory and a minimal async Mongo
collection fake, optimistic-locking conflict semantics, and the list
filter surface (status / capability_id / tenant_id). The fake-collection
infrastructure was lifted to :mod:`backend.tests.strategy._fakes` in
Task 61 so the scheduler-store tests can reuse it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from backend.agent.strategy.spec_store import (
    InMemorySpecStore,
    MongoSpecStore,
    SpecStore,
    VersionConflictError,
)
from backend.tests.strategy._fakes import (
    _AsyncCursor,
    _DeleteResult,
    _FakeAsyncCollection,
    _FakeAsyncDB,
)

# Re-exported for backward compatibility with sibling test modules that
# import these names from ``backend.tests.strategy.test_spec_store``.
__all__ = [
    "_AsyncCursor",
    "_DeleteResult",
    "_FakeAsyncCollection",
    "_FakeAsyncDB",
]


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════


def _snapshot(
    strategy_id: str,
    version: str,
    *,
    counter: int = 1,
    status: str = "draft",
    capability_id: str = "cap-billing",
    tenant_scope: str = "shared",
    extra_hash: str = "",
) -> dict[str, Any]:
    spec_data = {
        "strategy_id": strategy_id,
        "version": version,
        "capability_id": capability_id,
        "tenant_scope": tenant_scope,
        "status": status,
        "graph": {
            "nodes": [{"node_id": "n1", "node_type": "retrieve"}],
            "edges": [],
            "entry_node": "n1",
            "terminal_nodes": ["n1"],
        },
    }
    return {
        "spec_data": spec_data,
        "version": version,
        "version_counter": counter,
        "status": status,
        "created_at": datetime.utcnow(),
        "spec_hash": f"h-{strategy_id}-{version}-{counter}{extra_hash}",
    }


@pytest.fixture
def in_memory_store() -> InMemorySpecStore:
    return InMemorySpecStore()


@pytest.fixture
def mongo_store() -> MongoSpecStore:
    return MongoSpecStore(_FakeAsyncDB())


@pytest.fixture(params=["in_memory", "mongo"])
def store(request: pytest.FixtureRequest) -> SpecStore:
    if request.param == "in_memory":
        return InMemorySpecStore()
    return MongoSpecStore(_FakeAsyncDB())


# ══════════════════════════════════════════════════════════════════════════════
# CRUD parity (parametrized across both stores)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_missing_returns_none(store: SpecStore) -> None:
    assert await store.get("no-such-strategy") is None
    assert await store.get("no-such-strategy", version="1.0.0") is None


@pytest.mark.asyncio
async def test_upsert_then_get_latest(store: SpecStore) -> None:
    snap = _snapshot("strategy-a", "1.0.0", counter=1)
    await store.upsert(snap)

    found = await store.get("strategy-a")
    assert found is not None
    assert found["version"] == "1.0.0"
    assert found["version_counter"] == 1


@pytest.mark.asyncio
async def test_upsert_appends_new_version(store: SpecStore) -> None:
    await store.upsert(_snapshot("strategy-a", "1.0.0", counter=1))
    await store.upsert(_snapshot("strategy-a", "1.1.0", counter=2))

    latest = await store.get("strategy-a")
    assert latest is not None
    assert latest["version"] == "1.1.0"
    assert latest["version_counter"] == 2

    versions = await store.list_versions("strategy-a")
    assert [v["version"] for v in versions] == ["1.0.0", "1.1.0"]


@pytest.mark.asyncio
async def test_upsert_replaces_same_version(store: SpecStore) -> None:
    await store.upsert(_snapshot("strategy-a", "1.0.0", counter=1))
    await store.upsert(
        _snapshot("strategy-a", "1.0.0", counter=1, status="active")
    )

    latest = await store.get("strategy-a", version="1.0.0")
    assert latest is not None
    assert latest["status"] == "active"

    versions = await store.list_versions("strategy-a")
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_optimistic_locking_conflict_raises(store: SpecStore) -> None:
    await store.upsert(_snapshot("strategy-a", "1.0.0", counter=1))

    with pytest.raises(VersionConflictError) as exc_info:
        await store.upsert(
            _snapshot("strategy-a", "1.1.0", counter=2),
            expected_version=99,
        )
    assert exc_info.value.expected == 99
    assert exc_info.value.current == 1


@pytest.mark.asyncio
async def test_optimistic_locking_accepts_matching_expected(
    store: SpecStore,
) -> None:
    await store.upsert(_snapshot("strategy-a", "1.0.0", counter=1))
    await store.upsert(
        _snapshot("strategy-a", "1.1.0", counter=2),
        expected_version=1,
    )

    latest = await store.get("strategy-a")
    assert latest is not None
    assert latest["version"] == "1.1.0"


@pytest.mark.asyncio
async def test_optimistic_locking_on_first_insert(store: SpecStore) -> None:
    """expected_version=0 enforces 'must not already exist'."""
    await store.upsert(
        _snapshot("strategy-a", "1.0.0", counter=1),
        expected_version=0,
    )
    with pytest.raises(VersionConflictError):
        await store.upsert(
            _snapshot("strategy-a", "2.0.0", counter=2),
            expected_version=0,
        )


@pytest.mark.asyncio
async def test_delete_specific_version(store: SpecStore) -> None:
    await store.upsert(_snapshot("strategy-a", "1.0.0", counter=1))
    await store.upsert(_snapshot("strategy-a", "1.1.0", counter=2))

    assert await store.delete("strategy-a", version="1.0.0") is True

    versions = await store.list_versions("strategy-a")
    assert [v["version"] for v in versions] == ["1.1.0"]


@pytest.mark.asyncio
async def test_delete_all_versions(store: SpecStore) -> None:
    await store.upsert(_snapshot("strategy-a", "1.0.0", counter=1))
    await store.upsert(_snapshot("strategy-a", "1.1.0", counter=2))

    assert await store.delete("strategy-a") is True
    assert await store.get("strategy-a") is None
    assert await store.list_versions("strategy-a") == []


@pytest.mark.asyncio
async def test_delete_missing_returns_false(store: SpecStore) -> None:
    assert await store.delete("nope") is False


@pytest.mark.asyncio
async def test_list_filters_by_status(store: SpecStore) -> None:
    await store.upsert(_snapshot("a", "1.0.0", counter=1, status="draft"))
    await store.upsert(_snapshot("b", "1.0.0", counter=1, status="active"))
    await store.upsert(_snapshot("c", "1.0.0", counter=1, status="active"))

    draft = await store.list(status="draft")
    active = await store.list(status="active")
    assert {s["spec_data"]["strategy_id"] for s in draft} == {"a"}
    assert {s["spec_data"]["strategy_id"] for s in active} == {"b", "c"}


@pytest.mark.asyncio
async def test_list_filters_by_capability_id(store: SpecStore) -> None:
    await store.upsert(_snapshot("a", "1.0.0", capability_id="cap-x"))
    await store.upsert(_snapshot("b", "1.0.0", capability_id="cap-y"))

    only_x = await store.list(capability_id="cap-x")
    assert {s["spec_data"]["strategy_id"] for s in only_x} == {"a"}


@pytest.mark.asyncio
async def test_list_filters_by_tenant_id(store: SpecStore) -> None:
    await store.upsert(_snapshot("a", "1.0.0", tenant_scope="quellex"))
    await store.upsert(_snapshot("b", "1.0.0", tenant_scope="recallhub"))

    quellex = await store.list(tenant_id="quellex")
    assert {s["spec_data"]["strategy_id"] for s in quellex} == {"a"}


@pytest.mark.asyncio
async def test_list_returns_latest_per_strategy(store: SpecStore) -> None:
    await store.upsert(_snapshot("a", "1.0.0", counter=1, status="draft"))
    await store.upsert(_snapshot("a", "1.1.0", counter=2, status="active"))

    all_specs = await store.list()
    matching = [
        s for s in all_specs if s["spec_data"]["strategy_id"] == "a"
    ]
    assert len(matching) == 1
    assert matching[0]["version"] == "1.1.0"
    assert matching[0]["version_counter"] == 2


@pytest.mark.asyncio
async def test_snapshots_view_exposes_dict_of_list(
    in_memory_store: InMemorySpecStore,
) -> None:
    snap = _snapshot("strategy-a", "1.0.0", counter=1)
    await in_memory_store.upsert(snap)

    assert "strategy-a" in in_memory_store.snapshots
    assert in_memory_store.snapshots["strategy-a"][0]["version"] == "1.0.0"


# ══════════════════════════════════════════════════════════════════════════════
# Mongo-specific behavior
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mongo_store_requires_db_handle() -> None:
    with pytest.raises(ValueError):
        MongoSpecStore(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_mongo_store_mirror_hydrates_lazily(
    mongo_store: MongoSpecStore,
) -> None:
    await mongo_store.upsert(_snapshot("a", "1.0.0", counter=1))
    await mongo_store.ensure_loaded()
    assert "a" in mongo_store.snapshots
    assert mongo_store.snapshots["a"][0]["version"] == "1.0.0"


# ═════════════════════════════════════════════════════════════════════════════
# Promotion-manager write-through (Task 67)
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_in_memory_flush_snapshot_is_noop(
    in_memory_store: InMemorySpecStore,
) -> None:
    """InMemorySpecStore.flush_snapshot returns True without errors."""
    # Empty store is fine — the in-memory mirror IS the canonical state
    # so the no-op should not require a matching entry to exist.
    assert await in_memory_store.flush_snapshot("nope", "1.0.0") is True

    await in_memory_store.upsert(_snapshot("strategy-a", "1.0.0", counter=1))
    assert (
        await in_memory_store.flush_snapshot("strategy-a", "1.0.0") is True
    )


@pytest.mark.asyncio
async def test_mongo_flush_snapshot_missing_returns_false(
    mongo_store: MongoSpecStore,
) -> None:
    """Flushing a snapshot that is not in the mirror returns False."""
    assert (
        await mongo_store.flush_snapshot("missing-strategy", "1.0.0")
        is False
    )

    # An entry for a different version should not satisfy the lookup.
    await mongo_store.upsert(_snapshot("strategy-a", "1.0.0", counter=1))
    await mongo_store.ensure_loaded()
    assert (
        await mongo_store.flush_snapshot("strategy-a", "9.9.9") is False
    )


@pytest.mark.asyncio
async def test_mongo_flush_snapshot_persists_status_change(
    mongo_store: MongoSpecStore,
) -> None:
    """PromotionManager mirror mutation is persisted by flush_snapshot."""
    from backend.agent.strategy.promotion_manager import PromotionManager

    # Seed a draft snapshot through the store so both Mongo and the
    # in-memory mirror are populated.
    seed = _snapshot("strategy-a", "1.0.0", counter=1, status="draft")
    await mongo_store.upsert(seed)
    await mongo_store.ensure_loaded()

    # Mutate the mirror in place via the promotion manager (matches the
    # production wiring — router endpoints share the snapshot view).
    manager = PromotionManager(store=mongo_store.snapshots)
    promoted = await manager.promote(
        "strategy-a", "1.0.0", actor="alice", reason="ready for production"
    )
    assert promoted.status == "active"

    # Sanity: Mongo has not yet observed the mutation.
    pre_flush = await mongo_store.collection.find_one(
        {"strategy_id": "strategy-a", "version": "1.0.0"}
    )
    assert pre_flush is not None
    assert pre_flush["status"] == "draft"

    # Flush the mirror back to Mongo and confirm the change landed.
    flushed = await mongo_store.flush_snapshot("strategy-a", "1.0.0")
    assert flushed is True

    persisted = await mongo_store.get("strategy-a", version="1.0.0")
    assert persisted is not None
    assert persisted["status"] == "active"
    assert persisted["spec_data"]["status"] == "active"
    # Counter must have been bumped to defeat any concurrent stale write.
    assert persisted["version_counter"] == 2
