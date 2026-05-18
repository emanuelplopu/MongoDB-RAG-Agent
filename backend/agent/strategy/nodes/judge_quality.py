"""JudgeQualityExecutor — LLM-based quality scoring with rule-based fallback."""
from __future__ import annotations

import json
import logging
import re
import statistics
from typing import Any, Optional

from backend.agent.strategy.models import (
    NodeOutput,
    StrategyNode,
    StrategyRunState,
    ValidationIssue,
    ValidationResult,
)
from backend.agent.strategy.nodes.base import NodeExecutor
from backend.evaluation.models import DimensionId, DimensionScore

logger = logging.getLogger(__name__)

# Default dimension weights (rule-based fallback)
_DEFAULT_WEIGHTS: dict[str, float] = {
    "completeness": 0.3,
    "coherence": 0.3,
    "citation_density": 0.2,
    "language": 0.2,
}

# Truncation markers that indicate an incomplete answer
_TRUNCATION_MARKERS = re.compile(
    r"(\.\.\.$|…$|<truncated>|<cut off>|\[continued\])",
    re.IGNORECASE,
)

# Variance threshold for LLM judge determinism checks
_VARIANCE_THRESHOLD = 0.05


class JudgeQualityExecutor(NodeExecutor):
    """
    LLM-based quality judge evaluating 7 dimensions.

    Determinism strategy:
    - OpenAI: temperature=0, seed=42
    - Ollama: median-of-3 runs

    Variance threshold: ±0.05 per dimension across repeated runs.
    Falls back to rule-based scoring if LLM call fails.
    """

    def __init__(self, model_registry=None, llm_client=None, llm_helper=None):
        self.model_registry = model_registry
        self.llm_client = llm_client or llm_helper  # accept either name

    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        """Execute quality judgment."""
        accessor = self.get_state_accessor(state)
        synthesis = accessor.get_synthesis()

        if synthesis is None:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=ValidationResult(
                    validator_id="quality_judge",
                    passed=True,
                    score=0.0,
                    metadata={"reason": "no_synthesis"},
                ).model_dump(),
                tokens_used=0,
            )

        # Try LLM judge first
        if self.llm_client and self.model_registry:
            try:
                result = await self._judge_with_llm(node, accessor, synthesis)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"LLM judge failed, falling back to rule-based: {e}")

        # Fallback: rule-based scoring (existing logic)
        return await self._judge_rule_based(node, accessor, synthesis)

    # ------------------------------------------------------------------
    # LLM Judge
    # ------------------------------------------------------------------

    async def _judge_with_llm(self, node, accessor, synthesis) -> Optional[NodeOutput]:
        """
        Call LLM judge for 7-dimension evaluation.

        1. Build evaluation prompt with synthesis text, query, and dimension definitions
        2. Call LLM with determinism params
        3. Parse response into dimension scores
        4. Validate variance (run 3x for Ollama)
        5. Return ValidationResult with dimension breakdown
        """
        query = accessor.get_query()
        chunks = accessor.get_chunks()
        chunks_summary = self._build_chunks_summary(chunks)

        prompt = self._build_judge_prompt(query, synthesis.text, chunks_summary)

        # Get model config for "judge" role
        role_config = self.model_registry.resolve_with_fallback("judge")
        model_config = {
            "provider": role_config.provider,
            "model": role_config.model,
            "timeout_ms": role_config.timeout_ms,
        }

        # Determine determinism strategy based on provider
        is_ollama = role_config.provider == "ollama"
        runs = 3 if is_ollama else 1

        dimension_scores = await self._run_with_variance_check(prompt, model_config, runs=runs)
        if not dimension_scores:
            return None

        # Compute weighted aggregate using evaluation model weights
        total_weight = sum(ds.weight for ds in dimension_scores)
        if total_weight > 0:
            weighted_sum = sum(ds.score * ds.weight for ds in dimension_scores) / total_weight
        else:
            weighted_sum = sum(ds.score for ds in dimension_scores) / max(len(dimension_scores), 1)

        score = round(weighted_sum, 4)
        passed = score >= 0.5

        # Build issues from low-scoring dimensions
        issues: list[ValidationIssue] = []
        for ds in dimension_scores:
            if ds.score < 0.5:
                issues.append(
                    ValidationIssue(
                        issue_type=f"low_{ds.dimension_id.value}",
                        severity="warning",
                        message=f"{ds.dimension_id.value} score is low ({ds.score:.2f}): {ds.evidence or 'N/A'}",
                    )
                )

        result = ValidationResult(
            validator_id="quality_judge_llm",
            passed=passed,
            score=score,
            issues=issues,
            metadata={
                "dimensions": {
                    ds.dimension_id.value: {
                        "score": round(ds.score, 4),
                        "weight": ds.weight,
                        "evidence": ds.evidence,
                    }
                    for ds in dimension_scores
                },
                "judge_model": f"{role_config.provider}/{role_config.model}",
                "determinism_runs": runs,
            },
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=result.model_dump(),
            model_used=f"{role_config.provider}/{role_config.model}",
            tokens_used=0,  # Updated by caller if tracked
        )

    def _build_judge_prompt(self, query: str, synthesis_text: str, chunks_summary: str) -> str:
        """
        Build the 7-dimension evaluation prompt.

        Prompt template asks the judge to score each dimension 0.0-1.0 with evidence.
        Expected response format: JSON with dimensions object.
        """
        return f"""You are a quality judge evaluating an AI-generated answer.

## Task
Score the following answer on 7 quality dimensions, each from 0.0 to 1.0.
Provide brief evidence for each score.

## Dimensions
- groundedness: Is the answer grounded in the provided sources? (weight: 0.25)
- citation_quality: Are citations accurate, relevant, and sufficient? (weight: 0.20)
- directness: Does the answer directly address the question? (weight: 0.10)
- completeness: Are all aspects of the question covered? (weight: 0.15)
- format_adherence: Does the output follow the requested format? (weight: 0.15)
- domain_value: Does the answer demonstrate domain expertise? (weight: 0.10)
- conciseness: Is the answer appropriately concise without losing substance? (weight: 0.05)

## User Question
{query}

## Source Material Summary
{chunks_summary}

## Answer to Evaluate
{synthesis_text}

## Response Format
Respond ONLY with valid JSON in this exact structure:
{{
  "dimensions": {{
    "groundedness": {{"score": 0.0, "evidence": "..."}},
    "citation_quality": {{"score": 0.0, "evidence": "..."}},
    "directness": {{"score": 0.0, "evidence": "..."}},
    "completeness": {{"score": 0.0, "evidence": "..."}},
    "format_adherence": {{"score": 0.0, "evidence": "..."}},
    "domain_value": {{"score": 0.0, "evidence": "..."}},
    "conciseness": {{"score": 0.0, "evidence": "..."}}
  }}
}}"""

    def _parse_judge_response(self, response: str) -> Optional[list[DimensionScore]]:
        """Parse LLM response into list of DimensionScore objects."""
        # Default weights per dimension
        weight_map = {
            "groundedness": 0.25,
            "citation_quality": 0.20,
            "directness": 0.10,
            "completeness": 0.15,
            "format_adherence": 0.15,
            "domain_value": 0.10,
            "conciseness": 0.05,
        }

        try:
            # Try to extract JSON from response (handle markdown code blocks)
            json_match = re.search(r"\{[\s\S]*\}", response)
            if not json_match:
                return None

            data = json.loads(json_match.group())
            dimensions = data.get("dimensions", {})
            if not dimensions:
                return None

            scores: list[DimensionScore] = []
            for dim_id in DimensionId:
                dim_key = dim_id.value
                if dim_key not in dimensions:
                    continue
                dim_data = dimensions[dim_key]
                score_val = float(dim_data.get("score", 0.0))
                score_val = max(0.0, min(1.0, score_val))
                evidence = dim_data.get("evidence", "")

                scores.append(
                    DimensionScore(
                        dimension_id=dim_id,
                        score=score_val,
                        weight=weight_map.get(dim_key, 0.1),
                        evidence=evidence if evidence else None,
                    )
                )

            if len(scores) < 4:
                # Too few dimensions parsed — consider it a failure
                return None

            return scores

        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse judge response: {e}")
            return None

    async def _run_with_variance_check(
        self, prompt: str, model_config: dict, runs: int = 3
    ) -> Optional[list[DimensionScore]]:
        """
        Run judge multiple times and check variance.
        Accept if max spread per dimension <= 0.05.
        Returns median scores.
        """
        all_run_scores: list[list[DimensionScore]] = []

        determinism_params = {}
        if model_config.get("provider") == "openai":
            determinism_params = {"temperature": 0, "seed": 42}
        elif model_config.get("provider") == "ollama":
            determinism_params = {"temperature": 0}
        else:
            determinism_params = {"temperature": 0}

        for _ in range(runs):
            try:
                response = await self.llm_client.generate(
                    prompt=prompt,
                    model=model_config.get("model", ""),
                    provider=model_config.get("provider", ""),
                    **determinism_params,
                )
                scores = self._parse_judge_response(response)
                if scores:
                    all_run_scores.append(scores)
            except Exception as e:
                logger.warning(f"Judge run failed: {e}")

        if not all_run_scores:
            return None

        # Single run — just return it
        if len(all_run_scores) == 1:
            return all_run_scores[0]

        # Multiple runs — compute median and check variance
        dimension_values: dict[str, list[float]] = {}
        for run_scores in all_run_scores:
            for ds in run_scores:
                dim_key = ds.dimension_id.value
                if dim_key not in dimension_values:
                    dimension_values[dim_key] = []
                dimension_values[dim_key].append(ds.score)

        # Check variance threshold
        for dim_key, values in dimension_values.items():
            if len(values) < 2:
                continue
            spread = max(values) - min(values)
            if spread > _VARIANCE_THRESHOLD:
                logger.warning(
                    f"Judge variance too high for {dim_key}: spread={spread:.3f} "
                    f"(threshold={_VARIANCE_THRESHOLD})"
                )
                # Still return median but log the warning

        # Build median scores using first run as template for metadata
        template = all_run_scores[0]
        median_scores: list[DimensionScore] = []
        for ds in template:
            dim_key = ds.dimension_id.value
            values = dimension_values.get(dim_key, [ds.score])
            median_val = statistics.median(values)
            median_scores.append(
                DimensionScore(
                    dimension_id=ds.dimension_id,
                    score=round(median_val, 4),
                    weight=ds.weight,
                    evidence=ds.evidence,
                )
            )

        return median_scores

    @staticmethod
    def _build_chunks_summary(chunks: list) -> str:
        """Build a brief summary of retrieved chunks for the judge prompt."""
        if not chunks:
            return "(No source chunks available)"
        summaries = []
        for i, chunk in enumerate(chunks[:10]):  # Limit to 10 chunks
            title = getattr(chunk, "document_title", "Unknown")
            content = getattr(chunk, "content", "")
            # Truncate long content
            if len(content) > 200:
                content = content[:200] + "..."
            summaries.append(f"[{i + 1}] {title}: {content}")
        return "\n".join(summaries)

    # ------------------------------------------------------------------
    # Rule-Based Fallback (preserved from Phase 2)
    # ------------------------------------------------------------------

    async def _judge_rule_based(self, node, accessor, synthesis) -> NodeOutput:
        """
        Original rule-based scoring (preserved from Phase 2 as fallback).
        Evaluates: completeness, coherence, citation_density, language consistency.
        Maps to the 7 dimensions as best as possible.
        """
        query = accessor.get_query()
        text = synthesis.text
        weights: dict[str, float] = node.config.get("weights", _DEFAULT_WEIGHTS)

        # Evaluate each dimension
        dimensions: dict[str, float] = {}
        issues: list[ValidationIssue] = []

        # 1. Completeness — keyword overlap with query
        completeness = self._score_completeness(query, text)
        dimensions["completeness"] = completeness
        if completeness < 0.5:
            issues.append(
                ValidationIssue(
                    issue_type="low_completeness",
                    severity="warning",
                    message=(
                        f"Answer may not fully address the query "
                        f"(completeness={completeness:.2f})"
                    ),
                )
            )

        # 2. Coherence
        coherence = self._score_coherence(text)
        dimensions["coherence"] = coherence
        if coherence < 0.5:
            issues.append(
                ValidationIssue(
                    issue_type="low_coherence",
                    severity="warning",
                    message=f"Text coherence is low (score={coherence:.2f})",
                )
            )

        # 3. Citation density
        citation_density = self._score_citation_density(text, synthesis.citations)
        dimensions["citation_density"] = citation_density
        if citation_density < 0.5:
            issues.append(
                ValidationIssue(
                    issue_type="low_citation_density",
                    severity="warning",
                    message=f"Citation density is low (score={citation_density:.2f})",
                )
            )

        # 4. Language consistency
        expected_lang = self._get_expected_language(accessor, node)
        language_score = self._score_language(text, expected_lang)
        dimensions["language"] = language_score
        if language_score < 0.5:
            issues.append(
                ValidationIssue(
                    issue_type="language_mismatch",
                    severity="warning",
                    message=(
                        f"Response language may not match expected "
                        f"'{expected_lang}' (score={language_score:.2f})"
                    ),
                )
            )

        # Weighted aggregate
        total_weight = sum(weights.get(d, 0.0) for d in dimensions)
        if total_weight > 0:
            score = sum(
                dimensions[d] * weights.get(d, 0.0) for d in dimensions
            ) / total_weight
        else:
            score = sum(dimensions.values()) / max(len(dimensions), 1)

        passed = score >= 0.5

        result = ValidationResult(
            validator_id="quality_judge",
            passed=passed,
            score=round(score, 4),
            issues=issues,
            metadata={"dimensions": {k: round(v, 4) for k, v in dimensions.items()}},
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=result.model_dump(),
            tokens_used=0,
        )

    # ------------------------------------------------------------------
    # Scoring helpers (rule-based)
    # ------------------------------------------------------------------

    @staticmethod
    def _score_completeness(query: str, text: str) -> float:
        """Keyword-overlap heuristic for answer completeness."""
        if not query or not text:
            return 0.0

        # Extract meaningful words (ignore short stop-words)
        query_words = {
            w.lower()
            for w in re.findall(r"\w+", query)
            if len(w) > 2
        }
        if not query_words:
            return 1.0  # trivial query

        text_lower = text.lower()
        hits = sum(1 for w in query_words if w in text_lower)
        return min(hits / len(query_words), 1.0)

    @staticmethod
    def _score_coherence(text: str) -> float:
        """Heuristic coherence: sentence structure and no truncation."""
        if not text:
            return 0.0

        sentences = [s.strip() for s in re.split(r"[.!?]\s+", text) if s.strip()]
        n_sentences = len(sentences)

        if n_sentences == 0:
            return 0.2

        # Average sentence length (word count)
        avg_len = sum(len(s.split()) for s in sentences) / n_sentences

        # Good range: 8-40 words per sentence
        length_score = 1.0
        if avg_len < 5:
            length_score = 0.4
        elif avg_len < 8:
            length_score = 0.7
        elif avg_len > 60:
            length_score = 0.5
        elif avg_len > 40:
            length_score = 0.7

        # Check truncation
        truncation_penalty = 0.0
        if _TRUNCATION_MARKERS.search(text):
            truncation_penalty = 0.3

        # Minimum sentence count bonus
        count_score = min(n_sentences / 3.0, 1.0)

        score = (length_score * 0.5 + count_score * 0.5) - truncation_penalty
        return max(min(score, 1.0), 0.0)

    @staticmethod
    def _score_citation_density(text: str, citations: list[Any]) -> float:
        """Score based on citations per 100 words."""
        word_count = len(text.split())
        if word_count == 0:
            return 0.0

        n_citations = len(citations) if citations else 0
        if n_citations == 0:
            return 0.2  # Some answers may legitimately have no citations

        density = (n_citations / word_count) * 100
        # Target: ~1-3 citations per 100 words
        if density >= 1.0:
            return 1.0
        elif density >= 0.5:
            return 0.8
        elif density >= 0.2:
            return 0.5
        return 0.3

    @staticmethod
    def _get_expected_language(accessor: Any, node: StrategyNode) -> str:
        """Determine expected language from business context or config."""
        bc = accessor.get_business_context()
        if bc is not None:
            lp = getattr(bc, "language_policy", None)
            if lp is not None:
                langs = getattr(lp, "primary_languages", [])
                if langs:
                    return langs[0]
        return node.config.get("expected_language", "de")

    @staticmethod
    def _score_language(text: str, expected_lang: str) -> float:
        """
        Basic language consistency heuristic.

        Uses common word frequency to guess the dominant language.
        Only distinguishes German (de) vs English (en) for now.
        """
        if not text or not expected_lang:
            return 1.0

        text_lower = text.lower()

        de_markers = ["der", "die", "das", "und", "ist", "ein", "eine", "für", "mit", "auf"]
        en_markers = ["the", "and", "is", "for", "with", "this", "that", "from", "are", "was"]

        de_hits = sum(1 for m in de_markers if f" {m} " in f" {text_lower} ")
        en_hits = sum(1 for m in en_markers if f" {m} " in f" {text_lower} ")

        total = de_hits + en_hits
        if total == 0:
            return 0.8  # Can't determine — give benefit of doubt

        if expected_lang.startswith("de"):
            return min(de_hits / max(total, 1), 1.0) if de_hits >= en_hits else 0.3
        elif expected_lang.startswith("en"):
            return min(en_hits / max(total, 1), 1.0) if en_hits >= de_hits else 0.3

        # Unknown language — pass
        return 0.8
