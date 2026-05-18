"""Tests for :class:`EvolutionaryMode` (Task 77 / P8).

Covers materialization across multiple generations, the three-step
parent-pool fallback chain, fitness recording, registry plumbing and
state-store recoverability across mode re-instantiation.
"""
from __future__ import annotations

import os
from typing import Any

import pytest

from backend.agent.strategy.evolution_state_store import (
    EvolutionGenerationState,
    InMemoryEvolutionStateStore,
)
from backend.agent.strategy.evolutionary_mutator import EvolutionaryMutator
from backend.agent.strategy.experiment_job_models import (
    JobStatus,
    StrategyExperimentJob,
)
from backend.agent.strategy.experiment_job_store import (
    InMemoryExperimentJobStore,
)
from backend.agent.strategy.exploration_modes import (
    DEFAULT_EVOLUTION_GENERATIONS,
    DEFAULT_EVOLUTION_TOP_K,
    EvolutionaryMode,
    ModeRegistry,
)
from backend.agent.strategy.models import StrategySpec
from backend.agent.strategy.spec_loader import load_spec_from_yaml
from backend.agent.strategy.spec_store import InMemorySpecStore


SPEC_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "..", "config", "strategy_specs"
    )
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fast_evidence_spec() -> StrategySpec:
    """Production fast-evidence spec used as the seed parent."""
    return load_spec_from_yaml(os.path.join(SPEC_DIR, "fast_evidence_v1.yaml"))


def _spec_to_snapshot(
    spec: StrategySpec, *, status: str = "active"
) -> dict[str, Any]:
    spec.status = status  # type: ignore[assignment]
    return {
        "spec_data": spec.model_dump(mode="json"),
        "version": spec.version,
        "version_counter": 1,
        "status": status,
        "spec_hash": spec.spec_hash or "",
        "created_at": spec.created_at,
    }


async def _seed_active(
    store: InMemorySpecStore, spec: StrategySpec
) -> None:
    await store.upsert(_spec_to_snapshot(spec, status="active"))


