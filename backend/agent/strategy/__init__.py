"""Strategy OS — config-driven DAG execution models and runtime types.

This package defines the Pydantic v2 data contracts for the Strategy Engine:
run state accumulation, strategy specifications, DAG graph definitions, and
node/policy models used throughout the strategy runner pipeline.
"""

from backend.agent.strategy.models import (
    StrategyRunState,
    StrategySpec,
    StrategyRunResult,
    NodeOutput,
    StrategyGraph,
    StrategyNode,
    BusinessContext,
    ResolvedSourcePolicy,
    SearchRequest,
    OmittedReason,
)
from backend.agent.strategy.legacy_adapter import (
    LegacyStrategyAdapter,
    LEGACY_STRATEGY_ID,
    LEGACY_STRATEGY_VERSION,
)
from backend.agent.strategy.strategy_runner import (
    StrategyRunner,
    StrategyExecutionError,
    StateViolationError,
)
from backend.agent.strategy.graph_compiler import (
    compile_graph,
    CompiledGraph,
    GraphCompilationError,
)
from backend.agent.strategy.condition_evaluator import (
    restricted_eval,
    ConditionEvaluationError,
)
from backend.agent.strategy.nodes import (
    NodeRegistry,
    create_default_registry,
    NODE_TYPE_REGISTRY,
)

__all__ = [
    "StrategyRunState",
    "StrategySpec",
    "StrategyRunResult",
    "NodeOutput",
    "StrategyGraph",
    "StrategyNode",
    "BusinessContext",
    "ResolvedSourcePolicy",
    "SearchRequest",
    "OmittedReason",
    "LegacyStrategyAdapter",
    "LEGACY_STRATEGY_ID",
    "LEGACY_STRATEGY_VERSION",
    "StrategyRunner",
    "StrategyExecutionError",
    "StateViolationError",
    "compile_graph",
    "CompiledGraph",
    "GraphCompilationError",
    "restricted_eval",
    "ConditionEvaluationError",
    "NodeRegistry",
    "create_default_registry",
    "NODE_TYPE_REGISTRY",
]
