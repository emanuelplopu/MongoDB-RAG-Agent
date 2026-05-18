"""Promotion candidate evaluator (Phase 6 / Task 79 / P10).

Applies the Blueprint 05 §11 thresholds to candidate strategies and emits
:class:`PromotionCandidateSummary` rows the nightly report consumes.

This module **never auto-promotes**. It only reads aggregated quality
signals from ``evaluation_results`` and ``strategy_specs`` and emits a
recommendation tag per candidate (``recommended`` / ``blocked`` /
``needs_more_runs``) plus a per-threshold pass/fail breakdown.

The eight thresholds match Blueprint 05 §11 defaults exactly:

    1. ``min_runs                   = 30``
    2. ``min_composite_score        = 0.82``
    3. ``beats_current_default_by   = 0.05``
    4. ``max_fatal_failures         = 0``
    5. ``min_citation_coverage      = 0.85``
    6. ``latency_p95_below_ms       = 60_000``
    7. ``privacy_policy_pass        = True``
    8. ``manual_approval_required   = True`` (informational — never
       auto-cleared by this module).

Repo rule #3: this module uses ``pymongo.AsyncMongoClient`` collection
semantics only — no Motor APIs are introduced.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

__all__ = [
    "PromotionThresholds",
    "PromotionCandidateSummary",
    "PromotionEvaluator",
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile mirroring ``numpy.percentile``."""
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


def _coerce_float(value: Any) -> Optional[float]:
    """Return ``value`` as a ``float`` or ``None`` for non-numeric input."""
    if isinstance(value, bool):
        # ``bool`` is a subclass of ``int`` — exclude it explicitly because
        # citation-coverage style booleans should never coerce to 0/1.
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _extract_metrics(doc: dict[str, Any]) -> dict[str, Any]:
    """Return the merged ``metrics`` mapping for an evaluation doc.

    Looks for an explicit top-level ``metrics`` dict first, then falls
    back to dimension-keyed scores under ``result.dimension_scores``
    (so a citation_quality dimension contributes a citation_coverage
    proxy when no dedicated metric is recorded).
    """
    metrics: dict[str, Any] = {}
    raw = doc.get("metrics")
    if isinstance(raw, dict):
        metrics.update(raw)
    result = doc.get("result")
    if isinstance(result, dict):
        for dim in result.get("dimension_scores") or []:
            if not isinstance(dim, dict):
                continue
            dim_id = dim.get("dimension_id")
            score = dim.get("score")
            if isinstance(dim_id, str) and isinstance(score, (int, float)):
                metrics.setdefault(f"dimension.{dim_id}", float(score))
    return metrics


def _citation_coverage(doc: dict[str, Any]) -> Optional[float]:
    """Best-effort extract a citation-coverage float from ``doc``."""
    metrics = _extract_metrics(doc)
    for key in ("citation_coverage", "citation_coverage_mean"):
        value = _coerce_float(metrics.get(key))
        if value is not None:
            return value
    # Fall back to the citation_quality dimension score.
    return _coerce_float(metrics.get("dimension.citation_quality"))


def _is_fatal(doc: dict[str, Any]) -> bool:
    """Return ``True`` when ``doc`` represents a fatal failure."""
    if doc.get("status") == "fatal":
        return True
    result = doc.get("result")
    if isinstance(result, dict) and int(result.get("fatal_gate", 1)) == 0:
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schema
# ─────────────────────────────────────────────────────────────────────────────


class PromotionThresholds(BaseModel):
    """Blueprint 05 §11 promotion thresholds.

    All defaults match the blueprint exactly. Override individual
    fields by passing them to the constructor; unspecified values
    fall back to the §11 defaults.
    """

    min_runs: int = Field(default=30, ge=0)
    min_composite_score: float = Field(default=0.82, ge=0.0, le=1.0)
    beats_current_default_by: float = Field(default=0.05, ge=0.0)
    max_fatal_failures: int = Field(default=0, ge=0)
    min_citation_coverage: float = Field(default=0.85, ge=0.0, le=1.0)
    latency_p95_below_ms: int = Field(default=60_000, ge=0)
    privacy_policy_pass: bool = Field(default=True)
    manual_approval_required: bool = Field(default=True)


