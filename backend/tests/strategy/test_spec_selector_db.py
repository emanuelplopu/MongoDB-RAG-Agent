"""Tests for the DB-backed :class:`StrategySpecSelector` (Phase 5 / Task 65).

Exercises the contract described in the Task 65 brief:

* Cache key is ``(capability_id, tenant_id)`` and stamps a TTL.
* Cache hits do not re-issue store reads.
* Cache misses after TTL expiry refetch from the store.
* When no active spec exists, the legacy adapter is consulted, the
  miss is logged at ``warning`` level, and the result is cached with
  the shorter fallback TTL.
* Tenant matching wins over recency in the multi-spec ranking.
* :meth:`StrategySpecSelector.clear_cache` evicts entries.
* :class:`NoActiveSpecError` is raised when both the DB and a
  configured legacy adapter return nothing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agent.strategy.models import (
    StrategyBudgets,
    StrategyGraph,
    StrategyNode,
    StrategySpec,
)
from backend.agent.strategy.spec_selector import (
    NoActiveSpecError,
    StrategySpecSelector,
)
from backend.agent.strategy.spec_store import InMemorySpecStore, MongoSpecStore
from backend.tests.strategy._fakes import _FakeAsyncDB


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_spec(
    *,
    strategy_id: str = "spec-1",
    capability_id: str = "qa",
    tenant_scope: str = "default",
    status: str = "active",
    updated_at: datetime | None = None,
    local_only: bool = False,
    latency_target_ms: int = 8000,
) -> StrategySpec:
    graph = StrategyGraph(
        nodes=[StrategyNode(node_id="n1", node_type="retrieve")],
        edges=[],
        entry_node="n1",
        terminal_nodes=["n1"],
    )
    return StrategySpec(
        strategy_id=strategy_id,
        status=status,
        capability_id=capability_id,
        tenant_scope=tenant_scope,
        graph=graph,
        budgets=StrategyBudgets(
            local_only=local_only, latency_target_ms=latency_target_ms
        ),
        updated_at=updated_at,
    )


def _seed_snapshot(store: Any, spec: StrategySpec, *, version_counter: int = 1) -> dict[str, Any]:
    spec_data = spec.model_dump(mode="json")
    snapshot = {
        "spec_data": spec_data,
        "version": spec.version,
        "version_counter": version_counter,
        "status": spec.status,
        "created_at": spec.created_at,
        "spec_hash": spec.spec_hash or f"hash-{spec.strategy_id}",
    }
    if isinstance(store, InMemorySpecStore):
        store._snapshots.setdefault(spec.strategy_id, []).append(snapshot)
    return snapshot


async def _seed_mongo(store: MongoSpecStore, spec: StrategySpec, *, version_counter: int = 1) -> None:
    snapshot = _seed_snapshot(store, spec, version_counter=version_counter)
    await store.upsert(snapshot, expected_version=None)


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_spec_returned_and_cached() -> None:
    """An active spec for the requested capability is returned and cached."""
    db = _FakeAsyncDB()
    store = MongoSpecStore(db)
    spec = _make_spec(strategy_id="active-1", capability_id="qa")
    await _seed_mongo(store, spec)

    selector = StrategySpecSelector(store=store, legacy_adapter=None)
    result = await selector.select(capability_id="qa", tenant_id="recallhub")

    assert result is not None
    assert result.strategy_id == "active-1"
    # The cache must hold an entry for the (capability, tenant) tuple.
    assert ("qa", "recallhub") in selector._cache


@pytest.mark.asyncio
async def test_cache_hit_within_ttl_skips_store() -> None:
    """A second call within the TTL window must not re-query the store."""
    store = InMemorySpecStore()
    spec = _make_spec(strategy_id="cached", capability_id="qa")
    _seed_snapshot(store, spec)

    list_mock = AsyncMock(side_effect=store.list)
    store.list = list_mock  # type: ignore[assignment]

    selector = StrategySpecSelector(store=store, legacy_adapter=None)
    first = await selector.select(capability_id="qa", tenant_id="recallhub")
    second = await selector.select(capability_id="qa", tenant_id="recallhub")

    assert first is second
    assert list_mock.await_count == 1, "Cache hit must not re-issue store.list"


@pytest.mark.asyncio
async def test_cache_miss_after_ttl_expiry_refetches() -> None:
    """Once the TTL has elapsed the selector must re-query the store."""
    store = InMemorySpecStore()
    spec = _make_spec(strategy_id="ttl-spec", capability_id="qa")
    _seed_snapshot(store, spec)

    list_mock = AsyncMock(side_effect=store.list)
    store.list = list_mock  # type: ignore[assignment]

    selector = StrategySpecSelector(
        store=store, legacy_adapter=None, cache_ttl_seconds=300
    )
    await selector.select(capability_id="qa", tenant_id="recallhub")

    # Force the cached entry to look expired.
    entry = selector._cache[("qa", "recallhub")]
    entry.expires_at = 0.0

    await selector.select(capability_id="qa", tenant_id="recallhub")
    assert list_mock.await_count == 2


@pytest.mark.asyncio
async def test_no_active_spec_falls_back_to_legacy_adapter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Empty store -> legacy adapter is consulted, warning logged, short TTL."""
    store = InMemorySpecStore()  # nothing seeded
    legacy_spec = _make_spec(
        strategy_id="legacy-fallback",
        capability_id="qa",
        tenant_scope="default",
    )
    legacy_adapter = MagicMock()
    legacy_adapter.get_spec.return_value = legacy_spec

    selector = StrategySpecSelector(
        store=store,
        legacy_adapter=legacy_adapter,
        cache_ttl_seconds=300,
        fallback_ttl_seconds=60,
    )

    with caplog.at_level(logging.WARNING):
        result = await selector.select(capability_id="qa", tenant_id="recallhub")

    assert result is legacy_spec
    legacy_adapter.get_spec.assert_called_once()
    # Warning log must reference the missed capability.
    assert any(
        "no active spec" in rec.getMessage() and "qa" in rec.getMessage()
        for rec in caplog.records
    )
    entry = selector._cache[("qa", "recallhub")]
    assert entry.from_fallback is True
    # TTL stamp must use the shorter fallback window.
    remaining = entry.expires_at - __import__("time").time()
    assert remaining <= 60 + 1


