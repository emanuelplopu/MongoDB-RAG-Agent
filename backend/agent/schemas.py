"""Data models and schemas for the Federated Agent System."""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
import uuid


# ============== Enums ==============

class DataSourceType(str, Enum):
    """Types of data sources available for search."""
    PROFILE = "profile"  # Shared profile documents
    CLOUD_SHARED = "cloud_shared"  # Shared cloud storage
    CLOUD_PRIVATE = "cloud_private"  # User's private cloud storage
    PERSONAL = "personal"  # User's personal data (emails, etc.)


class AccessType(str, Enum):
    """Access control type for data sources."""
    PROFILE = "profile"  # Access based on profile membership
    SHARED = "shared"  # Access for all authenticated users
    PRIVATE = "private"  # Access only for owner


class TaskType(str, Enum):
    """Types of tasks workers can execute."""
    SEARCH_PROFILE = "search_profile"
    SEARCH_CLOUD = "search_cloud"
    SEARCH_PERSONAL = "search_personal"
    SEARCH_ALL = "search_all"
    WEB_SEARCH = "web_search"
    BROWSE_WEB = "browse_web"
    SUMMARIZE = "summarize"
    REFINE_QUERY = "refine_query"


class ResultQuality(str, Enum):
    """Quality assessment of worker results."""
    EXCELLENT = "excellent"  # Highly relevant, comprehensive
    GOOD = "good"  # Relevant, useful
    PARTIAL = "partial"  # Some relevance, incomplete
    EMPTY = "empty"  # No results found


class OrchestratorPhase(str, Enum):
    """Phases of orchestrator processing."""
    ANALYZE = "analyze"
    PLAN = "plan"
    EVALUATE = "evaluate"
    REFINE = "refine"
    SYNTHESIZE = "synthesize"


class AgentMode(str, Enum):
    """Agent execution modes."""
    AUTO = "auto"  # Adaptive based on query complexity
    THINKING = "thinking"  # Full orchestrator-worker flow
    FAST = "fast"  # Single fast model, no orchestration


# ============== Data Source Models ==============

class DataSource(BaseModel):
    """Represents a searchable data source."""
    id: str = Field(description="Unique identifier for this source")
    type: DataSourceType = Field(description="Type of data source")
    database: str = Field(description="MongoDB database name")
    collection_documents: str = Field(default="documents")
    collection_chunks: str = Field(default="chunks")
    access_type: AccessType = Field(description="Access control type")
    owner_id: Optional[str] = Field(default=None, description="User ID for private sources")
    profile_key: Optional[str] = Field(default=None, description="Profile key for profile sources")
    display_name: str = Field(description="Human-readable name")
    
    class Config:
        use_enum_values = True


# ============== Task Models ==============

class TaskDefinition(BaseModel):
    """A task for workers to execute."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: TaskType = Field(description="Type of task")
    query: str = Field(description="Search query or URL")
    sources: List[str] = Field(default_factory=list, description="Source IDs to search")
    priority: int = Field(default=1, description="Priority (1=highest)")
    depends_on: List[str] = Field(default_factory=list, description="Task IDs to wait for")
    max_results: int = Field(default=10)
    context_hint: Optional[str] = Field(default=None, description="What to look for")
    
    class Config:
        use_enum_values = True


class AgentPlan(BaseModel):
    """Orchestrator's plan for handling the user request."""
    intent_summary: str = Field(description="Brief summary of user intent")
    reasoning: str = Field(description="Orchestrator's reasoning")
    strategy: Literal["parallel", "sequential", "iterative"] = Field(default="parallel")
    tasks: List[TaskDefinition] = Field(default_factory=list)
    success_criteria: str = Field(description="What constitutes a successful answer")
    max_iterations: int = Field(default=3)
    
    def get_ready_tasks(self, completed_ids: set) -> List[TaskDefinition]:
        """Get tasks whose dependencies are satisfied."""
        return [t for t in self.tasks if all(d in completed_ids for d in t.depends_on)]


