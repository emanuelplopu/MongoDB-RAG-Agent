"""Pause condition evaluator for the overnight scheduler."""

from __future__ import annotations

import logging
import time
from typing import Optional

from backend.scheduler.models import (
    PauseConfig,
    PauseDecision,
    PauseReason,
    SchedulerRunState,
    SchedulerRunStatus,
)

logger = logging.getLogger(__name__)


class PauseConditionEvaluator:
    """
    Evaluates whether the scheduler should pause execution.

    Pause triggers:
    1. Interactive users detected (SSE connections or recent chat API calls)
    2. Resource pressure (CPU > 80%, RAM > 85%, GPU > 90%)
    3. Manual pause request

    State transitions:
    - RUNNING → PAUSED (on trigger)
    - PAUSED → RUNNING (trigger clears)
    - PAUSED → DEFERRED (max_pause_minutes exceeded)
    """

    def __init__(self, config: PauseConfig):
        self.config = config
        self._paused_since: Optional[float] = None  # timestamp when pause started
        self._manual_pause: bool = False

    def evaluate(
        self,
        run_state: SchedulerRunState,
        active_sse_connections: int = 0,
        last_chat_activity_ago_seconds: float = float("inf"),
        cpu_pct: float = 0.0,
        ram_pct: float = 0.0,
        gpu_pct: float = 0.0,
    ) -> PauseDecision:
        """Evaluate current conditions and return pause decision."""
        # Check manual pause first
        if self._manual_pause:
            self._mark_paused()
            return PauseDecision(
                should_pause=True,
                reason=PauseReason.MANUAL,
                details="Manual pause requested",
                active_connections=active_sse_connections,
                cpu_pct=cpu_pct,
                ram_pct=ram_pct,
                gpu_pct=gpu_pct,
            )

        # Check interactive users
        if self.config.pause_if_interactive_users:
            cooldown_seconds = self.config.interactive_cooldown_minutes * 60
            if (
                active_sse_connections > 0
                or last_chat_activity_ago_seconds < cooldown_seconds
            ):
                self._mark_paused()
                details_parts = []
                if active_sse_connections > 0:
                    details_parts.append(
                        f"{active_sse_connections} active SSE connections"
                    )
                if last_chat_activity_ago_seconds < cooldown_seconds:
                    details_parts.append(
                        f"Chat activity {last_chat_activity_ago_seconds:.0f}s ago "
                        f"(cooldown: {cooldown_seconds}s)"
                    )
                return PauseDecision(
                    should_pause=True,
                    reason=PauseReason.INTERACTIVE_USERS,
                    details="; ".join(details_parts),
                    active_connections=active_sse_connections,
                    cpu_pct=cpu_pct,
                    ram_pct=ram_pct,
                    gpu_pct=gpu_pct,
                )

        # Check resource pressure
        if self.config.pause_if_resource_pressure:
            pressure_details = []
            if cpu_pct > 80:
                pressure_details.append(f"CPU {cpu_pct:.1f}% > 80%")
            if ram_pct > 85:
                pressure_details.append(f"RAM {ram_pct:.1f}% > 85%")
            if gpu_pct > 90:
                pressure_details.append(f"GPU {gpu_pct:.1f}% > 90%")

            if pressure_details:
                self._mark_paused()
                return PauseDecision(
                    should_pause=True,
                    reason=PauseReason.RESOURCE_PRESSURE,
                    details="; ".join(pressure_details),
                    active_connections=active_sse_connections,
                    cpu_pct=cpu_pct,
                    ram_pct=ram_pct,
                    gpu_pct=gpu_pct,
                )

        # No pause triggers active — clear pause tracking
        if self._paused_since is not None:
            pause_duration = time.time() - self._paused_since
            logger.info(
                "Pause condition cleared after %.1f seconds", pause_duration
            )
            self._paused_since = None

        return PauseDecision(
            should_pause=False,
            reason=None,
            details=None,
            active_connections=active_sse_connections,
            cpu_pct=cpu_pct,
            ram_pct=ram_pct,
            gpu_pct=gpu_pct,
        )

    def request_manual_pause(self) -> None:
        """Manually request a pause."""
        logger.info("Manual pause requested")
        self._manual_pause = True

    def clear_manual_pause(self) -> None:
        """Clear manual pause request."""
        logger.info("Manual pause cleared")
        self._manual_pause = False

    def check_deferred(self, run_state: SchedulerRunState) -> bool:
        """Check if pause has exceeded max_pause_minutes → should transition to DEFERRED."""
        if self._paused_since is None:
            return False

        elapsed_seconds = time.time() - self._paused_since
        max_pause_seconds = self.config.max_pause_minutes * 60

        if elapsed_seconds > max_pause_seconds:
            logger.warning(
                "Pause exceeded max_pause_minutes (%d min, elapsed %.1f min). "
                "Transitioning to DEFERRED.",
                self.config.max_pause_minutes,
                elapsed_seconds / 60,
            )
            return True

        return False

    def reset(self) -> None:
        """Reset evaluator state for a new run."""
        self._paused_since = None
        self._manual_pause = False

    def _mark_paused(self) -> None:
        """Record the start of a pause period if not already paused."""
        if self._paused_since is None:
            self._paused_since = time.time()
            logger.info("Pause started at %.0f", self._paused_since)
