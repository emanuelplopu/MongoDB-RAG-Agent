"""Node type registry for Strategy OS DAG execution."""
from __future__ import annotations

import logging
import time
from typing import Type

from backend.agent.strategy.models import StrategyNode, StrategyRunState, NodeOutput
from backend.agent.strategy.nodes.base import NodeExecutor

logger = logging.getLogger(__name__)


class NodeRegistryError(Exception):
    """Raised when node registry encounters an error."""
    pass


class NodeRegistry:
    """
    Registry mapping node_type strings to NodeExecutor implementations.

    Usage:
        registry = NodeRegistry()
        registry.register("normalize_query", NormalizeQueryExecutor)
        registry.register("retrieve", RetrieveExecutor)
        ...

        output = await registry.execute(node, state)
    """

    def __init__(self):
        self._executors: dict[str, Type[NodeExecutor]] = {}
        self._executor_kwargs: dict[str, dict] = {}  # constructor kwargs per type
        self._instances: dict[str, NodeExecutor] = {}  # lazy singleton instances

    def register(self, node_type: str, executor_cls: Type[NodeExecutor], **kwargs) -> None:
        """
        Register a node type with its executor class.

        Args:
            node_type: The node type string (e.g., "normalize_query", "retrieve")
            executor_cls: The NodeExecutor subclass to handle this type
            **kwargs: Constructor arguments forwarded when the executor is
                      lazily instantiated (e.g. ``llm_helper=helper``).

        Raises:
            NodeRegistryError: If node_type is already registered
        """
        if node_type in self._executors:
            raise NodeRegistryError(f"Node type '{node_type}' is already registered")
        if not issubclass(executor_cls, NodeExecutor):
            raise NodeRegistryError(
                f"Executor must be a subclass of NodeExecutor, got {executor_cls}"
            )
        self._executors[node_type] = executor_cls
        if kwargs:
            self._executor_kwargs[node_type] = kwargs
        logger.debug(f"Registered node type: {node_type} -> {executor_cls.__name__}")

    def get_executor(self, node_type: str) -> NodeExecutor:
        """
        Get (or create) the executor instance for a node type.

        Returns a singleton instance per node type.
        Raises NodeRegistryError if node_type is not registered.
        """
        if node_type not in self._executors:
            raise NodeRegistryError(
                f"Unknown node type: '{node_type}'. "
                f"Registered types: {sorted(self._executors.keys())}"
            )
        if node_type not in self._instances:
            kwargs = self._executor_kwargs.get(node_type, {})
            self._instances[node_type] = self._executors[node_type](**kwargs)
        return self._instances[node_type]

    @property
    def registered_types(self) -> list[str]:
        """List all registered node type names."""
        return sorted(self._executors.keys())

    @property
    def type_count(self) -> int:
        """Number of registered node types."""
        return len(self._executors)

    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        """
        Dispatch node execution to the appropriate executor.

        Wraps execution with:
        - Timing (started_at_ms, finished_at_ms, duration_ms)
        - Exception handling (returns error NodeOutput on failure)
        - Logging

        Args:
            node: The StrategyNode to execute
            state: Current StrategyRunState

        Returns:
            NodeOutput with timing and status populated
        """
        started_at = time.time()
        started_at_ms = int(started_at * 1000)

        try:
            executor = self.get_executor(node.node_type)
            logger.info(f"Executing node '{node.node_id}' (type={node.node_type})")

            output = await executor.execute(node, state)

            # Ensure timing is set
            finished_at_ms = int(time.time() * 1000)
            output.started_at_ms = started_at_ms
            output.finished_at_ms = finished_at_ms
            output.duration_ms = finished_at_ms - started_at_ms

            logger.info(
                f"Node '{node.node_id}' completed: status={output.status}, "
                f"duration={output.duration_ms}ms"
            )
            return output

        except Exception as e:
            finished_at_ms = int(time.time() * 1000)
            logger.error(f"Node '{node.node_id}' failed with exception: {e}")

            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="error",
                started_at_ms=started_at_ms,
                finished_at_ms=finished_at_ms,
                duration_ms=finished_at_ms - started_at_ms,
                output_data=None,
                error_message=str(e),
                retry_count=0,
                tokens_used=0,
            )


