"""Strategy experiment runner (Phase 6 / Task 74 / P5).

Orchestrates the per-job execution of strategy experiments:

    candidate generation → strategy execution per case →
    evaluation → runtime profiling → result persistence.

Design:

* **Single-worker assumption.** This runner is invoked with a specific
  ``job_id`` it has been told to run; it does **not** poll the queue
  itself. A separate scheduler/orchestrator (P11 CLI / scheduler
  daemon, future task) is responsible for picking jobs out of
  :class:`~backend.agent.strategy.experiment_job_models.JobStatus.QUEUED`.
  Multi-worker claim semantics are deferred until a real concurrency
  story shows up.
* **Candidate set** is supplied either by an explicit ``mode_dispatcher``
  callable (P6 wires regression / exploration / smoke modes) or — in the
  default ``mode_dispatcher=None`` path — built from
  :attr:`StrategyExperimentJob.strategy_ids` resolved through a
  caller-provided ``spec_resolver``. This module deliberately does not
  implement the modes themselves.
* **Cooperative cancel** is checked between cases (a single strategy
  run is treated as atomic from the runner's POV).
* **Resource gating** is checked once per candidate (Jimmy's note:
  per-case is too expensive). A snapshot is also captured *after* each
  candidate so the resulting :class:`ExperimentResult` carries the
  matching ``resource_snapshot_ids``.
* **Runtime profiling** is invoked once per candidate, not once per
  case (avoids blowing up profile cost on a 100-case dataset).
* **Progress writes** are throttled to at most one write every
  ``progress_throttle_ms`` milliseconds (default 500ms).

Repo rule #3: no Motor APIs introduced. The runner uses the abstract
:class:`backend.agent.strategy.experiment_job_store.ExperimentJobStore`
interface, which has both an in-memory and a PyMongo-Async backend.

Open issues kicked downstream:

* P6 (Task 75): mode dispatcher shape — a callable
  ``(job, *, spec_resolver, candidate_generator_factory) -> list[StrategySpec]``.
* P10 (Task 79): the report generator should join on
  :attr:`JobResultSummary.results[*].candidate_strategy_id` +
  :attr:`StrategyExperimentJob.id` to assemble per-experiment views.
* P11 (Task 80): CLI surface needs ``--job-id``, ``--strategy-id``
  (repeatable), ``--dataset-id`` (repeatable), ``--tenant``,
  ``--mode`` and ``--dry-run`` to drive a one-off experiment.
"""
from __future__ import annotations

import logging
import re
import statistics
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.agent.strategy.experiment_job_models import (
    JobProgress,
    JobStatus,
    StrategyExperimentJob,
)
from backend.agent.strategy.experiment_job_store import ExperimentJobStore
from backend.agent.strategy.models import StrategySpec
from backend.agent.strategy.scheduler_models import ResourceLimits

logger = logging.getLogger(__name__)

__all__ = [
    "ExperimentResult",
    "JobResultSummary",
    "StrategyExperimentRunner",
    "InvalidJobStateError",
    "ExperimentResourceExceeded",
    "ModeDispatcher",
    "SpecResolver",
]


# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────


class InvalidJobStateError(Exception):
    """Raised when :meth:`StrategyExperimentRunner.run_job` rejects a job.

    Attributes:
        job_id: The offending job's identifier.
        expected_status: The status the runner required (always
            :attr:`~backend.agent.strategy.experiment_job_models.JobStatus.QUEUED`
            today).
        actual_status: The status the job was actually in.
    """

    def __init__(
        self,
        *,
        job_id: str,
        expected_status: JobStatus,
        actual_status: Optional[JobStatus],
    ) -> None:
        actual_repr = (
            getattr(actual_status, "value", actual_status)
            if actual_status is not None
            else "<missing>"
        )
        super().__init__(
            f"Job {job_id!r} not runnable: expected status="
            f"{expected_status.value}, actual={actual_repr}"
        )
        self.job_id = job_id
        self.expected_status = expected_status
        self.actual_status = actual_status


class ExperimentResourceExceeded(Exception):
    """Raised internally when a per-candidate resource pre-check fails.

    The runner catches this and transitions the job to ``PAUSED`` rather
    than letting the exception propagate.

    Attributes:
        reasons: Human-readable reason strings reported by
            :meth:`~backend.services.resource_snapshot.ResourceSnapshotCollector.is_safe_to_run`.
    """

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("Resource limits exceeded: " + "; ".join(reasons))
        self.reasons = list(reasons)


# ─────────────────────────────────────────────────────────────────────────────
# Result models
# ─────────────────────────────────────────────────────────────────────────────


