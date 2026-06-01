"""Tests for the Strategy OS SSE per-node progress callback (Task 92 / T4).

Verifies that ``StrategyRunner.run`` correctly invokes the
``on_node_complete`` callback for every executed node and that
callback errors do not crash the run.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.agent.strategy.models import (
    BusinessContext,
    NodeOutput,
    StrategyBudgets,
    StrategyEdge,
    StrategyGraph,
    StrategyNode,
    StrategyRunState,
    StrategySpec,
)
from backend.agent.strategy.nodes.base import NodeExecutor
from backend.agent.strategy.nodes.registry import NodeRegistry
from backend.agent.strategy.strategy_runner import StrategyRunner


# ─── Helpers ────────────────────────────────────────────────────────────────


class _MockExecutor(NodeExecutor):
    """Executor that returns a configurable NodeOutput."""

    def __init__(
        self,
        output_data: Any = None,
        status: str = "success",
        duration_ms: float = 12.5,
    ) -> None:
        self.output_data = output_data
        self.status = status
        self.duration_ms = duration_ms

    async def execute(
        self, node: StrategyNode, state: StrategyRunState
    ) -> NodeOutput:
        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status=self.status,
            output_data=self.output_data,
            tokens_used=0,
            duration_ms=self.duration_ms,
        )


def _registry_with(executors: dict[str, _MockExecutor]) -> NodeRegistry:
    registry = NodeRegistry()
    for ntype, executor in executors.items():
        registry._executors[ntype] = type(executor)
        registry._instances[ntype] = executor
    return registry


def _linear_three_node_spec() -> StrategySpec:
    graph = StrategyGraph(
        nodes=[
            StrategyNode(node_id="n1", node_type="normalize_query", config={}),
            StrategyNode(node_id="n2", node_type="retrieve", config={}),
            StrategyNode(node_id="n3", node_type="synthesize", config={}),
        ],
        edges=[
            StrategyEdge(from_node="n1", to_node="n2"),
            StrategyEdge(from_node="n2", to_node="n3"),
        ],
        entry_node="n1",
        terminal_nodes=["n3"],
    )
    return StrategySpec(
        strategy_id="t4-test-strategy",
        graph=graph,
        budgets=StrategyBudgets(),
    )


def _business_context() -> BusinessContext:
    return BusinessContext(
        capability_id="general_qa",
        tenant_id="test",
        profile_key="default",
    )


# ─── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_node_complete_called_for_each_executed_node() -> None:
    """The callback must fire once per executed node, in order, with the
    correct (node_id, node_type, status, duration_ms) signature."""
    spec = _linear_three_node_spec()
    registry = _registry_with(
        {
            "normalize_query": _MockExecutor(output_data="cleaned"),
            "retrieve": _MockExecutor(output_data=[]),
            "synthesize": _MockExecutor(
                output_data={"text": "ok", "citations": []}
            ),
        }
    )
    runner = StrategyRunner(registry=registry)

    received: list[tuple[str, str, str, float]] = []

    async def _cb(node_id: str, node_type: str, status: str, duration_ms: float) -> None:
        received.append((node_id, node_type, status, duration_ms))

    result = await runner.run(
        spec=spec,
        context=_business_context(),
        query="hello",
        on_node_complete=_cb,
    )

    assert result.state.node_outputs["n1"].status == "success"
    assert [r[0] for r in received] == ["n1", "n2", "n3"]
    assert [r[1] for r in received] == [
        "normalize_query",
        "retrieve",
        "synthesize",
    ]
    assert all(r[2] == "success" for r in received)
    # duration_ms is forwarded as a float (from _MockExecutor)
    assert all(isinstance(r[3], float) for r in received)


@pytest.mark.asyncio
async def test_on_node_complete_errors_do_not_crash_run() -> None:
    """Best-effort contract: callback exceptions must be swallowed."""
    spec = _linear_three_node_spec()
    registry = _registry_with(
        {
            "normalize_query": _MockExecutor(output_data="cleaned"),
            "retrieve": _MockExecutor(output_data=[]),
            "synthesize": _MockExecutor(
                output_data={"text": "ok", "citations": []}
            ),
        }
    )
    runner = StrategyRunner(registry=registry)

    call_count = 0

    async def _bad_cb(node_id: str, node_type: str, status: str, duration_ms: float) -> None:
        nonlocal call_count
        call_count += 1
        raise RuntimeError(f"boom at {node_id}")

    # Should NOT raise.
    result = await runner.run(
        spec=spec,
        context=_business_context(),
        query="hello",
        on_node_complete=_bad_cb,
    )

    assert call_count == 3
    # Even though every callback raised, the run still completed all
    # nodes and produced synthesis output.
    assert result.state.synthesis_result is not None
    assert all(
        out.status == "success" for out in result.state.node_outputs.values()
    )


@pytest.mark.asyncio
async def test_on_node_complete_optional_backward_compatible() -> None:
    """Omitting the callback must not change runner behaviour."""
    spec = _linear_three_node_spec()
    registry = _registry_with(
        {
            "normalize_query": _MockExecutor(output_data="cleaned"),
            "retrieve": _MockExecutor(output_data=[]),
            "synthesize": _MockExecutor(
                output_data={"text": "ok", "citations": []}
            ),
        }
    )
    runner = StrategyRunner(registry=registry)

    result = await runner.run(spec=spec, context=_business_context(), query="hi")
    assert result.state.synthesis_result is not None
    assert len(result.state.node_outputs) == 3


@pytest.mark.asyncio
async def test_on_node_complete_fires_for_skipped_status() -> None:
    """Skipped (non-pending) outputs should still emit the callback so
    the SSE stream surfaces the lifecycle event."""
    graph = StrategyGraph(
        nodes=[
            StrategyNode(
                node_id="only",
                node_type="normalize_query",
                config={},
                on_empty="skip",
            ),
        ],
        edges=[],
        entry_node="only",
        terminal_nodes=["only"],
    )
    spec = StrategySpec(
        strategy_id="t4-skip-test", graph=graph, budgets=StrategyBudgets()
    )
    registry = _registry_with(
        {
            # status="empty" + on_empty="skip" -> output.status becomes "skipped"
            "normalize_query": _MockExecutor(output_data=None, status="empty"),
        }
    )
    runner = StrategyRunner(registry=registry)

    received: list[tuple[str, str, str, float]] = []

    async def _cb(node_id: str, node_type: str, status: str, duration_ms: float) -> None:
        received.append((node_id, node_type, status, duration_ms))

    await runner.run(
        spec=spec,
        context=_business_context(),
        query="hi",
        on_node_complete=_cb,
    )

    assert len(received) == 1
    assert received[0][0] == "only"
    assert received[0][2] == "skipped"


@pytest.mark.asyncio
async def test_runner_is_reusable_after_callback_set_then_unset() -> None:
    """After a run, the per-run callback must be cleared so a subsequent
    run without a callback does not accidentally reuse the previous one."""
    spec = _linear_three_node_spec()
    registry = _registry_with(
        {
            "normalize_query": _MockExecutor(output_data="cleaned"),
            "retrieve": _MockExecutor(output_data=[]),
            "synthesize": _MockExecutor(
                output_data={"text": "ok", "citations": []}
            ),
        }
    )
    runner = StrategyRunner(registry=registry)

    received: list[str] = []

    async def _cb(node_id: str, node_type: str, status: str, duration_ms: float) -> None:
        received.append(node_id)

    await runner.run(
        spec=spec,
        context=_business_context(),
        query="run-1",
        on_node_complete=_cb,
    )
    assert len(received) == 3
    assert runner._on_node_complete is None

    received.clear()
    # Second run: NO callback. Must not re-fire the previous one.
    await runner.run(spec=spec, context=_business_context(), query="run-2")
    assert received == []
    assert runner._on_node_complete is None
