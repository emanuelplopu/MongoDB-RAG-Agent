"""Tests for :mod:`backend.agent.strategy.run_trace_store` (Task 68)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.agent.strategy.run_trace_store import (
    InMemoryRunTraceStore,
    MongoRunTraceStore,
    RUN_TRACE_COLLECTION_NAME,
    RunTraceDoc,
)
from backend.tests.strategy._fakes import _FakeAsyncDB


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_doc(
    *,
    trace_id: str = "trace-1",
    strategy_id: str = "strategy-A",
    status: str = "success",
    started_at: datetime | None = None,
    duration_ms: float = 123.4,
) -> RunTraceDoc:
    started = started_at or datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    completed = started + timedelta(milliseconds=duration_ms)
    return RunTraceDoc(
        trace_id=trace_id,
        run_id=f"run-{trace_id}",
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        capability_id="qa",
        tenant_id="recallhub",
        status=status,  # type: ignore[arg-type]
        started_at=started,
        completed_at=completed,
        duration_ms=duration_ms,
        node_outputs=[
            {
                "node_id": "n1",
                "node_type": "retrieve",
                "status": "success",
                "duration_ms": 50.0,
                "error": None,
            },
            {
                "node_id": "n2",
                "node_type": "synthesize",
                "status": "error",
                "duration_ms": 73.4,
                "error": "boom",
            },
        ],
        halt_reason=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# RunTraceDoc validation
# ─────────────────────────────────────────────────────────────────────────────


def test_run_trace_doc_rejects_missing_required_fields() -> None:
    """``RunTraceDoc`` must require trace_id/strategy_id/status/timestamps."""
    with pytest.raises(ValidationError):
        RunTraceDoc.model_validate({})  # all required fields missing

    base = {
        "trace_id": "t",
        "strategy_id": "s",
        "status": "success",
        "started_at": datetime.now(timezone.utc),
        "completed_at": datetime.now(timezone.utc),
    }
    # status outside the literal must fail.
    with pytest.raises(ValidationError):
        RunTraceDoc.model_validate({**base, "status": "halted"})


def test_run_trace_doc_accepts_minimal_required_payload() -> None:
    """Minimal payload (only the strictly required fields) must validate."""
    now = datetime.now(timezone.utc)
    doc = RunTraceDoc(
        trace_id="t1",
        strategy_id="strategy-X",
        status="success",
        started_at=now,
        completed_at=now,
    )
    assert doc.run_id is None
    assert doc.duration_ms == 0.0
    assert doc.node_outputs == []
    assert doc.created_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# MongoRunTraceStore (against _FakeAsyncDB)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mongo_run_trace_store_save_and_get_round_trip() -> None:
    """``save`` followed by ``get`` returns an equivalent document."""
    db = _FakeAsyncDB()
    store = MongoRunTraceStore(db)
    doc = _make_doc(trace_id="trace-roundtrip")

    saved = await store.save(doc)
    assert saved.trace_id == "trace-roundtrip"

    fetched = await store.get("trace-roundtrip")
    assert fetched is not None
    assert fetched.trace_id == doc.trace_id
    assert fetched.run_id == doc.run_id
    assert fetched.strategy_id == doc.strategy_id
    assert fetched.status == doc.status
    assert fetched.duration_ms == pytest.approx(doc.duration_ms)
    assert fetched.started_at == doc.started_at
    assert fetched.completed_at == doc.completed_at
    assert len(fetched.node_outputs) == 2
    assert fetched.node_outputs[1]["error"] == "boom"


@pytest.mark.asyncio
async def test_mongo_run_trace_store_list_recent_filters() -> None:
    """``list_recent`` honours time window, strategy_id, and status filters."""
    db = _FakeAsyncDB()
    store = MongoRunTraceStore(db)

    base = datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc)
    docs = [
        _make_doc(
            trace_id="t-a-success",
            strategy_id="strat-a",
            status="success",
            started_at=base + timedelta(hours=1),
        ),
        _make_doc(
            trace_id="t-a-failed",
            strategy_id="strat-a",
            status="failed",
            started_at=base + timedelta(hours=2),
        ),
        _make_doc(
            trace_id="t-b-success",
            strategy_id="strat-b",
            status="success",
            started_at=base + timedelta(hours=3),
        ),
        _make_doc(
            trace_id="t-out-of-window",
            strategy_id="strat-a",
            status="success",
            started_at=base + timedelta(days=10),
        ),
    ]
    for d in docs:
        await store.save(d)

    # Window filter: drop the out-of-window entry.
    in_window = await store.list_recent(
        since=base, until=base + timedelta(hours=24)
    )
    trace_ids = {d.trace_id for d in in_window}
    assert trace_ids == {"t-a-success", "t-a-failed", "t-b-success"}

    # strategy_id filter.
    only_a = await store.list_recent(
        since=base,
        until=base + timedelta(hours=24),
        strategy_id="strat-a",
    )
    assert {d.trace_id for d in only_a} == {"t-a-success", "t-a-failed"}

    # status filter.
    only_failed = await store.list_recent(
        since=base,
        until=base + timedelta(hours=24),
        status="failed",
    )
    assert [d.trace_id for d in only_failed] == ["t-a-failed"]

    # limit caps the result count.
    limited = await store.list_recent(
        since=base,
        until=base + timedelta(hours=24),
        limit=1,
    )
    assert len(limited) == 1


@pytest.mark.asyncio
async def test_mongo_run_trace_store_save_is_idempotent_on_trace_id() -> None:
    """Re-saving the same ``trace_id`` overwrites instead of duplicating."""
    db = _FakeAsyncDB()
    store = MongoRunTraceStore(db)

    doc = _make_doc(trace_id="t-idem", status="success")
    await store.save(doc)

    # Re-save with the same trace_id but a different status.
    doc2 = _make_doc(trace_id="t-idem", status="failed")
    await store.save(doc2)

    fetched = await store.get("t-idem")
    assert fetched is not None
    assert fetched.status == "failed"

    # Underlying collection must contain a single doc keyed by trace_id.
    stored_docs = db[RUN_TRACE_COLLECTION_NAME]._docs  # type: ignore[attr-defined]
    matches = [d for d in stored_docs if d.get("trace_id") == "t-idem"]
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_mongo_run_trace_store_ensure_indexes_is_idempotent() -> None:
    """``ensure_indexes`` may be called repeatedly without raising."""
    db = _FakeAsyncDB()
    store = MongoRunTraceStore(db)

    await store.ensure_indexes()
    await store.ensure_indexes()

    coll = db[RUN_TRACE_COLLECTION_NAME]
    # First call records the three indexes (compound, unique trace_id,
    # TTL on created_at); subsequent calls are short-circuited by the
    # internal flag, so total index creations remains <= 3.
    assert len(coll.created_indexes) <= 3  # type: ignore[attr-defined]
    assert len(coll.created_indexes) >= 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_ensure_indexes_creates_ttl_index() -> None:
    """``ensure_indexes`` registers a TTL index on ``created_at``.

    The TTL value passed via ``ttl_seconds`` must propagate to the
    underlying ``create_index`` call as ``expireAfterSeconds``.
    """
    db = _FakeAsyncDB()
    store = MongoRunTraceStore(db, ttl_seconds=86400)

    await store.ensure_indexes()

    coll = db[RUN_TRACE_COLLECTION_NAME]
    ttl_calls = [
        (args, kwargs)
        for (args, kwargs) in coll.created_indexes  # type: ignore[attr-defined]
        if "expireAfterSeconds" in kwargs
    ]
    assert len(ttl_calls) == 1, f"expected one TTL index, got {ttl_calls!r}"
    args, kwargs = ttl_calls[0]
    assert args == ("created_at",)
    assert kwargs["expireAfterSeconds"] == 86400
    assert kwargs.get("name") == "strategy_runs_ttl"


@pytest.mark.asyncio
async def test_ensure_indexes_default_ttl_is_7_days() -> None:
    """Default constructor uses a 7-day TTL (604800 seconds)."""
    db = _FakeAsyncDB()
    store = MongoRunTraceStore(db)

    await store.ensure_indexes()

    coll = db[RUN_TRACE_COLLECTION_NAME]
    ttl_calls = [
        kwargs
        for (_, kwargs) in coll.created_indexes  # type: ignore[attr-defined]
        if "expireAfterSeconds" in kwargs
    ]
    assert len(ttl_calls) == 1
    assert ttl_calls[0]["expireAfterSeconds"] == 7 * 24 * 3600
    assert ttl_calls[0]["expireAfterSeconds"] == 604800


@pytest.mark.asyncio
async def test_ensure_indexes_ttl_idempotent_under_repeated_calls() -> None:
    """Repeated ``ensure_indexes`` calls do not duplicate the TTL index."""
    db = _FakeAsyncDB()
    store = MongoRunTraceStore(db, ttl_seconds=3600)

    await store.ensure_indexes()
    await store.ensure_indexes()
    await store.ensure_indexes()

    coll = db[RUN_TRACE_COLLECTION_NAME]
    ttl_calls = [
        kwargs
        for (_, kwargs) in coll.created_indexes  # type: ignore[attr-defined]
        if "expireAfterSeconds" in kwargs
    ]
    # The internal _indexes_ready flag short-circuits subsequent calls,
    # so the TTL index must be created exactly once.
    assert len(ttl_calls) == 1
    assert ttl_calls[0]["expireAfterSeconds"] == 3600


@pytest.mark.asyncio
async def test_mongo_run_trace_store_get_returns_none_for_unknown() -> None:
    """``get`` on an unknown trace_id returns ``None`` (no exception)."""
    db = _FakeAsyncDB()
    store = MongoRunTraceStore(db)
    assert await store.get("does-not-exist") is None


# ─────────────────────────────────────────────────────────────────────────────
# InMemoryRunTraceStore parity
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_in_memory_run_trace_store_round_trip_and_filter() -> None:
    """In-memory store mirrors Mongo semantics for save/get/list_recent."""
    store = InMemoryRunTraceStore()
    base = datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc)

    await store.save(_make_doc(trace_id="m1", strategy_id="x", started_at=base))
    await store.save(
        _make_doc(
            trace_id="m2",
            strategy_id="x",
            status="failed",
            started_at=base + timedelta(hours=1),
        )
    )

    fetched = await store.get("m1")
    assert fetched is not None
    assert fetched.strategy_id == "x"

    failed = await store.list_recent(
        since=base, until=base + timedelta(hours=2), status="failed"
    )
    assert [d.trace_id for d in failed] == ["m2"]

    # ensure_indexes is a no-op on the in-memory store.
    await store.ensure_indexes()
