"""Tests for :mod:`backend.agent.strategy.evolutionary_mutator` (Task 77 / P8).

These tests pin down two contracts:

* Each operator either yields a validated child :class:`StrategySpec` or
  returns ``None`` (never raises) for an inapplicable parent.
* :class:`EvolutionaryMutator` produces deterministic, lineage-tagged,
  capped child populations under a fixed RNG seed.
"""
from __future__ import annotations

import os
from typing import Optional

import pytest

from backend.agent.strategy.evolutionary_mutator import (
    DEFAULT_MUTATION_OPERATORS,
    EvolutionaryMutator,
    add_evidence_cards_node,
    add_refinement_node,
    add_reranker_node,
    add_validation_node,
    alter_query_expansion_count,
    change_contract_strictness,
    reduce_context_budget,
    remove_evidence_cards_node,
    remove_reranker_node,
    switch_synthesis_model,
)
from backend.agent.strategy.models import AnswerContract, StrategySpec
from backend.agent.strategy.spec_loader import load_spec_from_yaml, validate_spec


SPEC_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "..", "config", "strategy_specs"
    )
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fast_evidence_spec() -> StrategySpec:
    """Load the production ``fast_evidence_v1`` spec."""
    return load_spec_from_yaml(os.path.join(SPEC_DIR, "fast_evidence_v1.yaml"))


@pytest.fixture(scope="module")
def deep_orchestrated_spec() -> StrategySpec:
    """Load the production ``deep_orchestrated_v1`` spec (has rerank + expand)."""
    return load_spec_from_yaml(
        os.path.join(SPEC_DIR, "deep_orchestrated_v1.yaml")
    )


@pytest.fixture()
def fast_evidence_with_contract(fast_evidence_spec: StrategySpec) -> StrategySpec:
    """A clone of fast_evidence augmented with an answer contract."""
    spec = fast_evidence_spec.model_copy(deep=True)
    spec.answer_contract = AnswerContract(
        format_id="default_answer_v1",
        unsupported_claim_policy="flag",
    )
    return spec


def _has_tag(spec: StrategySpec, prefix: str) -> bool:
    return any(t.startswith(prefix) for t in spec.tags)


# ─────────────────────────────────────────────────────────────────────────────
# Per-operator behaviour
# ─────────────────────────────────────────────────────────────────────────────


def test_reduce_context_budget_shrinks_value(
    fast_evidence_spec: StrategySpec,
) -> None:
    """``reduce_context_budget`` returns a spec with a strictly smaller budget."""
    parent_budget = fast_evidence_spec.budgets.max_context_tokens
    mutated = reduce_context_budget(fast_evidence_spec, factor=0.5)
    assert mutated is not None
    assert mutated.budgets.max_context_tokens < parent_budget
    assert validate_spec(mutated) == []


def test_reduce_context_budget_below_floor_drops(
    fast_evidence_spec: StrategySpec,
) -> None:
    """Result below the ``minimum_tokens`` floor drops to ``None``."""
    out = reduce_context_budget(
        fast_evidence_spec, factor=0.01, minimum_tokens=2000
    )
    assert out is None


def test_switch_synthesis_model_picks_alternative(
    fast_evidence_spec: StrategySpec,
) -> None:
    """``switch_synthesis_model`` rotates the synthesizer ``model_role``."""
    parent_role = next(
        (
            (n.config or {}).get("model_role")
            for n in fast_evidence_spec.graph.nodes
            if n.node_type == "synthesize"
        ),
        None,
    )
    mutated = switch_synthesis_model(fast_evidence_spec)
    assert mutated is not None
    new_role = next(
        (
            (n.config or {}).get("model_role")
            for n in mutated.graph.nodes
            if n.node_type == "synthesize"
        ),
        None,
    )
    assert new_role is not None
    assert new_role != parent_role


