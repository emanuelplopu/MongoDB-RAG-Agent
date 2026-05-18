"""Pydantic v2 models for the Strategy OS DAG execution engine.

Defines the full type hierarchy for strategy specifications, run-state
accumulation, execution results, and supporting policy / contract models.

Reference: docs/08-STRATEGY_DAG_AND_NODES.md
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

__all__ = [
    # Core execution state
    "CitationRef",
    "ValidationIssue",
    "RetrievedChunk",
    "EvidenceCard",
    "SynthesisResult",
    "ValidationResult",
    "BusinessContextResult",
    "NodeOutput",
    "StrategyRunState",
    # Strategy spec — budgets & policies
    "StrategyBudgets",
    "SourcePolicy",
    "RetrievalPolicy",
    "EvidencePolicy",
    "ValidationPolicy",
    # Graph structure
    "StrategyEdge",
    "StrategyNode",
    "StrategyGraph",
    # Answer contract
    "ColumnDef",
    "OutputSection",
    "TableSchema",
    "AnswerContract",
    # Business capability
    "BusinessCapability",
    # Phase 1 — source enforcement & context
    "OmittedReason",
    "OmittedResultEntry",
    "ResolvedSourcePolicy",
    "SearchRequest",
    "AmbiguityResolution",
    "LanguagePolicy",
    "UserFacingMessage",
    "BusinessContext",
    # Top-level spec & result
    "StrategySpec",
    "StrategyRunResult",
]


# ═══════════════════════════════════════════════════════════════════════════════
# Core Execution State Models
# ═══════════════════════════════════════════════════════════════════════════════


class CitationRef(BaseModel):
    """A single citation reference within a synthesis output."""

    source_id: str
    document_title: str
    span_start: Optional[int] = None
    span_end: Optional[int] = None
    quote: Optional[str] = None
    confidence: float = 1.0


class ValidationIssue(BaseModel):
    """A single issue found by a validator node."""

    issue_type: str  # e.g. "unsupported_claim", "format_violation"
    severity: Literal["warning", "error", "fatal"]
    message: str
    field: Optional[str] = None


class RetrievedChunk(BaseModel):
    """A single chunk returned by a retrieval node."""

    chunk_id: str
    document_id: str
    document_title: str
    source_id: str
    content: str
    score: float
    search_type: Literal["semantic", "text", "hybrid"]
    span_start: Optional[int] = None
    span_end: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceCard(BaseModel):
    """Compressed evidence unit produced by an evidence_cards node."""

    card_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    card_type: Literal[
        "fact",
        "legal_question",
        "contradiction",
        "business_insight",
        "technical_requirement",
    ]
    topic: str
    source_id: str
    document_title: str
    source_excerpt: str
    factual_basis: str
    confidence: float
    span_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SynthesisResult(BaseModel):
    """Output of a synthesize / refine / compare_candidates node."""

    text: str
    citations: list[CitationRef] = Field(default_factory=list)
    language: str = "de"
    format_id: Optional[str] = None
    token_count: int = 0
    model_used: str = ""
    confidence: Optional[float] = None


class ValidationResult(BaseModel):
    """Output of a validation node (contract, citation, quality)."""

    validator_id: str
    passed: bool
    score: Optional[float] = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BusinessContextResult(BaseModel):
    """Output of the business_context node."""

    capability_id: Optional[str] = None
    confidence: float = 0.0
    profile_key: Optional[str] = None
    matter_id: Optional[str] = None
    source_ids: list[str] = Field(default_factory=list)
    answer_format_id: Optional[str] = None
    language: str = "de"
    tone: Optional[str] = None
    ambiguity_detected: bool = False
    alternative_interpretations: list[str] = Field(default_factory=list)


class NodeOutput(BaseModel):
    """Wrapper for the output of any single node execution."""

    node_id: str
    node_type: str
    status: Literal["success", "skipped", "empty", "error", "timed_out"] = "success"
    started_at_ms: float = 0.0
    finished_at_ms: float = 0.0
    duration_ms: float = 0.0
    output_data: Any = None
    error_message: Optional[str] = None
    retry_count: int = 0
    model_used: Optional[str] = None
    tokens_used: int = 0
    timed_out: bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# Strategy Run State (the DAG accumulator)
# ═══════════════════════════════════════════════════════════════════════════════


class StrategyRunState(BaseModel):
    """Accumulator for an entire strategy DAG execution.

    Created once per run. Nodes read from state via typed accessors
    and produce NodeOutput objects. The runner merges each NodeOutput
    into the state after the node completes.
    """

    # ── Identity ──
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # ── Input ──
    query: str = ""
    normalized_query: Optional[str] = None

    # ── Accumulated outputs ──
    business_context: Optional[BusinessContextResult] = None
    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)
    evidence_cards: list[EvidenceCard] = Field(default_factory=list)
    synthesis_result: Optional[SynthesisResult] = None
    validation_results: list[ValidationResult] = Field(default_factory=list)
    node_outputs: dict[str, NodeOutput] = Field(default_factory=dict)

    # ── Metadata ──
    metadata: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: float = 0.0
    cancelled: bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# Strategy Spec — Budgets & Policies
# ═══════════════════════════════════════════════════════════════════════════════


class StrategyBudgets(BaseModel):
    """Resource budgets for a single strategy run."""

    latency_target_ms: int = 8000
    latency_hard_limit_ms: int = 45000
    max_context_tokens: int = 32000
    max_output_tokens: int = 4000
    max_llm_calls: int = 10
    max_cost_usd: Optional[float] = None
    local_only: bool = False


class SourcePolicy(BaseModel):
    """Controls which data sources a strategy may access."""

    allow_cross_profile: bool = False
    allow_cross_matter: bool = False
    allow_web: bool = False
    allow_personal: bool = True
    allowed_profile_ids: Optional[list[str]] = None
    allowed_matter_ids: Optional[list[str]] = None
    excluded_matter_ids: list[str] = Field(default_factory=list)
    require_source_spans: bool = False
    # Phase 1 fields
    allow_cloud_private: bool = True
    web_requires_sanitization: bool = True
    prefer_latest_versions: bool = True
    exclude_boilerplate: bool = False
    primary_sources: list[str] = Field(default_factory=lambda: ["documents"])
    secondary_sources: list[str] = Field(default_factory=list)
    excluded_sources: list[str] = Field(default_factory=list)


class RetrievalPolicy(BaseModel):
    """Controls retrieval behavior for a strategy."""

    search_type: Literal["semantic", "text", "hybrid"] = "hybrid"
    top_k: int = 10
    query_variants: int = 1
    reranker: Optional[Literal["llm", "cross_encoder", "none"]] = None
    min_score_threshold: float = 0.0
    rrf_constant: int = 60
    entity_boosting: bool = False
    document_family_boosting: bool = False


class EvidencePolicy(BaseModel):
    """Controls evidence card generation behavior."""

    enabled: bool = True
    card_types: list[str] = Field(default_factory=lambda: ["fact"])
    max_cards: int = 20
    min_confidence: float = 0.5
    preserve_quotes_max_words: int = 25


class ValidationPolicy(BaseModel):
    """Controls which validation checks are active."""

    answer_contract_check: bool = True
    citation_coverage_check: bool = True
    unsupported_claim_check: bool = False
    cross_matter_leak_check: bool = True
    latency_budget_check: bool = True
    source_scope_check: bool = True
    allow_refine_on_failure: bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# Graph Structure
# ═══════════════════════════════════════════════════════════════════════════════


class StrategyEdge(BaseModel):
    """A directed edge in the strategy DAG."""

    from_node: str
    to_node: str
    condition: Optional[str] = None  # Python expression evaluated against state


class StrategyNode(BaseModel):
    """A single execution node in the strategy DAG."""

    node_id: str
    node_type: str  # e.g. "retrieve", "evidence_cards", "synthesize"
    config: dict[str, Any] = Field(default_factory=dict)
    model_role: Optional[str] = None
    timeout_ms: Optional[int] = None
    optional: bool = False
    on_empty: Literal["skip", "error"] = "skip"
    on_error: Literal["skip", "halt", "retry"] = "halt"
    retry_config: Optional[dict[str, Any]] = None


class StrategyGraph(BaseModel):
    """The full DAG definition for a strategy."""

    nodes: list[StrategyNode]
    edges: list[StrategyEdge]
    entry_node: str
    terminal_nodes: list[str]


# ═══════════════════════════════════════════════════════════════════════════════
# Answer Contract
# ═══════════════════════════════════════════════════════════════════════════════


class OutputSection(BaseModel):
    """A section in the answer contract output definition."""

    section_id: str
    title: str
    required: bool = True
    format: Literal["prose", "list", "table", "structured"] = "prose"
    description: str = ""


class ColumnDef(BaseModel):
    """Enhanced column definition for table schemas."""

    name: str
    display_name: str = ""
    column_type: Literal["text", "number", "boolean", "date", "enum", "list"] = "text"
    required: bool = True
    description: str = ""
    enum_values: Optional[list[str]] = None
    max_length: Optional[int] = None


class TableSchema(BaseModel):
    """Schema for table-formatted output sections."""

    columns: list[ColumnDef]
    row_source: str = ""
    sort_by: Optional[str] = None
    min_rows: Optional[int] = None
    max_rows: Optional[int] = None


class AnswerContract(BaseModel):
    """Defines the expected structure and quality of a strategy's output."""

    format_id: str
    version: int = 1
    language: str = "de"
    output_sections: list[OutputSection] = Field(default_factory=list)
    table_schema: Optional[TableSchema] = None
    tone: str = "professional"
    citation_granularity: Literal["document", "chunk", "span"] = "chunk"
    unsupported_claim_policy: Literal["flag", "omit", "allow"] = "flag"
    required_fields: list[str] = Field(default_factory=list)
    max_length_tokens: Optional[int] = None


