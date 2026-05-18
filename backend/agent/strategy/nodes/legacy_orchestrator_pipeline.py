"""LegacyOrchestratorPipelineExecutor — Wraps FederatedAgent as a single DAG node."""
from __future__ import annotations

import logging
import time
import traceback
from typing import TYPE_CHECKING, Any, Optional

from backend.agent.strategy.models import (
    NodeOutput,
    RetrievedChunk,
    StrategyNode,
    StrategyRunState,
    SynthesisResult,
)
from backend.agent.strategy.nodes.base import NodeExecutor

if TYPE_CHECKING:
    from backend.agent.coordinator import FederatedAgent

logger = logging.getLogger(__name__)


class LegacyOrchestratorPipelineExecutor(NodeExecutor):
    """
    Backward-compatibility node that runs the *entire* existing
    :class:`FederatedAgent` orchestrator-worker pipeline as a single
    strategy DAG node.

    This is the fallback executor for the ``legacy_orchestrator_v1``
    strategy.  The full pipeline (analysis, search, worker dispatch,
    synthesis, evaluation) executes inside one ``execute()`` call.

    Dependency injection:
        ``federated_agent`` must be set externally before execution.
        It expects the ``process()`` coroutine of :class:`FederatedAgent`.
    """

    def __init__(self, federated_agent: Optional[FederatedAgent] = None) -> None:
        self.federated_agent: Optional[FederatedAgent] = federated_agent

    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        accessor = self.get_state_accessor(state)
        query = accessor.get_query()

        if not query:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=None,
                tokens_used=0,
            )

        if self.federated_agent is None:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="error",
                error_message="FederatedAgent not configured",
                tokens_used=0,
            )

        # Pull execution parameters from node config
        user_id: str = node.config.get("user_id", "system")
        user_email: str = node.config.get("user_email", "system@localhost")
        session_id: Optional[str] = node.config.get("session_id")
        active_profile_key: Optional[str] = node.config.get("active_profile_key")
        active_profile_database: Optional[str] = node.config.get("active_profile_database")
        accessible_profile_keys: Optional[list[str]] = node.config.get("accessible_profile_keys")
        language: Optional[str] = node.config.get("language")
        conversation_history: Optional[list[dict[str, Any]]] = node.config.get(
            "conversation_history"
        )

        start_ms = time.time() * 1000

        try:
            response_text, agent_trace = await self.federated_agent.process(
                user_message=query,
                user_id=user_id,
                user_email=user_email,
                session_id=session_id,
                conversation_history=conversation_history,
                active_profile_key=active_profile_key,
                active_profile_database=active_profile_database,
                accessible_profile_keys=accessible_profile_keys,
                language=language,
            )
        except Exception as exc:
            elapsed = time.time() * 1000 - start_ms
            tb = traceback.format_exc()
            logger.error(
                "LegacyOrchestratorPipeline failed for node '%s': %s\n%s",
                node.node_id,
                exc,
                tb,
            )
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="error",
                duration_ms=elapsed,
                error_message=f"FederatedAgent.process() failed: {exc}",
                tokens_used=0,
            )

        elapsed = time.time() * 1000 - start_ms

        # --- extract results from trace -----------------------------------
        synthesis_dict = self._build_synthesis_dict(response_text, agent_trace)
        chunks_list = self._extract_chunks(agent_trace)
        metrics_dict = self._extract_metrics(agent_trace, elapsed)

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            duration_ms=elapsed,
            output_data={
                "synthesis_result": synthesis_dict,
                "retrieved_chunks": chunks_list,
                "metrics": metrics_dict,
            },
            tokens_used=metrics_dict.get("total_tokens", 0),
            model_used="legacy_orchestrator",
        )

    # ------------------------------------------------------------------
    # Result extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_synthesis_dict(response_text: str, trace: Any) -> dict[str, Any]:
        """Build a serialized SynthesisResult dict from agent output."""
        model_used = "legacy_orchestrator"
        token_count = len(response_text.split()) * 2  # rough estimate

        if trace is not None:
            model_used = getattr(trace, "orchestrator_model", model_used) or model_used

        return SynthesisResult(
            text=response_text,
            language="de",
            token_count=token_count,
            model_used=model_used,
        ).model_dump()

    @staticmethod
    def _extract_chunks(trace: Any) -> list[dict[str, Any]]:
        """Pull retrieved chunk data from agent trace search operations."""
        chunks: list[dict[str, Any]] = []
        if trace is None:
            return chunks

        search_ops = getattr(trace, "search_operations", None) or []
        for op in search_ops:
            if not isinstance(op, dict):
                op = op.__dict__ if hasattr(op, "__dict__") else {}
            raw_chunks = op.get("chunks_returned", [])
            for i, c in enumerate(raw_chunks if isinstance(raw_chunks, list) else []):
                if isinstance(c, dict):
                    chunks.append(
                        RetrievedChunk(
                            chunk_id=c.get("chunk_id", f"legacy_chunk_{i}"),
                            document_id=c.get("document_id", ""),
                            document_title=c.get("document_title", ""),
                            source_id=c.get("source_id", c.get("document_id", "")),
                            content=c.get("content", c.get("chunk_preview", "")),
                            score=float(c.get("score", 0.0)),
                            search_type=c.get("search_type", "hybrid"),
                            metadata=c.get("metadata", {}),
                        ).model_dump()
                    )
        return chunks

    @staticmethod
    def _extract_metrics(trace: Any, elapsed_ms: float) -> dict[str, Any]:
        """Pull timing and token metrics from agent trace."""
        metrics: dict[str, Any] = {
            "total_duration_ms": elapsed_ms,
            "total_tokens": 0,
            "llm_calls": 0,
            "search_operations": 0,
        }
        if trace is None:
            return metrics

        # LLM call totals
        llm_calls = getattr(trace, "llm_calls", None) or []
        total_tokens = 0
        for call in llm_calls:
            if isinstance(call, dict):
                total_tokens += call.get("total_tokens", 0)
            elif hasattr(call, "total_tokens"):
                total_tokens += getattr(call, "total_tokens", 0)
        metrics["total_tokens"] = total_tokens
        metrics["llm_calls"] = len(llm_calls)

        search_ops = getattr(trace, "search_operations", None) or []
        metrics["search_operations"] = len(search_ops)

        # Phase metrics
        phase_metrics = getattr(trace, "phase_metrics", None) or []
        if phase_metrics:
            metrics["phases"] = [
                pm if isinstance(pm, dict) else (pm.__dict__ if hasattr(pm, "__dict__") else {})
                for pm in phase_metrics
            ]

        return metrics
