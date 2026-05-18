"""Stop condition evaluator for the overnight scheduler."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from backend.scheduler.models import (
    CandidateResult,
    SchedulerRunState,
    StopConditions,
    StopReason,
)

logger = logging.getLogger(__name__)


class StopConditionEvaluator:
    """
    Evaluates whether the scheduler should stop execution.

    Stop conditions (any triggers immediate stop):
    1. max_runtime_minutes exceeded
    2. max_llm_calls exceeded
    3. max_cost_usd exceeded
    4. max_consecutive_failures (5) reached
    5. fatal_failure_rate > 50%
    6. leader_found: one strategy beats 2nd by leader_margin_pct after min_candidates_completed
    7. all_candidates_complete: no more strategies to evaluate
    """

    def __init__(self, conditions: StopConditions):
        self.conditions = conditions

    def evaluate(self, run_state: SchedulerRunState) -> Optional[StopReason]:
        """Check all stop conditions against current run state. Returns reason if should stop, None otherwise."""
        # 1. Max runtime
        if run_state.started_at is not None:
            now = datetime.now(timezone.utc)
            started = run_state.started_at
            # Handle naive datetimes by assuming UTC
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed_minutes = (now - started).total_seconds() / 60
            if elapsed_minutes > self.conditions.max_runtime_minutes:
                logger.warning(
                    "Stop: max_runtime_minutes exceeded (%.1f > %d)",
                    elapsed_minutes,
                    self.conditions.max_runtime_minutes,
                )
                return StopReason.MAX_RUNTIME

        # 2. Max LLM calls
        if run_state.total_llm_calls > self.conditions.max_llm_calls:
            logger.warning(
                "Stop: max_llm_calls exceeded (%d > %d)",
                run_state.total_llm_calls,
                self.conditions.max_llm_calls,
            )
            return StopReason.MAX_LLM_CALLS

        # 3. Max cost
        if run_state.total_cost_usd > self.conditions.max_cost_usd:
            logger.warning(
                "Stop: max_cost_usd exceeded (%.2f > %.2f)",
                run_state.total_cost_usd,
                self.conditions.max_cost_usd,
            )
            return StopReason.MAX_COST

        # 4. Max consecutive failures
        if run_state.consecutive_failures >= self.conditions.max_consecutive_failures:
            logger.warning(
                "Stop: max_consecutive_failures reached (%d >= %d)",
                run_state.consecutive_failures,
                self.conditions.max_consecutive_failures,
            )
            return StopReason.MAX_CONSECUTIVE_FAILURES

        # 5. Fatal failure rate
        if run_state.candidates_completed >= 3:
            total_fatal = sum(
                r.fatal_failures for r in run_state.candidate_results
            )
            total_evaluated = run_state.candidates_completed
            if total_evaluated > 0:
                fatal_rate = total_fatal / total_evaluated
                if fatal_rate > self.conditions.stop_on_fatal_failure_rate:
                    logger.warning(
                        "Stop: fatal_failure_rate exceeded (%.2f > %.2f)",
                        fatal_rate,
                        self.conditions.stop_on_fatal_failure_rate,
                    )
                    return StopReason.FATAL_FAILURE_RATE

        # 6. Leader found
        if self.conditions.stop_on_leader_found:
            leader_id = self.detect_leader(run_state)
            if leader_id is not None:
                logger.info(
                    "Stop: leader found — strategy '%s' beats 2nd by margin",
                    leader_id,
                )
                return StopReason.LEADER_FOUND

        # 7. All candidates complete
        if (
            run_state.candidates_total > 0
            and run_state.candidates_completed >= run_state.candidates_total
        ):
            logger.info("Stop: all candidates complete")
            return StopReason.ALL_CANDIDATES_COMPLETE

        return None

    def record_result(
        self, run_state: SchedulerRunState, result: CandidateResult
    ) -> None:
        """Record a candidate result and update run state counters."""
        # Add to results list
        run_state.candidate_results.append(result)
        run_state.candidates_completed += 1

        # Accumulate resource usage
        run_state.total_llm_calls += result.llm_calls
        run_state.total_cost_usd += result.cost_usd

        # Track consecutive failures
        if result.fatal_failures > 0:
            run_state.consecutive_failures += 1
        else:
            run_state.consecutive_failures = 0

        # Update current leader
        leader_id = self._find_top_scorer(run_state)
        if leader_id is not None:
            run_state.current_leader = leader_id

        logger.info(
            "Recorded result for strategy '%s': score=%.3f, llm_calls=%d, "
            "cost=%.4f, fatal=%d (total completed: %d)",
            result.strategy_id,
            result.composite_score,
            result.llm_calls,
            result.cost_usd,
            result.fatal_failures,
            run_state.candidates_completed,
        )

    def detect_leader(self, run_state: SchedulerRunState) -> Optional[str]:
        """Check if a leader has emerged (beats 2nd by margin after min candidates)."""
        # Only activate after min_candidates_completed results
        if run_state.candidates_completed < self.conditions.min_candidates_completed:
            return None

        results = run_state.candidate_results
        if len(results) < 2:
            return None

        # Sort by composite_score descending
        sorted_results = sorted(
            results, key=lambda r: r.composite_score, reverse=True
        )

        first_score = sorted_results[0].composite_score
        second_score = sorted_results[1].composite_score

        # Avoid division issues with zero scores
        if second_score <= 0:
            # If second is zero/negative and first is positive, leader is clear
            if first_score > 0:
                return sorted_results[0].strategy_id
            return None

        # Leader if 1st place score > 2nd place score * (1 + margin/100)
        margin_threshold = second_score * (
            1 + self.conditions.leader_margin_pct / 100
        )
        if first_score > margin_threshold:
            return sorted_results[0].strategy_id

        return None

    def _find_top_scorer(self, run_state: SchedulerRunState) -> Optional[str]:
        """Find the strategy with the highest composite score."""
        if not run_state.candidate_results:
            return None

        best = max(
            run_state.candidate_results, key=lambda r: r.composite_score
        )
        return best.strategy_id
