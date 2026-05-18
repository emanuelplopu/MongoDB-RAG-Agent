"""Response synthesis node executor.

Combines evidence cards (or raw chunks as fallback) into a coherent
response shaped by the active AnswerContract.  Uses LLM-driven synthesis
when a NodeLLMHelper is provided; falls back to deterministic template
assembly otherwise.

Reference: docs/08-STRATEGY_DAG_AND_NODES.md
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from backend.agent.strategy.context_budgeter import (
    DEFAULT_TOTAL_BUDGET_TOKENS,
    BudgetBreakdown,
    ContextBudgeter,
)
from backend.agent.strategy.models import (
    AnswerContract,
    CitationRef,
    EvidenceCard,
    NodeOutput,
    OutputSection,
    ResolvedSourcePolicy,
    RetrievedChunk,
    SourcePolicy,
    StrategyNode,
    StrategyRunState,
    SynthesisResult,
    TableSchema,
)
from backend.agent.strategy.nodes.base import NodeExecutor, StateAccessor
from backend.agent.strategy.operational_trace import emit_trace_event
from backend.agent.strategy.prompts import build_synthesis_prompt

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

_MODEL_NAME_TEMPLATE = "template_v1"
"""Placeholder model name until a real LLM is wired."""

_DEFAULT_MAX_OUTPUT_TOKENS = 2000
"""Soft ceiling for template output length (in approximate word count)."""

_BUDGET_TRACE_EVENT = "context_budget_applied"
"""Trace event name emitted by the budgeter when it allocates context."""

# ═══════════════════════════════════════════════════════════════════════════════
# Citation helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _citation_from_card(card: EvidenceCard) -> CitationRef:
    """Build a CitationRef from an EvidenceCard."""
    return CitationRef(
        source_id=card.source_id,
        document_title=card.document_title,
        span_start=None,
        span_end=None,
        quote=card.source_excerpt[:120] if card.source_excerpt else None,
        confidence=card.confidence,
    )


def _citation_from_chunk(chunk: RetrievedChunk) -> CitationRef:
    """Build a CitationRef from a raw RetrievedChunk."""
    return CitationRef(
        source_id=chunk.source_id,
        document_title=chunk.document_title,
        span_start=chunk.span_start,
        span_end=chunk.span_end,
        quote=chunk.content[:120] if chunk.content else None,
        confidence=chunk.score,
    )


def _deduplicate_citations(citations: list[CitationRef]) -> list[CitationRef]:
    """Remove duplicate citations (same source_id)."""
    seen: set[str] = set()
    unique: list[CitationRef] = []
    for c in citations:
        if c.source_id not in seen:
            seen.add(c.source_id)
            unique.append(c)
    return unique


def _avg_confidence(
    cards: list[EvidenceCard],
    chunks: list[RetrievedChunk],
) -> float:
    """Compute average confidence from evidence cards or chunks."""
    if cards:
        values = [c.confidence for c in cards]
    elif chunks:
        values = [c.score for c in chunks]
    else:
        return 0.0
    return round(sum(values) / len(values), 4) if values else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Format builders (template-based)
# ═══════════════════════════════════════════════════════════════════════════════


def _build_prose(
    cards: list[EvidenceCard],
    chunks: list[RetrievedChunk],
    query: str,
    language: str,
) -> str:
    """Build a prose-style answer from evidence cards / chunks."""
    parts: list[str] = []

    # Introduction
    if language.startswith("de"):
        parts.append(f"Basierend auf den verfuegbaren Quellen zur Anfrage '{query}':\n")
    else:
        parts.append(f"Based on the available sources regarding \"{query}\":\n")

    # Body paragraphs from evidence cards
    if cards:
        for idx, card in enumerate(cards, 1):
            parts.append(
                f"{idx}. **{card.topic}** — {card.source_excerpt} "
                f"(Source: {card.document_title}) [confidence: {card.confidence:.2f}]"
            )
    elif chunks:
        for idx, chunk in enumerate(chunks, 1):
            topic = chunk.content.split(".")[0].strip()[:80]
            parts.append(
                f"{idx}. **{topic}** — {chunk.content[:200]} "
                f"(Source: {chunk.document_title})"
            )

    # Conclusion
    source_count = len(cards) if cards else len(chunks)
    if language.startswith("de"):
        parts.append(f"\n_Zusammengefasst aus {source_count} Quelle(n)._")  # noqa: RUF001
    else:
        parts.append(f"\n_Synthesized from {source_count} source(s)._")

    return "\n\n".join(parts)


def _build_table(
    cards: list[EvidenceCard],
    chunks: list[RetrievedChunk],
    schema: TableSchema,
) -> str:
    """Build a markdown table from evidence cards / chunks following *schema*."""
    col_names = [col.display_name or col.name for col in schema.columns]
    col_keys = [col.name for col in schema.columns]

    # Header
    header = "| " + " | ".join(col_names) + " |"
    separator = "| " + " | ".join("---" for _ in col_names) + " |"
    rows: list[str] = [header, separator]

    # Map cards / chunks into rows
    items: list[dict[str, Any]] = []
    if cards:
        for card in cards:
            row: dict[str, Any] = {
                "topic": card.topic,
                "source": card.document_title,
                "excerpt": card.source_excerpt[:80],
                "confidence": f"{card.confidence:.2f}",
                "card_type": card.card_type,
                "factual_basis": card.factual_basis[:80],
                "source_id": card.source_id,
            }
            items.append(row)
    elif chunks:
        for chunk in chunks:
            row = {
                "topic": chunk.content.split(".")[0].strip()[:60],
                "source": chunk.document_title,
                "excerpt": chunk.content[:80],
                "confidence": f"{chunk.score:.2f}",
                "source_id": chunk.source_id,
            }
            items.append(row)

    # Apply row limits
    if schema.max_rows and len(items) > schema.max_rows:
        items = items[: schema.max_rows]

    for item in items:
        cells = [str(item.get(key, "")) for key in col_keys]
        rows.append("| " + " | ".join(cells) + " |")

    return "\n".join(rows)


def _build_sectioned(
    cards: list[EvidenceCard],
    chunks: list[RetrievedChunk],
    sections: list[OutputSection],
    query: str,
    language: str,
) -> str:
    """Build a sectioned answer from evidence cards / chunks.

    Each ``OutputSection`` becomes a heading with relevant evidence
    gathered beneath it.
    """
    parts: list[str] = []

    for section in sections:
        parts.append(f"## {section.title}")

        # Gather evidence relevant to section (simple keyword match for now)
        section_lower = section.title.lower()
        relevant_cards = [
            c for c in cards if section_lower in c.topic.lower()
        ] if cards else []
        relevant_chunks = [
            ch for ch in chunks if section_lower in ch.content.lower()
        ] if not relevant_cards and chunks else []

        # If no relevance match, distribute evenly
        if not relevant_cards and not relevant_chunks:
            # Assign cards round-robin if nothing matched
            if cards:
                relevant_cards = cards
            elif chunks:
                relevant_chunks = chunks

        if section.format == "table":
            # Create a minimal table schema if none defined
            fallback_schema = TableSchema(
                columns=[],
                row_source="evidence_cards",
            )
            parts.append(
                _build_table(relevant_cards, relevant_chunks, fallback_schema)
                if relevant_cards or relevant_chunks
                else "_No evidence available for this section._"
            )
        elif section.format == "list":
            for item in relevant_cards:
                parts.append(f"- **{item.topic}**: {item.source_excerpt[:100]}")
            for item in relevant_chunks:  # type: ignore[assignment]
                parts.append(f"- {item.content[:100]}")  # type: ignore[union-attr]
            if not relevant_cards and not relevant_chunks:
                parts.append("_No evidence available for this section._")
        else:
            # prose
            prose = _build_prose(relevant_cards, relevant_chunks, query, language)
            parts.append(prose)

        parts.append("")  # blank line between sections

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# Executor
# ═══════════════════════════════════════════════════════════════════════════════


class SynthesizeExecutor(NodeExecutor):
    """Response synthesis node with LLM and template paths.

    When a ``NodeLLMHelper`` is provided, uses LLM-driven synthesis for
    fluent, context-aware responses.  Falls back to deterministic template
    assembly when the helper is absent or the LLM call fails.

    Supported output formats:

    * **Prose** — numbered evidence paragraphs with introduction and conclusion.
    * **Table** — markdown table following a ``TableSchema``.
    * **Sectioned** — multi-heading document driven by ``OutputSection`` definitions.
    """

    def __init__(self, llm_helper=None):
        self.llm_helper = llm_helper  # NodeLLMHelper or None

    # ── helpers ──────────────────────────────────────────────────────────────

    def _resolve_answer_contract(
        self,
        node: StrategyNode,
        accessor: StateAccessor,
    ) -> Optional[AnswerContract]:
        """Resolve the effective AnswerContract from config or business context."""
        # 1. Node-level override
        raw = node.config.get("answer_contract")
        if isinstance(raw, dict):
            return AnswerContract(**raw)
        # 2. Business context
        bctx = accessor.get_business_context()
        if bctx and hasattr(bctx, "answer_contract") and bctx.answer_contract:
            return bctx.answer_contract
        # 3. State metadata
        meta = accessor.get_metadata("answer_contract")
        if isinstance(meta, dict):
            return AnswerContract(**meta)
        return None

    def _resolve_language(self, accessor: StateAccessor) -> str:
        """Determine output language from business context or default."""
        bctx = accessor.get_business_context()
        if bctx and hasattr(bctx, "language_policy") and bctx.language_policy:
            return bctx.language_policy.primary_languages[0]
        if bctx and hasattr(bctx, "language"):
            return bctx.language  # type: ignore[return-value]
        return "en"

    def _determine_format(
        self,
        contract: Optional[AnswerContract],
        format_override: Optional[str],
    ) -> str:
        """Choose output format: prose / table / sectioned."""
        if format_override:
            return format_override
        if contract:
            if contract.table_schema:
                return "table"
            if contract.output_sections:
                return "sectioned"
        return "prose"

    # ── main entry point ─────────────────────────────────────────────────────

    async def execute(
        self,
        node: StrategyNode,
        state: StrategyRunState,
    ) -> NodeOutput:
        """Execute response synthesis.

        Tries LLM-driven synthesis when *llm_helper* is available.
        Falls back to deterministic template assembly on failure or
        when no helper is configured.

        Args:
            node: Strategy node definition carrying config overrides.
            state: Current DAG run state.

        Returns:
            ``NodeOutput`` whose ``output_data`` is a serialised
            ``SynthesisResult`` dict, or *None* when no evidence is available.
        """
        accessor = self.get_state_accessor(state)

        # 1. Gather evidence cards (preferred) or chunks (fallback)
        cards = accessor.get_evidence_cards()
        chunks: list[RetrievedChunk] = []
        if not cards:
            chunks = accessor.get_chunks()

        if not cards and not chunks:
            logger.info(
                f"Node '{node.node_id}': no evidence cards or chunks — returning empty"
            )
            return NodeOutput(
                node_id=node.node_id,
                node_type=node.node_type,
                status="empty",
                output_data=None,
                tokens_used=0,
            )

        # 2. Resolve contract, language, and config
        contract = self._resolve_answer_contract(node, accessor)
        language = self._resolve_language(accessor)
        query = accessor.get_normalized_query() or accessor.get_query()

        logger.debug(
            f"Node '{node.node_id}': lang={language}, "
            f"cards={len(cards)}, chunks={len(chunks)}"
        )

        # 3. Try LLM synthesis first
        if self.llm_helper:
            try:
                result = await self._synthesize_with_llm(
                    node, accessor, cards, chunks, query, contract, language,
                )
                if result:
                    return result
            except Exception as e:
                logger.warning(f"LLM synthesis failed, falling back to template: {e}")

        # 4. Fallback: deterministic template-based synthesis
        return self._synthesize_template(
            node, cards, chunks, query, contract, language,
        )

    # ── LLM-driven synthesis ─────────────────────────────────────────────────

    async def _synthesize_with_llm(
        self,
        node: StrategyNode,
        accessor: StateAccessor,
        evidence_cards: list[EvidenceCard],
        chunks: list[RetrievedChunk],
        query: str,
        answer_contract: Optional[AnswerContract],
        language: str,
    ) -> Optional[NodeOutput]:
        """Synthesize a response using an LLM call.

        Returns ``None`` if the LLM produces an empty response so the
        caller can fall through to the template path.
        """
        # Apply the context budgeter so large local models never get
        # 20k tokens for a 4k-token-worth task. The budgeter
        # deterministically drops the lowest-priority items first and
        # records the breakdown for telemetry.
        budgeted_cards, budgeted_chunks = self._apply_context_budget(
            node=node,
            accessor=accessor,
            evidence_cards=evidence_cards,
            chunks=chunks,
            query=query,
            answer_contract=answer_contract,
        )

        # Serialize evidence cards for the prompt
        cards_data = [
            c.model_dump() if hasattr(c, "model_dump") else c
            for c in budgeted_cards
        ]
        contract_data = (
            answer_contract.model_dump()
            if answer_contract and hasattr(answer_contract, "model_dump")
            else answer_contract
        )

        # Build prompt messages
        messages = build_synthesis_prompt(
            evidence_cards=cards_data,
            query=query,
            answer_contract=contract_data,
            language=language,
        )

        # Determine model role from node config
        role = node.config.get("model_role", "synthesizer_fast")

        # Call LLM
        response_text, tokens_used = await self.llm_helper.complete(
            role, messages, node.config,
        )

        if not response_text or not response_text.strip():
            logger.warning(f"Node '{node.node_id}': LLM returned empty response")
            return None

        # Extract citations from [Card N] references in the response
        citations = self._extract_citations_from_response(
            response_text, budgeted_cards or evidence_cards,
        )

        # Build result
        format_id = (
            answer_contract.format_id
            if answer_contract and hasattr(answer_contract, "format_id")
            else "prose"
        )

        synthesis = SynthesisResult(
            text=response_text,
            citations=citations,
            language=language,
            format_id=format_id,
            token_count=tokens_used,
            model_used=role,
            confidence=0.85,  # higher baseline for LLM-generated
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=synthesis.model_dump(),
            model_used=role,
            tokens_used=tokens_used,
        )

    # ── context budgeting ────────────────────────────────────────────────────

    def _apply_context_budget(
        self,
        *,
        node: StrategyNode,
        accessor: StateAccessor,
        evidence_cards: list[EvidenceCard],
        chunks: list[RetrievedChunk],
        query: str,
        answer_contract: Optional[AnswerContract],
    ) -> tuple[list[EvidenceCard], list[RetrievedChunk]]:
        """Run the :class:`ContextBudgeter` and return the items that fit.

        The budgeter is fed, in priority order:

        1. The active ``AnswerContract`` (if any).
        2. The user prompt / normalized query (always).
        3. A short summary of the active ``SourcePolicy`` (if any).
        4. Each ``EvidenceCard`` in score / confidence order.
        5. Each raw chunk span (used only when no evidence cards exist).
        6. Recent conversation snippets (when surfaced by upstream
           nodes via ``state.metadata['conversation_history']``).

        The breakdown is emitted as a ``context_budget_applied`` trace
        event for diagnostics, and only the included evidence cards /
        chunks are returned for downstream prompt assembly.

        Args:
            node: The current strategy node (for config overrides).
            accessor: Read-only state accessor.
            evidence_cards: Score-ordered evidence cards from upstream.
            chunks: Raw retrieved chunks (fallback when no cards).
            query: User prompt / normalized query.
            answer_contract: Active answer contract, if any.

        Returns:
            ``(included_cards, included_chunks)`` — the subset of the
            inputs that fit within the active context budget. When the
            budget is large enough to fit everything, the inputs are
            returned unchanged.
        """
        budget_cap = self._resolve_context_budget(node, accessor)
        budgeter = ContextBudgeter(total_budget_tokens=budget_cap)

        # Track originals so we can map the budgeter's `included` list
        # back to the actual model instances.
        card_by_signature: dict[str, EvidenceCard] = {}
        chunk_by_signature: dict[str, RetrievedChunk] = {}

        # 1. Answer contract (highest priority).
        if answer_contract is not None:
            budgeter.add(
                "answer_contract",
                self._format_answer_contract(answer_contract),
            )

        # 2. User prompt — always included if non-empty.
        if query:
            budgeter.add("user_prompt", query)

        # 3. Source policy summary, if reachable from business context.
        policy_text = self._format_source_policy(accessor)
        if policy_text:
            budgeter.add("source_policy", policy_text)

        # 4. Evidence cards in their incoming order (already
        #    score-sorted by upstream nodes).
        for idx, card in enumerate(evidence_cards):
            content = self._format_evidence_card(idx, card)
            sig = f"card::{idx}"
            card_by_signature[sig] = card
            budgeter.add(
                "evidence_card",
                content,
                metadata={"signature": sig, "card_id": card.card_id},
            )

        # 5. Raw spans — only meaningful when there are no evidence
        #    cards (otherwise they're redundant with the cards).
        if not evidence_cards and chunks:
            for idx, chunk in enumerate(chunks):
                content = self._format_raw_span(idx, chunk)
                sig = f"chunk::{idx}"
                chunk_by_signature[sig] = chunk
                budgeter.add(
                    "raw_span",
                    content,
                    metadata={"signature": sig, "chunk_id": chunk.chunk_id},
                )

        # 6. Conversation context, if upstream nodes attached any.
        for snippet in self._collect_conversation_snippets(accessor):
            budgeter.add("conversation", snippet)

        _, breakdown = budgeter.build()

        # Telemetry — emit a structured trace event with the breakdown.
        self._emit_budget_trace(node, accessor, breakdown)

        # Map the budgeter's included items back to the originals so
        # we can hand them to ``build_synthesis_prompt``.
        included_cards: list[EvidenceCard] = []
        included_chunks: list[RetrievedChunk] = []
        for item in breakdown.included:
            if item.kind == "evidence_card":
                sig = item.metadata.get("signature")
                if sig and sig in card_by_signature:
                    included_cards.append(card_by_signature[sig])
            elif item.kind == "raw_span":
                sig = item.metadata.get("signature")
                if sig and sig in chunk_by_signature:
                    included_chunks.append(chunk_by_signature[sig])

        # If nothing was dropped, hand back the originals so we don't
        # alter ordering or identity for callers that compare by ref.
        if not breakdown.dropped:
            return evidence_cards, chunks
        return included_cards, included_chunks

    @staticmethod
    def _resolve_context_budget(
        node: StrategyNode,
        accessor: StateAccessor,
    ) -> int:
        """Resolve the active context budget cap in tokens.

        Resolution order:

        1. ``node.config['max_context_tokens']`` — explicit override.
        2. ``state.metadata['strategy_budgets']['max_context_tokens']``
           — populated by the runner when a strategy spec is active.
        3. :data:`DEFAULT_TOTAL_BUDGET_TOKENS` (6000) — safe default.

        Args:
            node: Current strategy node.
            accessor: Read-only state accessor.

        Returns:
            A positive integer token budget.
        """
        node_override = node.config.get("max_context_tokens")
        if isinstance(node_override, int) and node_override > 0:
            return node_override

        meta_budgets = accessor.get_metadata("strategy_budgets") or {}
        if isinstance(meta_budgets, dict):
            cap = meta_budgets.get("max_context_tokens")
            if isinstance(cap, int) and cap > 0:
                return cap

        return DEFAULT_TOTAL_BUDGET_TOKENS

    @staticmethod
    def _format_answer_contract(contract: AnswerContract) -> str:
        """Render an :class:`AnswerContract` as a compact textual blob."""
        sections = ", ".join(
            f"{s.title}({s.format})" for s in contract.output_sections
        ) or "-"
        table = (
            ", ".join(c.name for c in contract.table_schema.columns)
            if contract.table_schema
            else "-"
        )
        return (
            f"format_id={contract.format_id} | language={contract.language} | "
            f"tone={contract.tone} | citation_granularity={contract.citation_granularity} | "
            f"sections=[{sections}] | table_columns=[{table}]"
        )

    @staticmethod
    def _format_evidence_card(idx: int, card: EvidenceCard) -> str:
        """Render an :class:`EvidenceCard` for budget accounting."""
        return (
            f"[Card {idx + 1}] {card.topic} (src={card.document_title}, "
            f"conf={card.confidence:.2f})\n{card.source_excerpt}"
        )

    @staticmethod
    def _format_raw_span(idx: int, chunk: RetrievedChunk) -> str:
        """Render a :class:`RetrievedChunk` for budget accounting."""
        return (
            f"[Span {idx + 1}] src={chunk.document_title} "
            f"score={chunk.score:.2f}\n{chunk.content}"
        )

    @staticmethod
    def _format_source_policy(accessor: StateAccessor) -> Optional[str]:
        """Render the active source policy as a single-line summary."""
        bctx = accessor.get_business_context()
        if not bctx:
            return None
        policy = getattr(bctx, "resolved_source_policy", None)
        if not isinstance(policy, ResolvedSourcePolicy):
            return None
        return (
            f"primary={policy.primary_sources or []} | "
            f"secondary={policy.secondary_sources or []} | "
            f"web={policy.allow_web} | personal={policy.allow_personal} | "
            f"cross_matter={policy.allow_cross_matter}"
        )

    @staticmethod
    def _collect_conversation_snippets(
        accessor: StateAccessor,
    ) -> list[str]:
        """Pull conversation history snippets from state metadata."""
        history = accessor.get_metadata("conversation_history") or []
        snippets: list[str] = []
        if isinstance(history, list):
            for msg in history:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if content:
                    snippets.append(f"[{role}] {content}")
        return snippets

    @staticmethod
    def _emit_budget_trace(
        node: StrategyNode,
        accessor: StateAccessor,
        breakdown: BudgetBreakdown,
    ) -> None:
        """Emit the ``context_budget_applied`` trace event."""
        # Count included items by kind so the payload stays compact.
        counts: dict[str, int] = {}
        for item in breakdown.included:
            counts[item.kind] = counts.get(item.kind, 0) + 1

        payload = {
            "node_id": node.node_id,
            "total_budget_tokens": breakdown.total_budget_tokens,
            "total_tokens_used": breakdown.total_tokens_used,
            "included_count_by_kind": counts,
            "included_total": len(breakdown.included),
            "dropped": list(breakdown.dropped),
        }
        # ``StateAccessor`` exposes the underlying state as ``_state``.
        # Falling back to ``None`` keeps the helper safe when accessor
        # is somehow detached.
        state = getattr(accessor, "_state", None)
        emit_trace_event(state, _BUDGET_TRACE_EVENT, payload)

    # ── citation extraction ──────────────────────────────────────────────────

    @staticmethod
    def _extract_citations_from_response(
        text: str,
        evidence_cards: list[EvidenceCard],
    ) -> list[CitationRef]:
        """Extract citation references from LLM response text.

        Recognises ``[Card N]`` and ``[N]`` patterns and maps the
        1-based index back to the corresponding evidence card.
        """
        if not evidence_cards:
            return []

        # Find all [Card N] or plain [N] references
        pattern = re.compile(r"\[(?:Card\s+)?(\d+)\]")
        referenced_indices: set[int] = set()
        for match in pattern.finditer(text):
            idx = int(match.group(1)) - 1  # convert 1-based to 0-based
            if 0 <= idx < len(evidence_cards):
                referenced_indices.add(idx)

        # If no explicit references found, cite all cards
        if not referenced_indices:
            referenced_indices = set(range(len(evidence_cards)))

        citations: list[CitationRef] = []
        seen: set[str] = set()
        for idx in sorted(referenced_indices):
            card = evidence_cards[idx]
            if card.source_id not in seen:
                seen.add(card.source_id)
                citations.append(_citation_from_card(card))

        return citations

    # ── template-based synthesis (fallback) ──────────────────────────────────

    def _synthesize_template(
        self,
        node: StrategyNode,
        cards: list[EvidenceCard],
        chunks: list[RetrievedChunk],
        query: str,
        contract: Optional[AnswerContract],
        language: str,
    ) -> NodeOutput:
        """Deterministic template-based synthesis (original implementation)."""
        format_override = node.config.get("format_override")
        max_output_tokens: int = node.config.get(
            "max_output_tokens", _DEFAULT_MAX_OUTPUT_TOKENS,
        )
        output_format = self._determine_format(contract, format_override)

        logger.debug(
            f"Node '{node.node_id}': template path, format={output_format}"
        )

        # Build text according to format
        if output_format == "table" and contract and contract.table_schema:
            text = _build_table(cards, chunks, contract.table_schema)
        elif output_format == "sectioned" and contract and contract.output_sections:
            text = _build_sectioned(
                cards, chunks, contract.output_sections, query, language,
            )
        else:
            text = _build_prose(cards, chunks, query, language)

        # Build citations
        citations: list[CitationRef] = []
        if cards:
            citations = [_citation_from_card(c) for c in cards]
        elif chunks:
            citations = [_citation_from_chunk(c) for c in chunks]
        citations = _deduplicate_citations(citations)

        # Compute confidence
        confidence = _avg_confidence(cards, chunks)

        # Approximate token count (rough: 1 token ≈ 0.75 words)
        word_count = len(text.split())
        token_count = int(word_count / 0.75)

        # Apply soft length cap
        if token_count > max_output_tokens:
            target_words = int(max_output_tokens * 0.75)
            words = text.split()
            text = " ".join(words[:target_words]) + "\n\n_[Output truncated]_"
            token_count = max_output_tokens

        # Build SynthesisResult
        format_id = contract.format_id if contract else "prose_default"
        synthesis = SynthesisResult(
            text=text,
            citations=citations,
            language=language,
            format_id=format_id,
            token_count=token_count,
            model_used=_MODEL_NAME_TEMPLATE,
            confidence=confidence,
        )

        return NodeOutput(
            node_id=node.node_id,
            node_type=node.node_type,
            status="success",
            output_data=synthesis.model_dump(),
            model_used=_MODEL_NAME_TEMPLATE,
            tokens_used=0,  # no LLM calls in template path
        )