def test_add_evidence_cards_returns_none_when_already_present(
    fast_evidence_spec: StrategySpec,
) -> None:
    """``add_evidence_cards_node`` is a no-op when ``n_evidence`` exists."""
    assert add_evidence_cards_node(fast_evidence_spec) is None


def test_add_reranker_node_inserts_between_retrieve_and_consumer(
    fast_evidence_spec: StrategySpec,
) -> None:
    """``add_reranker_node`` produces a valid spec with a ``rerank`` node."""
    mutated = add_reranker_node(fast_evidence_spec)
    assert mutated is not None
    types = [n.node_type for n in mutated.graph.nodes]
    assert "rerank" in types
    assert validate_spec(mutated) == []


def test_remove_reranker_node_drops_rerank_step(
    deep_orchestrated_spec: StrategySpec,
) -> None:
    """``remove_reranker_node`` drops the rerank node when present."""
    mutated = remove_reranker_node(deep_orchestrated_spec)
    assert mutated is not None
    assert all(n.node_type != "rerank" for n in mutated.graph.nodes)
    assert validate_spec(mutated) == []


def test_change_contract_strictness_rotates_policy(
    fast_evidence_with_contract: StrategySpec,
) -> None:
    """The contract strictness ladder rotates ``flag`` → ``omit``."""
    mutated = change_contract_strictness(fast_evidence_with_contract)
    assert mutated is not None
    assert mutated.answer_contract is not None
    assert (
        mutated.answer_contract.unsupported_claim_policy
        != fast_evidence_with_contract.answer_contract.unsupported_claim_policy
    )


def test_change_contract_strictness_returns_none_without_contract(
    fast_evidence_spec: StrategySpec,
) -> None:
    """No ``answer_contract`` → operator declines (returns ``None``)."""
    assert change_contract_strictness(fast_evidence_spec) is None


def test_alter_query_expansion_count_changes_max_variants(
    deep_orchestrated_spec: StrategySpec,
) -> None:
    """The query-expand mutation rotates ``max_variants`` to a new value."""
    mutated = alter_query_expansion_count(deep_orchestrated_spec)
    assert mutated is not None
    expand_node = next(
        n for n in mutated.graph.nodes if n.node_type == "query_expand"
    )
    parent_expand = next(
        n
        for n in deep_orchestrated_spec.graph.nodes
        if n.node_type == "query_expand"
    )
    assert (expand_node.config or {}).get("max_variants") != (
        parent_expand.config or {}
    ).get("max_variants")


def test_add_validation_node_appends_terminal_validate(
    fast_evidence_spec: StrategySpec,
) -> None:
    """``add_validation_node`` extends the terminal path with ``validate_contract``."""
    mutated = add_validation_node(fast_evidence_spec)
    assert mutated is not None
    types = [n.node_type for n in mutated.graph.nodes]
    assert "validate_contract" in types
    assert "n_validate" in mutated.graph.terminal_nodes
    assert validate_spec(mutated) == []


def test_add_refinement_node_uses_evo_specific_id(
    fast_evidence_spec: StrategySpec,
) -> None:
    """``add_refinement_node`` defaults to ``n_refine_evo`` to avoid clashes."""
    mutated = add_refinement_node(fast_evidence_spec)
    assert mutated is not None
    ids = [n.node_id for n in mutated.graph.nodes]
    assert "n_refine_evo" in ids
    assert validate_spec(mutated) == []


# ─────────────────────────────────────────────────────────────────────────────
# EvolutionaryMutator
# ─────────────────────────────────────────────────────────────────────────────


def test_mutate_with_seed_is_deterministic(
    fast_evidence_spec: StrategySpec,
) -> None:
    """Same seed + same parents → identical (id, version) pairs."""
    m1 = EvolutionaryMutator(seed=42, mutations_per_parent=3, max_per_generation=8)
    m2 = EvolutionaryMutator(seed=42, mutations_per_parent=3, max_per_generation=8)
    children1 = m1.mutate(parents=[fast_evidence_spec], generation=1)
    children2 = m2.mutate(parents=[fast_evidence_spec], generation=1)
    assert [(c.strategy_id, c.version) for c in children1] == [
        (c.strategy_id, c.version) for c in children2
    ]


