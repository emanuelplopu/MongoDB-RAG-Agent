"""Tests for ``backend.agent.strategy.candidate_generator``."""
from __future__ import annotations

import os
from typing import Any

import pytest

from backend.agent.strategy.candidate_generator import (
    DEFAULT_AXES,
    CandidateGenerator,
    _set_by_dotted_path,
)
from backend.agent.strategy.models import StrategySpec
from backend.agent.strategy.spec_loader import load_spec_from_yaml

SPEC_DIR = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "config",
    "strategy_specs",
)


@pytest.fixture(scope="module")
def fast_evidence_spec() -> StrategySpec:
    """Load the ``fast_evidence_v1`` base spec used by most tests."""
    path = os.path.abspath(os.path.join(SPEC_DIR, "fast_evidence_v1.yaml"))
    return load_spec_from_yaml(path)


# ───────────────────────────────────────────────────────────── default axes


def test_default_axes_generate_full_grid(fast_evidence_spec: StrategySpec) -> None:
    """Default axes produce the full Cartesian product on the fast spec."""
    expected = 1
    for values in DEFAULT_AXES.values():
        expected *= len(values)

    gen = CandidateGenerator(fast_evidence_spec)
    candidates = gen.generate()

    info = gen.describe()
    assert info["total_combinations"] == expected
    assert info["kept"] == expected
    assert info["dropped"] == 0
    assert len(candidates) == expected
    assert expected <= 12, "default grid should respect the documented cap"

    for cand in candidates:
        assert cand.status == "draft"
        assert cand.strategy_id.startswith("fast_evidence_v1__grid_")
        assert "[grid " in cand.display_name
        assert f"parent:{fast_evidence_spec.strategy_id}" in cand.tags
        assert f"parent_version:{fast_evidence_spec.version}" in cand.tags
        # Each candidate is a valid spec that compiles.
        assert cand.graph.entry_node == fast_evidence_spec.graph.entry_node


# ───────────────────────────────────────────────────────────── determinism


def test_generate_is_deterministic(fast_evidence_spec: StrategySpec) -> None:
    """Two runs with identical inputs produce identical id/name sequences."""
    gen_a = CandidateGenerator(fast_evidence_spec)
    gen_b = CandidateGenerator(fast_evidence_spec)

    a = gen_a.generate()
    b = gen_b.generate()

    assert [c.strategy_id for c in a] == [c.strategy_id for c in b]
    assert [c.display_name for c in a] == [c.display_name for c in b]
    assert [c.version for c in a] == [c.version for c in b]


# ───────────────────────────────────────────────────────────── custom axes


def test_custom_single_axis_only_mutates_target(
    fast_evidence_spec: StrategySpec,
) -> None:
    """A single-axis spec mutates only that field; other fields are stable."""
    gen = CandidateGenerator(
        fast_evidence_spec,
        axes={"budgets.max_llm_calls": [5, 10]},
    )
    candidates = gen.generate()

    assert len(candidates) == 2
    seen = sorted(c.budgets.max_llm_calls for c in candidates)
    assert seen == [5, 10]

    base = fast_evidence_spec
    for cand in candidates:
        assert cand.budgets.latency_target_ms == base.budgets.latency_target_ms
        assert cand.budgets.latency_hard_limit_ms == base.budgets.latency_hard_limit_ms
        assert cand.budgets.max_context_tokens == base.budgets.max_context_tokens
        assert (
            [n.node_id for n in cand.graph.nodes]
            == [n.node_id for n in base.graph.nodes]
        )


# ───────────────────────────────────────────────────────────── invalid drops


def test_invalid_mutation_is_dropped_not_raised(
    fast_evidence_spec: StrategySpec,
) -> None:
    """Setting a node_type to a value the registry rejects drops the candidate."""
    gen = CandidateGenerator(
        fast_evidence_spec,
        axes={"nodes.n_retrieve.node_type": ["__definitely_not_a_real_node_type__"]},
    )
    candidates = gen.generate()

    assert candidates == []
    info = gen.describe()
    assert info["total_combinations"] == 1
    assert info["kept"] == 0
    assert info["dropped"] == 1
    assert info["dropped_reasons"], "drop reasons must be recorded"
    reason = info["dropped_reasons"][0]["reason"]
    assert "validate_spec" in reason or "apply_error" in reason


def test_unknown_node_id_is_dropped_with_apply_error(
    fast_evidence_spec: StrategySpec,
) -> None:
    """Targeting a non-existent node yields an apply_error, not a crash."""
    gen = CandidateGenerator(
        fast_evidence_spec,
        axes={"nodes.n_does_not_exist.config.top_k": [10]},
    )
    candidates = gen.generate()

    assert candidates == []
    info = gen.describe()
    assert info["dropped"] == 1
    assert "apply_error" in info["dropped_reasons"][0]["reason"]


# ───────────────────────────────────────────────────────────── cap behavior


def test_max_candidates_cap_is_respected(fast_evidence_spec: StrategySpec) -> None:
    """When the grid exceeds the cap, only ``max_candidates`` are kept."""
    axes: dict[str, list[Any]] = {
        "budgets.max_llm_calls": [1, 2, 3, 4, 5, 6],
        "budgets.max_context_tokens": [1000, 2000],
    }
    gen = CandidateGenerator(fast_evidence_spec, axes=axes, max_candidates=4)
    candidates = gen.generate()

    info = gen.describe()
    assert info["total_combinations"] == 12
    assert info["kept"] == 4
    assert info["dropped"] == 0
    assert len(candidates) == 4
    # Strategy ids are unique within the truncated batch.
    assert len({c.strategy_id for c in candidates}) == 4


def test_cap_truncation_is_deterministic(fast_evidence_spec: StrategySpec) -> None:
    """Truncation order is stable across runs."""
    axes: dict[str, list[Any]] = {
        "budgets.max_llm_calls": [1, 2, 3, 4, 5, 6],
        "budgets.max_context_tokens": [1000, 2000],
    }
    a = CandidateGenerator(fast_evidence_spec, axes=axes, max_candidates=5).generate()
    b = CandidateGenerator(fast_evidence_spec, axes=axes, max_candidates=5).generate()
    assert [c.strategy_id for c in a] == [c.strategy_id for c in b]


# ───────────────────────────────────────────────────────────── helper unit


def test_set_by_dotted_path_node_config() -> None:
    """The dotted-path helper updates a node's config in place."""
    spec_dict: dict[str, Any] = {
        "graph": {
            "nodes": [
                {"node_id": "n_retrieve", "node_type": "retrieve", "config": {"top_k": 1}},
                {"node_id": "n_evidence", "node_type": "evidence_cards", "config": {}},
            ]
        },
        "budgets": {"max_llm_calls": 10},
    }

    _set_by_dotted_path(spec_dict, "nodes.n_retrieve.config.top_k", 42)
    _set_by_dotted_path(spec_dict, "budgets.max_llm_calls", 99)

    assert spec_dict["graph"]["nodes"][0]["config"]["top_k"] == 42
    assert spec_dict["budgets"]["max_llm_calls"] == 99


def test_set_by_dotted_path_unknown_node_raises() -> None:
    """Unknown ``node_id`` raises ``KeyError`` (caller catches and drops)."""
    spec_dict: dict[str, Any] = {"graph": {"nodes": []}}
    with pytest.raises(KeyError):
        _set_by_dotted_path(spec_dict, "nodes.missing.config.x", 1)
