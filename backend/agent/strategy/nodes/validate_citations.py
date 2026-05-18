"""ValidateCitationsExecutor — Verify synthesis citations against source chunks."""
from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Any

from backend.agent.strategy.models import (
    NodeOutput,
    StrategyNode,
    StrategyRunState,
    ValidationIssue,
    ValidationResult,
)
from backend.agent.strategy.nodes.base import NodeExecutor

logger = logging.getLogger(__name__)

# Fuzzy-match threshold: ratio at which a quote is considered "found"
_FUZZY_THRESHOLD = 0.65


class ValidateCitationsExecutor(NodeExecutor):
    """
    Verify that citations in the synthesis output actually correspond to
    retrieved chunks and that the cited text can be located in the source.

    Scoring:
        1.0 = all citations valid, high coverage.
        Deductions for orphan citations (cite non-existent sources) and
        low coverage (fraction of the answer without supporting citations).

    Output:
        Serialized ``ValidationResult`` dict with
        ``validator_id="citation_check"``.
    """

    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        accessor = self.get_state_accessor(state)
        synthesis = accessor.get_synthesis()
        chunks = accessor.get_chunks()

        if synthesis is None:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=self._empty_result().model_dump(),
                tokens_used=0,
            )

        citations = synthesis.citations
        if not citations:
            # No citations to validate — still flag low coverage
            result = ValidationResult(
                validator_id="citation_check",
                passed=True,
                score=0.5,  # No citations means we can't verify
                issues=[
                    ValidationIssue(
                        issue_type="no_citations",
                        severity="warning",
                        message="Synthesis contains no citations to validate",
                    )
                ],
                metadata={"coverage_ratio": 0.0, "orphan_count": 0},
            )
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="success",
                output_data=result.model_dump(),
                tokens_used=0,
            )

        # Build lookup of source_id -> chunk content
        source_index: dict[str, str] = {}
        for chunk in chunks:
            # Aggregate content per source_id (a source may have many chunks)
            existing = source_index.get(chunk.source_id, "")
            source_index[chunk.source_id] = existing + "\n" + chunk.content

        issues: list[ValidationIssue] = []
        valid_count = 0
        orphan_count = 0

        for citation in citations:
            # 1. Check if source exists in retrieved chunks
            if citation.source_id not in source_index:
                orphan_count += 1
                issues.append(
                    ValidationIssue(
                        issue_type="orphan_citation",
                        severity="error",
                        message=(
                            f"Citation references source '{citation.source_id}' "
                            f"which was not found in retrieved chunks"
                        ),
                        field=citation.source_id,
                    )
                )
                continue

            # 2. Check if cited quote/text can be found in the chunk
            if citation.quote:
                source_text = source_index[citation.source_id]
                if not self._fuzzy_match(citation.quote, source_text):
                    issues.append(
                        ValidationIssue(
                            issue_type="unverifiable_quote",
                            severity="warning",
                            message=(
                                f"Cited quote from '{citation.source_id}' could not "
                                f"be fuzzy-matched in the source content"
                            ),
                            field=citation.source_id,
                        )
                    )
                    # Still count as partially valid (source exists, quote unverified)
                    valid_count += 1
                    continue

            valid_count += 1

        # Coverage: fraction of citations that are valid
        total = len(citations)
        coverage_ratio = valid_count / total if total > 0 else 0.0

        # Scoring
        score = coverage_ratio
        # Penalize orphan citations
        if total > 0:
            orphan_penalty = (orphan_count / total) * 0.3
            score = max(score - orphan_penalty, 0.0)

        passed = score >= 0.5 and orphan_count == 0

        result = ValidationResult(
            validator_id="citation_check",
            passed=passed,
            score=round(score, 4),
            issues=issues,
            metadata={
                "coverage_ratio": round(coverage_ratio, 4),
                "orphan_count": orphan_count,
                "total_citations": total,
                "valid_citations": valid_count,
            },
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=result.model_dump(),
            tokens_used=0,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fuzzy_match(quote: str, source_text: str) -> bool:
        """Check if *quote* can be found (or fuzzy-matched) in *source_text*."""
        if not quote:
            return True
        quote_lower = quote.lower().strip()
        source_lower = source_text.lower()

        # Exact substring match first
        if quote_lower in source_lower:
            return True

        # Sliding-window fuzzy match for short quotes
        window_size = len(quote_lower)
        if window_size == 0:
            return True

        best_ratio = 0.0
        step = max(1, window_size // 4)
        for start in range(0, max(len(source_lower) - window_size + 1, 1), step):
            window = source_lower[start : start + window_size]
            ratio = SequenceMatcher(None, quote_lower, window).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
            if best_ratio >= _FUZZY_THRESHOLD:
                return True

        return best_ratio >= _FUZZY_THRESHOLD

    @staticmethod
    def _empty_result() -> ValidationResult:
        """Return a neutral result when there is nothing to validate."""
        return ValidationResult(
            validator_id="citation_check",
            passed=True,
            score=1.0,
            issues=[],
            metadata={"coverage_ratio": 0.0, "orphan_count": 0},
        )