def test_mutate_different_seeds_diverge(
    fast_evidence_spec: StrategySpec,
) -> None:
    """Distinct seeds should pick distinct operator sequences."""
    m1 = EvolutionaryMutator(seed=1, mutations_per_parent=4, max_per_generation=8)
    m2 = EvolutionaryMutator(seed=999, mutations_per_parent=4, max_per_generation=8)
    children1 = m1.mutate(parents=[fast_evidence_spec], generation=2)
    children2 = m2.mutate(parents=[fast_evidence_spec], generation=2)
    # Some divergence must exist somewhere — operator picks differ.
    sequence1 = m1.describe()["attempts"]
    sequence2 = m2.describe()["attempts"]
    assert sequence1 != sequence2 or [c.strategy_id for c in children1] != [
        c.strategy_id for c in children2
    ]


def test_mutate_caps_at_max_per_generation(
    fast_evidence_spec: StrategySpec,
) -> None:
    """Children list must never exceed ``max_per_generation``."""
    mutator = EvolutionaryMutator(
        seed=7, mutations_per_parent=10, max_per_generation=3
    )
    children = mutator.mutate(
        parents=[fast_evidence_spec, fast_evidence_spec],
        generation=1,
    )
    assert len(children) <= 3


def test_mutate_handles_invalid_operator_silently(
    fast_evidence_spec: StrategySpec,
) -> None:
    """An operator that raises is logged and skipped, not propagated."""

    def boom(_spec: StrategySpec) -> Optional[StrategySpec]:
        raise RuntimeError("operator blew up")

    mutator = EvolutionaryMutator(
        operators=(("boom", boom), ("reduce_context_budget", reduce_context_budget)),
        seed=3,
        mutations_per_parent=2,
        max_per_generation=4,
    )
    children = mutator.mutate(parents=[fast_evidence_spec], generation=1)
    # The boom operator must have run at least once and produced no
    # children, while reduce_context_budget can still contribute.
    telemetry = mutator.describe()
    assert telemetry["attempts"].get("boom", 0) >= 1
    assert telemetry["successes"].get("boom", 0) == 0
    for child in children:
        assert isinstance(child, StrategySpec)


def test_mutate_applies_lineage_tags(
    fast_evidence_spec: StrategySpec,
) -> None:
    """Each child carries parent / generation / mutation lineage tags."""
    mutator = EvolutionaryMutator(seed=11, mutations_per_parent=2, max_per_generation=4)
    children = mutator.mutate(parents=[fast_evidence_spec], generation=2)
    assert children, "expected at least one mutated child"
    for child in children:
        assert _has_tag(child, "parent:")
        assert _has_tag(child, "parent_version:")
        assert "generation:2" in child.tags
        assert _has_tag(child, "mutation:")
        assert child.status == "draft"
        assert child.strategy_id.startswith(
            f"{fast_evidence_spec.strategy_id}__evo_02_"
        )
        assert child.version.startswith(f"{fast_evidence_spec.version}+evo.2.")


def test_mutate_empty_parents_returns_empty() -> None:
    """No parents → no children, no error."""
    mutator = EvolutionaryMutator(seed=0)
    assert mutator.mutate(parents=[], generation=1) == []


def test_mutate_default_operators_match_blueprint() -> None:
    """The default registry exposes all 10 Blueprint 05 §6.3 operators."""
    names = {name for name, _ in DEFAULT_MUTATION_OPERATORS}
    assert names == {
        "reduce_context_budget",
        "switch_synthesis_model",
        "add_evidence_cards_node",
        "remove_evidence_cards_node",
        "add_reranker_node",
        "remove_reranker_node",
        "change_contract_strictness",
        "alter_query_expansion_count",
        "add_validation_node",
        "add_refinement_node",
    }
