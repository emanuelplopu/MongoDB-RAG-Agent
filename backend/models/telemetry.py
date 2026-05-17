"""Pydantic models for development telemetry records.

These models define the schema for PII-pseudonymized interaction traces
used for data-driven development improvements.
"""

from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
import uuid


class ToolCall(BaseModel):
    """Record of a single tool call during agent execution."""
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    result_summary: str = ""
    latency_ms: int = 0


class SearchHit(BaseModel):
    """Summary of a search result with pseudonymized content."""
    document_id: str
    score: float
    chunk_preview: str = ""  # Pseudonymized snippet


# === Detailed telemetry capture models ===


class LLMCall(BaseModel):
    """Complete record of a single LLM invocation."""
    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    phase: str = ""  # analyze, plan, evaluate, synthesize, summarize, refine
    model: str = ""
    provider: str = ""  # ollama, openai, google, anthropic
    temperature: float = 0.7
    max_tokens: int = 0
    prompt_text: str = ""  # Full prompt sent to LLM
    response_text: str = ""  # Full LLM response
    prompt_tokens: int = 0
    response_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str = ""
    success: bool = True
    error: Optional[str] = None
    is_cold_start: bool = False
    repetition_detected: bool = False


class SearchOperation(BaseModel):
    """Record of a search operation across one or more sources."""
    query: str = ""  # Full query text
    search_type: str = ""  # vector, text, hybrid
    sources_queried: list[str] = Field(default_factory=list)
    results_per_source: dict = Field(default_factory=dict)  # source -> count
    total_results: int = 0
    deduplicated_results: int = 0
    duration_ms: int = 0
    embedding_duration_ms: int = 0
    rrf_applied: bool = False
    top_scores: list[float] = Field(default_factory=list)  # Top N relevance scores
    chunks_returned: list[str] = Field(default_factory=list)  # Full chunk text content


class ToolExecution(BaseModel):
    """Record of a tool/task execution by a worker."""
    task_id: str = ""
    task_type: str = ""  # SEARCH_PROFILE, SEARCH_CLOUD, WEB_SEARCH, SUMMARIZE, REFINE_QUERY, BROWSE_WEB
    input_query: str = ""  # Full input
    output_text: str = ""  # Full output/result
    duration_ms: int = 0
    tokens_used: int = 0
    success: bool = True
    error: Optional[str] = None
    results_count: int = 0
    sources_searched: list[str] = Field(default_factory=list)


class PhaseMetrics(BaseModel):
    """Metrics for a single orchestrator phase."""
    phase: str = ""  # analyze, plan, evaluate, synthesize
    duration_ms: int = 0
    tokens_used: int = 0
    input_size: int = 0  # chars
    output_size: int = 0  # chars
    success: bool = True
    error: Optional[str] = None


class StrategyNodeMetric(BaseModel):
    """Metrics for a single strategy DAG node execution."""
    node_id: str
    node_type: str
    duration_ms: float = 0.0
    model_role: Optional[str] = None
    model_used: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    search_count: int = 0
    sources_in: int = 0
    sources_out: int = 0
    success: bool = True
    error: Optional[str] = None


class TelemetryRecord(BaseModel):
    """Complete telemetry record for a single interaction.

    All text fields are pseudonymized before storage.
    Cross-reference consistency is maintained within a session
    (same entity always gets same alias).
    """

    # Metadata
    record_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    user_id: str  # Pseudonymized user identifier
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tenant: str  # recallhub | quellex
    app_version: str = ""

    # Request
    prompt_original: Optional[str] = None  # Only in dev mode, pseudonymized
    prompt_pseudonymized: str = ""
    prompt_tokens: int = 0
    prompt_language: str = "de"

    # Agent state
    agent_strategy: str = ""
    agent_thinking: str = ""  # Pseudonymized reasoning trace
    tools_called: list[ToolCall] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)  # Pseudonymized
    search_results_summary: list[SearchHit] = Field(default_factory=list)

    # Response
    response_pseudonymized: str = ""
    response_tokens: int = 0
    response_latency_ms: int = 0
    model_used: str = ""

    # Quality signals
    hybrid_search_scores: dict = Field(default_factory=dict)
    sources_cited: list[str] = Field(default_factory=list)
    confidence_score: Optional[float] = None

    # Entity mapping
    pii_entities_detected: int = 0
    entity_types_found: list[str] = Field(default_factory=list)

    # === FULL CAPTURE FIELDS ===
    # LLM calls (every single call with full prompt/response)
    llm_calls: list[LLMCall] = Field(default_factory=list)

    # Search operations (every search with full results)
    search_operations: list[SearchOperation] = Field(default_factory=list)

    # Tool/worker executions (every task with full I/O)
    tool_executions: list[ToolExecution] = Field(default_factory=list)

    # Phase-level orchestrator metrics
    phase_metrics: list[PhaseMetrics] = Field(default_factory=list)

    # Execution context
    agent_mode: str = ""  # thinking, fast, auto
    orchestrator_model: str = ""
    worker_model: str = ""
    orchestrator_provider: str = ""
    worker_provider: str = ""

    # Timing breakdown
    orchestrator_duration_ms: int = 0
    worker_duration_ms: int = 0
    total_duration_ms: int = 0

    # Token breakdown by model
    tokens_per_model: dict = Field(default_factory=dict)

    # Quality
    early_exit_triggered: bool = False
    early_exit_confidence: Optional[float] = None
    total_sources_found: int = 0
    deduplicated_sources: int = 0

    # Strategy OS fields (Phase 0 - all optional for backward compat)
    strategy_id: Optional[str] = None
    strategy_version: Optional[str] = None
    strategy_spec_hash: Optional[str] = None
    strategy_status: Optional[str] = None
    capability_id: Optional[str] = None
    answer_contract_id: Optional[str] = None
    telemetry_schema_version: int = 1  # 1=legacy, 2=strategy_os
    strategy_node_metrics: Optional[list[StrategyNodeMetric]] = None
    experiment_id: Optional[str] = None
    trace_id: Optional[str] = None
