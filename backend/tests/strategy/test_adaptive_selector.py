"""Tests for the Phase 6 / Task 78 :class:`AdaptiveSelector`.

Covers the routing-score path, the fast-path bias, hard
disqualifications (privacy + latency hard limit), and the
backward-compatible fall-through to the base
:class:`StrategySpecSelector` when no adaptive inputs are wired.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from backend.agent.strategy.models import (
    BusinessContext,
    ResolvedSourcePolicy,
    SourcePolicy,
    StrategyBudgets,
    StrategyGraph,
    StrategyNode,
    StrategySpec,
)
from backend.agent.strategy.runtime_profile_store import (
    InMemoryRuntimeProfileStore,
    RuntimeModelProfile,
)
from backend.agent.strategy.spec_selector import (
    AdaptiveSelector,
    StrategySpecSelector,
)
from backend.agent.strategy.spec_store import InMemorySpecStore, MongoSpecStore
from backend.agent.strategy.adaptive_decision_store import (
    AdaptiveDecisionDoc,
    AdaptiveDecisionStore,
    InMemoryAdaptiveDecisionStore,
)
from backend.tests.strategy._fakes import _FakeAsyncDB


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_spec(
    *,
    strategy_id: str,
    capability_id: str = "qa",
    tenant_scope: str = "default",
    synth_model: Optional[str] = "synth-mini",
    latency_target_ms: int = 8000,
    latency_hard_limit_ms: int = 45000,
    max_output_tokens: int = 1000,
    allow_web: bool = False,
    local_only: bool = False,
    display_name: str = "",
    updated_at: Optional[datetime] = None,
) -> StrategySpec:
    graph = StrategyGraph(
        nodes=[StrategyNode(node_id="n1", node_type="retrieve")],
        edges=[],
        entry_node="n1",
        terminal_nodes=["n1"],
    )
    model_roles: dict[str, str] = {}
    if synth_model:
        model_roles["synth"] = synth_model
    return StrategySpec(
        strategy_id=strategy_id,
        display_name=display_name,
        status="active",
        capability_id=capability_id,
        tenant_scope=tenant_scope,
        graph=graph,
        budgets=StrategyBudgets(
            local_only=local_only,
            latency_target_ms=latency_target_ms,
            latency_hard_limit_ms=latency_hard_limit_ms,
            max_output_tokens=max_output_tokens,
        ),
        source_policy=SourcePolicy(allow_web=allow_web),
        model_roles=model_roles,
        updated_at=updated_at,
    )


def _seed_snapshot(store: InMemorySpecStore, spec: StrategySpec) -> None:
    """Seed an in-memory spec store with ``spec``."""
    spec_data = spec.model_dump(mode="json")
    snapshot = {
        "spec_data": spec_data,
        "version": spec.version,
        "version_counter": 1,
        "status": spec.status,
        "created_at": spec.created_at,
        "spec_hash": spec.spec_hash or f"hash-{spec.strategy_id}",
    }
    store._snapshots.setdefault(spec.strategy_id, []).append(snapshot)


async def _seed_evaluation_result(
    db: _FakeAsyncDB,
    *,
    strategy_id: str,
    score: float,
    capability_id: str = "qa",
) -> None:
    await db["evaluation_results"].insert_one(
        {
            "strategy_id": strategy_id,
            "capability_id": capability_id,
            "result": {"composite_score": score},
            "created_at": datetime.now(timezone.utc),
        }
    )


class _StubResourceCollector:
    """Minimal stand-in exposing :meth:`latest` like the real collector."""

    def __init__(self, snapshot: Any | None) -> None:
        self._snapshot = snapshot

    async def latest(self) -> Any | None:
        return self._snapshot


class _StubSnapshot:
    def __init__(
        self,
        *,
        ollama_resident_models: list[str] | None = None,
        gpu_pct: Optional[float] = None,
        cpu_pct: Optional[float] = 25.0,
    ) -> None:
        self.ollama_resident_models = ollama_resident_models or []
        self.gpu_pct = gpu_pct
        self.cpu_pct = cpu_pct


def _make_business_context(
    *,
    capability_id: str = "qa",
    matter_id: Optional[str] = None,
    allow_web: bool = False,
) -> BusinessContext:
    return BusinessContext(
        capability_id=capability_id,
        matter_id=matter_id,
        resolved_source_policy=ResolvedSourcePolicy(allow_web=allow_web),
    )


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_candidate_returned_without_adaptive_logic() -> None:
    """One active spec → returned verbatim, scoring is bypassed."""
    store = InMemorySpecStore()
    spec = _make_spec(strategy_id="solo", capability_id="qa")
    _seed_snapshot(store, spec)

    base = StrategySpecSelector(store=store, legacy_adapter=None)
    selector = AdaptiveSelector(base_selector=base)

    result = await selector.select(capability_id="qa", tenant_id="recallhub")
    assert result is not None
    assert result.strategy_id == "solo"


@pytest.mark.asyncio
async def test_quality_score_breaks_tie_between_two_candidates() -> None:
    """Higher historical composite_score wins when other signals match."""
    store = InMemorySpecStore()
    high_quality = _make_spec(strategy_id="high", capability_id="qa")
    low_quality = _make_spec(strategy_id="low", capability_id="qa")
    _seed_snapshot(store, high_quality)
    _seed_snapshot(store, low_quality)

    eval_db = _FakeAsyncDB()
    await _seed_evaluation_result(eval_db, strategy_id="high", score=0.9)
    await _seed_evaluation_result(eval_db, strategy_id="low", score=0.2)

    base = StrategySpecSelector(store=store, legacy_adapter=None)
    selector = AdaptiveSelector(
        base_selector=base, evaluation_results_db=eval_db
    )

    result = await selector.select(capability_id="qa", tenant_id="recallhub")
    assert result is not None
    assert result.strategy_id == "high"


@pytest.mark.asyncio
async def test_residency_breaks_tie_when_quality_equal() -> None:
    """Resident model wins when other signals match."""
    store = InMemorySpecStore()
    resident = _make_spec(
        strategy_id="resident", capability_id="qa", synth_model="hot-model"
    )
    cold = _make_spec(
        strategy_id="cold", capability_id="qa", synth_model="cold-model"
    )
    _seed_snapshot(store, resident)
    _seed_snapshot(store, cold)

    snapshot = _StubSnapshot(ollama_resident_models=["hot-model"])
    base = StrategySpecSelector(store=store, legacy_adapter=None)
    selector = AdaptiveSelector(
        base_selector=base,
        resource_collector=_StubResourceCollector(snapshot),
    )

    result = await selector.select(capability_id="qa", tenant_id="recallhub")
    assert result is not None
    assert result.strategy_id == "resident"


@pytest.mark.asyncio
async def test_latency_hard_limit_disqualifies_candidate() -> None:
    """Candidate whose model can't meet the hard latency limit is excluded."""
    store = InMemorySpecStore()
    slow = _make_spec(
        strategy_id="slow",
        capability_id="qa",
        synth_model="slow-model",
        latency_target_ms=2000,
        latency_hard_limit_ms=4000,
        max_output_tokens=1000,
    )
    fast = _make_spec(
        strategy_id="fast",
        capability_id="qa",
        synth_model="fast-model",
        latency_target_ms=2000,
        latency_hard_limit_ms=4000,
        max_output_tokens=1000,
    )
    _seed_snapshot(store, slow)
    _seed_snapshot(store, fast)

    profile_store = InMemoryRuntimeProfileStore()
    # slow-model: 1000 tokens / 10 tps = 100s > hard_limit (4s) → disqualified
    await profile_store.save(
        RuntimeModelProfile(
            id="p-slow",
            host_id="h",
            tenant="t",
            model="slow-model",
            provider="ollama",
            test_name="short_rag",
            context_tokens=100,
            output_tokens=100,
            total_latency_ms=10000,
            tokens_per_second=10.0,
            success=True,
        )
    )
    # fast-model: 1000 tokens / 1000 tps = 1s ≤ target → full credit
    await profile_store.save(
        RuntimeModelProfile(
            id="p-fast",
            host_id="h",
            tenant="t",
            model="fast-model",
            provider="ollama",
            test_name="short_rag",
            context_tokens=100,
            output_tokens=100,
            total_latency_ms=100,
            tokens_per_second=1000.0,
            success=True,
        )
    )

    base = StrategySpecSelector(store=store, legacy_adapter=None)
    selector = AdaptiveSelector(
        base_selector=base, runtime_profile_store=profile_store
    )

    result = await selector.select(capability_id="qa", tenant_id="recallhub")
    assert result is not None
    assert result.strategy_id == "fast"


