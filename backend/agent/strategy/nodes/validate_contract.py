"""ValidateContractExecutor — Validate synthesis matches the answer contract."""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from backend.agent.strategy.models import (
    AnswerContract,
    NodeOutput,
    StrategyNode,
    StrategyRunState,
    ValidationIssue,
    ValidationResult,
)
from backend.agent.strategy.nodes.base import NodeExecutor

logger = logging.getLogger(__name__)

# Simple markers used to detect formality
_CASUAL_MARKERS = re.compile(
    r"\b(lol|haha|ok so|gonna|wanna|btw|imho|tbh|nah|yep|yeah)\b",
    re.IGNORECASE,
)
_FORMAL_MARKERS = re.compile(
    r"\b(therefore|furthermore|accordingly|consequently|herein|pursuant)\b",
    re.IGNORECASE,
)


class ValidateContractExecutor(NodeExecutor):
    """
    Validate that the synthesis output satisfies the answer contract.

    Checks performed:
        * Required output sections are present (header text match).
        * Table-like structure exists when ``table_schema`` is defined.
        * Tone consistency (formal vs. casual markers).
        * Min / max length constraints.

    Output:
        Serialized ``ValidationResult`` with ``validator_id="contract_check"``.
    """

    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        accessor = self.get_state_accessor(state)
        synthesis = accessor.get_synthesis()

        if synthesis is None:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=ValidationResult(
                    validator_id="contract_check",
                    passed=True,
                    score=1.0,
                ).model_dump(),
                tokens_used=0,
            )

        # Resolve contract: business context first, then node config
        contract = self._resolve_contract(accessor, node)

        if contract is None:
            # No contract → nothing to enforce, pass by default
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="success",
                output_data=ValidationResult(
                    validator_id="contract_check",
                    passed=True,
                    score=1.0,
                    metadata={"reason": "no_contract_defined"},
                ).model_dump(),
                tokens_used=0,
            )

        text = synthesis.text
        issues: list[ValidationIssue] = []
        checks_total = 0
        checks_passed = 0

        # --- section headers check ----------------------------------------
        if contract.output_sections:
            for section in contract.output_sections:
                checks_total += 1
                if section.title.lower() in text.lower():
                    checks_passed += 1
                elif not section.required:
                    checks_passed += 1  # optional section missing is OK
                else:
                    issues.append(
                        ValidationIssue(
                            issue_type="missing_section",
                            severity="warning",
                            message=f"Required section '{section.title}' not found in output",
                            field=section.section_id,
                        )
                    )

        # --- table schema check -------------------------------------------
        if contract.table_schema:
            checks_total += 1
            if self._has_table_structure(text):
                checks_passed += 1
            else:
                issues.append(
                    ValidationIssue(
                        issue_type="missing_table",
                        severity="warning",
                        message="Answer contract expects table-formatted output but none detected",
                    )
                )

        # --- tone check ---------------------------------------------------
        if contract.tone:
            checks_total += 1
            tone_ok = self._check_tone(text, contract.tone)
            if tone_ok:
                checks_passed += 1
            else:
                issues.append(
                    ValidationIssue(
                        issue_type="tone_mismatch",
                        severity="warning",
                        message=f"Tone inconsistency detected: expected '{contract.tone}'",
                    )
                )

        # --- length constraints -------------------------------------------
        token_estimate = len(text.split())
        if contract.max_length_tokens is not None:
            checks_total += 1
            if token_estimate <= contract.max_length_tokens:
                checks_passed += 1
            else:
                issues.append(
                    ValidationIssue(
                        issue_type="max_length_exceeded",
                        severity="warning",
                        message=(
                            f"Output length ~{token_estimate} tokens exceeds "
                            f"max {contract.max_length_tokens}"
                        ),
                    )
                )

        score = checks_passed / checks_total if checks_total > 0 else 1.0
        passed = score >= 0.5 and not any(i.severity == "error" for i in issues)

        result = ValidationResult(
            validator_id="contract_check",
            passed=passed,
            score=round(score, 4),
            issues=issues,
            metadata={
                "checks_total": checks_total,
                "checks_passed": checks_passed,
                "contract_format_id": contract.format_id,
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
    def _resolve_contract(
        accessor: Any,
        node: StrategyNode,
    ) -> Optional[AnswerContract]:
        """Resolve answer contract from business context or node config."""
        bc = accessor.get_business_context()
        if bc is not None:
            contract = getattr(bc, "answer_contract", None)
            if contract is not None:
                return contract

        # Fallback: build from node config if raw dict provided
        raw = node.config.get("answer_contract")
        if isinstance(raw, dict):
            try:
                return AnswerContract(**raw)
            except Exception:
                return None
        return None

    @staticmethod
    def _has_table_structure(text: str) -> bool:
        """Heuristic: detect pipe-delimited or markdown tables."""
        lines = text.split("\n")
        pipe_rows = sum(1 for line in lines if line.count("|") >= 2)
        return pipe_rows >= 2  # header + at least one data row

    @staticmethod
    def _check_tone(text: str, expected_tone: str) -> bool:
        """Basic heuristic tone consistency check."""
        casual_hits = len(_CASUAL_MARKERS.findall(text))
        formal_hits = len(_FORMAL_MARKERS.findall(text))

        if expected_tone in ("professional", "formal", "legal"):
            # Should not have many casual markers
            return casual_hits <= 1
        elif expected_tone in ("casual", "conversational"):
            # Should not be overly formal
            return formal_hits <= 2
        # Unknown tone — pass
        return True
