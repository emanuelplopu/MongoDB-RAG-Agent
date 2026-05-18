"""Pydantic models for the overnight exploration scheduler."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

# Source of truth: ``StrategyExperimentJob.mode`` in
# ``backend/agent/strategy/experiment_job_models.py`` (Phase 6 / Task 73).
# Duplicated verbatim here to keep ``backend.scheduler`` foundationally
# decoupled from ``backend.agent.strategy`` (which already imports from
# this module via :mod:`backend.agent.strategy.scheduler_store`).
# Keep the two literal definitions in lock-step on every Phase 6 mode
# change.
StrategyScheduleMode = Literal[
    "regression",
    "exploration",
    "profiling",
    "smoke",
    "grid",
    "bandit",
    "evolutionary",
]


class SchedulerRunStatus(str, Enum):
    """Lifecycle states for a scheduler run."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    DEFERRED = "deferred"  # paused too long, will not retry this run
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OllamaModelStatus(str, Enum):
    """Ollama model residency state."""

    LOADED = "loaded"
    UNLOADED = "unloaded"
    LOADING = "loading"
    UNKNOWN = "unknown"


class PauseReason(str, Enum):
    """Why the scheduler paused."""

    INTERACTIVE_USERS = "interactive_users"
    RESOURCE_PRESSURE = "resource_pressure"
    MANUAL = "manual"


class StopReason(str, Enum):
    """Why the scheduler stopped."""

    MAX_RUNTIME = "max_runtime"
    MAX_LLM_CALLS = "max_llm_calls"
    MAX_COST = "max_cost"
    LEADER_FOUND = "leader_found"
    MAX_CONSECUTIVE_FAILURES = "max_consecutive_failures"
    FATAL_FAILURE_RATE = "fatal_failure_rate"
    ALL_CANDIDATES_COMPLETE = "all_candidates_complete"
    MANUAL_STOP = "manual_stop"


class PauseConfig(BaseModel):
    """Configuration for scheduler pause behavior."""

    max_pause_minutes: int = Field(default=120, description="Max pause before DEFERRED")
    pause_if_interactive_users: bool = Field(default=True)
    pause_if_resource_pressure: bool = Field(default=True)
    interactive_cooldown_minutes: int = Field(
        default=5, description="Wait after last user activity"
    )


class StopConditions(BaseModel):
    """Conditions that halt the scheduler."""

    max_runtime_minutes: int = Field(default=360)
    max_llm_calls: int = Field(default=500)
    max_cost_usd: float = Field(default=50.0)
    max_consecutive_failures: int = Field(default=5)
    stop_on_fatal_failure_rate: float = Field(
        default=0.5, ge=0.0, le=1.0, description=">50% fatal → stop"
    )
    stop_on_leader_found: bool = Field(default=True)
    leader_margin_pct: float = Field(
        default=10.0, description="Leader must beat 2nd by this %"
    )
    min_candidates_completed: int = Field(
        default=3, description="Min before leader detection activates"
    )


class SchedulerBudget(BaseModel):
    """Resource limits for the scheduler."""

    max_cpu_pct: float = Field(default=60.0, ge=0.0, le=100.0)
    max_ram_pct: float = Field(default=50.0, ge=0.0, le=100.0)
    max_gpu_pct: float = Field(default=70.0, ge=0.0, le=100.0)


