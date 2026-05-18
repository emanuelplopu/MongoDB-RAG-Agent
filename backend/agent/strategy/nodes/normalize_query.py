"""NormalizeQueryExecutor — normalizes the user query text."""
from __future__ import annotations

import re
import unicodedata
import logging

from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor
from backend.agent.strategy.models import StrategyNode, StrategyRunState, NodeOutput

logger = logging.getLogger(__name__)

# Simple heuristic: common German-specific characters and high-frequency words
_GERMAN_INDICATORS = re.compile(
    r"[äöüÄÖÜß]|"
    r"\b(und|der|die|das|ist|ein|eine|für|mit|auf|nicht|sich|auch|werden|kann)\b",
    re.IGNORECASE,
)


def _detect_language(text: str) -> str:
    """Detect language using simple heuristic. Returns 'de' or 'en'."""
    if _GERMAN_INDICATORS.search(text):
        return "de"
    return "en"


class NormalizeQueryExecutor(NodeExecutor):
    """Normalize the user query: strip, NFC, collapse spaces, detect language."""

    async def execute(self, node: StrategyNode, state: StrategyRunState) -> NodeOutput:
        accessor = self.get_state_accessor(state)
        query = accessor.get_query()

        if not query or not query.strip():
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=None,
                tokens_used=0,
            )

        # Strip leading/trailing whitespace
        normalized = query.strip()

        # Normalize Unicode to NFC form
        normalized = unicodedata.normalize("NFC", normalized)

        # Collapse multiple spaces into one
        normalized = re.sub(r"\s+", " ", normalized)

        # Detect language
        detected_lang = _detect_language(normalized)

        logger.debug(
            "NormalizeQuery: lang=%s, original_len=%d, normalized_len=%d",
            detected_lang,
            len(query),
            len(normalized),
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=normalized,
            tokens_used=0,
        )
