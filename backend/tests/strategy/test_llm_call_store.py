"""Tests for :mod:`backend.agent.strategy.llm_call_store` (Task 89 / T1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from backend.agent.strategy.llm_call_store import (
    LLM_CALL_COLLECTION_NAME,
    InMemoryLLMCallStore,
    LLMCallDoc,
    LLMCallStore,
    MongoLLMCallStore,
)
from backend.tests.strategy._fakes import _FakeAsyncDB


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_doc(
    *,
    trace_id: str = "trace-1",
    node_id: str = "n_synthesize",
    node_type: str = "synthesize",
    model: str = "gemma3:4b",
    provider: str = "ollama",
    user_prompt: str = "What is the capital of France?",
    assistant_response: str = "Paris.",
    created_at: datetime | None = None,
    **extra: Any,
) -> LLMCallDoc:
    """Build a fully populated :class:`LLMCallDoc` for tests."""
    return LLMCallDoc(
        trace_id=trace_id,
        node_id=node_id,
        node_type=node_type,
        model=model,
        provider=provider,
        model_role="synthesizer_fast",
        system_prompt="You are a helpful assistant.",
        user_prompt=user_prompt,
        assistant_response=assistant_response,
        prompt_tokens=12,
        completion_tokens=4,
        total_tokens=16,
        latency_ms=42.5,
        temperature=0.2,
        max_tokens=128,
        cost_eur=0.0001,
        success=True,
        error=None,
        created_at=created_at or datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        **extra,
    )


def _make_store(kind: str) -> LLMCallStore:
    """Build a fresh store of the given ``kind`` (``memory`` / ``mongo``)."""
    if kind == "memory":
        return InMemoryLLMCallStore()
    if kind == "mongo":
        return MongoLLMCallStore(_FakeAsyncDB())
    raise ValueError(f"unknown store kind: {kind!r}")


# Parametrized fixture: every shared test exercises both backends so the
# in-memory and Mongo implementations stay behaviourally equivalent.
STORE_KINDS = ("memory", "mongo")


# ─────────────────────────────────────────────────────────────────────────────
# Shared store contract
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_save_and_get_round_trip(store_kind: str) -> None:
    """``save`` followed by ``get`` returns an equivalent document."""
    store = _make_store(store_kind)
    doc = _make_doc(
        trace_id="trace-roundtrip",
        node_id="n_synth",
        user_prompt="A" * 5000,  # long prompt — full text must round-trip
        assistant_response="B" * 8000,  # long response — full text must round-trip
    )

    await store.save(doc)

    fetched = await store.get(doc.call_id)
    assert fetched is not None
    assert fetched.call_id == doc.call_id
    assert fetched.trace_id == "trace-roundtrip"
    assert fetched.node_id == "n_synth"
    assert fetched.node_type == doc.node_type
    assert fetched.model == doc.model
    assert fetched.provider == doc.provider
    assert fetched.model_role == doc.model_role
    assert fetched.system_prompt == doc.system_prompt
    assert fetched.user_prompt == "A" * 5000
    assert fetched.assistant_response == "B" * 8000
    assert fetched.prompt_tokens == 12
    assert fetched.completion_tokens == 4
    assert fetched.total_tokens == 16
    assert fetched.latency_ms == pytest.approx(42.5)
    assert fetched.temperature == pytest.approx(0.2)
    assert fetched.max_tokens == 128
    assert fetched.cost_eur == pytest.approx(0.0001)
    assert fetched.success is True
    assert fetched.error is None
    assert fetched.created_at == doc.created_at


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_list_by_trace(store_kind: str) -> None:
    """``list_by_trace`` returns only docs whose ``trace_id`` matches."""
    store = _make_store(store_kind)
    base = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)

    target_ids = []
    for i in range(3):
        d = _make_doc(
            trace_id="trace-target",
            node_id=f"node-{i}",
            created_at=base + timedelta(seconds=i),
        )
        target_ids.append(d.call_id)
        await store.save(d)
    other = _make_doc(
        trace_id="trace-other",
        node_id="node-other",
        created_at=base + timedelta(seconds=99),
    )
    await store.save(other)

    listed = await store.list_by_trace("trace-target")
    assert len(listed) == 3
    assert {d.call_id for d in listed} == set(target_ids)
    # Ordered ascending by created_at.
    assert [d.created_at for d in listed] == sorted(d.created_at for d in listed)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_list_by_node(store_kind: str) -> None:
    """``list_by_node`` filters by ``(trace_id, node_id)`` pair."""
    store = _make_store(store_kind)
    base = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)

    a1 = _make_doc(
        trace_id="t1", node_id="node_a", created_at=base + timedelta(seconds=1)
    )
    a2 = _make_doc(
        trace_id="t1", node_id="node_a", created_at=base + timedelta(seconds=2)
    )
    b1 = _make_doc(
        trace_id="t1", node_id="node_b", created_at=base + timedelta(seconds=3)
    )
    other = _make_doc(
        trace_id="t2", node_id="node_a", created_at=base + timedelta(seconds=4)
    )
    for d in (a1, a2, b1, other):
        await store.save(d)

    listed = await store.list_by_node("t1", "node_a")
    assert len(listed) == 2
    assert {d.call_id for d in listed} == {a1.call_id, a2.call_id}
    # Oldest first.
    assert listed[0].created_at < listed[1].created_at


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", STORE_KINDS)
async def test_list_by_trace_respects_limit(store_kind: str) -> None:
    """``list_by_trace`` honours the ``limit`` cap."""
    store = _make_store(store_kind)
    base = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)

    for i in range(10):
        await store.save(
            _make_doc(
                trace_id="trace-limit",
                node_id=f"n{i}",
                created_at=base + timedelta(seconds=i),
            )
        )

    listed = await store.list_by_trace("trace-limit", limit=3)
    assert len(listed) == 3
    # Limit picks the oldest 3 since results are sorted asc.
    assert [d.node_id for d in listed] == ["n0", "n1", "n2"]


# ─────────────────────────────────────────────────────────────────────────────
# Mongo-only index behaviour
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_indexes_creates_ttl() -> None:
    """``ensure_indexes`` registers a TTL index on ``created_at``.

    The configured ``ttl_seconds`` must propagate to ``create_index``
    as ``expireAfterSeconds`` and the index must be created with the
    stable ``strategy_llm_calls_ttl`` name.
    """
    db = _FakeAsyncDB()
    store = MongoLLMCallStore(db, ttl_seconds=12345)

    await store.ensure_indexes()

    coll = db[LLM_CALL_COLLECTION_NAME]
    ttl_calls = [
        (args, kwargs)
        for (args, kwargs) in coll.created_indexes  # type: ignore[attr-defined]
        if "expireAfterSeconds" in kwargs
    ]
    assert len(ttl_calls) == 1, f"expected one TTL index, got {ttl_calls!r}"
    args, kwargs = ttl_calls[0]
    assert args == ("created_at",)
    assert kwargs["expireAfterSeconds"] == 12345
    assert kwargs.get("name") == "strategy_llm_calls_ttl"

    # Compound (trace_id, node_id) index also registered.
    compound_calls = [
        args
        for (args, kwargs) in coll.created_indexes  # type: ignore[attr-defined]
        if "expireAfterSeconds" not in kwargs
    ]
    assert any(
        a == ([("trace_id", 1), ("node_id", 1)],) for a in compound_calls
    ), f"missing (trace_id, node_id) compound index: {compound_calls!r}"


@pytest.mark.asyncio
async def test_ensure_indexes_idempotent() -> None:
    """Repeated ``ensure_indexes`` calls do not raise or duplicate indexes."""
    db = _FakeAsyncDB()
    store = MongoLLMCallStore(db, ttl_seconds=3600)

    await store.ensure_indexes()
    await store.ensure_indexes()
    await store.ensure_indexes()

    coll = db[LLM_CALL_COLLECTION_NAME]
    ttl_calls = [
        kwargs
        for (_, kwargs) in coll.created_indexes  # type: ignore[attr-defined]
        if "expireAfterSeconds" in kwargs
    ]
    # The internal _indexes_ready flag short-circuits subsequent calls,
    # so each index is created exactly once.
    assert len(ttl_calls) == 1
    assert ttl_calls[0]["expireAfterSeconds"] == 3600


# ─────────────────────────────────────────────────────────────────────────────
# Schema validation
# ─────────────────────────────────────────────────────────────────────────────


def test_doc_validation() -> None:
    """``LLMCallDoc`` rejects payloads missing required fields."""
    # Empty payload — every required field missing.
    with pytest.raises(ValidationError):
        LLMCallDoc.model_validate({})

    base: dict[str, Any] = {
        "trace_id": "t",
        "node_id": "n",
        "node_type": "synthesize",
        "model": "m",
        "provider": "p",
        "user_prompt": "u",
    }

    # Missing trace_id.
    bad = dict(base)
    bad.pop("trace_id")
    with pytest.raises(ValidationError):
        LLMCallDoc.model_validate(bad)

    # Missing node_id.
    bad = dict(base)
    bad.pop("node_id")
    with pytest.raises(ValidationError):
        LLMCallDoc.model_validate(bad)

    # Missing user_prompt.
    bad = dict(base)
    bad.pop("user_prompt")
    with pytest.raises(ValidationError):
        LLMCallDoc.model_validate(bad)

    # Minimal valid payload populates the auto-generated fields.
    doc = LLMCallDoc.model_validate(base)
    assert doc.call_id  # UUID4 default factory
    assert doc.assistant_response == ""
    assert doc.success is True
    assert doc.error is None
    assert doc.latency_ms == 0.0


def test_extra_fields_allowed() -> None:
    """``LLMCallDoc`` preserves unknown keys for forward compatibility."""
    payload: dict[str, Any] = {
        "trace_id": "t",
        "node_id": "n",
        "node_type": "synthesize",
        "model": "m",
        "provider": "p",
        "user_prompt": "u",
        # Forward-compat fields the future executor may emit.
        "future_field": "future_value",
        "future_metric": 3.14,
    }

    doc = LLMCallDoc.model_validate(payload)
    dumped = doc.model_dump()
    assert dumped["future_field"] == "future_value"
    assert dumped["future_metric"] == pytest.approx(3.14)
