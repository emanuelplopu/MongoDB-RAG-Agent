"""Grid-based candidate strategy spec generator.

Produces draft ``StrategySpec`` variants from a base spec by Cartesian-producting
a set of mutation axes. Each candidate is validated via
``spec_loader.validate_spec`` and invalid candidates are dropped with a logged
warning.

This module is intentionally side-effect free: it does not persist anything to
MongoDB or call any external services. It is consumed by the overnight
exploration loop in later phases.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
from typing import Any, Optional

from backend.agent.strategy.models import StrategySpec
from backend.agent.strategy.spec_loader import validate_spec

logger = logging.getLogger(__name__)

__all__ = ["CandidateGenerator", "DEFAULT_AXES"]


# Default grid axes. Chosen so the Cartesian product stays within the default
# ``max_candidates`` cap (12). Targets node IDs that exist in both shipped
# sample specs (``fast_evidence_v1`` and ``deep_orchestrated_v1``).
DEFAULT_AXES: dict[str, list[Any]] = {
    "nodes.n_retrieve.config.top_k": [10, 20, 30],
    "nodes.n_evidence.config.max_cards": [10, 20],
}


class CandidateGenerator:
    """Generate draft strategy spec candidates by gridding mutation axes.

    The generator takes a base ``StrategySpec`` and a dict of mutation axes,
    where each key is a dotted path identifying a field to mutate and each
    value is the list of values to try along that axis. It produces the full
    Cartesian product (capped by ``max_candidates``), deep-copies the base for
    each combination, applies the mutations, and validates the result.

    Candidates that fail to apply (e.g. unknown node id) or fail
    ``validate_spec`` are dropped with a logged warning. The order of returned
    candidates is deterministic: it follows the sorted order of mutation
    tuples.

    Supported dotted-path forms:

    - ``budgets.<field>`` — mutate a field on the budgets block.
    - ``model_roles.<role>`` — mutate a model role mapping.
    - ``nodes.<node_id>.config.<key>`` — mutate a key in a node's ``config``.
    - ``nodes.<node_id>.<attr>`` — mutate a top-level node attribute (e.g.
      ``node_type``, ``timeout_ms``).
    - Any other dotted path is traversed as nested dicts on the spec dump.

    Parent-spec tracking is recorded via tags of the form
    ``parent:<strategy_id>`` and ``parent_version:<version>`` because the
    current :class:`StrategySpec` model has no dedicated ``parent_spec_id``
    field.
    """

    def __init__(
        self,
        base_spec: StrategySpec,
        axes: Optional[dict[str, list[Any]]] = None,
        max_candidates: int = 12,
    ) -> None:
        """Initialize the generator.

        Args:
            base_spec: The base ``StrategySpec`` from which candidates are
                derived.
            axes: Mapping of dotted-path mutation targets to lists of values.
                If ``None`` (default), :data:`DEFAULT_AXES` is used.
            max_candidates: Upper bound on the number of returned candidates.
                If the Cartesian product exceeds this, results are
                deterministically truncated by sorted mutation tuple.
        """
        if max_candidates <= 0:
            raise ValueError("max_candidates must be positive")
        self.base_spec = base_spec
        self.axes: dict[str, list[Any]] = (
            dict(DEFAULT_AXES) if axes is None else dict(axes)
        )
        self.max_candidates = max_candidates

        # Telemetry — populated by ``generate``.
        self._total_combinations: int = 0
        self._kept: int = 0
        self._dropped: int = 0
        self._dropped_reasons: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ public

    def generate(self) -> list[StrategySpec]:
        """Produce the list of validated candidate specs.

        Returns:
            A list of validated ``StrategySpec`` candidates in deterministic
            order. May be shorter than the Cartesian product if validation
            drops candidates or if ``max_candidates`` truncates the grid.
        """
        # Reset telemetry — ``generate`` is idempotent.
        self._dropped_reasons = []
        self._dropped = 0
        self._kept = 0

        combinations = self._enumerate_combinations()
        self._total_combinations = len(combinations)

        truncated = combinations[: self.max_candidates]

        base_dump = self.base_spec.model_dump(mode="json")
        base_strategy_id = self.base_spec.strategy_id
        base_version = self.base_spec.version
        base_display_name = self.base_spec.display_name or base_strategy_id

        kept: list[StrategySpec] = []
        for idx, mutation in enumerate(truncated):
            mutation_dict = dict(mutation)
            hash_short = _short_hash(mutation_dict)
            candidate_id = f"{base_strategy_id}__grid_{hash_short}"

            try:
                spec_dict = json.loads(json.dumps(base_dump))  # deep copy
                for path, value in mutation_dict.items():
                    _set_by_dotted_path(spec_dict, path, value)

                # Identity / lineage fields.
                spec_dict["strategy_id"] = candidate_id
                spec_dict["version"] = f"{base_version}+grid.{idx:02d}"
                spec_dict["display_name"] = (
                    f"{base_display_name} [grid {idx:02d}]"
                )
                spec_dict["status"] = "draft"
                spec_dict["spec_hash"] = None

                # Parent lineage stored as tags (no dedicated model field).
                tags = list(spec_dict.get("tags") or [])
                tags = [
                    t
                    for t in tags
                    if not (
                        isinstance(t, str)
                        and (t.startswith("parent:") or t.startswith("parent_version:"))
                    )
                ]
                tags.append(f"parent:{base_strategy_id}")
                tags.append(f"parent_version:{base_version}")
                spec_dict["tags"] = tags

                candidate = StrategySpec.model_validate(spec_dict)
            except Exception as exc:  # noqa: BLE001 - intentional broad catch
                self._record_drop(candidate_id, mutation_dict, f"apply_error: {exc}")
                continue

            errors = validate_spec(candidate)
            if errors:
                reason = "validate_spec: " + "; ".join(errors)
                self._record_drop(candidate.strategy_id, mutation_dict, reason)
                continue

            kept.append(candidate)

        self._kept = len(kept)
        return kept

    def describe(self) -> dict[str, Any]:
        """Return a telemetry-style summary of the most recent ``generate``.

        Returns:
            Dict with ``axes``, ``total_combinations``, ``cap``, ``kept``,
            ``dropped``, and ``dropped_reasons``. ``dropped_reasons`` is a
            list of ``{candidate_id, mutation, reason}`` records.
        """
        return {
            "axes": {k: list(v) for k, v in self.axes.items()},
            "total_combinations": self._total_combinations,
            "cap": self.max_candidates,
            "kept": self._kept,
            "dropped": self._dropped,
            "dropped_reasons": list(self._dropped_reasons),
        }

    # ----------------------------------------------------------------- helpers

    def _enumerate_combinations(self) -> list[tuple[tuple[str, Any], ...]]:
        """Enumerate all mutation tuples, sorted deterministically.

        Each combination is a tuple of ``(path, value)`` pairs ordered by
        ``path``. The list itself is sorted by JSON-serialized form so the
        truncation behavior is stable across runs and Python versions.
        """
        if not self.axes:
            return [tuple()]

        sorted_axes = sorted(self.axes.items(), key=lambda kv: kv[0])
        paths = [path for path, _ in sorted_axes]
        value_lists = [list(values) for _, values in sorted_axes]

        combinations: list[tuple[tuple[str, Any], ...]] = []
        for combo in itertools.product(*value_lists):
            combinations.append(tuple(zip(paths, combo)))

        combinations.sort(key=lambda c: json.dumps(c, sort_keys=True, default=str))
        return combinations

    def _record_drop(
        self,
        candidate_id: str,
        mutation: dict[str, Any],
        reason: str,
    ) -> None:
        """Log and accumulate a dropped-candidate record."""
        self._dropped += 1
        self._dropped_reasons.append(
            {
                "candidate_id": candidate_id,
                "mutation": dict(mutation),
                "reason": reason,
            }
        )
        logger.warning(
            "CandidateGenerator dropped candidate %s: %s", candidate_id, reason
        )


# ─────────────────────────────────────────────────────────────────── utilities


def _short_hash(mutation: dict[str, Any]) -> str:
    """Return a stable 8-char hex hash of a mutation mapping.

    The mapping is serialized with sorted keys so the hash is independent of
    Python dict insertion order.
    """
    payload = json.dumps(mutation, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _set_by_dotted_path(
    spec_dict: dict[str, Any],
    path: str,
    value: Any,
) -> None:
    """Apply ``value`` at ``path`` inside ``spec_dict`` in place.

    Supports the special ``nodes.<node_id>.<…>`` prefix which dispatches into
    ``graph.nodes`` (a list of node dicts) by matching ``node_id``.

    Args:
        spec_dict: The ``StrategySpec.model_dump(mode="json")`` dict.
        path: Dotted path describing the mutation target.
        value: New value to write at the path.

    Raises:
        KeyError: If a path component does not resolve.
        ValueError: If the path is empty or malformed.
    """
    if not path:
        raise ValueError("path must be a non-empty dotted string")

    parts = path.split(".")

    # Special handling: ``nodes.<node_id>.<rest...>`` → graph.nodes list lookup.
    if parts[0] == "nodes":
        if len(parts) < 3:
            raise ValueError(
                f"node mutation path '{path}' must be of the form "
                "'nodes.<node_id>.<attr>[.<sub>...]'"
            )
        node_id = parts[1]
        graph = spec_dict.get("graph")
        if not isinstance(graph, dict):
            raise KeyError("spec has no 'graph' block")
        nodes = graph.get("nodes")
        if not isinstance(nodes, list):
            raise KeyError("graph has no 'nodes' list")
        target_node: Optional[dict[str, Any]] = None
        for node in nodes:
            if isinstance(node, dict) and node.get("node_id") == node_id:
                target_node = node
                break
        if target_node is None:
            raise KeyError(f"no node with node_id='{node_id}'")
        _set_nested(target_node, parts[2:], value)
        return

    _set_nested(spec_dict, parts, value)


def _set_nested(container: dict[str, Any], parts: list[str], value: Any) -> None:
    """Walk ``container`` along ``parts`` and assign ``value`` at the leaf.

    Intermediate dicts must already exist; this helper does not auto-create
    missing branches because doing so would mask typos in mutation paths.
    """
    if not parts:
        raise ValueError("parts must be non-empty")
    cursor: Any = container
    for key in parts[:-1]:
        if not isinstance(cursor, dict):
            raise KeyError(f"cannot descend into non-dict at '{key}'")
        if key not in cursor:
            raise KeyError(f"missing key '{key}' while traversing path")
        cursor = cursor[key]
    leaf = parts[-1]
    if not isinstance(cursor, dict):
        raise KeyError(f"cannot assign '{leaf}' on non-dict container")
    cursor[leaf] = value
