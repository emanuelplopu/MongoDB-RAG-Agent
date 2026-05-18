"""Pure unit tests for the Phase 6 / Task 78 :class:`FastPathRules`.

Each Blueprint 08 §9 condition is exercised in isolation. Optional
inputs (``top_retrieval_score``, missing ``answer_contract``) are
treated conservatively — i.e. ``False`` rather than implicit pass.
"""
from __future__ import annotations

import pytest

from backend.agent.strategy.models import (
    AmbiguityResolution,
    AnswerContract,
    BusinessContext,
    ResolvedSourcePolicy,
)
from backend.agent.strategy.spec_selector import (
    FAST_PATH_ELIGIBLE_CAPABILITIES,
    FastPathRules,
)


def _make_business_context(
    *,
    capability_id: str = "legal_hearing_questions",
    matter_id: str = "matter-1",
    ambiguity_detected: bool = False,
    answer_contract: AnswerContract | None = None,
) -> BusinessContext:
    return BusinessContext(
        capability_id=capability_id,
        matter_id=matter_id,
        ambiguity=AmbiguityResolution(ambiguity_detected=ambiguity_detected),
        answer_contract=answer_contract,
    )


def _local_policy() -> ResolvedSourcePolicy:
    return ResolvedSourcePolicy(allow_web=False)


def test_all_conditions_met_returns_true() -> None:
    """Every Blueprint 08 §9 precondition holds → ``True``."""
    rules = FastPathRules()
    bctx = _make_business_context()
    assert (
        rules.evaluate(
            business_context=bctx,
            source_policy=_local_policy(),
            capability_id="legal_hearing_questions",
            top_retrieval_score=0.95,
        )
        is True
    )


def test_missing_business_context_returns_false() -> None:
    """No ``BusinessContext`` → cannot satisfy condition 1."""
    rules = FastPathRules()
    assert (
        rules.evaluate(
            business_context=None,
            source_policy=_local_policy(),
            capability_id="legal_hearing_questions",
            top_retrieval_score=0.95,
        )
        is False
    )


def test_missing_active_matter_returns_false() -> None:
    """Condition 1: missing ``matter_id`` → fast path disallowed."""
    rules = FastPathRules()
    bctx = _make_business_context(matter_id="")
    assert (
        rules.evaluate(
            business_context=bctx,
            source_policy=_local_policy(),
            capability_id="legal_hearing_questions",
            top_retrieval_score=0.95,
        )
        is False
    )


def test_ambiguity_detected_returns_false() -> None:
    """Condition 1: ambiguous capability → fast path disallowed."""
    rules = FastPathRules()
    bctx = _make_business_context(ambiguity_detected=True)
    assert (
        rules.evaluate(
            business_context=bctx,
            source_policy=_local_policy(),
            capability_id="legal_hearing_questions",
            top_retrieval_score=0.95,
        )
        is False
    )


def test_low_retrieval_score_returns_false() -> None:
    """Condition 2: top score below 0.85 threshold → ``False``."""
    rules = FastPathRules()
    bctx = _make_business_context()
    assert (
        rules.evaluate(
            business_context=bctx,
            source_policy=_local_policy(),
            capability_id="legal_hearing_questions",
            top_retrieval_score=0.50,
        )
        is False
    )


def test_missing_retrieval_score_returns_false() -> None:
    """Condition 2: ``None`` retrieval score is treated as unknown → ``False``."""
    rules = FastPathRules()
    bctx = _make_business_context()
    assert (
        rules.evaluate(
            business_context=bctx,
            source_policy=_local_policy(),
            capability_id="legal_hearing_questions",
            top_retrieval_score=None,
        )
        is False
    )


def test_capability_not_in_eligible_list_returns_false() -> None:
    """Condition 3 fallback: unknown capability without a template → ``False``."""
    rules = FastPathRules()
    bctx = _make_business_context(capability_id="adhoc_research_questions")
    assert "adhoc_research_questions" not in FAST_PATH_ELIGIBLE_CAPABILITIES
    assert (
        rules.evaluate(
            business_context=bctx,
            source_policy=_local_policy(),
            capability_id="adhoc_research_questions",
            top_retrieval_score=0.95,
        )
        is False
    )


def test_web_allowed_returns_false() -> None:
    """Condition 4: web access allowed → fast path disallowed."""
    rules = FastPathRules()
    bctx = _make_business_context()
    web_policy = ResolvedSourcePolicy(allow_web=True)
    assert (
        rules.evaluate(
            business_context=bctx,
            source_policy=web_policy,
            capability_id="legal_hearing_questions",
            top_retrieval_score=0.95,
        )
        is False
    )


def test_non_drafting_capability_returns_false() -> None:
    """Condition 5: capability id without drafting suffix → ``False``."""
    rules = FastPathRules()
    bctx = _make_business_context(capability_id="legal_research")
    assert (
        rules.evaluate(
            business_context=bctx,
            source_policy=_local_policy(),
            capability_id="legal_research",
            top_retrieval_score=0.95,
        )
        is False
    )


def test_business_summary_capability_uses_eligible_list() -> None:
    """``business_summary`` is in the hardcoded fallback list → eligible."""
    rules = FastPathRules()
    bctx = _make_business_context(capability_id="business_summary")
    assert (
        rules.evaluate(
            business_context=bctx,
            source_policy=_local_policy(),
            capability_id="business_summary",
            top_retrieval_score=0.90,
        )
        is True
    )