class PromotionCandidateSummary(BaseModel):
    """Per-candidate promotion outcome surfaced into the nightly report."""

    strategy_id: str
    version: Optional[str] = None
    capability_id: str

    composite_score_mean: float = 0.0
    composite_score_p50: float = 0.0
    composite_score_p95: float = 0.0
    latency_p95_ms: float = 0.0
    citation_coverage_mean: float = 0.0
    total_runs: int = 0
    fatal_failure_count: int = 0

    current_default_score: Optional[float] = None
    score_delta: Optional[float] = None

    passes_thresholds: dict[str, bool] = Field(default_factory=dict)
    recommendation: Literal["recommended", "blocked", "needs_more_runs"] = "blocked"
    blocking_reasons: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Evaluator
# ─────────────────────────────────────────────────────────────────────────────


class PromotionEvaluator:
    """Apply Blueprint 05 §11 thresholds to candidate strategies.

    Args:
        thresholds: Override the §11 defaults. ``None`` keeps the
            blueprint defaults exactly.

    The evaluator is purely a scoring/aggregation engine — it never
    mutates ``strategy_specs`` and never marks a candidate active.
    """

    #: Threshold keys that block promotion when they fail. ``min_runs``
    #: routes to the ``needs_more_runs`` recommendation instead of
    #: ``blocked`` because operators can simply collect more samples.
    BLOCKING_KEYS: tuple[str, ...] = (
        "min_composite_score",
        "beats_current_default_by",
        "max_fatal_failures",
        "min_citation_coverage",
        "latency_p95_below_ms",
        "privacy_policy_pass",
        "manual_approval_required",
    )

    def __init__(self, *, thresholds: Optional[PromotionThresholds] = None) -> None:
        self.thresholds = thresholds or PromotionThresholds()

    # ── Public API ──────────────────────────────────────────────────────

    async def evaluate(
        self,
        *,
        db: Any,
        capability_id: str,
        candidate_strategy_id: str,
        candidate_version: Optional[str],
        since: datetime,
        until: datetime,
    ) -> PromotionCandidateSummary:
        """Score the candidate against the active default for a capability.

        Aggregates ``evaluation_results`` for the candidate over the
        window, looks up the active default spec via ``strategy_specs``
        and aggregates its score over the same window, then computes a
        per-threshold pass/fail breakdown.

        Args:
            db: Async Mongo database handle.
            capability_id: Capability the candidate competes for.
            candidate_strategy_id: Candidate spec ``strategy_id``.
            candidate_version: Optional candidate spec version.
            since: Window lower bound (UTC).
            until: Window upper bound (UTC, inclusive).

        Returns:
            A populated :class:`PromotionCandidateSummary`.
        """
        candidate_docs = await self._read_eval_docs(
            db,
            strategy_id=candidate_strategy_id,
            since=since,
            until=until,
        )

        scores = [
            float(d["result"]["composite_score"])
            for d in candidate_docs
            if isinstance(d.get("result"), dict)
            and isinstance(d["result"].get("composite_score"), (int, float))
        ]
        latencies = [
            float(d["result"]["latency_ms"])
            for d in candidate_docs
            if isinstance(d.get("result"), dict)
            and isinstance(d["result"].get("latency_ms"), (int, float))
        ]
        coverages = [
            v for v in (_citation_coverage(d) for d in candidate_docs) if v is not None
        ]
        fatal_count = sum(1 for d in candidate_docs if _is_fatal(d))
        total_runs = len(candidate_docs)

        composite_mean = statistics.fmean(scores) if scores else 0.0
        composite_p50 = _percentile(scores, 50.0)
        composite_p95 = _percentile(scores, 95.0)
        latency_p95 = _percentile(latencies, 95.0)
        coverage_mean = statistics.fmean(coverages) if coverages else 0.0

        default_score = await self._aggregate_default_score(
            db,
            capability_id=capability_id,
            since=since,
            until=until,
            exclude_strategy_id=candidate_strategy_id,
        )
        score_delta: Optional[float] = (
            composite_mean - default_score if default_score is not None else None
        )

        passes, blocking = self._check_thresholds(
            total_runs=total_runs,
            composite_mean=composite_mean,
            latency_p95=latency_p95,
            coverage_mean=coverage_mean,
            fatal_count=fatal_count,
            score_delta=score_delta,
            candidate_docs=candidate_docs,
        )

        recommendation = self._recommendation(passes)

        return PromotionCandidateSummary(
            strategy_id=candidate_strategy_id,
            version=candidate_version,
            capability_id=capability_id,
            composite_score_mean=round(composite_mean, 6),
            composite_score_p50=round(composite_p50, 6),
            composite_score_p95=round(composite_p95, 6),
            latency_p95_ms=round(latency_p95, 3),
            citation_coverage_mean=round(coverage_mean, 6),
            total_runs=total_runs,
            fatal_failure_count=fatal_count,
            current_default_score=(
                round(default_score, 6) if default_score is not None else None
            ),
            score_delta=(round(score_delta, 6) if score_delta is not None else None),
            passes_thresholds=passes,
            recommendation=recommendation,
            blocking_reasons=blocking,
        )

    async def evaluate_all(
        self,
        *,
        db: Any,
        since: datetime,
        until: datetime,
    ) -> list[PromotionCandidateSummary]:
        """Evaluate every non-active candidate spec in ``strategy_specs``.

        Returns the candidates sorted by ``score_delta`` descending. A
        ``None`` score_delta sorts last.
        """
        candidates = await self._iter_candidate_specs(db)
        results: list[PromotionCandidateSummary] = []
        for capability_id, strategy_id, version in candidates:
            try:
                summary = await self.evaluate(
                    db=db,
                    capability_id=capability_id,
                    candidate_strategy_id=strategy_id,
                    candidate_version=version,
                    since=since,
                    until=until,
                )
            except Exception as exc:  # noqa: BLE001 - per-candidate isolation
                logger.warning(
                    "PromotionEvaluator: skipping candidate %s/%s: %s",
                    capability_id,
                    strategy_id,
                    exc,
                )
                continue
            results.append(summary)

        results.sort(
            key=lambda s: (
                0 if s.score_delta is None else 1,
                -(s.score_delta if s.score_delta is not None else 0.0),
            )
        )
        return results

    # ── Threshold logic ─────────────────────────────────────────────────

    def _check_thresholds(
        self,
        *,
        total_runs: int,
        composite_mean: float,
        latency_p95: float,
        coverage_mean: float,
        fatal_count: int,
        score_delta: Optional[float],
        candidate_docs: list[dict[str, Any]],
    ) -> tuple[dict[str, bool], list[str]]:
        """Compute per-threshold pass/fail and the blocking-reason list."""
        t = self.thresholds
        passes: dict[str, bool] = {
            "min_runs": total_runs >= t.min_runs,
            "min_composite_score": composite_mean >= t.min_composite_score,
            "beats_current_default_by": (
                score_delta is not None and score_delta >= t.beats_current_default_by
            )
            if score_delta is not None
            else True,  # No baseline → don't block on this; flagged separately.
            "max_fatal_failures": fatal_count <= t.max_fatal_failures,
            "min_citation_coverage": coverage_mean >= t.min_citation_coverage,
            "latency_p95_below_ms": latency_p95 < float(t.latency_p95_below_ms),
            "privacy_policy_pass": self._privacy_policy_pass(candidate_docs),
            # Manual-approval gate: this module never auto-clears it. We
            # report ``False`` whenever the threshold demands manual
            # approval so the consumer renders "manual approval pending".
            "manual_approval_required": not t.manual_approval_required,
        }

        # When score_delta is unavailable but a baseline was required,
        # surface it as a blocking reason rather than a silent pass.
        if score_delta is None and t.beats_current_default_by > 0:
            passes["beats_current_default_by"] = False

        blocking: list[str] = [
            key for key in self.BLOCKING_KEYS if not passes.get(key, True)
        ]
        return passes, blocking

    def _recommendation(
        self, passes: dict[str, bool]
    ) -> Literal["recommended", "blocked", "needs_more_runs"]:
        """Pick the headline recommendation from the threshold breakdown."""
        if all(passes.values()):
            return "recommended"
        if not passes.get("min_runs", False):
            other_failures = [
                key
                for key, ok in passes.items()
                if not ok and key != "min_runs"
            ]
            if not other_failures:
                return "needs_more_runs"
        return "blocked"

    @staticmethod
    def _privacy_policy_pass(candidate_docs: list[dict[str, Any]]) -> bool:
        """Return ``True`` when no candidate run flagged a privacy gate fail.

        We rely on ``result.privacy_gate`` (1=pass, 0=fail) emitted by
        :class:`backend.evaluation.composite_scorer.CompositeResult`. An
        absent gate value is treated as a pass — the gate defaults to
        ``1`` upstream so a missing field would only happen for legacy
        rows.
        """
        for doc in candidate_docs:
            result = doc.get("result")
            if not isinstance(result, dict):
                continue
            gate = result.get("privacy_gate")
            if isinstance(gate, (int, float)) and int(gate) == 0:
                return False
        return True

    # ── Mongo readers ───────────────────────────────────────────────────

    async def _read_eval_docs(
        self,
        db: Any,
        *,
        strategy_id: str,
        since: datetime,
        until: datetime,
    ) -> list[dict[str, Any]]:
        """Read ``evaluation_results`` for ``strategy_id`` over the window."""
        if db is None:
            return []
        match: dict[str, Any] = {
            "strategy_id": strategy_id,
            "created_at": {"$gte": since, "$lte": until},
        }
        try:
            cursor = db["evaluation_results"].find(match)
            return [doc async for doc in cursor]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PromotionEvaluator: failed to read evaluation_results for %s: %s",
                strategy_id,
                exc,
            )
            return []

    async def _aggregate_default_score(
        self,
        db: Any,
        *,
        capability_id: str,
        since: datetime,
        until: datetime,
        exclude_strategy_id: str,
    ) -> Optional[float]:
        """Return the active-default mean composite score for ``capability_id``.

        Resolves the active spec via :class:`SpecStore` (or a direct
        ``strategy_specs`` query when the store is unavailable) and
        aggregates its evaluation_results scores over the window. A
        ``None`` return means no active default was found, in which
        case ``score_delta`` is unset on the candidate summary.
        """
        if db is None:
            return None
        default_id = await self._find_default_strategy_id(
            db, capability_id=capability_id, exclude_strategy_id=exclude_strategy_id
        )
        if default_id is None:
            return None

        docs = await self._read_eval_docs(
            db, strategy_id=default_id, since=since, until=until
        )
        scores = [
            float(d["result"]["composite_score"])
            for d in docs
            if isinstance(d.get("result"), dict)
            and isinstance(d["result"].get("composite_score"), (int, float))
        ]
        if not scores:
            return None
        return statistics.fmean(scores)

    async def _find_default_strategy_id(
        self,
        db: Any,
        *,
        capability_id: str,
        exclude_strategy_id: str,
    ) -> Optional[str]:
        """Return the active spec's ``strategy_id`` for ``capability_id``."""
        try:
            cursor = db["strategy_specs"].find(
                {
                    "status": "active",
                    "spec_data.capability_id": capability_id,
                }
            )
            async for doc in cursor:
                spec_data = doc.get("spec_data") or {}
                sid = spec_data.get("strategy_id") or doc.get("strategy_id")
                if isinstance(sid, str) and sid != exclude_strategy_id:
                    return sid
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PromotionEvaluator: failed to resolve active spec for %s: %s",
                capability_id,
                exc,
            )
        return None

    async def _iter_candidate_specs(
        self, db: Any
    ) -> list[tuple[str, str, Optional[str]]]:
        """Return ``(capability_id, strategy_id, version)`` for non-active specs.

        We treat any spec whose ``status`` is not ``"active"`` as a
        candidate. This mirrors the legacy promotion flow which
        promoted strategies into ``status="active"`` on approval.
        """
        if db is None:
            return []
        results: list[tuple[str, str, Optional[str]]] = []
        seen: set[tuple[str, str]] = set()
        try:
            cursor = db["strategy_specs"].find({"status": {"$ne": "active"}})
            async for doc in cursor:
                spec_data = doc.get("spec_data") or {}
                strategy_id = spec_data.get("strategy_id") or doc.get("strategy_id")
                capability_id = spec_data.get("capability_id")
                version = doc.get("version") or spec_data.get("version")
                if not isinstance(strategy_id, str):
                    continue
                if not isinstance(capability_id, str):
                    continue
                key = (capability_id, strategy_id)
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    (
                        capability_id,
                        strategy_id,
                        version if isinstance(version, str) else None,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PromotionEvaluator: failed to enumerate candidate specs: %s", exc
            )
        return results
