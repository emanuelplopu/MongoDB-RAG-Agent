"""RetrieveExecutor — Policy-enforced retrieval via FederatedSearch."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from backend.agent.strategy.models import (
    NodeOutput,
    RetrievedChunk,
    SearchRequest,
    StrategyNode,
    StrategyRunState,
)
from backend.agent.strategy.nodes.base import NodeExecutor

if TYPE_CHECKING:
    from backend.agent.federated_search import FederatedSearch

logger = logging.getLogger(__name__)


class RetrieveExecutor(NodeExecutor):
    """
    Wraps FederatedSearch for policy-enforced retrieval.

    The executor builds a SearchRequest from state and node config, then
    delegates to ``FederatedSearch.search_with_policy()`` when a resolved
    source policy is available.  Without a policy the standard search
    path is used instead.

    Dependency injection:
        ``federated_search`` is an instance attribute that must be set
        externally (e.g. by the StrategyRunner or registry) before the
        first ``execute()`` call.
    """

    def __init__(self, federated_search: Optional[FederatedSearch] = None) -> None:
        self.federated_search: Optional[FederatedSearch] = federated_search

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        accessor = self.get_state_accessor(state)

        # --- validate dependency ------------------------------------------
        if self.federated_search is None:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="error",
                error_message="FederatedSearch not configured",
                tokens_used=0,
            )

        # --- determine query ----------------------------------------------
        query = accessor.get_normalized_query() or accessor.get_query()
        if not query:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=[],
                tokens_used=0,
            )

        # --- resolve config -----------------------------------------------
        top_k: int = node.config.get("top_k", 20)
        search_type: str = node.config.get("search_type", "hybrid")
        min_score: float = node.config.get("min_score", 0.0)
        num_candidates: int = node.config.get("num_candidates", top_k * 20)
        profile_key: Optional[str] = node.config.get("profile_key")

        # Business-context overrides
        business_ctx = accessor.get_business_context()
        resolved_policy = None
        if business_ctx is not None:
            resolved_policy = getattr(business_ctx, "resolved_source_policy", None)
            if profile_key is None:
                profile_key = getattr(business_ctx, "profile_key", None)

        # --- gather queries (query-expansion support) ---------------------
        queries = self._collect_queries(query, state)

        all_valid: list[dict[str, Any]] = []
        all_omitted: list[dict[str, Any]] = []

        for q in queries:
            search_request = SearchRequest(
                query=q,
                resolved_policy=resolved_policy,
                max_results=top_k,
                num_candidates=num_candidates,
                min_score=min_score,
                search_type=search_type,
                profile_key=profile_key,
            )

            if resolved_policy is not None:
                valid_results, omitted_results = (
                    await self.federated_search.search_with_policy(search_request)
                )
            else:
                # Fallback — no policy enforcement
                logger.debug(
                    "No resolved source policy; falling back to standard search "
                    "for node '%s'",
                    node.node_id,
                )
                valid_results, omitted_results = (
                    await self.federated_search.search_with_policy(search_request)
                )

            all_valid.extend(self._results_to_chunks(valid_results))
            all_omitted.extend(self._omitted_to_dicts(omitted_results))

        # Deduplicate by chunk_id
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for chunk in all_valid:
            cid = chunk.get("chunk_id", "")
            if cid not in seen:
                seen.add(cid)
                deduped.append(chunk)

        if not deduped:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=[],
                tokens_used=0,
                error_message="No results after retrieval",
            )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=deduped,
            tokens_used=0,
            model_used=None,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_queries(primary_query: str, state: StrategyRunState) -> list[str]:
        """
        Collect search queries.

        If a previous ``query_expand`` node produced expanded queries they
        are stored in ``state.metadata["query_expand"]``.  We merge them
        with the primary query so every variant is searched.
        """
        queries = [primary_query]
        expanded = state.metadata.get("query_expand")
        if isinstance(expanded, list):
            for eq in expanded:
                q = eq if isinstance(eq, str) else str(eq)
                if q and q != primary_query:
                    queries.append(q)
        return queries

    @staticmethod
    def _results_to_chunks(results: list[Any]) -> list[dict[str, Any]]:
        """Normalize raw search results to serialized RetrievedChunk dicts."""
        chunks: list[dict[str, Any]] = []
        for i, r in enumerate(results):
            if isinstance(r, RetrievedChunk):
                chunks.append(r.model_dump())
            elif isinstance(r, dict):
                chunks.append(
                    RetrievedChunk(
                        chunk_id=r.get("chunk_id", f"chunk_{i}"),
                        document_id=r.get("document_id", ""),
                        document_title=r.get("document_title", ""),
                        source_id=r.get("source_id", r.get("document_id", "")),
                        content=r.get("content", r.get("chunk_preview", "")),
                        score=float(r.get("score", r.get("similarity_score", 0.0))),
                        search_type=r.get("search_type", "hybrid"),
                        metadata=r.get("metadata", {}),
                    ).model_dump()
                )
            else:
                # Best-effort: wrap opaque result objects
                chunks.append(
                    RetrievedChunk(
                        chunk_id=getattr(r, "chunk_id", f"chunk_{i}"),
                        document_id=getattr(r, "document_id", ""),
                        document_title=getattr(r, "document_title", ""),
                        source_id=getattr(r, "source_id", getattr(r, "document_id", "")),
                        content=getattr(r, "content", str(r)),
                        score=float(getattr(r, "score", getattr(r, "similarity_score", 0.0))),
                        search_type=getattr(r, "search_type", "hybrid"),
                        metadata=getattr(r, "metadata", {}),
                    ).model_dump()
                )
        return chunks

    @staticmethod
    def _omitted_to_dicts(omitted: list[Any]) -> list[dict[str, Any]]:
        """Convert omitted result entries to plain dicts for telemetry."""
        result: list[dict[str, Any]] = []
        for entry in omitted:
            if hasattr(entry, "model_dump"):
                result.append(entry.model_dump())
            elif isinstance(entry, dict):
                result.append(entry)
            else:
                result.append({"raw": str(entry)})
        return result
