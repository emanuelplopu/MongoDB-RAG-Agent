"""Mutation operators and ``EvolutionaryMutator`` for the evolutionary mode (Task 77 / P8).

This module implements the population-evolution primitives described in
Blueprint 05 §6.3. It is the spiritual sibling of
:mod:`backend.agent.strategy.candidate_generator` (Task 63), but where
``CandidateGenerator`` produces a *Cartesian* grid of mutations from a
single base spec the operators here apply *single-shot* mutations to a
list of parent specs and combine them through random operator selection.

The eleven mutation operators ship as plain async-free callables so they
can be unit-tested in isolation. Each operator:

* deep-copies the input :class:`~backend.agent.strategy.models.StrategySpec`
  via ``model_dump(mode="json")`` round-tripped through ``json``;
* applies *one* targeted change to the dump;
* re-hydrates a :class:`StrategySpec` and validates it via
  :func:`backend.agent.strategy.spec_loader.validate_spec`;
* returns the mutated :class:`StrategySpec` on success or ``None`` when
  the mutation produced an invalid candidate (for example removing the
  only ``n_evidence`` node when the synthesizer consumes evidence
  cards). Failures are logged at ``WARNING`` and **never** raised — the
  evolutionary mode treats a failed operator as "skip this child".

Operator selection in :class:`EvolutionaryMutator` is governed by a
seeded :class:`random.Random` instance so the same ``seed`` always
produces the same children for a given parent pool. Lineage tags
(``parent:``, ``parent_version:``, ``generation:``, ``mutation:``) and
identity fields (``strategy_id``, ``version``, ``status``) are applied
*after* the operator returns — operators themselves are kept deliberately
oblivious to lineage so they can be reused outside the evolutionary
loop (e.g. by P10 ad-hoc what-if reports).
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import random
from typing import Any, Callable, Optional

from backend.agent.strategy.models import StrategySpec
from backend.agent.strategy.spec_loader import validate_spec

logger = logging.getLogger(__name__)

__all__ = [
    "MutationOperator",
    "DEFAULT_MUTATION_OPERATORS",
    "DEFAULT_SYNTH_ROLE_ALTERNATIVES",
    "DEFAULT_CONTRACT_STRICTNESS_LEVELS",
    "DEFAULT_QUERY_EXPANSION_RANGE",
    "EvolutionaryMutator",
    # Operators
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
]


# ─────────────────────────────────────────────────────────────────────────────
# Type aliases & defaults
# ─────────────────────────────────────────────────────────────────────────────


#: Callable shape every operator implements. The keyword-only signature
#: makes operator-specific knobs explicit at the call site so the
#: registry stays readable.
MutationOperator = Callable[..., Optional[StrategySpec]]


#: Default candidate roles for :func:`switch_synthesis_model`. Picked
#: to match the role names shipped in
#: ``backend/config/strategy_specs/*.yaml``.
DEFAULT_SYNTH_ROLE_ALTERNATIVES: tuple[str, ...] = (
    "synthesizer_fast",
    "synthesizer_deep",
    "worker",
)


#: Default policy ladder for :func:`change_contract_strictness`. The
#: operator rotates the answer-contract's ``unsupported_claim_policy``
#: along this ordering.
DEFAULT_CONTRACT_STRICTNESS_LEVELS: tuple[str, ...] = (
    "lenient",
    "moderate",
    "strict",
)


#: Inclusive ``(low, high)`` bounds for :func:`alter_query_expansion_count`.
DEFAULT_QUERY_EXPANSION_RANGE: tuple[int, int] = (1, 4)


_STRICTNESS_TO_POLICY: dict[str, str] = {
    "lenient": "allow",
    "moderate": "flag",
    "strict": "omit",
}


_POLICY_TO_STRICTNESS: dict[str, str] = {
    v: k for k, v in _STRICTNESS_TO_POLICY.items()
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _deep_dump(spec: StrategySpec) -> dict[str, Any]:
    """Return an isolated JSON-mode dump of ``spec``.

    The double-encode insulates the caller from any nested
    ``Pydantic`` / ``datetime`` aliasing so operator mutations cannot
    leak back into the original parent spec.
    """
    return json.loads(json.dumps(spec.model_dump(mode="json"), default=str))


def _hydrate(spec_dict: dict[str, Any]) -> Optional[StrategySpec]:
    """Re-hydrate a :class:`StrategySpec` from a mutated dump.

    Returns ``None`` and logs at ``WARNING`` when Pydantic rejects the
    payload — operators must never raise on invalid intermediate
    shapes.
    """
    try:
        return StrategySpec.model_validate(spec_dict)
    except Exception as exc:  # noqa: BLE001 - swallow per contract
        logger.warning("EvolutionaryMutator hydration failed: %s", exc)
        return None


def _validate(spec: StrategySpec, op_name: str) -> Optional[StrategySpec]:
    """Run :func:`validate_spec` and return ``spec`` on success.

    Errors are joined into a single ``WARNING`` log line; ``None`` is
    returned so the caller drops the candidate.
    """
    errors = validate_spec(spec)
    if errors:
        logger.warning(
            "EvolutionaryMutator operator %r produced invalid candidate: %s",
            op_name,
            "; ".join(errors),
        )
        return None
    return spec


def _find_node_index(
    spec_dict: dict[str, Any], node_id: str
) -> Optional[int]:
    """Return the index of ``node_id`` in ``graph.nodes`` or ``None``."""
    nodes = spec_dict.get("graph", {}).get("nodes")
    if not isinstance(nodes, list):
        return None
    for i, node in enumerate(nodes):
        if isinstance(node, dict) and node.get("node_id") == node_id:
            return i
    return None


def _find_node_by_type(
    spec_dict: dict[str, Any], node_type: str
) -> Optional[tuple[int, dict[str, Any]]]:
    """Return ``(index, node)`` for the first node matching ``node_type``."""
    nodes = spec_dict.get("graph", {}).get("nodes")
    if not isinstance(nodes, list):
        return None
    for i, node in enumerate(nodes):
        if isinstance(node, dict) and node.get("node_type") == node_type:
            return i, node
    return None


def _retire_edges_through(
    spec_dict: dict[str, Any], node_id: str
) -> None:
    """Re-route edges around ``node_id`` (collapse predecessor → successor).

    The operator semantics expect a single predecessor and successor
    for the removed node. When the topology is more complex we drop
    every edge touching ``node_id`` and let the caller's graph
    validator surface any breakage.
    """
    edges = spec_dict.get("graph", {}).get("edges")
    if not isinstance(edges, list):
        return
    incoming = [e for e in edges if isinstance(e, dict) and e.get("to_node") == node_id]
    outgoing = [e for e in edges if isinstance(e, dict) and e.get("from_node") == node_id]
    remaining = [
        e
        for e in edges
        if isinstance(e, dict)
        and e.get("from_node") != node_id
        and e.get("to_node") != node_id
    ]
    # Splice predecessor → successor when the topology is linear.
    if len(incoming) == 1 and len(outgoing) == 1:
        bridged = {
            "from_node": incoming[0]["from_node"],
            "to_node": outgoing[0]["to_node"],
        }
        if bridged not in remaining:
            remaining.append(bridged)
    spec_dict["graph"]["edges"] = remaining


def _replace_inbound_edges(
    spec_dict: dict[str, Any],
    *,
    target_node: str,
    new_predecessor: str,
) -> None:
    """Insert ``new_predecessor`` between ``target_node`` and its parents.

    Rewrites every ``edge.to_node == target_node`` so it now points to
    ``new_predecessor`` and adds a single ``new_predecessor → target_node``
    edge. Used by the ``add_*_node`` operators that splice a brand-new
    node directly upstream of an existing one.
    """
    edges = spec_dict.get("graph", {}).get("edges")
    if not isinstance(edges, list):
        return
    rewritten: list[dict[str, Any]] = []
    inserted = False
    for edge in edges:
        if not isinstance(edge, dict):
            rewritten.append(edge)
            continue
        if edge.get("to_node") == target_node:
            new_edge = dict(edge)
            new_edge["to_node"] = new_predecessor
            rewritten.append(new_edge)
            inserted = True
        else:
            rewritten.append(edge)
    if inserted:
        bridge = {"from_node": new_predecessor, "to_node": target_node}
        if bridge not in rewritten:
            rewritten.append(bridge)
    spec_dict["graph"]["edges"] = rewritten


def _append_after_terminal(
    spec_dict: dict[str, Any],
    *,
    new_node_id: str,
) -> None:
    """Append ``new_node_id`` after the current terminal and update state.

    Retires the existing terminal in favour of ``new_node_id``, adding
    a single bridge edge from the previous terminal to the new one.
    Assumes the spec has exactly one terminal node — the only shape
    the shipped sample specs use.
    """
    graph = spec_dict.setdefault("graph", {})
    terminals = graph.get("terminal_nodes") or []
    if not isinstance(terminals, list) or len(terminals) != 1:
        # Conservative: we don't know which terminal to extend, so make
        # the operator a no-op by not adding an edge — validation will
        # then drop the candidate which is the desired behaviour.
        return
    previous_terminal = terminals[0]
    edges = graph.setdefault("edges", [])
    if not isinstance(edges, list):
        return
    edges.append({"from_node": previous_terminal, "to_node": new_node_id})
    graph["terminal_nodes"] = [new_node_id]


# ─────────────────────────────────────────────────────────────────────────────
# Mutation operators
# ─────────────────────────────────────────────────────────────────────────────


def reduce_context_budget(
    spec: StrategySpec,
    *,
    factor: float = 0.7,
    minimum_tokens: int = 1024,
) -> Optional[StrategySpec]:
    """Shrink the context-token budget by ``factor``.

    Args:
        spec: Parent spec to mutate.
        factor: Multiplier applied to ``budgets.max_context_tokens``.
            Must be in ``(0.0, 1.0]``.
        minimum_tokens: Floor for the resulting budget; the operator
            returns ``None`` when the new value would fall below this.

    Returns:
        Mutated :class:`StrategySpec` or ``None`` when the resulting
        budget is non-positive, equal to the parent, or fails
        validation.
    """
    if not 0.0 < factor <= 1.0:
        return None
    spec_dict = _deep_dump(spec)
    budgets = spec_dict.get("budgets")
    if not isinstance(budgets, dict):
        return None
    current = int(budgets.get("max_context_tokens") or 0)
    if current <= 0:
        return None
    new_value = int(current * float(factor))
    if new_value <= 0 or new_value < minimum_tokens or new_value == current:
        return None
    budgets["max_context_tokens"] = new_value
    hydrated = _hydrate(spec_dict)
    if hydrated is None:
        return None
    return _validate(hydrated, "reduce_context_budget")


def switch_synthesis_model(
    spec: StrategySpec,
    *,
    alternatives: tuple[str, ...] = DEFAULT_SYNTH_ROLE_ALTERNATIVES,
) -> Optional[StrategySpec]:
    """Swap the synthesizer node's ``model_role`` to a different alternative.

    Picks the next role from ``alternatives`` after the current one
    (rotation, deterministic). Returns ``None`` when the spec has no
    ``synthesize`` node or when no alternative differs from the
    current role.
    """
    if not alternatives:
        return None
    spec_dict = _deep_dump(spec)
    located = _find_node_by_type(spec_dict, "synthesize")
    if located is None:
        return None
    _, node = located
    config = node.setdefault("config", {})
    if not isinstance(config, dict):
        config = {}
        node["config"] = config
    current_role = (
        node.get("model_role")
        or config.get("model_role")
    )
    # Build rotated candidate list starting after current_role so the
    # mutation is deterministic for any given (spec, alternatives) pair.
    options = list(alternatives)
    if current_role in options:
        idx = options.index(current_role)
        rotated = options[idx + 1 :] + options[: idx + 1]
    else:
        rotated = options
    chosen: Optional[str] = None
    for candidate in rotated:
        if candidate != current_role:
            chosen = candidate
            break
    if chosen is None:
        return None
    node["model_role"] = chosen
    config["model_role"] = chosen
    hydrated = _hydrate(spec_dict)
    if hydrated is None:
        return None
    return _validate(hydrated, "switch_synthesis_model")


def add_evidence_cards_node(
    spec: StrategySpec,
    *,
    node_id: str = "n_evidence",
    insert_before: str = "synthesize",
) -> Optional[StrategySpec]:
    """Add an ``evidence_cards`` node immediately upstream of a synthesizer.

    Returns ``None`` when an evidence-cards node already exists or when
    no ``synthesize`` node is present to anchor the insertion.
    """
    spec_dict = _deep_dump(spec)
    if _find_node_by_type(spec_dict, "evidence_cards") is not None:
        return None
    located = _find_node_by_type(spec_dict, insert_before)
    if located is None:
        return None
    _, anchor = located
    anchor_id = anchor.get("node_id")
    if not isinstance(anchor_id, str):
        return None
    new_node = {
        "node_id": node_id,
        "node_type": "evidence_cards",
        "config": {"max_cards": 20, "min_confidence": 0.5},
    }
    nodes = spec_dict.setdefault("graph", {}).setdefault("nodes", [])
    if any(n.get("node_id") == node_id for n in nodes if isinstance(n, dict)):
        return None
    nodes.append(new_node)
    _replace_inbound_edges(
        spec_dict, target_node=anchor_id, new_predecessor=node_id
    )
    hydrated = _hydrate(spec_dict)
    if hydrated is None:
        return None
    return _validate(hydrated, "add_evidence_cards_node")


def remove_evidence_cards_node(
    spec: StrategySpec,
    *,
    node_id: str = "n_evidence",
) -> Optional[StrategySpec]:
    """Remove the ``evidence_cards`` node and bridge its neighbours.

    Returns ``None`` when no node with the requested id exists or when
    the resulting graph fails validation (e.g. when the synthesizer
    relies on evidence-card output and validation flags the missing
    edge).
    """
    spec_dict = _deep_dump(spec)
    idx = _find_node_index(spec_dict, node_id)
    if idx is None:
        # Try to find any evidence_cards node, regardless of id.
        located = _find_node_by_type(spec_dict, "evidence_cards")
        if located is None:
            return None
        idx, node = located
        node_id = node.get("node_id", node_id)
    nodes = spec_dict["graph"]["nodes"]
    nodes.pop(idx)
    _retire_edges_through(spec_dict, node_id)
    hydrated = _hydrate(spec_dict)
    if hydrated is None:
        return None
    return _validate(hydrated, "remove_evidence_cards_node")


def add_reranker_node(
    spec: StrategySpec,
    *,
    node_id: str = "n_rerank",
    insert_after: str = "retrieve",
) -> Optional[StrategySpec]:
    """Splice a ``rerank`` node downstream of the retrieval step.

    The new node is inserted between the retrieve node and whatever
    consumed its output (typically the evidence-cards node).
    """
    spec_dict = _deep_dump(spec)
    if _find_node_by_type(spec_dict, "rerank") is not None:
        return None
    retrieve_node = _find_node_by_type(spec_dict, insert_after)
    if retrieve_node is None:
        return None
    _, retrieve = retrieve_node
    retrieve_id = retrieve.get("node_id")
    if not isinstance(retrieve_id, str):
        return None
    edges = spec_dict.setdefault("graph", {}).setdefault("edges", [])
    successors = [
        e for e in edges if isinstance(e, dict) and e.get("from_node") == retrieve_id
    ]
    if len(successors) != 1:
        # Ambiguous topology — bail rather than guessing.
        return None
    successor_to = successors[0]["to_node"]
    nodes = spec_dict["graph"].setdefault("nodes", [])
    if any(n.get("node_id") == node_id for n in nodes if isinstance(n, dict)):
        return None
    nodes.append(
        {
            "node_id": node_id,
            "node_type": "rerank",
            "config": {"top_k": 20},
        }
    )
    # Replace the retrieve → successor edge with retrieve → rerank → successor.
    new_edges: list[dict[str, Any]] = []
    for edge in edges:
        if (
            isinstance(edge, dict)
            and edge.get("from_node") == retrieve_id
            and edge.get("to_node") == successor_to
        ):
            new_edges.append({"from_node": retrieve_id, "to_node": node_id})
            new_edges.append({"from_node": node_id, "to_node": successor_to})
        else:
            new_edges.append(edge)
    spec_dict["graph"]["edges"] = new_edges
    hydrated = _hydrate(spec_dict)
    if hydrated is None:
        return None
    return _validate(hydrated, "add_reranker_node")


def remove_reranker_node(
    spec: StrategySpec,
    *,
    node_id: str = "n_rerank",
) -> Optional[StrategySpec]:
    """Drop the rerank node and bridge its neighbours."""
    spec_dict = _deep_dump(spec)
    idx = _find_node_index(spec_dict, node_id)
    if idx is None:
        located = _find_node_by_type(spec_dict, "rerank")
        if located is None:
            return None
        idx, node = located
        node_id = node.get("node_id", node_id)
    nodes = spec_dict["graph"]["nodes"]
    nodes.pop(idx)
    _retire_edges_through(spec_dict, node_id)
    hydrated = _hydrate(spec_dict)
    if hydrated is None:
        return None
    return _validate(hydrated, "remove_reranker_node")


def change_contract_strictness(
    spec: StrategySpec,
    *,
    levels: tuple[str, ...] = DEFAULT_CONTRACT_STRICTNESS_LEVELS,
) -> Optional[StrategySpec]:
    """Rotate the answer-contract's ``unsupported_claim_policy``.

    The policy values are mapped through
    ``lenient → allow``, ``moderate → flag``, ``strict → omit`` (per
    Blueprint 05 §6.3). Returns ``None`` when the spec has no
    :class:`AnswerContract` or when no level differs from the current
    one.
    """
    if not levels:
        return None
    spec_dict = _deep_dump(spec)
    contract = spec_dict.get("answer_contract")
    if not isinstance(contract, dict):
        return None
    current_policy = contract.get("unsupported_claim_policy", "flag")
    current_level = _POLICY_TO_STRICTNESS.get(current_policy, "moderate")
    options = list(levels)
    if current_level in options:
        idx = options.index(current_level)
        rotated = options[idx + 1 :] + options[: idx + 1]
    else:
        rotated = options
    chosen_level: Optional[str] = None
    for candidate in rotated:
        if candidate != current_level:
            chosen_level = candidate
            break
    if chosen_level is None:
        return None
    chosen_policy = _STRICTNESS_TO_POLICY.get(chosen_level)
    if chosen_policy is None:
        return None
    contract["unsupported_claim_policy"] = chosen_policy
    hydrated = _hydrate(spec_dict)
    if hydrated is None:
        return None
    return _validate(hydrated, "change_contract_strictness")


def alter_query_expansion_count(
    spec: StrategySpec,
    *,
    range: tuple[int, int] = DEFAULT_QUERY_EXPANSION_RANGE,
) -> Optional[StrategySpec]:
    """Mutate the ``query_expand`` node's ``max_variants`` config value.

    Picks the next integer in ``[low, high]`` after the current
    ``max_variants`` (deterministic rotation). Returns ``None`` when
    the spec has no ``query_expand`` node or when ``range`` produces
    no usable alternatives.
    """
    low, high = int(range[0]), int(range[1])
    if low < 1 or high < low:
        return None
    spec_dict = _deep_dump(spec)
    located = _find_node_by_type(spec_dict, "query_expand")
    if located is None:
        return None
    _, node = located
    config = node.setdefault("config", {})
    if not isinstance(config, dict):
        config = {}
        node["config"] = config
    current = int(config.get("max_variants") or 0)
    options = list(range_inclusive(low, high))
    if not options:
        return None
    if current in options:
        idx = options.index(current)
        rotated = options[idx + 1 :] + options[: idx + 1]
    else:
        rotated = options
    chosen: Optional[int] = None
    for cand in rotated:
        if cand != current:
            chosen = cand
            break
    if chosen is None:
        return None
    config["max_variants"] = chosen
    hydrated = _hydrate(spec_dict)
    if hydrated is None:
        return None
    return _validate(hydrated, "alter_query_expansion_count")


def add_validation_node(
    spec: StrategySpec,
    *,
    node_id: str = "n_validate",
) -> Optional[StrategySpec]:
    """Append a ``validate_contract`` node to the terminal path."""
    spec_dict = _deep_dump(spec)
    nodes = spec_dict.setdefault("graph", {}).setdefault("nodes", [])
    if any(
        n.get("node_id") == node_id for n in nodes if isinstance(n, dict)
    ):
        return None
    nodes.append(
        {
            "node_id": node_id,
            "node_type": "validate_contract",
            "config": {},
        }
    )
    _append_after_terminal(spec_dict, new_node_id=node_id)
    hydrated = _hydrate(spec_dict)
    if hydrated is None:
        return None
    return _validate(hydrated, "add_validation_node")


def add_refinement_node(
    spec: StrategySpec,
    *,
    node_id: str = "n_refine_evo",
) -> Optional[StrategySpec]:
    """Append a ``refine`` node to the terminal path.

    Uses ``n_refine_evo`` by default to avoid colliding with the
    ``n_refine`` id already present in
    :file:`deep_orchestrated_v1.yaml`.
    """
    spec_dict = _deep_dump(spec)
    nodes = spec_dict.setdefault("graph", {}).setdefault("nodes", [])
    if any(
        n.get("node_id") == node_id for n in nodes if isinstance(n, dict)
    ):
        return None
    nodes.append(
        {
            "node_id": node_id,
            "node_type": "refine",
            "config": {"max_output_tokens": 2000},
        }
    )
    _append_after_terminal(spec_dict, new_node_id=node_id)
    hydrated = _hydrate(spec_dict)
    if hydrated is None:
        return None
    return _validate(hydrated, "add_refinement_node")


def range_inclusive(low: int, high: int) -> list[int]:
    """Return ``[low, low+1, ..., high]`` (inclusive)."""
    if high < low:
        return []
    return list(range(int(low), int(high) + 1))


# ─────────────────────────────────────────────────────────────────────────────
# Default operator registry
# ─────────────────────────────────────────────────────────────────────────────


#: Tuple of default operators used by :class:`EvolutionaryMutator`. Each
#: entry is ``(name, callable)`` so the mutator can record which operator
#: was applied as a lineage tag (``mutation:<name>``) and emit the same
#: name in telemetry.
DEFAULT_MUTATION_OPERATORS: tuple[tuple[str, MutationOperator], ...] = (
    ("reduce_context_budget", reduce_context_budget),
    ("switch_synthesis_model", switch_synthesis_model),
    ("add_evidence_cards_node", add_evidence_cards_node),
    ("remove_evidence_cards_node", remove_evidence_cards_node),
    ("add_reranker_node", add_reranker_node),
    ("remove_reranker_node", remove_reranker_node),
    ("change_contract_strictness", change_contract_strictness),
    ("alter_query_expansion_count", alter_query_expansion_count),
    ("add_validation_node", add_validation_node),
    ("add_refinement_node", add_refinement_node),
)


# ─────────────────────────────────────────────────────────────────────────────
# EvolutionaryMutator
# ─────────────────────────────────────────────────────────────────────────────


class EvolutionaryMutator:
    """Combine mutation operators into a population-evolution step.

    The mutator picks ``mutations_per_parent`` operators per parent
    (with replacement, sampled by a seeded :class:`random.Random`) and
    applies each one. Successful children are tagged with lineage,
    re-identified, and accumulated until the per-generation cap fires.

    Args:
        operators: Tuple of ``(name, callable)`` pairs. Defaults to
            :data:`DEFAULT_MUTATION_OPERATORS`. The order matters only
            for deterministic naming; selection itself is RNG-driven.
        mutations_per_parent: How many operator picks the mutator
            attempts per parent. The actual surviving child count may
            be lower because operators can return ``None``.
        max_per_generation: Hard cap on the number of children
            returned by a single :meth:`mutate` call. Truncation is
            stable: children are kept in the order they were
            successfully produced.
        seed: Optional integer fed to :class:`random.Random`. Pinning
            the seed makes :meth:`mutate` deterministic for unit tests
            and reproducible experiments.
    """

    def __init__(
        self,
        *,
        operators: Optional[tuple[tuple[str, MutationOperator], ...]] = None,
        mutations_per_parent: int = 2,
        max_per_generation: int = 8,
        seed: Optional[int] = None,
    ) -> None:
        if mutations_per_parent <= 0:
            raise ValueError("mutations_per_parent must be positive")
        if max_per_generation <= 0:
            raise ValueError("max_per_generation must be positive")
        self._operators: tuple[tuple[str, MutationOperator], ...] = (
            tuple(operators) if operators is not None else DEFAULT_MUTATION_OPERATORS
        )
        if not self._operators:
            raise ValueError("EvolutionaryMutator requires at least one operator")
        self.mutations_per_parent = int(mutations_per_parent)
        self.max_per_generation = int(max_per_generation)
        self._seed = seed
        self._rng = random.Random(seed)

        # Telemetry — populated by :meth:`mutate`.
        self._operator_attempts: dict[str, int] = {}
        self._operator_successes: dict[str, int] = {}

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def operators(self) -> tuple[tuple[str, MutationOperator], ...]:
        """Return the configured ``(name, callable)`` tuple."""
        return self._operators

    @property
    def seed(self) -> Optional[int]:
        """Return the RNG seed (``None`` when unseeded)."""
        return self._seed

    # ── Public API ──────────────────────────────────────────────────────

    def mutate(
        self,
        *,
        parents: list[StrategySpec],
        generation: int,
    ) -> list[StrategySpec]:
        """Produce a child population from ``parents``.

        Args:
            parents: Non-empty list of :class:`StrategySpec` instances
                seeding this generation. Empty input returns an empty
                list.
            generation: Generation index (``>= 0``). Embedded into the
                child ``strategy_id``, ``version`` and lineage tags.

        Returns:
            A list of validated :class:`StrategySpec` children, capped
            at :attr:`max_per_generation`.
        """
        if generation < 0:
            raise ValueError("generation must be non-negative")
        if not parents:
            return []
        # Reset telemetry — :meth:`mutate` is idempotent in spirit.
        self._operator_attempts = {}
        self._operator_successes = {}

        children: list[StrategySpec] = []
        op_names = [name for name, _ in self._operators]
        op_by_name = dict(self._operators)
        idx = 0
        for parent in parents:
            if len(children) >= self.max_per_generation:
                break
            # Deterministic per-parent operator pick — sample from the
            # seeded RNG so results are stable across runs.
            picks = [self._rng.choice(op_names) for _ in range(self.mutations_per_parent)]
            for op_name in picks:
                if len(children) >= self.max_per_generation:
                    break
                self._operator_attempts[op_name] = (
                    self._operator_attempts.get(op_name, 0) + 1
                )
                operator = op_by_name[op_name]
                try:
                    mutated = operator(parent)
                except Exception as exc:  # noqa: BLE001 - swallow per contract
                    logger.warning(
                        "Operator %r raised on parent=%s: %s",
                        op_name,
                        parent.strategy_id,
                        exc,
                    )
                    mutated = None
                if mutated is None:
                    continue
                tagged = self._apply_lineage(
                    parent=parent,
                    child=mutated,
                    generation=generation,
                    operator_name=op_name,
                    sequence=idx,
                )
                if tagged is None:
                    continue
                children.append(tagged)
                self._operator_successes[op_name] = (
                    self._operator_successes.get(op_name, 0) + 1
                )
                idx += 1
        return children

    def describe(self) -> dict[str, Any]:
        """Return per-operator attempt / success counts from the latest run."""
        return {
            "attempts": dict(self._operator_attempts),
            "successes": dict(self._operator_successes),
            "total_attempts": sum(self._operator_attempts.values()),
            "total_successes": sum(self._operator_successes.values()),
        }

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _apply_lineage(
        *,
        parent: StrategySpec,
        child: StrategySpec,
        generation: int,
        operator_name: str,
        sequence: int,
    ) -> Optional[StrategySpec]:
        """Stamp identity + lineage onto a successfully mutated child.

        Re-validates the spec after the rewrite — the only realistic
        failure path is a Pydantic regression, but the contract still
        says we never raise.
        """
        spec_dict = json.loads(
            json.dumps(child.model_dump(mode="json"), default=str)
        )
        hash_input = json.dumps(
            {
                "parent": parent.strategy_id,
                "parent_version": parent.version,
                "operator": operator_name,
                "generation": generation,
                "sequence": sequence,
            },
            sort_keys=True,
            default=str,
        )
        hash8 = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:8]
        new_id = f"{parent.strategy_id}__evo_{generation:02d}_{hash8}"
        spec_dict["strategy_id"] = new_id
        spec_dict["version"] = f"{parent.version}+evo.{generation}.{sequence}"
        spec_dict["status"] = "draft"
        spec_dict["spec_hash"] = None
        display_name = parent.display_name or parent.strategy_id
        spec_dict["display_name"] = (
            f"{display_name} [evo gen {generation:02d} {operator_name}]"
        )
        # Lineage tags — strip stale parent tags first so successive
        # generations don't accumulate breadcrumbs from the original
        # ancestor.
        existing_tags = list(spec_dict.get("tags") or [])
        cleaned = [
            t
            for t in existing_tags
            if not (
                isinstance(t, str)
                and (
                    t.startswith("parent:")
                    or t.startswith("parent_version:")
                    or t.startswith("generation:")
                    or t.startswith("mutation:")
                )
            )
        ]
        cleaned.append(f"parent:{parent.strategy_id}")
        cleaned.append(f"parent_version:{parent.version}")
        cleaned.append(f"generation:{generation}")
        cleaned.append(f"mutation:{operator_name}")
        spec_dict["tags"] = cleaned

        try:
            tagged = StrategySpec.model_validate(spec_dict)
        except Exception as exc:  # noqa: BLE001 - never raise
            logger.warning(
                "Failed to apply lineage on operator=%r child of %s: %s",
                operator_name,
                parent.strategy_id,
                exc,
            )
            return None
        errors = validate_spec(tagged)
        if errors:
            logger.warning(
                "Lineage-stamped child failed validate_spec: %s",
                "; ".join(errors),
            )
            return None
        return tagged


def _clone_spec(spec: StrategySpec) -> StrategySpec:
    """Return a deep copy of ``spec`` (intentionally exported for tests)."""
    return copy.deepcopy(spec)
