"""Strategy exploration mode dispatcher (Phase 6 / Task 75 / P6).

Materializes the candidate :class:`StrategySpec` list for a
:class:`StrategyExperimentJob` based on its ``mode`` field. The runner
(P5, :class:`backend.agent.strategy.experiment_runner.StrategyExperimentRunner`)
calls :meth:`ExplorationMode.materialize_candidates` to obtain the list
of strategies to execute.

Three modes ship in P6 (Blueprint 05 §6):

* ``grid`` — :class:`GridMode` wraps Jay's
  :class:`backend.agent.strategy.candidate_generator.CandidateGenerator`
  with a YAML-loadable ``mutation_grid`` block (Blueprint 05 §6.1).
* ``regression`` — :class:`RegressionMode` re-runs the production
  strategies for the job's tenant/capability without mutation, with
  fatal-fail triggers per Blueprint 05 §6.4.
* ``smoke`` — :class:`SmokeMode` runs a single strategy across a small
  deterministic sample of the dataset.

Bandit (P7, Task 76) and evolutionary (P8, Task 77) modes register into
the same :class:`ModeRegistry` later. ``BanditMode`` will likely reuse
the candidate set produced by :class:`GridMode` (UCB allocates within an
existing pool); :class:`EvolutionaryMode` is expected to generate its
own pool. Both decisions are flagged here so P10's report can join on a
common candidate-id surface.

Repo rule #3: PyMongo Async only — this module performs no I/O of its
own and only relies on the abstract
:class:`backend.agent.strategy.spec_store.SpecStore` interface.
"""
from __future__ import annotations

import json
import logging
import math
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

import yaml

from backend.agent.strategy.bandit_state_store import (
    BanditArmState,
    BanditStateStore,
    InMemoryBanditStateStore,
)
from backend.agent.strategy.candidate_generator import CandidateGenerator
from backend.agent.strategy.evolution_state_store import (
    EvolutionGenerationState,
    EvolutionStateStore,
    InMemoryEvolutionStateStore,
)
from backend.agent.strategy.evolutionary_mutator import EvolutionaryMutator
from backend.agent.strategy.experiment_job_models import StrategyExperimentJob
from backend.agent.strategy.models import StrategySpec
from backend.agent.strategy.spec_store import SpecStore

logger = logging.getLogger(__name__)

__all__ = [
    "ExplorationMode",
    "GridMode",
    "RegressionMode",
    "SmokeMode",
    "BanditMode",
    "EvolutionaryMode",
    "ModeRegistry",
    "UnknownExplorationModeError",
    "MutationGridYAMLError",
    "DEFAULT_REGRESSION_THRESHOLDS",
    "DEFAULT_BANDIT_EXPLORATION_CONSTANT",
    "DEFAULT_BANDIT_WARMUP_PULLS_PER_ARM",
    "DEFAULT_EVOLUTION_GENERATIONS",
    "DEFAULT_EVOLUTION_TOP_K",
    "ParentPoolResolver",
]


# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────


class UnknownExplorationModeError(Exception):
    """Raised by :meth:`ModeRegistry.get` when the mode name is unregistered.

    Attributes:
        mode_name: The unknown mode string supplied by the caller.
    """

    def __init__(self, mode_name: str) -> None:
        super().__init__(
            f"Unknown exploration mode: {mode_name!r}. "
            f"Registered modes: {sorted(ModeRegistry._registry)}"
        )
        self.mode_name = mode_name


class MutationGridYAMLError(Exception):
    """Raised when a ``mutation_grid`` YAML file cannot be parsed.

    Attributes:
        path: The offending YAML file path.
        line: Best-effort line number reported by ``yaml.YAMLError`` or
            ``None`` when the parser did not surface a position.
    """

    def __init__(
        self,
        path: Path,
        message: str,
        *,
        line: Optional[int] = None,
    ) -> None:
        location = f"{path}" + (f":{line}" if line is not None else "")
        super().__init__(f"Failed to load mutation_grid YAML at {location}: {message}")
        self.path = path
        self.line = line


# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────