@pytest.mark.asyncio
async def test_strict_privacy_excludes_web_enabled_spec() -> None:
    """Strict privacy → web-enabled spec is hard-disqualified."""
    store = InMemorySpecStore()
    web_spec = _make_spec(
        strategy_id="web", capability_id="qa", allow_web=True
    )
    local_spec = _make_spec(
        strategy_id="local", capability_id="qa", allow_web=False
    )
    _seed_snapshot(store, web_spec)
    _seed_snapshot(store, local_spec)

    base = StrategySpecSelector(store=store, legacy_adapter=None)
    selector = AdaptiveSelector(base_selector=base)

    result = await selector.select(
        capability_id="qa",
        tenant_id="recallhub",
        privacy_mode="strict",
    )
    assert result is not None
    assert result.strategy_id == "local"


@pytest.mark.asyncio
async def test_fast_path_eligible_request_prefers_fast_variant() -> None:
    """Eligible fast-path request biases scoring toward the ``fast`` variant."""
    store = InMemorySpecStore()
    deep_spec = _make_spec(
        strategy_id="deep",
        capability_id="business_summary",
        display_name="Deep summary",
        latency_target_ms=20000,
    )
    fast_spec = _make_spec(
        strategy_id="fast",
        capability_id="business_summary",
        display_name="Fast summary",
        latency_target_ms=2000,
    )
    _seed_snapshot(store, deep_spec)
    _seed_snapshot(store, fast_spec)

    base = StrategySpecSelector(store=store, legacy_adapter=None)
    selector = AdaptiveSelector(base_selector=base)

    bctx = _make_business_context(
        capability_id="business_summary", matter_id="matter-1"
    )

    result = await selector.select(
        capability_id="business_summary",
        tenant_id="recallhub",
        business_context=bctx,
        top_retrieval_score=0.95,
    )
    assert result is not None
    assert result.strategy_id == "fast"


