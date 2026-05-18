"""Strategy graph compilation and topological ordering."""
from __future__ import annotations

import logging
from collections import deque
from typing import Optional

from pydantic import BaseModel, Field

from backend.agent.strategy.models import StrategyEdge, StrategyGraph, StrategyNode

logger = logging.getLogger(__name__)

__all__ = [
    "CompiledGraph",
    "GraphCompilationError",
    "compile_graph",
    "topological_levels",
]


# ═══════════════════════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════════════════════


class CompiledGraph(BaseModel):
    """Pre-processed graph ready for execution."""

    adjacency_out: dict[str, list[str]] = Field(default_factory=dict)
    """node_id -> list of successor node_ids"""

    adjacency_in: dict[str, list[str]] = Field(default_factory=dict)
    """node_id -> list of predecessor node_ids"""

    conditional_edges: dict[str, str] = Field(default_factory=dict)
    """Serialized key 'from_node::to_node' -> condition expression string"""

    levels: list[list[str]] = Field(default_factory=list)
    """Topological levels — nodes in the same level can execute in parallel."""

    entry_node: str = ""
    """ID of the entry node."""

    terminal_nodes: list[str] = Field(default_factory=list)
    """IDs of all terminal/sink nodes."""

    node_map: dict[str, StrategyNode] = Field(default_factory=dict)
    """node_id -> StrategyNode for O(1) lookup."""

    def get_condition(self, from_node: str, to_node: str) -> Optional[str]:
        """Return the condition expression for an edge, or None."""
        return self.conditional_edges.get(f"{from_node}::{to_node}")

    def successors(self, node_id: str) -> list[str]:
        """Return successor node IDs."""
        return self.adjacency_out.get(node_id, [])

    def predecessors(self, node_id: str) -> list[str]:
        """Return predecessor node IDs."""
        return self.adjacency_in.get(node_id, [])


# ═══════════════════════════════════════════════════════════════════════════════
# Exceptions
# ═══════════════════════════════════════════════════════════════════════════════


class GraphCompilationError(Exception):
    """Raised when graph fails validation."""

    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════


def compile_graph(graph: StrategyGraph) -> CompiledGraph:
    """Validate and compile a StrategyGraph into execution-ready form.

    Validates:
    - No duplicate node IDs
    - Entry node exists in graph
    - All terminal nodes exist in graph
    - All edges reference existing nodes
    - No cycles (DAG validation)
    - Entry node has a path to at least one terminal node

    Returns:
        CompiledGraph with adjacency lists, topological levels, and node map.

    Raises:
        GraphCompilationError: On any validation failure.
    """
    # 1. Collect all node IDs and check for duplicates
    node_ids: set[str] = set()
    node_map: dict[str, StrategyNode] = {}
    for node in graph.nodes:
        if node.node_id in node_ids:
            raise GraphCompilationError(
                f"Duplicate node ID: '{node.node_id}'"
            )
        node_ids.add(node.node_id)
        node_map[node.node_id] = node

    # 2. Validate entry node exists
    if graph.entry_node not in node_ids:
        raise GraphCompilationError(
            f"Entry node '{graph.entry_node}' not found in graph nodes"
        )

    # 3. Validate terminal nodes exist
    for t_node in graph.terminal_nodes:
        if t_node not in node_ids:
            raise GraphCompilationError(
                f"Terminal node '{t_node}' not found in graph nodes"
            )

    # 4. Build adjacency lists
    adjacency_out: dict[str, list[str]] = {nid: [] for nid in node_ids}
    adjacency_in: dict[str, list[str]] = {nid: [] for nid in node_ids}
    conditional_edges: dict[str, str] = {}

    for edge in graph.edges:
        if edge.from_node not in node_ids:
            raise GraphCompilationError(
                f"Edge references unknown source node: '{edge.from_node}'"
            )
        if edge.to_node not in node_ids:
            raise GraphCompilationError(
                f"Edge references unknown target node: '{edge.to_node}'"
            )
        adjacency_out[edge.from_node].append(edge.to_node)
        adjacency_in[edge.to_node].append(edge.from_node)

        if edge.condition is not None:
            conditional_edges[f"{edge.from_node}::{edge.to_node}"] = edge.condition

    # 5. Cycle detection
    cycle = _detect_cycle(adjacency_out, node_ids)
    if cycle is not None:
        cycle_repr = " -> ".join(cycle)
        raise GraphCompilationError(f"Cycle detected in graph: {cycle_repr}")

    # 6. Compute topological levels
    levels = topological_levels(graph)

    # 7. Validate entry node can reach at least one terminal
    reachable = _reachable_from(graph.entry_node, adjacency_out)
    reachable_terminals = [t for t in graph.terminal_nodes if t in reachable]
    if not reachable_terminals:
        raise GraphCompilationError(
            f"Entry node '{graph.entry_node}' cannot reach any terminal node. "
            f"Terminal nodes: {graph.terminal_nodes}"
        )

    compiled = CompiledGraph(
        adjacency_out=adjacency_out,
        adjacency_in=adjacency_in,
        conditional_edges=conditional_edges,
        levels=levels,
        entry_node=graph.entry_node,
        terminal_nodes=graph.terminal_nodes,
        node_map=node_map,
    )

    logger.info(
        "Graph compiled: %d nodes, %d edges, %d levels",
        len(node_ids),
        len(graph.edges),
        len(levels),
    )
    return compiled


