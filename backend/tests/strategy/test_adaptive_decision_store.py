"""Tests for the F8 / Task 85 :class:`AdaptiveDecisionStore`.

Covers the round-trip + filter contract for both the in-memory and
Mongo-backed implementations, plus index creation behaviour and the
forward-compatible ``extra="allow"`` model contract.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.agent.strategy.adaptive_decision_store import (
    ADAPTIVE_DECISION_COLLECTION_NAME,
    DEFAULT_ADAPTIVE_DECISION_TTL_DAYS,
    AdaptiveDecisionDoc,
    AdaptiveDecisionStore,
    InMemoryAdaptiveDecisionStore,
    MongoAdaptiveDecisionStore,
)
from backend.tests.strategy._fakes import _FakeAsyncDB


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_doc(
    *,
    capability_id: str = "qa",
    winner_strategy_id: str = "alpha",
    tenant_id: str | None = "recallhub",
    privacy_mode: str | None = "standard",
    agent_mode: str | None = "auto",
    fast_path_eligible: bool = False,
    chosen_via_cache: bool = False,
    candidate_scores: list[dict[str, Any]] | None = None,
    created_at: datetime | None = None,
    **extras: Any,
) -> AdaptiveDecisionDoc:
    payload: dict[str, Any] = {
        "capability_id": capability_id,
        "winner_strategy_id": winner_strategy_id,
        "tenant_id": tenant_id,
        "privacy_mode": privacy_mode,
        "agent_mode": agent_mode,
        "fast_path_eligible": fast_path_eligible,
        "chosen_via_cache": chosen_via_cache,
        "candidate_scores": candidate_scores
        if candidate_scores is not None
        else [
            {
                "strategy_id": winner_strategy_id,
                "latency_fit": 0.9,
                "quality": 0.8,
                "resource_fit": 1.0,
                "residency": 1.0,
                "fast_path_bias": 0.0,
                "total": 0.85,
                "disqualified": False,
                "disqualified_reason": None,
            }
        ],
    }
    if created_at is not None:
        payload["created_at"] = created_at
    payload.update(extras)
    return AdaptiveDecisionDoc(**payload)


def _stores() -> list[AdaptiveDecisionStore]:
    return [InMemoryAdaptiveDecisionStore(), MongoAdaptiveDecisionStore(_FakeAsyncDB())]


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "store_factory",
    [
        lambda: InMemoryAdaptiveDecisionStore(),
        lambda: MongoAdaptiveDecisionStore(_FakeAsyncDB()),
    ],
    ids=["in_memory", "mongo"],
)
async def test_record_and_list(store_factory: Any) -> None:
    """Round-trip a single decision through both store impls."""
    store = store_factory()
    doc = _make_doc(capability_id="qa", winner_strategy_id="alpha")

    await store.record(doc)
    listed = await store.list_recent()

    assert len(listed) == 1
    assert listed[0].capability_id == "qa"
    assert listed[0].winner_strategy_id == "alpha"
    assert listed[0].decision_id == doc.decision_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "store_factory",
    [
        lambda: InMemoryAdaptiveDecisionStore(),
        lambda: MongoAdaptiveDecisionStore(_FakeAsyncDB()),
    ],
    ids=["in_memory", "mongo"],
)
async def test_list_recent_filters_by_capability(store_factory: Any) -> None:
    """``capability_id`` filter excludes other capabilities."""
    store = store_factory()
    await store.record(_make_doc(capability_id="qa", winner_strategy_id="alpha"))
    await store.record(_make_doc(capability_id="qa", winner_strategy_id="beta"))
    await store.record(_make_doc(capability_id="summary", winner_strategy_id="gamma"))

    qa_only = await store.list_recent(capability_id="qa")
    summary_only = await store.list_recent(capability_id="summary")

    assert {d.winner_strategy_id for d in qa_only} == {"alpha", "beta"}
    assert [d.winner_strategy_id for d in summary_only] == ["gamma"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "store_factory",
    [
        lambda: InMemoryAdaptiveDecisionStore(),
        lambda: MongoAdaptiveDecisionStore(_FakeAsyncDB()),
    ],
    ids=["in_memory", "mongo"],
)
async def test_list_recent_filters_by_winner_strategy_id(store_factory: Any) -> None:
    """``winner_strategy_id`` filter is honoured by both stores."""
    store = store_factory()
    await store.record(_make_doc(capability_id="qa", winner_strategy_id="alpha"))
    await store.record(_make_doc(capability_id="qa", winner_strategy_id="beta"))
    await store.record(_make_doc(capability_id="summary", winner_strategy_id="alpha"))

    alpha_only = await store.list_recent(winner_strategy_id="alpha")
    beta_only = await store.list_recent(winner_strategy_id="beta")

    assert len(alpha_only) == 2
    assert all(d.winner_strategy_id == "alpha" for d in alpha_only)
    assert len(beta_only) == 1
    assert beta_only[0].winner_strategy_id == "beta"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "store_factory",
    [
        lambda: InMemoryAdaptiveDecisionStore(),
        lambda: MongoAdaptiveDecisionStore(_FakeAsyncDB()),
    ],
    ids=["in_memory", "mongo"],
)
async def test_list_recent_orders_by_created_at_desc(store_factory: Any) -> None:
    """Most recent entries are returned first."""
    store = store_factory()
    now = datetime.now(timezone.utc)
    await store.record(
        _make_doc(winner_strategy_id="old", created_at=now - timedelta(days=2))
    )
    await store.record(
        _make_doc(winner_strategy_id="new", created_at=now)
    )

    listed = await store.list_recent()
    assert [d.winner_strategy_id for d in listed[:2]] == ["new", "old"]


@pytest.mark.asyncio
async def test_ensure_indexes_creates_ttl() -> None:
    """``ensure_indexes`` issues a TTL index on ``created_at``."""
    db = _FakeAsyncDB()
    store = MongoAdaptiveDecisionStore(db, ttl_days=7)
    await store.ensure_indexes()

    collection = db[ADAPTIVE_DECISION_COLLECTION_NAME]
    ttl_indexes = [
        (args, kwargs)
        for (args, kwargs) in collection.created_indexes
        if "expireAfterSeconds" in kwargs
    ]
    assert ttl_indexes, "TTL index missing"
    args, kwargs = ttl_indexes[0]
    assert args == ("created_at",)
    assert kwargs["expireAfterSeconds"] == 7 * 24 * 3600


@pytest.mark.asyncio
async def test_ensure_indexes_creates_capability_and_winner_indexes() -> None:
    """Capability + winner compound indexes are created idempotently."""
    db = _FakeAsyncDB()
    store = MongoAdaptiveDecisionStore(db)
    await store.ensure_indexes()
    # Second call must be a no-op (idempotent).
    await store.ensure_indexes()

    collection = db[ADAPTIVE_DECISION_COLLECTION_NAME]
    keys = [args[0] for (args, _kwargs) in collection.created_indexes if args]
    assert [("capability_id", 1), ("created_at", -1)] in keys
    assert [("winner_strategy_id", 1), ("created_at", -1)] in keys


@pytest.mark.asyncio
async def test_default_ttl_is_thirty_days() -> None:
    """The default TTL window matches the published 30-day contract."""
    db = _FakeAsyncDB()
    store = MongoAdaptiveDecisionStore(db)
    assert store.ttl_seconds == DEFAULT_ADAPTIVE_DECISION_TTL_DAYS * 24 * 3600


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "store_factory",
    [
        lambda: InMemoryAdaptiveDecisionStore(),
        lambda: MongoAdaptiveDecisionStore(_FakeAsyncDB()),
    ],
    ids=["in_memory", "mongo"],
)
async def test_extra_fields_preserved(store_factory: Any) -> None:
    """``extra='allow'`` lets future selector signals round-trip."""
    store = store_factory()
    doc = _make_doc(
        capability_id="qa",
        winner_strategy_id="alpha",
        future_signal_xyz=42,
        another_extra={"a": [1, 2, 3]},
    )
    await store.record(doc)
    listed = await store.list_recent()
    assert len(listed) == 1
    assert getattr(listed[0], "future_signal_xyz", None) == 42
    extra = getattr(listed[0], "another_extra", None)
    assert extra == {"a": [1, 2, 3]}


@pytest.mark.asyncio
async def test_mongo_store_rejects_none_db() -> None:
    """Passing ``db=None`` is a programmer error and surfaces immediately."""
    with pytest.raises(ValueError):
        MongoAdaptiveDecisionStore(None)