@pytest.mark.asyncio
async def test_fast_path_failing_conditions_runs_standard_scoring() -> None:
    """Fast-path conditions failing → fast bias not applied."""
    store = InMemorySpecStore()
    deep_spec = _make_spec(
        strategy_id="deep",
        capability_id="business_summary",
        display_name="Deep summary",
        latency_target_ms=20000,
    )
    fast_spec = _make_spec(
        strategy_id="fast",
        capability_id="business_summary",
        display_name="Fast summary",
        latency_target_ms=2000,
    )
    _seed_snapshot(store, deep_spec)
    _seed_snapshot(store, fast_spec)

    eval_db = _FakeAsyncDB()
    # Heavily favour the deep spec on quality; without fast bias it must win.
    await _seed_evaluation_result(
        eval_db, strategy_id="deep", score=0.95, capability_id="business_summary"
    )
    await _seed_evaluation_result(
        eval_db, strategy_id="fast", score=0.10, capability_id="business_summary"
    )

    base = StrategySpecSelector(store=store, legacy_adapter=None)
    selector = AdaptiveSelector(
        base_selector=base, evaluation_results_db=eval_db
    )

    # No business context → fast path conditions cannot pass.
    result = await selector.select(
        capability_id="business_summary",
        tenant_id="recallhub",
        top_retrieval_score=0.95,
    )
    assert result is not None
    assert result.strategy_id == "deep"