# ═══════════════════════════════════════════════════════════════════════════════
# Business Capability
# ═══════════════════════════════════════════════════════════════════════════════


class BusinessCapability(BaseModel):
    """A domain-level capability that maps to one or more strategies."""

    capability_id: str
    display_name: str
    description: str = ""
    tenant_scope: str = "default"
    default_strategy_id: Optional[str] = None
    default_answer_contract_id: Optional[str] = None
    default_source_policy: Optional[SourcePolicy] = None
    detection_keywords: list[str] = Field(default_factory=list)
    detection_patterns: list[str] = Field(default_factory=list)
    scoring_profile: Optional[dict[str, float]] = None


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1 — Source Enforcement & Context Models
# ═══════════════════════════════════════════════════════════════════════════════


class OmittedReason(str, Enum):
    """Reasons why a search result was omitted from processing."""

    BOILERPLATE_FILTERED = "boilerplate_filtered"
    FORBIDDEN_SOURCE = "forbidden_source"
    CROSS_MATTER_BLOCKED = "cross_matter_blocked"
    CROSS_PROFILE_BLOCKED = "cross_profile_blocked"
    WEB_BLOCKED = "web_blocked"
    LOW_RELEVANCE_SCORE = "low_relevance_score"
    DUPLICATE_VERSION = "duplicate_version"
    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"
    PERSONAL_DATA_BLOCKED = "personal_data_blocked"


