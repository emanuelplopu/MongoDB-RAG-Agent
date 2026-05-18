"""BoilerplateFilterExecutor — remove low-signal boilerplate chunks."""
from __future__ import annotations

import logging
import re
from typing import Any

from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor
from backend.agent.strategy.models import (
    StrategyNode,
    StrategyRunState,
    NodeOutput,
    RetrievedChunk,
)

logger = logging.getLogger(__name__)

# Default patterns that indicate boilerplate content
_DEFAULT_BOILERPLATE_PATTERNS: list[str] = [
    r"^\s*table\s+of\s+contents\s*$",
    r"^\s*inhaltsverzeichnis\s*$",
    r"^\s*page\s+\d+\s*(of\s+\d+)?\s*$",
    r"^\s*seite\s+\d+\s*(von\s+\d+)?\s*$",
    r"^\s*-+\s*\d+\s*-+\s*$",
    r"^\s*©.*\d{4}",
    r"^\s*all\s+rights\s+reserved\s*$",
    r"^\s*confidential\s*$",
    r"^\s*draft\s*$",
]

_MIN_CONTENT_LENGTH = 20
_PUNCTUATION_RATIO_THRESHOLD = 0.6


def _is_mostly_punctuation(text: str) -> bool:
    """Check if content is mostly punctuation/whitespace."""
    if not text:
        return True
    non_alnum = sum(1 for c in text if not c.isalnum() and not c.isspace())
    total = len(text)
    return (non_alnum / max(total, 1)) > _PUNCTUATION_RATIO_THRESHOLD


def _matches_boilerplate(content: str, patterns: list[re.Pattern[str]]) -> bool:
    """Check if content matches any boilerplate pattern."""
    content_stripped = content.strip()
    for pattern in patterns:
        if pattern.search(content_stripped):
            return True
    return False


def _is_boilerplate(chunk: RetrievedChunk, patterns: list[re.Pattern[str]]) -> bool:
    """Determine if a chunk is boilerplate."""
    content = chunk.content.strip()

    # Very short content
    if len(content) < _MIN_CONTENT_LENGTH:
        return True

    # Mostly punctuation/whitespace
    if _is_mostly_punctuation(content):
        return True

    # Matches boilerplate patterns
    if _matches_boilerplate(content, patterns):
        return True

    return False


class BoilerplateFilterExecutor(NodeExecutor):
    """Filter out low-signal boilerplate chunks."""

    async def execute(self, node: StrategyNode, state: StrategyRunState) -> NodeOutput:
        accessor = self.get_state_accessor(state)
        chunks = accessor.get_chunks()

        if not chunks:
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=None,
                tokens_used=0,
            )

        # Compile boilerplate patterns from config + defaults
        config_patterns = node.config.get("boilerplate_patterns", [])
        all_pattern_strs = _DEFAULT_BOILERPLATE_PATTERNS + config_patterns
        compiled_patterns = [
            re.compile(p, re.IGNORECASE | re.MULTILINE) for p in all_pattern_strs
        ]

        original_count = len(chunks)
        filtered = [
            chunk for chunk in chunks if not _is_boilerplate(chunk, compiled_patterns)
        ]

        removed_count = original_count - len(filtered)
        logger.debug(
            "BoilerplateFilter: %d chunks → %d kept, %d removed",
            original_count,
            len(filtered),
            removed_count,
        )

        # Serialize to dicts for output_data
        output = [chunk.model_dump() for chunk in filtered]

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=output,
            tokens_used=0,
        )
