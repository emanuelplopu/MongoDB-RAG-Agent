"""PlanExecutor — generate an execution plan via LLM with rule-based fallback."""
from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor
from backend.agent.strategy.models import StrategyNode, StrategyRunState, NodeOutput
from backend.agent.strategy.prompts import build_plan_prompt

if TYPE_CHECKING:
    from backend.agent.strategy.llm_helper import NodeLLMHelper

logger = logging.getLogger(__name__)

# Default plan templates based on available context
_PLAN_TEMPLATE_WITH_CHUNKS = [
    "Identify relevant sources from {chunk_count} retrieved chunks",
    "Extract key facts and evidence from top-scoring sources",
    "Synthesize answer in {format} format with citations",
]

_PLAN_TEMPLATE_NO_CHUNKS = [
    "Analyze query intent and scope",
    "Retrieve relevant documents from knowledge base",
    "Extract key facts and synthesize a coherent answer",
]


def _build_plan(
    query: str,
    chunk_count: int,
    format_style: str,
    max_steps: int,
    plan_style: str,
) -> tuple[str, list[str]]:
    """
    Build a template-based plan.

    # TODO: Replace with LLM call via ModelRoleRegistry in Phase 3

    Returns:
        (plan_text, steps)
    """
    if chunk_count > 0:
        template = _PLAN_TEMPLATE_WITH_CHUNKS
    else:
        template = _PLAN_TEMPLATE_NO_CHUNKS

    # Render template steps
    steps: list[str] = []
    for step_template in template[:max_steps]:
        step = step_template.format(
            chunk_count=chunk_count,
            format=format_style,
        )
        steps.append(step)

    # Add verification step if plan_style is "thorough"
    if plan_style == "thorough" and len(steps) < max_steps:
        steps.append("Verify citations and cross-reference claims")

    # Add formatting step if plan_style is "detailed"
    if plan_style == "detailed" and len(steps) < max_steps:
        steps.append("Format output according to answer contract requirements")

    # Generate plan text from steps
    plan_text = f"Plan for: {query[:80]}{'...' if len(query) > 80 else ''}\n"
    for i, step in enumerate(steps, 1):
        plan_text += f"  Step {i}: {step}\n"

    return plan_text.strip(), steps


class PlanExecutor(NodeExecutor):
    """Generate an execution plan via LLM with rule-based fallback."""

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

        # Get configuration
        max_steps = node.config.get("max_steps", 5)
        plan_style = node.config.get("plan_style", "standard")
        format_style = node.config.get("format", "prose")

        # Check available context
        chunks = accessor.get_chunks()
        chunk_count = len(chunks)

        # Try LLM-based planning first
        if self.llm_helper:
            try:
                result = await self._execute_with_llm(
                    node, query, chunks, chunk_count, max_steps,
                )
                if result:
                    return result
            except Exception as e:
                logger.warning("LLM plan generation failed, falling back to rule-based: %s", e)

        # Fallback: rule-based plan
        return self._execute_rule_based(
            node, query, chunk_count, format_style, max_steps, plan_style,
        )

    async def _execute_with_llm(
        self,
        node: StrategyNode,
        query: str,
        chunks: list,
        chunk_count: int,
        max_steps: int,
    ) -> Optional[NodeOutput]:
        """LLM-driven plan generation."""
        # Build a summary of available chunks for the planner
        chunks_summary = ""
        if chunk_count > 0:
            summaries = []
            for i, c in enumerate(chunks[:10], 1):  # Summarise top 10
                title = getattr(c, "document_title", "Unknown")
                content_preview = (getattr(c, "content", "") or "")[:120]
                summaries.append(f"[{i}] {title}: {content_preview}")
            chunks_summary = f"{chunk_count} chunks available:\n" + "\n".join(summaries)

        # Build answer_contract dict from node config if present
        answer_contract = node.config.get("answer_contract")

        messages = build_plan_prompt(
            query=query,
            chunks_summary=chunks_summary,
            answer_contract=answer_contract,
        )

        data, tokens = await self.llm_helper.complete_json(  # type: ignore[union-attr]
            "orchestrator", messages, node.config,
        )

        # Expect {"plan_text": str, "steps": list[str]}
        if not isinstance(data, dict):
            logger.warning("LLM plan returned non-dict: %s", type(data).__name__)
            return None

        plan_text = data.get("plan_text", "")
        steps = data.get("steps", [])

        if not isinstance(steps, list) or not steps:
            logger.warning("LLM plan returned invalid steps: %s", type(steps).__name__)
            return None

        # Trim to max_steps
        steps = [str(s) for s in steps[:max_steps]]

        result = {
            "plan_text": str(plan_text),
            "steps": steps,
        }

        logger.debug(
            "PlanExecutor(LLM): generated %d-step plan for query=%r",
            len(steps),
            query[:50],
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=result,
            tokens_used=tokens,
        )

    def _execute_rule_based(
        self,
        node: StrategyNode,
        query: str,
        chunk_count: int,
        format_style: str,
        max_steps: int,
        plan_style: str,
    ) -> NodeOutput:
        """Rule-based plan generation (original logic)."""
        plan_text, steps = _build_plan(
            query=query,
            chunk_count=chunk_count,
            format_style=format_style,
            max_steps=max_steps,
            plan_style=plan_style,
        )

        logger.debug(
            "PlanExecutor(rule): generated %d-step plan (style=%s, chunks=%d)",
            len(steps),
            plan_style,
            chunk_count,
        )

        result = {
            "plan_text": plan_text,
            "steps": steps,
        }

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=result,
            tokens_used=0,
        )