class OmittedResultEntry(BaseModel):
    """Tracks a single omitted search result with reason."""

    source_id: str
    document_title: Optional[str] = None
    reason: OmittedReason
    severity: Literal["info", "warning", "error"] = "info"
    score: Optional[float] = None


class ResolvedSourcePolicy(BaseModel):
    """Concrete result of source policy intersection across all layers."""

    allow_cross_profile: bool = False
    allow_cross_matter: bool = False
    allow_web: bool = False
    allow_personal: bool = False
    allow_cloud_private: bool = False
    web_requires_sanitization: bool = True
    require_source_spans: bool = False
    prefer_latest_versions: bool = True
    exclude_boilerplate: bool = False
    allowed_profile_ids: list[str] = Field(default_factory=list)
    allowed_matter_ids: list[str] = Field(default_factory=list)
    excluded_matter_ids: list[str] = Field(default_factory=list)
    primary_sources: list[str] = Field(default_factory=lambda: ["documents"])
    secondary_sources: list[str] = Field(default_factory=list)
    excluded_sources: list[str] = Field(default_factory=list)
    resolved_from: list[str] = Field(default_factory=list)  # layers that contributed


class SearchRequest(BaseModel):
    """Request to FederatedSearch with source policy enforcement."""

    query: str
    query_embedding: Optional[list[float]] = None
    resolved_policy: Optional[ResolvedSourcePolicy] = None
    max_results: int = 10
    num_candidates: int = 200
    min_score: float = 0.0
    search_type: Literal["semantic", "text", "hybrid"] = "hybrid"
    profile_key: Optional[str] = None
    matter_id: Optional[str] = None


