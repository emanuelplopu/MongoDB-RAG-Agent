"""EmitTelemetryExecutor — side-effect node for operational trace logging."""
from __future__ import annotations

import logging
from typing import Any

from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor
from backend.agent.strategy.models import StrategyNode, StrategyRunState, NodeOutput

logger = logging.getLogger(__name__)


def _collect_metrics(state: StrategyRunState) -> dict[str, Any]:
    """
    Collect execution metrics from the current state.

    Returns:
        Summary dict with nodes_executed, total_tokens, total_duration_ms.
    """
    node_outputs = state.node_outputs or {}

    nodes_executed = len(node_outputs)
    total_tokens = 0
    total_duration_ms = 0.0
    node_summaries: list[dict[str, Any]] = []

    for node_id, output in node_outputs.items():
        total_tokens += output.tokens_used
        total_duration_ms += output.duration_ms

        node_summaries.append({
            "node_id": node_id,
            "node_type": output.node_type,
            "status": output.status,
            "duration_ms": output.duration_ms,
            "tokens_used": output.tokens_used,
        })

    return {
        "nodes_executed": nodes_executed,
        "total_tokens": total_tokens,
        "total_duration_ms": round(total_duration_ms, 2),
        "node_summaries": node_summaries,
        "run_id": state.run_id,
        "trace_id": state.trace_id,
        "query_length": len(state.query),
        "chunks_in_state": len(state.retrieved_chunks),
        "evidence_cards_in_state": len(state.evidence_cards),
        "has_synthesis": state.synthesis_result is not None,
        "validation_count": len(state.validation_results),
    }


class EmitTelemetryExecutor(NodeExecutor):
    """Side-effect node that collects and logs execution metrics.

    This node does not transform data — it observes the current state,
    builds a summary of execution metrics, and emits them via logging.
    The summary is also returned as output_data for downstream use.
    """

    async def execute(self, node: StrategyNode, state: StrategyRunState) -> NodeOutput:
        # Collect metrics from all executed nodes
        metrics = _collect_metrics(state)

        if metrics["nodes_executed"] == 0:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=None,
                tokens_used=0,
            )

        # Log the operational trace
        logger.info(
            "StrategyTelemetry [run=%s trace=%s]: "
            "nodes=%d, tokens=%d, duration=%.1fms, "
            "chunks=%d, evidence=%d, synthesis=%s, validations=%d",
            metrics["run_id"],
            metrics["trace_id"],
            metrics["nodes_executed"],
            metrics["total_tokens"],
            metrics["total_duration_ms"],
            metrics["chunks_in_state"],
            metrics["evidence_cards_in_state"],
            metrics["has_synthesis"],
            metrics["validation_count"],
        )

        # Emit per-node summaries at debug level
        for summary in metrics.get("node_summaries", []):
            logger.debug(
                "  Node %s (%s): status=%s, duration=%.1fms, tokens=%d",
                summary["node_id"],
                summary["node_type"],
                summary["status"],
                summary["duration_ms"],
                summary["tokens_used"],
            )

        # Return the top-level metrics as output_data
        result = {
            "nodes_executed": metrics["nodes_executed"],
            "total_tokens": metrics["total_tokens"],
            "total_duration_ms": metrics["total_duration_ms"],
        }

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=result,
            tokens_used=0,
        )