def _make_job(
    *,
    job_id: str = "job-evo",
    experiment_id: str = "exp-evo",
    tenant: str = "shared",
) -> StrategyExperimentJob:
    return StrategyExperimentJob(
        id=job_id,
        experiment_id=experiment_id,
        tenant=tenant,
        mode="evolutionary",
        datasets=["ds-a"],
        strategy_ids=[],
        status=JobStatus.SCHEDULED,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Materialization
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_materialize_candidates_emits_seed_plus_generations(
    fast_evidence_spec: StrategySpec,
) -> None:
    """``materialize_candidates`` returns gen 0 + N evolved generations."""
    spec_store = InMemorySpecStore()
    await _seed_active(spec_store, fast_evidence_spec)

    state_store = InMemoryEvolutionStateStore()
    mode = EvolutionaryMode(
        mutator=EvolutionaryMutator(
            seed=42, mutations_per_parent=2, max_per_generation=4
        ),
        state_store=state_store,
        num_generations=2,
        top_k_parents=1,
    )
    job = _make_job(experiment_id="exp-1")

    candidates = await mode.materialize_candidates(job, spec_store=spec_store)

    assert len(candidates) >= 1
    # Generation 0 = the seed parent itself.
    assert candidates[0].strategy_id == fast_evidence_spec.strategy_id
    # Gen 1+ children carry the ``__evo_<gen>_`` segment.
    assert any("__evo_01_" in c.strategy_id for c in candidates[1:])
    rows = await state_store.list_generations("exp-1")
    assert sorted(r.generation for r in rows) == [0, 1, 2]


@pytest.mark.asyncio
async def test_materialize_falls_back_to_active_specs_when_no_history(
    fast_evidence_spec: StrategySpec,
) -> None:
    """Without prior experiments the resolver returns active production specs."""
    spec_store = InMemorySpecStore()
    await _seed_active(spec_store, fast_evidence_spec)

    job_store = InMemoryExperimentJobStore()  # empty — no prior runs
    mode = EvolutionaryMode(
        mutator=EvolutionaryMutator(seed=7, mutations_per_parent=1, max_per_generation=2),
        state_store=InMemoryEvolutionStateStore(),
        num_generations=1,
        top_k_parents=1,
        experiment_job_store=job_store,
    )
    job = _make_job(experiment_id="exp-fallback")

    candidates = await mode.materialize_candidates(job, spec_store=spec_store)

    assert candidates, "expected fallback to seed gen 0 from active specs"
    assert candidates[0].strategy_id == fast_evidence_spec.strategy_id


@pytest.mark.asyncio
async def test_materialize_returns_empty_when_no_parents(
    fast_evidence_spec: StrategySpec,
) -> None:
    """Empty store + empty job-store → empty candidates."""
    mode = EvolutionaryMode(
        mutator=EvolutionaryMutator(seed=0),
        state_store=InMemoryEvolutionStateStore(),
        num_generations=1,
        top_k_parents=1,
        experiment_job_store=InMemoryExperimentJobStore(),
    )
    job = _make_job(experiment_id="exp-empty")
    candidates = await mode.materialize_candidates(
        job, spec_store=InMemorySpecStore()
    )
    assert candidates == []


# ─────────────────────────────────────────────────────────────────────────────
# Fitness recording
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_generation_results_updates_fitness_fields() -> None:
    """``record_generation_results`` persists mean/best fitness."""
    state_store = InMemoryEvolutionStateStore()
    await state_store.upsert_generation(
        EvolutionGenerationState(
            experiment_id="exp-fit",
            generation=1,
            population_size=2,
            child_ids=["c1", "c2"],
        )
    )
    mode = EvolutionaryMode(
        mutator=EvolutionaryMutator(seed=0),
        state_store=state_store,
        num_generations=1,
    )
    updated = await mode.record_generation_results(
        experiment_id="exp-fit",
        generation=1,
        child_fitness={"c1": 0.5, "c2": 0.9},
    )
    assert updated.mean_fitness == pytest.approx(0.7)
    assert updated.best_fitness == pytest.approx(0.9)
    assert updated.completed_at is not None


@pytest.mark.asyncio
async def test_record_generation_results_creates_row_when_missing() -> None:
    """Recording for an unseen generation seeds a fresh state row."""
    state_store = InMemoryEvolutionStateStore()
    mode = EvolutionaryMode(
        mutator=EvolutionaryMutator(seed=0),
        state_store=state_store,
        num_generations=0,
    )
    updated = await mode.record_generation_results(
        experiment_id="exp-new",
        generation=0,
        child_fitness={"only": 0.3},
    )
    assert updated.experiment_id == "exp-new"
    assert updated.generation == 0
    assert updated.mean_fitness == pytest.approx(0.3)
    rows = await state_store.list_generations("exp-new")
    assert len(rows) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Registry & persistence
# ─────────────────────────────────────────────────────────────────────────────


def test_mode_registry_resolves_evolutionary() -> None:
    """``ModeRegistry`` exposes ``"evolutionary"`` and instantiates correctly."""
    assert "evolutionary" in ModeRegistry.known_modes()
    mode = ModeRegistry.get(
        "evolutionary",
        config={"num_generations": 1, "top_k_parents": 2, "seed": 5},
    )
    assert isinstance(mode, EvolutionaryMode)
    assert mode.num_generations == 1
    assert mode.top_k_parents == 2


def test_default_constants_match_brief() -> None:
    """The exported defaults match the values referenced by callers."""
    assert DEFAULT_EVOLUTION_GENERATIONS == 3
    assert DEFAULT_EVOLUTION_TOP_K == 3


@pytest.mark.asyncio
async def test_persisted_generations_recoverable_across_mode_instances(
    fast_evidence_spec: StrategySpec,
) -> None:
    """Re-instantiating ``EvolutionaryMode`` with the same store recovers state."""
    spec_store = InMemorySpecStore()
    await _seed_active(spec_store, fast_evidence_spec)

    state_store = InMemoryEvolutionStateStore()
    first = EvolutionaryMode(
        mutator=EvolutionaryMutator(seed=21, mutations_per_parent=1, max_per_generation=2),
        state_store=state_store,
        num_generations=1,
        top_k_parents=1,
    )
    job = _make_job(experiment_id="exp-recover")
    await first.materialize_candidates(job, spec_store=spec_store)

    # Fresh mode wired to the same store sees the prior generations.
    second = EvolutionaryMode(
        mutator=EvolutionaryMutator(seed=99),
        state_store=state_store,
        num_generations=0,
    )
    rows = await second.state_store.list_generations("exp-recover")
    assert sorted(r.generation for r in rows) == [0, 1]
    latest = await second.state_store.get_latest("exp-recover")
    assert latest is not None
    assert latest.generation == 1
