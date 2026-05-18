"""Strategy experiment-job data models (Phase 6 / Task 73).

Defines the durable lifecycle record for one "schedule fired" event in
the Strategy Experiment system. Each :class:`StrategyExperimentJob`
represents a scheduled or on-demand experiment run that may execute many
strategies over many datasets and produce per-run trace documents
(tracked via :attr:`StrategyExperimentJob.trace_ids`, which reference
:attr:`backend.agent.strategy.run_trace_store.RunTraceDoc.trace_id`).

The model intentionally mirrors Blueprint 05 §8 ("Experiment job
lifecycle"). The state machine is encoded once on
:meth:`StrategyExperimentJob.allowed_transitions` so the persistence
layer can enforce legal status changes consistently across stores.

Repo rule #3: no Motor APIs introduced; this module only defines
Pydantic v2 schemas.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.agent.strategy.scheduler_models import ResourceLimits

__all__ = [
    "JobStatus",
    "JobProgress",
    "StrategyExperimentJob",
]


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    """Return a freshly minted UUID4 string."""
    return str(uuid.uuid4())


class JobStatus(str, Enum):
    """Lifecycle status for a :class:`StrategyExperimentJob`.

    The state machine (see :meth:`StrategyExperimentJob.allowed_transitions`)
    is::

        SCHEDULED -> {QUEUED, CANCELLED}
        QUEUED    -> {RUNNING, CANCELLED, PAUSED}
        RUNNING   -> {COMPLETED, FAILED, CANCELLED, PAUSED}
        PAUSED    -> {QUEUED, CANCELLED}
        COMPLETED, FAILED, CANCELLED  -> {}  (terminal)
    """

    SCHEDULED = "scheduled"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


#: Set of terminal statuses with no outgoing transitions.
TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)


class JobProgress(BaseModel):
    """Granular progress counters for an in-flight experiment job.

    The runner (P5) increments these fields as it walks the
    strategies × datasets matrix so operators and the UI can show a
    live progress bar without parsing trace documents.

    Attributes:
        total_runs: Total number of (strategy, dataset, case) tuples
            scheduled by the runner. ``0`` until the runner has
            expanded the matrix.
        completed_runs: Number of runs that finished successfully.
        failed_runs: Number of runs that errored or timed out.
        current_strategy: Strategy id currently executing (or last
            executed when paused). ``None`` before the first run.
        current_dataset: Dataset id currently being processed. ``None``
            before the first run.
        current_case_index: Zero-based index of the case within the
            current dataset.
        last_progress_at: UTC timestamp of the most recent progress
            update. ``None`` until the first increment.
    """

    model_config = ConfigDict(extra="forbid")

    total_runs: int = Field(default=0, ge=0)
    completed_runs: int = Field(default=0, ge=0)
    failed_runs: int = Field(default=0, ge=0)
    current_strategy: Optional[str] = None
    current_dataset: Optional[str] = None
    current_case_index: int = Field(default=0, ge=0)
    last_progress_at: Optional[datetime] = None


class StrategyExperimentJob(BaseModel):
    """Durable lifecycle record for one experiment-runner invocation.

    Mirrors Blueprint 05 §8 exactly. One job is created per "schedule
    fired" event (or per on-demand operator request) and aggregates the
    set of per-run :class:`backend.agent.strategy.run_trace_store.RunTraceDoc`
    identifiers it produced via :attr:`trace_ids`.

    The model is persisted via
    :class:`backend.agent.strategy.experiment_job_store.ExperimentJobStore`.
    The runner is responsible for transitioning ``status`` through the
    legal state machine and for populating :attr:`result_summary` on
    completion.
    """

    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    id: str = Field(default_factory=_new_uuid, description="Job UUID4 identifier.")
    schedule_id: Optional[str] = Field(
        default=None,
        description=(
            "Reference to the :class:`backend.scheduler.models.StrategySchedule` "
            "that triggered this job. ``None`` for ad-hoc / manual runs."
        ),
    )
    experiment_id: str = Field(
        default_factory=_new_uuid,
        description=(
            "Logical experiment identifier. Multiple jobs (e.g. nightly "
            "re-runs of the same experiment) may share an "
            "``experiment_id`` while having distinct ``id`` values."
        ),
    )
    status: JobStatus = Field(
        default=JobStatus.SCHEDULED,
        description="Current lifecycle status. See :class:`JobStatus`.",
    )
    priority: int = Field(
        default=0,
        description=(
            "Dispatch priority. Higher values run first when multiple "
            "jobs are queued; ties broken by ``created_at``."
        ),
    )
    tenant: str = Field(description="Tenant scope (e.g. ``recallhub``, ``quellex``).")
    profile_key: Optional[str] = Field(
        default=None,
        description="Optional ``profiles.yaml`` profile key for tenant-aware model selection.",
    )
    mode: Literal[
        "regression",
        "exploration",
        "profiling",
        "smoke",
        "grid",
        "bandit",
        "evolutionary",
    ] = Field(
        description=(
            "Job mode controlling runner behaviour: ``regression`` (re-run "
            "promoted specs), ``exploration`` / ``grid`` (try candidate "
            "generators), ``profiling`` (collect latency/cost telemetry), "
            "``smoke`` for single-strategy debugging, ``bandit`` for "
            "UCB1-based dynamic arm allocation, or ``evolutionary`` for "
            "population-based mutation across successive generations. The "
            "P6 :class:`ModeRegistry` dispatches every value listed here."
        ),
    )
    datasets: list[str] = Field(
        default_factory=list,
        description="Dataset identifiers to evaluate this job against.",
    )
    strategy_ids: list[str] = Field(
        default_factory=list,
        description="Strategy spec identifiers to execute as part of this job.",
    )
    candidate_generator_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional candidate-generator id. Required for ``exploration`` "
            "mode; ignored otherwise."
        ),
    )
    scoring_profile: Optional[str] = Field(
        default=None,
        description="Optional scoring-profile id used to rank strategy outputs.",
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when the job transitioned to ``RUNNING``.",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when the job reached a terminal status.",
    )
    progress: JobProgress = Field(default_factory=JobProgress)
    error: Optional[str] = Field(
        default=None,
        description=(
            "Terminal error string set when ``status`` is ``FAILED``. "
            "Cleared on legal transitions out of ``FAILED`` (currently none)."
        ),
    )
    trace_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Trace identifiers produced by this job. Each entry references "
            ":attr:`backend.agent.strategy.run_trace_store.RunTraceDoc.trace_id`."
        ),
    )
    result_summary: Optional[dict] = Field(
        default=None,
        description=(
            "Aggregate scores, latency p50/p95, and the top winner. "
            "Populated by :meth:`ExperimentJobStore.finalize` on completion."
        ),
    )
    resource_limits: Optional[ResourceLimits] = Field(
        default=None,
        description=(
            "Optional per-job resource gating limits consulted by the "
            ":class:`StrategyExperimentRunner` before each candidate. "
            "When ``None`` the runner falls back to the legacy "
            "``result_summary['resource_limits']`` shim and finally to "
            ":class:`ResourceLimits` defaults."
        ),
    )
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    # ── State machine ──────────────────────────────────────────────────

    @classmethod
    def allowed_transitions(cls, from_status: JobStatus) -> set[JobStatus]:
        """Return the legal next statuses for ``from_status``.

        Args:
            from_status: The current job status.

        Returns:
            The set of statuses to which a job currently in
            ``from_status`` may transition. Empty for terminal
            statuses (``COMPLETED``, ``FAILED``, ``CANCELLED``).
        """
        # Normalise raw string values (Mongo round-trips often hand
        # back the underlying ``str`` form) into the enum so callers
        # can pass either shape.
        if not isinstance(from_status, JobStatus):
            from_status = JobStatus(from_status)
        if from_status is JobStatus.SCHEDULED:
            return {JobStatus.QUEUED, JobStatus.CANCELLED}
        if from_status is JobStatus.QUEUED:
            return {JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.PAUSED}
        if from_status is JobStatus.RUNNING:
            return {
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
                JobStatus.PAUSED,
            }
        if from_status is JobStatus.PAUSED:
            return {JobStatus.QUEUED, JobStatus.CANCELLED}
        # COMPLETED / FAILED / CANCELLED are terminal.
        return set()

    @classmethod
    def is_terminal(cls, status: JobStatus) -> bool:
        """Return ``True`` when ``status`` is a terminal lifecycle state."""
        if not isinstance(status, JobStatus):
            status = JobStatus(status)
        return status in TERMINAL_STATUSES