def create_default_registry(llm_helper=None) -> NodeRegistry:
    """
    Create a NodeRegistry with all standard node types registered.

    Args:
        llm_helper: Optional NodeLLMHelper instance.  When provided,
                    LLM-capable nodes will use real LLM calls instead
                    of rule-based / template fallbacks.

    This is called during app startup to wire all available node executors.
    Note: Individual node modules are imported here (lazy) to avoid circular
    imports with the nodes __init__.py which also imports this module.
    """
    # Lazy imports to avoid circular dependency with nodes/__init__.py
    from backend.agent.strategy.nodes.normalize_query import NormalizeQueryExecutor
    from backend.agent.strategy.nodes.intent_classify import IntentClassifyExecutor
    from backend.agent.strategy.nodes.query_expand import QueryExpandExecutor
    from backend.agent.strategy.nodes.dedupe_versions import DedupeVersionsExecutor
    from backend.agent.strategy.nodes.boilerplate_filter import BoilerplateFilterExecutor
    from backend.agent.strategy.nodes.plan_node import PlanExecutor
    from backend.agent.strategy.nodes.emit_telemetry import EmitTelemetryExecutor
    from backend.agent.strategy.nodes.compare_candidates import CompareCandidatesExecutor
    from backend.agent.strategy.nodes.retrieve import RetrieveExecutor
    from backend.agent.strategy.nodes.rerank import RerankExecutor
    from backend.agent.strategy.nodes.validate_citations import ValidateCitationsExecutor
    from backend.agent.strategy.nodes.validate_contract import ValidateContractExecutor
    from backend.agent.strategy.nodes.judge_quality import JudgeQualityExecutor
    from backend.agent.strategy.nodes.business_context_node import BusinessContextNodeExecutor
    from backend.agent.strategy.nodes.legacy_orchestrator_pipeline import LegacyOrchestratorPipelineExecutor
    from backend.agent.strategy.nodes.evidence_cards import EvidenceCardExecutor
    from backend.agent.strategy.nodes.synthesize import SynthesizeExecutor
    from backend.agent.strategy.nodes.refine import RefineExecutor

    registry = NodeRegistry()

    # ── Nodes WITHOUT LLM support (no constructor kwargs needed) ──
    _plain_nodes: dict[str, type[NodeExecutor]] = {
        "normalize_query": NormalizeQueryExecutor,
        "dedupe_versions": DedupeVersionsExecutor,
        "boilerplate_filter": BoilerplateFilterExecutor,
        "emit_telemetry": EmitTelemetryExecutor,
        "validate_citations": ValidateCitationsExecutor,
        "validate_contract": ValidateContractExecutor,
        "compare_candidates": CompareCandidatesExecutor,
        "business_context": BusinessContextNodeExecutor,
        "retrieve": RetrieveExecutor,
        "legacy_orchestrator_pipeline": LegacyOrchestratorPipelineExecutor,
    }
    for node_type, executor_cls in _plain_nodes.items():
        registry.register(node_type, executor_cls)

    # ── Nodes WITH LLM support ──
    # These accept ``llm_helper`` in their constructor.  When *llm_helper*
    # is None the nodes fall back to rule-based / template behaviour.
    _llm_nodes: dict[str, type[NodeExecutor]] = {
        "synthesize": SynthesizeExecutor,
        "evidence_cards": EvidenceCardExecutor,
        "query_expand": QueryExpandExecutor,
        "plan": PlanExecutor,
        "intent_classify": IntentClassifyExecutor,
        "refine": RefineExecutor,
        "rerank": RerankExecutor,
        "judge_quality": JudgeQualityExecutor,
    }
    for node_type, executor_cls in _llm_nodes.items():
        registry.register(node_type, executor_cls, llm_helper=llm_helper)

    logger.info(f"Created default node registry with {registry.type_count} types")
    return registry
