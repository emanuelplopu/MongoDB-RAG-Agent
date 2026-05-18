"""DedupeVersionsExecutor — deduplicate chunks from same document by version."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor
from backend.agent.strategy.models import (
    StrategyNode,
    StrategyRunState,
    NodeOutput,
    RetrievedChunk,
)

logger = logging.getLogger(__name__)


def _deduplicate_chunks(
    chunks: list[RetrievedChunk],
    prefer: str,
) -> list[RetrievedChunk]:
    """
    Deduplicate chunks by document_id.

    Within each document group, keep the chunk based on the preference:
    - 'highest_score': keep chunk with highest retrieval score
    - 'latest_version': keep chunk with most recent metadata.version
    """
    # Group by document_id
    groups: dict[str, list[RetrievedChunk]] = defaultdict(list)
    for chunk in chunks:
        groups[chunk.document_id].append(chunk)

    result: list[RetrievedChunk] = []

    for doc_id, doc_chunks in groups.items():
        if len(doc_chunks) == 1:
            result.append(doc_chunks[0])
            continue

        if prefer == "latest_version":
            # Sort by version (descending), fall back to score
            best = max(
                doc_chunks,
                key=lambda c: (
                    c.metadata.get("version", 0),
                    c.score,
                ),
            )
        else:
            # Default: highest_score
            best = max(doc_chunks, key=lambda c: c.score)

        result.append(best)

    return result


class DedupeVersionsExecutor(NodeExecutor):
    """Deduplicate chunks from the same document, keeping best version."""

    async def execute(self, node: StrategyNode, state: StrategyRunState) -> NodeOutput:
        accessor = self.get_state_accessor(state)
        chunks = accessor.get_chunks()

        if not chunks:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=None,
                tokens_used=0,
            )

        prefer = node.config.get("prefer", "highest_score")
        original_count = len(chunks)
        deduped = _deduplicate_chunks(chunks, prefer)

        logger.debug(
            "DedupeVersions: %d chunks → %d after dedup (prefer=%s)",
            original_count,
            len(deduped),
            prefer,
        )

        # Serialize to dicts for output_data
        output = [chunk.model_dump() for chunk in deduped]

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=output,
            tokens_used=0,
        )