@pytest.mark.asyncio
async def test_no_adaptive_inputs_falls_back_to_base_selector() -> None:
    """All optional inputs ``None`` → matches base selector behaviour."""
    store = InMemorySpecStore()
    spec_a = _make_spec(
        strategy_id="a",
        capability_id="qa",
        tenant_scope="recallhub",
        updated_at=datetime(2026, 1, 1),
    )
    spec_b = _make_spec(
        strategy_id="b",
        capability_id="qa",
        tenant_scope="default",
        updated_at=datetime(2026, 5, 1),
    )
    _seed_snapshot(store, spec_a)
    _seed_snapshot(store, spec_b)

    base = StrategySpecSelector(store=store, legacy_adapter=None)
    base_result = await base.select(capability_id="qa", tenant_id="recallhub")

    base2 = StrategySpecSelector(store=store, legacy_adapter=None)
    selector = AdaptiveSelector(base_selector=base2)
    adaptive_result = await selector.select(
        capability_id="qa", tenant_id="recallhub"
    )

    assert base_result is not None
    assert adaptive_result is not None
    # With no adaptive signals, ranking falls back to the base
    # tenant-preference rule via the tie-breaker.
    assert adaptive_result.strategy_id == base_result.strategy_id == "a"


@pytest.mark.asyncio
async def test_caching_within_ttl_prevents_repeated_store_queries() -> None:
    """Two calls within TTL → only one ``store.list`` query."""
    db = _FakeAsyncDB()
    store = MongoSpecStore(db)
    spec = _make_spec(strategy_id="s1", capability_id="qa")
    spec2 = _make_spec(strategy_id="s2", capability_id="qa")
    snap = {
        "spec_data": spec.model_dump(mode="json"),
        "version": spec.version,
        "version_counter": 1,
        "status": "active",
        "created_at": spec.created_at,
        "spec_hash": "hash-1",
    }
    snap2 = {
        "spec_data": spec2.model_dump(mode="json"),
        "version": spec2.version,
        "version_counter": 1,
        "status": "active",
        "created_at": spec2.created_at,
        "spec_hash": "hash-2",
    }
    await store.upsert(snap, expected_version=None)
    await store.upsert(snap2, expected_version=None)

    base = StrategySpecSelector(store=store, legacy_adapter=None)
    call_count = {"n": 0}
    original_list = store.list

    async def counting_list(*args: Any, **kwargs: Any) -> Any:
        call_count["n"] += 1
        return await original_list(*args, **kwargs)

    store.list = counting_list  # type: ignore[assignment]

    selector = AdaptiveSelector(base_selector=base)
    first = await selector.select(capability_id="qa", tenant_id="recallhub")
    second = await selector.select(capability_id="qa", tenant_id="recallhub")

    assert first is not None and second is not None
    assert first.strategy_id == second.strategy_id
    assert call_count["n"] == 1, "Adaptive cache must dedupe within TTL"


@pytest.mark.asyncio
async def test_clear_cache_drops_both_layers() -> None:
    """``clear_cache`` flushes the adaptive cache and the base cache."""
    store = InMemorySpecStore()
    spec = _make_spec(strategy_id="solo", capability_id="qa")
    _seed_snapshot(store, spec)

    base = StrategySpecSelector(store=store, legacy_adapter=None)
    selector = AdaptiveSelector(base_selector=base)

    await selector.select(capability_id="qa", tenant_id="recallhub")
    # Single-candidate path populates the adaptive cache only.
    assert selector._cache != {}

    removed = selector.clear_cache()
    assert removed >= 1
    assert selector._cache == {}


# ── F8 / Task 85: decision-store persistence ────────────────────────────


class _RaisingDecisionStore(AdaptiveDecisionStore):
    """Decision store that always raises on ``record`` for failure-mode tests."""

    def __init__(self) -> None:
        self.calls = 0

    async def record(self, doc: AdaptiveDecisionDoc) -> AdaptiveDecisionDoc:
        self.calls += 1
        raise RuntimeError("boom")

    async def list_recent(self, **_kwargs: Any) -> list[AdaptiveDecisionDoc]:
        return []


def _seed_two_candidates() -> InMemorySpecStore:
    store = InMemorySpecStore()
    spec_a = _make_spec(
        strategy_id="alpha",
        capability_id="qa",
        tenant_scope="recallhub",
        updated_at=datetime(2026, 1, 1),
    )
    spec_b = _make_spec(
        strategy_id="beta",
        capability_id="qa",
        tenant_scope="default",
        updated_at=datetime(2026, 5, 1),
    )
    _seed_snapshot(store, spec_a)
    _seed_snapshot(store, spec_b)
    return store