class ExperimentResult(BaseModel):
    """Aggregated outcome of one ``(candidate, dataset)`` pair.

    Attributes:
        candidate_strategy_id: The strategy executed.
        candidate_version: Strategy spec version, if known.
        dataset_id: Dataset identifier the candidate was scored against.
        case_count: Total number of cases attempted (success + failure).
        composite_score_mean: Mean composite score across successful cases.
        composite_score_p50: Median composite score across successful cases.
        composite_score_p95: 95th-percentile composite score.
        latency_ms_p50: Median strategy run latency.
        latency_ms_p95: 95th-percentile strategy run latency.
        success_count: Cases where the strategy ran without exception.
        failure_count: Cases where strategy execution or scoring raised.
        fatal_failure_count: Cases where evaluation could not score at all.
        trace_ids: All :class:`RunTraceDoc` ids produced by this pair.
        runtime_profile_ids: Runtime-profile ids captured per candidate.
        resource_snapshot_ids: Resource-snapshot ids captured per candidate.
        metrics: Extra evaluator-specific aggregates (e.g. citation
            coverage, retrieval precision); shape is evaluator-defined.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_strategy_id: str
    candidate_version: Optional[str] = None
    dataset_id: str
    case_count: int = Field(default=0, ge=0)
    composite_score_mean: float = 0.0
    composite_score_p50: float = 0.0
    composite_score_p95: float = 0.0
    latency_ms_p50: float = 0.0
    latency_ms_p95: float = 0.0
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    fatal_failure_count: int = Field(default=0, ge=0)
    trace_ids: list[str] = Field(default_factory=list)
    runtime_profile_ids: list[str] = Field(default_factory=list)
    resource_snapshot_ids: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class JobResultSummary(BaseModel):
    """Top-level summary written to
    :attr:`StrategyExperimentJob.result_summary`.

    Attributes:
        total_runs: Sum of ``case_count`` across all results.
        completed_runs: Sum of ``success_count`` across all results.
        failed_runs: Sum of ``failure_count`` + ``fatal_failure_count``.
        winning_strategy: The candidate id with the highest
            ``composite_score_mean``, or ``None`` if no candidate
            produced any successful run.
        winning_score: The composite-score mean of the winner.
        results: Per-(candidate, dataset) records.
        duration_ms: Wall-clock duration of :meth:`run_job` in ms.
        resource_violations: Reason strings captured during execution.
    """

    model_config = ConfigDict(extra="forbid")

    total_runs: int = Field(default=0, ge=0)
    completed_runs: int = Field(default=0, ge=0)
    failed_runs: int = Field(default=0, ge=0)
    winning_strategy: Optional[str] = None
    winning_score: Optional[float] = None
    results: list[ExperimentResult] = Field(default_factory=list)
    duration_ms: int = Field(default=0, ge=0)
    resource_violations: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Type aliases
# ─────────────────────────────────────────────────────────────────────────────


#: ``mode_dispatcher(job, *, spec_resolver, candidate_generator_factory)``
#: returns the list of :class:`StrategySpec` candidates to execute. P6
#: wires grid / regression / smoke modes through this callable.
ModeDispatcher = Callable[..., Awaitable[list[StrategySpec]]]


#: ``spec_resolver(strategy_id)`` returns the live :class:`StrategySpec`
#: for ``strategy_id``. The default candidate path uses this on each
#: entry of :attr:`StrategyExperimentJob.strategy_ids`.
SpecResolver = Callable[[str], Awaitable[StrategySpec]]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


_EVO_GEN_RE = re.compile(r"__evo_(\d+)_")


def _extract_generation(candidate_strategy_id: str) -> int:
    """Return the evolution generation encoded in a candidate id.

    Children carry a ``__evo_<gen>_`` segment injected by
    :class:`EvolutionaryMutator`; seed-pool members (generation 0) carry
    no such segment, so they are bucketed under ``0``.

    Args:
        candidate_strategy_id: ``StrategySpec.strategy_id`` of the run.

    Returns:
        The integer generation index, defaulting to ``0`` when no marker
        is present or the marker fails to parse.
    """

    match = _EVO_GEN_RE.search(candidate_strategy_id)
    if match is None:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def _percentile(values: list[float], pct: float) -> float:
    """Return the percentile ``pct`` of ``values`` using linear interpolation.

    Args:
        values: Numeric samples; an empty list returns ``0.0``.
        pct: Percentile in the inclusive range ``[0, 100]``.

    Returns:
        The interpolated percentile, or ``0.0`` for empty input.
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    sorted_values = sorted(float(v) for v in values)
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lower_idx = int(rank)
    upper_idx = min(lower_idx + 1, len(sorted_values) - 1)
    fraction = rank - lower_idx
    return sorted_values[lower_idx] + fraction * (
        sorted_values[upper_idx] - sorted_values[lower_idx]
    )


def _resolve_synth_model(spec: StrategySpec) -> Optional[tuple[str, str]]:
    """Best-effort ``(provider, model)`` extraction from ``spec``.

    Looks at ``spec.model_roles`` for a ``"synth_model"`` (preferred) or
    ``"synthesis"`` entry. Values may be either bare model identifiers
    (returned with ``provider="default"``) or ``"<provider>/<model>"``
    / ``"<provider>:<model>"`` forms.

    Returns:
        ``(provider, model)`` if a synth role is configured; ``None``
        when no usable role is present, in which case the runner skips
        runtime profiling for that candidate.
    """
    roles = getattr(spec, "model_roles", {}) or {}
    raw = roles.get("synth_model") or roles.get("synthesis")
    if not raw:
        return None
    for sep in ("/", ":"):
        if sep in raw:
            provider, _, model = raw.partition(sep)
            if provider and model:
                return provider, model
    return "default", raw


