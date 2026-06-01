"""Tests for LLM-call capture wiring (Task 90 / T2).

Covers:

* :meth:`backend.agent.strategy.llm_helper.NodeLLMHelper.complete`
  persists a full :class:`LLMCallDoc` when a capture context is active.
* Failure path persists ``success=False`` + error and still propagates
  the underlying exception (existing contract preserved).
* Without a capture context (or with ``store=None``) capture is a no-op.
* Telemetry write errors never break the LLM call.
* :class:`backend.agent.strategy.strategy_runner.StrategyRunner`
  collects ``call_id`` values into ``state.llm_call_ids`` after each
  node executes.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agent.strategy.llm_call_store import (
    InMemoryLLMCallStore,
    LLMCallDoc,
)
from backend.agent.strategy.llm_helper import (
    LLMCaptureContext,
    NodeLLMHelper,
    reset_capture_context,
    set_capture_context,
)
from backend.agent.strategy.models import (
    BusinessContext,
    NodeOutput,
    StrategyBudgets,
    StrategyEdge,
    StrategyGraph,
    StrategyNode,
    StrategyRunState,
    StrategySpec,
    SynthesisResult,
)
from backend.agent.strategy.nodes.base import NodeExecutor
from backend.agent.strategy.nodes.registry import NodeRegistry
from backend.agent.strategy.strategy_runner import StrategyRunner


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _mock_response(text: str, *, total: int = 42, prompt: int = 30, completion: int = 12):
    """Build a litellm-shaped response object for ``acompletion`` mocks."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    usage = MagicMock()
    usage.total_tokens = total
    usage.prompt_tokens = prompt
    usage.completion_tokens = completion
    response.usage = usage
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Capture happy path
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_helper_captures_call_on_invoke() -> None:
    """A successful invoke persists one LLMCallDoc with full prompts and tokens."""
    store = InMemoryLLMCallStore()
    helper = NodeLLMHelper(model_registry=None, settings=None)

    capture = LLMCaptureContext(
        trace_id="trace-1",
        node_id="n_synth",
        node_type="synthesize",
        store=store,
    )
    token = set_capture_context(capture)

    response = _mock_response("Paris.", total=18, prompt=14, completion=4)
    messages = [
        {"role": "system", "content": "You are concise."},
        {"role": "user", "content": "Capital of France?"},
    ]
    try:
        with patch(
            "backend.agent.strategy.llm_helper.acompletion",
            new_callable=AsyncMock,
        ) as mock_acomp:
            mock_acomp.return_value = response
            text, tokens = await helper.complete("synthesizer_fast", messages)
    finally:
        reset_capture_context(token)

    assert text == "Paris."
    assert tokens == 18

    docs = store.all_docs
    assert len(docs) == 1
    doc = docs[0]
    assert isinstance(doc, LLMCallDoc)
    assert doc.trace_id == "trace-1"
    assert doc.node_id == "n_synth"
    assert doc.node_type == "synthesize"
    assert doc.model == "llama3.1:8b"
    assert doc.provider == "ollama"
    assert doc.model_role == "synthesizer_fast"
    assert doc.system_prompt == "You are concise."
    assert "Capital of France?" in doc.user_prompt
    assert doc.assistant_response == "Paris."
    assert doc.prompt_tokens == 14
    assert doc.completion_tokens == 4
    assert doc.total_tokens == 18
    assert doc.latency_ms >= 0.0
    assert doc.success is True
    assert doc.error is None
    # call_id is appended to the active capture context for runner pickup.
    assert capture.call_ids == [doc.call_id]


# ─────────────────────────────────────────────────────────────────────────────
# Capture on failure
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_helper_captures_failure() -> None:
    """When the provider raises, a failure doc is saved AND the exception propagates."""
    store = InMemoryLLMCallStore()
    helper = NodeLLMHelper(model_registry=None, settings=None)

    capture = LLMCaptureContext(
        trace_id="trace-fail",
        node_id="n_synth",
        node_type="synthesize",
        store=store,
    )
    token = set_capture_context(capture)

    boom = RuntimeError("provider exploded")
    try:
        with patch(
            "backend.agent.strategy.llm_helper.acompletion",
            new_callable=AsyncMock,
        ) as mock_acomp:
            mock_acomp.side_effect = boom
            with pytest.raises(RuntimeError, match="provider exploded"):
                await helper.complete(
                    "worker",
                    [{"role": "user", "content": "trigger failure"}],
                )
    finally:
        reset_capture_context(token)

    docs = store.all_docs
    assert len(docs) == 1
    doc = docs[0]
    assert doc.success is False
    assert doc.error is not None
    assert "provider exploded" in doc.error
    assert doc.assistant_response == ""
    assert capture.call_ids == [doc.call_id]


# ─────────────────────────────────────────────────────────────────────────────
# No-op paths
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_helper_no_store_means_no_capture() -> None:
    """No capture context → no save attempted, normal return."""
    helper = NodeLLMHelper(model_registry=None, settings=None)
    response = _mock_response("hi", total=7)

    # Explicitly clear any leftover context from prior tests.
    token = set_capture_context(None)
    try:
        with patch(
            "backend.agent.strategy.llm_helper.acompletion",
            new_callable=AsyncMock,
        ) as mock_acomp:
            mock_acomp.return_value = response
            text, tokens = await helper.complete(
                "worker",
                [{"role": "user", "content": "ping"}],
            )
    finally:
        reset_capture_context(token)

    assert text == "hi"
    assert tokens == 7
    # No exceptions raised — implicit assertion: capture path was a no-op.


