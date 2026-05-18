"""Nightly report generator for the Strategy OS (Phase 5 / Task 62).

Aggregates the last 24 hours of Strategy OS activity into a structured
:class:`NightlyReportSummary` Pydantic model, a Markdown report on disk,
and an upserted document in the ``strategy_run_reports`` Mongo collection.

Inputs (read-only):
    * ``strategy_runs``           — per-run trace docs emitted by the
      :class:`backend.agent.strategy.strategy_runner.StrategyRunner`.
      **Placeholder collection** — the runner does not yet persist
      traces. The expected shape is documented in :data:`EXPECTED_RUN_DOC`.
    * ``evaluation_results``      — :class:`backend.evaluation.models.EvaluationRun`
      docs written by :class:`backend.evaluation.runner.EvaluationRunner`.
    * ``scheduler_results``       — per-candidate
      :class:`backend.scheduler.models.CandidateResult` docs written by
      :meth:`backend.scheduler.scheduler_daemon.SchedulerDaemon._persist_result`.
    * ``scheduler_reports``       — per-run
      :class:`backend.scheduler.models.NightlyReport` docs written by
      :meth:`backend.scheduler.scheduler_daemon.SchedulerDaemon._persist_report`.
      Used to derive the prior-week composite-score baseline.
    * ``strategy_schedules``      — read via
      :class:`backend.agent.strategy.scheduler_store.MongoSchedulerStore`.

Outputs:
    * Markdown report at ``{output_dir}/{run_date:%Y-%m-%d}.md``.
    * Upsert into ``strategy_run_reports`` keyed by ``report_date`` so
      re-running for the same day is idempotent.

Repo rule #3: this module uses ``pymongo.AsyncMongoClient`` collection
semantics only — no Motor APIs are introduced.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.agent.strategy.exploration_modes import RegressionMode
from backend.agent.strategy.promotion_evaluator import (
    PromotionCandidateSummary,
    PromotionEvaluator,
)
from backend.agent.strategy.runtime_profile_store import RuntimeModelProfile

logger = logging.getLogger(__name__)

__all__ = [
    "EXPECTED_RUN_DOC",
    "NightlyReportGenerator",
    "NightlyReportSummary",
    "RUN_REPORT_COLLECTION_NAME",
    "STRATEGY_RUN_COLLECTION_NAME",
]


#: Canonical Mongo collection name for the day-grained nightly summaries.
RUN_REPORT_COLLECTION_NAME: str = "strategy_run_reports"

#: Placeholder collection name expected to hold per-run trace documents
#: emitted by :class:`StrategyRunner`. Update this in tandem with the
#: runner once Phase 2 trace persistence lands.
STRATEGY_RUN_COLLECTION_NAME: str = "strategy_runs"

#: Documented expected shape of a strategy run document. Kept as a dict
#: rather than a Pydantic model so test fixtures can seed the collection
#: without coupling to a frozen schema during the placeholder window.
EXPECTED_RUN_DOC: dict[str, Any] = {
    "run_id": "str",
    "strategy_id": "str",
    "capability_id": "str | None",
    "tenant_id": "str | None",
    "status": "success | failed | timed_out | cancelled",
    "started_at": "datetime (UTC)",
    "completed_at": "datetime (UTC)",
    "duration_ms": "float",
    "halt_reason": "str | None",
    # Optional flat list of per-node outcomes used for top-failing-node ranking.
    # Each entry: {node_id: str, status: str, duration_ms: float}.
    "node_outputs": "list[dict]",
}


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


def _ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Coerce ``value`` to a timezone-aware UTC ``datetime``."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _percentile(values: list[float], pct: float) -> float:
    """Return the ``pct``-percentile of ``values`` (linear interpolation).

    Returns ``0.0`` for empty input. Mirrors the ``"linear"`` method of
    ``numpy.percentile`` so report numbers stay comparable to ad-hoc
    debugging sessions.
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * frac)


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic surface
# ═══════════════════════════════════════════════════════════════════════════════