class StrategySchedule(BaseModel):
    """Configuration for a scheduled overnight exploration run."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(default="Overnight Exploration")
    cron: str = Field(default="0 22 * * 1-5", description="Cron expression (5-field)")
    timezone: str = Field(default="Europe/Vienna", description="IANA timezone")
    allowed_window_start: str = Field(default="22:00", description="HH:MM local time")
    allowed_window_end: str = Field(default="06:00", description="HH:MM local time")

    # Strategy scope
    dataset_id: Optional[str] = Field(
        default=None, description="Test case dataset to evaluate"
    )
    candidate_strategy_ids: list[str] = Field(
        default_factory=list, description="Strategies to compare"
    )

    # Sub-configs
    pause_config: PauseConfig = Field(default_factory=PauseConfig)
    stop_conditions: StopConditions = Field(default_factory=StopConditions)
    budget: SchedulerBudget = Field(default_factory=SchedulerBudget)

    # Status
    status: str = Field(default="active")  # active, paused, disabled
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Runtime state (Phase 5 / Task 61 — persisted by SchedulerStore)
    paused: bool = Field(
        default=False,
        description="Manual or operator pause flag — survives restarts",
    )
    paused_reason: Optional[str] = Field(
        default=None, description="Free-form reason captured when paused=True"
    )
    last_run_at: Optional[datetime] = Field(
        default=None,
        description="Last time a run was triggered for this schedule",
    )
    last_run_status: Optional[str] = Field(
        default=None,
        description="Status of the most recent run (e.g. completed, failed, deferred)",
    )
    next_run_at: Optional[datetime] = Field(
        default=None,
        description="Computed next-run time (UTC). Used by list_due()",
    )
    consecutive_failures: int = Field(
        default=0,
        description="Consecutive failed runs since last success — feeds circuit breaker",
    )
    circuit_breaker_state: Optional[str] = Field(
        default=None,
        description="Persisted circuit breaker state: closed | open | half_open",
    )

    # Phase 6 dispatch metadata (Task 83). All four default to ``None`` so
    # legacy persisted documents (predating Phase 6) round-trip cleanly
    # through :class:`backend.agent.strategy.scheduler_store.MongoSchedulerStore`.
    mode: Optional[StrategyScheduleMode] = Field(
        default=None,
        description=(
            "Experiment mode the scheduler dispatches when this schedule fires. "
            "Mirrors :attr:`backend.agent.strategy.experiment_job_models."
            "StrategyExperimentJob.mode`."
        ),
    )
    tenant: Optional[str] = Field(
        default=None,
        description="Tenant context for the scheduled experiment.",
    )
    profile_key: Optional[str] = Field(
        default=None,
        description=(
            "Tenant profile key (e.g. ``rag_test_law``) for the scheduled "
            "experiment."
        ),
    )
    candidate_generator_id: Optional[str] = Field(
        default=None,
        description=(
            "Identifier of the candidate generator preset to use "
            "(grid/bandit/evolutionary)."
        ),
    )


class OllamaModelState(BaseModel):
    """Tracks Ollama model residency and lock state."""

    model_name: str
    status: OllamaModelStatus = OllamaModelStatus.UNKNOWN
    last_used: Optional[datetime] = None
    lock_holder: Optional[str] = None  # run_id holding the lock
    load_count_10min: int = Field(
        default=0, description="Loads in last 10 minutes (thrashing)"
    )
    keep_alive_until: Optional[datetime] = None


class CandidateResult(BaseModel):
    """Result of evaluating one strategy candidate in a scheduler run."""

    strategy_id: str
    composite_score: float = 0.0
    test_cases_evaluated: int = 0
    duration_ms: float = 0.0
    llm_calls: int = 0
    cost_usd: float = 0.0
    fatal_failures: int = 0
    completed_at: Optional[datetime] = None


class PauseDecision(BaseModel):
    """Result of evaluating pause conditions."""

    should_pause: bool = False
    reason: Optional[PauseReason] = None
    details: Optional[str] = None
    active_connections: int = 0
    cpu_pct: float = 0.0
    ram_pct: float = 0.0
    gpu_pct: float = 0.0


class SchedulerRunState(BaseModel):
    """Current state of a scheduler run."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    schedule_id: str
    status: SchedulerRunStatus = SchedulerRunStatus.PENDING

    # Timing
    started_at: Optional[datetime] = None
    paused_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_pause_minutes: float = 0.0

    # Progress
    candidates_completed: int = 0
    candidates_total: int = 0
    candidate_results: list[CandidateResult] = Field(default_factory=list)
    current_leader: Optional[str] = None  # strategy_id of current best

    # Resource usage
    total_llm_calls: int = 0
    total_cost_usd: float = 0.0
    consecutive_failures: int = 0

    # Stop info
    stop_reason: Optional[StopReason] = None


class NightlyReport(BaseModel):
    """Summary report generated after a scheduler run completes."""

    run_id: str
    schedule_id: str
    status: SchedulerRunStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_minutes: float = 0.0

    # Results
    candidates_evaluated: int = 0
    leader_strategy_id: Optional[str] = None
    leader_score: float = 0.0
    candidate_rankings: list[dict] = Field(
        default_factory=list
    )  # [{strategy_id, score, rank}]

    # Resource summary
    total_llm_calls: int = 0
    total_cost_usd: float = 0.0
    pause_count: int = 0
    total_pause_minutes: float = 0.0

    # Issues
    stop_reason: Optional[StopReason] = None
    warnings: list[str] = Field(default_factory=list)