class EvaluationDecision(BaseModel):
    """Orchestrator's evaluation of worker results."""
    phase: Literal["initial", "refinement", "final"] = "initial"
    findings_summary: str = Field(description="Summary of what was found")
    gaps_identified: List[str] = Field(default_factory=list)
    decision: Literal["sufficient", "need_refinement", "need_expansion", "cannot_answer"]
    follow_up_tasks: List[TaskDefinition] = Field(default_factory=list)
    reasoning: str = Field(description="Why this decision was made")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# ============== Result Models ==============

class DocumentReference(BaseModel):
    """Reference to a found document chunk."""
    id: str = Field(description="Chunk ID")
    document_id: str = Field(description="Parent document ID")
    title: str = Field(description="Document title")
    source_type: DataSourceType = Field(description="Source type")
    source_database: str = Field(description="Database name")
    excerpt: str = Field(description="Relevant excerpt (max 500 chars)")
    full_content: Optional[str] = Field(default=None, description="Full chunk content")
    similarity_score: float = Field(default=0.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        use_enum_values = True


class WebReference(BaseModel):
    """Reference to a web page."""
    url: str
    title: str = ""
    excerpt: str = Field(default="", description="Snippet or description")
    fetched_content: Optional[str] = Field(default=None, description="Full fetched content")
    search_query: str = Field(default="", description="Query that found this")


class WorkerResult(BaseModel):
    """Result from a worker task execution."""
    task_id: str
    task_type: TaskType
    query: str
    success: bool = True
    error: Optional[str] = None
    documents_found: List[DocumentReference] = Field(default_factory=list)
    web_links_found: List[WebReference] = Field(default_factory=list)
    summary: str = Field(default="", description="Worker's summary of findings")
    result_quality: ResultQuality = ResultQuality.EMPTY
    suggested_refinements: List[str] = Field(default_factory=list)
    duration_ms: float = 0.0
    tokens_used: int = 0
    
    class Config:
        use_enum_values = True
    
    @property
    def total_results(self) -> int:
        return len(self.documents_found) + len(self.web_links_found)


# ============== Trace Models ==============

class OrchestratorStep(BaseModel):
    """A step in the orchestrator's reasoning."""
    phase: OrchestratorPhase
    model: str
    timestamp: datetime = Field(default_factory=datetime.now)
    input_summary: str = Field(description="What was input to this phase")
    reasoning: str = Field(description="The model's reasoning")
    output_summary: str = Field(description="What was decided/output")
    tokens_used: int = 0
    duration_ms: float = 0.0
    
    class Config:
        use_enum_values = True


class WorkerStep(BaseModel):
    """A worker's task execution record."""
    task_id: str
    task_type: TaskType
    model: str
    timestamp: datetime = Field(default_factory=datetime.now)
    tool_name: str
    tool_input: Dict[str, Any]
    tool_output_summary: str = ""
    documents: List[DocumentReference] = Field(default_factory=list)
    web_links: List[WebReference] = Field(default_factory=list)
    duration_ms: float = 0.0
    tokens_used: int = 0
    success: bool = True
    error: Optional[str] = None
    
    class Config:
        use_enum_values = True


class AgentTrace(BaseModel):
    """Complete trace of agent operations for transparency."""
    # Configuration
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    orchestrator_model: str
    worker_model: str
    mode: AgentMode
    
    # User context
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    
    # Execution trace
    orchestrator_steps: List[OrchestratorStep] = Field(default_factory=list)
    worker_steps: List[WorkerStep] = Field(default_factory=list)
    iterations: int = 0
    
    # Aggregated sources (deduplicated)
    all_documents: List[DocumentReference] = Field(default_factory=list)
    all_web_links: List[WebReference] = Field(default_factory=list)
    
    # Plan tracking
    initial_plan: Optional[AgentPlan] = None
    evaluation_history: List[EvaluationDecision] = Field(default_factory=list)
    
    # Timing
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    total_duration_ms: float = 0.0
    orchestrator_duration_ms: float = 0.0
    worker_duration_ms: float = 0.0
    
    # Costs
    total_tokens: int = 0
    orchestrator_tokens: int = 0
    worker_tokens: int = 0
    estimated_cost_usd: float = 0.0
    
    class Config:
        use_enum_values = True
    
    def add_orchestrator_step(self, step: OrchestratorStep):
        self.orchestrator_steps.append(step)
        self.orchestrator_tokens += step.tokens_used
        self.orchestrator_duration_ms += step.duration_ms
        self.total_tokens += step.tokens_used
    
    def add_worker_step(self, step: WorkerStep):
        self.worker_steps.append(step)
        self.worker_tokens += step.tokens_used
        self.worker_duration_ms += step.duration_ms
        self.total_tokens += step.tokens_used
        # Add documents and links
        for doc in step.documents:
            if doc.id not in [d.id for d in self.all_documents]:
                self.all_documents.append(doc)
        for link in step.web_links:
            if link.url not in [l.url for l in self.all_web_links]:
                self.all_web_links.append(link)
    
    def finalize(self):
        self.completed_at = datetime.now()
        self.total_duration_ms = (self.completed_at - self.started_at).total_seconds() * 1000
    
    def to_response_dict(self) -> Dict[str, Any]:
        """Convert to a dict suitable for API response."""
        return {
            "id": self.id,
            "mode": self.mode,
            "models": {
                "orchestrator": self.orchestrator_model,
                "worker": self.worker_model,
            },
            "iterations": self.iterations,
            "orchestrator_steps": [
                {
                    "phase": s.phase,
                    "reasoning": s.reasoning,
                    "output": s.output_summary,
                    "duration_ms": s.duration_ms,
                    "tokens": s.tokens_used,
                }
                for s in self.orchestrator_steps
            ],
            "worker_steps": [
                {
                    "task_id": s.task_id,
                    "task_type": s.task_type,
                    "tool": s.tool_name,
                    "input": s.tool_input,
                    "output": s.tool_output_summary,
                    "documents": [
                        {
                            "id": d.id,
                            "title": d.title,
                            "source": d.source_type,
                            "excerpt": d.excerpt,
                            "score": d.similarity_score,
                        }
                        for d in s.documents
                    ],
                    "web_links": [
                        {"url": l.url, "title": l.title}
                        for l in s.web_links
                    ],
                    "duration_ms": s.duration_ms,
                    "success": s.success,
                }
                for s in self.worker_steps
            ],
            "sources": {
                "documents": [
                    {
                        "id": d.id,
                        "document_id": d.document_id,
                        "title": d.title,
                        "source_type": d.source_type,
                        "source_database": d.source_database,
                        "excerpt": d.excerpt,
                        "score": d.similarity_score,
                    }
                    for d in self.all_documents
                ],
                "web_links": [
                    {"url": l.url, "title": l.title, "excerpt": l.excerpt}
                    for l in self.all_web_links
                ],
            },
            "timing": {
                "total_ms": self.total_duration_ms,
                "orchestrator_ms": self.orchestrator_duration_ms,
                "worker_ms": self.worker_duration_ms,
            },
            "tokens": {
                "total": self.total_tokens,
                "orchestrator": self.orchestrator_tokens,
                "worker": self.worker_tokens,
            },
            "cost_usd": self.estimated_cost_usd,
        }


# ============== Configuration Models ==============

class AgentModeConfig(BaseModel):
    """Per-session agent configuration."""
    mode: AgentMode = AgentMode.AUTO
    orchestrator_model: str = "gpt-4o"
    worker_model: str = "gpt-4o-mini"
    max_iterations: int = 3
    parallel_workers: int = 4
    show_full_trace: bool = True
    # Thresholds for auto mode
    auto_thinking_threshold: int = 20  # Query length to trigger thinking mode
    
    class Config:
        use_enum_values = True
    
    def should_use_thinking(self, query: str) -> bool:
        """Determine if thinking mode should be used based on query."""
        if self.mode == AgentMode.THINKING:
            return True
        if self.mode == AgentMode.FAST:
            return False
        # Auto mode: use heuristics
        # Complex queries, multi-step requests, or long queries use thinking
        complexity_indicators = [
            "step by step",
            "analyze",
            "compare",
            "find and then",
            "first",
            "explain",
            "why",
            "how does",
        ]
        query_lower = query.lower()
        has_complexity = any(ind in query_lower for ind in complexity_indicators)
        is_long = len(query) > self.auto_thinking_threshold
        return has_complexity or is_long
