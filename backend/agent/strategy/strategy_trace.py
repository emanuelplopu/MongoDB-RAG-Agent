"""Aggregated Strategy OS trace exposed to chat responses (T3).

This module defines :class:`StrategyTraceResponse` — the unified, structured
view of a single Strategy OS execution that the coordinator attaches to
every chat response. The frontend's strategy-trace panel and the telemetry
viewer consume this object alongside the legacy
:class:`backend.agent.schemas.AgentTrace` (``FederatedAgentTrace``).

The model is intentionally derived from already-captured artefacts:

* :class:`StrategyRunResult` produced by
  :class:`~backend.agent.strategy.strategy_runner.StrategyRunner` (T2/F2).
* :class:`StrategySpec` selected by
  :class:`~backend.agent.strategy.spec_selector.AdaptiveSelector` (P9).
* The ``context_budget_applied`` event accumulated under
  ``state.metadata["trace_events"]`` by the synthesize node (P3).
* The persisted :class:`AdaptiveDecisionDoc` candidate scores (F8).

Helper :func:`build_strategy_trace` performs the projection from these
sources into a flat, JSON-serialisable trace.

Reference: docs/07-STRATEGY_OS_OVERVIEW.md
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.agent.strategy.models import (
    StrategyRunResult,
    StrategySpec,
)
from backend.agent.strategy.operational_trace import TRACE_EVENTS_METADATA_KEY

logger = logging.getLogger(__name__)

__all__ = [
    "NodeTraceEntry",
    "ContextBudgetSummary",
    "StrategyTraceResponse",
    "build_strategy_trace",
]


#: Trace-event name emitted by the synthesize node when it applies
#: :class:`~backend.agent.strategy.context_budgeter.ContextBudgeter`.
_BUDGET_EVENT_NAME = "context_budget_applied"

#: Routing-reason identifiers consumed by the frontend trace panel and
#: telemetry viewer. Kept as plain strings (rather than an Enum) for
#: forward-compat: new callers can introduce additional reasons without
#: requiring a coordinated frontend deploy.
ROUTING_REASON_ADAPTIVE: str = "adaptive_selector"
ROUTING_REASON_LEGACY_FALLBACK: str = "legacy_fallback"
ROUTING_REASON_CAPABILITY_NOT_MATCHED: str = "capability_not_matched"
ROUTING_REASON_DISABLED: str = "strategy_os_disabled"
ROUTING_REASON_DAG_FAILED: str = "dag_failed_fallback"


# ─────────────────────────────────────────────────────────────────────────────
# Sub-models
# ─────────────────────────────────────────────────────────────────────────────


class NodeTraceEntry(BaseModel):
    """Per-node execution record projected from :class:`NodeOutput`.

    Attributes:
        node_id: Stable identifier of the node within the strategy DAG.
        node_type: Node-type registry key (e.g. ``"retrieve"``).
        status: Final node status. One of ``success`` / ``failed`` /
            ``skipped`` (collapsed from the broader
            :class:`~backend.agent.strategy.models.NodeOutput.status`
            enum so the frontend can render a 3-color badge).
        duration_ms: Wall-clock duration of the node, in milliseconds.
        tokens_used: LLM tokens attributed to the node, summed across
            its captured calls.
        llm_call_ids: ``call_id`` values recorded by
            :class:`~backend.agent.strategy.llm_helper.NodeLLMHelper`
            during this node's execution. Joins back to
            ``strategy_llm_calls`` for full prompt/response inspection.
        error: Error message when ``status != "success"``.
    """

    model_config = ConfigDict(extra="allow")

    node_id: str
    node_type: str
    status: str
    duration_ms: float = 0.0
    tokens_used: int = 0
    llm_call_ids: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class ContextBudgetSummary(BaseModel):
    """Compact summary of the synthesize-node context budget allocation.

    Built from the ``context_budget_applied`` trace event emitted by
    :class:`~backend.agent.strategy.nodes.synthesize.SynthesizeNode`.

    Attributes:
        total_budget_tokens: Token cap configured for the budgeter.
        tokens_used: Sum of tokens across included items.
        included_count: Number of items that fit in the budget.
        dropped_count: Number of items dropped due to the budget.
        dropped_items: Per-dropped-item dicts of the form
            ``{"kind", "tokens", "reason"}``.
    """

    model_config = ConfigDict(extra="allow")

    total_budget_tokens: int = 0
    tokens_used: int = 0
    included_count: int = 0
    dropped_count: int = 0
    dropped_items: list[dict[str, Any]] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Top-level trace
# ─────────────────────────────────────────────────────────────────────────────


class StrategyTraceResponse(BaseModel):
    """Unified Strategy OS trace attached to a chat response.

    Sits alongside the legacy ``FederatedAgentTrace`` — both flow back
    to the frontend, which renders the legacy panel and the new
    Strategy-OS panel together. Set ``active=False`` for legacy /
    fallback executions; the frontend hides Strategy-specific affordances
    in that case.

    Attributes:
        active: ``True`` only when the Strategy OS DAG ran end-to-end
            and produced a synthesis result.
        spec_selected: ``strategy_id`` of the chosen
            :class:`StrategySpec` (``None`` when no spec was selected).
        spec_version: Spec version string (semver-ish) for the chosen
            spec.
        routing_reason: How the spec was chosen. One of
            :data:`ROUTING_REASON_ADAPTIVE`,
            :data:`ROUTING_REASON_LEGACY_FALLBACK`,
            :data:`ROUTING_REASON_CAPABILITY_NOT_MATCHED`,
            :data:`ROUTING_REASON_DISABLED`,
            :data:`ROUTING_REASON_DAG_FAILED`. Stored as ``str`` for
            forward-compat.
        adaptive_scores: Per-candidate score breakdown produced by
            :class:`AdaptiveSelector` and persisted into
            ``adaptive_selection_decisions`` (F8). ``None`` if scores
            were not available (cache hit or non-adaptive selector).
        fast_path_eligible: Result of
            :class:`~backend.agent.strategy.spec_selector.FastPathRules`
            for this request.
        nodes_executed: Flat per-node trace, in execution order.
        total_duration_ms: Wall-clock duration of the DAG run.
        context_budget: Synthesize-node budget summary, when emitted.
        trace_id: ``trace_id`` correlating this run with persisted
            ``strategy_runs`` and ``strategy_llm_calls`` documents.
        llm_call_count: Number of captured LLM invocations.
        total_llm_tokens: Sum of ``tokens_used`` across nodes.
        fallback_reason: When the DAG failed and the coordinator fell
            back to the legacy orchestrator, the underlying exception
            message.
    """

    model_config = ConfigDict(extra="allow")

    active: bool = False
    spec_selected: Optional[str] = None
    spec_version: Optional[str] = None
    routing_reason: str = ROUTING_REASON_LEGACY_FALLBACK
    adaptive_scores: Optional[list[dict[str, Any]]] = None
    fast_path_eligible: bool = False
    nodes_executed: list[NodeTraceEntry] = Field(default_factory=list)
    total_duration_ms: float = 0.0
    context_budget: Optional[ContextBudgetSummary] = None
    trace_id: Optional[str] = None
    llm_call_count: int = 0
    total_llm_tokens: int = 0
    fallback_reason: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Builder helpers
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_status(raw: Any) -> str:
    """Collapse a :class:`NodeOutput.status` value into a 3-state badge.

    The runtime status enum has five values
    (``success``/``skipped``/``empty``/``error``/``timed_out``); the
    frontend trace panel only renders three. This mapping keeps the
    public surface stable when more enum values are added.
    """
    text = str(raw or "").lower()
    if text in {"success", "ok"}:
        return "success"
    if text in {"skipped", "empty"}:
        return "skipped"
    # error, timed_out, failed, anything else → failed
    return "failed"


def _node_trace_from_output(output: Any) -> NodeTraceEntry:
    """Project a single :class:`NodeOutput` (or compatible mapping) onto
    a :class:`NodeTraceEntry`.

    Accepts both Pydantic models and plain ``dict`` payloads so callers
    that hydrate state from telemetry (where pydantic types are no longer
    available) work transparently.
    """
    def _g(name: str, default: Any = None) -> Any:
        if hasattr(output, name):
            return getattr(output, name)
        if isinstance(output, dict):
            return output.get(name, default)
        return default

    raw_call_ids = _g("llm_call_ids", []) or []
    if not isinstance(raw_call_ids, list):
        raw_call_ids = []

    return NodeTraceEntry(
        node_id=str(_g("node_id", "") or ""),
        node_type=str(_g("node_type", "") or ""),
        status=_normalize_status(_g("status", "success")),
        duration_ms=float(_g("duration_ms", 0.0) or 0.0),
        tokens_used=int(_g("tokens_used", 0) or 0),
        llm_call_ids=[str(c) for c in raw_call_ids if c],
        error=_g("error_message", None),
    )


def _extract_context_budget(state: Any) -> Optional[ContextBudgetSummary]:
    """Return the most recent ``context_budget_applied`` event as a
    :class:`ContextBudgetSummary`, or ``None`` if no such event exists.

    The synthesize node may emit multiple budget events when ``refine``
    iterations are active; the latest one most accurately describes the
    final synthesized answer.
    """
    metadata = getattr(state, "metadata", None) or {}
    if not isinstance(metadata, dict):
        return None
    events = metadata.get(TRACE_EVENTS_METADATA_KEY) or []
    if not isinstance(events, list):
        return None

    last_payload: Optional[dict[str, Any]] = None
    for entry in events:
        if not isinstance(entry, dict):
            continue
        if entry.get("event") != _BUDGET_EVENT_NAME:
            continue
        payload = entry.get("payload")
        if isinstance(payload, dict):
            last_payload = payload

    if last_payload is None:
        return None

    dropped_raw = last_payload.get("dropped") or []
    dropped_items: list[dict[str, Any]] = [
        d for d in dropped_raw if isinstance(d, dict)
    ]

    return ContextBudgetSummary(
        total_budget_tokens=int(last_payload.get("total_budget_tokens", 0) or 0),
        tokens_used=int(last_payload.get("total_tokens_used", 0) or 0),
        included_count=int(last_payload.get("included_total", 0) or 0),
        dropped_count=len(dropped_items),
        dropped_items=dropped_items,
    )


def build_strategy_trace(
    *,
    spec: Optional[StrategySpec],
    run_result: Optional[StrategyRunResult],
    routing_reason: str,
    adaptive_scores: Optional[list[dict[str, Any]]] = None,
    fast_path_eligible: bool = False,
    fallback_reason: Optional[str] = None,
) -> StrategyTraceResponse:
    """Construct a :class:`StrategyTraceResponse` from execution artefacts.

    Args:
        spec: The selected :class:`StrategySpec`, or ``None`` for
            legacy / disabled / capability-miss paths.
        run_result: The :class:`StrategyRunResult` produced by
            :class:`~backend.agent.strategy.strategy_runner.StrategyRunner`.
            ``None`` when the DAG did not run (legacy fallback,
            disabled, capability-miss, DAG failure).
        routing_reason: Free-form identifier describing how the spec
            was chosen. Use one of the ``ROUTING_REASON_*`` constants.
        adaptive_scores: Optional per-candidate score breakdown from
            :class:`~backend.agent.strategy.adaptive_decision_store.AdaptiveDecisionDoc.candidate_scores`.
        fast_path_eligible: Whether the request was fast-path eligible
            (read from the same adaptive decision).
        fallback_reason: Underlying exception message when
            ``routing_reason == ROUTING_REASON_DAG_FAILED``.

    Returns:
        A populated :class:`StrategyTraceResponse`. ``active`` is
        ``True`` only when ``run_result`` indicates a successful DAG
        run that produced a synthesis result.
    """
    trace = StrategyTraceResponse(
        active=False,
        routing_reason=routing_reason,
        adaptive_scores=adaptive_scores,
        fast_path_eligible=bool(fast_path_eligible),
        fallback_reason=fallback_reason,
    )

    if spec is not None:
        trace.spec_selected = spec.strategy_id
        trace.spec_version = spec.version

    if run_result is None:
        return trace

    state = run_result.state
    trace.trace_id = getattr(state, "trace_id", None)
    trace.total_duration_ms = float(
        getattr(run_result, "total_duration_ms", 0.0) or 0.0
    )

    # Project node_outputs (insertion-order preserved by Python dicts) →
    # ordered list of NodeTraceEntry. Sum tokens for the aggregate
    # ``total_llm_tokens`` field.
    node_outputs = getattr(state, "node_outputs", {}) or {}
    nodes: list[NodeTraceEntry] = []
    total_tokens = 0
    if isinstance(node_outputs, dict):
        for output in node_outputs.values():
            entry = _node_trace_from_output(output)
            nodes.append(entry)
            total_tokens += entry.tokens_used
    trace.nodes_executed = nodes
    trace.total_llm_tokens = total_tokens

    # LLM call IDs accumulated by NodeLLMHelper / runner.
    call_ids = getattr(state, "llm_call_ids", []) or []
    trace.llm_call_count = len(call_ids) if isinstance(call_ids, list) else 0

    # Context budget summary from the synthesize node trace event.
    trace.context_budget = _extract_context_budget(state)

    # ``active`` requires the DAG to have produced an end-to-end answer.
    success = bool(getattr(run_result, "success", False))
    has_synthesis = getattr(state, "synthesis_result", None) is not None
    trace.active = success and has_synthesis

    return trace
