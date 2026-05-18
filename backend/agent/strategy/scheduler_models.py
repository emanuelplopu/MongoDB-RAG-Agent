"""Scheduler-side strategy resource and limits models (Phase 3 / Task 38).

This module defines the :class:`ResourceLimits` Pydantic v2 model that the
overnight exploration scheduler and the strategy experiment runner consult
when deciding whether it is safe to start (or continue) a strategy run.

The shape mirrors blueprint
``docs/quellex_recallhub_strategy_blueprints/05-OVERNIGHT_EXPLORATION_SCHEDULER.md``
section 5. It is intentionally separate from the legacy
:class:`backend.scheduler.models.SchedulerBudget` /
:class:`backend.scheduler.models.PauseConfig` pair so the strategy-engine
code path can evolve its gating policy without disturbing the scheduler-run
state machine that already ships in production.

Repo rule #3: No Motor APIs are introduced in this module. The model is a
pure Pydantic v2 schema with no I/O.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


__all__ = ["ResourceLimits"]


class ResourceLimits(BaseModel):
    """Resource gating limits for the strategy experiment runner.

    All ``*_pct`` ceilings are expressed as integers in ``[0, 100]``. Any
    field set to ``None`` is treated as "no limit" and is skipped during
    :class:`backend.services.resource_snapshot.ResourceSnapshotCollector`'s
    ``is_safe_to_run`` check.

    Attributes:
        max_parallel_runs: Maximum concurrent strategy runs the runner is
            allowed to launch. Defaults to ``1`` for offline/local installs.
        max_runtime_minutes: Hard cap on total runner wall-clock time.
        max_llm_calls: Optional cap on aggregate LLM call count for a run.
        max_cost_eur: Optional Euro budget for a run (LLM + embeddings).
        max_gpu_utilization_pct: Pause threshold for GPU utilization (0-100).
        max_cpu_utilization_pct: Pause threshold for CPU utilization (0-100).
        max_ram_usage_pct: Pause threshold for RAM usage (0-100).
        pause_if_interactive_users: When ``True``, halt experiments while a
            real user appears to be interacting with the system.
        pause_if_backend_chat_active: When ``True``, halt experiments while
            an interactive ``/api/chat`` request was observed recently.
    """

    model_config = ConfigDict(extra="forbid")

    max_parallel_runs: int = Field(default=1, ge=1)
    max_runtime_minutes: int = Field(default=480, ge=1)
    max_llm_calls: Optional[int] = Field(default=None, ge=0)
    max_cost_eur: Optional[float] = Field(default=None, ge=0.0)
    max_gpu_utilization_pct: Optional[int] = Field(default=90, ge=0, le=100)
    max_cpu_utilization_pct: Optional[int] = Field(default=85, ge=0, le=100)
    max_ram_usage_pct: Optional[int] = Field(default=90, ge=0, le=100)
    pause_if_interactive_users: bool = Field(default=True)
    pause_if_backend_chat_active: bool = Field(default=True)
