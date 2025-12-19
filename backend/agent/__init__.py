"""Federated Agent System - Orchestrator-Worker Architecture.

This module implements a two-tier agentic system:
- Orchestrator: High-level thinking model (GPT-5.1) for planning and synthesis
- Workers: Fast execution models (Gemini Flash) for parallel tool execution

Key components:
- FederatedSearch: Multi-database search with access control
- Orchestrator: Plans, evaluates, and synthesizes
- WorkerPool: Parallel task execution
- FederatedAgent: Main coordinator
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

from backend.agent.federated_search import FederatedSearch
from backend.agent.orchestrator import Orchestrator
from backend.agent.worker_pool import WorkerPool
from backend.agent.coordinator import FederatedAgent

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
