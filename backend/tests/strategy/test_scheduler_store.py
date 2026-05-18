"""Tests for :mod:`backend.agent.strategy.scheduler_store`.

Covers CRUD parity between the in-memory and a minimal async Mongo
collection fake, partial ``update_runtime_state`` semantics, and the
``list_due`` query (active + un-paused only). The fake collection is
shared with the spec-store tests via :mod:`backend.tests.strategy._fakes`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.agent.strategy.scheduler_store import (
    SCHEDULE_COLLECTION_NAME,
    InMemorySchedulerStore,
    MongoSchedulerStore,
    SchedulerStore,
    _doc_to_schedule,
    _is_due,
    _matches,
    _schedule_to_doc,
)
from backend.scheduler.models import StrategySchedule
from backend.tests.strategy._fakes import _FakeAsyncDB

# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════


def _make_schedule(
    schedule_id: str = "sched-1",
    *,
    name: str = "Nightly",
    paused: bool = False,
    status: str = "active",
    next_run_at: Any = None,
    candidate_strategy_ids: list[str] | None = None,
) -> StrategySchedule:
    return StrategySchedule(
        id=schedule_id,
        name=name,
        cron="0 22 * * 1-5",
        timezone="Europe/Vienna",
        status=status,
        paused=paused,
        next_run_at=next_run_at,
        candidate_strategy_ids=candidate_strategy_ids or [],
    )


@pytest.fixture
def in_memory_store() -> InMemorySchedulerStore:
    return InMemorySchedulerStore()


@pytest.fixture
def fake_db() -> _FakeAsyncDB:
    return _FakeAsyncDB()


@pytest.fixture
def mongo_store(fake_db: _FakeAsyncDB) -> MongoSchedulerStore:
    return MongoSchedulerStore(fake_db)


@pytest.fixture(params=["in_memory", "mongo"])
def store(
    request: pytest.FixtureRequest,
    in_memory_store: InMemorySchedulerStore,
    mongo_store: MongoSchedulerStore,
) -> SchedulerStore:
    """Parametrize across both store backends to exercise CRUD parity."""
    return in_memory_store if request.param == "in_memory" else mongo_store


# ══════════════════════════════════════════════════════════════════════════════
# CRUD parity
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_unknown_returns_none(store: SchedulerStore) -> None:
    assert await store.get("nope") is None


@pytest.mark.asyncio
async def test_upsert_then_get_roundtrips(store: SchedulerStore) -> None:
    schedule = _make_schedule("sched-a", name="Alpha")

    persisted = await store.upsert(schedule)
    assert persisted.id == "sched-a"
    assert persisted.name == "Alpha"

    fetched = await store.get("sched-a")
    assert fetched is not None
    assert fetched.id == "sched-a"
    assert fetched.name == "Alpha"
    assert fetched.status == "active"


@pytest.mark.asyncio
async def test_upsert_replaces_existing(store: SchedulerStore) -> None:
    await store.upsert(_make_schedule("sched-b", name="Original"))
    await store.upsert(_make_schedule("sched-b", name="Updated"))

    fetched = await store.get("sched-b")
    assert fetched is not None
    assert fetched.name == "Updated"

    listed = await store.list()
    # Replacement must not duplicate the row.
    assert len([s for s in listed if s.id == "sched-b"]) == 1


@pytest.mark.asyncio
async def test_list_filters_by_status(store: SchedulerStore) -> None:
    await store.upsert(_make_schedule("a", status="active"))
    await store.upsert(_make_schedule("d", status="disabled"))

    actives = await store.list({"status": "active"})
    assert {s.id for s in actives} == {"a"}

    disabled = await store.list({"status": "disabled"})
    assert {s.id for s in disabled} == {"d"}


@pytest.mark.asyncio
async def test_delete_removes_row(store: SchedulerStore) -> None:
    await store.upsert(_make_schedule("sched-x"))
    assert await store.delete("sched-x") is True
    assert await store.get("sched-x") is None
    # Idempotent: deleting again is a clean no-op.
    assert await store.delete("sched-x") is False


# ══════════════════════════════════════════════════════════════════════════════
# update_runtime_state
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_update_runtime_state_partial(store: SchedulerStore) -> None:
    base = _make_schedule("rt-1")
    await store.upsert(base)

    now = datetime.now(timezone.utc)
    updated = await store.update_runtime_state(
        "rt-1",
        last_run_at=now,
        last_run_status="completed",
    )
    assert updated is not None
    assert updated.last_run_at == now
    assert updated.last_run_status == "completed"
    # Untouched fields preserved.
    assert updated.paused is False
    assert updated.consecutive_failures == 0
    assert updated.circuit_breaker_state is None


@pytest.mark.asyncio
async def test_update_runtime_state_pause_and_clear(store: SchedulerStore) -> None:
    await store.upsert(_make_schedule("rt-2"))

    paused = await store.update_runtime_state(
        "rt-2", paused=True, paused_reason="manual"
    )
    assert paused is not None
    assert paused.paused is True
    assert paused.paused_reason == "manual"

    cleared = await store.update_runtime_state(
        "rt-2", paused=False, paused_reason=""
    )
    assert cleared is not None
    assert cleared.paused is False
    # Empty-string sentinel clears the reason.
    assert cleared.paused_reason is None


@pytest.mark.asyncio
async def test_update_runtime_state_records_circuit_breaker(
    store: SchedulerStore,
) -> None:
    await store.upsert(_make_schedule("rt-3"))

    out = await store.update_runtime_state(
        "rt-3",
        circuit_breaker="open",
        consecutive_failures=4,
    )
    assert out is not None
    assert out.circuit_breaker_state == "open"
    assert out.consecutive_failures == 4


@pytest.mark.asyncio
async def test_update_runtime_state_unknown_returns_none(
    store: SchedulerStore,
) -> None:
    assert await store.update_runtime_state("missing", paused=True) is None


# ══════════════════════════════════════════════════════════════════════════════
# list_due
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_list_due_returns_only_active_unpaused_due(
    store: SchedulerStore,
) -> None:
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=1)
    future = now + timedelta(hours=1)

    # Eligible: active, unpaused, due.
    await store.upsert(_make_schedule("due", next_run_at=past))
    # Ineligible: paused.
    await store.upsert(
        _make_schedule("paused", next_run_at=past, paused=True)
    )
    # Ineligible: not due yet.
    await store.upsert(_make_schedule("future", next_run_at=future))
    # Ineligible: disabled.
    await store.upsert(
        _make_schedule("disabled", next_run_at=past, status="disabled")
    )
    # Ineligible: no next_run_at.
    await store.upsert(_make_schedule("no-next", next_run_at=None))

    due = await store.list_due(now)
    assert {s.id for s in due} == {"due"}


@pytest.mark.asyncio
async def test_list_due_handles_naive_next_run_at(
    store: SchedulerStore,
) -> None:
    naive_past = datetime.utcnow() - timedelta(minutes=5)
    await store.upsert(_make_schedule("naive", next_run_at=naive_past))

    due = await store.list_due(datetime.now(timezone.utc))
    assert {s.id for s in due} == {"naive"}


# ══════════════════════════════════════════════════════════════════════════════
# Mongo-specific behaviour
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mongo_store_requires_db() -> None:
    with pytest.raises(ValueError):
        MongoSchedulerStore(None)


@pytest.mark.asyncio
async def test_mongo_ensure_indexes_is_idempotent(
    mongo_store: MongoSchedulerStore, fake_db: _FakeAsyncDB
) -> None:
    await mongo_store.ensure_indexes()
    await mongo_store.ensure_indexes()  # second call must not double-up
    collection = fake_db[SCHEDULE_COLLECTION_NAME]
    # Two distinct indexes registered exactly once each.
    assert len(collection.created_indexes) == 2


@pytest.mark.asyncio
async def test_mongo_persists_schedule_id_field(
    mongo_store: MongoSchedulerStore, fake_db: _FakeAsyncDB
) -> None:
    await mongo_store.upsert(_make_schedule("mid-1"))

    raw = fake_db[SCHEDULE_COLLECTION_NAME]._docs
    assert len(raw) == 1
    assert raw[0]["schedule_id"] == "mid-1"
    assert raw[0]["id"] == "mid-1"


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def test_schedule_to_doc_round_trip() -> None:
    schedule = _make_schedule("rt-helper", name="Helper")
    doc = _schedule_to_doc(schedule)
    assert doc["schedule_id"] == "rt-helper"

    rebuilt = _doc_to_schedule(doc)
    assert rebuilt is not None
    assert rebuilt.id == "rt-helper"
    assert rebuilt.name == "Helper"


def test_doc_to_schedule_handles_legacy_doc_without_id() -> None:
    legacy = {"schedule_id": "legacy", "name": "Legacy schedule"}
    rebuilt = _doc_to_schedule(legacy)
    assert rebuilt is not None
    assert rebuilt.id == "legacy"


def test_doc_to_schedule_handles_corrupt_doc() -> None:
    # ``cron`` constraint violations should be tolerated rather than crashing.
    rebuilt = _doc_to_schedule({"schedule_id": "x", "name": object()})
    assert rebuilt is None


def test_is_due_respects_paused_and_status() -> None:
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=1)
    assert _is_due(_make_schedule("a", next_run_at=past), now) is True
    assert (
        _is_due(_make_schedule("a", next_run_at=past, paused=True), now)
        is False
    )
    assert (
        _is_due(_make_schedule("a", next_run_at=past, status="disabled"), now)
        is False
    )


def test_matches_helper_matches_top_level_fields() -> None:
    schedule = _make_schedule("m-1", status="active")
    assert _matches(schedule, {"status": "active"}) is True
    assert _matches(schedule, {"status": "disabled"}) is False
    # Empty filter \u2192 always matches.
    assert _matches(schedule, {}) is True


# ═════════════════════════════════════════════════════════════════════════════
# Task 83: Phase 6 dispatch metadata fields
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_schedule_round_trip_preserves_new_fields(
    store: SchedulerStore,
) -> None:
    """All four Phase 6 dispatch fields must round-trip through both stores."""
    schedule = StrategySchedule(
        id="phase6-rt",
        name="Phase6 RT",
        cron="0 22 * * *",
        timezone="UTC",
        mode="grid",
        tenant="recallhub",
        profile_key="rag_test_law",
        candidate_generator_id="grid-default",
    )

    await store.upsert(schedule)
    fetched = await store.get("phase6-rt")

    assert fetched is not None
    assert fetched.mode == "grid"
    assert fetched.tenant == "recallhub"
    assert fetched.profile_key == "rag_test_law"
    assert fetched.candidate_generator_id == "grid-default"


@pytest.mark.asyncio
async def test_legacy_schedule_doc_without_new_fields_still_loads(
    fake_db: _FakeAsyncDB,
    mongo_store: MongoSchedulerStore,
) -> None:
    """Legacy persisted docs (predating Task 83) must still load cleanly.

    Inserts a hand-shaped document missing the four Phase 6 fields
    directly into the fake collection and asserts the store fetches it
    with the new fields defaulting to ``None`` rather than raising a
    validation error.
    """
    legacy_doc = {
        "id": "legacy-pre-p6",
        "schedule_id": "legacy-pre-p6",
        "name": "Legacy Nightly",
        "cron": "0 22 * * 1-5",
        "timezone": "Europe/Vienna",
        "status": "active",
        "paused": False,
        "candidate_strategy_ids": ["old-strat"],
    }
    collection = fake_db[SCHEDULE_COLLECTION_NAME]
    await collection.insert_one(legacy_doc)

    fetched = await mongo_store.get("legacy-pre-p6")
    assert fetched is not None
    assert fetched.id == "legacy-pre-p6"
    assert fetched.name == "Legacy Nightly"
    assert fetched.mode is None
    assert fetched.tenant is None
    assert fetched.profile_key is None
    assert fetched.candidate_generator_id is None