def topological_levels(graph: StrategyGraph) -> list[list[str]]:
    """Compute topological levels using Kahn's algorithm.

    Returns nodes grouped by execution level:
    - Level 0: nodes with no predecessors (typically the entry node)
    - Level N: nodes whose ALL predecessors are in levels 0..N-1

    Nodes at the same level can execute in parallel.

    Raises:
        GraphCompilationError: If graph contains a cycle (detected via
            remaining in-degree).
    """
    # Build in-degree map
    node_ids = {node.node_id for node in graph.nodes}
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    adj_out: dict[str, list[str]] = {nid: [] for nid in node_ids}

    for edge in graph.edges:
        in_degree[edge.to_node] += 1
        adj_out[edge.from_node].append(edge.to_node)

    # Kahn's algorithm — level by level
    levels: list[list[str]] = []
    queue: deque[str] = deque()

    # Seed with zero in-degree nodes
    for nid in node_ids:
        if in_degree[nid] == 0:
            queue.append(nid)

    processed = 0
    while queue:
        level: list[str] = []
        next_queue: deque[str] = deque()
        while queue:
            node_id = queue.popleft()
            level.append(node_id)
            processed += 1
            for successor in adj_out[node_id]:
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    next_queue.append(successor)
        levels.append(sorted(level))  # Sort for determinism
        queue = next_queue

    if processed != len(node_ids):
        raise GraphCompilationError(
            "Graph contains a cycle — topological sort could not complete. "
            f"Processed {processed}/{len(node_ids)} nodes."
        )

    return levels


# ═══════════════════════════════════════════════════════════════════════════════
# Internal Helpers
# ═══════════════════════════════════════════════════════════════════════════════


# DFS coloring constants
_WHITE = 0  # Not visited
_GRAY = 1  # In current DFS path (on stack)
_BLACK = 2  # Fully processed


def _detect_cycle(
    adjacency_out: dict[str, list[str]], all_nodes: set[str]
) -> Optional[list[str]]:
    """Detect cycles using DFS coloring.

    Returns the cycle path if found, None if the graph is a valid DAG.
    """
    color: dict[str, int] = {nid: _WHITE for nid in all_nodes}
    parent: dict[str, Optional[str]] = {nid: None for nid in all_nodes}

    def dfs(node: str) -> Optional[list[str]]:
        color[node] = _GRAY
        for neighbor in adjacency_out.get(node, []):
            if color[neighbor] == _GRAY:
                # Found a back edge — reconstruct cycle
                cycle = [neighbor, node]
                current = node
                while current != neighbor:
                    current = parent[current]  # type: ignore[assignment]
                    if current is None:
                        break
                    cycle.append(current)
                cycle.reverse()
                return cycle
            if color[neighbor] == _WHITE:
                parent[neighbor] = node
                result = dfs(neighbor)
                if result is not None:
                    return result
        color[node] = _BLACK
        return None

    for node in sorted(all_nodes):  # Sort for deterministic cycle reporting
        if color[node] == _WHITE:
            result = dfs(node)
            if result is not None:
                return result
    return None


def _reachable_from(start: str, adjacency_out: dict[str, list[str]]) -> set[str]:
    """BFS to find all nodes reachable from start."""
    visited: set[str] = set()
    queue: deque[str] = deque([start])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for neighbor in adjacency_out.get(node, []):
            if neighbor not in visited:
                queue.append(neighbor)
    return visited