def _coerce_resource_limits(job: StrategyExperimentJob) -> ResourceLimits:
    """Return :class:`ResourceLimits` derived from the job, or defaults.

    Resolution order (P6 / Task 75):

    1. The typed :attr:`StrategyExperimentJob.resource_limits` field —
       new code paths populate this at job-create time.
    2. The legacy ``job.result_summary['resource_limits']`` shim — kept
       for backwards compatibility with operators / tests written
       before the typed field landed.
    3. :class:`ResourceLimits` defaults.
    """
    if job.resource_limits is not None:
        return job.resource_limits
    summary = job.result_summary or {}
    raw = summary.get("resource_limits") if isinstance(summary, dict) else None
    if isinstance(raw, dict):
        try:
            return ResourceLimits.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - tolerate legacy shapes
            logger.warning(
                "Falling back to default ResourceLimits for job %s (%s)",
                job.id,
                exc,
            )
    return ResourceLimits()


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────


class StrategyExperimentRunner:
    """Drives a single :class:`StrategyExperimentJob` end-to-end.

    The runner is single-worker by design (see module docstring) — it
    is told *which* job to run and assumes a separate component already
    moved that job into ``QUEUED``.

    Args:
        job_store: Persistence backend for the experiment job lifecycle.
        run_trace_store: Trace store. The runner does not directly call
            this — :class:`StrategyRunner` already writes traces — but
            the handle is plumbed through so future enhancements (e.g.
            per-candidate trace diffs) can attach without re-wiring the
            constructor.
        runtime_profiler: Profiler used to capture one runtime profile
            per candidate.
        resource_snapshot_collector: Host-resource gating service.
        evaluation_runner: Object exposing
            ``async load_dataset(dataset_id) -> list[case]`` and
            ``async score(case, run_result) -> EvaluationScore``. The
            score object is duck-typed; the runner reads
            ``composite_score`` (float), ``latency_ms`` (optional
            float) and ``metrics`` (optional dict).
        strategy_runner_factory: Zero-arg callable returning a
            :class:`StrategyRunner` instance for one candidate. The
            runner expects ``await runner.run(spec=..., context=...,
            query=...) -> StrategyRunResult``.
        candidate_generator_factory: Optional zero-arg callable used by
            the P6 mode dispatcher; threaded through unchanged.
        spec_resolver: Optional ``async (strategy_id) -> StrategySpec``
            used in the default ``mode_dispatcher=None`` branch to
            materialize :attr:`StrategyExperimentJob.strategy_ids` into
            executable specs.
        spec_store: Optional :class:`SpecStore` handle. When supplied,
            and no explicit ``mode_dispatcher`` is passed, the runner
            looks up :attr:`StrategyExperimentJob.mode` via the P6
            :class:`ModeRegistry` and delegates to
            :meth:`ExplorationMode.materialize_candidates`.
        snapshot_poll_interval_s: Documented poll cadence (seconds) for
            mid-candidate ``latest()`` checks. Reserved for the future
            long-running candidate path; current implementation only
            performs the per-candidate pre-check and post-snapshot.
        progress_throttle_ms: Minimum gap between
            :meth:`ExperimentJobStore.update_progress` writes.
    """

    def __init__(
        self,
        *,
        job_store: ExperimentJobStore,
        run_trace_store: Any,
        runtime_profiler: Any,
        resource_snapshot_collector: Any,
        evaluation_runner: Any,
        strategy_runner_factory: Callable[[], Any],
        candidate_generator_factory: Optional[Callable[..., Any]] = None,
        spec_resolver: Optional[SpecResolver] = None,
        spec_store: Optional[Any] = None,
        snapshot_poll_interval_s: int = 30,
        progress_throttle_ms: int = 500,
    ) -> None:
        self._job_store = job_store
        self._run_trace_store = run_trace_store
        self._runtime_profiler = runtime_profiler
        self._resource_snapshot_collector = resource_snapshot_collector
        self._evaluation_runner = evaluation_runner
        self._strategy_runner_factory = strategy_runner_factory
        self._candidate_generator_factory = candidate_generator_factory
        self._spec_resolver = spec_resolver
        self._spec_store = spec_store
        self._snapshot_poll_interval_s = max(1, int(snapshot_poll_interval_s))
        self._progress_throttle_ms = max(0, int(progress_throttle_ms))

    # ── Public API ──────────────────────────────────────────────────────

    async def run_job(
        self,
        job_id: str,
        *,
        mode_dispatcher: Optional[ModeDispatcher] = None,
    ) -> StrategyExperimentJob:
        """Execute the job end-to-end and return its terminal record.

        Args:
            job_id: The job to run. Must currently be ``QUEUED``.
            mode_dispatcher: Optional async callable returning the
                candidate :class:`StrategySpec` list. When ``None`` the
                runner expands ``job.strategy_ids`` via
                ``spec_resolver``.

        Raises:
            InvalidJobStateError: ``job_id`` does not exist or is not in
                ``QUEUED`` state.

        Returns:
            The job record after the runner finishes (terminal state on
            success, ``PAUSED`` on resource breach, ``FAILED`` on a
            catastrophic error).
        """
        job = await self._job_store.get(job_id)
        if job is None or job.status != JobStatus.QUEUED:
            raise InvalidJobStateError(
                job_id=job_id,
                expected_status=JobStatus.QUEUED,
                actual_status=job.status if job is not None else None,
            )

        start_perf = time.perf_counter()
        try:
            job = await self._job_store.update_status(
                job_id, JobStatus.RUNNING
            )
        except Exception:
            logger.exception(
                "Failed to mark job %s RUNNING; bailing out.", job_id
            )
            raise

        results: list[ExperimentResult] = []
        violations: list[str] = []
        progress = JobProgress()
        last_progress_perf = 0.0
        cancelled = False
        paused = False
        failed_error: Optional[str] = None
        mode_obj: Optional[Any] = None

        try:
            candidates, mode_obj = await self._prepare_run(
                job, mode_dispatcher=mode_dispatcher
            )
            datasets = list(job.datasets or [])
            limits = _coerce_resource_limits(job)

            # Bandit allocation walks every dataset's case list and asks
            # the :class:`BanditMode` instance which arm to pull on each
            # iteration; the legacy candidate × case nested loop below
            # is bypassed entirely for that branch.
            from backend.agent.strategy.exploration_modes import (  # local: avoid import cycle on cold start
                BanditMode,
            )

            if isinstance(mode_obj, BanditMode):
                results, violations, last_progress_perf, cancelled, paused = (
                    await self._run_bandit_loop(
                        job=job,
                        mode=mode_obj,
                        candidates=candidates,
                        datasets=datasets,
                        limits=limits,
                        progress=progress,
                        last_progress_perf=last_progress_perf,
                    )
                )
            else:
                # Expand the (candidate × dataset × case) matrix lazily —
                # we set ``total_runs`` after we know each dataset's case
                # count.
                for candidate in candidates:
                    if cancelled or paused:
                        break

                    # Per-candidate resource pre-check.
                    safe, reasons = await self._resource_snapshot_collector.is_safe_to_run(
                        limits
                    )
                    if not safe:
                        violations.extend(reasons)
                        paused = True
                        break

                    for dataset_id in datasets:
                        if await self._is_externally_cancelled(job_id):
                            cancelled = True
                            break

                        dataset_cases = await self._evaluation_runner.load_dataset(
                            dataset_id
                        )
                        progress.current_strategy = candidate.strategy_id
                        progress.current_dataset = dataset_id
                        progress.total_runs += len(dataset_cases)

                        candidate_result, last_progress_perf, cancelled = await self._run_candidate(
                            candidate=candidate,
                            dataset_id=dataset_id,
                            cases=dataset_cases,
                            job_id=job_id,
                            progress=progress,
                            last_progress_perf=last_progress_perf,
                        )
                        results.append(candidate_result)
                        if cancelled:
                            break

                    # Per-candidate post-tasks: runtime profile + resource snapshot.
                    profile_id = await self._capture_runtime_profile(candidate)
                    if profile_id is not None and results:
                        results[-1].runtime_profile_ids.append(profile_id)

                    snapshot_id = await self._capture_resource_snapshot()
                    if snapshot_id is not None and results:
                        results[-1].resource_snapshot_ids.append(snapshot_id)

        except Exception as exc:  # noqa: BLE001 - propagate as FAILED
            failed_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Experiment job %s failed", job_id)

        # Evolutionary post-run hook (Task 77 / P8). The candidate ids
        # carry their generation index in a ``__evo_<gen>_`` segment so
        # we can group results without re-querying the state store.
        # Failures here are best-effort — they must never alter the
        # job's terminal status decision.
        if not failed_error and not cancelled and not paused:
            await self._maybe_record_evolution_results(
                mode_obj=mode_obj,
                results=results,
                job=job,
            )

        duration_ms = int((time.perf_counter() - start_perf) * 1000)
        summary = self._build_summary(
            results=results,
            duration_ms=duration_ms,
            violations=violations,
        )

        # Final state transition.
        if failed_error is not None:
            return await self._job_store.update_status(
                job_id, JobStatus.FAILED, error=failed_error
            )
        if cancelled:
            # The job was flipped to CANCELLED externally — that is
            # already terminal so re-issuing the same transition is
            # illegal. Re-read and return the live record.
            current = await self._job_store.get(job_id)
            if current is not None and current.status == JobStatus.CANCELLED:
                return current
            return await self._job_store.update_status(
                job_id, JobStatus.CANCELLED
            )
        if paused:
            current = await self._job_store.get(job_id)
            if current is not None and current.status == JobStatus.PAUSED:
                return current
            return await self._job_store.update_status(
                job_id, JobStatus.PAUSED
            )
        return await self._job_store.finalize(
            job_id,
            result_summary=summary.model_dump(mode="python"),
            completed_at=_utcnow(),
        )

    # ── Internal: candidate expansion ───────────────────────────────────

    async def _prepare_run(
        self,
        job: StrategyExperimentJob,
        *,
        mode_dispatcher: Optional[ModeDispatcher],
    ) -> tuple[list[StrategySpec], Optional[Any]]:
        """Resolve candidates plus the active :class:`ExplorationMode`.

        Returns ``(candidates, mode)``. ``mode`` is the live
        :class:`~backend.agent.strategy.exploration_modes.ExplorationMode`
        instance when the registry path materialized it (so the runner
        can route bandit allocation through ``isinstance`` checks);
        ``None`` when an explicit ``mode_dispatcher`` was passed or
        when the legacy ``spec_resolver`` expansion path applies.

        Resolution order:

        1. An explicit ``mode_dispatcher`` — bypasses the registry
           entirely. Used by older callers and tests.
        2. The P6 :class:`ModeRegistry` when both ``job.mode`` is
           registered and the runner was constructed with a
           ``spec_store`` handle. The registered
           :class:`ExplorationMode.materialize_candidates` is invoked.
        3. Legacy ``spec_resolver`` expansion of
           :attr:`StrategyExperimentJob.strategy_ids`.
        """
        if mode_dispatcher is not None:
            candidates = await mode_dispatcher(
                job,
                spec_resolver=self._spec_resolver,
                candidate_generator_factory=self._candidate_generator_factory,
            )
            return candidates, None

        if self._spec_store is not None:
            try:
                from backend.agent.strategy.exploration_modes import (
                    ModeRegistry,
                    UnknownExplorationModeError,
                )

                mode = ModeRegistry.get(job.mode)
            except UnknownExplorationModeError:
                logger.debug(
                    "job %s mode=%r is not registered; falling back to "
                    "spec_resolver expansion",
                    job.id,
                    job.mode,
                )
            else:
                candidates = await mode.materialize_candidates(
                    job,
                    spec_store=self._spec_store,
                    candidate_generator_factory=self._candidate_generator_factory,
                )
                return candidates, mode

        if not job.strategy_ids:
            return [], None
        if self._spec_resolver is None:
            raise InvalidJobStateError(
                job_id=job.id,
                expected_status=JobStatus.QUEUED,
                actual_status=job.status,
            )
        out: list[StrategySpec] = []
        for sid in job.strategy_ids:
            spec = await self._spec_resolver(sid)
            out.append(spec)
        return out, None

    # Backwards-compatible thin shim retained for any older subclasses
    # / tests that imported the name directly. New code should use
    # :meth:`_prepare_run`.
    async def _build_candidates(
        self,
        job: StrategyExperimentJob,
        *,
        mode_dispatcher: Optional[ModeDispatcher],
    ) -> list[StrategySpec]:
        """Return only the candidate list (drops the resolved mode handle)."""
        candidates, _mode = await self._prepare_run(
            job, mode_dispatcher=mode_dispatcher
        )
        return candidates

    # ── Internal: evolutionary post-run hook ───────────────────────────

    async def _maybe_record_evolution_results(
        self,
        *,
        mode_obj: Any,
        results: list[ExperimentResult],
        job: StrategyExperimentJob,
    ) -> None:
        """Persist per-generation fitness back to the evolution state store.

        For :class:`EvolutionaryMode` runs the candidate ids encode their
        generation in a ``__evo_<gen>_`` segment (gen 0 is the seed pool
        and has no segment). We aggregate the composite score per
        candidate across datasets, group by generation, and forward each
        bucket to :meth:`EvolutionaryMode.record_generation_results`.

        Failures are swallowed: this is a best-effort telemetry step and
        must not change the job's terminal status.

        Args:
            mode_obj: Resolved :class:`ExplorationMode` instance, or
                ``None`` when no mode was selected.
            results: All :class:`ExperimentResult`s produced by the run.
            job: The persisted experiment job (used for ``experiment_id``).
        """

        if mode_obj is None or not results:
            return
        try:
            from backend.agent.strategy.exploration_modes import (  # local: avoid import cycle
                EvolutionaryMode,
            )
        except Exception:  # noqa: BLE001
            return
        if not isinstance(mode_obj, EvolutionaryMode):
            return

        try:
            # candidate_strategy_id -> list[float] (composite_score_mean per dataset)
            per_candidate: dict[str, list[float]] = {}
            for result in results:
                bucket = per_candidate.setdefault(
                    result.candidate_strategy_id, []
                )
                bucket.append(float(result.composite_score_mean))

            # generation -> {candidate_id -> mean composite}
            per_generation: dict[int, dict[str, float]] = {}
            for cand_id, scores in per_candidate.items():
                gen = _extract_generation(cand_id)
                fitness = sum(scores) / len(scores) if scores else 0.0
                per_generation.setdefault(gen, {})[cand_id] = fitness

            for gen in sorted(per_generation):
                try:
                    await mode_obj.record_generation_results(
                        experiment_id=job.experiment_id,
                        generation=gen,
                        child_fitness=per_generation[gen],
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to record evolution results for experiment %s gen %s",
                        job.experiment_id,
                        gen,
                    )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Unexpected failure while finalising evolutionary mode for experiment %s",
                job.experiment_id,
            )

    # ── Internal: bandit allocation ─────────────────────────────────────

    async def _run_bandit_loop(
        self,
        *,
        job: StrategyExperimentJob,
        mode: Any,
        candidates: list[StrategySpec],
        datasets: list[str],
        limits: ResourceLimits,
        progress: JobProgress,
        last_progress_perf: float,
    ) -> tuple[
        list[ExperimentResult],
        list[str],
        float,
        bool,
        bool,
    ]:
        """Run the experiment using UCB1 arm-by-arm allocation.

        For each dataset:

        1. Run a single per-dataset resource pre-check.
        2. Walk the case list and ask
           :meth:`BanditMode.next_arm` which arm to pull on each
           iteration. ``None`` aborts the dataset (budget reached).
        3. After each successful score, forward the composite score
           through :meth:`BanditMode.record_result` so the bandit
           state store stays current.
        4. After the loop, emit one :class:`ExperimentResult` per
           ``(arm, dataset)`` pair that was actually pulled, plus
           per-arm runtime profile / resource snapshot ids.

        Returns:
            ``(results, violations, last_progress_perf, cancelled, paused)``.
        """
        results: list[ExperimentResult] = []
        violations: list[str] = []
        cancelled = False
        paused = False
        arm_index: dict[str, StrategySpec] = {
            c.strategy_id: c for c in candidates
        }
        if not arm_index:
            return results, violations, last_progress_perf, cancelled, paused

        total_pulls = 0
        per_pair: dict[tuple[str, str], dict[str, Any]] = {}

        for dataset_id in datasets:
            if cancelled or paused:
                break
            if await self._is_externally_cancelled(job.id):
                cancelled = True
                break

            safe, reasons = await self._resource_snapshot_collector.is_safe_to_run(
                limits
            )
            if not safe:
                violations.extend(reasons)
                paused = True
                break

            dataset_cases = await self._evaluation_runner.load_dataset(
                dataset_id
            )
            progress.current_dataset = dataset_id
            progress.total_runs += len(dataset_cases)

            for case_idx, case in enumerate(dataset_cases):
                if await self._is_externally_cancelled(job.id):
                    cancelled = True
                    break

                arm_id = await mode.next_arm(
                    experiment_id=job.experiment_id,
                    total_pulls_so_far=total_pulls,
                )
                if arm_id is None:
                    break
                candidate = arm_index.get(arm_id)
                if candidate is None:
                    logger.warning(
                        "BanditMode.next_arm returned unknown arm_id=%r; "
                        "skipping case %d of dataset %s",
                        arm_id,
                        case_idx,
                        dataset_id,
                    )
                    continue

                progress.current_strategy = arm_id
                progress.current_case_index = case_idx

                bucket = per_pair.setdefault(
                    (arm_id, dataset_id),
                    {
                        "scores": [],
                        "latencies": [],
                        "trace_ids": [],
                        "metrics_accum": {},
                        "success": 0,
                        "failure": 0,
                        "fatal": 0,
                        "case_count": 0,
                        "version": getattr(candidate, "version", None),
                    },
                )
                bucket["case_count"] += 1

                run_result, run_failed, trace_id = await self._run_single_case(
                    candidate=candidate, case=case
                )
                if trace_id is not None:
                    bucket["trace_ids"].append(trace_id)
                    try:
                        await self._job_store.append_trace_id(job.id, trace_id)
                    except Exception as exc:  # noqa: BLE001 - best effort
                        logger.warning(
                            "append_trace_id(%s, %s) failed: %s",
                            job.id,
                            trace_id,
                            exc,
                        )

                if run_failed or run_result is None:
                    bucket["failure"] += 1
                    progress.failed_runs += 1
                else:
                    try:
                        score = await self._evaluation_runner.score(
                            case, run_result
                        )
                    except Exception as exc:  # noqa: BLE001 - per-case fatal
                        bucket["fatal"] += 1
                        progress.failed_runs += 1
                        logger.warning(
                            "evaluation_runner.score raised for arm=%s "
                            "dataset=%s case_idx=%d: %s",
                            arm_id,
                            dataset_id,
                            case_idx,
                            exc,
                        )
                    else:
                        composite = float(
                            getattr(score, "composite_score", 0.0)
                        )
                        bucket["scores"].append(composite)
                        latency = getattr(score, "latency_ms", None)
                        if latency is None:
                            latency = float(
                                getattr(run_result, "total_duration_ms", 0.0)
                                or 0.0
                            )
                        bucket["latencies"].append(float(latency))
                        extra_metrics = getattr(score, "metrics", None) or {}
                        if isinstance(extra_metrics, dict):
                            for k, v in extra_metrics.items():
                                try:
                                    bucket["metrics_accum"].setdefault(
                                        k, []
                                    ).append(float(v))
                                except (TypeError, ValueError):
                                    continue
                        bucket["success"] += 1
                        progress.completed_runs += 1
                        try:
                            await mode.record_result(
                                experiment_id=job.experiment_id,
                                arm_id=arm_id,
                                reward=composite,
                            )
                        except Exception as exc:  # noqa: BLE001 - best effort
                            logger.warning(
                                "BanditMode.record_result(%s, %s) failed: %s",
                                job.experiment_id,
                                arm_id,
                                exc,
                            )

                total_pulls += 1
                last_progress_perf = await self._maybe_write_progress(
                    job_id=job.id,
                    progress=progress,
                    last_progress_perf=last_progress_perf,
                )

        for (arm_id, dataset_id), bucket in per_pair.items():
            scores: list[float] = bucket["scores"]
            latencies: list[float] = bucket["latencies"]
            metrics_summary: dict[str, Any] = {
                k: {
                    "mean": statistics.fmean(vals),
                    "p50": _percentile(vals, 50.0),
                    "p95": _percentile(vals, 95.0),
                    "count": len(vals),
                }
                for k, vals in bucket["metrics_accum"].items()
            }
            result = ExperimentResult(
                candidate_strategy_id=arm_id,
                candidate_version=bucket["version"],
                dataset_id=dataset_id,
                case_count=int(bucket["case_count"]),
                composite_score_mean=(
                    statistics.fmean(scores) if scores else 0.0
                ),
                composite_score_p50=_percentile(scores, 50.0),
                composite_score_p95=_percentile(scores, 95.0),
                latency_ms_p50=_percentile(latencies, 50.0),
                latency_ms_p95=_percentile(latencies, 95.0),
                success_count=int(bucket["success"]),
                failure_count=int(bucket["failure"]),
                fatal_failure_count=int(bucket["fatal"]),
                trace_ids=list(bucket["trace_ids"]),
                metrics=metrics_summary,
            )
            candidate = arm_index.get(arm_id)
            if candidate is not None:
                profile_id = await self._capture_runtime_profile(candidate)
                if profile_id is not None:
                    result.runtime_profile_ids.append(profile_id)
            snapshot_id = await self._capture_resource_snapshot()
            if snapshot_id is not None:
                result.resource_snapshot_ids.append(snapshot_id)
            results.append(result)

        return results, violations, last_progress_perf, cancelled, paused

    # ── Internal: per-candidate execution ───────────────────────────────

    async def _run_candidate(
        self,
        *,
        candidate: StrategySpec,
        dataset_id: str,
        cases: list[Any],
        job_id: str,
        progress: JobProgress,
        last_progress_perf: float,
    ) -> tuple[ExperimentResult, float, bool]:
        """Execute one ``(candidate, dataset)`` pair.

        Returns:
            Tuple ``(result, last_progress_perf, cancelled)``.
        """
        scores: list[float] = []
        latencies: list[float] = []
        trace_ids: list[str] = []
        metrics_accum: dict[str, list[float]] = {}
        success_count = 0
        failure_count = 0
        fatal_failure_count = 0
        cancelled = False

        for case_idx, case in enumerate(cases):
            if await self._is_externally_cancelled(job_id):
                cancelled = True
                break

            progress.current_case_index = case_idx
            run_result, run_failed, trace_id = await self._run_single_case(
                candidate=candidate, case=case
            )
            if trace_id is not None:
                trace_ids.append(trace_id)
                try:
                    await self._job_store.append_trace_id(job_id, trace_id)
                except Exception as exc:  # noqa: BLE001 - best effort
                    logger.warning(
                        "append_trace_id(%s, %s) failed: %s",
                        job_id,
                        trace_id,
                        exc,
                    )

            if run_failed or run_result is None:
                failure_count += 1
                progress.failed_runs += 1
            else:
                try:
                    score = await self._evaluation_runner.score(case, run_result)
                except Exception as exc:  # noqa: BLE001 - per-case fatal
                    fatal_failure_count += 1
                    progress.failed_runs += 1
                    logger.warning(
                        "evaluation_runner.score raised for candidate=%s "
                        "dataset=%s case_idx=%d: %s",
                        candidate.strategy_id,
                        dataset_id,
                        case_idx,
                        exc,
                    )
                else:
                    composite = float(getattr(score, "composite_score", 0.0))
                    scores.append(composite)
                    latency = getattr(score, "latency_ms", None)
                    if latency is None:
                        latency = float(
                            getattr(run_result, "total_duration_ms", 0.0) or 0.0
                        )
                    latencies.append(float(latency))
                    extra_metrics = getattr(score, "metrics", None) or {}
                    if isinstance(extra_metrics, dict):
                        for k, v in extra_metrics.items():
                            try:
                                metrics_accum.setdefault(k, []).append(float(v))
                            except (TypeError, ValueError):
                                continue
                    success_count += 1
                    progress.completed_runs += 1

            last_progress_perf = await self._maybe_write_progress(
                job_id=job_id,
                progress=progress,
                last_progress_perf=last_progress_perf,
            )

        # Aggregate.
        composite_mean = (
            statistics.fmean(scores) if scores else 0.0
        )
        composite_p50 = _percentile(scores, 50.0)
        composite_p95 = _percentile(scores, 95.0)
        latency_p50 = _percentile(latencies, 50.0)
        latency_p95 = _percentile(latencies, 95.0)
        metrics_summary: dict[str, Any] = {
            k: {
                "mean": statistics.fmean(vals),
                "p50": _percentile(vals, 50.0),
                "p95": _percentile(vals, 95.0),
                "count": len(vals),
            }
            for k, vals in metrics_accum.items()
        }

        result = ExperimentResult(
            candidate_strategy_id=candidate.strategy_id,
            candidate_version=getattr(candidate, "version", None),
            dataset_id=dataset_id,
            case_count=len(cases),
            composite_score_mean=float(composite_mean),
            composite_score_p50=float(composite_p50),
            composite_score_p95=float(composite_p95),
            latency_ms_p50=float(latency_p50),
            latency_ms_p95=float(latency_p95),
            success_count=success_count,
            failure_count=failure_count,
            fatal_failure_count=fatal_failure_count,
            trace_ids=trace_ids,
            metrics=metrics_summary,
        )
        return result, last_progress_perf, cancelled

    async def _run_single_case(
        self,
        *,
        candidate: StrategySpec,
        case: Any,
    ) -> tuple[Any, bool, Optional[str]]:
        """Run one case through a fresh :class:`StrategyRunner` instance.

        Returns ``(run_result, failed, trace_id)``. ``failed`` is
        ``True`` when the strategy runner raised; ``trace_id`` is the
        :attr:`RunTraceDoc.trace_id` value when extractable.
        """
        runner = self._strategy_runner_factory()
        query = getattr(case, "query", None) or getattr(case, "user_prompt", "")
        try:
            run_result = await runner.run(
                spec=candidate,
                query=query,
                context=self._build_context(case),
            )
        except TypeError:
            # Some test doubles ignore ``context`` — fall back to the
            # two-arg form.
            try:
                run_result = await runner.run(spec=candidate, query=query)
            except Exception as exc:  # noqa: BLE001 - per-case failure
                logger.warning(
                    "strategy_runner.run raised for candidate=%s: %s",
                    candidate.strategy_id,
                    exc,
                )
                return None, True, None
        except Exception as exc:  # noqa: BLE001 - per-case failure
            logger.warning(
                "strategy_runner.run raised for candidate=%s: %s",
                candidate.strategy_id,
                exc,
            )
            return None, True, None

        trace_id = self._extract_trace_id(run_result)
        return run_result, False, trace_id

    @staticmethod
    def _extract_trace_id(run_result: Any) -> Optional[str]:
        """Pluck ``trace_id`` from a :class:`StrategyRunResult` if present."""
        if run_result is None:
            return None
        state = getattr(run_result, "state", None)
        if state is None:
            return getattr(run_result, "trace_id", None)
        return getattr(state, "trace_id", None)

    @staticmethod
    def _build_context(case: Any) -> Any:
        """Construct a :class:`BusinessContext` for ``case``.

        Imported lazily so that the experiment runner can be imported
        in environments where the heavier strategy-models module is
        not yet wired (e.g. unit tests using stubbed strategy runners).
        """
        try:
            from backend.agent.strategy.models import BusinessContext

            tenant_id = getattr(case, "tenant_id", None) or "experiment"
            return BusinessContext(
                capability_id=getattr(case, "capability_id", None),
                tenant_id=tenant_id,
            )
        except Exception:  # noqa: BLE001 - test stubs may not need a context
            return None

    # ── Internal: progress + cancellation + resource ───────────────────

    async def _maybe_write_progress(
        self,
        *,
        job_id: str,
        progress: JobProgress,
        last_progress_perf: float,
    ) -> float:
        """Write progress only when the throttle window has elapsed."""
        now = time.perf_counter()
        elapsed_ms = (now - last_progress_perf) * 1000.0
        if last_progress_perf > 0.0 and elapsed_ms < self._progress_throttle_ms:
            return last_progress_perf
        snapshot = progress.model_copy(deep=True)
        snapshot.last_progress_at = _utcnow()
        try:
            await self._job_store.update_progress(job_id, snapshot)
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning("update_progress(%s) failed: %s", job_id, exc)
        return now

    async def _is_externally_cancelled(self, job_id: str) -> bool:
        """Return ``True`` when an external actor flipped the job to ``CANCELLED``."""
        try:
            current = await self._job_store.get(job_id)
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning("Cancellation poll failed for %s: %s", job_id, exc)
            return False
        if current is None:
            return False
        return current.status == JobStatus.CANCELLED

    async def _capture_runtime_profile(
        self, candidate: StrategySpec
    ) -> Optional[str]:
        """Capture one runtime profile per candidate. Best-effort."""
        resolved = _resolve_synth_model(candidate)
        if resolved is None:
            return None
        provider, model = resolved
        budgets = getattr(candidate, "budgets", None)
        ctx_tokens = int(getattr(budgets, "max_context_tokens", 4000) or 4000)
        out_tokens = int(getattr(budgets, "max_output_tokens", 500) or 500)
        try:
            profile = await self._runtime_profiler.run_test(
                model=model,
                provider=provider,
                test_name="evidence_synthesis",
                context_tokens=ctx_tokens,
                output_tokens=out_tokens,
            )
        except Exception as exc:  # noqa: BLE001 - never fail the run
            logger.warning(
                "runtime_profiler.run_test failed for candidate=%s: %s",
                candidate.strategy_id,
                exc,
            )
            return None
        return getattr(profile, "id", None)

    async def _capture_resource_snapshot(self) -> Optional[str]:
        """Capture and return the id of one snapshot. Best-effort."""
        try:
            snapshot = await self._resource_snapshot_collector.capture()
        except Exception as exc:  # noqa: BLE001 - never fail the run
            logger.warning("resource snapshot capture failed: %s", exc)
            return None
        return getattr(snapshot, "id", None)

    # ── Internal: summary aggregation ───────────────────────────────────

    @staticmethod
    def _build_summary(
        *,
        results: list[ExperimentResult],
        duration_ms: int,
        violations: list[str],
    ) -> JobResultSummary:
        """Aggregate per-pair results into a :class:`JobResultSummary`."""
        total_runs = sum(r.case_count for r in results)
        completed_runs = sum(r.success_count for r in results)
        failed_runs = sum(
            r.failure_count + r.fatal_failure_count for r in results
        )

        winning: Optional[ExperimentResult] = None
        for r in results:
            if r.success_count <= 0:
                continue
            if winning is None or r.composite_score_mean > winning.composite_score_mean:
                winning = r

        return JobResultSummary(
            total_runs=total_runs,
            completed_runs=completed_runs,
            failed_runs=failed_runs,
            winning_strategy=(
                winning.candidate_strategy_id if winning is not None else None
            ),
            winning_score=(
                winning.composite_score_mean if winning is not None else None
            ),
            results=results,
            duration_ms=duration_ms,
            resource_violations=list(violations),
        )
