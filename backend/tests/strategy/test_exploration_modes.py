"""Tests for :mod:`backend.agent.strategy.exploration_modes` (Task 75 / P6)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from backend.agent.strategy.candidate_generator import CandidateGenerator
from backend.agent.strategy.experiment_job_models import (
    JobStatus,
    StrategyExperimentJob,
)
from backend.agent.strategy.exploration_modes import (
    DEFAULT_REGRESSION_THRESHOLDS,
    GridMode,
    ModeRegistry,
    MutationGridYAMLError,
    RegressionMode,
    SmokeMode,
    UnknownExplorationModeError,
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
    """Load the production ``fast_evidence_v1`` spec used as the grid base."""
    return load_spec_from_yaml(os.path.join(SPEC_DIR, "fast_evidence_v1.yaml"))


def _spec_to_snapshot(spec: StrategySpec, *, status: str = "active") -> dict[str, Any]:
    """Wrap a :class:`StrategySpec` in the snapshot envelope the store expects."""
    spec.status = status  # type: ignore[assignment]
    return {
        "spec_data": spec.model_dump(mode="json"),
        "version": spec.version,
        "version_counter": 1,
        "status": status,
        "spec_hash": spec.spec_hash or "",
        "created_at": spec.created_at,
    }


async def _seed_store(
    store: InMemorySpecStore,
    spec: StrategySpec,
    *,
    status: str = "active",
) -> None:
    await store.upsert(_spec_to_snapshot(spec, status=status))


def _make_job(
    *,
    mode: str = "grid",
    strategy_ids: list[str] | None = None,
    tenant: str = "shared",
) -> StrategyExperimentJob:
    return StrategyExperimentJob(
        id="job-test",
        tenant=tenant,
        mode=mode,  # type: ignore[arg-type]
        datasets=["ds-a"],
        strategy_ids=list(strategy_ids or []),
        status=JobStatus.SCHEDULED,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GridMode
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grid_mode_inline_axes_produces_validated_candidates(
    fast_evidence_spec: StrategySpec,
) -> None:
    """Inline axes → CandidateGenerator yields N draft StrategySpecs."""
    store = InMemorySpecStore()
    await _seed_store(store, fast_evidence_spec)

    mode = GridMode(
        mutation_grid={
            "nodes.n_retrieve.config.top_k": [10, 20],
            "budgets.max_llm_calls": [5, 10],
        },
        max_candidates=12,
    )
    job = _make_job(strategy_ids=[fast_evidence_spec.strategy_id])

    candidates = await mode.materialize_candidates(job, spec_store=store)

    assert len(candidates) == 4
    for cand in candidates:
        assert isinstance(cand, StrategySpec)
        assert cand.status == "draft"
        assert cand.strategy_id.startswith(
            f"{fast_evidence_spec.strategy_id}__grid_"
        )
        assert f"parent:{fast_evidence_spec.strategy_id}" in cand.tags


@pytest.mark.asyncio
async def test_grid_mode_yaml_path_matches_inline_axes(
    fast_evidence_spec: StrategySpec, tmp_path: Path
) -> None:
    """A YAML mutation_grid loads to the same axes as the inline form."""
    store = InMemorySpecStore()
    await _seed_store(store, fast_evidence_spec)

    yaml_path = tmp_path / "grid.yaml"
    yaml_path.write_text(
        "mutation_grid:\n"
        "  nodes.n_retrieve.config.top_k: [10, 20]\n"
        "  budgets.max_llm_calls: [5, 10]\n",
        encoding="utf-8",
    )

    inline_mode = GridMode(
        mutation_grid={
            "nodes.n_retrieve.config.top_k": [10, 20],
            "budgets.max_llm_calls": [5, 10],
        },
    )
    yaml_mode = GridMode(mutation_grid_yaml_path=yaml_path)

    job = _make_job(strategy_ids=[fast_evidence_spec.strategy_id])
    inline_ids = [
        c.strategy_id
        for c in await inline_mode.materialize_candidates(job, spec_store=store)
    ]
    yaml_ids = [
        c.strategy_id
        for c in await yaml_mode.materialize_candidates(job, spec_store=store)
    ]
    assert inline_ids == yaml_ids
    assert len(yaml_ids) == 4


@pytest.mark.asyncio
async def test_grid_mode_drops_invalid_candidates(
    fast_evidence_spec: StrategySpec,
) -> None:
    """Candidates that fail ``validate_spec`` are dropped (Jay's behavior)."""
    store = InMemorySpecStore()
    await _seed_store(store, fast_evidence_spec)

    mode = GridMode(
        mutation_grid={
            "nodes.n_retrieve.node_type": [
                "__definitely_not_a_real_node_type__",
            ],
        },
    )
    job = _make_job(strategy_ids=[fast_evidence_spec.strategy_id])
    candidates = await mode.materialize_candidates(job, spec_store=store)
    assert candidates == []


@pytest.mark.asyncio
async def test_grid_mode_raises_on_unknown_base_strategy(
    fast_evidence_spec: StrategySpec,
) -> None:
    """An empty store cannot resolve the grid base spec."""
    store = InMemorySpecStore()
    mode = GridMode(mutation_grid={"budgets.max_llm_calls": [5]})
    job = _make_job(strategy_ids=["does-not-exist"])
    with pytest.raises(ValueError, match="could not resolve base strategy"):
        await mode.materialize_candidates(job, spec_store=store)


def test_grid_mode_yaml_loader_reports_path_on_bad_schema(tmp_path: Path) -> None:
    """A YAML file without a ``mutation_grid`` key raises a clear error."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("not_a_grid: {}\n", encoding="utf-8")
    with pytest.raises(MutationGridYAMLError) as excinfo:
        GridMode._load_yaml_axes(bad)
    assert str(bad) in str(excinfo.value)


def test_grid_mode_yaml_loader_reports_line_on_parse_error(
    tmp_path: Path,
) -> None:
    """A malformed YAML file surfaces the parser's line number."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("mutation_grid:\n  - this is: : invalid", encoding="utf-8")
    with pytest.raises(MutationGridYAMLError) as excinfo:
        GridMode._load_yaml_axes(bad)
    assert excinfo.value.line is not None


@pytest.mark.asyncio
async def test_grid_mode_uses_candidate_generator_factory(
    fast_evidence_spec: StrategySpec,
) -> None:
    """A custom factory is used when supplied."""
    store = InMemorySpecStore()
    await _seed_store(store, fast_evidence_spec)

    captured: dict[str, Any] = {}

    def factory(*, base_spec: StrategySpec, axes: Any, max_candidates: int) -> CandidateGenerator:
        captured["base_id"] = base_spec.strategy_id
        captured["axes"] = axes
        captured["cap"] = max_candidates
        return CandidateGenerator(
            base_spec=base_spec, axes=axes, max_candidates=max_candidates
        )

    mode = GridMode(mutation_grid={"budgets.max_llm_calls": [5]})
    job = _make_job(strategy_ids=[fast_evidence_spec.strategy_id])
    cands = await mode.materialize_candidates(
        job, spec_store=store, candidate_generator_factory=factory
    )
    assert len(cands) == 1
    assert captured["base_id"] == fast_evidence_spec.strategy_id
    assert captured["axes"] == {"budgets.max_llm_calls": [5]}


# ─────────────────────────────────────────────────────────────────────────────
# RegressionMode
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_regression_mode_returns_active_strategies_unchanged(
    fast_evidence_spec: StrategySpec,
) -> None:
    """RegressionMode yields the production strategies; no mutation."""
    store = InMemorySpecStore()
    await _seed_store(store, fast_evidence_spec, status="active")

    mode = RegressionMode()
    # No strategy_ids → load all active for the tenant.
    job = _make_job(
        mode="regression",
        strategy_ids=[],
        tenant=fast_evidence_spec.tenant_scope,
    )
    candidates = await mode.materialize_candidates(job, spec_store=store)

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.strategy_id == fast_evidence_spec.strategy_id
    assert cand.status == "active"
    # No grid lineage tags applied.
    assert not any(t.startswith("parent:") for t in cand.tags)


@pytest.mark.asyncio
async def test_regression_mode_explicit_strategy_ids(
    fast_evidence_spec: StrategySpec,
) -> None:
    """Explicit strategy_ids resolve via the store; missing ids are skipped."""
    store = InMemorySpecStore()
    await _seed_store(store, fast_evidence_spec, status="active")

    mode = RegressionMode()
    job = _make_job(
        mode="regression",
        strategy_ids=[fast_evidence_spec.strategy_id, "missing-strategy"],
    )
    candidates = await mode.materialize_candidates(job, spec_store=store)
    assert [c.strategy_id for c in candidates] == [fast_evidence_spec.strategy_id]


def test_regression_mode_triggers_match_blueprint() -> None:
    """The four blueprint §6.4 triggers are exposed verbatim."""
    triggers = RegressionMode().regression_triggers()
    assert triggers == [
        "score_drop_exceeds_threshold",
        "forbidden_source_leak",
        "citation_coverage_drop",
        "latency_hard_limit_breach",
    ]
    # Default thresholds are the documented values.
    assert (
        RegressionMode().thresholds["score_drop_pct"]
        == DEFAULT_REGRESSION_THRESHOLDS["score_drop_pct"]
    )


# ─────────────────────────────────────────────────────────────────────────────
# SmokeMode
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_smoke_mode_returns_single_strategy(
    fast_evidence_spec: StrategySpec,
) -> None:
    """SmokeMode resolves exactly one StrategySpec via the store."""
    store = InMemorySpecStore()
    await _seed_store(store, fast_evidence_spec, status="active")

    mode = SmokeMode(sample_count=3)
    job = _make_job(mode="smoke", strategy_ids=[fast_evidence_spec.strategy_id])
    candidates = await mode.materialize_candidates(job, spec_store=store)
    assert len(candidates) == 1
    assert candidates[0].strategy_id == fast_evidence_spec.strategy_id


def test_smoke_mode_dataset_subsample_returns_first_n() -> None:
    """``dataset_subsample`` is deterministic and head-truncating."""
    mode = SmokeMode(sample_count=3)
    cases = [f"case-{i}" for i in range(10)]
    assert mode.dataset_subsample(cases) == ["case-0", "case-1", "case-2"]
    # Override count.
    assert mode.dataset_subsample(cases, count=2) == ["case-0", "case-1"]
    # Edge: subsample larger than dataset returns all.
    assert mode.dataset_subsample(["only"], count=5) == ["only"]
    # Edge: count <= 0 → empty.
    assert mode.dataset_subsample(cases, count=0) == []


# ─────────────────────────────────────────────────────────────────────────────
# ModeRegistry
# ─────────────────────────────────────────────────────────────────────────────


def test_mode_registry_dispatch_for_built_in_modes() -> None:
    """``grid`` / ``regression`` / ``smoke`` resolve to their classes."""
    assert isinstance(ModeRegistry.get("grid"), GridMode)
    assert isinstance(ModeRegistry.get("regression"), RegressionMode)
    assert isinstance(ModeRegistry.get("smoke"), SmokeMode)


def test_mode_registry_forwards_config() -> None:
    """``config`` kwargs are forwarded to the registered factory."""
    instance = ModeRegistry.get("smoke", config={"sample_count": 7})
    assert isinstance(instance, SmokeMode)
    assert instance.sample_count == 7


def test_mode_registry_unknown_mode_raises() -> None:
    """Unknown mode names raise :class:`UnknownExplorationModeError`.

    ``bandit`` lands in P7 (Task 76); once registered it is no longer a
    valid "unknown" probe. We use a sentinel name that is guaranteed
    not to be registered.
    """
    sentinel = "__definitely_not_a_real_mode__"
    with pytest.raises(UnknownExplorationModeError) as excinfo:
        ModeRegistry.get(sentinel)
    assert excinfo.value.mode_name == sentinel
