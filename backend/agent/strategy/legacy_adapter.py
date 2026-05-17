"""
Legacy Strategy Adapter for Strategy OS Phase 0.

Wraps the existing FederatedAgent orchestrator-worker pipeline as a single-node
StrategySpec, enabling backward-compatible telemetry emission and providing
a bridge from the legacy system to the new DAG-based strategy runner.

This adapter does NOT change any existing behavior. It only adds metadata
and maps existing outputs to Strategy OS data contracts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any, Optional

from backend.agent.strategy.models import (
    NodeOutput,
    RetrievalPolicy,
    RetrievedChunk,
    StrategyBudgets,
    StrategyEdge,
    StrategyGraph,
    StrategyNode,
    StrategyRunState,
    StrategySpec,
    SynthesisResult,
    ValidationPolicy,
)
from backend.models.telemetry import StrategyNodeMetric

logger = logging.getLogger(__name__)

# Fixed strategy ID for the legacy adapter
LEGACY_STRATEGY_ID = "legacy_orchestrator_v1"
LEGACY_STRATEGY_VERSION = "1.0.0"


class LegacyStrategyAdapter:
    """
    Wraps the existing FederatedAgent as a Strategy OS compatible StrategySpec.

    This adapter:
    1. Produces a fixed StrategySpec describing the legacy orchestrator as a single node
    2. After FederatedAgent executes, maps its outputs to StrategyRunState
    3. Generates strategy_node_metrics from phase_metrics for telemetry compatibility
    4. Does NOT modify FederatedAgent behavior in any way
    """

    def __init__(self) -> None:
        self._spec: Optional[StrategySpec] = None
        self._spec_hash: Optional[str] = None

    # ------------------------------------------------------------------
    # Spec construction
    # ------------------------------------------------------------------

    def get_spec(self) -> StrategySpec:
        """
        Return the fixed StrategySpec that describes the legacy orchestrator pipeline.
        Uses a single node: legacy_orchestrator_pipeline.
        """
        if self._spec is None:
            self._spec = StrategySpec(
                strategy_id=LEGACY_STRATEGY_ID,
                version=LEGACY_STRATEGY_VERSION,
                display_name="Legacy Orchestrator Pipeline",
                description=(
                    "Wraps the existing FederatedAgent orchestrator-worker "
                    "pipeline as a single strategy node."
                ),
                tenant_scope="default",
                status="active",
                graph=StrategyGraph(
                    nodes=[
                        StrategyNode(
                            node_id="legacy_orchestrator_pipeline",
                            node_type="legacy_orchestrator_pipeline",
                            config={
                                "max_iterations": 3,
                                "parallel_workers": 4,
                                "timeout_ms": 120000,
                                "early_exit": True,
                                "mode": "auto",
                            },
                            model_role="orchestrator",
                            timeout_ms=300000,  # 5 minutes total
                            optional=False,
                            on_empty="skip",
                            on_error="halt",
                        )
                    ],
                    edges=[],
                    entry_node="legacy_orchestrator_pipeline",
                    terminal_nodes=["legacy_orchestrator_pipeline"],
                ),
                budgets=StrategyBudgets(
                    latency_target_ms=30000,
                    latency_hard_limit_ms=300000,
                    max_context_tokens=128000,
                    max_output_tokens=8000,
                    max_llm_calls=50,
                    local_only=False,
                ),
                retrieval_policy=RetrievalPolicy(
                    search_type="hybrid",
                    top_k=10,
                ),
                validation_policy=ValidationPolicy(
                    answer_contract_check=False,
                    citation_coverage_check=False,
                    cross_matter_leak_check=False,
                    latency_budget_check=False,
                    source_scope_check=False,
                ),
            )
            self._spec_hash = self._compute_spec_hash(self._spec)
            self._spec.spec_hash = self._spec_hash

        return self._spec

    def get_spec_hash(self) -> str:
        """Return the SHA-256 hash of the legacy spec."""
        if self._spec_hash is None:
            self.get_spec()
        assert self._spec_hash is not None
        return self._spec_hash

    # ------------------------------------------------------------------
    # Execution wrapping
    # ------------------------------------------------------------------

    def wrap_execution(
        self,
        query: str,
        response_text: str,
        trace: Any = None,
        phase_metrics: Optional[list[dict]] = None,
        search_operations: Optional[list[dict]] = None,
        llm_calls: Optional[list[dict]] = None,
        total_duration_ms: float = 0.0,
        session_id: Optional[str] = None,
    ) -> StrategyRunState:
        """
        After FederatedAgent completes, wrap its outputs into a StrategyRunState.

        Args:
            query: The original user query.
            response_text: The agent's response text.
            trace: AgentTrace object (if available).
            phase_metrics: List of phase metric dicts from telemetry.
            search_operations: List of search operation dicts from telemetry.
            llm_calls: List of LLM call dicts from telemetry.
            total_duration_ms: Total execution time in milliseconds.
            session_id: Chat session ID.

        Returns:
            StrategyRunState populated with mapped data.
        """
        run_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())

        state = StrategyRunState(
            run_id=run_id,
            trace_id=trace_id,
            query=query,
            elapsed_ms=total_duration_ms,
        )

        # Map search operations to RetrievedChunk[]
        if search_operations:
            for op in search_operations:
                chunks = op.get("chunks_returned", []) if isinstance(op, dict) else []
                for i, chunk in enumerate(chunks if isinstance(chunks, list) else []):
                    if isinstance(chunk, dict):
                        state.retrieved_chunks.append(
                            RetrievedChunk(
                                chunk_id=chunk.get("chunk_id", f"chunk_{i}"),
                                document_id=chunk.get("document_id", ""),
                                document_title=chunk.get("document_title", ""),
                                source_id=chunk.get(
                                    "source_id", chunk.get("document_id", "")
                                ),
                                content=chunk.get(
                                    "content", chunk.get("chunk_preview", "")
                                ),
                                score=chunk.get("score", 0.0),
                                search_type=chunk.get("search_type", "hybrid"),
                                metadata=chunk.get("metadata", {}),
                            )
                        )

        # Map response to SynthesisResult
        state.synthesis_result = SynthesisResult(
            text=response_text,
            language="de",  # Default; could be detected
            token_count=len(response_text.split()) * 2,  # Rough estimate
            model_used="legacy_orchestrator",
        )

        # Map phase metrics to NodeOutput entries
        if phase_metrics:
            for pm in phase_metrics:
                if isinstance(pm, dict):
                    phase_name = pm.get("phase", pm.get("name", "unknown"))
                    node_output = NodeOutput(
                        node_id=f"legacy_{phase_name}",
                        node_type="legacy_orchestrator_pipeline",
                        status="success",
                        duration_ms=pm.get("duration_ms", 0.0),
                        tokens_used=pm.get("tokens_used", 0),
                    )
                    state.node_outputs[node_output.node_id] = node_output

        # Add the overall pipeline as a single node output
        state.node_outputs["legacy_orchestrator_pipeline"] = NodeOutput(
            node_id="legacy_orchestrator_pipeline",
            node_type="legacy_orchestrator_pipeline",
            status="success",
            duration_ms=total_duration_ms,
            tokens_used=sum(
                call.get("total_tokens", 0)
                for call in (llm_calls or [])
                if isinstance(call, dict)
            ),
            model_used="legacy_orchestrator",
        )

        # Store metadata
        state.metadata = {
            "adapter": "legacy",
            "strategy_id": LEGACY_STRATEGY_ID,
            "session_id": session_id,
            "llm_call_count": len(llm_calls) if llm_calls else 0,
            "search_operation_count": len(search_operations) if search_operations else 0,
        }

        return state

    # ------------------------------------------------------------------
    # Telemetry generation
    # ------------------------------------------------------------------

    def generate_node_metrics(
        self,
        state: StrategyRunState,
        phase_metrics: Optional[list[dict]] = None,
        llm_calls: Optional[list[dict]] = None,
        search_operations: Optional[list[dict]] = None,
    ) -> list[StrategyNodeMetric]:
        """
        Generate strategy_node_metrics from legacy execution data.
        Maps phase_metrics to node metrics for backward-compatible telemetry.
        """
        metrics: list[StrategyNodeMetric] = []

        # Map each phase as a node metric
        if phase_metrics:
            for pm in phase_metrics:
                if isinstance(pm, dict):
                    phase_name = pm.get("phase", pm.get("name", "unknown"))
                    metrics.append(
                        StrategyNodeMetric(
                            node_id=f"legacy_{phase_name}",
                            node_type="legacy_orchestrator_pipeline",
                            duration_ms=pm.get("duration_ms", 0.0),
                            model_role=(
                                "orchestrator"
                                if phase_name in ("analyze", "plan", "evaluate")
                                else "worker"
                            ),
                            tokens_in=pm.get("input_size", 0),
                            tokens_out=pm.get("output_size", 0),
                            success=True,
                        )
                    )

        # Add overall pipeline metric
        total_tokens_in = sum(
            c.get("prompt_tokens", 0)
            for c in (llm_calls or [])
            if isinstance(c, dict)
        )
        total_tokens_out = sum(
            c.get("completion_tokens", 0)
            for c in (llm_calls or [])
            if isinstance(c, dict)
        )
        total_search = len(search_operations) if search_operations else 0

        metrics.append(
            StrategyNodeMetric(
                node_id="legacy_orchestrator_pipeline",
                node_type="legacy_orchestrator_pipeline",
                duration_ms=state.elapsed_ms,
                model_role="orchestrator",
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                search_count=total_search,
                sources_in=0,
                sources_out=len(state.retrieved_chunks),
                success=True,
            )
        )

        return metrics

    def get_telemetry_fields(self, state: StrategyRunState, business_context=None) -> dict[str, Any]:
        """
        Return a dict of Strategy OS telemetry fields to be merged into TelemetryRecord.
        These are the new fields added in Phase 0 telemetry extension.

        Args:
            state: The current strategy run state.
            business_context: Optional BusinessContextResult with capability/contract info.
        """
        fields = {
            "strategy_id": LEGACY_STRATEGY_ID,
            "strategy_version": LEGACY_STRATEGY_VERSION,
            "strategy_spec_hash": self.get_spec_hash(),
            "strategy_status": "active",
            "telemetry_schema_version": 1,  # Legacy schema
            "trace_id": state.trace_id,
        }

        # Add business context fields if available
        if business_context is not None:
            fields["capability_id"] = getattr(business_context, "capability_id", None)
            if hasattr(business_context, "answer_contract") and business_context.answer_contract:
                fields["answer_contract_id"] = business_context.answer_contract.format_id

        return fields

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_spec_hash(spec: StrategySpec) -> str:
        """Compute SHA-256 hash of canonical JSON serialization of the spec."""
        spec_dict = spec.model_dump(exclude={"spec_hash", "created_at", "updated_at"})
        canonical = json.dumps(
            spec_dict, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(canonical.encode()).hexdigest()