#: Default fatal-fail trigger thresholds for :class:`RegressionMode`.
#: Mirrors Blueprint 05 §6.4. Values are advisory; P10's report
#: generator is expected to load these from the same dict shape.
DEFAULT_REGRESSION_THRESHOLDS: dict[str, Any] = {
    "score_drop_pct": 10.0,
    "forbidden_source_leak": True,
    "citation_coverage_drop_pct": 10.0,
    "latency_hard_limit_breach_max_cases": 1,
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _snapshot_to_spec(snapshot: dict[str, Any]) -> StrategySpec:
    """Convert a :class:`SpecStore` snapshot dict to a :class:`StrategySpec`.

    The store hands back a wrapper of the form
    ``{"spec_data": {...}, "version": ..., "version_counter": ...}``;
    we hydrate the inner ``spec_data`` payload.

    Args:
        snapshot: Snapshot dict produced by
            :meth:`SpecStore.get` / :meth:`SpecStore.list`.

    Returns:
        The hydrated :class:`StrategySpec` instance.

    Raises:
        ValueError: ``snapshot`` is not in the expected shape.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be a dict")
    spec_data = snapshot.get("spec_data")
    if not isinstance(spec_data, dict):
        raise ValueError("snapshot is missing 'spec_data'")
    # Deep-copy through JSON to insulate the live snapshot.
    payload = json.loads(json.dumps(spec_data, default=str))
    return StrategySpec.model_validate(payload)


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────────────────────────────────────


class ExplorationMode(ABC):
    """Abstract base class for P6 exploration modes.

    Subclasses materialize the candidate :class:`StrategySpec` list the
    runner will execute. They optionally expose ``regression_triggers``
    so that P10's report generator can label fatal-fail outcomes.
    """

    @abstractmethod
    async def materialize_candidates(
        self,
        job: StrategyExperimentJob,
        *,
        spec_store: SpecStore,
        candidate_generator_factory: Optional[Callable[..., CandidateGenerator]] = None,
    ) -> list[StrategySpec]:
        """Return the candidate strategies to execute for ``job``.

        Args:
            job: The :class:`StrategyExperimentJob` being run.
            spec_store: Persistence handle used to resolve ids /
                production specs.
            candidate_generator_factory: Optional override producing a
                :class:`CandidateGenerator`. ``None`` means the mode
                uses the default constructor.

        Returns:
            A list of :class:`StrategySpec` objects ready to execute.
        """

    def regression_triggers(self) -> list[str]:
        """Return human-readable fatal-fail trigger names.

        The default implementation returns an empty list; subclasses
        that participate in regression labeling override this.
        """
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Grid mode
# ─────────────────────────────────────────────────────────────────────────────


class GridMode(ExplorationMode):
    """Cartesian-product mutation grid mode (Blueprint 05 §6.1).

    Wraps :class:`CandidateGenerator` with an inline ``axes`` dict or a
    YAML file at ``mutation_grid_yaml_path``. The expected YAML shape
    is::

        mutation_grid:
          nodes.n_retrieve.config.top_k: [10, 20, 30]
          budgets.max_llm_calls: [5, 10]

    Args:
        mutation_grid: Inline mapping of dotted-path → list-of-values
            axes. Mutually exclusive with ``mutation_grid_yaml_path``.
        max_candidates: Upper bound on the number of generated
            candidates. Defaults to ``12`` to match
            :class:`CandidateGenerator`.
        base_strategy_id: Optional override for the base spec id.
            Falls back to ``job.strategy_ids[0]``.
        mutation_grid_yaml_path: Path to a YAML file containing a
            ``mutation_grid`` mapping. Loaded lazily on first
            :meth:`materialize_candidates` call.
    """

    def __init__(
        self,
        *,
        mutation_grid: Optional[dict[str, list[Any]]] = None,
        max_candidates: int = 12,
        base_strategy_id: Optional[str] = None,
        mutation_grid_yaml_path: Optional[Path] = None,
    ) -> None:
        if mutation_grid is not None and mutation_grid_yaml_path is not None:
            raise ValueError(
                "GridMode accepts either 'mutation_grid' or "
                "'mutation_grid_yaml_path', not both"
            )
        self._mutation_grid = (
            dict(mutation_grid) if mutation_grid is not None else None
        )
        self._max_candidates = int(max_candidates)
        self._base_strategy_id = base_strategy_id
        self._mutation_grid_yaml_path = (
            Path(mutation_grid_yaml_path)
            if mutation_grid_yaml_path is not None
            else None
        )

    async def materialize_candidates(
        self,
        job: StrategyExperimentJob,
        *,
        spec_store: SpecStore,
        candidate_generator_factory: Optional[Callable[..., CandidateGenerator]] = None,
    ) -> list[StrategySpec]:
        """Generate grid candidates for ``job``.

        Resolves the base spec id (from constructor or
        ``job.strategy_ids[0]``), fetches it via ``spec_store``,
        instantiates a :class:`CandidateGenerator` with the configured
        axes, and returns its output. If no axes are configured the
        generator falls back to its built-in ``DEFAULT_AXES``.
        """
        base_id = self._base_strategy_id
        if base_id is None:
            if not job.strategy_ids:
                raise ValueError(
                    "GridMode requires either 'base_strategy_id' or a "
                    "non-empty job.strategy_ids[0] to seed the grid"
                )
            base_id = job.strategy_ids[0]

        snapshot = await spec_store.get(base_id)
        if snapshot is None:
            raise ValueError(
                f"GridMode could not resolve base strategy id {base_id!r} "
                "from the supplied spec_store"
            )
        base_spec = _snapshot_to_spec(snapshot)

        axes = self._resolve_axes()

        if candidate_generator_factory is not None:
            generator = candidate_generator_factory(
                base_spec=base_spec,
                axes=axes,
                max_candidates=self._max_candidates,
            )
        else:
            generator = CandidateGenerator(
                base_spec=base_spec,
                axes=axes,
                max_candidates=self._max_candidates,
            )
        return generator.generate()

    # ── Helpers ─────────────────────────────────────────────────────────

    def _resolve_axes(self) -> Optional[dict[str, list[Any]]]:
        """Return the axes dict to drive :class:`CandidateGenerator`.

        Priority: explicit inline ``mutation_grid`` → YAML path → ``None``
        (which lets :class:`CandidateGenerator` pick its built-in
        defaults).
        """
        if self._mutation_grid is not None:
            return dict(self._mutation_grid)
        if self._mutation_grid_yaml_path is not None:
            return self._load_yaml_axes(self._mutation_grid_yaml_path)
        return None

    @staticmethod
    def _load_yaml_axes(path: Path) -> dict[str, list[Any]]:
        """Parse a ``mutation_grid`` YAML file and return its axes.

        Raises :class:`MutationGridYAMLError` on any I/O or schema
        problem, surfacing the file path and best-effort line number.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MutationGridYAMLError(path, str(exc)) from exc
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            line = (mark.line + 1) if mark is not None else None
            raise MutationGridYAMLError(path, str(exc), line=line) from exc
        if not isinstance(data, dict):
            raise MutationGridYAMLError(
                path, "top-level YAML node must be a mapping"
            )
        grid = data.get("mutation_grid")
        if not isinstance(grid, dict):
            raise MutationGridYAMLError(
                path,
                "missing or non-mapping 'mutation_grid' top-level key",
            )
        axes: dict[str, list[Any]] = {}
        for key, value in grid.items():
            if not isinstance(key, str):
                raise MutationGridYAMLError(
                    path, f"axis key must be a string, got {type(key).__name__}"
                )
            if not isinstance(value, list):
                raise MutationGridYAMLError(
                    path,
                    f"axis '{key}' must map to a YAML sequence; got "
                    f"{type(value).__name__}",
                )
            axes[key] = list(value)
        return axes


# ─────────────────────────────────────────────────────────────────────────────
# Regression mode
# ─────────────────────────────────────────────────────────────────────────────


class RegressionMode(ExplorationMode):
    """Production-strategy regression mode (Blueprint 05 §6.4).

    No mutation: returns the active production strategies for the job.
    Candidates are picked in the following order:

    1. ``job.strategy_ids`` when non-empty — each id is resolved via
       :meth:`SpecStore.get`.
    2. Otherwise every snapshot in ``spec_store.list(status="active")``
       scoped to ``job.tenant``.

    The :meth:`regression_triggers` accessor lists the fatal-fail
    triggers P10's report generator should raise on; the actual
    threshold values live on :attr:`thresholds`.

    Args:
        regression_thresholds: Override the per-trigger threshold
            values. Merged on top of :data:`DEFAULT_REGRESSION_THRESHOLDS`.
    """

    #: Stable trigger labels used by the report generator.
    TRIGGER_LABELS: tuple[str, ...] = (
        "score_drop_exceeds_threshold",
        "forbidden_source_leak",
        "citation_coverage_drop",
        "latency_hard_limit_breach",
    )

    def __init__(
        self,
        *,
        regression_thresholds: Optional[dict[str, Any]] = None,
    ) -> None:
        merged: dict[str, Any] = dict(DEFAULT_REGRESSION_THRESHOLDS)
        if regression_thresholds:
            merged.update(regression_thresholds)
        self.thresholds: dict[str, Any] = merged

    async def materialize_candidates(
        self,
        job: StrategyExperimentJob,
        *,
        spec_store: SpecStore,
        candidate_generator_factory: Optional[Callable[..., CandidateGenerator]] = None,
    ) -> list[StrategySpec]:
        """Return the production strategies to regression-test."""
        del candidate_generator_factory  # unused — regression never mutates
        if job.strategy_ids:
            specs: list[StrategySpec] = []
            for sid in job.strategy_ids:
                snapshot = await spec_store.get(sid)
                if snapshot is None:
                    logger.warning(
                        "RegressionMode: skipping unknown strategy id %r", sid
                    )
                    continue
                specs.append(_snapshot_to_spec(snapshot))
            return specs

        snapshots = await spec_store.list(
            status="active", tenant_id=job.tenant
        )
        return [_snapshot_to_spec(s) for s in snapshots]

    def regression_triggers(self) -> list[str]:
        """Return human-readable fatal-fail trigger names."""
        return list(self.TRIGGER_LABELS)


# ─────────────────────────────────────────────────────────────────────────────
# Smoke mode
# ─────────────────────────────────────────────────────────────────────────────


class SmokeMode(ExplorationMode):
    """Single-strategy smoke mode (Blueprint 05 §6).

    Returns exactly one :class:`StrategySpec` and provides a
    deterministic dataset subsampler the runner uses to clip each
    dataset to the first ``sample_count`` cases.

    Args:
        sample_count: How many cases the runner should keep per
            dataset. Defaults to ``3``.
        sample_strategy_id: Optional strategy id override. Falls back
            to ``job.strategy_ids[0]``.
    """

    def __init__(
        self,
        *,
        sample_count: int = 3,
        sample_strategy_id: Optional[str] = None,
    ) -> None:
        if sample_count <= 0:
            raise ValueError("sample_count must be positive")
        self.sample_count = int(sample_count)
        self._sample_strategy_id = sample_strategy_id

    async def materialize_candidates(
        self,
        job: StrategyExperimentJob,
        *,
        spec_store: SpecStore,
        candidate_generator_factory: Optional[Callable[..., CandidateGenerator]] = None,
    ) -> list[StrategySpec]:
        """Return a single :class:`StrategySpec` for the smoke run."""
        del candidate_generator_factory
        target_id = self._sample_strategy_id
        if target_id is None:
            if not job.strategy_ids:
                raise ValueError(
                    "SmokeMode requires either 'sample_strategy_id' or a "
                    "non-empty job.strategy_ids[0]"
                )
            target_id = job.strategy_ids[0]

        snapshot = await spec_store.get(target_id)
        if snapshot is None:
            raise ValueError(
                f"SmokeMode could not resolve strategy id {target_id!r} "
                "from the supplied spec_store"
            )
        return [_snapshot_to_spec(snapshot)]

    def dataset_subsample(
        self,
        dataset: Sequence[Any],
        count: Optional[int] = None,
    ) -> list[Any]:
        """Return the first ``count`` cases of ``dataset`` deterministically.

        Args:
            dataset: Iterable of cases produced by
                :meth:`evaluation_runner.load_dataset`.
            count: Optional override for the configured
                ``sample_count``. ``None`` uses the constructor value.

        Returns:
            A new list with at most ``count`` cases from the head of
            ``dataset``. Order is preserved exactly.
        """
        n = self.sample_count if count is None else int(count)
        if n <= 0:
            return []
        return list(dataset)[:n]


# ─────────────────────────────────────────────────────────────────────────────
# Bandit mode (Task 76 / P7)
# ─────────────────────────────────────────────────────────────────────────────


#: Default UCB1 exploration constant — ``sqrt(2)`` per Auer et al. (2002).
DEFAULT_BANDIT_EXPLORATION_CONSTANT: float = math.sqrt(2.0)

#: Default minimum pulls per arm before UCB1 takes over.
DEFAULT_BANDIT_WARMUP_PULLS_PER_ARM: int = 3


class BanditMode(ExplorationMode):
    """UCB1 bandit exploration mode (Blueprint 05 §6 / Task 76).

    The mode reuses the candidate pool produced by an internal
    :class:`GridMode` (Taylor's recommendation: do **not** generate a
    separate pool — bandits *allocate* within an existing arm set) and
    layers UCB1 selection on top via :meth:`next_arm`. Per-arm progress
    is durable via the injected :class:`BanditStateStore` so an
    interrupted experiment can resume after a process restart.

    Allocation algorithm:

    1. **Warmup phase.** Any arm whose ``pull_count`` is strictly less
       than ``warmup_pulls_per_arm`` is pulled before UCB1 takes over.
       Within the warmup phase we pick the arm with the smallest
       ``pull_count`` (ties broken by the lowest ``arm_id`` for
       determinism).
    2. **UCB1 phase.** Once every arm has at least
       ``warmup_pulls_per_arm`` pulls, allocation switches to the
       deterministic UCB1 score::

           score(arm) = mean_reward(arm)
                        + exploration_constant
                        * sqrt(ln(total_pulls_so_far) / arm.pull_count)

       The arm with the highest score wins (ties broken by lowest
       ``arm_id``). When ``total_pulls_so_far`` is zero we coerce it to
       ``1.0`` before taking the logarithm to avoid ``-inf``; this is
       only reachable when ``warmup_pulls_per_arm == 0`` and is
       therefore an explicit, narrow concession.
    3. **Termination.** When ``max_total_pulls`` is set and the runner
       has pulled at least that many times, :meth:`next_arm` returns
       ``None`` to signal "stop".

    Args:
        grid_mode: Pre-configured :class:`GridMode` whose
            :meth:`materialize_candidates` output is used as the arm
            pool. When ``None`` a default :class:`GridMode` is
            constructed lazily on the first
            :meth:`materialize_candidates` call.
        state_store: Persistent backing store for per-arm progress.
            Defaults to a fresh :class:`InMemoryBanditStateStore`.
        exploration_constant: UCB1 exploration multiplier (``c``).
            Defaults to ``sqrt(2)``.
        warmup_pulls_per_arm: Minimum pulls per arm before UCB1
            allocation kicks in. Must be ``>= 0``; ``0`` disables the
            warmup phase.
        max_total_pulls: Optional global pull budget. When set,
            :meth:`next_arm` returns ``None`` after this many pulls
            have been recorded.
    """

    def __init__(
        self,
        *,
        grid_mode: Optional[GridMode] = None,
        state_store: Optional[BanditStateStore] = None,
        exploration_constant: float = DEFAULT_BANDIT_EXPLORATION_CONSTANT,
        warmup_pulls_per_arm: int = DEFAULT_BANDIT_WARMUP_PULLS_PER_ARM,
        max_total_pulls: Optional[int] = None,
    ) -> None:
        if warmup_pulls_per_arm < 0:
            raise ValueError("warmup_pulls_per_arm must be non-negative")
        if exploration_constant < 0.0:
            raise ValueError("exploration_constant must be non-negative")
        if max_total_pulls is not None and max_total_pulls < 0:
            raise ValueError("max_total_pulls must be non-negative when set")
        self._grid_mode = grid_mode
        self._state_store: BanditStateStore = (
            state_store if state_store is not None else InMemoryBanditStateStore()
        )
        self._exploration_constant = float(exploration_constant)
        self._warmup_pulls_per_arm = int(warmup_pulls_per_arm)
        self._max_total_pulls = max_total_pulls
        # Cached arm-id index for the current experiment so the runner
        # can resolve ``next_arm`` -> :class:`StrategySpec` without a
        # second materialization round-trip.
        self._arm_pool: list[StrategySpec] = []

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def state_store(self) -> BanditStateStore:
        """Return the underlying :class:`BanditStateStore` handle."""
        return self._state_store

    @property
    def exploration_constant(self) -> float:
        """Return the UCB1 exploration multiplier (``c``)."""
        return self._exploration_constant

    @property
    def warmup_pulls_per_arm(self) -> int:
        """Return the per-arm warmup pull threshold."""
        return self._warmup_pulls_per_arm

    @property
    def max_total_pulls(self) -> Optional[int]:
        """Return the global pull budget (or ``None`` when unbounded)."""
        return self._max_total_pulls

    @property
    def arm_pool(self) -> list[StrategySpec]:
        """Return the most recently materialized arm pool (read-only view)."""
        return list(self._arm_pool)

    # ── ExplorationMode contract ───────────────────────────────────────

    async def materialize_candidates(
        self,
        job: StrategyExperimentJob,
        *,
        spec_store: SpecStore,
        candidate_generator_factory: Optional[Callable[..., CandidateGenerator]] = None,
    ) -> list[StrategySpec]:
        """Return the bandit's arm pool for ``job``.

        Delegates to an internal :class:`GridMode` (auto-instantiated
        with sensible defaults when none was supplied) and seeds the
        bandit-state collection with one zero-progress
        :class:`BanditArmState` row per arm. Existing rows for the same
        ``(experiment_id, arm_id)`` are left untouched so an
        interrupted experiment can resume against a warm state store.
        """
        grid = self._grid_mode
        if grid is None:
            grid = GridMode()
            self._grid_mode = grid
        candidates = await grid.materialize_candidates(
            job,
            spec_store=spec_store,
            candidate_generator_factory=candidate_generator_factory,
        )
        self._arm_pool = list(candidates)
        await self._state_store.ensure_indexes()
        for cand in candidates:
            existing = await self._state_store.get_arm(
                job.experiment_id, cand.strategy_id
            )
            if existing is None:
                await self._state_store.upsert_arm(
                    BanditArmState(
                        experiment_id=job.experiment_id,
                        arm_id=cand.strategy_id,
                    )
                )
        return candidates

    # ── Bandit-specific surface ───────────────────────────────────────

    async def next_arm(
        self,
        *,
        experiment_id: str,
        total_pulls_so_far: int,
    ) -> Optional[str]:
        """Return the arm id to pull next.

        Args:
            experiment_id: Owning experiment id; used to scope the
                bandit-state lookup.
            total_pulls_so_far: Sum of ``pull_count`` across every arm
                of this experiment. The runner is expected to maintain
                this counter as it loops over cases.

        Returns:
            The arm id (== candidate strategy id) selected for the
            next pull, or ``None`` when ``max_total_pulls`` is
            configured and reached, or when no arms exist yet.
        """
        if (
            self._max_total_pulls is not None
            and total_pulls_so_far >= self._max_total_pulls
        ):
            return None
        arms = await self._state_store.list_arms(experiment_id)
        if not arms:
            return None

        # Phase 1 — warmup: prefer arms below the warmup threshold,
        # picking the smallest pull_count (ties → lowest arm_id).
        if self._warmup_pulls_per_arm > 0:
            warmup_candidates = [
                a for a in arms if a.pull_count < self._warmup_pulls_per_arm
            ]
            if warmup_candidates:
                warmup_candidates.sort(key=lambda a: (a.pull_count, a.arm_id))
                return warmup_candidates[0].arm_id

        # Phase 2 — UCB1: highest score wins (ties → lowest arm_id).
        log_n = math.log(max(total_pulls_so_far, 1))
        best_score = -math.inf
        best_arm: Optional[BanditArmState] = None
        for arm in sorted(arms, key=lambda a: a.arm_id):
            pulls = max(arm.pull_count, 1)
            ucb = arm.mean_reward + self._exploration_constant * math.sqrt(
                log_n / pulls
            )
            if ucb > best_score:
                best_score = ucb
                best_arm = arm
        return best_arm.arm_id if best_arm is not None else None

    async def record_result(
        self,
        *,
        experiment_id: str,
        arm_id: str,
        reward: float,
    ) -> BanditArmState:
        """Record one pull's reward via the configured state store.

        ``reward`` is expected to be the composite score returned by
        :class:`~backend.agent.strategy.evaluation_runner_adapter.EvaluationRunnerAdapter`
        — a float in ``[0.0, 1.0]``. The runner forwards exactly that
        number; no further normalization is applied here.
        """
        return await self._state_store.record_pull(
            experiment_id, arm_id, float(reward)
        )


# ─────────────────────────────────────────────────────────────────────────────
# Evolutionary mode (Task 77 / P8)
# ─────────────────────────────────────────────────────────────────────────────


#: Default number of generations the evolutionary mode materializes
#: ahead of the runner. Generation 0 is the seed pool plus N evolved
#: generations.
DEFAULT_EVOLUTION_GENERATIONS: int = 3

#: Default number of top-K parents promoted between generations.
DEFAULT_EVOLUTION_TOP_K: int = 3


#: Async resolver returning the parent :class:`StrategySpec` pool used to
#: seed generation 0. The resolver receives the live
#: :class:`StrategyExperimentJob` and the project's :class:`SpecStore`.
ParentPoolResolver = Callable[
    [StrategyExperimentJob, SpecStore],
    Awaitable[list[StrategySpec]],
]


class EvolutionaryMode(ExplorationMode):
    """Population-evolution exploration mode (Blueprint 05 §6.3 / Task 77).

    The mode generates candidates **upfront** for ``num_generations``
    successive generations so the existing legacy nested-loop runner
    path (P5's ``StrategyExperimentRunner``) can iterate every child
    without an online, generation-by-generation feedback loop.
    Online inter-generation feedback *is* supported across runs: at
    materialization time the mode consults the
    :class:`EvolutionStateStore` for prior generations of the same
    ``experiment_id`` and uses their recorded fitness to seed the next
    parent pool. For the **first** experiment (no history) the parent
    pool is supplied by ``parent_pool_resolver``.

    The default :meth:`default_parent_pool_resolver` implements the
    Blueprint-mandated three-step fallback chain:

    1. **Prior experiment.** If ``experiment_job_store`` is supplied,
       look up the most recent ``COMPLETED`` job sharing this job's
       ``(tenant, capability_id)`` and return the top-K candidates by
       ``composite_score_mean``.
    2. **Active production specs.** Same logic as :class:`RegressionMode`
       — every ``status="active"`` spec for the tenant.
    3. **Empty list.** Caller will then return zero candidates and the
       runner emits a no-op summary.

    Args:
        mutator: Pre-configured :class:`EvolutionaryMutator`. ``None``
            constructs a default with deterministic seeding.
        state_store: Persistent :class:`EvolutionStateStore`. Defaults
            to an in-process :class:`InMemoryEvolutionStateStore`.
        num_generations: How many *evolved* generations to add on top
            of generation 0. ``0`` returns just the seed pool.
        top_k_parents: Number of top-fitness parents promoted between
            generations.
        parent_pool_resolver: Async callable returning the seed pool.
            ``None`` falls back to :meth:`default_parent_pool_resolver`.
        experiment_job_store: Optional handle used by
            :meth:`default_parent_pool_resolver` to read prior winners.
        seed: RNG seed forwarded to the default mutator.
    """

    def __init__(
        self,
        *,
        mutator: Optional[EvolutionaryMutator] = None,
        state_store: Optional[EvolutionStateStore] = None,
        num_generations: int = DEFAULT_EVOLUTION_GENERATIONS,
        top_k_parents: int = DEFAULT_EVOLUTION_TOP_K,
        parent_pool_resolver: Optional[ParentPoolResolver] = None,
        experiment_job_store: Optional[Any] = None,
        seed: Optional[int] = None,
    ) -> None:
        if num_generations < 0:
            raise ValueError("num_generations must be non-negative")
        if top_k_parents <= 0:
            raise ValueError("top_k_parents must be positive")
        self._mutator: EvolutionaryMutator = (
            mutator
            if mutator is not None
            else EvolutionaryMutator(seed=seed)
        )
        self._state_store: EvolutionStateStore = (
            state_store
            if state_store is not None
            else InMemoryEvolutionStateStore()
        )
        self._num_generations = int(num_generations)
        self._top_k_parents = int(top_k_parents)
        self._parent_pool_resolver = parent_pool_resolver
        self._experiment_job_store = experiment_job_store
        self._seed = seed

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def state_store(self) -> EvolutionStateStore:
        """Return the underlying :class:`EvolutionStateStore` handle."""
        return self._state_store

    @property
    def mutator(self) -> EvolutionaryMutator:
        """Return the configured :class:`EvolutionaryMutator`."""
        return self._mutator

    @property
    def num_generations(self) -> int:
        """Return the count of evolved generations beyond gen 0."""
        return self._num_generations

    @property
    def top_k_parents(self) -> int:
        """Return the number of top-fitness parents promoted per generation."""
        return self._top_k_parents

    # ── ExplorationMode contract ───────────────────────────────────────

    async def materialize_candidates(
        self,
        job: StrategyExperimentJob,
        *,
        spec_store: SpecStore,
        candidate_generator_factory: Optional[Callable[..., CandidateGenerator]] = None,
    ) -> list[StrategySpec]:
        """Materialize generation 0 + ``num_generations`` evolved generations.

        Returns the **flattened** child pool ordered by generation
        ascending so the legacy runner consumes generation 0 first,
        then generation 1, etc. Persistence of per-generation state is
        eagerly written through :class:`EvolutionStateStore`.
        """
        del candidate_generator_factory  # not used — operators self-validate
        await self._state_store.ensure_indexes()

        seed_pool = await self._resolve_seed_pool(job, spec_store=spec_store)
        if not seed_pool:
            logger.warning(
                "EvolutionaryMode: empty parent pool for job=%s tenant=%s; "
                "returning no candidates.",
                job.id,
                job.tenant,
            )
            return []

        # Truncate seed pool to top_k_parents to match the inter-generation
        # bottleneck — the resolver may return more than K specs.
        seed_parents = list(seed_pool)[: self._top_k_parents]

        all_children: list[StrategySpec] = []

        # Generation 0: the seed pool itself, persisted and emitted as-is.
        await self._state_store.upsert_generation(
            EvolutionGenerationState(
                experiment_id=job.experiment_id,
                generation=0,
                population_size=len(seed_parents),
                parent_ids=[],
                child_ids=[s.strategy_id for s in seed_parents],
            )
        )
        all_children.extend(seed_parents)

        current_parents = list(seed_parents)
        for gen in range(1, self._num_generations + 1):
            if not current_parents:
                break
            children = self._mutator.mutate(
                parents=current_parents, generation=gen
            )
            await self._state_store.upsert_generation(
                EvolutionGenerationState(
                    experiment_id=job.experiment_id,
                    generation=gen,
                    population_size=len(children),
                    parent_ids=[p.strategy_id for p in current_parents],
                    child_ids=[c.strategy_id for c in children],
                )
            )
            all_children.extend(children)
            # Without recorded fitness for *this* run we promote the
            # head of the produced list — fitness-based promotion only
            # kicks in on subsequent experiment runs via
            # :meth:`default_parent_pool_resolver`.
            current_parents = children[: self._top_k_parents]

        return all_children

    # ── Public helpers ─────────────────────────────────────────────────

    async def record_generation_results(
        self,
        *,
        experiment_id: str,
        generation: int,
        child_fitness: dict[str, float],
    ) -> EvolutionGenerationState:
        """Persist mean / best fitness for ``generation``.

        Args:
            experiment_id: Experiment identifier (matches
                :attr:`StrategyExperimentJob.experiment_id`).
            generation: Generation index (``>= 0``).
            child_fitness: Mapping ``strategy_id -> composite_score``.
                Empty mappings are accepted and recorded as ``None``
                fitness.

        Returns:
            The updated :class:`EvolutionGenerationState`.
        """
        existing = None
        rows = await self._state_store.list_generations(experiment_id)
        for row in rows:
            if row.generation == generation:
                existing = row
                break
        if existing is None:
            existing = EvolutionGenerationState(
                experiment_id=experiment_id,
                generation=generation,
            )
        scores = [
            float(v)
            for v in child_fitness.values()
            if isinstance(v, (int, float))
        ]
        mean_fitness = sum(scores) / len(scores) if scores else None
        best_fitness = max(scores) if scores else None
        updated = existing.model_copy(
            update={
                "mean_fitness": mean_fitness,
                "best_fitness": best_fitness,
                "completed_at": _utcnow_aware(),
            }
        )
        return await self._state_store.upsert_generation(updated)

    # ── Parent-pool resolution ─────────────────────────────────────────

    async def _resolve_seed_pool(
        self,
        job: StrategyExperimentJob,
        *,
        spec_store: SpecStore,
    ) -> list[StrategySpec]:
        """Dispatch to user-supplied resolver or the default fallback."""
        if self._parent_pool_resolver is not None:
            return list(
                await self._parent_pool_resolver(job, spec_store)
            )
        return await self.default_parent_pool_resolver(
            job, spec_store=spec_store
        )

    async def default_parent_pool_resolver(
        self,
        job: StrategyExperimentJob,
        *,
        spec_store: SpecStore,
    ) -> list[StrategySpec]:
        """Three-step fallback parent resolution.

        See the class docstring for the precise ordering. Each step
        is wrapped in a defensive ``try/except`` so a single failing
        backend cannot starve the experiment — the resolver simply
        falls through to the next step.
        """
        # Step 1 — prior experiment top-K.
        prior = await self._top_k_from_prior_experiment(job, spec_store=spec_store)
        if prior:
            return prior

        # Step 2 — active production specs.
        try:
            snapshots = await spec_store.list(
                status="active", tenant_id=job.tenant
            )
            actives = [
                _snapshot_to_spec(s) for s in snapshots
            ]
            if actives:
                return actives[: self._top_k_parents]
        except Exception as exc:  # noqa: BLE001 - tolerate store failures
            logger.warning(
                "EvolutionaryMode: spec_store.list(active) failed: %s",
                exc,
            )

        # Step 3 — give up.
        return []

    async def _top_k_from_prior_experiment(
        self,
        job: StrategyExperimentJob,
        *,
        spec_store: SpecStore,
    ) -> list[StrategySpec]:
        """Read the most recent prior ``COMPLETED`` job for this tenant.

        Returns the top-K specs by ``composite_score_mean`` extracted
        from the prior job's ``result_summary``. Specs are fetched
        through ``spec_store``; ids that no longer resolve are
        silently skipped.
        """
        if self._experiment_job_store is None:
            return []
        try:
            from backend.agent.strategy.experiment_job_models import JobStatus

            jobs = await self._experiment_job_store.list(
                status=JobStatus.COMPLETED,
                tenant=job.tenant,
                limit=20,
            )
        except Exception as exc:  # noqa: BLE001 - tolerate store failures
            logger.warning(
                "EvolutionaryMode: experiment_job_store.list failed: %s",
                exc,
            )
            return []
        # Skip jobs from this very experiment so we don't recursively
        # promote our own prior generations.
        candidates = [
            j for j in jobs if j.experiment_id != job.experiment_id
        ]
        for prior_job in candidates:
            specs = await self._extract_top_k(prior_job, spec_store=spec_store)
            if specs:
                return specs
        return []

    async def _extract_top_k(
        self,
        prior_job: StrategyExperimentJob,
        *,
        spec_store: SpecStore,
    ) -> list[StrategySpec]:
        """Pull the top-K specs by composite score from a prior job summary."""
        summary = prior_job.result_summary
        if not isinstance(summary, dict):
            return []
        results = summary.get("results")
        if not isinstance(results, list):
            return []
        ranked: list[tuple[float, str]] = []
        for entry in results:
            if not isinstance(entry, dict):
                continue
            sid = entry.get("candidate_strategy_id")
            score = entry.get("composite_score_mean")
            if not isinstance(sid, str):
                continue
            try:
                score_f = float(score)
            except (TypeError, ValueError):
                continue
            ranked.append((score_f, sid))
        ranked.sort(key=lambda r: r[0], reverse=True)
        out: list[StrategySpec] = []
        seen: set[str] = set()
        for _score, sid in ranked:
            if sid in seen:
                continue
            seen.add(sid)
            try:
                snapshot = await spec_store.get(sid)
            except Exception as exc:  # noqa: BLE001 - tolerate failures
                logger.warning(
                    "EvolutionaryMode: spec_store.get(%r) failed: %s",
                    sid,
                    exc,
                )
                continue
            if snapshot is None:
                continue
            try:
                out.append(_snapshot_to_spec(snapshot))
            except Exception as exc:  # noqa: BLE001 - tolerate parse failures
                logger.warning(
                    "EvolutionaryMode: _snapshot_to_spec(%r) failed: %s",
                    sid,
                    exc,
                )
                continue
            if len(out) >= self._top_k_parents:
                break
        return out


def _utcnow_aware() -> "datetime":
    """Return a timezone-aware UTC ``datetime`` for state-row stamping."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────


class ModeRegistry:
    """Static dispatch table for P6 exploration modes.

    The registry maps mode names to :class:`ExplorationMode` factories.
    Bandit (P7) and evolutionary (P8) modes register here later.
    """

    _registry: dict[str, Callable[..., ExplorationMode]] = {}

    @classmethod
    def register(
        cls,
        mode_name: str,
        factory: Callable[..., ExplorationMode],
    ) -> None:
        """Register a factory for ``mode_name``.

        Args:
            mode_name: Lower-case mode identifier.
            factory: Callable invoked as ``factory(**config)`` by
                :meth:`get`.
        """
        cls._registry[mode_name] = factory

    @classmethod
    def get(
        cls,
        mode_name: str,
        *,
        config: Optional[dict[str, Any]] = None,
    ) -> ExplorationMode:
        """Return the :class:`ExplorationMode` instance for ``mode_name``.

        Args:
            mode_name: Mode identifier (e.g. ``"grid"``).
            config: Optional kwargs forwarded to the registered factory.

        Raises:
            UnknownExplorationModeError: ``mode_name`` is not registered.
        """
        factory = cls._registry.get(mode_name)
        if factory is None:
            raise UnknownExplorationModeError(mode_name)
        return factory(**(config or {}))

    @classmethod
    def known_modes(cls) -> list[str]:
        """Return the sorted list of registered mode names."""
        return sorted(cls._registry)


# Built-in registrations.
ModeRegistry.register("grid", GridMode)
ModeRegistry.register("regression", RegressionMode)
ModeRegistry.register("smoke", SmokeMode)
ModeRegistry.register("bandit", BanditMode)
ModeRegistry.register("evolutionary", lambda **cfg: EvolutionaryMode(**cfg))
