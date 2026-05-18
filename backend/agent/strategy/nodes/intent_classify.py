"""IntentClassifyExecutor — query intent classification via LLM with keyword-based fallback."""
from __future__ import annotations

import logging
import re
from typing import Any, Optional, TYPE_CHECKING

from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor
from backend.agent.strategy.models import StrategyNode, StrategyRunState, NodeOutput
from backend.agent.strategy.prompts import build_intent_classify_prompt

if TYPE_CHECKING:
    from backend.agent.strategy.llm_helper import NodeLLMHelper

logger = logging.getLogger(__name__)

# Default intent keywords if none provided in node config
_DEFAULT_INTENT_KEYWORDS: dict[str, list[str]] = {
    "legal_question": ["Frage", "Recht", "Gesetz", "Paragraph", "Klausel", "legal", "law"],
    "summary": ["zusammenfass", "Zusammenfassung", "overview", "summary", "summarize"],
    "comparison": ["vergleich", "compare", "versus", "vs", "difference"],
    "definition": ["definition", "define", "was ist", "what is", "meaning"],
    "action_item": ["aufgabe", "task", "todo", "action", "next steps"],
}


def _tokenize(text: str) -> list[str]:
    """Simple word tokenization for matching."""
    return re.findall(r"\w+", text.lower())


def _classify_intent(
    query: str,
    intent_keywords: dict[str, list[str]],
) -> tuple[str, float, Optional[str]]:
    """
    Classify intent by keyword matching.

    Returns:
        (intent, confidence, domain)
    """
    query_tokens = _tokenize(query)
    query_lower = query.lower()

    if not query_tokens:
        return "general", 0.0, None

    scores: dict[str, int] = {}

    for intent, keywords in intent_keywords.items():
        match_count = 0
        for keyword in keywords:
            keyword_lower = keyword.lower()
            # Check as substring for partial matches (e.g., "zusammenfass" in "zusammenfassung")
            if keyword_lower in query_lower:
                match_count += 1
            elif keyword_lower in query_tokens:
                match_count += 1
        if match_count > 0:
            scores[intent] = match_count

    if not scores:
        return "general", 0.0, None

    # Pick the intent with the highest match count
    best_intent = max(scores, key=scores.get)  # type: ignore[arg-type]
    best_count = scores[best_intent]

    # Confidence: ratio of matched keywords to total keywords for that intent
    total_keywords = len(intent_keywords.get(best_intent, []))
    confidence = min(best_count / max(total_keywords, 1), 1.0)

    # Domain is derived from the intent name (simple heuristic)
    domain = best_intent.split("_")[0] if "_" in best_intent else None

    return best_intent, confidence, domain


class IntentClassifyExecutor(NodeExecutor):
    """Classify query intent via LLM with keyword-based fallback."""

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

        # Get intent keywords from config, fall back to defaults
        config_intents = node.config.get("intent_keywords", {})
        intent_keywords = config_intents.get("intents", _DEFAULT_INTENT_KEYWORDS)

        # Try LLM-based classification first
        if self.llm_helper:
            try:
                result = await self._execute_with_llm(node, query, intent_keywords)
                if result:
                    return result
            except Exception as e:
                logger.warning("LLM intent classification failed, falling back to keyword-based: %s", e)

        # Fallback: keyword-based classification
        return self._execute_rule_based(node, query, intent_keywords)

    async def _execute_with_llm(
        self,
        node: StrategyNode,
        query: str,
        intent_keywords: dict[str, list[str]],
    ) -> Optional[NodeOutput]:
        """LLM-driven intent classification."""
        intents = list(intent_keywords.keys())
        messages = build_intent_classify_prompt(query, intents=intents)

        data, tokens = await self.llm_helper.complete_json(  # type: ignore[union-attr]
            "classifier", messages, node.config,
        )

        # Expect {"intent": str, "confidence": float, "domain": str}
        if not isinstance(data, dict):
            logger.warning("LLM intent_classify returned non-dict: %s", type(data).__name__)
            return None

        intent = data.get("intent", "general")
        confidence = data.get("confidence", 0.0)
        domain = data.get("domain")

        # Validate intent is one of the known intents
        if intent not in intents:
            # If the LLM returned an unknown intent, still accept it
            # but log a warning
            logger.warning(
                "LLM intent_classify returned unknown intent=%r (known: %s)",
                intent,
                intents,
            )

        # Normalise confidence
        try:
            confidence = min(max(float(confidence), 0.0), 1.0)
        except (TypeError, ValueError):
            confidence = 0.5

        result = {
            "intent": str(intent),
            "confidence": round(confidence, 3),
            "domain": domain,
        }

        logger.debug(
            "IntentClassify(LLM): query=%r → intent=%s, confidence=%.2f, domain=%s",
            query[:50],
            intent,
            confidence,
            domain,
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
        intent_keywords: dict[str, list[str]],
    ) -> NodeOutput:
        """Keyword-based intent classification (original logic)."""
        intent, confidence, domain = _classify_intent(query, intent_keywords)

        logger.debug(
            "IntentClassify(rule): query=%r → intent=%s, confidence=%.2f, domain=%s",
            query[:50],
            intent,
            confidence,
            domain,
        )

        result = {
            "intent": intent,
            "confidence": round(confidence, 3),
            "domain": domain,
        }

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=result,
            tokens_used=0,
        )
