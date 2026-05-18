"""QueryExpandExecutor — generate query variants via LLM with rule-based fallback."""
from __future__ import annotations

import logging
import re
from typing import Any, Optional, TYPE_CHECKING

from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor
from backend.agent.strategy.models import StrategyNode, StrategyRunState, NodeOutput
from backend.agent.strategy.prompts import build_query_expansion_prompt

if TYPE_CHECKING:
    from backend.agent.strategy.llm_helper import NodeLLMHelper

logger = logging.getLogger(__name__)

# Simple expansion rules: (pattern, replacement)
_EXPANSION_RULES: list[tuple[re.Pattern[str], str]] = [
    # "what is X" → "define X"
    (re.compile(r"^what\s+is\s+", re.IGNORECASE), "define "),
    # "was ist X" → "Definition X" (German)
    (re.compile(r"^was\s+ist\s+", re.IGNORECASE), "Definition "),
    # "how does X work" → "explain X"
    (re.compile(r"^how\s+does\s+(.+?)\s+work", re.IGNORECASE), r"explain \1"),
    # "wie funktioniert X" → "erkläre X" (German)
    (re.compile(r"^wie\s+funktioniert\s+", re.IGNORECASE), "erkläre "),
]


def _remove_question_mark(text: str) -> str:
    """Remove trailing question mark."""
    return text.rstrip("?").strip()


def _add_explain_prefix(text: str) -> str:
    """Add 'explain' prefix if not already present."""
    lower = text.lower()
    if lower.startswith(("explain ", "erkläre ", "define ", "definition ")):
        return text
    return f"explain {text}"


def _generate_variants(query: str, max_variants: int) -> list[str]:
    """
    Generate query variants using simple rule-based strategies.

    Always includes the original query as first variant.
    """
    variants: list[str] = [query]

    # Variant: remove question mark
    no_question = _remove_question_mark(query)
    if no_question != query and no_question not in variants:
        variants.append(no_question)

    # Variant: apply expansion rules
    for pattern, replacement in _EXPANSION_RULES:
        expanded = pattern.sub(replacement, query).strip()
        if expanded != query and expanded not in variants:
            variants.append(expanded)
            break  # Only apply first matching rule

    # Variant: add explain prefix
    explain_variant = _add_explain_prefix(no_question)
    if explain_variant not in variants:
        variants.append(explain_variant)

    # Trim to max_variants
    return variants[:max_variants]


class QueryExpandExecutor(NodeExecutor):
    """Generate query variants via LLM with rule-based fallback."""

    def __init__(self, llm_helper: Optional["NodeLLMHelper"] = None):
        self.llm_helper = llm_helper

    async def execute(self, node: StrategyNode, state: StrategyRunState) -> NodeOutput:
        accessor = self.get_state_accessor(state)
        query = accessor.get_normalized_query() or accessor.get_query()

        if not query or not query.strip():
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=None,
                tokens_used=0,
            )

        max_variants = node.config.get("max_variants", 3)

        # Try LLM-based expansion first
        if self.llm_helper:
            try:
                result = await self._execute_with_llm(node, query, max_variants)
                if result:
                    return result
            except Exception as e:
                logger.warning("LLM query expansion failed, falling back to rule-based: %s", e)

        # Fallback: rule-based expansion
        return self._execute_rule_based(node, query, max_variants)

    async def _execute_with_llm(
        self,
        node: StrategyNode,
        query: str,
        max_variants: int,
    ) -> Optional[NodeOutput]:
        """LLM-driven query expansion."""
        language = node.config.get("language", "en")
        messages = build_query_expansion_prompt(query, language=language, max_variants=max_variants)

        data, tokens = await self.llm_helper.complete_json(  # type: ignore[union-attr]
            "query_expander", messages, node.config,
        )

        # Expect a JSON list of strings
        if isinstance(data, list):
            variants = [str(v) for v in data if isinstance(v, str)][:max_variants]
        else:
            logger.warning("LLM query_expand returned non-list: %s", type(data).__name__)
            return None

        if not variants:
            return None

        # Ensure original query is always first
        if query not in variants:
            variants.insert(0, query)
            variants = variants[:max_variants]
        elif variants[0] != query:
            variants.remove(query)
            variants.insert(0, query)

        logger.debug(
            "QueryExpand(LLM): generated %d variants for query=%r",
            len(variants),
            query[:50],
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=variants,
            tokens_used=tokens,
        )

    def _execute_rule_based(
        self,
        node: StrategyNode,
        query: str,
        max_variants: int,
    ) -> NodeOutput:
        """Rule-based query expansion (original logic)."""
        variants = _generate_variants(query, max_variants)

        logger.debug(
            "QueryExpand(rule): generated %d variants for query=%r",
            len(variants),
            query[:50],
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=variants,
            tokens_used=0,
        )