class AmbiguityResolution(BaseModel):
    """Result of capability detection ambiguity analysis."""

    ambiguity_detected: bool = False
    confidence: float = 0.0
    interpreted_as: Optional[str] = None
    alternative_interpretations: list[str] = Field(default_factory=list)
    clarification_needed: bool = False
    clarification_prompt: Optional[str] = None


class LanguagePolicy(BaseModel):
    """Tenant/profile language configuration."""

    primary_languages: list[str] = Field(default_factory=lambda: ["de"])
    supported_languages: list[str] = Field(default_factory=lambda: ["de", "en"])
    fallback_language: str = "en"
    detect_query_language: bool = True
    enforce_response_language: bool = True
    max_language_retry: int = 1
    terminology_preservation: dict[str, list[str]] = Field(default_factory=dict)


class UserFacingMessage(BaseModel):
    """Localized user-facing message for errors, warnings, and info."""

    level: Literal["success", "info", "warning", "error", "fatal"] = "info"
    title: str = ""
    message: str = ""
    reason_code: str = ""
    suggestion: Optional[str] = None
    language: str = "de"


class BusinessContext(BaseModel):
    """Aggregate context envelope resolved before strategy execution."""

    capability_id: Optional[str] = None
    capability_confidence: float = 0.0
    resolved_source_policy: Optional[ResolvedSourcePolicy] = None
    answer_contract: Optional[AnswerContract] = None
    ambiguity: Optional[AmbiguityResolution] = None
    language_policy: Optional[LanguagePolicy] = None
    profile_key: Optional[str] = None
    matter_id: Optional[str] = None
    tenant_id: str = "default"
    user_facing_messages: list[UserFacingMessage] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# Top-Level Strategy Spec & Run Result
# ═══════════════════════════════════════════════════════════════════════════════


class StrategySpec(BaseModel):
    """Complete specification for a single strategy — the unit of deployment."""

    strategy_id: str
    version: str = "1.0.0"
    display_name: str = ""
    description: str = ""
    tenant_scope: str = "default"
    capability_id: Optional[str] = None
    status: Literal["draft", "active", "candidate", "deprecated", "archived"] = "draft"

    # ── Execution ──
    graph: StrategyGraph
    budgets: StrategyBudgets = Field(default_factory=StrategyBudgets)
    model_roles: dict[str, str] = Field(default_factory=dict)  # role_id -> model override

    # ── Policies ──
    source_policy: Optional[SourcePolicy] = None
    retrieval_policy: RetrievalPolicy = Field(default_factory=RetrievalPolicy)
    evidence_policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    validation_policy: ValidationPolicy = Field(default_factory=ValidationPolicy)

    # ── Answer ──
    answer_contract: Optional[AnswerContract] = None

    # ── Metadata ──
    spec_hash: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class StrategyRunResult(BaseModel):
    """Final result returned by the strategy runner after DAG execution."""

    success: bool
    state: StrategyRunState
    strategy_id: str
    strategy_spec_hash: Optional[str] = None
    halt_reason: Optional[str] = None
    total_duration_ms: float = 0.0
    total_tokens: int = 0
    total_llm_calls: int = 0
    nodes_executed: int = 0
    nodes_skipped: int = 0