class NightlyReportSummary(BaseModel):
    """Structured day-grained summary of Strategy OS activity.

    Persisted into ``strategy_run_reports`` and serialized into Markdown
    for human consumption. Fields are intentionally flat — the consumer
    contract is "one row per day" so downstream dashboards can chart
    metrics directly without a join.
    """

    report_date: str = Field(
        description="Calendar date the report covers (YYYY-MM-DD, UTC)."
    )
    window_start: datetime = Field(description="Aggregation window lower bound (UTC).")
    window_end: datetime = Field(description="Aggregation window upper bound (UTC).")

    # Run aggregates
    total_runs: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    p50_latency_ms: float = Field(default=0.0, ge=0.0)
    p95_latency_ms: float = Field(default=0.0, ge=0.0)

    # Evaluation aggregates
    mean_composite_score: float = Field(default=0.0)
    evaluation_count: int = Field(default=0, ge=0)

    # Scheduler aggregates
    scheduler_candidate_count: int = Field(default=0, ge=0)
    scheduler_mean_candidate_score: float = Field(default=0.0)

    # Quality signals
    top_failing_nodes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Top failing node ids ranked by failure count.",
    )
    regressions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="(strategy_id, capability_id) pairs whose composite score "
                    "dropped vs the prior-week baseline. Mode-specific trigger "
                    "names from RegressionMode.regression_triggers() are "
                    "attached when mode='regression'.",
    )

    # Blueprint 05 §10 sections (Phase 6 / Task 79 / P10).
    winning_strategies_by_capability: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-capability winning strategy: "
                    "{capability_id: {strategy_id, mean_score, sample_count}}.",
    )
    fatal_failures: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Strategy runs flagged as fatal or hard-limit-breach.",
    )
    latency_cost_table: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-strategy latency p50/p95 and mean cost when available.",
    )
    retrieval_quality_table: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-strategy retrieval precision and top_k stats.",
    )
    citation_quality_table: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-strategy citation coverage and accuracy stats.",
    )
    recommended_promotions: list[PromotionCandidateSummary] = Field(
        default_factory=list,
        description="Promotion candidates with recommendation='recommended'.",
    )
    promotion_evaluation_breakdown: list[PromotionCandidateSummary] = Field(
        default_factory=list,
        description="Full evaluator output (recommended + blocked + needs_more_runs).",
    )
    recommended_deprecations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Active strategies whose composite score dropped >10% vs the "
                    "prior 30-day window OR which produced any fatal failure in "
                    "the current window.",
    )
    cases_needing_better_gold_labels: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Test cases where every strategy scored < 0.5 in the window, "
                    "or where judge_variance is high (placeholder when calibration "
                    "data is unavailable).",
    )

    # Scheduler health
    paused_schedules_count: int = Field(default=0, ge=0)
    open_circuit_breakers_count: int = Field(default=0, ge=0)
    schedules_at_failure_threshold: int = Field(
        default=0,
        ge=0,
        description="Schedules with consecutive_failures >= 3.",
    )

    # File pointer + timestamps
    markdown_path: Optional[Path] = Field(
        default=None, description="Path of the Markdown report on disk."
    )
    json_path: Optional[Path] = Field(
        default=None,
        description="Path of the JSON sibling report on disk (F5 / Task 87).",
    )
    generated_at: datetime = Field(default_factory=_utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# Generator
# ═══════════════════════════════════════════════════════════════════════════════


class NightlyReportGenerator:
    """Aggregate the last ``lookback_hours`` of Strategy OS activity.

    Args:
        db: Async Mongo database handle (``pymongo.AsyncMongoClient``
            database). May be ``None`` to disable persistence — the
            Markdown still gets written and the in-memory summary is
            returned, which is useful for dry-runs from the CLI.
        lookback_hours: Size of the aggregation window. Defaults to 24.
        regression_threshold: Composite-score drop (in absolute units)
            required to flag a ``(strategy_id, capability_id)`` pair as
            a regression. Defaults to ``0.05``.
        output_dir: Directory the Markdown report is written into.
            Defaults to ``data/reports/strategy/`` resolved relative to
            the repo root.
    """

    #: Default consecutive-failure count at which a schedule is flagged
    #: as "at threshold" in the scheduler health rollup.
    FAILURE_FLAG_THRESHOLD: int = 3

    def __init__(
        self,
        db: Any,
        *,
        lookback_hours: int = 24,
        regression_threshold: float = 0.05,
        output_dir: Optional[Path] = None,
    ) -> None:
        if lookback_hours <= 0:
            raise ValueError("lookback_hours must be positive")
        if regression_threshold < 0:
            raise ValueError("regression_threshold must be non-negative")
        self.db = db
        self.lookback_hours = int(lookback_hours)
        self.regression_threshold = float(regression_threshold)
        self.output_dir = Path(output_dir) if output_dir else self._default_output_dir()

    # ── Public API ──────────────────────────────────────────────────────

    async def generate(
        self, *, run_date: Optional[datetime] = None
    ) -> NightlyReportSummary:
        """Build the report for the window ending at ``run_date``.

        Args:
            run_date: Upper bound of the aggregation window (UTC).
                Defaults to ``datetime.now(timezone.utc)``.

        Returns:
            The freshly generated :class:`NightlyReportSummary`. The
            corresponding Markdown file has been written to
            ``output_dir`` and (when ``db`` is non-None) the summary has
            been upserted into the ``strategy_run_reports`` collection.
        """
        end = _ensure_utc(run_date) or _utcnow()
        start = end - timedelta(hours=self.lookback_hours)
        report_date = end.date().isoformat()

        run_stats = await self._aggregate_runs(start, end)
        eval_stats = await self._aggregate_evaluation_scores(start, end)
        scheduler_stats = await self._aggregate_scheduler_results(start, end)
        baseline = await self._aggregate_baseline(end)
        regressions = self._detect_regressions(eval_stats["per_pair"], baseline)
        # Augment regressions with mode-specific trigger labels per
        # Blueprint 05 §6.4 — RegressionMode publishes the canonical list.
        regression_triggers = RegressionMode().regression_triggers()
        for entry in regressions:
            entry.setdefault("mode", "regression")
            entry.setdefault("triggers", list(regression_triggers))
        schedule_stats = await self._aggregate_schedule_health()

        winning = self._winning_strategies_by_capability(eval_stats["per_pair"])
        latency_cost = await self._latency_cost_table(
            run_stats["per_strategy_latencies"]
        )
        retrieval_table = self._retrieval_quality_table(eval_stats["docs"])
        citation_table = self._citation_quality_table(eval_stats["docs"])
        cases_needing_labels = self._cases_needing_better_gold_labels(
            eval_stats["docs"]
        )

        promotion_breakdown: list[PromotionCandidateSummary] = []
        try:
            promotion_breakdown = await PromotionEvaluator().evaluate_all(
                db=self.db, since=start, until=end
            )
        except Exception as exc:  # noqa: BLE001 - never crash the report
            logger.warning(
                "PromotionEvaluator.evaluate_all failed (treating as empty): %s",
                exc,
            )
        recommended_promotions = [
            c for c in promotion_breakdown if c.recommendation == "recommended"
        ]

        deprecations = await self._compute_recommended_deprecations(
            current_per_strategy=eval_stats["per_strategy"],
            fatal_failures=run_stats["fatal_failures"],
            window_start=start,
            window_end=end,
        )

        summary = NightlyReportSummary(
            report_date=report_date,
            window_start=start,
            window_end=end,
            total_runs=run_stats["total"],
            success_count=run_stats["success"],
            failure_count=run_stats["failed"],
            p50_latency_ms=run_stats["p50_ms"],
            p95_latency_ms=run_stats["p95_ms"],
            mean_composite_score=eval_stats["mean_score"],
            evaluation_count=eval_stats["count"],
            scheduler_candidate_count=scheduler_stats["count"],
            scheduler_mean_candidate_score=scheduler_stats["mean_score"],
            top_failing_nodes=run_stats["top_failing_nodes"],
            regressions=regressions,
            winning_strategies_by_capability=winning,
            fatal_failures=run_stats["fatal_failures"],
            latency_cost_table=latency_cost,
            retrieval_quality_table=retrieval_table,
            citation_quality_table=citation_table,
            recommended_promotions=recommended_promotions,
            promotion_evaluation_breakdown=promotion_breakdown,
            recommended_deprecations=deprecations,
            cases_needing_better_gold_labels=cases_needing_labels,
            paused_schedules_count=schedule_stats["paused"],
            open_circuit_breakers_count=schedule_stats["open_breakers"],
            schedules_at_failure_threshold=schedule_stats["at_failure_threshold"],
        )

        markdown_path = self._write_markdown(summary)
        summary.markdown_path = markdown_path

        # F5 / Task 87: write the JSON sibling next to the Markdown report.
        # Best-effort — Markdown success is not invalidated by JSON failure.
        # Assign the path on the summary BEFORE serializing so the file
        # itself is self-describing (contains its own ``json_path``).
        json_path = self.output_dir / f"{summary.report_date}.json"
        summary.json_path = json_path
        try:
            self._write_json(summary, json_path)
        except Exception as exc:  # noqa: BLE001 - best-effort sibling write
            logger.warning(
                "Failed to write JSON sibling report at %s (non-fatal): %s",
                json_path,
                exc,
            )
            summary.json_path = None

        await self._persist_summary(summary)

        logger.info(
            "Nightly report written: md=%s, json=%s",
            markdown_path,
            summary.json_path,
        )
        logger.info(
            "Nightly report generated for %s: runs=%d successes=%d failures=%d "
            "regressions=%d",
            report_date,
            summary.total_runs,
            summary.success_count,
            summary.failure_count,
            len(summary.regressions),
        )
        return summary

    # ── Aggregation helpers ─────────────────────────────────────────────

    async def _aggregate_runs(
        self, start: datetime, end: datetime
    ) -> dict[str, Any]:
        """Aggregate :data:`STRATEGY_RUN_COLLECTION_NAME` over ``[start, end]``."""
        empty: dict[str, Any] = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "top_failing_nodes": [],
            "docs": [],
            "per_strategy_latencies": {},
            "fatal_failures": [],
        }
        if self.db is None:
            return empty

        # Project only the fields we need; leave node_outputs since it can
        # be unbounded but is required for failing-node ranking.
        match: dict[str, Any] = {
            "started_at": {"$gte": start, "$lte": end},
        }
        docs: list[dict[str, Any]] = []
        try:
            cursor = self.db[STRATEGY_RUN_COLLECTION_NAME].find(match)
            async for doc in cursor:
                docs.append(doc)
        except Exception as exc:  # noqa: BLE001 - tolerate missing collection
            logger.warning(
                "Failed to read %s (treating as empty): %s",
                STRATEGY_RUN_COLLECTION_NAME,
                exc,
            )
            return empty

        if not docs:
            return empty

        success = sum(1 for d in docs if d.get("status") == "success")
        failed = sum(
            1
            for d in docs
            if d.get("status") in ("failed", "error", "timed_out")
        )
        durations = [
            float(d["duration_ms"])
            for d in docs
            if isinstance(d.get("duration_ms"), (int, float))
        ]

        # Top failing nodes: count node_outputs entries with non-success status.
        failing_counter: Counter[str] = Counter()
        for d in docs:
            for output in d.get("node_outputs") or []:
                if not isinstance(output, dict):
                    continue
                if output.get("status") not in ("success", "skipped", "empty"):
                    node_id = output.get("node_id")
                    if isinstance(node_id, str):
                        failing_counter[node_id] += 1
        top_failing = [
            {"node_id": node_id, "failure_count": count}
            for node_id, count in failing_counter.most_common(5)
        ]

        per_strategy_latencies: dict[str, list[float]] = defaultdict(list)
        fatal_failures: list[dict[str, Any]] = []
        for d in docs:
            sid = d.get("strategy_id") or "unknown"
            dur = d.get("duration_ms")
            if isinstance(dur, (int, float)):
                per_strategy_latencies[sid].append(float(dur))
            status = d.get("status")
            halt = d.get("halt_reason")
            is_fatal = status == "fatal" or status == "hard_limit_breach" or (
                isinstance(halt, str) and "hard_limit" in halt.lower()
            )
            if is_fatal:
                fatal_failures.append(
                    {
                        "strategy_id": sid,
                        "dataset_id": d.get("dataset_id"),
                        "case_id": d.get("case_id") or d.get("test_case_id"),
                        "reason": halt or status or "fatal",
                    }
                )

        return {
            "total": len(docs),
            "success": success,
            "failed": failed,
            "p50_ms": _percentile(durations, 50.0),
            "p95_ms": _percentile(durations, 95.0),
            "top_failing_nodes": top_failing,
            "docs": docs,
            "per_strategy_latencies": dict(per_strategy_latencies),
            "fatal_failures": fatal_failures,
        }

    async def _aggregate_evaluation_scores(
        self, start: datetime, end: datetime
    ) -> dict[str, Any]:
        """Aggregate the ``evaluation_results`` collection.

        The collection is named ``evaluation_results`` (not
        ``evaluation_scores``) — see
        :class:`backend.evaluation.runner.EvaluationRunner`.
        """
        empty: dict[str, Any] = {
            "count": 0,
            "mean_score": 0.0,
            "per_pair": {},
            "per_strategy": {},
            "docs": [],
        }
        if self.db is None:
            return empty

        match: dict[str, Any] = {
            "created_at": {"$gte": start, "$lte": end},
        }
        try:
            cursor = self.db["evaluation_results"].find(match)
            docs = [doc async for doc in cursor]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to read evaluation_results (treating as empty): %s", exc
            )
            return empty

        if not docs:
            return {**empty, "docs": []}

        scores: list[float] = []
        per_pair: dict[tuple[str, str], list[float]] = defaultdict(list)
        per_strategy: dict[str, list[float]] = defaultdict(list)
        for doc in docs:
            result = doc.get("result") or {}
            score = result.get("composite_score")
            if not isinstance(score, (int, float)):
                continue
            scores.append(float(score))
            strategy_id = doc.get("strategy_id") or "unknown"
            capability_id = doc.get("capability_id") or "unknown"
            per_pair[(strategy_id, capability_id)].append(float(score))
            per_strategy[strategy_id].append(float(score))

        mean_per_pair: dict[tuple[str, str], float] = {
            key: statistics.fmean(values) for key, values in per_pair.items()
        }
        return {
            "count": len(scores),
            "mean_score": statistics.fmean(scores) if scores else 0.0,
            "per_pair": mean_per_pair,
            "per_strategy": dict(per_strategy),
            "docs": docs,
        }

    async def _aggregate_scheduler_results(
        self, start: datetime, end: datetime
    ) -> dict[str, Any]:
        """Aggregate per-candidate scores from ``scheduler_results``."""
        empty = {"count": 0, "mean_score": 0.0}
        if self.db is None:
            return empty

        match: dict[str, Any] = {
            "completed_at": {"$gte": start, "$lte": end},
        }
        try:
            cursor = self.db["scheduler_results"].find(match)
            docs = [doc async for doc in cursor]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to read scheduler_results (treating as empty): %s", exc
            )
            return empty

        scores = [
            float(d["composite_score"])
            for d in docs
            if isinstance(d.get("composite_score"), (int, float))
        ]
        return {
            "count": len(scores),
            "mean_score": statistics.fmean(scores) if scores else 0.0,
        }

    async def _aggregate_baseline(
        self, end: datetime
    ) -> dict[tuple[str, str], float]:
        """Pull the prior-week composite-score baseline.

        Window: ``[end - 7d, end - 6d]`` against ``scheduler_reports``.
        Returns a mapping of ``(strategy_id, capability_id)`` to the
        average composite score across that 24h baseline slice.
        """
        if self.db is None:
            return {}
        baseline_end = end - timedelta(days=6)
        baseline_start = end - timedelta(days=7)

        try:
            cursor = self.db["scheduler_reports"].find(
                {"completed_at": {"$gte": baseline_start, "$lte": baseline_end}}
            )
            docs = [doc async for doc in cursor]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to read scheduler_reports baseline (treating as empty): %s",
                exc,
            )
            return {}

        accum: dict[tuple[str, str], list[float]] = defaultdict(list)
        for doc in docs:
            capability_id = doc.get("capability_id") or "unknown"
            for ranking in doc.get("candidate_rankings") or []:
                if not isinstance(ranking, dict):
                    continue
                strategy_id = ranking.get("strategy_id")
                score = ranking.get("score")
                if not isinstance(strategy_id, str):
                    continue
                if not isinstance(score, (int, float)):
                    continue
                accum[(strategy_id, capability_id)].append(float(score))

        return {
            key: statistics.fmean(values) for key, values in accum.items() if values
        }

    def _detect_regressions(
        self,
        current_per_pair: dict[tuple[str, str], float],
        baseline_per_pair: dict[tuple[str, str], float],
    ) -> list[dict[str, Any]]:
        """Flag pairs whose current score dropped past the threshold."""
        regressions: list[dict[str, Any]] = []
        for key, current in current_per_pair.items():
            baseline = baseline_per_pair.get(key)
            if baseline is None:
                continue
            delta = current - baseline
            if delta < -self.regression_threshold:
                strategy_id, capability_id = key
                regressions.append(
                    {
                        "strategy_id": strategy_id,
                        "capability_id": capability_id,
                        "current_score": round(current, 4),
                        "baseline_score": round(baseline, 4),
                        "delta": round(delta, 4),
                    }
                )
        regressions.sort(key=lambda r: r["delta"])
        return regressions

    # ── §10 section helpers (Phase 6 / Task 79 / P10) ───────────────────

    @staticmethod
    def _winning_strategies_by_capability(
        per_pair: dict[tuple[str, str], float],
    ) -> dict[str, dict[str, Any]]:
        """Pick the highest-mean strategy per capability.

        Args:
            per_pair: ``(strategy_id, capability_id) -> mean_score``.

        Returns:
            Mapping of ``capability_id -> {strategy_id, mean_score,
            sample_count}``. ``sample_count`` is set to ``1`` here as a
            placeholder; the real count lives in the per-strategy doc
            list, which we re-derive when callers need it.
        """
        winners: dict[str, dict[str, Any]] = {}
        for (strategy_id, capability_id), score in per_pair.items():
            current = winners.get(capability_id)
            if current is None or float(score) > float(current["mean_score"]):
                winners[capability_id] = {
                    "strategy_id": strategy_id,
                    "mean_score": round(float(score), 6),
                    "sample_count": 1,
                }
        return winners

    async def _latency_cost_table(
        self, per_strategy_latencies: dict[str, list[float]]
    ) -> list[dict[str, Any]]:
        """Build the latency / cost rollup per strategy.

        ``mean_cost_eur`` is sourced from
        :class:`~backend.agent.strategy.runtime_profile_store.RuntimeModelProfile.cost_eur`
        via :meth:`_mean_cost_per_strategy` (typed read; populated by the
        runtime profiler with provider billing metadata or estimated
        from ``ModelRoleConfig`` rates per F4 / Task 82). Strategies
        without a numeric cost surface ``None``, which the markdown
        formatter renders as ``—``. The table sorts by
        ``latency_p95_ms`` descending so the worst offenders are
        surfaced first.
        """
        cost_lookup = await self._mean_cost_per_strategy()
        rows: list[dict[str, Any]] = []
        for strategy_id, latencies in per_strategy_latencies.items():
            if not latencies:
                continue
            rows.append(
                {
                    "strategy_id": strategy_id,
                    "latency_p50_ms": round(_percentile(latencies, 50.0), 3),
                    "latency_p95_ms": round(_percentile(latencies, 95.0), 3),
                    "total_runs": len(latencies),
                    "mean_cost_eur": cost_lookup.get(strategy_id),
                }
            )
        rows.sort(key=lambda r: r["latency_p95_ms"], reverse=True)
        return rows

    async def _mean_cost_per_strategy(self) -> dict[str, Optional[float]]:
        """Return ``strategy_id -> mean cost`` from runtime profiles.

        Reads :attr:`RuntimeModelProfile.cost_eur` directly via typed
        validation (no ``getattr``/extras lookup): the field is
        populated by
        :class:`~backend.services.runtime_profiler.RuntimeProfiler` from
        provider billing metadata when available, else estimated from
        ``ModelRoleConfig`` cost rates, else ``None`` (F4 / Task 82).

        ``strategy_id`` is read from ``RuntimeModelProfile.model_extra``
        because it is an optional cross-cutting tag rather than a core
        profile field. Profiles missing both ``cost_eur`` and a
        ``strategy_id`` extra are silently skipped, mirroring the prior
        best-effort contract.
        """
        out: dict[str, Optional[float]] = {}
        if self.db is None:
            return out
        try:
            cursor = self.db["runtime_model_profiles"].find({})
            buckets: dict[str, list[float]] = defaultdict(list)
            async for doc in cursor:
                raw = dict(doc)
                raw.pop("_id", None)
                try:
                    profile = RuntimeModelProfile.model_validate(raw)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "Skipping malformed runtime profile in cost rollup: %s",
                        exc,
                    )
                    continue
                cost = profile.cost_eur
                if cost is None:
                    continue
                extras = profile.model_extra or {}
                strategy_id = extras.get("strategy_id") or extras.get("id")
                if isinstance(strategy_id, str):
                    buckets[strategy_id].append(float(cost))
            for sid, values in buckets.items():
                out[sid] = statistics.fmean(values) if values else None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to read runtime_model_profiles cost (treating as empty): %s",
                exc,
            )
        return out

    @staticmethod
    def _retrieval_quality_table(
        eval_docs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Aggregate ``retrieval_precision`` / ``top_k`` per strategy.

        Reads ``metrics.retrieval_precision`` and ``metrics.top_k_used``
        when present; emits a row per strategy that produced at least
        one numeric precision sample.
        """
        precisions: dict[str, list[float]] = defaultdict(list)
        top_ks: dict[str, list[int]] = defaultdict(list)
        for doc in eval_docs:
            sid = doc.get("strategy_id") or "unknown"
            metrics = doc.get("metrics") if isinstance(doc.get("metrics"), dict) else {}
            precision = metrics.get("retrieval_precision")
            if isinstance(precision, (int, float)):
                precisions[sid].append(float(precision))
            top_k = metrics.get("top_k_used") or metrics.get("top_k")
            if isinstance(top_k, (int, float)):
                top_ks[sid].append(int(top_k))
        rows: list[dict[str, Any]] = []
        for sid, values in precisions.items():
            rows.append(
                {
                    "strategy_id": sid,
                    "retrieval_precision_mean": round(
                        statistics.fmean(values), 6
                    ),
                    "top_k_used": (
                        int(round(statistics.fmean(top_ks[sid])))
                        if top_ks.get(sid)
                        else None
                    ),
                    "sample_count": len(values),
                }
            )
        rows.sort(key=lambda r: r["retrieval_precision_mean"], reverse=True)
        return rows

    @staticmethod
    def _citation_quality_table(
        eval_docs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Aggregate citation coverage / accuracy per strategy.

        ``citation_coverage`` and ``citation_accuracy`` are read from
        the optional ``metrics`` mapping; falls back to the
        ``citation_quality`` dimension score for coverage so that
        legacy evaluation rows still produce a row.
        """
        coverages: dict[str, list[float]] = defaultdict(list)
        accuracies: dict[str, list[float]] = defaultdict(list)
        for doc in eval_docs:
            sid = doc.get("strategy_id") or "unknown"
            metrics = doc.get("metrics") if isinstance(doc.get("metrics"), dict) else {}
            cov = metrics.get("citation_coverage")
            if not isinstance(cov, (int, float)):
                # Fall back to the citation_quality dimension score.
                result = doc.get("result") or {}
                for dim in result.get("dimension_scores") or []:
                    if (
                        isinstance(dim, dict)
                        and dim.get("dimension_id") == "citation_quality"
                        and isinstance(dim.get("score"), (int, float))
                    ):
                        cov = float(dim["score"])
                        break
            if isinstance(cov, (int, float)):
                coverages[sid].append(float(cov))
            acc = metrics.get("citation_accuracy")
            if isinstance(acc, (int, float)):
                accuracies[sid].append(float(acc))
        rows: list[dict[str, Any]] = []
        for sid, values in coverages.items():
            rows.append(
                {
                    "strategy_id": sid,
                    "citation_coverage_mean": round(
                        statistics.fmean(values), 6
                    ),
                    "citation_accuracy_mean": (
                        round(statistics.fmean(accuracies[sid]), 6)
                        if accuracies.get(sid)
                        else None
                    ),
                    "sample_count": len(values),
                }
            )
        rows.sort(key=lambda r: r["citation_coverage_mean"], reverse=True)
        return rows

    @staticmethod
    def _cases_needing_better_gold_labels(
        eval_docs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Heuristic for cases whose gold labels look weak.

        Formula (documented for P12 verification):

            For each (dataset_id, case_id):
                * Collect every strategy's composite_score in the
                  window.
                * Flag the case when ``max(score) < 0.5`` (no strategy
                  could solve it) — this is the primary signal.
                * Also flag when ``judge_variance`` is recorded and
                  the per-case mean ``judge_variance`` exceeds 0.25;
                  this is a *placeholder* until calibration data is
                  wired in (P12 / future work).

        Returns:
            List of ``{dataset_id, case_id, reason}`` rows. Empty when
            no case meets the criteria.
        """
        per_case_scores: dict[tuple[str, str], list[float]] = defaultdict(list)
        per_case_variance: dict[tuple[str, str], list[float]] = defaultdict(list)
        for doc in eval_docs:
            dataset_id = (
                doc.get("dataset_id") or doc.get("test_case_dataset_id") or "default"
            )
            case_id = doc.get("test_case_id") or doc.get("case_id")
            if not isinstance(case_id, str):
                continue
            result = doc.get("result") or {}
            score = result.get("composite_score")
            if isinstance(score, (int, float)):
                per_case_scores[(dataset_id, case_id)].append(float(score))
            variance = doc.get("judge_variance")
            if isinstance(variance, (int, float)):
                per_case_variance[(dataset_id, case_id)].append(float(variance))
        flagged: list[dict[str, Any]] = []
        for key, scores in per_case_scores.items():
            dataset_id, case_id = key
            reasons: list[str] = []
            if scores and max(scores) < 0.5:
                reasons.append("all_strategies_below_0.5")
            variances = per_case_variance.get(key) or []
            if variances and statistics.fmean(variances) > 0.25:
                reasons.append("high_judge_variance")
            if reasons:
                flagged.append(
                    {
                        "dataset_id": dataset_id,
                        "case_id": case_id,
                        "reason": ",".join(reasons),
                    }
                )
        flagged.sort(key=lambda r: (r["dataset_id"], r["case_id"]))
        return flagged

    async def _compute_recommended_deprecations(
        self,
        *,
        current_per_strategy: dict[str, list[float]],
        fatal_failures: list[dict[str, Any]],
        window_start: datetime,
        window_end: datetime,
    ) -> list[dict[str, Any]]:
        """Identify active strategies recommended for deprecation.

        Formula (documented for P12 verification):

            current_window  = [window_start, window_end]
            current30_start = window_end - 30 days
            prior30_start   = window_end - 60 days
            prior30_end     = current30_start

            For each strategy in current_per_strategy:
                current_mean = mean(scores in window)
                current30 = mean(evaluation_results.composite_score in
                                 [current30_start, window_end])
                prior30   = mean(evaluation_results.composite_score in
                                 [prior30_start, prior30_end])
                If current30 / prior30 < 0.9 (i.e. >10% drop) -> flag.
                If any fatal failure exists for the strategy in the
                current window -> flag.

        Returns one row per flagged strategy with the reason and the
        observed deltas (rounded to 4 decimal places).
        """
        rows: list[dict[str, Any]] = []
        fatal_by_strategy: dict[str, int] = defaultdict(int)
        for entry in fatal_failures:
            sid = entry.get("strategy_id")
            if isinstance(sid, str):
                fatal_by_strategy[sid] += 1

        # 30-day windows.
        current30_start = window_end - timedelta(days=30)
        prior30_start = window_end - timedelta(days=60)
        prior30_end = current30_start

        current30 = await self._mean_score_window(current30_start, window_end)
        prior30 = await self._mean_score_window(prior30_start, prior30_end)

        candidates: set[str] = set(current_per_strategy.keys())
        candidates.update(current30.keys())
        candidates.update(fatal_by_strategy.keys())

        for sid in sorted(candidates):
            reasons: list[str] = []
            cur = current30.get(sid)
            pri = prior30.get(sid)
            delta_pct: Optional[float] = None
            if cur is not None and pri is not None and pri > 0:
                delta_pct = (cur - pri) / pri
                if delta_pct < -0.10:
                    reasons.append("score_drop_gt_10pct")
            if fatal_by_strategy.get(sid, 0) > 0:
                reasons.append("fatal_failure_in_window")
            if reasons:
                rows.append(
                    {
                        "strategy_id": sid,
                        "reasons": reasons,
                        "current_mean_score": (
                            round(cur, 4) if cur is not None else None
                        ),
                        "prior_mean_score": (
                            round(pri, 4) if pri is not None else None
                        ),
                        "score_delta_pct": (
                            round(delta_pct, 4) if delta_pct is not None else None
                        ),
                        "fatal_failure_count": fatal_by_strategy.get(sid, 0),
                    }
                )
        return rows

    async def _mean_score_window(
        self, start: datetime, end: datetime
    ) -> dict[str, float]:
        """Return ``strategy_id -> mean composite score`` over ``[start, end]``."""
        out: dict[str, float] = {}
        if self.db is None:
            return out
        try:
            cursor = self.db["evaluation_results"].find(
                {"created_at": {"$gte": start, "$lte": end}}
            )
            buckets: dict[str, list[float]] = defaultdict(list)
            async for doc in cursor:
                sid = doc.get("strategy_id")
                result = doc.get("result") or {}
                score = result.get("composite_score")
                if isinstance(sid, str) and isinstance(score, (int, float)):
                    buckets[sid].append(float(score))
            for sid, values in buckets.items():
                if values:
                    out[sid] = statistics.fmean(values)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to read evaluation_results window [%s, %s]: %s",
                start,
                end,
                exc,
            )
        return out

    async def _aggregate_schedule_health(self) -> dict[str, int]:
        """Roll up scheduler health stats via :class:`MongoSchedulerStore`."""
        empty = {"paused": 0, "open_breakers": 0, "at_failure_threshold": 0}
        if self.db is None:
            return empty

        # Imported lazily to avoid a circular import between
        # backend.agent.strategy and backend.scheduler at module load.
        from backend.agent.strategy.scheduler_store import MongoSchedulerStore

        try:
            store = MongoSchedulerStore(self.db)
            schedules = await store.list()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to read strategy_schedules (treating as empty): %s", exc
            )
            return empty

        paused = sum(1 for s in schedules if s.paused)
        open_breakers = sum(
            1 for s in schedules if (s.circuit_breaker_state or "").lower() == "open"
        )
        at_failure_threshold = sum(
            1
            for s in schedules
            if (s.consecutive_failures or 0) >= self.FAILURE_FLAG_THRESHOLD
        )
        return {
            "paused": paused,
            "open_breakers": open_breakers,
            "at_failure_threshold": at_failure_threshold,
        }

    # ── Output ──────────────────────────────────────────────────────────

    def _write_markdown(self, summary: NightlyReportSummary) -> Path:
        """Serialize ``summary`` to Markdown on disk and return the path."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{summary.report_date}.md"
        path.write_text(self._render_markdown(summary), encoding="utf-8")
        return path

    @staticmethod
    def _write_json(summary: NightlyReportSummary, path: Path) -> None:
        """Serialize ``summary`` to a JSON sibling file (F5 / Task 87).

        Uses ``model_dump(mode="json")`` so Pydantic-aware types (Path,
        datetime, Decimal, ...) are JSON-compatible, plus ``default=str``
        as a safety net for any residual non-serializable value. Indent
        of ``2`` keeps the file diff-friendly. Same overwrite-in-place
        semantics as the Markdown sibling: re-running for the same
        ``run_date`` rewrites the file.
        """
        payload: dict[str, Any] = summary.model_dump(mode="json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, default=str),
            encoding="utf-8",
        )

    @staticmethod
    def _render_markdown(summary: NightlyReportSummary) -> str:
        """Render a :class:`NightlyReportSummary` as plain Markdown."""
        success_rate = (
            summary.success_count / summary.total_runs
            if summary.total_runs
            else 0.0
        )
        lines: list[str] = []
        lines.append(f"# Strategy OS Nightly Report — {summary.report_date}")
        lines.append("")
        lines.append(
            f"_Window: {summary.window_start.isoformat()} → "
            f"{summary.window_end.isoformat()} (UTC)_"
        )
        lines.append("")

        # ── Summary ────────────────────────────────────────────────────
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- Total runs: **{summary.total_runs}**")
        lines.append(f"- Successful: **{summary.success_count}**")
        lines.append(f"- Failed: **{summary.failure_count}**")
        lines.append(f"- Success rate: **{success_rate:.1%}**")
        lines.append(f"- p50 latency: **{summary.p50_latency_ms:.1f} ms**")
        lines.append(f"- p95 latency: **{summary.p95_latency_ms:.1f} ms**")
        lines.append(
            f"- Mean composite score (evaluations, "
            f"n={summary.evaluation_count}): "
            f"**{summary.mean_composite_score:.4f}**"
        )
        lines.append(
            f"- Scheduler candidates evaluated: "
            f"**{summary.scheduler_candidate_count}** "
            f"(mean score {summary.scheduler_mean_candidate_score:.4f})"
        )
        lines.append("")

        # 2. Winning strategies by capability ────────────────────────
        lines.append("## Winning Strategies by Capability")
        lines.append("")
        if not summary.winning_strategies_by_capability:
            lines.append("_None this period._")
        else:
            lines.append("| Capability | Strategy | Mean Score | Samples |")
            lines.append("| --- | --- | --- | --- |")
            for cap_id, payload in sorted(
                summary.winning_strategies_by_capability.items()
            ):
                lines.append(
                    f"| {cap_id} | {payload.get('strategy_id')} | "
                    f"{float(payload.get('mean_score', 0.0)):.4f} | "
                    f"{int(payload.get('sample_count', 0))} |"
                )
        lines.append("")

        # ── Regressions ────────────────────────────────────────────────
        lines.append("## Regressions")
        lines.append("")
        if not summary.regressions:
            lines.append("_None this period._")
        else:
            lines.append("| Strategy | Capability | Current | Baseline | Δ | Triggers |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for entry in summary.regressions:
                triggers = ",".join(entry.get("triggers") or [])
                lines.append(
                    f"| {entry['strategy_id']} | {entry['capability_id']} | "
                    f"{entry['current_score']:.4f} | "
                    f"{entry['baseline_score']:.4f} | "
                    f"{entry['delta']:+.4f} | {triggers} |"
                )
        lines.append("")

        # 4. Fatal failures ──────────────────────────────────────────────
        lines.append("## Fatal Failures")
        lines.append("")
        if not summary.fatal_failures:
            lines.append("_None this period._")
        else:
            lines.append("| Strategy | Dataset | Case | Reason |")
            lines.append("| --- | --- | --- | --- |")
            for entry in summary.fatal_failures:
                lines.append(
                    f"| {entry.get('strategy_id', '')} | "
                    f"{entry.get('dataset_id', '') or ''} | "
                    f"{entry.get('case_id', '') or ''} | "
                    f"{entry.get('reason', '') or ''} |"
                )
        lines.append("")

        # 5. Latency / cost / resource table ───────────────────────────
        lines.append("## Latency / Cost / Resource Table")
        lines.append("")
        if not summary.latency_cost_table:
            lines.append("_None this period._")
        else:
            lines.append(
                "| Strategy | p50 (ms) | p95 (ms) | Runs | Mean cost (€) |"
            )
            lines.append("| --- | --- | --- | --- | --- |")
            for row in summary.latency_cost_table:
                cost = row.get("mean_cost_eur")
                cost_str = "—" if cost is None else f"{float(cost):.4f}"
                lines.append(
                    f"| {row['strategy_id']} | "
                    f"{float(row['latency_p50_ms']):.1f} | "
                    f"{float(row['latency_p95_ms']):.1f} | "
                    f"{int(row['total_runs'])} | {cost_str} |"
                )
        lines.append("")

        # 6. Retrieval quality table ────────────────────────────────────
        lines.append("## Retrieval Quality Table")
        lines.append("")
        if not summary.retrieval_quality_table:
            lines.append("_None this period._")
        else:
            lines.append("| Strategy | Precision | Top-K | Samples |")
            lines.append("| --- | --- | --- | --- |")
            for row in summary.retrieval_quality_table:
                top_k = row.get("top_k_used")
                top_k_str = "-" if top_k is None else str(int(top_k))
                lines.append(
                    f"| {row['strategy_id']} | "
                    f"{float(row['retrieval_precision_mean']):.4f} | "
                    f"{top_k_str} | {int(row['sample_count'])} |"
                )
        lines.append("")

        # 7. Citation quality table ───────────────────────────────────────
        lines.append("## Citation Quality Table")
        lines.append("")
        if not summary.citation_quality_table:
            lines.append("_None this period._")
        else:
            lines.append("| Strategy | Coverage | Accuracy | Samples |")
            lines.append("| --- | --- | --- | --- |")
            for row in summary.citation_quality_table:
                acc = row.get("citation_accuracy_mean")
                acc_str = "-" if acc is None else f"{float(acc):.4f}"
                lines.append(
                    f"| {row['strategy_id']} | "
                    f"{float(row['citation_coverage_mean']):.4f} | "
                    f"{acc_str} | {int(row['sample_count'])} |"
                )
        lines.append("")

        # 8. Recommended promotions ──────────────────────────────────────
        lines.append("## Recommended Promotions")
        lines.append("")
        if not summary.recommended_promotions:
            lines.append("_None this period._")
        else:
            lines.append(
                "| Strategy | Capability | Score (mean) | Δ vs default | Runs | p95 (ms) |"
            )
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for cand in summary.recommended_promotions:
                delta = cand.score_delta
                delta_str = "-" if delta is None else f"{delta:+.4f}"
                lines.append(
                    f"| {cand.strategy_id} | {cand.capability_id} | "
                    f"{cand.composite_score_mean:.4f} | {delta_str} | "
                    f"{cand.total_runs} | {cand.latency_p95_ms:.1f} |"
                )
        lines.append("")

        # 9. Recommended deprecations ─────────────────────────────────────
        lines.append("## Recommended Deprecations")
        lines.append("")
        if not summary.recommended_deprecations:
            lines.append("_None this period._")
        else:
            lines.append(
                "| Strategy | Reasons | Current | Prior | Δ% | Fatal |"
            )
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for row in summary.recommended_deprecations:
                reasons = ",".join(row.get("reasons") or [])
                cur = row.get("current_mean_score")
                pri = row.get("prior_mean_score")
                delta = row.get("score_delta_pct")
                cur_s = "-" if cur is None else f"{float(cur):.4f}"
                pri_s = "-" if pri is None else f"{float(pri):.4f}"
                delta_s = "-" if delta is None else f"{float(delta) * 100:+.1f}%"
                lines.append(
                    f"| {row['strategy_id']} | {reasons} | {cur_s} | "
                    f"{pri_s} | {delta_s} | "
                    f"{int(row.get('fatal_failure_count', 0))} |"
                )
        lines.append("")

        # 10. Test cases needing better gold labels ──────────────────────
        lines.append("## Test Cases Needing Better Gold Labels")
        lines.append("")
        if not summary.cases_needing_better_gold_labels:
            lines.append("_None this period._")
        else:
            lines.append("| Dataset | Case | Reason |")
            lines.append("| --- | --- | --- |")
            for row in summary.cases_needing_better_gold_labels:
                lines.append(
                    f"| {row.get('dataset_id', '')} | "
                    f"{row.get('case_id', '')} | "
                    f"{row.get('reason', '')} |"
                )
        lines.append("")

        # ── Top failing nodes ──────────────────────────────────────────
        lines.append("## Top Failing Nodes")
        lines.append("")
        if not summary.top_failing_nodes:
            lines.append("_No failing nodes recorded in window._")
        else:
            lines.append("| Node | Failures |")
            lines.append("| --- | --- |")
            for entry in summary.top_failing_nodes:
                lines.append(f"| {entry['node_id']} | {entry['failure_count']} |")
        lines.append("")

        # ── Scheduler health ───────────────────────────────────────────
        lines.append("## Scheduler Health")
        lines.append("")
        lines.append(
            f"- Paused schedules: **{summary.paused_schedules_count}**"
        )
        lines.append(
            f"- OPEN circuit breakers: **{summary.open_circuit_breakers_count}**"
        )
        lines.append(
            f"- Schedules with ≥ "
            f"{NightlyReportGenerator.FAILURE_FLAG_THRESHOLD} consecutive failures: "
            f"**{summary.schedules_at_failure_threshold}**"
        )
        lines.append("")
        lines.append(f"_Generated at {summary.generated_at.isoformat()}_")
        lines.append("")
        return "\n".join(lines)

    async def _persist_summary(self, summary: NightlyReportSummary) -> None:
        """Upsert ``summary`` into :data:`RUN_REPORT_COLLECTION_NAME`.

        Adds a unique index on ``report_date`` so re-running the
        generator for the same date overwrites the prior row in place.
        """
        if self.db is None:
            return
        collection = self.db[RUN_REPORT_COLLECTION_NAME]
        try:
            result = collection.create_index("report_date", unique=True)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning(
                "create_index on %s.report_date failed (non-fatal): %s",
                RUN_REPORT_COLLECTION_NAME,
                exc,
            )

        payload = summary.model_dump(mode="python")
        # Path fields aren't BSON-encodable; stringify before persistence.
        for path_key in ("markdown_path", "json_path"):
            value = payload.get(path_key)
            if value is not None:
                payload[path_key] = str(value)
        try:
            await collection.replace_one(
                {"report_date": summary.report_date},
                payload,
                upsert=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to upsert nightly report for %s: %s",
                summary.report_date,
                exc,
            )

    # ── Internals ───────────────────────────────────────────────────────

    @staticmethod
    def _default_output_dir() -> Path:
        """Return the default ``data/reports/strategy/`` output directory.

        Resolved relative to the repo root (the parent of ``backend/``)
        so that running the generator from any working directory always
        lands under the canonical report tree.
        """
        repo_root = Path(__file__).resolve().parents[3]
        return repo_root / "data" / "reports" / "strategy"
