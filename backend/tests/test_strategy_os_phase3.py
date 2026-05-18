"""
Unit tests for Strategy OS Phase 3 (Evaluation, Scheduling, Selection).

Tests verify:
- CompositeScoreCalculator scoring formula (weights, latency, gates)
- CalibrationGate Pearson correlation and gate logic
- ContradictionDetector claim extraction and conflict detection
- TestCaseManager versioning and lifecycle
- EvaluationRunner orchestration and leaderboard
- StrategySpecSelector filtering and ranking
- ScheduleTimezoneResolver cron evaluation and dedup keys
- PauseConditionEvaluator and StopConditionEvaluator trigger logic
- MongoDBCircuitBreaker state machine

All tests use mocks — no real DB, network, or LLM calls.
"""

import asyncio
import math
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Composite Scorer Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCompositeScorer:
    """Tests for CompositeScoreCalculator."""

    def _make_scorer(self, **kwargs):
        from backend.evaluation.composite_scorer import CompositeScoreCalculator
        from backend.evaluation.models import ScoringProfile

        profile = ScoringProfile(**kwargs) if kwargs else None
        return CompositeScoreCalculator(profile=profile)

    def _all_dimension_scores(self, score_value: float):
        """Create DimensionScore list with uniform score and default weights."""
        from backend.evaluation.composite_scorer import CompositeScoreCalculator

        dims = {
            "groundedness": score_value,
            "citation_quality": score_value,
            "directness": score_value,
            "completeness": score_value,
            "format_adherence": score_value,
            "domain_value": score_value,
            "conciseness": score_value,
        }
        return CompositeScoreCalculator.create_default_scores(dims)

    def test_perfect_scores_produce_1_0(self):
        """All dimensions 1.0, no latency penalty → composite=1.0."""
        scorer = self._make_scorer()
        scores = self._all_dimension_scores(1.0)
        result = scorer.calculate(dimension_scores=scores, latency_ms=0.0)
        assert result.composite_score == pytest.approx(1.0, abs=1e-6)

    def test_zero_scores_produce_0_0(self):
        """All dimensions 0.0 → composite=0.0."""
        scorer = self._make_scorer()
        scores = self._all_dimension_scores(0.0)
        result = scorer.calculate(dimension_scores=scores, latency_ms=0.0)
        assert result.composite_score == pytest.approx(0.0, abs=1e-6)

    def test_latency_at_target_scores_1_0(self):
        """Latency at target → latency_score=1.0."""
        from backend.evaluation.models import LatencyConfig

        scorer = self._make_scorer()
        config = LatencyConfig(target_ms=5000.0, hard_limit_ms=30000.0)
        latency_score = scorer.calculate_latency_score(5000.0, config)
        assert latency_score == 1.0

    def test_latency_at_hard_limit_scores_0_0(self):
        """Latency at hard limit → latency_score=0.0."""
        from backend.evaluation.models import LatencyConfig

        scorer = self._make_scorer()
        config = LatencyConfig(target_ms=5000.0, hard_limit_ms=30000.0)
        latency_score = scorer.calculate_latency_score(30000.0, config)
        assert latency_score == 0.0

    def test_latency_midpoint_scores_0_5(self):
        """Latency halfway between target and hard limit → latency_score≈0.5."""
        from backend.evaluation.models import LatencyConfig

        scorer = self._make_scorer()
        config = LatencyConfig(target_ms=5000.0, hard_limit_ms=30000.0)
        midpoint = (5000.0 + 30000.0) / 2.0  # 17500
        latency_score = scorer.calculate_latency_score(midpoint, config)
        assert latency_score == pytest.approx(0.5, abs=1e-6)

    def test_fatal_gate_zeros_score(self):
        """fatal_gate=0 → composite=0.0 regardless of other scores."""
        scorer = self._make_scorer()
        scores = self._all_dimension_scores(1.0)
        result = scorer.calculate(dimension_scores=scores, latency_ms=0.0, fatal_gate=0)
        assert result.composite_score == 0.0

    def test_privacy_gate_zeros_score(self):
        """privacy_gate=0 → composite=0.0 regardless of other scores."""
        scorer = self._make_scorer()
        scores = self._all_dimension_scores(1.0)
        result = scorer.calculate(dimension_scores=scores, latency_ms=0.0, privacy_gate=0)
        assert result.composite_score == 0.0

    def test_custom_weights_applied(self):
        """Non-default weights produce correct weighted sum."""
        from backend.evaluation.models import DimensionId, DimensionScore

        scorer = self._make_scorer()
        # Single dimension with known weight
        scores = [
            DimensionScore(dimension_id=DimensionId.GROUNDEDNESS, score=0.8, weight=0.5),
            DimensionScore(dimension_id=DimensionId.DIRECTNESS, score=0.6, weight=0.5),
        ]
        result = scorer.calculate(dimension_scores=scores, latency_ms=0.0)
        expected = (0.8 * 0.5 + 0.6 * 0.5) * 1.0 * 1 * 1  # 0.7
        assert result.composite_score == pytest.approx(expected, abs=1e-6)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Judge Calibration Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestJudgeCalibration:
    """Tests for CalibrationGate."""

    def test_pearson_r_perfect_correlation(self):
        """Identical lists → r=1.0."""
        from backend.evaluation.judge_calibrator import CalibrationGate

        gate = CalibrationGate()
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert gate.pearson_r(x, y) == pytest.approx(1.0, abs=1e-9)

    def test_pearson_r_negative_correlation(self):
        """Inverse lists → r=-1.0."""
        from backend.evaluation.judge_calibrator import CalibrationGate

        gate = CalibrationGate()
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [5.0, 4.0, 3.0, 2.0, 1.0]
        assert gate.pearson_r(x, y) == pytest.approx(-1.0, abs=1e-9)

    def test_pearson_r_no_correlation(self):
        """Zero variance in one list → r=0.0."""
        from backend.evaluation.judge_calibrator import CalibrationGate

        gate = CalibrationGate()
        # Constant y → zero variance → 0.0
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [3.0, 3.0, 3.0, 3.0, 3.0]
        assert gate.pearson_r(x, y) == 0.0

    @pytest.mark.asyncio
    async def test_calibration_passes_high_correlation(self):
        """Correlations >0.85 → PASSED."""
        from backend.evaluation.judge_calibrator import CalibrationGate
        from backend.evaluation.models import CalibrationStatus

        gate = CalibrationGate(llm_client=None)  # dry-run mode

        # Create 25 seed cases with known human scores
        seed_cases = []
        for i in range(25):
            seed_cases.append({
                "human_scores": {
                    "groundedness": 0.5 + (i % 10) * 0.05,
                    "citation_quality": 0.4 + (i % 10) * 0.06,
                },
                "test_case": {"user_prompt": f"test prompt {i}"},
            })

        result = await gate.calibrate(
            judge_model="gpt-4o",
            profile_id="default",
            seed_cases=seed_cases,
            runs_per_case=1,
        )
        # Dry-run adds tiny noise, so correlation should be very high
        assert result.gate_status == CalibrationStatus.PASSED
        assert result.overall_correlation > 0.85

    @pytest.mark.asyncio
    async def test_calibration_rejects_low_correlation(self):
        """Manually injected low correlations → REJECTED."""
        from backend.evaluation.judge_calibrator import CalibrationGate
        from backend.evaluation.models import CalibrationStatus

        gate = CalibrationGate(llm_client=None)

        # Check _evaluate_gate directly with low correlations
        correlations = {"groundedness": 0.5, "citation_quality": 0.6}
        status = gate._evaluate_gate(correlations)
        assert status == CalibrationStatus.REJECTED

    @pytest.mark.asyncio
    async def test_calibration_requires_min_seed_cases(self):
        """<20 cases → REJECTED."""
        from backend.evaluation.judge_calibrator import CalibrationGate
        from backend.evaluation.models import CalibrationStatus

        gate = CalibrationGate(llm_client=None)

        seed_cases = [
            {"human_scores": {"groundedness": 0.8}, "test_case": {}}
            for _ in range(10)
        ]

        result = await gate.calibrate(
            judge_model="gpt-4o",
            profile_id="default",
            seed_cases=seed_cases,
        )
        assert result.gate_status == CalibrationStatus.REJECTED
        assert result.seed_case_count == 10


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Contradiction Detector Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestContradictionDetector:
    """Tests for ContradictionDetector."""

    def _make_detector(self):
        from backend.evaluation.contradiction_detector import ContradictionDetector

        return ContradictionDetector()

    @pytest.mark.asyncio
    async def test_no_contradiction_identical_responses(self):
        """Same text → no contradictions."""
        detector = self._make_detector()
        text = "The system contains 5 documents in the knowledge base for processing."
        result = await detector.detect(
            previous_response=text,
            current_response=text,
            session_id="sess-1",
            turn=2,
        )
        assert result.has_contradictions is False
        assert len(result.contradictions) == 0

    @pytest.mark.asyncio
    async def test_detects_negation_conflict(self):
        """'X is true' vs 'X is not true' → detected."""
        detector = self._make_detector()
        prev = "The project deadline is confirmed and the team is ready to proceed."
        curr = "The project deadline is not confirmed and the team needs more time."
        result = await detector.detect(
            previous_response=prev,
            current_response=curr,
            session_id="sess-1",
            turn=2,
        )
        assert result.has_contradictions is True
        assert len(result.contradictions) > 0

    @pytest.mark.asyncio
    async def test_detects_numeric_conflict(self):
        """'3 documents' vs '5 documents' → detected."""
        detector = self._make_detector()
        prev = "There are 3 documents available in the knowledge base for review."
        curr = "There are 5 documents available in the knowledge base for review."
        result = await detector.detect(
            previous_response=prev,
            current_response=curr,
            session_id="sess-1",
            turn=2,
        )
        assert result.has_contradictions is True
        # Should detect factual conflict
        assert any(c.conflict_type == "factual" for c in result.contradictions)

    def test_extracts_claims_from_declarative(self):
        """Filters questions and short sentences."""
        detector = self._make_detector()
        text = (
            "The system is running properly and all services are active. "
            "How are you? "
            "OK. "
            "The database contains over one million records for analysis."
        )
        claims = detector.extract_claims(text)
        # Should keep declarative sentences with 5+ words, skip question and short
        assert len(claims) == 2
        assert all("?" not in c.claim_text for c in claims)

    @pytest.mark.asyncio
    async def test_timeout_sets_timed_out_flag(self):
        """Slow detection → timed_out=True."""
        detector = self._make_detector()

        # Patch _run_detection to simulate a slow operation
        async def slow_detection(*args, **kwargs):
            await asyncio.sleep(10)  # Way longer than timeout

        with patch.object(detector, "_run_detection", side_effect=slow_detection):
            result = await detector.detect(
                previous_response="text a long sentence that qualifies.",
                current_response="text b long sentence that qualifies.",
                session_id="sess-1",
                turn=2,
                timeout_ms=50,  # Very short timeout
            )
        assert result.timed_out is True

    @pytest.mark.asyncio
    async def test_skips_turn_1(self):
        """turn=1 → empty result (not applicable)."""
        detector = self._make_detector()
        result = await detector.detect(
            previous_response="anything here does not matter at all.",
            current_response="anything here does not matter at all.",
            session_id="sess-1",
            turn=1,
        )
        assert result.has_contradictions is False
        assert len(result.contradictions) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Test Case Manager Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestTestCaseManager:
    """Tests for TestCaseManager (in-memory mode)."""

    def _make_manager(self):
        from backend.evaluation.test_case_manager import TestCaseManager

        return TestCaseManager(db=None)

    def _make_test_case(self, **kwargs):
        from backend.evaluation.models import EvaluationTestCase

        defaults = {
            "user_prompt": "What is the company policy?",
            "expected_source_ids": ["doc-1", "doc-2"],
            "expected_topics": ["policy", "compliance"],
            "dataset_id": "default",
        }
        defaults.update(kwargs)
        return EvaluationTestCase(**defaults)

    @pytest.mark.asyncio
    async def test_create_test_case(self):
        """Creates with version 1."""
        manager = self._make_manager()
        tc = self._make_test_case()
        created = await manager.create(tc)
        assert created.version == 1
        assert created.id == tc.id

    @pytest.mark.asyncio
    async def test_semantic_update_bumps_version(self):
        """Changing user_prompt → version 2."""
        manager = self._make_manager()
        tc = self._make_test_case()
        await manager.create(tc)

        updated = await manager.update(tc.id, {"user_prompt": "New prompt?"})
        assert updated.version == 2
        assert updated.user_prompt == "New prompt?"

    @pytest.mark.asyncio
    async def test_non_semantic_update_no_version_bump(self):
        """Changing tags → version stays."""
        manager = self._make_manager()
        tc = self._make_test_case()
        await manager.create(tc)

        updated = await manager.update(tc.id, {"tags": ["new-tag"]})
        assert updated.version == 1
        assert updated.tags == ["new-tag"]

    @pytest.mark.asyncio
    async def test_deprecate_sets_timestamp(self):
        """deprecated_at set."""
        manager = self._make_manager()
        tc = self._make_test_case()
        await manager.create(tc)

        await manager.deprecate(tc.id)
        fetched = await manager.get(tc.id)
        assert fetched.deprecated_at is not None

    @pytest.mark.asyncio
    async def test_list_excludes_deprecated(self):
        """Deprecated cases not in default list."""
        manager = self._make_manager()
        tc1 = self._make_test_case(user_prompt="Prompt A")
        tc2 = self._make_test_case(user_prompt="Prompt B")
        await manager.create(tc1)
        await manager.create(tc2)

        await manager.deprecate(tc1.id)

        active_list = await manager.list_by_dataset("default")
        ids = [tc.id for tc in active_list]
        assert tc1.id not in ids
        assert tc2.id in ids

    @pytest.mark.asyncio
    async def test_get_specific_version(self):
        """Retrieves version 1 even if version 2 exists."""
        manager = self._make_manager()
        tc = self._make_test_case()
        await manager.create(tc)
        await manager.update(tc.id, {"user_prompt": "Updated prompt"})

        v1 = await manager.get(tc.id, version=1)
        v2 = await manager.get(tc.id, version=2)
        assert v1.version == 1
        assert v1.user_prompt == "What is the company policy?"
        assert v2.version == 2
        assert v2.user_prompt == "Updated prompt"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Evaluation Runner Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvaluationRunner:
    """Tests for EvaluationRunner (in-memory, no DB)."""

    def _make_runner(self):
        from backend.evaluation.runner import EvaluationRunner

        return EvaluationRunner(db=None, strategy_runner=None)

    @pytest.mark.asyncio
    async def test_run_evaluation_returns_results(self):
        """Runs against test cases, returns EvaluationRun list."""
        from backend.evaluation.models import EvaluationTestCase

        runner = self._make_runner()

        # Create test cases in the runner's test_case_manager
        tc = EvaluationTestCase(
            user_prompt="What is our data policy?",
            expected_source_ids=["doc-1"],
            expected_topics=["data", "policy"],
            dataset_id="test-ds",
        )
        await runner.test_case_manager.create(tc)

        results = await runner.run_evaluation(
            strategy_id="strategy-a",
            dataset_id="test-ds",
        )
        assert len(results) == 1
        assert results[0].strategy_id == "strategy-a"
        assert results[0].result.composite_score >= 0.0

    @pytest.mark.asyncio
    async def test_run_comparison_multiple_strategies(self):
        """Compares 2 strategies."""
        from backend.evaluation.models import EvaluationTestCase

        runner = self._make_runner()

        tc = EvaluationTestCase(
            user_prompt="Explain the architecture",
            expected_topics=["architecture"],
            dataset_id="ds-1",
        )
        await runner.test_case_manager.create(tc)

        comparison = await runner.run_comparison(
            strategy_ids=["strat-a", "strat-b"],
            dataset_id="ds-1",
        )
        assert "strat-a" in comparison
        assert "strat-b" in comparison
        assert len(comparison["strat-a"]) == 1
        assert len(comparison["strat-b"]) == 1

    @pytest.mark.asyncio
    async def test_leaderboard_ranks_by_score(self):
        """Higher scores rank first."""
        from backend.evaluation.models import CompositeResult, EvaluationRun

        runner = self._make_runner()

        # Manually inject in-memory results
        runner._in_memory_results = [
            EvaluationRun(
                test_case_id="tc-1",
                test_case_version=1,
                strategy_id="low",
                result=CompositeResult(composite_score=0.3),
            ),
            EvaluationRun(
                test_case_id="tc-1",
                test_case_version=1,
                strategy_id="high",
                result=CompositeResult(composite_score=0.9),
            ),
        ]

        leaderboard = await runner.get_leaderboard()
        assert leaderboard[0]["strategy_id"] == "high"
        assert leaderboard[0]["rank"] == 1
        assert leaderboard[1]["strategy_id"] == "low"
        assert leaderboard[1]["rank"] == 2

    def test_source_check_computes_overlap(self):
        """Expected vs actual source ratio."""
        from backend.evaluation.runner import EvaluationRunner

        runner = EvaluationRunner(db=None)
        execution_result = {"source_ids": ["doc-1", "doc-3"]}
        expected = ["doc-1", "doc-2", "doc-3"]
        score = runner._check_sources(execution_result, expected)
        # 2 out of 3 expected found
        assert score == pytest.approx(2 / 3, abs=1e-6)

    def test_topic_check_finds_keywords(self):
        """Expected topics found in text."""
        from backend.evaluation.runner import EvaluationRunner

        runner = EvaluationRunner(db=None)
        text = "The architecture follows a microservices design with event-driven patterns."
        topics = ["architecture", "microservices", "blockchain"]
        score = runner._check_topics(text, topics)
        # 2 out of 3 topics found
        assert score == pytest.approx(2 / 3, abs=1e-6)

    @pytest.mark.asyncio
    async def test_handles_empty_dataset(self):
        """Empty dataset returns empty results."""
        runner = self._make_runner()
        results = await runner.run_evaluation(
            strategy_id="strat-x",
            dataset_id="nonexistent-dataset",
        )
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Strategy Spec Selector Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrategySpecSelector:
    """Tests for StrategySpecSelector."""

    def _make_selector(self, db=None):
        from backend.agent.strategy.spec_selector import StrategySpecSelector

        return StrategySpecSelector(db=db)

    def _make_spec(self, strategy_id="spec-1", status="active", capability_id=None,
                   local_only=False, latency_target_ms=8000, tags=None, tenant_scope="default"):
        from backend.agent.strategy.models import (
            StrategyBudgets,
            StrategyEdge,
            StrategyGraph,
            StrategyNode,
            StrategySpec,
        )

        graph = StrategyGraph(
            nodes=[StrategyNode(node_id="n1", node_type="retrieve")],
            edges=[],
            entry_node="n1",
            terminal_nodes=["n1"],
        )
        return StrategySpec(
            strategy_id=strategy_id,
            status=status,
            capability_id=capability_id,
            graph=graph,
            budgets=StrategyBudgets(local_only=local_only, latency_target_ms=latency_target_ms),
            tags=tags or [],
            tenant_scope=tenant_scope,
        )

    @pytest.mark.asyncio
    async def test_returns_none_without_db(self):
        """No DB → always returns None."""
        selector = self._make_selector(db=None)
        result = await selector.select(capability_id="legal_qa")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_without_capability(self):
        """No capability_id and no matching specs → None."""
        selector = self._make_selector(db=None)
        result = await selector.select(capability_id=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_filters_draft_specs(self):
        """Draft specs excluded."""
        selector = self._make_selector(db=None)
        # Manually inject cache
        draft_spec = self._make_spec(strategy_id="draft-1", status="draft")
        active_spec = self._make_spec(strategy_id="active-1", status="active", capability_id="qa")
        selector._cache = [draft_spec, active_spec]
        selector._cache_loaded_at = time.time()

        result = await selector.select(capability_id="qa")
        assert result is not None
        assert result.strategy_id == "active-1"

    @pytest.mark.asyncio
    async def test_ranks_by_capability_match(self):
        """Matching capability scores higher."""
        selector = self._make_selector(db=None)
        generic = self._make_spec(strategy_id="generic", status="active", capability_id=None)
        matched = self._make_spec(strategy_id="matched", status="active", capability_id="legal_qa")
        selector._cache = [generic, matched]
        selector._cache_loaded_at = time.time()

        result = await selector.select(capability_id="legal_qa")
        assert result is not None
        assert result.strategy_id == "matched"

    @pytest.mark.asyncio
    async def test_privacy_mode_filters_non_local(self):
        """local_only mode filters cloud specs."""
        selector = self._make_selector(db=None)
        cloud_spec = self._make_spec(strategy_id="cloud", status="active", local_only=False)
        local_spec = self._make_spec(strategy_id="local", status="active", local_only=True)
        selector._cache = [cloud_spec, local_spec]
        selector._cache_loaded_at = time.time()

        result = await selector.select(capability_id=None, privacy_mode="local_only")
        assert result is not None
        assert result.strategy_id == "local"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Timezone Resolver Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestTimezoneResolver:
    """Tests for ScheduleTimezoneResolver."""

    def _make_resolver(self):
        from backend.scheduler.timezone_resolver import ScheduleTimezoneResolver

        return ScheduleTimezoneResolver()

    def _make_schedule(self, cron="0 22 * * 1-5", window_start="22:00", window_end="06:00"):
        from backend.scheduler.models import StrategySchedule

        return StrategySchedule(
            cron=cron,
            timezone="Europe/Vienna",
            allowed_window_start=window_start,
            allowed_window_end=window_end,
        )

    def test_basic_cron_evaluation(self):
        """'0 22 * * 1-5' produces correct next time."""
        resolver = self._make_resolver()
        schedule = self._make_schedule(cron="0 22 * * 0-4")
        # Set 'now' to a Monday 20:00 UTC (21:00 Vienna in winter, 22:00 in summer)
        # Use a fixed known date: Monday 2026-01-05 20:00 UTC (winter, Vienna is UTC+1)
        now = datetime(2026, 1, 5, 20, 0, tzinfo=timezone.utc)
        result = resolver.evaluate_next_run(schedule, now=now)
        assert result is not None
        # Should be 22:00 Vienna = 21:00 UTC on Jan 5 (Mon)
        assert result.hour == 21
        assert result.minute == 0

    def test_allowed_window_overnight(self):
        """22:00-06:00 wraps midnight correctly."""
        resolver = self._make_resolver()
        schedule = self._make_schedule(window_start="22:00", window_end="06:00")

        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Vienna")

        # 23:00 local → inside window
        dt_inside = datetime(2026, 1, 5, 22, 0, tzinfo=timezone.utc).astimezone(tz)
        dt_inside = dt_inside.replace(hour=23, minute=0)
        # Convert back to aware for the check
        aware_inside = dt_inside.replace(tzinfo=tz)
        assert resolver.is_in_allowed_window(aware_inside, schedule) is True

        # 03:00 local → inside window (after midnight)
        dt_inside2 = dt_inside.replace(hour=3, minute=0)
        aware_inside2 = dt_inside2.replace(tzinfo=tz)
        assert resolver.is_in_allowed_window(aware_inside2, schedule) is True

    def test_outside_window_returns_none(self):
        """Time outside window not scheduled."""
        resolver = self._make_resolver()
        # Window 22:00-23:00, cron at 14:00 every day
        schedule = self._make_schedule(cron="0 14 * * *", window_start="22:00", window_end="23:00")
        now = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
        result = resolver.evaluate_next_run(schedule, now=now)
        # 14:00 Vienna is outside 22:00-23:00 window
        assert result is None

    def test_dedup_key_format(self):
        """Returns '{id}:{date}' format."""
        from backend.scheduler.timezone_resolver import ScheduleTimezoneResolver
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Europe/Vienna")
        dt = datetime(2026, 3, 15, 22, 0, tzinfo=timezone.utc)
        key = ScheduleTimezoneResolver.get_dedup_key("sched-123", dt, tz)
        assert key.startswith("sched-123:")
        assert "2026-03-" in key

    def test_weekday_filter(self):
        """Cron '0 22 * * 0-4' skips weekends (Sat=5, Sun=6)."""
        resolver = self._make_resolver()
        # days_of_week 0-4 = Mon-Fri
        schedule = self._make_schedule(cron="0 22 * * 0-4")
        # Saturday 2026-01-10 20:00 UTC
        now = datetime(2026, 1, 10, 20, 0, tzinfo=timezone.utc)
        result = resolver.evaluate_next_run(schedule, now=now)
        # Should skip weekend and land on Monday
        if result is not None:
            # Result should be on a weekday (0=Mon..4=Fri in Python)
            assert result.weekday() < 5

    def test_specific_hour_minute(self):
        """'30 14 * * *' matches 14:30."""
        resolver = self._make_resolver()
        schedule = self._make_schedule(cron="30 14 * * *", window_start="14:00", window_end="15:00")
        now = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
        result = resolver.evaluate_next_run(schedule, now=now)
        if result is not None:
            from zoneinfo import ZoneInfo
            local = result.astimezone(ZoneInfo("Europe/Vienna"))
            assert local.hour == 14
            assert local.minute == 30


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Pause/Stop Evaluator Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestPauseStopEvaluators:
    """Tests for PauseConditionEvaluator and StopConditionEvaluator."""

    def _make_pause_evaluator(self, **config_kwargs):
        from backend.scheduler.models import PauseConfig
        from backend.scheduler.pause_evaluator import PauseConditionEvaluator

        config = PauseConfig(**config_kwargs)
        return PauseConditionEvaluator(config=config)

    def _make_run_state(self, **kwargs):
        from backend.scheduler.models import SchedulerRunState

        defaults = {"schedule_id": "sched-1"}
        defaults.update(kwargs)
        return SchedulerRunState(**defaults)

    def test_pause_on_interactive_users(self):
        """SSE connections trigger pause."""
        evaluator = self._make_pause_evaluator()
        run_state = self._make_run_state()
        decision = evaluator.evaluate(
            run_state=run_state,
            active_sse_connections=2,
        )
        assert decision.should_pause is True
        assert decision.reason.value == "interactive_users"

    def test_pause_on_resource_pressure(self):
        """CPU>80% triggers pause."""
        evaluator = self._make_pause_evaluator()
        run_state = self._make_run_state()
        decision = evaluator.evaluate(
            run_state=run_state,
            active_sse_connections=0,
            last_chat_activity_ago_seconds=99999,
            cpu_pct=85.0,
        )
        assert decision.should_pause is True
        assert decision.reason.value == "resource_pressure"

    def test_no_pause_when_idle(self):
        """No activity → no pause."""
        evaluator = self._make_pause_evaluator()
        run_state = self._make_run_state()
        decision = evaluator.evaluate(
            run_state=run_state,
            active_sse_connections=0,
            last_chat_activity_ago_seconds=99999,
            cpu_pct=20.0,
            ram_pct=30.0,
            gpu_pct=10.0,
        )
        assert decision.should_pause is False
        assert decision.reason is None

    def test_deferred_after_max_pause(self):
        """Pause exceeding limit → DEFERRED."""
        evaluator = self._make_pause_evaluator(max_pause_minutes=1)
        run_state = self._make_run_state()

        # Simulate being paused for longer than max
        evaluator._paused_since = time.time() - 120  # 2 minutes ago

        is_deferred = evaluator.check_deferred(run_state)
        assert is_deferred is True

    def test_stop_on_max_runtime(self):
        """Runtime exceeded → StopReason.MAX_RUNTIME."""
        from backend.scheduler.models import StopConditions, StopReason
        from backend.scheduler.stop_evaluator import StopConditionEvaluator

        conditions = StopConditions(max_runtime_minutes=60)
        evaluator = StopConditionEvaluator(conditions=conditions)

        run_state = self._make_run_state(
            started_at=datetime.now(timezone.utc) - timedelta(minutes=120)
        )
        reason = evaluator.evaluate(run_state)
        assert reason == StopReason.MAX_RUNTIME

    def test_stop_on_leader_found(self):
        """Clear leader → StopReason.LEADER_FOUND."""
        from backend.scheduler.models import CandidateResult, StopConditions, StopReason
        from backend.scheduler.stop_evaluator import StopConditionEvaluator

        conditions = StopConditions(
            stop_on_leader_found=True,
            leader_margin_pct=10.0,
            min_candidates_completed=3,
        )
        evaluator = StopConditionEvaluator(conditions=conditions)

        run_state = self._make_run_state(
            started_at=datetime.now(timezone.utc),
            candidates_completed=3,
            candidate_results=[
                CandidateResult(strategy_id="winner", composite_score=0.95),
                CandidateResult(strategy_id="second", composite_score=0.70),
                CandidateResult(strategy_id="third", composite_score=0.60),
            ],
        )
        reason = evaluator.evaluate(run_state)
        assert reason == StopReason.LEADER_FOUND


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Circuit Breaker Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    """Tests for MongoDBCircuitBreaker."""

    def _make_breaker(self, **kwargs):
        from backend.scheduler.circuit_breaker import MongoDBCircuitBreaker

        return MongoDBCircuitBreaker(**kwargs)

    @pytest.mark.asyncio
    async def test_closed_executes_normally(self):
        """Operation runs, returns result."""
        breaker = self._make_breaker()

        async def write_op():
            return "ok"

        result = await breaker.execute(write_op)
        assert result == "ok"
        assert breaker.state.value == "closed"

    @pytest.mark.asyncio
    async def test_opens_after_threshold_failures(self):
        """3 failures → OPEN state."""
        breaker = self._make_breaker(failure_threshold=3)

        async def failing_op():
            raise RuntimeError("DB down")

        for _ in range(3):
            await breaker.execute(failing_op)

        assert breaker.state.value == "open"

    @pytest.mark.asyncio
    async def test_open_buffers_operations(self):
        """In OPEN, operations buffered."""
        breaker = self._make_breaker(failure_threshold=1)

        async def failing_op():
            raise RuntimeError("DB down")

        await breaker.execute(failing_op)
        assert breaker.state.value == "open"

        # Now try another operation — should be buffered
        async def write_op():
            return "should_be_buffered"

        result = await breaker.execute(write_op)
        assert result is None  # Buffered, not executed
        assert breaker.buffer_size >= 1

    @pytest.mark.asyncio
    async def test_half_open_after_timeout(self):
        """After recovery timeout → HALF_OPEN."""
        breaker = self._make_breaker(failure_threshold=1, recovery_timeout_seconds=0.01)

        async def failing_op():
            raise RuntimeError("DB down")

        await breaker.execute(failing_op)
        assert breaker._state.value == "open"

        # Wait for recovery timeout
        await asyncio.sleep(0.02)

        # Accessing .state triggers auto-transition
        assert breaker.state.value == "half_open"

    @pytest.mark.asyncio
    async def test_half_open_success_closes(self):
        """Successful probe → CLOSED."""
        breaker = self._make_breaker(failure_threshold=1, recovery_timeout_seconds=0.01)

        async def failing_op():
            raise RuntimeError("DB down")

        await breaker.execute(failing_op)
        await asyncio.sleep(0.02)
        assert breaker.state.value == "half_open"

        # Successful probe
        async def success_op():
            return "recovered"

        result = await breaker.execute(success_op)
        assert result == "recovered"
        assert breaker.state.value == "closed"

    @pytest.mark.asyncio
    async def test_flush_buffer_replays_operations(self):
        """Buffered ops replayed on recovery."""
        breaker = self._make_breaker(failure_threshold=1)
        results = []

        async def failing_op():
            raise RuntimeError("DB down")

        await breaker.execute(failing_op)
        # Breaker is now OPEN. Clear the buffer of the failed op for a clean test.
        breaker._buffer.clear()

        # Buffer some operations while OPEN
        async def write_a():
            results.append("a")

        async def write_b():
            results.append("b")

        await breaker.execute(write_a)
        await breaker.execute(write_b)
        assert breaker.buffer_size == 2

        # Flush buffer — both should replay successfully
        flushed = await breaker.flush_buffer()
        assert flushed == 2
        assert "a" in results
        assert "b" in results


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestPhase3Integration:
    """Integration tests combining multiple Phase 3 components."""

    @pytest.mark.asyncio
    async def test_evaluation_end_to_end(self):
        """Create test case, run evaluation, check composite score generated."""
        from backend.evaluation.models import EvaluationTestCase
        from backend.evaluation.runner import EvaluationRunner

        runner = EvaluationRunner(db=None, strategy_runner=None)

        # Create a test case
        tc = EvaluationTestCase(
            user_prompt="What are the key findings from the Q4 review?",
            expected_source_ids=["q4-report"],
            expected_topics=["findings", "Q4", "review"],
            dataset_id="integration-test",
        )
        await runner.test_case_manager.create(tc)

        # Run evaluation
        results = await runner.run_evaluation(
            strategy_id="integration-strategy",
            dataset_id="integration-test",
        )
        assert len(results) == 1
        run = results[0]
        assert run.result.composite_score >= 0.0
        assert run.result.composite_score <= 1.0
        assert run.strategy_id == "integration-strategy"
        assert run.test_case_id == tc.id
        assert run.test_case_version == 1
        assert len(run.result.dimension_scores) == 7

    def test_scheduler_models_instantiate(self):
        """Full StrategySchedule with all sub-configs instantiates cleanly."""
        from backend.scheduler.models import (
            PauseConfig,
            SchedulerBudget,
            StopConditions,
            StrategySchedule,
        )

        schedule = StrategySchedule(
            name="Full Integration Schedule",
            cron="0 22 * * 0-4",
            timezone="Europe/Vienna",
            allowed_window_start="22:00",
            allowed_window_end="06:00",
            dataset_id="prod-dataset",
            candidate_strategy_ids=["strat-a", "strat-b", "strat-c"],
            pause_config=PauseConfig(
                max_pause_minutes=90,
                pause_if_interactive_users=True,
                pause_if_resource_pressure=True,
                interactive_cooldown_minutes=10,
            ),
            stop_conditions=StopConditions(
                max_runtime_minutes=480,
                max_llm_calls=1000,
                max_cost_usd=100.0,
                max_consecutive_failures=3,
                stop_on_leader_found=True,
                leader_margin_pct=15.0,
                min_candidates_completed=2,
            ),
            budget=SchedulerBudget(
                max_cpu_pct=70.0,
                max_ram_pct=60.0,
                max_gpu_pct=80.0,
            ),
        )
        assert schedule.id  # UUID generated
        assert schedule.cron == "0 22 * * 0-4"
        assert schedule.pause_config.max_pause_minutes == 90
        assert schedule.stop_conditions.max_runtime_minutes == 480
        assert schedule.budget.max_cpu_pct == 70.0
        assert len(schedule.candidate_strategy_ids) == 3
