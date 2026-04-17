"""Federated Agent System - Orchestrator-Worker Architecture.

This package keeps schema imports light-weight and lazily resolves the heavier
runtime components so test modules can import schemas without pulling in every
optional provider dependency at import time.
"""

from backend.agent.schemas import (
    # Data sources
    DataSource,
    DataSourceType,
    AccessType,
    # Tasks
    TaskDefinition,
    TaskType,
    AgentPlan,
    EvaluationDecision,
    # Results
    DocumentReference,
    WebReference,
    WorkerResult,
    ResultQuality,
    # Trace
    OrchestratorStep,
    OrchestratorPhase,
    WorkerStep,
    AgentTrace,
    # Configuration
    AgentModeConfig,
    AgentMode,
)

__all__ = [
    # Schemas
    "DataSource",
    "DataSourceType",
    "AccessType",
    "TaskDefinition",
    "TaskType",
    "AgentPlan",
    "EvaluationDecision",
    "DocumentReference",
    "WebReference",
    "WorkerResult",
    "ResultQuality",
    "OrchestratorStep",
    "OrchestratorPhase",
    "WorkerStep",
    "AgentTrace",
    "AgentModeConfig",
    "AgentMode",
    # Components
    "FederatedSearch",
    "Orchestrator",
    "WorkerPool",
    "FederatedAgent",
]


def __getattr__(name: str):
    """Lazily import heavy runtime components on demand."""
    if name == "FederatedSearch":
        from backend.agent.federated_search import FederatedSearch
        return FederatedSearch
    if name == "Orchestrator":
        from backend.agent.orchestrator import Orchestrator
        return Orchestrator
    if name == "WorkerPool":
        from backend.agent.worker_pool import WorkerPool
        return WorkerPool
    if name == "FederatedAgent":
        from backend.agent.coordinator import FederatedAgent
        return FederatedAgent
    raise AttributeError(f"module 'backend.agent' has no attribute {name!r}")
