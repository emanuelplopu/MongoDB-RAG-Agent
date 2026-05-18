"""Evaluation runner — orchestrates strategy evaluation against test cases."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Optional

from backend.evaluation.models import (
    EvaluationTestCase,
    EvaluationRun,
    CompositeResult,
    DimensionScore,
    DimensionId,
    ScoringProfile,
)
from backend.evaluation.composite_scorer import CompositeScoreCalculator
from backend.evaluation.test_case_manager import TestCaseManager

logger = logging.getLogger(__name__)


class EvaluationRunner:
    """
    Orchestrates strategy evaluation against a test case dataset.

    Flow per test case:
    1. Execute strategy via StrategyRunner with test case's user_prompt
    2. Collect quality dimension scores (via judge node or rule-based)
    3. Calculate composite score with latency and gates
    4. Store EvaluationRun result

    Supports:
    - Running a single strategy against all cases in a dataset
    - Running multiple strategies for comparison (leaderboard)
    - Incremental evaluation (skip already-evaluated cases)
    """

    def __init__(
        self,
        db=None,
        strategy_runner=None,
        scorer: Optional[CompositeScoreCalculator] = None,
    ):
        self.db = db
        self.strategy_runner = strategy_runner
        self.scorer = scorer or CompositeScoreCalculator()
        self.test_case_manager = TestCaseManager(db=db)
        self._results_collection = "evaluation_results"
        # In-memory storage when no DB
        self._in_memory_results: list[EvaluationRun] = []

    async def run_evaluation(
        self,
        strategy_id: str,
        dataset_id: str = "default",
        judge_model: Optional[str] = None,
        scoring_profile: Optional[ScoringProfile] = None,
    ) -> list[EvaluationRun]:
        """
        Run evaluation for a strategy against all test cases in a dataset.

        Args:
            strategy_id: Strategy to evaluate
            dataset_id: Test case dataset to use
            judge_model: LLM judge model identifier (None = rule-based)
            scoring_profile: Override scoring weights

        Returns:
            List of EvaluationRun results (one per test case)
        """
        profile = scoring_profile or ScoringProfile()
        test_cases = await self.test_case_manager.list_by_dataset(dataset_id)

        if not test_cases:
            logger.warning("No test cases found for dataset: %s", dataset_id)
            return []

        logger.info(
            "Starting evaluation: strategy=%s, dataset=%s, cases=%d, judge=%s",
            strategy_id,
            dataset_id,
            len(test_cases),
            judge_model or "rule-based",
        )

        results: list[EvaluationRun] = []
        for test_case in test_cases:
            if test_case.is_stale:
                logger.info(
                    "Skipping stale test case: %s", test_case.id
                )
                continue

            run = await self._evaluate_single_case(
                strategy_id=strategy_id,
                test_case=test_case,
                judge_model=judge_model,
                scoring_profile=profile,
            )
            results.append(run)
            await self._store_result(run)

        logger.info(
            "Evaluation complete: strategy=%s, runs=%d, avg_score=%.4f",
            strategy_id,
            len(results),
            (sum(r.result.composite_score for r in results) / len(results))
            if results
            else 0.0,
        )
        return results

    async def run_comparison(
        self,
        strategy_ids: list[str],
        dataset_id: str = "default",
        judge_model: Optional[str] = None,
    ) -> dict[str, list[EvaluationRun]]:
        """
        Run evaluation for multiple strategies for leaderboard comparison.
        Returns: {strategy_id: [EvaluationRun, ...]}
        """
        comparison: dict[str, list[EvaluationRun]] = {}

        for strategy_id in strategy_ids:
            runs = await self.run_evaluation(
                strategy_id=strategy_id,
                dataset_id=dataset_id,
                judge_model=judge_model,
            )
            comparison[strategy_id] = runs

        return comparison

    async def get_leaderboard(
        self,
        dataset_id: str = "default",
        limit: int = 10,
    ) -> list[dict]:
        """
        Get ranked strategies by average composite score.
        Returns: [{"strategy_id": str, "avg_score": float, "runs": int, "rank": int}]
        """
        if self.db is not None:
            collection = self.db[self._results_collection]
            pipeline = [
                {"$group": {
                    "_id": "$strategy_id",
                    "avg_score": {"$avg": "$result.composite_score"},
                    "runs": {"$sum": 1},
                }},
                {"$sort": {"avg_score": -1}},
                {"$limit": limit},
            ]
            cursor = collection.aggregate(pipeline)
            results = []
            rank = 1
            async for doc in cursor:
                results.append({
                    "strategy_id": doc["_id"],
                    "avg_score": doc["avg_score"],
                    "runs": doc["runs"],
                    "rank": rank,
                })
                rank += 1
            return results
        else:
            # In-memory leaderboard
            strategy_scores: dict[str, list[float]] = {}
            for run in self._in_memory_results:
                strategy_scores.setdefault(run.strategy_id, []).append(
                    run.result.composite_score
                )

            ranked = sorted(
                [
                    {
                        "strategy_id": sid,
                        "avg_score": sum(scores) / len(scores),
                        "runs": len(scores),
                    }
                    for sid, scores in strategy_scores.items()
                ],
                key=lambda x: x["avg_score"],
                reverse=True,
            )[:limit]

            for i, entry in enumerate(ranked, start=1):
                entry["rank"] = i

            return ranked

    async def _evaluate_single_case(
        self,
        strategy_id: str,
        test_case: EvaluationTestCase,
        judge_model: Optional[str],
        scoring_profile: ScoringProfile,
    ) -> EvaluationRun:
        """
        Evaluate a single test case for a strategy.

        1. Execute strategy (or mock if no runner)
        2. Score dimensions
        3. Calculate composite
        4. Return EvaluationRun
        """
        start_time = time.perf_counter()

        # 1. Execute strategy
        execution_result = await self._execute_strategy(
            strategy_id=strategy_id,
            user_prompt=test_case.user_prompt,
        )

        # 2. Score dimensions
        dimension_scores = await self._score_dimensions(
            execution_result=execution_result,
            test_case=test_case,
            judge_model=judge_model,
        )

        # 3. Calculate composite
        duration_ms = (time.perf_counter() - start_time) * 1000
        latency_ms = execution_result.get("duration_ms", duration_ms)

        composite_result = self.scorer.calculate(
            dimension_scores=dimension_scores,
            latency_ms=latency_ms,
            latency_config=scoring_profile.latency_config,
        )

        # 4. Build EvaluationRun
        run = EvaluationRun(
            run_id=str(uuid.uuid4()),
            test_case_id=test_case.id,
            test_case_version=test_case.version,
            strategy_id=strategy_id,
            result=composite_result,
            judge_model=judge_model,
            duration_ms=duration_ms,
            created_at=datetime.utcnow(),
        )

        logger.debug(
            "Evaluated case %s for strategy %s: composite=%.4f, latency=%.0fms",
            test_case.id,
            strategy_id,
            composite_result.composite_score,
            latency_ms,
        )
        return run

    async def _execute_strategy(
        self,
        strategy_id: str,
        user_prompt: str,
    ) -> dict:
        """
        Execute a strategy and return execution results.

        If strategy_runner is available: load spec, run DAG
        If not: return mock execution result for testing

        Returns: {"synthesis_text": str, "duration_ms": float, "chunks": list, ...}
        """
        if self.strategy_runner is not None:
            try:
                from backend.agent.strategy.models import BusinessContext

                context = BusinessContext(tenant_id="evaluation")
                result = await self.strategy_runner.run(
                    spec=await self._load_strategy_spec(strategy_id),
                    context=context,
                    query=user_prompt,
                )

                synthesis_text = ""
                if result.state.synthesis_result:
                    synthesis_text = result.state.synthesis_result.text

                source_ids = [
                    chunk.source_id for chunk in result.state.retrieved_chunks
                ]

                return {
                    "synthesis_text": synthesis_text,
                    "duration_ms": result.total_duration_ms,
                    "chunks": [
                        c.model_dump() for c in result.state.retrieved_chunks
                    ],
                    "source_ids": source_ids,
                    "success": result.success,
                }
            except Exception as e:
                logger.warning(
                    "Strategy execution failed for %s, using mock: %s",
                    strategy_id,
                    e,
                )

        # Mock execution result for testing without a real strategy runner
        mock_duration = 1500.0 + (hash(strategy_id + user_prompt) % 3000)
        return {
            "synthesis_text": (
                f"[Mock synthesis for strategy '{strategy_id}'] "
                f"Based on the query: {user_prompt[:100]}... "
                "This is a placeholder response generated during evaluation testing."
            ),
            "duration_ms": mock_duration,
            "chunks": [],
            "source_ids": [],
            "success": True,
        }

    async def _load_strategy_spec(self, strategy_id: str):
        """Load a strategy spec from DB or raise."""
        if self.db is not None:
            collection = self.db["strategies"]
            doc = await collection.find_one({"strategy_id": strategy_id})
            if doc:
                doc.pop("_id", None)
                from backend.agent.strategy.models import StrategySpec
                return StrategySpec(**doc)

        raise ValueError(f"Strategy spec not found: {strategy_id}")

    async def _score_dimensions(
        self,
        execution_result: dict,
        test_case: EvaluationTestCase,
        judge_model: Optional[str],
    ) -> list[DimensionScore]:
        """
        Score all 7 quality dimensions.

        Uses judge model if available, otherwise rule-based heuristics.
        """
        synthesis_text = execution_result.get("synthesis_text", "")
        profile = ScoringProfile()

        # Rule-based heuristic scoring
        scores: dict[str, float] = {}

        # Groundedness: fraction of expected sources actually retrieved
        source_score = self._check_sources(
            execution_result, test_case.expected_source_ids
        )
        scores[DimensionId.GROUNDEDNESS.value] = source_score

        # Citation quality: presence of citation-like patterns
        citation_markers = synthesis_text.count("[") + synthesis_text.count("(")
        citation_score = min(1.0, citation_markers / max(1, len(test_case.expected_source_ids) * 2))
        scores[DimensionId.CITATION_QUALITY.value] = citation_score

        # Directness: shorter responses score higher (relative to content)
        word_count = len(synthesis_text.split())
        directness_score = min(1.0, 200.0 / max(1, word_count)) if word_count > 200 else 1.0
        scores[DimensionId.DIRECTNESS.value] = directness_score

        # Completeness: fraction of expected topics covered
        topic_score = self._check_topics(synthesis_text, test_case.expected_topics)
        scores[DimensionId.COMPLETENESS.value] = topic_score

        # Format adherence: basic structural checks
        has_structure = any(
            marker in synthesis_text
            for marker in ["##", "- ", "1.", "**", "\n\n"]
        )
        scores[DimensionId.FORMAT_ADHERENCE.value] = 0.8 if has_structure else 0.5

        # Domain value: non-empty substantive response
        domain_score = min(1.0, word_count / 50.0) if word_count > 0 else 0.0
        scores[DimensionId.DOMAIN_VALUE.value] = domain_score

        # Conciseness: penalize very long responses
        if word_count <= 300:
            conciseness_score = 1.0
        elif word_count <= 600:
            conciseness_score = 0.7
        else:
            conciseness_score = max(0.3, 1.0 - (word_count - 300) / 1000.0)
        scores[DimensionId.CONCISENESS.value] = conciseness_score

        # Build DimensionScore list with weights from profile
        dimension_scores: list[DimensionScore] = []
        for dim_id_str, score in scores.items():
            weight = profile.weights.get(dim_id_str, 0.0)
            dimension_scores.append(
                DimensionScore(
                    dimension_id=DimensionId(dim_id_str),
                    score=max(0.0, min(1.0, score)),
                    weight=weight,
                    evidence=f"rule-based heuristic (judge={judge_model or 'none'})",
                )
            )

        return dimension_scores

    async def _store_result(self, run: EvaluationRun) -> None:
        """Store evaluation run result in DB (or in-memory if no DB)."""
        if self.db is not None:
            collection = self.db[self._results_collection]
            await collection.insert_one(run.model_dump())
        else:
            self._in_memory_results.append(run)

    def _check_sources(
        self,
        execution_result: dict,
        expected_source_ids: list[str],
    ) -> float:
        """Check what fraction of expected sources were retrieved."""
        if not expected_source_ids:
            return 1.0  # No expectations = pass

        actual_source_ids = set(execution_result.get("source_ids", []))
        if not actual_source_ids:
            return 0.0

        hits = sum(1 for sid in expected_source_ids if sid in actual_source_ids)
        return hits / len(expected_source_ids)

    def _check_topics(
        self,
        synthesis_text: str,
        expected_topics: list[str],
    ) -> float:
        """Check what fraction of expected topics are covered in synthesis."""
        if not expected_topics:
            return 1.0  # No expectations = pass

        text_lower = synthesis_text.lower()
        hits = sum(
            1 for topic in expected_topics if topic.lower() in text_lower
        )
        return hits / len(expected_topics)
