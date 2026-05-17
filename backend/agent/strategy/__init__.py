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
]
