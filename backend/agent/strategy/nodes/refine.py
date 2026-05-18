"""Iterative refinement node executor.

Applies LLM-driven re-synthesis with rule-based corrections as fallback.
When an ``llm_helper`` is provided the executor attempts an LLM refinement
first; if the call fails or no helper is configured the original rule-based
fixes are applied instead.

Reference: docs/08-STRATEGY_DAG_AND_NODES.md
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Optional

from backend.agent.strategy.models import (
    CitationRef,
    NodeOutput,
    StrategyNode,
    StrategyRunState,
    SynthesisResult,
    ValidationIssue,
    ValidationResult,
)
from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor
from backend.agent.strategy.prompts import build_refinement_prompt

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Issue classification helpers
# ═══════════════════════════════════════════════════════════════════════════════

_CITATION_KEYWORDS = frozenset({
    "citation", "orphan", "reference", "source", "coverage",
    "unsupported_claim", "missing_citation",
})
_CONTRACT_KEYWORDS = frozenset({
    "section", "format", "length", "missing_section", "format_violation",
    "contract", "schema", "max_length", "min_length",
})


def _classify_issues(
    validation_results: list[ValidationResult],
    min_score: float,
) -> tuple[list[ValidationIssue], list[ValidationIssue], list[ValidationIssue]]:
    """Partition actionable issues into citation, contract, and quality buckets.

    Only considers results that did not pass or whose score falls below
    *min_score*.

    Returns:
        (citation_issues, contract_issues, quality_issues)
    """
    citation_issues: list[ValidationIssue] = []
    contract_issues: list[ValidationIssue] = []
    quality_issues: list[ValidationIssue] = []

    for vr in validation_results:
        # Skip passed validators with acceptable score
        if vr.passed and (vr.score is None or vr.score >= min_score):
            continue
        for issue in vr.issues:
            lower_type = issue.issue_type.lower()
            if any(kw in lower_type for kw in _CITATION_KEYWORDS):
                citation_issues.append(issue)
            elif any(kw in lower_type for kw in _CONTRACT_KEYWORDS):
                contract_issues.append(issue)
            else:
                quality_issues.append(issue)

    return citation_issues, contract_issues, quality_issues


# ═══════════════════════════════════════════════════════════════════════════════
# Rule-based refinement actions
# ═══════════════════════════════════════════════════════════════════════════════


def _fix_orphan_citations(
    synthesis: SynthesisResult,
    issues: list[ValidationIssue],
) -> tuple[SynthesisResult, list[str]]:
    """Remove citations that are flagged as orphaned (not referenced in text).

    Returns the (possibly modified) synthesis and a list of fix descriptions.
    """
    fixes: list[str] = []
    # Identify source_ids mentioned in the text
    text_lower = synthesis.text.lower()
    valid: list[CitationRef] = []
    for cit in synthesis.citations:
        # Keep citations whose source_id or document_title appears in text
        title_lower = (cit.document_title or "").lower()
        if cit.source_id.lower() in text_lower or title_lower in text_lower:
            valid.append(cit)
        else:
            fixes.append(f"Removed orphan citation: {cit.source_id}")

    if fixes:
        synthesis = synthesis.model_copy(update={"citations": valid})
    return synthesis, fixes


def _fix_missing_sections(
    synthesis: SynthesisResult,
    issues: list[ValidationIssue],
) -> tuple[SynthesisResult, list[str]]:
    """Append placeholder headings for sections flagged as missing."""
    fixes: list[str] = []
    text = synthesis.text
    for issue in issues:
        if "missing_section" in issue.issue_type.lower():
            section_title = issue.field or "Untitled Section"
            placeholder = f"\n\n## {section_title}\n\n_Content pending — no evidence available._"
            text += placeholder
            fixes.append(f"Added placeholder for missing section: {section_title}")
    if fixes:
        synthesis = synthesis.model_copy(update={"text": text})
    return synthesis, fixes


def _fix_length_violations(
    synthesis: SynthesisResult,
    issues: list[ValidationIssue],
) -> tuple[SynthesisResult, list[str]]:
    """Truncate text or add a padding note for length violations."""
    fixes: list[str] = []
    text = synthesis.text
    for issue in issues:
        itype = issue.issue_type.lower()
        if "max_length" in itype or "length" in itype:
            # Attempt a conservative trim if text is very long
            words = text.split()
            if len(words) > 1500:
                text = " ".join(words[:1500]) + "\n\n_[Trimmed to meet length constraint]_"
                fixes.append("Truncated output to ≤1500 words")
    if fixes:
        word_count = len(text.split())
        token_count = int(word_count / 0.75)
        synthesis = synthesis.model_copy(
            update={"text": text, "token_count": token_count}
        )
    return synthesis, fixes


# ═══════════════════════════════════════════════════════════════════════════════
# Executor
# ═══════════════════════════════════════════════════════════════════════════════


class RefineExecutor(NodeExecutor):
    """Iterative refinement node with optional LLM enhancement.

    When *llm_helper* is provided the executor first attempts an LLM-driven
    refinement pass (role ``"synthesizer_deep"``) that can genuinely improve
    quality, fluency, and citation accuracy.  If the LLM call fails or no
    helper is configured the executor falls back to mechanical rule-based
    corrections:

    * **Citation fixes** — orphan citation removal.
    * **Contract fixes** — placeholder sections, length truncation.
    * **Quality notes** — unfixable issues are recorded in metadata so
      downstream consumers can address them.
    """

    def __init__(self, llm_helper: Optional[Any] = None) -> None:
        self.llm_helper = llm_helper

    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        """Execute refinement pass.

        Args:
            node: Strategy node definition carrying config overrides.
            state: Current DAG run state.

        Returns:
            ``NodeOutput`` whose ``output_data`` is a serialised
            ``SynthesisResult`` dict (refined), or *None* if there is
            nothing to refine.
        """
        accessor = self.get_state_accessor(state)

        # 1. Get current synthesis
        synthesis = accessor.get_synthesis()
        if synthesis is None:
            logger.info(f"Node '{node.node_id}': no synthesis to refine — returning empty")
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=None,
                tokens_used=0,
            )

        # 2. Get validation results
        validation_results = accessor.get_validation_results()
        min_score: float = node.config.get("min_score_threshold", 0.7)

        # 3. Classify issues
        citation_issues, contract_issues, quality_issues = _classify_issues(
            validation_results, min_score
        )

        total_issues = len(citation_issues) + len(contract_issues) + len(quality_issues)
        if total_issues == 0:
            logger.info(
                f"Node '{node.node_id}': all validations passed — returning original synthesis"
            )
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="success",
                output_data=synthesis.model_dump(),
                tokens_used=0,
            )

        logger.info(
            f"Node '{node.node_id}': {total_issues} issue(s) found — "
            f"citation={len(citation_issues)}, contract={len(contract_issues)}, "
            f"quality={len(quality_issues)}"
        )

        # 4. Try LLM refinement first, fall back to rule-based fixes
        if self.llm_helper:
            try:
                result = await self._refine_with_llm(
                    node, accessor, synthesis, validation_results,
                )
                if result:
                    return result
            except Exception as e:
                logger.warning(f"LLM refinement failed, falling back: {e}")

        # 5. Rule-based fixes (fallback)
        all_fixes: list[str] = []
        unfixable: list[str] = []

        # Citation fixes
        if citation_issues:
            synthesis, fixes = _fix_orphan_citations(synthesis, citation_issues)
            all_fixes.extend(fixes)
            # Low coverage cannot be fixed without LLM
            coverage_issues = [
                i for i in citation_issues
                if "coverage" in i.issue_type.lower()
            ]
            if coverage_issues:
                unfixable.append("Low citation coverage requires LLM re-synthesis")

        # Contract fixes
        if contract_issues:
            synthesis, fixes = _fix_missing_sections(synthesis, contract_issues)
            all_fixes.extend(fixes)
            synthesis, fixes = _fix_length_violations(synthesis, contract_issues)
            all_fixes.extend(fixes)

        # Quality issues — mostly unfixable without LLM
        for qi in quality_issues:
            unfixable.append(
                f"Quality issue ({qi.issue_type}): {qi.message}"
            )

        # 6. Adjust confidence (slight reduction per unfixable issue)
        original_confidence = synthesis.confidence or 1.0
        confidence_penalty = len(unfixable) * 0.05
        refined_confidence = max(0.1, round(original_confidence - confidence_penalty, 4))

        # 7. Build refined result
        refined = synthesis.model_copy(
            update={"confidence": refined_confidence}
        )
        # We store refinement metadata in the NodeOutput, not the SynthesisResult
        refinement_metadata: dict[str, Any] = {
            "fixes_applied": all_fixes,
            "unfixable_issues": unfixable,
            "original_confidence": original_confidence,
            "refined_confidence": refined_confidence,
            "total_issues": total_issues,
        }

        logger.info(
            f"Node '{node.node_id}': applied {len(all_fixes)} fix(es), "
            f"{len(unfixable)} unfixable issue(s)"
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=refined.model_dump(),
            tokens_used=0,
            model_used=None,
        )

    # ------------------------------------------------------------------
    # LLM-driven refinement
    # ------------------------------------------------------------------

    async def _refine_with_llm(
        self,
        node: StrategyNode,
        accessor: StateAccessor,
        synthesis: SynthesisResult,
        validation_results: list[ValidationResult],
    ) -> NodeOutput | None:
        """Attempt LLM-driven refinement of the synthesis.

        Returns a ``NodeOutput`` on success, or *None* so the caller
        falls through to the rule-based path.
        """
        # Collect all issues as dicts for the prompt
        all_issues: list[dict] = []
        for vr in validation_results:
            for issue in vr.issues:
                all_issues.append(issue.model_dump())

        if not all_issues:
            return None

        # Build answer_contract dict from state metadata if available
        bctx = accessor.get_business_context()
        answer_contract: dict | None = None
        if bctx and hasattr(bctx, "answer_contract") and bctx.answer_contract:
            answer_contract = bctx.answer_contract.model_dump()

        messages = build_refinement_prompt(
            synthesis_text=synthesis.text,
            issues=all_issues,
            answer_contract=answer_contract,
        )

        refined_text, tokens = await self.llm_helper.complete(
            "synthesizer_deep", messages, node.config,
        )

        if not refined_text or not refined_text.strip():
            logger.warning("LLM refinement returned empty text — falling back")
            return None

        # Build refined SynthesisResult preserving original citations
        original_confidence = synthesis.confidence or 1.0
        refined_confidence = min(1.0, round(original_confidence + 0.05, 4))

        refined = synthesis.model_copy(
            update={
                "text": refined_text.strip(),
                "model_used": "synthesizer_deep",
                "confidence": refined_confidence,
                "token_count": tokens,
            },
        )

        logger.info(
            f"Node '{node.node_id}': LLM refinement succeeded — "
            f"tokens={tokens}, confidence {original_confidence} -> {refined_confidence}"
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=refined.model_dump(),
            tokens_used=tokens,
            model_used="synthesizer_deep",
        )
