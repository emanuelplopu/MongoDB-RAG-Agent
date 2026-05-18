"""Overnight exploration scheduler daemon — main execution loop."""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import datetime, timezone
from typing import Any, Optional

from backend.scheduler.circuit_breaker import MongoDBCircuitBreaker
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

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 60


class SchedulerDaemon:
    """Main overnight exploration scheduler.

    Lifecycle:
    1. Every 60s: evaluate if any schedule should run.
    2. If schedule triggers: start exploration run.
    3. During run: iterate candidates, check pause/stop conditions.
    4. After run: generate nightly report.

    Supports graceful shutdown via SIGTERM/SIGINT.
    """

    def __init__(
        self,
        db: Any = None,
        ollama_base_url: str = "http://localhost:11434",
    ):
        self.db = db
        self._running = False
        self._shutdown_requested = False
        self._current_run: Optional[SchedulerRunState] = None

        # Sub-components
        self.timezone_resolver = ScheduleTimezoneResolver()
        self.ollama_manager = OllamaResidencyManager(ollama_base_url)
        self.circuit_breaker = MongoDBCircuitBreaker()

        # Created per-run with the schedule's config
        self._pause_evaluator: Optional[PauseConditionEvaluator] = None
        self._stop_evaluator: Optional[StopConditionEvaluator] = None
        self._contention_manager: Optional[ResourceContentionManager] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        """Main tick loop (every 60 seconds).

        1. Load active schedules from DB (or cache).
        2. For each schedule: check if it should trigger.
        3. If trigger: start exploration run.
        4. Sleep until next tick.
        """
        while not self._shutdown_requested:
            try:
                schedules = await self._load_schedules()

                for schedule in schedules:
                    if self._shutdown_requested:
                        break

                    next_run = self.timezone_resolver.evaluate_next_run(schedule)
                    if next_run is None:
                        continue

                    now = datetime.now(timezone.utc)
                    # If next run is within the tick interval, trigger it
                    if (next_run - now).total_seconds() <= TICK_INTERVAL_SECONDS:
                        logger.info(
                            "Triggering exploration run for schedule '%s' (%s)",
                            schedule.name,
                            schedule.id,
                        )
                        await self._execute_exploration_run(schedule)

                # Flush circuit breaker buffer if recovered
                if self.circuit_breaker.state.value == "closed" and self.circuit_breaker.buffer_size > 0:
                    flushed = await self.circuit_breaker.flush_buffer(self.db)
                    if flushed > 0:
                        logger.info("Flushed %d buffered operations after recovery", flushed)

            except Exception as exc:
                logger.exception("Error in scheduler main loop: %s", exc)

            # Sleep for the tick interval (interruptible)
            await self._interruptible_sleep(TICK_INTERVAL_SECONDS)

    # ------------------------------------------------------------------
    # Exploration run
    # ------------------------------------------------------------------

    async def _execute_exploration_run(self, schedule: StrategySchedule) -> None:
        """Execute a full exploration run for a schedule.

        Flow:
        1. Initialize run state.
        2. Prewarm models.
        3. Get candidate strategies.
        4. For each candidate:
            a. Check pause conditions → pause/resume loop.
            b. Check stop conditions → break if stop.
            c. Check resource contention.
            d. Execute strategy evaluation.
            e. Record result.
        5. Generate nightly report.
        """
        # 1. Initialize run state
        run_state = SchedulerRunState(
            schedule_id=schedule.id,
            status=SchedulerRunStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
            candidates_total=len(schedule.candidate_strategy_ids),
        )
        self._current_run = run_state

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
    # Schedule loading
    # ------------------------------------------------------------------

    async def _load_schedules(self) -> list[StrategySchedule]:
        """Load active schedules from DB. Returns empty list if DB unavailable."""
        if self.db is None:
            return []

        try:
            collection = self.db["strategy_schedules"]
            cursor = collection.find({"status": "active"})
            schedules: list[StrategySchedule] = []
            async for doc in cursor:
                try:
                    schedules.append(StrategySchedule(**doc))
                except Exception as exc:
                    logger.warning("Failed to parse schedule document: %s", exc)
            return schedules
        except Exception as exc:
            logger.warning("Failed to load schedules from DB: %s", exc)
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
