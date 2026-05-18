"""Strategy spec YAML loader and validator.

Loads StrategySpec definitions from YAML files (single file or directory)
and validates them against the node registry, graph compiler, and budget rules.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import yaml

from backend.agent.strategy.models import StrategySpec

logger = logging.getLogger(__name__)

__all__ = [
    "SpecLoaderError",
    "load_spec_from_yaml",
    "load_all_specs",
    "validate_spec",
]


class SpecLoaderError(Exception):
    """Raised when a strategy spec cannot be parsed or validated."""

    pass


def load_spec_from_yaml(path: str) -> StrategySpec:
    """Load and parse a single YAML spec file into a validated StrategySpec.

    Args:
        path: Filesystem path to the YAML spec file.

    Returns:
        Parsed StrategySpec instance.

    Raises:
        SpecLoaderError: If the file is missing, unreadable, or fails Pydantic
            validation.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise SpecLoaderError(f"Spec file not found: {path}") from e
    except yaml.YAMLError as e:
        raise SpecLoaderError(f"Failed to parse YAML in {path}: {e}") from e

    if not isinstance(data, dict):
        raise SpecLoaderError(
            f"Spec file {path} must contain a YAML mapping at the top level"
        )

    try:
        spec = StrategySpec(**data)
    except Exception as e:
        raise SpecLoaderError(f"Failed to parse spec from {path}: {e}") from e

    return spec


def load_all_specs(directory: str) -> list[StrategySpec]:
    """Load all .yaml/.yml spec files from a directory.

    Files that fail to load are logged and skipped; this function does not
    raise on individual file errors so that one bad spec does not block the
    rest.

    Args:
        directory: Directory to scan (non-recursive).

    Returns:
        List of successfully loaded StrategySpec instances, sorted by filename.
    """
    specs: list[StrategySpec] = []
    if not os.path.isdir(directory):
        logger.warning("Spec directory does not exist: %s", directory)
        return specs

    for filename in sorted(os.listdir(directory)):
        if not filename.endswith((".yaml", ".yml")):
            continue
        filepath = os.path.join(directory, filename)
        try:
            spec = load_spec_from_yaml(filepath)
            specs.append(spec)
            logger.info("Loaded spec: %s", spec.strategy_id)
        except SpecLoaderError as e:
            logger.error("Failed to load %s: %s", filename, e)

    return specs


def validate_spec(spec: StrategySpec) -> list[str]:
    """Validate a StrategySpec against runtime invariants.

    Checks:
    - All node types are registered in NODE_TYPE_REGISTRY.
    - Graph compiles successfully (no cycles, entry/terminal nodes valid,
      every edge references a known node, entry can reach a terminal).
    - Budgets are sensible (latency_target_ms <= latency_hard_limit_ms).

    Args:
        spec: The StrategySpec to validate.

    Returns:
        List of error messages. Empty list means the spec is valid.
    """
    errors: list[str] = []

    # 1. Node type registry check (lazy import to avoid circular deps)
    try:
        from backend.agent.strategy.nodes import NODE_TYPE_REGISTRY

        registered_types = set(NODE_TYPE_REGISTRY.keys())
        for node in spec.graph.nodes:
            if node.node_type not in registered_types:
                errors.append(
                    f"Unknown node type: '{node.node_type}' "
                    f"(node_id={node.node_id})"
                )
    except ImportError as e:
        logger.warning("NODE_TYPE_REGISTRY unavailable, skipping check: %s", e)

    # 2. Graph compilation check
    try:
        from backend.agent.strategy.graph_compiler import compile_graph

        compile_graph(spec.graph)
    except Exception as e:
        errors.append(f"Graph validation failed: {e}")

    # 3. Budget sanity check
    budgets = spec.budgets
    if budgets is not None:
        target: Optional[int] = budgets.latency_target_ms
        hard: Optional[int] = budgets.latency_hard_limit_ms
        if target is not None and hard is not None and target > hard:
            errors.append(
                "latency_target_ms must be <= latency_hard_limit_ms "
                f"(target={target}, hard={hard})"
            )

    return errors
