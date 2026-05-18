"""Overnight exploration scheduler daemon — main execution loop."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from backend.scheduler.circuit_breaker import CircuitState, MongoDBCircuitBreaker
from backend.scheduler.contention_manager import ResourceContentionManager
from backend.scheduler.models import (
    CandidateResult,
    NightlyReport,
    SchedulerRunState,
    SchedulerRunStatus,
    StopReason,
    StrategySchedule,
)
from backend.scheduler.ollama_residency import OllamaResidencyManager
from backend.scheduler.pause_evaluator import PauseConditionEvaluator
from backend.scheduler.stop_evaluator import StopConditionEvaluator
from backend.scheduler.timezone_resolver import ScheduleTimezoneResolver

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backend.agent.strategy.scheduler_store import SchedulerStore

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 60


class SchedulePausedError(RuntimeError):
    """Raised when a run is requested for a schedule that is paused."""


class CircuitBreakerOpenError(RuntimeError):
    """Raised when a run is requested while the circuit breaker is OPEN."""


class ScheduleNotFoundError(LookupError):
    """Raised when a schedule id cannot be located in the store."""


class SchedulerDaemon:
    """Main overnight exploration scheduler.

    Lifecycle:
        1. Every tick: load due schedules from the store.
        2. If a schedule triggers: start an exploration run.
        3. During the run: iterate candidates, check pause/stop conditions.
        4. After the run: persist runtime state and emit a nightly report.

    The daemon is wired against an injected :class:`SchedulerStore`. When
    one is not provided, a :class:`MongoSchedulerStore` is created from
    the supplied database handle, falling back to an in-memory store when
    the database is also absent. Supports graceful shutdown via
    SIGTERM/SIGINT.

    Args:
        db: Optional async Mongo database handle. Used to construct a
            :class:`MongoSchedulerStore` when ``store`` is not provided.
        ollama_base_url: Base URL for the local Ollama server.
        store: Optional pre-built :class:`SchedulerStore` to use. Takes
            precedence over ``db`` when supplied (recommended for tests).
        tick_interval_seconds: Override the main-loop tick interval.
    """

    def __init__(
        self,
        db: Any = None,
        ollama_base_url: str = "http://localhost:11434",
        store: Optional["SchedulerStore"] = None,
        tick_interval_seconds: int = TICK_INTERVAL_SECONDS,
        *,
        nightly_report_enabled: bool = True,
        nightly_report_hour_local: int = 3,
        nightly_report_timezone: str = "UTC",
        nightly_report_dir: Optional[str] = None,
        nightly_report_regression_threshold: float = 0.05,
        nightly_report_lookback_hours: int = 24,
        timezone_resolver: Optional[ScheduleTimezoneResolver] = None,
    ):
        self.db = db
        self._running = False
        self._shutdown_requested = False
        self._current_run: Optional[SchedulerRunState] = None
        self._tick_interval = max(1, int(tick_interval_seconds))

        # Nightly-report cron config (Phase 5 / Task 62; tz-aware in F3).
        self._nightly_report_enabled = bool(nightly_report_enabled)
        self._nightly_report_hour_local = int(nightly_report_hour_local)
        self._nightly_report_timezone = str(nightly_report_timezone or "UTC")
        self._nightly_report_dir = (
            Path(nightly_report_dir) if nightly_report_dir else None
        )
        self._nightly_report_regression_threshold = float(
            nightly_report_regression_threshold
        )
        self._nightly_report_lookback_hours = int(nightly_report_lookback_hours)
        self._last_nightly_report_date: Optional[str] = None

        # Persistence (imported lazily to avoid a circular dependency
        # between ``backend.scheduler`` and ``backend.agent.strategy``).
        from backend.agent.strategy.scheduler_store import (
            InMemorySchedulerStore,
            MongoSchedulerStore,
            SchedulerStore as _SchedulerStore,
        )
        if store is not None:
            self.store: "SchedulerStore" = store
        elif db is not None:
            self.store = MongoSchedulerStore(db)
        else:
            self.store = InMemorySchedulerStore()

        # Sub-components
        self.timezone_resolver = timezone_resolver or ScheduleTimezoneResolver()
        self.ollama_manager = OllamaResidencyManager(ollama_base_url)
        self.circuit_breaker = MongoDBCircuitBreaker()

        # Created per-run with the schedule's config
        self._pause_evaluator: Optional[PauseConditionEvaluator] = None
        self._stop_evaluator: Optional[StopConditionEvaluator] = None
        self._contention_manager: Optional[ResourceContentionManager] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def resume_from_store(self) -> dict[str, int]:
        """Rebuild in-memory state from the persistent store.

        Restores the circuit breaker state from the last persisted value
        (best-effort; OPEN states are re-armed by recording the configured
        ``failure_threshold`` failures so the breaker auto-recovers via
        its normal HALF_OPEN probe path). Returns a small summary dict
        suitable for startup logging.
        """
        await self.store.ensure_indexes()
        schedules = await self.store.list()
        paused = sum(1 for s in schedules if s.paused)
        open_breakers = sum(
            1 for s in schedules if (s.circuit_breaker_state or "").lower() == "open"
        )
        # Restore the daemon-level breaker if any schedule was OPEN.
        if open_breakers:
            for _ in range(self.circuit_breaker.failure_threshold):
                self.circuit_breaker.record_failure()
        # Refresh next_run_at for active, unpaused schedules so list_due works.
        now = datetime.now(timezone.utc)
        for schedule in schedules:
            if schedule.paused or schedule.status != "active":
                continue
            next_run = self.timezone_resolver.evaluate_next_run(schedule, now=now)
            if next_run is not None and next_run != schedule.next_run_at:
                await self.store.update_runtime_state(
                    schedule.id, next_run_at=next_run
                )
        return {
            "loaded": len(schedules),
            "paused": paused,
            "open_breakers": open_breakers,
        }

    async def start(self) -> None:
        """Start the daemon loop. Runs until shutdown is requested."""
        if self._running:
            logger.warning("SchedulerDaemon already running")
            return

        self._running = True
        self._shutdown_requested = False
        self._setup_signal_handlers()
        logger.info("SchedulerDaemon starting")

        try:
            await self._main_loop()
        except asyncio.CancelledError:
            logger.info("SchedulerDaemon cancelled")
        finally:
            self._running = False
            logger.info("SchedulerDaemon stopped")

    async def stop(self) -> None:
        """Request graceful shutdown."""
        logger.info("SchedulerDaemon shutdown requested")
        self._shutdown_requested = True

    async def run_now(self, schedule_id: str) -> str:
        """Trigger an immediate exploration run for ``schedule_id``.

        Bypasses cron evaluation. Spawns the run as an asyncio task so
        the caller (typically an HTTP handler) can return the trace id
        without blocking. Respects the persisted pause flag and the
        per-schedule circuit breaker state.

        Args:
            schedule_id: The id of the schedule to execute.

        Returns:
            The trace id (== run id) of the freshly spawned run.

        Raises:
            ScheduleNotFoundError: ``schedule_id`` does not exist.
            SchedulePausedError: The schedule is currently paused.
            CircuitBreakerOpenError: The schedule's persisted circuit
                breaker state is ``open``.
        """
        schedule = await self.store.get(schedule_id)
        if schedule is None:
            raise ScheduleNotFoundError(schedule_id)
        if schedule.paused:
            raise SchedulePausedError(
                f"Schedule {schedule_id} is paused: "
                f"{schedule.paused_reason or 'no reason recorded'}"
            )
        if (schedule.circuit_breaker_state or "").lower() == CircuitState.OPEN.value:
            raise CircuitBreakerOpenError(
                f"Schedule {schedule_id} circuit breaker is OPEN"
            )

        trace_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        await self.store.update_runtime_state(
            schedule_id,
            last_run_at=now,
            last_run_status=SchedulerRunStatus.RUNNING.value,
        )
        # Fire-and-forget the actual exploration so the HTTP path returns fast.
        asyncio.create_task(
            self._run_now_task(schedule, trace_id),
            name=f"scheduler-run-now-{trace_id[:8]}",
        )
        return trace_id

    async def _run_now_task(
        self, schedule: StrategySchedule, trace_id: str
    ) -> None:
        """Background task body for :meth:`run_now`."""
        try:
            await self._execute_exploration_run(schedule, run_id=trace_id)
        except Exception as exc:  # noqa: BLE001 - keep the daemon alive
            logger.exception(
                "run_now task for schedule %s (%s) failed: %s",
                schedule.id,
                trace_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        """Main tick loop.

        On every tick:
            1. Ask the store for due schedules.
            2. Trigger an exploration run for each.
            3. Flush the circuit breaker buffer when recovered.
            4. Sleep until the next tick (interruptible).
        """
        while not self._shutdown_requested:
            try:
                now = datetime.now(timezone.utc)
                due = await self.store.list_due(now)

                for schedule in due:
                    if self._shutdown_requested:
                        break
                    logger.info(
                        "Triggering exploration run for schedule '%s' (%s)",
                        schedule.name,
                        schedule.id,
                    )
                    await self._execute_exploration_run(schedule)
                    next_run = self.timezone_resolver.evaluate_next_run(
                        schedule, now=datetime.now(timezone.utc)
                    )
                    if next_run is not None:
                        await self.store.update_runtime_state(
                            schedule.id, next_run_at=next_run
                        )

                # Flush circuit breaker buffer if recovered
                if (
                    self.circuit_breaker.state.value == "closed"
                    and self.circuit_breaker.buffer_size > 0
                ):
                    flushed = await self.circuit_breaker.flush_buffer(self.db)
                    if flushed > 0:
                        logger.info(
                            "Flushed %d buffered operations after recovery",
                            flushed,
                        )

                # Daily nightly-report cron tick (Phase 5 / Task 62).
                await self._maybe_run_nightly_report(now)

            except Exception as exc:
                logger.exception("Error in scheduler main loop: %s", exc)

            # Sleep for the tick interval (interruptible)
            await self._interruptible_sleep(self._tick_interval)

    # ------------------------------------------------------------------
    # Exploration run
    # ------------------------------------------------------------------

    async def _execute_exploration_run(
        self,
        schedule: StrategySchedule,
        run_id: Optional[str] = None,
    ) -> None:
        """Execute a full exploration run for ``schedule``.

        See class docstring for the high-level flow. ``run_id`` is
        accepted so callers (e.g. :meth:`run_now`) can emit a stable
        trace id ahead of time.
        """
        # 1. Initialize run state
        run_state = SchedulerRunState(
            schedule_id=schedule.id,
            status=SchedulerRunStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
            candidates_total=len(schedule.candidate_strategy_ids),
        )
        if run_id is not None:
            run_state.run_id = run_id
        self._current_run = run_state

        # Persist that the run has started.
        await self._safe_update_runtime(
            schedule.id,
            last_run_at=run_state.started_at,
            last_run_status=SchedulerRunStatus.RUNNING.value,
        )

        # Initialize per-run evaluators
        self._pause_evaluator = PauseConditionEvaluator(schedule.pause_config)
        self._stop_evaluator = StopConditionEvaluator(schedule.stop_conditions)
        self._contention_manager = ResourceContentionManager(schedule.budget)

        logger.info(
            "Starting exploration run %s for schedule '%s' — %d candidates",
            run_state.run_id,
            schedule.name,
            run_state.candidates_total,
        )

        try:
            # 2. Prewarm models (best-effort)
            if schedule.candidate_strategy_ids:
                await self.ollama_manager.prewarm_models([])

            # 3. Iterate candidates
            for strategy_id in schedule.candidate_strategy_ids:
                if self._shutdown_requested:
                    run_state.status = SchedulerRunStatus.CANCELLED
                    run_state.stop_reason = StopReason.MANUAL_STOP
                    break

                # 4a. Pause check
                should_continue = await self._handle_pause(run_state)
                if not should_continue:
                    run_state.status = SchedulerRunStatus.DEFERRED
                    break

                # 4b. Stop check
                stop_reason = self._stop_evaluator.evaluate(run_state)
                if stop_reason is not None:
                    run_state.stop_reason = stop_reason
                    run_state.status = SchedulerRunStatus.COMPLETED
                    logger.info(
                        "Run %s stopped: %s", run_state.run_id, stop_reason.value
                    )
                    break

                # 4c. Resource contention check
                pressure = self._contention_manager.check_resource_pressure()
                if pressure is not None and pressure.should_pause:
                    logger.info(
                        "Resource pressure detected: %s — pausing briefly",
                        pressure.details,
                    )
                    await self._interruptible_sleep(10)

                # 4d. Evaluate candidate
                result = await self._evaluate_candidate(
                    strategy_id, schedule, run_state
                )

                # 4e. Record result
                self._stop_evaluator.record_result(run_state, result)
                await self._persist_result(result)

            else:
                # All candidates processed without break
                if run_state.status == SchedulerRunStatus.RUNNING:
                    run_state.status = SchedulerRunStatus.COMPLETED
                    run_state.stop_reason = StopReason.ALL_CANDIDATES_COMPLETE

        except Exception as exc:
            logger.exception("Exploration run %s failed: %s", run_state.run_id, exc)
            run_state.status = SchedulerRunStatus.FAILED

        # 5. Finalize
        run_state.completed_at = datetime.now(timezone.utc)
        self._current_run = None

        # Persist final runtime state immediately so a crash here doesn't
        # leave the schedule stuck on RUNNING.
        await self._persist_post_run_state(schedule, run_state)

        # Generate and persist nightly report
        report = self._generate_report(run_state)
        await self._persist_report(report)

        logger.info(
            "Exploration run %s finished — status=%s, candidates=%d/%d, leader=%s",
            run_state.run_id,
            run_state.status.value,
            run_state.candidates_completed,
            run_state.candidates_total,
            run_state.current_leader or "none",
        )

    async def _persist_post_run_state(
        self, schedule: StrategySchedule, run_state: SchedulerRunState
    ) -> None:
        """Write the post-run runtime state and circuit-breaker snapshot."""
        consecutive_failures = (
            schedule.consecutive_failures + 1
            if run_state.status == SchedulerRunStatus.FAILED
            else 0
        )
        new_breaker_state = self.circuit_breaker.state.value
        await self._safe_update_runtime(
            schedule.id,
            last_run_at=run_state.completed_at,
            last_run_status=run_state.status.value,
            consecutive_failures=consecutive_failures,
            circuit_breaker=new_breaker_state,
        )

    async def _safe_update_runtime(self, schedule_id: str, **fields: Any) -> None:
        """Persist a runtime-state update, swallowing storage failures.

        Storage faults must never crash the daemon — they are logged at
        WARNING level and the in-memory loop continues.
        """
        try:
            await self.store.update_runtime_state(schedule_id, **fields)
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort
            logger.warning(
                "Failed to persist runtime state for schedule %s: %s",
                schedule_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Candidate evaluation
    # ------------------------------------------------------------------

    async def _evaluate_candidate(
        self,
        strategy_id: str,
        schedule: StrategySchedule,
        run_state: SchedulerRunState,
    ) -> CandidateResult:
        """Evaluate a single strategy candidate.

        In a real implementation this would:
        1. Load the StrategySpec from DB.
        2. Run it via StrategyRunner against the evaluation dataset.
        3. Score with CompositeScoreCalculator.
        4. Return CandidateResult.

        For now: placeholder that returns mock result.
        TODO: Wire to EvaluationRunner in Phase 3 integration.
        """
        start_time = time.monotonic()

        # Placeholder: simulate evaluation with a brief sleep
        await asyncio.sleep(0.1)

        elapsed_ms = (time.monotonic() - start_time) * 1000

        result = CandidateResult(
            strategy_id=strategy_id,
            composite_score=0.0,
            test_cases_evaluated=0,
            duration_ms=elapsed_ms,
            llm_calls=0,
            cost_usd=0.0,
            fatal_failures=0,
            completed_at=datetime.now(timezone.utc),
        )

        logger.debug(
            "Evaluated candidate '%s' (placeholder) in %.1fms",
            strategy_id,
            elapsed_ms,
        )
        return result

    # ------------------------------------------------------------------
    # Pause handling
    # ------------------------------------------------------------------

    async def _handle_pause(self, run_state: SchedulerRunState) -> bool:
        """Check and handle pause conditions.

        Returns True if should continue, False if DEFERRED (stop).
        """
        if self._pause_evaluator is None or self._contention_manager is None:
            return True

        metrics = self._contention_manager.get_system_metrics()
        decision = self._pause_evaluator.evaluate(
            run_state,
            cpu_pct=metrics["cpu_pct"],
            ram_pct=metrics["ram_pct"],
            gpu_pct=metrics["gpu_pct"],
        )

        if not decision.should_pause:
            return True

        # Enter pause loop
        logger.info(
            "Run %s pausing: %s — %s",
            run_state.run_id,
            decision.reason,
            decision.details,
        )
        run_state.status = SchedulerRunStatus.PAUSED
        pause_start = time.monotonic()

        while decision.should_pause and not self._shutdown_requested:
            # Check deferred timeout
            if self._pause_evaluator.check_deferred(run_state):
                logger.warning("Run %s deferred (max pause exceeded)", run_state.run_id)
                return False

            await self._interruptible_sleep(10)

            # Re-evaluate
            metrics = self._contention_manager.get_system_metrics()
            decision = self._pause_evaluator.evaluate(
                run_state,
                cpu_pct=metrics["cpu_pct"],
                ram_pct=metrics["ram_pct"],
                gpu_pct=metrics["gpu_pct"],
            )

        # Resume
        pause_duration = (time.monotonic() - pause_start) / 60.0
        run_state.total_pause_minutes += pause_duration
        run_state.status = SchedulerRunStatus.RUNNING
        logger.info(
            "Run %s resumed after %.1f minutes pause",
            run_state.run_id,
            pause_duration,
        )
        return True

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    def _generate_report(self, run_state: SchedulerRunState) -> NightlyReport:
        """Generate a nightly report from the completed run state."""
        # Build candidate rankings sorted by score descending
        rankings = sorted(
            [
                {
                    "strategy_id": r.strategy_id,
                    "score": r.composite_score,
                    "rank": 0,
                }
                for r in run_state.candidate_results
            ],
            key=lambda x: x["score"],
            reverse=True,
        )
        for i, entry in enumerate(rankings):
            entry["rank"] = i + 1

        # Calculate duration
        duration_minutes = 0.0
        if run_state.started_at and run_state.completed_at:
            started = run_state.started_at
            completed = run_state.completed_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if completed.tzinfo is None:
                completed = completed.replace(tzinfo=timezone.utc)
            duration_minutes = (completed - started).total_seconds() / 60.0

        # Identify leader
        leader_id: Optional[str] = None
        leader_score = 0.0
        if rankings:
            leader_id = rankings[0]["strategy_id"]
            leader_score = rankings[0]["score"]

        # Collect warnings
        warnings: list[str] = []
        if run_state.consecutive_failures > 0:
            warnings.append(
                f"{run_state.consecutive_failures} consecutive failures at end of run"
            )
        if self.circuit_breaker.buffer_size > 0:
            warnings.append(
                f"{self.circuit_breaker.buffer_size} DB writes still buffered"
            )

        return NightlyReport(
            run_id=run_state.run_id,
            schedule_id=run_state.schedule_id,
            status=run_state.status,
            started_at=run_state.started_at,
            completed_at=run_state.completed_at,
            duration_minutes=duration_minutes,
            candidates_evaluated=run_state.candidates_completed,
            leader_strategy_id=leader_id,
            leader_score=leader_score,
            candidate_rankings=rankings,
            total_llm_calls=run_state.total_llm_calls,
            total_cost_usd=run_state.total_cost_usd,
            pause_count=0,
            total_pause_minutes=run_state.total_pause_minutes,
            stop_reason=run_state.stop_reason,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Persistence (via circuit breaker)
    # ------------------------------------------------------------------

    async def _persist_result(self, result: CandidateResult) -> None:
        """Persist candidate result via circuit breaker."""
        if self.db is None:
            return

        async def _write_result() -> None:
            collection = self.db["scheduler_results"]
            await collection.insert_one(result.model_dump())

        await self.circuit_breaker.execute(_write_result)

    async def _persist_report(self, report: NightlyReport) -> None:
        """Persist nightly report via circuit breaker."""
        if self.db is None:
            logger.info(
                "No DB configured — report for run %s not persisted", report.run_id
            )
            return

        async def _write_report() -> None:
            collection = self.db["scheduler_reports"]
            await collection.insert_one(report.model_dump())

        await self.circuit_breaker.execute(_write_report)

    # ------------------------------------------------------------------
    # Nightly report cron (Phase 5 / Task 62)
    # ------------------------------------------------------------------

    async def _maybe_run_nightly_report(self, now: datetime) -> None:
        """Run the day-grained nightly report once per day, in local time.

        The trigger fires when ``now`` (UTC) maps to the configured local
        hour in ``self._nightly_report_timezone`` (an IANA tz string).
        Local time is resolved through :class:`ScheduleTimezoneResolver`
        so DST spring-forward and fall-back transitions are handled
        without bespoke arithmetic.

        De-duplication uses the local calendar date so a fall-back
        repeat of 03:xx only triggers once, and a spring-forward jump
        over 03:xx still only triggers once on the following day.

        If the configured timezone is invalid we log a warning and fall
        back to UTC behaviour rather than crashing the daemon.

        Errors from the underlying generator are logged at WARNING and
        never propagate so a bad report cycle cannot crash the daemon.

        Args:
            now: The current UTC time (must be timezone-aware in
                production; naive inputs are coerced to UTC).
        """
        if not self._nightly_report_enabled:
            return
        if self.db is None:
            return

        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        tz_name = self._nightly_report_timezone
        try:
            local_now = self.timezone_resolver.to_local(now, tz_name)
        except Exception as exc:  # noqa: BLE001 - tz lookup must not crash
            logger.warning(
                "Invalid nightly-report timezone %r — falling back to UTC: %s",
                tz_name,
                exc,
            )
            local_now = now

        if local_now.hour != self._nightly_report_hour_local:
            return
        date_key = local_now.date().isoformat()
        if self._last_nightly_report_date == date_key:
            return

        try:
            from backend.agent.strategy.report_generator import (
                NightlyReportGenerator,
            )

            generator = NightlyReportGenerator(
                self.db,
                lookback_hours=self._nightly_report_lookback_hours,
                regression_threshold=self._nightly_report_regression_threshold,
                output_dir=self._nightly_report_dir,
            )
            summary = await generator.generate(run_date=now)
            self._last_nightly_report_date = date_key
            logger.info(
                "Nightly report written for %s (markdown=%s, runs=%d, regressions=%d)",
                summary.report_date,
                summary.markdown_path,
                summary.total_runs,
                len(summary.regressions),
            )
        except Exception as exc:  # noqa: BLE001 - daemon must not crash
            logger.warning(
                "Nightly report generation failed (non-fatal): %s", exc
            )

    # ------------------------------------------------------------------
    # Schedule loading (legacy — kept as a fallback path for callers
    # that still inspect ``self.db`` directly)
    # ------------------------------------------------------------------

    async def _load_schedules(self) -> list[StrategySchedule]:
        """Return all known schedules via the configured store.

        Retained for backwards compatibility with external callers.
        """
        try:
            return await self.store.list()
        except Exception as exc:  # noqa: BLE001 - tolerate transient errors
            logger.warning("Failed to load schedules from store: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _setup_signal_handlers(self) -> None:
        """Register SIGTERM/SIGINT handlers for graceful shutdown."""
        try:
            signal.signal(signal.SIGINT, self._handle_signal)
            signal.signal(signal.SIGTERM, self._handle_signal)
        except (OSError, ValueError):
            # signal.signal may fail if not on main thread or on Windows for SIGTERM
            logger.debug("Could not register signal handlers (not main thread?)")

    def _handle_signal(self, signum: int, frame: Any) -> None:
        """Signal handler — sets shutdown flag."""
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.info("Received signal %s — requesting shutdown", sig_name)
        self._shutdown_requested = True

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    async def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep that can be interrupted by shutdown request."""
        interval = min(seconds, 1.0)
        elapsed = 0.0
        while elapsed < seconds and not self._shutdown_requested:
            await asyncio.sleep(interval)
            elapsed += interval
