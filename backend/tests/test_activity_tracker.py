"""Tests for :mod:`backend.services.activity_tracker` (Task 86 / F9)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.services.activity_tracker import (
    CHAT_ACTIVITY_COLLECTION_NAME,
    ChatActivityTracker,
)
from backend.tests.strategy._fakes import _FakeAsyncDB


# ─────────────────────────────────────────────────────────────────────────────
# in_process mode
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_in_process_mark_and_read() -> None:
    """mark_active() then a short sleep → seconds_since_last_active() < 1."""
    tracker = ChatActivityTracker(mode="in_process", host_id="h-1")
    await tracker.mark_active()
    await asyncio.sleep(0.1)
    secs = await tracker.seconds_since_last_active()
    assert secs is not None
    assert secs < 1.0
    assert secs >= 0.0


@pytest.mark.asyncio
async def test_in_process_returns_none_before_mark() -> None:
    """A fresh tracker reports None until the first mark_active() call."""
    tracker = ChatActivityTracker(mode="in_process", host_id="h-2")
    assert await tracker.seconds_since_last_active() is None


# ─────────────────────────────────────────────────────────────────────────────
# mongo mode
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mongo_mode_round_trip() -> None:
    """mark_active() upserts a doc; seconds_since_last_active reads it back."""
    db = _FakeAsyncDB()
    tracker = ChatActivityTracker(db=db, mode="mongo", host_id="host-A")

    await tracker.mark_active()
    # Persisted under the canonical collection / doc_id.
    docs = list(db[CHAT_ACTIVITY_COLLECTION_NAME]._docs)
    assert len(docs) == 1
    assert docs[0]["_id"] == "chat_activity:host-A"
    assert docs[0]["host_id"] == "host-A"

    secs = await tracker.seconds_since_last_active()
    assert secs is not None
    assert secs < 1.0


@pytest.mark.asyncio
async def test_mongo_mode_ensure_indexes_creates_ttl() -> None:
    """ensure_indexes() registers a 24h TTL on last_active_at."""
    db = _FakeAsyncDB()
    tracker = ChatActivityTracker(db=db, mode="mongo", host_id="host-B")

    await tracker.ensure_indexes()
    # Idempotent — second call must not double-create.
    await tracker.ensure_indexes()

    created = db[CHAT_ACTIVITY_COLLECTION_NAME].created_indexes
    assert len(created) == 1
    args, kwargs = created[0]
    assert args == ("last_active_at",)
    assert kwargs.get("expireAfterSeconds") == 24 * 3600


@pytest.mark.asyncio
async def test_multiple_workers_share_state_via_mongo() -> None:
    """Two trackers with the same host_id see each other's marks."""
    db = _FakeAsyncDB()
    worker_a = ChatActivityTracker(db=db, mode="mongo", host_id="shared-host")
    worker_b = ChatActivityTracker(db=db, mode="mongo", host_id="shared-host")

    # B has never marked locally — initial read must be None.
    assert await worker_b.seconds_since_last_active() is None

    # A marks; B sees it via the shared Mongo doc, even though it never
    # called mark_active() itself.
    await worker_a.mark_active()
    secs = await worker_b.seconds_since_last_active()
    assert secs is not None
    assert secs < 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Robustness
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mongo_mode_falls_back_to_in_process_on_db_failure() -> None:
    """If Mongo upsert raises, the in-process slot still works."""

    class _BoomCollection:
        async def update_one(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("mongo down")

        async def find_one(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("mongo down")

        async def create_index(self, *args: Any, **kwargs: Any) -> str:
            raise RuntimeError("mongo down")

    class _BoomDB:
        def __getitem__(self, _name: str) -> _BoomCollection:
            return _BoomCollection()

    tracker = ChatActivityTracker(db=_BoomDB(), mode="mongo", host_id="h-x")
    await tracker.ensure_indexes()  # must not raise
    await tracker.mark_active()  # must not raise

    secs = await tracker.seconds_since_last_active()
    assert secs is not None
    assert secs < 1.0


def test_invalid_mode_rejected() -> None:
    """Constructor refuses unknown modes early."""
    with pytest.raises(ValueError):
        ChatActivityTracker(mode="ephemeral")  # type: ignore[arg-type]


def test_invalid_ttl_rejected() -> None:
    """Constructor refuses non-positive TTLs."""
    with pytest.raises(ValueError):
        ChatActivityTracker(mode="in_process", ttl_seconds=0)
