"""Evidence card generation node executor.

Transforms retrieved chunks into structured EvidenceCard objects.  When an
``llm_helper`` is provided the node delegates extraction to an LLM and
falls back to deterministic rule-based heuristics on failure.  Without a
helper the behaviour is purely rule-based (original Phase 2 logic).

Reference: docs/quellex_recallhub_strategy_blueprints/13A-STRATEGY_RUN_STATE_AND_DAG_SEMANTICS.md
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Optional

from backend.agent.strategy.models import (
    EvidenceCard,
    EvidencePolicy,
    NodeOutput,
    RetrievedChunk,
    StrategyNode,
    StrategyRunState,
)
from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor
from backend.agent.strategy.prompts import build_evidence_extraction_prompt

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

_DEFAULT_MAX_TOPIC_WORDS = 12
"""Maximum words taken from the first sentence for the topic field."""

_DEFAULT_OVERLAP_THRESHOLD = 0.70
"""Word-overlap ratio above which two cards are considered duplicates."""

# Card-type heuristic keyword sets keyed by domain
_DOMAIN_CARD_TYPES: dict[str, list[tuple[str, list[str]]]] = {
    "legal": [
        ("legal_question", ["question", "issue", "whether", "dispute", "claim"]),
        ("contradiction", ["however", "contrary", "disagree", "conflict", "but"]),
        ("fact", []),  # fallback
    ],
    "business": [
        ("business_insight", ["recommend", "suggestion", "advise", "opportunity"]),
        ("fact", ["metric", "revenue", "cost", "kpi", "percentage", "growth"]),
        ("fact", []),
    ],
}

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_topic(content: str, max_words: int = _DEFAULT_MAX_TOPIC_WORDS) -> str:
    """Return the first sentence or first *max_words* words as topic."""
    # Try to grab the first sentence (period, question-mark, exclamation)
    match = re.match(r"^(.+?[.!?])\s", content, re.DOTALL)
    if match:
        sentence = match.group(1).strip()
        words = sentence.split()
        if len(words) <= max_words:
            return sentence
        return " ".join(words[:max_words]) + "…"
    # Fallback: first N words
    words = content.split()
    if len(words) <= max_words:
        return content.strip()
    return " ".join(words[:max_words]) + "…"


def _truncate_excerpt(content: str, max_words: int) -> str:
    """Truncate content to at most *max_words* words, preserving word boundaries."""
    words = content.split()
    if len(words) <= max_words:
        return content.strip()
    return " ".join(words[:max_words]) + "…"


def _normalise_score(raw_score: float) -> float:
    """Normalise a retrieval score into the 0–1 confidence range.

    Retrieval scores can be negative (cosine) or >1 (BM25). This applies a
    simple sigmoid-style clamp to keep confidence between 0.0 and 1.0.
    """
    if raw_score >= 1.0:
        return 1.0
    if raw_score <= 0.0:
        return max(0.0, 0.5 + raw_score * 0.5)  # map [-1,0] → [0, 0.5]
    return round(raw_score, 4)


def _detect_domain(capability_id: Optional[str], content: str) -> str:
    """Return a coarse domain key (``legal``, ``business``, or ``default``)."""
    if capability_id:
        cap_lower = capability_id.lower()
        if any(kw in cap_lower for kw in ("legal", "law", "litigation", "compliance")):
            return "legal"
        if any(kw in cap_lower for kw in ("business", "finance", "sales", "strategy")):
            return "business"
    # Content-based fallback
    lower = content.lower()
    if any(kw in lower for kw in ("court", "statute", "plaintiff", "defendant", "§")):
        return "legal"
    return "default"


def _infer_card_type(
    content: str,
    domain: str,
    allowed_types: list[str],
) -> str:
    """Choose a card_type using keyword heuristics within the domain."""
    lower = content.lower()
    type_rules = _DOMAIN_CARD_TYPES.get(domain, _DOMAIN_CARD_TYPES["business"])
    for card_type, keywords in type_rules:
        if card_type not in allowed_types:
            continue
        if not keywords:
            # Fallback entry — accept if type is allowed
            return card_type
        if any(kw in lower for kw in keywords):
            return card_type
    # Ultimate fallback — use the first allowed type
    return allowed_types[0] if allowed_types else "fact"


def _build_factual_basis(chunk: RetrievedChunk, domain: str) -> str:
    """Build a template-based factual_basis description."""
    source = chunk.document_title or chunk.source_id
    if domain == "legal":
        return (
            f"Extracted from legal source '{source}' with retrieval "
            f"score {chunk.score:.2f}."
        )
    return (
        f"Based on content from '{source}' (score {chunk.score:.2f})."
    )


def _word_set(text: str) -> set[str]:
    """Return lowercased word tokens for overlap computation."""
    return set(re.findall(r"\w+", text.lower()))


def _overlap_ratio(a: set[str], b: set[str]) -> float:
    """Symmetric word-overlap ratio between two word sets."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    smaller = min(len(a), len(b))
    return intersection / smaller


