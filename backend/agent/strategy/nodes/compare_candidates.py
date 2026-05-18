"""CompareCandidatesExecutor — select best synthesis from multiple candidates."""
from __future__ import annotations

import logging
from typing import Any, Optional

from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor
from backend.agent.strategy.models import (
    StrategyNode,
    StrategyRunState,
    NodeOutput,
    SynthesisResult,
)

logger = logging.getLogger(__name__)


def _extract_synthesis_from_output(output_data: Any) -> Optional[SynthesisResult]:
    """Try to extract a SynthesisResult from a node's output_data."""
    if output_data is None:
        return None

    # If it's already a SynthesisResult instance
    if isinstance(output_data, SynthesisResult):
        return output_data

    # If it's a dict, try to parse as SynthesisResult
    if isinstance(output_data, dict):
        try:
            return SynthesisResult(**output_data)
        except (TypeError, ValueError):
            return None

    return None


def _score_candidate(
    synthesis: SynthesisResult,
    strategy: str,
) -> float:
    """
    Score a synthesis candidate based on the selection strategy.

    Strategies:
    - 'highest_confidence': prefer higher confidence
    - 'shortest': prefer fewer tokens (efficiency)
    - 'balanced': combine confidence and brevity
    """
    confidence = synthesis.confidence if synthesis.confidence is not None else 0.5
    # Normalize token count (lower is better for efficiency)
    # Use inverse so higher score = better
    token_score = 1.0 / max(synthesis.token_count, 1)

    if strategy == "shortest":
        return token_score
    elif strategy == "balanced":
        return (confidence * 0.7) + (token_score * 100 * 0.3)
    else:
        # Default: highest_confidence
        return confidence


class CompareCandidatesExecutor(NodeExecutor):
    """Select the best synthesis result from multiple candidate nodes."""

    async def execute(self, node: StrategyNode, state: StrategyRunState) -> NodeOutput:
        accessor = self.get_state_accessor(state)

        # Get candidate node IDs from config
        candidate_node_ids: list[str] = node.config.get("candidate_node_ids", [])
        selection_strategy: str = node.config.get(
            "selection_strategy", "highest_confidence"
        )

        # Collect candidates from node outputs
        candidates: list[tuple[str, SynthesisResult]] = []

        for candidate_id in candidate_node_ids:
            candidate_output = accessor.get_node_output(candidate_id)
            if candidate_output is None:
                continue
            if candidate_output.status != "success":
                continue

            synthesis = _extract_synthesis_from_output(candidate_output.output_data)
            if synthesis is not None:
                candidates.append((candidate_id, synthesis))

        # If no candidates found from config, try state synthesis_result
        if not candidates:
            current_synthesis = accessor.get_synthesis()
            if current_synthesis is not None:
                logger.debug(
                    "CompareCandidates: no candidates from config, using state synthesis"
                )
                return NodeOutput(
                    node_id=node.node_id,
                    node_type=node.node_type,
                    status="success",
                    output_data=current_synthesis.model_dump(),
                    tokens_used=0,
                )

            # Truly empty — nothing to compare
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=None,
                tokens_used=0,
            )

        # Score and select best candidate
        if len(candidates) == 1:
            best_id, best_synthesis = candidates[0]
        else:
            scored = [
                (cid, syn, _score_candidate(syn, selection_strategy))
                for cid, syn in candidates
            ]
            scored.sort(key=lambda x: x[2], reverse=True)
            best_id, best_synthesis, best_score = scored[0]

            logger.debug(
                "CompareCandidates: %d candidates, best=%s (score=%.3f, strategy=%s)",
                len(candidates),
                best_id,
                best_score,
                selection_strategy,
            )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=best_synthesis.model_dump(),
            tokens_used=0,
        )
