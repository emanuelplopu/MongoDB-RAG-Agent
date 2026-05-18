"""Strategy OS node executor framework with all registered node types."""
from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor
from backend.agent.strategy.nodes.registry import NodeRegistry, create_default_registry

# Import all node executors for registration
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

# Node type -> Executor class mapping (all 18 types)
NODE_TYPE_REGISTRY = {
    "normalize_query": NormalizeQueryExecutor,
    "intent_classify": IntentClassifyExecutor,
    "query_expand": QueryExpandExecutor,
    "dedupe_versions": DedupeVersionsExecutor,
    "boilerplate_filter": BoilerplateFilterExecutor,
    "plan": PlanExecutor,
    "emit_telemetry": EmitTelemetryExecutor,
    "compare_candidates": CompareCandidatesExecutor,
    "retrieve": RetrieveExecutor,
    "rerank": RerankExecutor,
    "validate_citations": ValidateCitationsExecutor,
    "validate_contract": ValidateContractExecutor,
    "judge_quality": JudgeQualityExecutor,
    "business_context": BusinessContextNodeExecutor,
    "legacy_orchestrator_pipeline": LegacyOrchestratorPipelineExecutor,
    "evidence_cards": EvidenceCardExecutor,
    "synthesize": SynthesizeExecutor,
    "refine": RefineExecutor,
}

__all__ = [
    "NodeExecutor",
    "StateAccessor",
    "NodeRegistry",
    "create_default_registry",
    "NODE_TYPE_REGISTRY",
]