def _deduplicate_cards(
    cards: list[EvidenceCard],
    threshold: float = _DEFAULT_OVERLAP_THRESHOLD,
) -> list[EvidenceCard]:
    """Remove near-duplicate cards based on topic word overlap.

    When two cards share more than *threshold* topic words the one with
    lower confidence is dropped.
    """
    if len(cards) <= 1:
        return cards

    # Pre-compute word sets
    word_sets = [_word_set(c.topic) for c in cards]
    keep: list[bool] = [True] * len(cards)

    for i in range(len(cards)):
        if not keep[i]:
            continue
        for j in range(i + 1, len(cards)):
            if not keep[j]:
                continue
            ratio = _overlap_ratio(word_sets[i], word_sets[j])
            if ratio > threshold:
                # Drop the lower-confidence card
                if cards[i].confidence >= cards[j].confidence:
                    keep[j] = False
                else:
                    keep[i] = False
                    break  # i is dropped — no need to compare further

    return [c for c, k in zip(cards, keep) if k]


# ═══════════════════════════════════════════════════════════════════════════════
# Executor
# ═══════════════════════════════════════════════════════════════════════════════


class EvidenceCardExecutor(NodeExecutor):
    """Evidence card generation pipeline (LLM-first with rule-based fallback).

    Transforms ``RetrievedChunk`` objects into ``EvidenceCard`` models by:

    1. Extracting topic, excerpt, and factual basis from each chunk.
    2. Assigning a ``card_type`` via domain-aware keyword heuristics.
    3. Deduplicating cards with high topic overlap.
    4. Filtering by ``min_confidence`` and capping at ``max_cards``.

    When *llm_helper* is provided the extraction step is delegated to the
    configured LLM model.  If the LLM call fails (timeout, bad JSON, etc.)
    the node falls back to the deterministic rule-based path.
    """

    def __init__(self, llm_helper=None):
        self.llm_helper = llm_helper

    # ── helpers ──────────────────────────────────────────────────────────────

    def _resolve_evidence_policy(
        self,
        node: StrategyNode,
        accessor: StateAccessor,
    ) -> EvidencePolicy:
        """Resolve the effective EvidencePolicy from config or state metadata."""
        # 1. Explicit policy in node config
        raw = node.config.get("evidence_policy")
        if isinstance(raw, dict):
            return EvidencePolicy(**raw)
        # 2. Stashed in state metadata (set by orchestrator or earlier node)
        meta_raw = accessor.get_metadata("evidence_policy")
        if isinstance(meta_raw, dict):
            return EvidencePolicy(**meta_raw)
        # 3. Default
        return EvidencePolicy()

    # ── rule-based fallback ────────────────────────────────────────────────

    def _extract_rule_based(
        self,
        node: StrategyNode,
        chunks: list[RetrievedChunk],
        domain: str,
        allowed_types: list[str],
        max_excerpt_words: int,
        policy: EvidencePolicy,
    ) -> NodeOutput:
        """Deterministic rule-based evidence card extraction (original logic)."""
        cards: list[EvidenceCard] = [
            self._chunk_to_card(chunk, domain, allowed_types, max_excerpt_words)
            for chunk in chunks
        ]

        cards = _deduplicate_cards(cards)
        cards = [c for c in cards if c.confidence >= policy.min_confidence]
        cards.sort(key=lambda c: c.confidence, reverse=True)
        cards = cards[: policy.max_cards]

        logger.info(
            f"Node '{node.node_id}': produced {len(cards)} evidence cards "
            f"from {len(chunks)} chunks (rule-based)"
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=[card.model_dump() for card in cards],
            tokens_used=0,
        )

    # ── LLM extraction ────────────────────────────────────────────────────

    async def _extract_with_llm(
        self,
        node: StrategyNode,
        accessor: StateAccessor,
        chunks: list[RetrievedChunk],
        query: str,
        policy: EvidencePolicy,
        capability_id: Optional[str],
    ) -> Optional[NodeOutput]:
        """Extract evidence cards via batched LLM calls.

        Chunks are split into batches (default size 5) to keep prompt size
        manageable.  Each batch produces a JSON array of card dicts which
        are enriched with source metadata from the originating chunk.

        Returns ``None`` when the LLM produces no usable cards so the
        caller can fall back to rule-based extraction.
        """
        batch_size: int = node.config.get("batch_size", 5)
        allowed_types: list[str] = node.config.get("card_types", policy.card_types)

        # Resolve language from business context or default
        language = "en"
        business_ctx = accessor.get_business_context()
        if business_ctx and hasattr(business_ctx, "language"):
            language = business_ctx.language or "en"

        # Build a lookup from chunk index → chunk for metadata enrichment
        all_cards: list[EvidenceCard] = []
        total_tokens = 0

        for batch_start in range(0, len(chunks), batch_size):
            batch = chunks[batch_start: batch_start + batch_size]

            # Serialise chunks for the prompt
            chunk_dicts = [
                {
                    "content": c.content,
                    "source_id": c.source_id,
                    "document_title": c.document_title,
                    "chunk_id": c.chunk_id,
                }
                for c in batch
            ]

            messages = build_evidence_extraction_prompt(
                chunks=chunk_dicts,
                query=query,
                card_types=allowed_types,
                language=language,
            )

            raw, tokens = await self.llm_helper.complete_json(
                "worker", messages, node.config,
            )
            total_tokens += tokens

            # The LLM should return a JSON array of card objects
            card_dicts: list[dict] = []
            if isinstance(raw, list):
                card_dicts = raw
            elif isinstance(raw, dict) and "cards" in raw:
                card_dicts = raw["cards"]
            elif isinstance(raw, dict):
                card_dicts = [raw]

            # Enrich each card with source metadata from the originating chunk
            source_lookup = {c.source_id: c for c in batch}
            title_lookup = {c.document_title: c for c in batch}

            for cd in card_dicts:
                if not isinstance(cd, dict):
                    continue
                # Resolve the originating chunk for metadata
                origin = (
                    source_lookup.get(cd.get("source_id", ""))
                    or title_lookup.get(cd.get("document_title", ""))
                    or batch[0]  # last-resort: first chunk in batch
                )

                span_id = (
                    f"{origin.span_start}-{origin.span_end}"
                    if origin.span_start is not None and origin.span_end is not None
                    else None
                )

                try:
                    card = EvidenceCard(
                        card_id=str(uuid.uuid4()),
                        card_type=cd.get("card_type", "fact"),
                        topic=cd.get("topic", ""),
                        source_id=origin.source_id,
                        document_title=origin.document_title,
                        source_excerpt=cd.get("source_excerpt", ""),
                        factual_basis=cd.get("factual_basis", ""),
                        confidence=float(cd.get("confidence", 0.5)),
                        span_id=span_id,
                        metadata={
                            "chunk_id": origin.chunk_id,
                            "search_type": origin.search_type,
                            "extraction": "llm",
                        },
                    )
                    all_cards.append(card)
                except Exception as e:
                    logger.debug(f"Skipping malformed LLM card: {e}")

        if not all_cards:
            return None

        # Post-processing (same pipeline as rule-based)
        all_cards = _deduplicate_cards(all_cards)
        all_cards = [
            c for c in all_cards if c.confidence >= policy.min_confidence
        ]
        all_cards.sort(key=lambda c: c.confidence, reverse=True)
        all_cards = all_cards[: policy.max_cards]

        logger.info(
            f"Node '{node.node_id}': produced {len(all_cards)} evidence cards "
            f"via LLM ({total_tokens} tokens)"
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=[card.model_dump() for card in all_cards],
            tokens_used=total_tokens,
        )

    def _chunk_to_card(
        self,
        chunk: RetrievedChunk,
        domain: str,
        allowed_types: list[str],
        max_excerpt_words: int,
    ) -> EvidenceCard:
        """Convert a single chunk into an EvidenceCard."""
        content = chunk.content.strip()
        topic = _extract_topic(content)
        excerpt = _truncate_excerpt(content, max_excerpt_words)
        card_type = _infer_card_type(content, domain, allowed_types)
        confidence = _normalise_score(chunk.score)
        factual_basis = _build_factual_basis(chunk, domain)
        span_id = (
            f"{chunk.span_start}-{chunk.span_end}"
            if chunk.span_start is not None and chunk.span_end is not None
            else None
        )

        return EvidenceCard(
            card_id=str(uuid.uuid4()),
            card_type=card_type,  # type: ignore[arg-type]
            topic=topic,
            source_id=chunk.source_id,
            document_title=chunk.document_title,
            source_excerpt=excerpt,
            factual_basis=factual_basis,
            confidence=confidence,
            span_id=span_id,
            metadata={
                "chunk_id": chunk.chunk_id,
                "search_type": chunk.search_type,
                "raw_score": chunk.score,
            },
        )

    # ── main entry point ─────────────────────────────────────────────────────

    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        """Execute evidence card generation.

        Args:
            node: Strategy node definition carrying config overrides.
            state: Current DAG run state.

        Returns:
            ``NodeOutput`` whose ``output_data`` is a list of serialised
            ``EvidenceCard`` dicts, or *None* when no chunks are available.
        """
        accessor = self.get_state_accessor(state)

        # 1. Gather inputs
        chunks = accessor.get_chunks()
        if not chunks:
            logger.info(f"Node '{node.node_id}': no chunks available — returning empty")
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=None,
                tokens_used=0,
            )

        business_ctx = accessor.get_business_context()
        capability_id: Optional[str] = None
        if business_ctx and hasattr(business_ctx, "capability_id"):
            capability_id = business_ctx.capability_id

        # 2. Resolve evidence policy
        policy = self._resolve_evidence_policy(node, accessor)
        allowed_types: list[str] = node.config.get("card_types", policy.card_types)
        max_excerpt_words = policy.preserve_quotes_max_words

        # 3. Detect domain once
        sample_content = chunks[0].content if chunks else ""
        domain = _detect_domain(capability_id, sample_content)
        logger.debug(f"Node '{node.node_id}': detected domain='{domain}'")

        query = accessor.get_query()

        # ── LLM extraction path (preferred when helper is available) ──────
        if self.llm_helper:
            try:
                result = await self._extract_with_llm(
                    node, accessor, chunks, query, policy, capability_id,
                )
                if result:
                    return result
            except Exception as e:
                logger.warning(
                    f"LLM evidence extraction failed, falling back to "
                    f"rule-based: {e}"
                )

        # ── Fallback: rule-based extraction ───────────────────────────────
        return self._extract_rule_based(
            node, chunks, domain, allowed_types, max_excerpt_words, policy,
        )
