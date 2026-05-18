"""Tests for the runtime profiler stack (Phase 6 / Task 70).

Covers:

* :class:`RuntimeModelProfile` validation (required fields, status literal).
* :class:`MongoRuntimeProfileStore` CRUD round-trip + idempotent indexes.
* :class:`RuntimeProfiler.run_test` happy path with all timing fields.
* Failure path: provider exception → ``success=False``, ``error`` set.
* ``parallel_workers`` test reports degraded throughput vs single short_rag.
* GPU metrics fallback when ``nvidia-smi`` is mocked away.

Real LLM calls are forbidden — every test injects a deterministic
mock invoker.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest
from pydantic import ValidationError

from backend.agent.strategy.runtime_profile_store import (
    InMemoryRuntimeProfileStore,
    MongoRuntimeProfileStore,
    RUNTIME_PROFILE_COLLECTION_NAME,
    RuntimeModelProfile,
)
from backend.services.runtime_profiler import (
    ParallelWorkersConfig,
    RuntimeProfiler,
    build_standard_prompt,
)
from backend.tests.strategy._fakes import _FakeAsyncDB


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_profile(**overrides: Any) -> RuntimeModelProfile:
    base: dict[str, Any] = dict(
        id="profile-1",
        host_id="host-A",
        tenant="recallhub",
        model="gemma3:4b",
        provider="ollama",
        quantization="Q4_K_M",
        test_name="short_rag",
        context_tokens=2000,
        output_tokens=500,
        model_load_ms=200,
        first_token_ms=120,
        total_latency_ms=4000,
        tokens_per_second=125.0,
        prompt_tokens_per_second=2000.0,
        generation_tokens_per_second=125.0,
        ram_peak_gb=4.5,
        vram_peak_gb=6.2,
        cpu_avg_pct=45.0,
        gpu_avg_pct=72.0,
        success=True,
        error=None,
    )
    base.update(overrides)
    return RuntimeModelProfile(**base)


def _build_invoker(
    *,
    latency_per_call_s: float = 0.05,
    output_tokens: int = 500,
    first_token_ms: Optional[int] = 30,
    raise_exc: Optional[BaseException] = None,
) -> Any:
    """Return a deterministic async provider invoker for tests."""

    async def _invoke(
        *,
        model: str,
        provider: str,
        prompt: str,
        max_tokens: int,
        **_: Any,
    ) -> dict[str, Any]:
        await asyncio.sleep(latency_per_call_s)
        if raise_exc is not None:
            raise raise_exc
        return {
            "output_tokens": output_tokens,
            "first_token_ms": first_token_ms,
            "model_load_ms": 50,
            "prompt_tokens_per_second": 4000.0,
            "generation_tokens_per_second": 200.0,
            "quantization": "Q4_K_M",
        }

    return _invoke


# ─────────────────────────────────────────────────────────────────────────────
# RuntimeModelProfile validation
# ─────────────────────────────────────────────────────────────────────────────


def test_runtime_model_profile_rejects_missing_required_fields() -> None:
    """Missing required fields must raise ``ValidationError``."""
    with pytest.raises(ValidationError):
        RuntimeModelProfile.model_validate({})

    base = {
        "id": "p1",
        "host_id": "h",
        "tenant": "t",
        "model": "m",
        "provider": "ollama",
        "test_name": "short_rag",
        "context_tokens": 100,
        "output_tokens": 50,
        "total_latency_ms": 1000,
        "tokens_per_second": 50.0,
        "success": True,
    }
    # Unknown test_name must be rejected by the literal.
    with pytest.raises(ValidationError):
        RuntimeModelProfile.model_validate({**base, "test_name": "halted"})

    # Negative latency must be rejected.
    with pytest.raises(ValidationError):
        RuntimeModelProfile.model_validate({**base, "total_latency_ms": -1})


def test_runtime_model_profile_accepts_minimal_required_payload() -> None:
    """Optional resource fields default to ``None`` and ``created_at`` is auto-filled."""
    profile = RuntimeModelProfile(
        id="p1",
        host_id="h",
        tenant="t",
        model="m",
        provider="ollama",
        test_name="tiny_classification",  # type: ignore[arg-type]
        context_tokens=500,
        output_tokens=100,
        total_latency_ms=1000,
        tokens_per_second=100.0,
        success=True,
    )
    assert profile.ram_peak_gb is None
    assert profile.vram_peak_gb is None
    assert profile.gpu_avg_pct is None
    assert profile.error is None
    assert profile.created_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# MongoRuntimeProfileStore (against _FakeAsyncDB)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mongo_runtime_store_round_trip_and_indexes_idempotent() -> None:
    """``save``→``get`` round-trip; ``ensure_indexes`` is idempotent and uses unique ``id``."""
    db = _FakeAsyncDB()
    store = MongoRuntimeProfileStore(db)

    # Idempotent ensure_indexes: two calls register indexes once.
    await store.ensure_indexes()
    await store.ensure_indexes()

    coll = db[RUNTIME_PROFILE_COLLECTION_NAME]
    # Three indexes registered: id-unique, (model, created_at), (test_name, created_at).
    assert len(coll.created_indexes) == 3
    # Unique id index present.
    flat = [
        (args, kwargs) for args, kwargs in coll.created_indexes
    ]
    has_unique_id = any(
        kwargs.get("unique") is True and args[0] == "id" for args, kwargs in flat
    )
    assert has_unique_id, f"Expected unique 'id' index, got {flat!r}"

    profile = _make_profile(id="rt-roundtrip")
    saved = await store.save(profile)
    assert saved.id == "rt-roundtrip"

    fetched = await store.get("rt-roundtrip")
    assert fetched is not None
    assert fetched.model == profile.model
    assert fetched.tokens_per_second == profile.tokens_per_second

    # save() with a colliding id replaces (upsert semantics).
    updated = _make_profile(id="rt-roundtrip", tokens_per_second=999.0)
    await store.save(updated)
    fetched2 = await store.get("rt-roundtrip")
    assert fetched2 is not None
    assert fetched2.tokens_per_second == 999.0


@pytest.mark.asyncio
async def test_mongo_runtime_store_list_filters() -> None:
    """``list_recent`` filters by model+since; ``list_by_test_name`` filters by test."""
    db = _FakeAsyncDB()
    store = MongoRuntimeProfileStore(db)

    base_time = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    for i in range(3):
        await store.save(
            _make_profile(
                id=f"p{i}",
                model="gemma3:4b" if i < 2 else "gemma3:26b",
                test_name="short_rag" if i % 2 == 0 else "long_context",
                created_at=base_time + timedelta(minutes=i),
            )
        )

    recent_4b = await store.list_recent(
        model="gemma3:4b", since=base_time - timedelta(minutes=1), limit=10
    )
    assert len(recent_4b) == 2
    # Sorted desc by created_at.
    assert recent_4b[0].id == "p1"

    by_test = await store.list_by_test_name("short_rag", limit=10)
    assert {p.id for p in by_test} == {"p0", "p2"}


# ─────────────────────────────────────────────────────────────────────────────
# RuntimeProfiler.run_test
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runtime_profiler_run_test_happy_path(monkeypatch: Any) -> None:
    """``run_test`` produces a saved profile with all timing fields populated."""
    # Disable nvidia-smi for deterministic assertions on GPU fields.
    monkeypatch.setattr(
        "backend.services.runtime_profiler.shutil.which",
        lambda *_args, **_kw: None,
    )

    store = InMemoryRuntimeProfileStore()
    profiler = RuntimeProfiler(
        profile_store=store,
        host_id="host-1",
        tenant="recallhub",
        provider_invoker=_build_invoker(latency_per_call_s=0.05),
    )

    profile = await profiler.run_test(
        model="gemma3:4b",
        provider="ollama",
        test_name="short_rag",
        context_tokens=2000,
        output_tokens=500,
    )

    assert profile.success is True
    assert profile.error is None
    assert profile.test_name == "short_rag"
    assert profile.context_tokens == 2000
    assert profile.output_tokens == 500
    assert profile.total_latency_ms >= 50  # ~50ms from invoker sleep
    assert profile.tokens_per_second > 0.0
    assert profile.first_token_ms == 30
    assert profile.model_load_ms == 50
    assert profile.prompt_tokens_per_second == 4000.0
    assert profile.generation_tokens_per_second == 200.0
    # GPU fields gated to None because nvidia-smi mocked missing.
    assert profile.vram_peak_gb is None
    assert profile.gpu_avg_pct is None
    # Persisted in store.
    fetched = await store.get(profile.id)
    assert fetched is not None
    assert fetched.id == profile.id


@pytest.mark.asyncio
async def test_runtime_profiler_run_test_failure_path(monkeypatch: Any) -> None:
    """Provider exceptions → profile saved with ``success=False`` and ``error`` set."""
    monkeypatch.setattr(
        "backend.services.runtime_profiler.shutil.which",
        lambda *_args, **_kw: None,
    )

    store = InMemoryRuntimeProfileStore()
    boom = RuntimeError("model OOM")
    profiler = RuntimeProfiler(
        profile_store=store,
        provider_invoker=_build_invoker(raise_exc=boom),
    )

    profile = await profiler.run_test(
        model="gemma3:26b",
        provider="ollama",
        test_name="long_context",
        context_tokens=32000,
        output_tokens=1500,
    )

    assert profile.success is False
    assert profile.error is not None
    assert "RuntimeError" in profile.error
    assert "model OOM" in profile.error
    assert profile.tokens_per_second == 0.0
    # Still persisted (never silently dropped).
    assert await store.get(profile.id) is not None


@pytest.mark.asyncio
async def test_runtime_profiler_unknown_test_name_raises(monkeypatch: Any) -> None:
    """``run_test`` rejects unknown test names with ``ValueError``."""
    monkeypatch.setattr(
        "backend.services.runtime_profiler.shutil.which",
        lambda *_args, **_kw: None,
    )
    profiler = RuntimeProfiler(
        profile_store=InMemoryRuntimeProfileStore(),
        provider_invoker=_build_invoker(),
    )
    with pytest.raises(ValueError):
        await profiler.run_test(
            model="m",
            provider="ollama",
            test_name="bogus",
            context_tokens=100,
            output_tokens=50,
        )


# ─────────────────────────────────────────────────────────────────────────────
# parallel_workers throughput degradation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parallel_workers_reports_degraded_throughput(
    monkeypatch: Any,
) -> None:
    """``parallel_workers`` per-worker throughput < single short_rag throughput.

    The mock invoker holds a global semaphore-of-1 so concurrent calls
    serialize, guaranteeing the aggregate wall-clock for N=4 is ~4×
    that of a single short_rag — i.e. per-worker tps must drop below
    the single-call tps after dividing by N.
    """
    monkeypatch.setattr(
        "backend.services.runtime_profiler.shutil.which",
        lambda *_args, **_kw: None,
    )

    serial_lock = asyncio.Lock()

    async def _serial_invoker(
        *,
        model: str,
        provider: str,
        prompt: str,
        max_tokens: int,
        **_: Any,
    ) -> dict[str, Any]:
        async with serial_lock:
            await asyncio.sleep(0.05)
        return {"output_tokens": 500}

    store = InMemoryRuntimeProfileStore()
    profiler = RuntimeProfiler(
        profile_store=store,
        provider_invoker=_serial_invoker,
        parallel_workers=ParallelWorkersConfig(concurrency=4),
    )

    single = await profiler.run_test(
        model="gemma3:4b",
        provider="ollama",
        test_name="short_rag",
        context_tokens=2000,
        output_tokens=500,
    )
    parallel_records = await profiler.profile_model(
        model="gemma3:4b",
        provider="ollama",
        contexts=[],  # skip all single-context tests; parallel always runs
    )

    parallel = next(p for p in parallel_records if p.test_name == "parallel_workers")
    assert parallel.success is True
    # Per-worker tps must be strictly lower than the single-call
    # throughput when calls serialize under the lock.
    assert parallel.tokens_per_second < single.tokens_per_second
    # Single-call profile recorded as well.
    assert single.test_name == "short_rag"


# ─────────────────────────────────────────────────────────────────────────────
# GPU metrics fallback
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gpu_metrics_none_when_nvidia_smi_missing(monkeypatch: Any) -> None:
    """When ``nvidia-smi`` is not on PATH, GPU fields are ``None`` and no crash occurs."""
    monkeypatch.setattr(
        "backend.services.runtime_profiler.shutil.which",
        lambda name, *_a, **_kw: None if name == "nvidia-smi" else "/usr/bin/" + name,
    )

    store = InMemoryRuntimeProfileStore()
    profiler = RuntimeProfiler(
        profile_store=store,
        provider_invoker=_build_invoker(latency_per_call_s=0.02),
    )
    profile = await profiler.run_test(
        model="gemma3:4b",
        provider="ollama",
        test_name="tiny_classification",
        context_tokens=500,
        output_tokens=100,
    )

    assert profile.vram_peak_gb is None
    assert profile.gpu_avg_pct is None


# ─────────────────────────────────────────────────────────────────────────────
# Standardized prompt builder is deterministic
# ─────────────────────────────────────────────────────────────────────────────


def test_build_standard_prompt_is_deterministic_and_sized() -> None:
    """Prompt builder yields stable strings sized to ~4 chars per token."""
    a = build_standard_prompt(500)
    b = build_standard_prompt(500)
    assert a == b
    assert len(a) == 500 * 4
    assert build_standard_prompt(0) == ""


# ────────────────────────────────────────────────────────────────────────────
# F4 / Task 82 — cost_eur on RuntimeModelProfile + provider billing
# ────────────────────────────────────────────────────────────────────────────


class _StubRoleConfig:
    """Minimal stand-in for ``ModelRoleConfig`` carrying cost rates."""

    def __init__(
        self,
        *,
        model: str,
        cost_per_million_input: Optional[float] = None,
        cost_per_million_output: Optional[float] = None,
    ) -> None:
        self.model = model
        self.cost_per_million_input = cost_per_million_input
        self.cost_per_million_output = cost_per_million_output


class _StubRoleRegistry:
    """Registry stub exposing ``list_roles`` keyed by role id."""

    def __init__(self, roles: dict[str, _StubRoleConfig]) -> None:
        self._roles = roles

    def list_roles(self) -> dict[str, _StubRoleConfig]:
        return dict(self._roles)


def _cost_invoker(provider_result: dict[str, Any]) -> Any:
    """Build an async invoker that returns ``provider_result`` verbatim."""

    async def _invoke(
        *,
        model: str,
        provider: str,
        prompt: str,
        max_tokens: int,
        **_: Any,
    ) -> dict[str, Any]:
        return dict(provider_result)

    return _invoke


@pytest.mark.asyncio
async def test_run_test_populates_cost_from_provider_response(
    monkeypatch: Any,
) -> None:
    """LiteLLM-style ``_hidden_params.response_cost`` is preferred over estimation."""
    monkeypatch.setattr(
        "backend.services.runtime_profiler.shutil.which",
        lambda *_a, **_kw: None,
    )
    invoker = _cost_invoker(
        {
            "output_tokens": 500,
            "_hidden_params": {"response_cost": 0.0042},
        }
    )
    profiler = RuntimeProfiler(
        profile_store=InMemoryRuntimeProfileStore(),
        provider_invoker=invoker,
    )
    profile = await profiler.run_test(
        model="gpt-test",
        provider="openai",
        test_name="short_rag",
        context_tokens=2000,
        output_tokens=500,
    )
    assert profile.success is True
    assert profile.cost_eur == pytest.approx(0.0042)
    assert profile.cost_source == "provider"


@pytest.mark.asyncio
async def test_run_test_provider_cost_via_usage_block(monkeypatch: Any) -> None:
    """``usage.cost_eur`` is honoured when ``_hidden_params`` is absent."""
    monkeypatch.setattr(
        "backend.services.runtime_profiler.shutil.which",
        lambda *_a, **_kw: None,
    )
    invoker = _cost_invoker(
        {
            "output_tokens": 500,
            "usage": {"cost_eur": 0.01},
        }
    )
    profiler = RuntimeProfiler(
        profile_store=InMemoryRuntimeProfileStore(),
        provider_invoker=invoker,
    )
    profile = await profiler.run_test(
        model="gpt-test",
        provider="openai",
        test_name="short_rag",
        context_tokens=2000,
        output_tokens=500,
    )
    assert profile.cost_eur == pytest.approx(0.01)
    assert profile.cost_source == "provider"


@pytest.mark.asyncio
async def test_run_test_falls_back_to_estimated_cost(monkeypatch: Any) -> None:
    """With no provider cost but role-config rates, cost is estimated."""
    monkeypatch.setattr(
        "backend.services.runtime_profiler.shutil.which",
        lambda *_a, **_kw: None,
    )
    registry = _StubRoleRegistry(
        {
            "orchestrator": _StubRoleConfig(
                model="gpt-test",
                cost_per_million_input=2.0,
                cost_per_million_output=8.0,
            )
        }
    )
    # 2000 prompt tokens → 2/1000 × 2.0 = 0.004 EUR
    # 500 completion tokens → 0.5/1000 × 8.0 = 0.004 EUR
    # total = 0.008 EUR
    invoker = _cost_invoker({"output_tokens": 500})
    profiler = RuntimeProfiler(
        model_role_registry=registry,
        profile_store=InMemoryRuntimeProfileStore(),
        provider_invoker=invoker,
    )
    profile = await profiler.run_test(
        model="gpt-test",
        provider="openai",
        test_name="short_rag",
        context_tokens=2000,
        output_tokens=500,
    )
    assert profile.cost_source == "estimated"
    assert profile.cost_eur == pytest.approx(0.008)


@pytest.mark.asyncio
async def test_run_test_cost_none_when_no_rates(monkeypatch: Any) -> None:
    """Local Ollama with no role-config rates → ``cost_eur=None``, ``unknown``."""
    monkeypatch.setattr(
        "backend.services.runtime_profiler.shutil.which",
        lambda *_a, **_kw: None,
    )
    invoker = _cost_invoker({"output_tokens": 500})
    profiler = RuntimeProfiler(
        profile_store=InMemoryRuntimeProfileStore(),
        provider_invoker=invoker,
    )
    profile = await profiler.run_test(
        model="gemma3:4b",
        provider="ollama",
        test_name="short_rag",
        context_tokens=2000,
        output_tokens=500,
    )
    assert profile.cost_eur is None
    assert profile.cost_source == "unknown"


def test_estimate_cost_eur_helper() -> None:
    """Pure-unit math check: 1M tokens at 0.5 EUR/M → 0.5 EUR."""
    profiler = RuntimeProfiler(
        profile_store=InMemoryRuntimeProfileStore(),
        provider_invoker=_build_invoker(),
    )

    # 1M input tokens @ 0.5/M + 0 output → 0.5
    cost, source = profiler._estimate_cost_eur(
        role_config=_StubRoleConfig(
            model="m", cost_per_million_input=0.5, cost_per_million_output=0.0
        ),
        prompt_tokens=1_000_000,
        completion_tokens=0,
    )
    assert source == "estimated"
    assert cost == pytest.approx(0.5)

    # Mixed: 1M in @ 2 + 0.5M out @ 8 → 2 + 4 = 6
    cost, source = profiler._estimate_cost_eur(
        role_config=_StubRoleConfig(
            model="m", cost_per_million_input=2.0, cost_per_million_output=8.0
        ),
        prompt_tokens=1_000_000,
        completion_tokens=500_000,
    )
    assert source == "estimated"
    assert cost == pytest.approx(6.0)

    # No role config → unknown / None.
    cost, source = profiler._estimate_cost_eur(
        role_config=None, prompt_tokens=1000, completion_tokens=1000
    )
    assert cost is None
    assert source == "unknown"

    # Both rates None → unknown / None.
    cost, source = profiler._estimate_cost_eur(
        role_config=_StubRoleConfig(model="m"),
        prompt_tokens=1000,
        completion_tokens=1000,
    )
    assert cost is None
    assert source == "unknown"

    # Missing one rate is treated as 0.0.
    cost, source = profiler._estimate_cost_eur(
        role_config=_StubRoleConfig(
            model="m", cost_per_million_input=None, cost_per_million_output=10.0
        ),
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
    )
    assert source == "estimated"
    assert cost == pytest.approx(10.0)


def test_runtime_model_profile_backward_compat_without_cost() -> None:
    """Existing profile docs without ``cost_eur`` deserialize cleanly."""
    legacy = {
        "id": "p-legacy",
        "host_id": "h",
        "tenant": "t",
        "model": "m",
        "provider": "ollama",
        "test_name": "short_rag",
        "context_tokens": 100,
        "output_tokens": 10,
        "total_latency_ms": 1,
        "tokens_per_second": 1.0,
        "success": True,
    }
    profile = RuntimeModelProfile.model_validate(legacy)
    assert profile.cost_eur is None
    assert profile.cost_source is None