@pytest.mark.asyncio
async def test_multiple_active_specs_tenant_match_wins() -> None:
    """Tenant-scoped specs win over default-scoped specs even when newer."""
    store = InMemorySpecStore()
    older_tenant_match = _make_spec(
        strategy_id="tenant-spec",
        capability_id="qa",
        tenant_scope="recallhub",
        updated_at=datetime(2026, 1, 1),
    )
    newer_default = _make_spec(
        strategy_id="default-spec",
        capability_id="qa",
        tenant_scope="default",
        updated_at=datetime(2026, 5, 1),
    )
    _seed_snapshot(store, older_tenant_match)
    _seed_snapshot(store, newer_default)

    selector = StrategySpecSelector(store=store, legacy_adapter=None)
    result = await selector.select(capability_id="qa", tenant_id="recallhub")

    assert result is not None
    assert result.strategy_id == "tenant-spec"


@pytest.mark.asyncio
async def test_multiple_active_specs_recency_breaks_tie() -> None:
    """When no tenant match exists, the most recent ``updated_at`` wins."""
    store = InMemorySpecStore()
    newer = _make_spec(
        strategy_id="newer",
        capability_id="qa",
        tenant_scope="default",
        updated_at=datetime(2026, 5, 1),
    )
    older = _make_spec(
        strategy_id="older",
        capability_id="qa",
        tenant_scope="default",
        updated_at=datetime(2026, 1, 1),
    )
    _seed_snapshot(store, newer)
    _seed_snapshot(store, older)

    selector = StrategySpecSelector(store=store, legacy_adapter=None)
    result = await selector.select(capability_id="qa", tenant_id="recallhub")

    assert result is not None
    assert result.strategy_id == "newer"


@pytest.mark.asyncio
async def test_clear_cache_invalidates_entries() -> None:
    """``clear_cache`` evicts entries so subsequent calls re-query the store."""
    store = InMemorySpecStore()
    spec = _make_spec(strategy_id="cached", capability_id="qa")
    _seed_snapshot(store, spec)

    list_mock = AsyncMock(side_effect=store.list)
    store.list = list_mock  # type: ignore[assignment]

    selector = StrategySpecSelector(store=store, legacy_adapter=None)
    await selector.select(capability_id="qa", tenant_id="recallhub")
    assert list_mock.await_count == 1

    removed = selector.clear_cache()
    assert removed == 1
    assert selector._cache == {}

    await selector.select(capability_id="qa", tenant_id="recallhub")
    assert list_mock.await_count == 2


@pytest.mark.asyncio
async def test_no_active_spec_error_when_legacy_also_empty() -> None:
    """If both store and legacy adapter return nothing, raise NoActiveSpecError."""
    store = InMemorySpecStore()
    legacy_adapter = MagicMock()
    legacy_adapter.get_spec.return_value = None

    selector = StrategySpecSelector(store=store, legacy_adapter=legacy_adapter)

    with pytest.raises(NoActiveSpecError) as excinfo:
        await selector.select(capability_id="qa", tenant_id="recallhub")

    assert excinfo.value.capability_id == "qa"
    assert excinfo.value.tenant_id == "recallhub"