@pytest.mark.asyncio
async def test_llm_helper_capture_with_none_store_is_noop() -> None:
    """Capture context with ``store=None`` is treated as no capture."""
    helper = NodeLLMHelper(model_registry=None, settings=None)
    capture = LLMCaptureContext(
        trace_id="t",
        node_id="n",
        node_type="synthesize",
        store=None,
    )
    token = set_capture_context(capture)
    response = _mock_response("ok", total=3)
    try:
        with patch(
            "backend.agent.strategy.llm_helper.acompletion",
            new_callable=AsyncMock,
        ) as mock_acomp:
            mock_acomp.return_value = response
            text, _tokens = await helper.complete(
                "worker",
                [{"role": "user", "content": "x"}],
            )
    finally:
        reset_capture_context(token)
    assert text == "ok"
    assert capture.call_ids == []


# ─────────────────────────────────────────────────────────────────────────────
# Best-effort: store failures never raise
# ─────────────────────────────────────────────────────────────────────────────


class _ExplodingStore(InMemoryLLMCallStore):
    async def save(self, doc: LLMCallDoc) -> None:  # type: ignore[override]
        raise RuntimeError("disk full")


@pytest.mark.asyncio
async def test_llm_helper_capture_never_raises() -> None:
    """If ``store.save`` raises, the LLM call still returns normally."""
    helper = NodeLLMHelper(model_registry=None, settings=None)
    capture = LLMCaptureContext(
        trace_id="t",
        node_id="n",
        node_type="synthesize",
        store=_ExplodingStore(),
    )
    token = set_capture_context(capture)
    response = _mock_response("answer", total=11)
    try:
        with patch(
            "backend.agent.strategy.llm_helper.acompletion",
            new_callable=AsyncMock,
        ) as mock_acomp:
            mock_acomp.return_value = response
            text, tokens = await helper.complete(
                "worker",
                [{"role": "user", "content": "q"}],
            )
    finally:
        reset_capture_context(token)
    assert text == "answer"
    assert tokens == 11
    # Failed save → no call_id appended.
    assert capture.call_ids == []


# ─────────────────────────────────────────────────────────────────────────────
# StrategyRunner integration
# ─────────────────────────────────────────────────────────────────────────────


class _FakeLLMNode(NodeExecutor):
    """Node that simulates a single LLM invocation per execution."""

    def __init__(self, *, prefix: str = "node") -> None:
        self._prefix = prefix
        self._counter = 0

    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        # Pretend the node uses NodeLLMHelper.complete; capture happens
        # transparently via the active LLMCaptureContext set by the runner.
        helper = NodeLLMHelper(model_registry=None, settings=None)
        with patch(
            "backend.agent.strategy.llm_helper.acompletion",
            new_callable=AsyncMock,
        ) as mock_acomp:
            mock_acomp.return_value = _mock_response(
                f"{self._prefix}-response-{self._counter}",
                total=10,
            )
            text, _tokens = await helper.complete(
                "worker",
                [{"role": "user", "content": f"q-{self._counter}"}],
            )
        self._counter += 1

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=SynthesisResult(text=text).model_dump(),
            tokens_used=10,
        )


def _two_node_spec() -> StrategySpec:
    """Build a minimal two-node sequential synthesize-only DAG."""
    nodes = [
        StrategyNode(node_id="a", node_type="synthesize"),
        StrategyNode(node_id="b", node_type="refine"),
    ]
    edges = [StrategyEdge(from_node="a", to_node="b")]
    graph = StrategyGraph(
        nodes=nodes,
        edges=edges,
        entry_node="a",
        terminal_nodes=["b"],
    )
    return StrategySpec(
        strategy_id="capture-test",
        graph=graph,
        budgets=StrategyBudgets(
            latency_target_ms=60_000,
            latency_hard_limit_ms=120_000,
            max_context_tokens=10_000,
            max_llm_calls=10,
        ),
    )


@pytest.mark.asyncio
async def test_call_ids_collected_on_state() -> None:
    """Two nodes each making one LLM call → ``state.llm_call_ids`` has 2 entries."""
    registry = NodeRegistry()
    registry.register("synthesize", _FakeLLMNode)
    registry.register("refine", _FakeLLMNode)

    store = InMemoryLLMCallStore()
    runner = StrategyRunner(registry=registry, llm_call_store=store)

    spec = _two_node_spec()
    context = BusinessContext(tenant_id="t1")
    result = await runner.run(spec=spec, context=context, query="hello")

    assert len(result.state.llm_call_ids) == 2
    persisted = store.all_docs
    assert len(persisted) == 2
    assert {d.node_id for d in persisted} == {"a", "b"}
    assert {d.call_id for d in persisted} == set(result.state.llm_call_ids)
    # Every persisted doc shares the run trace_id.
    assert {d.trace_id for d in persisted} == {result.state.trace_id}


@pytest.mark.asyncio
async def test_runner_without_store_records_no_call_ids() -> None:
    """Backward-compat: with no llm_call_store, capture is a no-op."""
    registry = NodeRegistry()
    registry.register("synthesize", _FakeLLMNode)
    registry.register("refine", _FakeLLMNode)

    runner = StrategyRunner(registry=registry, llm_call_store=None)
    spec = _two_node_spec()
    context = BusinessContext(tenant_id="t1")
    result = await runner.run(spec=spec, context=context, query="hello")

    assert result.state.llm_call_ids == []