@pytest.mark.asyncio
async def test_decision_persisted_on_fresh_call() -> None:
    """Fresh adaptive decisions are persisted with the right winner + breakdown."""
    store = _seed_two_candidates()
    decision_store = InMemoryAdaptiveDecisionStore()

    base = StrategySpecSelector(store=store, legacy_adapter=None)
    selector = AdaptiveSelector(
        base_selector=base,
        decision_store=decision_store,
    )

    winner = await selector.select(capability_id="qa", tenant_id="recallhub")
    assert winner is not None

    listed = await decision_store.list_recent()
    assert len(listed) == 1
    persisted = listed[0]
    assert persisted.capability_id == "qa"
    assert persisted.tenant_id == "recallhub"
    assert persisted.privacy_mode == "standard"
    assert persisted.agent_mode == "auto"
    assert persisted.winner_strategy_id == winner.strategy_id
    assert persisted.chosen_via_cache is False
    # Both candidates must appear in the breakdown.
    breakdown_ids = {c["strategy_id"] for c in persisted.candidate_scores}
    assert breakdown_ids == {"alpha", "beta"}
    # Score totals are floats.
    assert all(isinstance(c["total"], float) for c in persisted.candidate_scores)


@pytest.mark.asyncio
async def test_decision_persisted_on_cache_hit_when_enabled() -> None:
    """Cache hits emit a second doc with ``chosen_via_cache=True``."""
    store = _seed_two_candidates()
    decision_store = InMemoryAdaptiveDecisionStore()

    base = StrategySpecSelector(store=store, legacy_adapter=None)
    selector = AdaptiveSelector(
        base_selector=base,
        decision_store=decision_store,
        record_cache_hits=True,
    )

    first = await selector.select(capability_id="qa", tenant_id="recallhub")
    second = await selector.select(capability_id="qa", tenant_id="recallhub")

    assert first is not None and second is not None
    assert first.strategy_id == second.strategy_id

    listed = await decision_store.list_recent()
    assert len(listed) == 2
    cache_hit_docs = [d for d in listed if d.chosen_via_cache]
    fresh_docs = [d for d in listed if not d.chosen_via_cache]
    assert len(cache_hit_docs) == 1
    assert len(fresh_docs) == 1
    assert cache_hit_docs[0].winner_strategy_id == first.strategy_id


@pytest.mark.asyncio
async def test_decision_not_persisted_when_record_cache_hits_false() -> None:
    """With ``record_cache_hits=False`` only fresh decisions are persisted."""
    store = _seed_two_candidates()
    decision_store = InMemoryAdaptiveDecisionStore()

    base = StrategySpecSelector(store=store, legacy_adapter=None)
    selector = AdaptiveSelector(
        base_selector=base,
        decision_store=decision_store,
        record_cache_hits=False,
    )

    await selector.select(capability_id="qa", tenant_id="recallhub")
    await selector.select(capability_id="qa", tenant_id="recallhub")

    listed = await decision_store.list_recent()
    assert len(listed) == 1
    assert listed[0].chosen_via_cache is False


@pytest.mark.asyncio
async def test_persistence_failure_does_not_break_selection() -> None:
    """A raising decision store must not propagate out of ``select()``."""
    store = _seed_two_candidates()
    decision_store = _RaisingDecisionStore()

    base = StrategySpecSelector(store=store, legacy_adapter=None)
    selector = AdaptiveSelector(
        base_selector=base,
        decision_store=decision_store,
    )

    winner = await selector.select(capability_id="qa", tenant_id="recallhub")
    assert winner is not None
    assert winner.strategy_id in {"alpha", "beta"}
    # The store was attempted exactly once for the fresh decision.
    assert decision_store.calls == 1


@pytest.mark.asyncio
async def test_legacy_callers_without_decision_store_still_work() -> None:
    """Existing call sites that omit ``decision_store`` keep their behaviour."""
    store = _seed_two_candidates()
    base = StrategySpecSelector(store=store, legacy_adapter=None)
    # No decision_store kwarg — mirrors every pre-F8 caller.
    selector = AdaptiveSelector(base_selector=base)

    winner = await selector.select(capability_id="qa", tenant_id="recallhub")
    assert winner is not None
    assert selector.decision_store is None

