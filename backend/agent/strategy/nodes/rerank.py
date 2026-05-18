"""RerankExecutor — Re-score and reorder retrieved chunks."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from backend.agent.strategy.models import (
    NodeOutput,
    RetrievedChunk,
    StrategyNode,
    StrategyRunState,
)
from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor
from backend.agent.strategy.prompts import build_rerank_prompt

logger = logging.getLogger(__name__)


class RerankExecutor(NodeExecutor):
    """
    Re-score and reorder retrieved chunks using configurable strategies.

    When *llm_helper* is provided, the executor first attempts LLM-based
    relevance scoring (role ``"reranker"``) and falls back to the
    traditional strategies on failure.

    Supported fallback strategies (via ``node.config["strategy"]"):
        * ``"score_based"`` — sort by existing score, apply thresholds.
        * ``"recency_boost"`` — boost newer documents via their metadata date.
        * ``"diversity"`` — penalize chunks from the same document to
          promote source diversity.

    Config keys:
        ``strategy``     (str)   — reranking approach, default ``"score_based"``.
        ``min_score``    (float) — minimum score threshold, default ``0.0``.
        ``max_results``  (int|None) — hard cap on returned chunks.
    """

    def __init__(self, llm_helper: Optional[Any] = None) -> None:
        self.llm_helper = llm_helper

    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        accessor = self.get_state_accessor(state)
        chunks = accessor.get_chunks()
        query = accessor.get_query()

        if not chunks:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=[],
                tokens_used=0,
            )

        # Try LLM reranking first when helper is available
        if self.llm_helper:
            try:
                result = await self._rerank_with_llm(node, accessor, chunks, query)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"LLM reranking failed, falling back: {e}")

        # Fallback: traditional strategy-based reranking
        strategy: str = node.config.get("strategy", "score_based")
        min_score: float = node.config.get("min_score", 0.0)
        max_results: int | None = node.config.get("max_results")

        # Apply reranking strategy
        if strategy == "recency_boost":
            chunks = self._apply_recency_boost(chunks)
        elif strategy == "diversity":
            chunks = self._apply_diversity(chunks)
        else:
            # score_based (default): already sorted by score from retrieval
            chunks.sort(key=lambda c: c.score, reverse=True)

        # Apply score threshold
        if min_score > 0.0:
            chunks = [c for c in chunks if c.score >= min_score]

        # Apply result limit
        if max_results is not None and max_results > 0:
            chunks = chunks[:max_results]

        if not chunks:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=[],
                tokens_used=0,
                error_message=f"All chunks filtered out (min_score={min_score})",
            )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=[c.model_dump() for c in chunks],
            tokens_used=0,
        )

    # ------------------------------------------------------------------
    # LLM-driven reranking
    # ------------------------------------------------------------------

    async def _rerank_with_llm(
        self,
        node: StrategyNode,
        accessor: StateAccessor,
        chunks: list[RetrievedChunk],
        query: str,
    ) -> NodeOutput | None:
        """Attempt LLM-based relevance reranking.

        Returns a ``NodeOutput`` on success, or *None* so the caller
        falls through to the traditional strategy path.
        """
        # Prepare chunk data for the prompt
        chunks_data = [
            {
                "content": c.content,
                "document_title": c.document_title,
                "source_id": c.source_id,
            }
            for c in chunks
        ]

        messages = build_rerank_prompt(query=query, chunks=chunks_data)

        parsed, tokens = await self.llm_helper.complete_json(
            "reranker", messages, node.config,
        )

        if not isinstance(parsed, list):
            logger.warning("LLM rerank response is not a list — falling back")
            return None

        # Build index->score map from LLM response
        score_map: dict[int, float] = {}
        for entry in parsed:
            try:
                idx = int(entry.get("chunk_index", -1))
                score = float(entry.get("relevance_score", 0.0))
                if 0 <= idx < len(chunks):
                    score_map[idx] = score
            except (TypeError, ValueError):
                continue

        if not score_map:
            logger.warning("LLM rerank produced no valid scores — falling back")
            return None

        # Apply LLM scores to chunks (keep original score for unscored)
        for idx, score in score_map.items():
            chunks[idx].score = score

        # Sort by LLM relevance score descending
        chunks.sort(key=lambda c: c.score, reverse=True)

        # Apply filters from node config
        min_score: float = node.config.get("min_score", 0.0)
        max_results: int | None = node.config.get("max_results")

        if min_score > 0.0:
            chunks = [c for c in chunks if c.score >= min_score]

        if max_results is not None and max_results > 0:
            chunks = chunks[:max_results]

        if not chunks:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=[],
                tokens_used=tokens,
                model_used="reranker",
                error_message=f"All chunks filtered out after LLM rerank (min_score={min_score})",
            )

        logger.info(
            f"Node '{node.node_id}': LLM reranking succeeded — "
            f"{len(chunks)} chunk(s) kept, tokens={tokens}"
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=[c.model_dump() for c in chunks],
            tokens_used=tokens,
            model_used="reranker",
        )

    # ------------------------------------------------------------------
    # Reranking strategies
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_recency_boost(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Boost score for newer documents based on metadata date fields."""
        now = datetime.utcnow()

        for chunk in chunks:
            date_str = (
                chunk.metadata.get("date")
                or chunk.metadata.get("created_at")
                or chunk.metadata.get("modified_at")
            )
            if date_str is None:
                continue

            try:
                if isinstance(date_str, datetime):
                    doc_date = date_str
                else:
                    doc_date = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))

                # Age in days — newer documents get a larger boost
                age_days = max((now - doc_date.replace(tzinfo=None)).days, 0)
                # Decay: full boost (0.15) at 0 days, ~0 at 365+ days
                boost = 0.15 * max(1.0 - age_days / 365.0, 0.0)
                chunk.score = chunk.score + boost
            except (ValueError, TypeError):
                pass

        chunks.sort(key=lambda c: c.score, reverse=True)
        return chunks

    @staticmethod
    def _apply_diversity(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Penalize subsequent chunks from the same document."""
        doc_seen: dict[str, int] = {}

        for chunk in chunks:
            doc_id = chunk.document_id
            count = doc_seen.get(doc_id, 0)
            if count > 0:
                # Each repeat from same doc gets a progressively larger penalty
                penalty = 0.05 * count
                chunk.score = max(chunk.score - penalty, 0.0)
            doc_seen[doc_id] = count + 1

        chunks.sort(key=lambda c: c.score, reverse=True)
        return chunks
