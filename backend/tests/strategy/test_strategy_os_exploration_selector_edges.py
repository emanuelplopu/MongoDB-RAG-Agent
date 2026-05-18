"""Edge-coverage tests for Strategy OS exploration and selection helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from backend.agent.strategy.bandit_state_store import InMemoryBanditStateStore
from backend.agent.strategy.evolution_state_store import InMemoryEvolutionStateStore
from backend.agent.strategy.evolutionary_mutator import EvolutionaryMutator
from backend.agent.strategy.experiment_job_models import JobStatus, StrategyExperimentJob
from backend.agent.strategy.experiment_job_store import InMemoryExperimentJobStore
from backend.agent.strategy.exploration_modes import (
    BanditMode,
    EvolutionaryMode,
    ExplorationMode,
    GridMode,
    ModeRegistry,
    MutationGridYAMLError,
    RegressionMode,
    SmokeMode,
    UnknownExplorationModeError,
    _snapshot_to_spec,
)
from backend.agent.strategy.models import (
    StrategyBudgets,
    StrategyGraph,
    StrategyNode,
    StrategySpec,
)
from backend.agent.strategy.spec_selector import (
    FastPathRules,
    NoActiveSpecError,
    StrategySpecSelector,
    _looks_fast_variant,
    _resolve_synth_model,
)
from backend.agent.strategy.spec_store import InMemorySpecStore


def _spec(
    strategy_id: str = "spec-a",
    *,
    capability_id: str = "qa",
    tenant_scope: str = "recallhub",
    latency_target_ms: int = 4000,
    local_only: bool = False,
) -> StrategySpec:
    return StrategySpec(
        strategy_id=strategy_id,
        version="1.0.0",
        status="active",
        display_name=strategy_id,
        capability_id=capability_id,
        tenant_scope=tenant_scope,
        graph=StrategyGraph(
            nodes=[StrategyNode(node_id="n1", node_type="retrieve")],
            edges=[],
            entry_node="n1",
            terminal_nodes=["n1"],
        ),
        budgets=StrategyBudgets(
            latency_target_ms=latency_target_ms,
            latency_hard_limit_ms=max(latency_target_ms + 1000, 6000),
            max_output_tokens=500,
            local_only=local_only,
        ),
    )


def _snapshot(spec: StrategySpec, *, status: str = "active") -> dict[str, Any]:
    return {
        "spec_data": {**spec.model_dump(mode="json"), "status": status},
        "version": spec.version,
        "version_counter": 1,
        "status": status,
        "spec_hash": spec.spec_hash or f"hash-{spec.strategy_id}",
        "created_at": datetime.utcnow(),
    }


async def _seed(store: InMemorySpecStore, spec: StrategySpec, *, status: str = "active") -> None:
    await store.upsert(_snapshot(spec, status=status))


def _job(**overrides: Any) -> StrategyExperimentJob:
    payload = {
        "id": "job-edge",
        "tenant": "recallhub",
        "mode": "grid",
        "datasets": ["ds-a"],
        "strategy_ids": ["spec-a"],
        "status": JobStatus.SCHEDULED,
        "experiment_id": "experiment-edge",
    }
    payload.update(overrides)
    return StrategyExperimentJob(**payload)


def test_exploration_helpers_grid_yaml_and_registry_edges(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="either"):
        GridMode(mutation_grid={}, mutation_grid_yaml_path=tmp_path / "grid.yaml")
    with pytest.raises(ValueError, match="snapshot must be a dict"):
        _snapshot_to_spec("bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="spec_data"):
        _snapshot_to_spec({})

    missing = tmp_path / "missing.yaml"
    with pytest.raises(MutationGridYAMLError):
        GridMode._load_yaml_axes(missing)

    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("[]\n", encoding="utf-8")
    with pytest.raises(MutationGridYAMLError, match="top-level"):
        GridMode._load_yaml_axes(scalar)

    missing_key = tmp_path / "missing-key.yaml"
    missing_key.write_text("not_grid: {}\n", encoding="utf-8")
    with pytest.raises(MutationGridYAMLError, match="mutation_grid"):
        GridMode._load_yaml_axes(missing_key)

    bad_key = tmp_path / "bad-key.yaml"
    bad_key.write_text("mutation_grid:\n  1: [a]\n", encoding="utf-8")
    with pytest.raises(MutationGridYAMLError, match="axis key"):
        GridMode._load_yaml_axes(bad_key)

    bad_value = tmp_path / "bad-value.yaml"
    bad_value.write_text("mutation_grid:\n  budgets.max_llm_calls: 5\n", encoding="utf-8")
    with pytest.raises(MutationGridYAMLError, match="YAML sequence"):
        GridMode._load_yaml_axes(bad_value)

    class MinimalMode(ExplorationMode):
        async def materialize_candidates(self, job, *, spec_store, candidate_generator_factory=None):
            return []

    assert MinimalMode().regression_triggers() == []
    assert "grid" in ModeRegistry.known_modes()
    with pytest.raises(UnknownExplorationModeError) as excinfo:
        ModeRegistry.get("definitely_missing")
    assert excinfo.value.mode_name == "definitely_missing"


@pytest.mark.asyncio
async def test_grid_regression_smoke_and_bandit_edges(tmp_path: Path) -> None:
    store = InMemorySpecStore()
    spec = _spec("spec-a")
    await _seed(store, spec)

    with pytest.raises(ValueError, match="GridMode requires"):
        await GridMode().materialize_candidates(_job(strategy_ids=[]), spec_store=store)
    with pytest.raises(ValueError, match="could not resolve"):
        await GridMode().materialize_candidates(_job(strategy_ids=["missing"]), spec_store=store)

    regression = RegressionMode(regression_thresholds={"score_drop_pct": 1.5})
    assert regression.thresholds["score_drop_pct"] == 1.5
    assert regression.regression_triggers()
    resolved = await regression.materialize_candidates(
        _job(mode="regression", strategy_ids=["missing", "spec-a"]),
        spec_store=store,
    )
    assert [s.strategy_id for s in resolved] == ["spec-a"]

    with pytest.raises(ValueError, match="sample_count"):
        SmokeMode(sample_count=0)
    smoke = SmokeMode(sample_count=2)
    with pytest.raises(ValueError, match="SmokeMode requires"):
        await smoke.materialize_candidates(_job(mode="smoke", strategy_ids=[]), spec_store=store)
    with pytest.raises(ValueError, match="could not resolve"):
        await smoke.materialize_candidates(_job(mode="smoke", strategy_ids=["missing"]), spec_store=store)
    assert smoke.dataset_subsample([1, 2, 3]) == [1, 2]
    assert smoke.dataset_subsample([1, 2, 3], count=0) == []

    with pytest.raises(ValueError, match="warmup"):
        BanditMode(warmup_pulls_per_arm=-1)
    with pytest.raises(ValueError, match="exploration"):
        BanditMode(exploration_constant=-0.1)
    with pytest.raises(ValueError, match="max_total"):
        BanditMode(max_total_pulls=-1)

    state = InMemoryBanditStateStore()
    bandit = BanditMode(
        grid_mode=GridMode(mutation_grid={"budgets.max_llm_calls": [5]}),
        state_store=state,
        warmup_pulls_per_arm=0,
        max_total_pulls=1,
    )
    assert bandit.state_store is state
    assert bandit.max_total_pulls == 1
    candidates = await bandit.materialize_candidates(_job(mode="bandit"), spec_store=store)
    assert len(candidates) == 1
    assert bandit.arm_pool[0].strategy_id.startswith("spec-a__grid_")
    assert await bandit.next_arm(experiment_id="experiment-edge", total_pulls_so_far=1) is None
    assert await BanditMode(state_store=InMemoryBanditStateStore()).next_arm(
        experiment_id="empty",
        total_pulls_so_far=0,
    ) is None


@pytest.mark.asyncio
async def test_evolutionary_mode_resolution_and_result_edges() -> None:
    with pytest.raises(ValueError, match="num_generations"):
        EvolutionaryMode(num_generations=-1)
    with pytest.raises(ValueError, match="top_k"):
        EvolutionaryMode(top_k_parents=0)

    store = InMemorySpecStore()
    seed = _spec("seed-a")
    await _seed(store, seed)
    state = InMemoryEvolutionStateStore()

    async def empty_resolver(_job: StrategyExperimentJob, _store: InMemorySpecStore) -> list[StrategySpec]:
        return []

    empty_mode = EvolutionaryMode(
        state_store=state,
        parent_pool_resolver=empty_resolver,
    )
    assert await empty_mode.materialize_candidates(_job(mode="evolutionary"), spec_store=store) == []

    class StaticMutator(EvolutionaryMutator):
        def __init__(self) -> None:
            pass

        def mutate(self, *, parents: list[StrategySpec], generation: int) -> list[StrategySpec]:
            child = parents[0].model_copy(deep=True)
            child.strategy_id = f"{parents[0].strategy_id}__child_{generation}"
            return [child]

    async def parent_resolver(job: StrategyExperimentJob, spec_store: InMemorySpecStore) -> list[StrategySpec]:
        return [seed]

    mode = EvolutionaryMode(
        mutator=StaticMutator(),
        state_store=state,
        parent_pool_resolver=parent_resolver,
        num_generations=2,
        top_k_parents=1,
    )
    candidates = await mode.materialize_candidates(_job(mode="evolutionary"), spec_store=store)
    assert [c.strategy_id for c in candidates] == ["seed-a", "seed-a__child_1", "seed-a__child_1__child_2"]
    rows = await state.list_generations("experiment-edge")
    assert [row.generation for row in rows] == [0, 1, 2]

    updated = await mode.record_generation_results(
        experiment_id="experiment-edge",
        generation=1,
        child_fitness={"a": 0.4, "b": "bad"},  # type: ignore[dict-item]
    )
    assert updated.mean_fitness == 0.4
    assert updated.best_fitness == 0.4

    job_store = InMemoryExperimentJobStore()
    prior = _job(
        id="prior",
        mode="evolutionary",
        status=JobStatus.COMPLETED,
        experiment_id="older-experiment",
        result_summary={
            "results": [
                {"candidate_strategy_id": "missing", "composite_score_mean": 0.9},
                {"candidate_strategy_id": "seed-a", "composite_score_mean": 0.8},
                {"candidate_strategy_id": "seed-a", "composite_score_mean": 0.1},
                {"candidate_strategy_id": 123, "composite_score_mean": 1.0},
                {"candidate_strategy_id": "bad-score", "composite_score_mean": "x"},
            ]
        },
    )
    await job_store.create(prior)
    resolver_mode = EvolutionaryMode(
        state_store=InMemoryEvolutionStateStore(),
        experiment_job_store=job_store,
        top_k_parents=1,
    )
    top = await resolver_mode.default_parent_pool_resolver(
        _job(mode="evolutionary", experiment_id="current"),
        spec_store=store,
    )
    assert [s.strategy_id for s in top] == ["seed-a"]

    class FailingJobStore:
        async def list(self, **kwargs: Any) -> list[Any]:
            raise RuntimeError("list failed")

    assert await EvolutionaryMode(experiment_job_store=FailingJobStore()).default_parent_pool_resolver(
        _job(mode="evolutionary"),
        spec_store=store,
    ) == [seed]


@pytest.mark.asyncio
async def test_strategy_spec_selector_and_fast_path_edge_branches() -> None:
    store = InMemorySpecStore()
    fast = _spec("fast-qa", latency_target_ms=2000)
    slow = _spec("slow-qa", latency_target_ms=9000)
    local = _spec("local-qa", latency_target_ms=6000, local_only=True)
    await _seed(store, fast)
    await _seed(store, slow)
    await _seed(store, local)
    await store.upsert({"spec_data": {"strategy_id": 123}, "status": "active"})

    selector = StrategySpecSelector(store=store, legacy_adapter=None, cache_ttl_seconds=999)
    assert await selector.select(None) is None
    chosen = await selector.select("qa", tenant_id="recallhub", agent_mode="fast")
    assert chosen is not None and chosen.strategy_id == "fast-qa"
    # Cached fast spec is unsuitable for deep mode, so the selector re-resolves.
    deep = await selector.select("qa", tenant_id="recallhub", agent_mode="deep")
    assert deep is not None and deep.strategy_id in {"slow-qa", "local-qa"}
    local_only = await selector.select("qa", tenant_id="recallhub", privacy_mode="local_only")
    assert local_only is not None and local_only.strategy_id == "local-qa"

    selector._cache[("qa", "recallhub")].expires_at = 0
    assert selector.clear_cache(capability_id="qa", tenant_id="other") == 0
    assert selector.clear_cache(capability_id="qa") >= 1
    selector.invalidate_cache()

    class FailingStore:
        async def list(self, **kwargs: Any) -> list[Any]:
            raise RuntimeError("store failed")

    class EmptyLegacy:
        def get_spec(self) -> None:
            return None

    failing_selector = StrategySpecSelector(store=FailingStore(), legacy_adapter=EmptyLegacy())
    with pytest.raises(NoActiveSpecError):
        await failing_selector.select("qa", tenant_id="recallhub")
    assert await failing_selector.list_active_for_capability("qa") == []
    assert await StrategySpecSelector(store=None, legacy_adapter=None).list_active_for_capability(None) == []

    class FailingLegacy:
        def get_spec(self) -> StrategySpec:
            raise RuntimeError("legacy failed")

    assert StrategySpecSelector(store=None, legacy_adapter=FailingLegacy())._resolve_from_legacy() is None
    assert StrategySpecSelector._snapshot_to_spec("bad") is None  # type: ignore[arg-type]
    assert StrategySpecSelector._snapshot_to_spec({"spec_data": {"strategy_id": 123}}) is None
    assert StrategySpecSelector._passes_runtime_filters(slow, "fast", "standard") is False
    assert StrategySpecSelector._passes_runtime_filters(fast, "deep", "standard") is False
    assert StrategySpecSelector._passes_runtime_filters(fast, "auto", "local_only") is False

    rules = FastPathRules()
    context = type("Ctx", (), {"matter_id": "m1", "ambiguity": None, "answer_contract": type("A", (), {"has_template": True})()})()
    assert rules.evaluate(
        capability_id="legal_hearing_questions",
        business_context=context,
        top_retrieval_score=0.99,
        source_policy={"allow_web": False},
    ) is True
    assert rules.evaluate(capability_id=None, business_context=context, top_retrieval_score=0.99, source_policy={"allow_web": False}) is False
    assert rules.evaluate(capability_id="qa", business_context=None, top_retrieval_score=0.99, source_policy={"allow_web": False}) is False
    assert rules.evaluate(capability_id="qa", business_context=context, top_retrieval_score=None, source_policy={"allow_web": False}) is False
    assert rules.evaluate(capability_id="qa", business_context=context, top_retrieval_score=0.1, source_policy={"allow_web": False}) is False
    assert rules.evaluate(capability_id="qa", business_context=context, top_retrieval_score=0.99, source_policy=None) is False
    assert rules.evaluate(capability_id="legal_drafting", business_context=context, top_retrieval_score=0.99, source_policy={"allow_web": True}) is False
    assert _resolve_synth_model(_spec("roleless")) is None
    role_spec = _spec("roleful")
    role_spec.model_roles = {"primary": "gemma", "other": "llama"}
    assert _resolve_synth_model(role_spec) == "gemma"
    assert _looks_fast_variant(fast) is True
