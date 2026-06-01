"""Tests for the StrategyTraceResponse model and ``build_strategy_trace`` helper.

Covers the projection from :class:`StrategyRunResult` artefacts onto a flat
:class:`StrategyTraceResponse` (T3 / Task 91), including success, DAG
failure, legacy-fallback, adaptive-scores enrichment, missing-context-budget
edge case, and per-node mapping.
"""
from __future__ import annotations

import pytest

from backend.agent.strategy.models import (
    NodeOutput,
    StrategyBudgets,
    StrategyEdge,
    StrategyGraph,
    StrategyNode,
    StrategyRunResult,
    StrategyRunState,
    StrategySpec,
    SynthesisResult,
)
from backend.agent.strategy.operational_trace import TRACE_EVENTS_METADATA_KEY
from backend.agent.strategy.strategy_trace import (
    ContextBudgetSummary,
    NodeTraceEntry,
    ROUTING_REASON_ADAPTIVE,
    ROUTING_REASON_DAG_FAILED,
    ROUTING_REASON_LEGACY_FALLBACK,
    StrategyTraceResponse,
    build_strategy_trace,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _make_spec(strategy_id: str = "test_strategy", version: str = "1.2.3") -> StrategySpec:
    """Return a minimal active :class:`StrategySpec` for trace assembly."""
    graph = StrategyGraph(
        nodes=[StrategyNode(node_id="n1", node_type="retrieve")],
        edges=[StrategyEdge(from_node="n1", to_node="n1")],
        entry_node="n1",
        terminal_nodes=["n1"],
    )
    return StrategySpec(
        strategy_id=strategy_id,
        version=version,
        graph=graph,
        budgets=StrategyBudgets(),
    )


def _make_run_result(
    *,
    nodes: list[NodeOutput],
    llm_call_ids: list[str],
    trace_events: list[dict] | None = None,
    synthesis: SynthesisResult | None = None,
    success: bool = True,
    total_duration_ms: float = 1234.5,
    trace_id: str = "trace-xyz",
) -> StrategyRunResult:
    """Construct a :class:`StrategyRunResult` with explicit state contents."""
    state = StrategyRunState(
        trace_id=trace_id,
        node_outputs={n.node_id: n for n in nodes},
        llm_call_ids=list(llm_call_ids),
        synthesis_result=synthesis,
    )
    if trace_events is not None:
        state.metadata[TRACE_EVENTS_METADATA_KEY] = trace_events
    return StrategyRunResult(
        success=success,
        state=state,
        strategy_id="test_strategy",
        total_duration_ms=total_duration_ms,
        nodes_executed=len(nodes),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_build_strategy_trace_success_path() -> None:
    """A complete run populates nodes, budget, call count, trace_id."""
    nodes = [
        NodeOutput(
            node_id="n_retrieve",
            node_type="retrieve",
            status="success",
            duration_ms=12.5,
            tokens_used=100,
        ),
        NodeOutput(
            node_id="n_evidence",
            node_type="evidence_cards",
            status="success",
            duration_ms=20.0,
            tokens_used=250,
        ),
        NodeOutput(
            node_id="n_synth",
            node_type="synthesize",
            status="success",
            duration_ms=80.0,
            tokens_used=500,
        ),
    ]
    trace_events = [
        {
            "event": "context_budget_applied",
            "payload": {
                "node_id": "n_synth",
                "total_budget_tokens": 8000,
                "total_tokens_used": 7100,
                "included_total": 5,
                "included_count_by_kind": {"evidence_card": 4, "user_prompt": 1},
                "dropped": [
                    {"kind": "evidence_card", "tokens": 400, "reason": "budget_exhausted"},
                ],
            },
        },
    ]
    spec = _make_spec()
    result = _make_run_result(
        nodes=nodes,
        llm_call_ids=["call-1", "call-2", "call-3"],
        trace_events=trace_events,
        synthesis=SynthesisResult(text="answer"),
    )

    trace = build_strategy_trace(
        spec=spec,
        run_result=result,
        routing_reason=ROUTING_REASON_ADAPTIVE,
        fast_path_eligible=True,
    )

    assert isinstance(trace, StrategyTraceResponse)
    assert trace.active is True
    assert trace.spec_selected == "test_strategy"
    assert trace.spec_version == "1.2.3"
    assert trace.routing_reason == ROUTING_REASON_ADAPTIVE
    assert trace.fast_path_eligible is True
    assert trace.fallback_reason is None
    assert trace.trace_id == "trace-xyz"
    assert trace.total_duration_ms == 1234.5
    assert trace.llm_call_count == 3
    assert trace.total_llm_tokens == 100 + 250 + 500
    assert len(trace.nodes_executed) == 3
    assert trace.nodes_executed[0].node_id == "n_retrieve"
    assert trace.nodes_executed[2].tokens_used == 500
    assert isinstance(trace.context_budget, ContextBudgetSummary)
    assert trace.context_budget.total_budget_tokens == 8000
    assert trace.context_budget.tokens_used == 7100
    assert trace.context_budget.included_count == 5
    assert trace.context_budget.dropped_count == 1
    assert trace.context_budget.dropped_items[0]["reason"] == "budget_exhausted"


def test_build_strategy_trace_dag_failure() -> None:
    """No run_result → trace is inactive and carries the fallback reason."""
    spec = _make_spec()
    trace = build_strategy_trace(
        spec=spec,
        run_result=None,
        routing_reason=ROUTING_REASON_DAG_FAILED,
        fallback_reason="boom: node retrieve crashed",
    )

    assert trace.active is False
    assert trace.routing_reason == ROUTING_REASON_DAG_FAILED
    assert trace.fallback_reason == "boom: node retrieve crashed"
    assert trace.spec_selected == "test_strategy"
    assert trace.nodes_executed == []
    assert trace.context_budget is None
    assert trace.llm_call_count == 0
    assert trace.total_llm_tokens == 0
    assert trace.trace_id is None


def test_build_strategy_trace_legacy_fallback() -> None:
    """No spec, no run_result → legacy fallback shape."""
    trace = build_strategy_trace(
        spec=None,
        run_result=None,
        routing_reason=ROUTING_REASON_LEGACY_FALLBACK,
    )

    assert trace.active is False
    assert trace.routing_reason == ROUTING_REASON_LEGACY_FALLBACK
    assert trace.spec_selected is None
    assert trace.spec_version is None
    assert trace.adaptive_scores is None
    assert trace.fast_path_eligible is False
    assert trace.fallback_reason is None
    assert trace.nodes_executed == []


def test_build_strategy_trace_with_adaptive_scores() -> None:
    """Adaptive scores list flows through to the response unchanged."""
    candidate_scores = [
        {
            "strategy_id": "fast_v1",
            "latency_fit": 0.9,
            "quality": 0.7,
            "resource_fit": 0.8,
            "residency": 0.0,
            "fast_path_bias": 0.05,
            "total": 0.81,
            "disqualified": False,
            "disqualified_reason": None,
        },
        {
            "strategy_id": "deep_v1",
            "latency_fit": 0.4,
            "quality": 0.9,
            "resource_fit": 0.6,
            "residency": 0.1,
            "fast_path_bias": 0.0,
            "total": 0.62,
            "disqualified": False,
            "disqualified_reason": None,
        },
    ]
    spec = _make_spec(strategy_id="fast_v1")
    nodes = [
        NodeOutput(
            node_id="n_retrieve",
            node_type="retrieve",
            status="success",
            duration_ms=5.0,
            tokens_used=10,
        ),
    ]
    result = _make_run_result(
        nodes=nodes,
        llm_call_ids=["c1"],
        synthesis=SynthesisResult(text="ok"),
    )

    trace = build_strategy_trace(
        spec=spec,
        run_result=result,
        routing_reason=ROUTING_REASON_ADAPTIVE,
        adaptive_scores=candidate_scores,
        fast_path_eligible=True,
    )

    assert trace.adaptive_scores == candidate_scores
    assert trace.fast_path_eligible is True
    assert trace.spec_selected == "fast_v1"
    assert trace.active is True


def test_build_strategy_trace_no_budget_event() -> None:
    """When no context_budget_applied event exists, context_budget is None."""
    nodes = [
        NodeOutput(
            node_id="n_retrieve",
            node_type="retrieve",
            status="success",
            duration_ms=5.0,
            tokens_used=10,
        ),
    ]
    # Trace events present but no budget event.
    trace_events = [
        {"event": "selector_decided", "payload": {"foo": "bar"}},
    ]
    result = _make_run_result(
        nodes=nodes,
        llm_call_ids=[],
        trace_events=trace_events,
        synthesis=SynthesisResult(text="hi"),
    )

    trace = build_strategy_trace(
        spec=_make_spec(),
        run_result=result,
        routing_reason=ROUTING_REASON_ADAPTIVE,
    )

    assert trace.context_budget is None
    assert trace.llm_call_count == 0
    assert trace.active is True


def test_node_trace_entry_maps_correctly() -> None:
    """NodeOutput → NodeTraceEntry collapses status and preserves fields."""
    nodes = [
        NodeOutput(
            node_id="n_ok",
            node_type="retrieve",
            status="success",
            duration_ms=11.1,
            tokens_used=42,
        ),
        NodeOutput(
            node_id="n_skipped",
            node_type="evidence_cards",
            status="empty",
            duration_ms=0.5,
            tokens_used=0,
        ),
        NodeOutput(
            node_id="n_failed",
            node_type="synthesize",
            status="error",
            duration_ms=2.2,
            tokens_used=0,
            error_message="LLM provider unavailable",
        ),
        NodeOutput(
            node_id="n_timeout",
            node_type="validate",
            status="timed_out",
            duration_ms=999.0,
            tokens_used=0,
            error_message="deadline exceeded",
        ),
    ]
    result = _make_run_result(
        nodes=nodes,
        llm_call_ids=["c-a", "c-b"],
        synthesis=None,
        success=False,
    )

    trace = build_strategy_trace(
        spec=_make_spec(),
        run_result=result,
        routing_reason=ROUTING_REASON_ADAPTIVE,
    )

    # active requires success AND synthesis_result; both missing here.
    assert trace.active is False
    assert len(trace.nodes_executed) == 4

    by_id = {entry.node_id: entry for entry in trace.nodes_executed}
    assert isinstance(by_id["n_ok"], NodeTraceEntry)
    assert by_id["n_ok"].status == "success"
    # NodeOutput does not currently expose per-node llm_call_ids; the
    # field defaults to [] on the trace entry. Per-node call IDs are
    # populated by the dict-based path (see
    # ``test_build_strategy_trace_node_dict_payload_supported``).
    assert by_id["n_ok"].llm_call_ids == []
    assert by_id["n_ok"].tokens_used == 42
    assert by_id["n_skipped"].status == "skipped"
    assert by_id["n_failed"].status == "failed"
    assert by_id["n_failed"].error == "LLM provider unavailable"
    assert by_id["n_timeout"].status == "failed"
    assert by_id["n_timeout"].error == "deadline exceeded"


def test_build_strategy_trace_inactive_when_no_synthesis() -> None:
    """A successful run without synthesis_result must remain ``active=False``."""
    nodes = [
        NodeOutput(
            node_id="n_retrieve",
            node_type="retrieve",
            status="success",
            duration_ms=5.0,
            tokens_used=10,
        ),
    ]
    result = _make_run_result(
        nodes=nodes,
        llm_call_ids=["c1"],
        synthesis=None,
        success=True,
    )

    trace = build_strategy_trace(
        spec=_make_spec(),
        run_result=result,
        routing_reason=ROUTING_REASON_ADAPTIVE,
    )

    assert trace.active is False
    assert trace.llm_call_count == 1
    assert trace.total_llm_tokens == 10


def test_build_strategy_trace_node_dict_payload_supported() -> None:
    """Node mapping accepts plain dicts (e.g. rehydrated from telemetry)."""
    state = StrategyRunState(trace_id="t-1")
    # Inject a raw dict in place of a NodeOutput pydantic model — this
    # mirrors the shape callers see when reading persisted ``strategy_runs``.
    state.node_outputs["n1"] = {
        "node_id": "n1",
        "node_type": "retrieve",
        "status": "success",
        "duration_ms": 7.0,
        "tokens_used": 22,
        "llm_call_ids": ["c-1"],
    }  # type: ignore[assignment]
    result = StrategyRunResult(
        success=True,
        state=state,
        strategy_id="test_strategy",
        total_duration_ms=7.0,
    )

    trace = build_strategy_trace(
        spec=_make_spec(),
        run_result=result,
        routing_reason=ROUTING_REASON_ADAPTIVE,
    )

    assert len(trace.nodes_executed) == 1
    entry = trace.nodes_executed[0]
    assert entry.node_id == "n1"
    assert entry.node_type == "retrieve"
    assert entry.status == "success"
    assert entry.tokens_used == 22
    assert entry.llm_call_ids == ["c-1"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
