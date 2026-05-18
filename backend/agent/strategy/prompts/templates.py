"""Prompt templates for Strategy OS node LLM calls.

Each function returns ``list[dict]`` — a list of message dicts (role/content)
suitable for direct use with OpenAI-compatible chat completion APIs.
"""
from __future__ import annotations

import json
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════════
# Language helpers
# ═══════════════════════════════════════════════════════════════════════════════

_LANGUAGE_INSTRUCTIONS: dict[str, str] = {
    "de": "Respond entirely in German.",
    "en": "Respond entirely in English.",
    "fr": "Respond entirely in French.",
    "es": "Respond entirely in Spanish.",
}


def _lang_instruction(language: str) -> str:
    """Return a language instruction string for the given language code."""
    return _LANGUAGE_INSTRUCTIONS.get(language, f"Respond in {language}.")


# ═══════════════════════════════════════════════════════════════════════════════
# Synthesis
# ═══════════════════════════════════════════════════════════════════════════════


def build_synthesis_prompt(
    evidence_cards: list[dict],
    query: str,
    answer_contract: Optional[dict] = None,
    language: str = "en",
) -> list[dict]:
    """Build prompt for the synthesize node.

    Constructs system + user messages instructing the LLM to produce a
    comprehensive answer from evidence cards with proper citations.

    If answer_contract has output_sections -> instruct per-section output.
    If answer_contract has table_schema -> instruct table format output.
    Default -> prose format.
    """
    system = (
        "You are an expert analyst synthesizing answers from evidence. "
        "Produce a well-structured, accurate response citing sources with [Card N] format. "
        "Never fabricate information beyond what the evidence supports."
    )

    # Format evidence cards as numbered list
    cards_text = _format_evidence_cards(evidence_cards)

    # Determine output format from answer_contract
    format_instruction = _build_format_instruction(answer_contract)

    user = (
        f"## Query\n{query}\n\n"
        f"## Evidence Cards\n{cards_text}\n\n"
        f"## Output Requirements\n{format_instruction}\n\n"
        f"{_lang_instruction(language)}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _format_evidence_cards(cards: list[dict]) -> str:
    """Format evidence cards as a numbered list for prompt inclusion."""
    if not cards:
        return "(No evidence cards provided)"
    lines: list[str] = []
    for i, card in enumerate(cards, 1):
        topic = card.get("topic", "Unknown")
        source = card.get("document_title", card.get("source_id", "Unknown"))
        excerpt = card.get("source_excerpt", "")
        confidence = card.get("confidence", 0.0)
        lines.append(
            f"[Card {i}] Topic: {topic}\n"
            f"  Source: {source}\n"
            f"  Excerpt: {excerpt}\n"
            f"  Confidence: {confidence:.2f}"
        )
    return "\n\n".join(lines)


def _build_format_instruction(answer_contract: Optional[dict]) -> str:
    """Build output format instructions from an answer contract."""
    if not answer_contract:
        return (
            "Format: Prose response with clear paragraphs. "
            "Cite sources using [Card N] notation inline."
        )

    sections = answer_contract.get("output_sections")
    table_schema = answer_contract.get("table_schema")
    tone = answer_contract.get("tone", "professional")

    if sections:
        section_descs = []
        for sec in sections:
            title = sec.get("title", "Untitled")
            fmt = sec.get("format", "prose")
            desc = sec.get("description", "")
            required = "Required" if sec.get("required", True) else "Optional"
            section_descs.append(f"- **{title}** ({fmt}, {required}): {desc}")
        return (
            f"Format: Multi-section document. Tone: {tone}.\n"
            f"Sections:\n" + "\n".join(section_descs) + "\n"
            "Cite sources using [Card N] notation inline within each section."
        )

    if table_schema:
        columns = table_schema.get("columns", [])
        col_names = [c.get("display_name") or c.get("name", "") for c in columns]
        max_rows = table_schema.get("max_rows")
        return (
            f"Format: Markdown table. Tone: {tone}.\n"
            f"Columns: {', '.join(col_names)}\n"
            f"{'Max rows: ' + str(max_rows) if max_rows else ''}\n"
            "Include a [Card N] citation in relevant cells."
        )

    return (
        f"Format: Prose response. Tone: {tone}. "
        "Cite sources using [Card N] notation inline."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Evidence Extraction
# ═══════════════════════════════════════════════════════════════════════════════


def build_evidence_extraction_prompt(
    chunks: list[dict],
    query: str,
    card_types: list[str] | None = None,
    language: str = "en",
) -> list[dict]:
    """Build prompt for the evidence_cards node.

    Instructs the LLM to extract structured evidence cards from retrieved
    document chunks as a JSON array.
    """
    if card_types is None:
        card_types = ["fact", "legal_question", "contradiction", "business_insight", "technical_requirement"]

    system = (
        "You are an evidence extraction specialist. "
        "Extract structured evidence from document chunks and return a JSON array. "
        "Be precise: only extract claims that are directly supported by the text."
    )

    chunks_text = _format_chunks(chunks)

    user = (
        f"## User Query\n{query}\n\n"
        f"## Document Chunks\n{chunks_text}\n\n"
        f"## Instructions\n"
        f"Extract evidence cards from the chunks above relevant to the query.\n"
        f"Allowed card types: {', '.join(card_types)}\n\n"
        f"## Response Format\n"
        f"Return ONLY a JSON array of objects with this structure:\n"
        f'{{"topic": "string", "factual_basis": "string", "source_excerpt": "string", '
        f'"card_type": "string", "confidence": 0.0}}\n\n'
        f"{_lang_instruction(language)}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _format_chunks(chunks: list[dict]) -> str:
    """Format document chunks as numbered items for prompt inclusion."""
    if not chunks:
        return "(No chunks provided)"
    lines: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        title = chunk.get("document_title", "Unknown")
        content = chunk.get("content", "")
        source_id = chunk.get("source_id", "")
        lines.append(f"[Chunk {i}] Source: {title} (id: {source_id})\n{content}")
    return "\n\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Query Expansion
# ═══════════════════════════════════════════════════════════════════════════════


def build_query_expansion_prompt(
    query: str,
    language: str = "en",
    max_variants: int = 3,
) -> list[dict]:
    """Build prompt for the query_expand node.

    Generates alternative phrasings of the query to improve retrieval recall.
    Always includes the original query as the first variant.
    """
    system = (
        "You are a search query optimization expert. "
        "Generate alternative phrasings of a query to maximize retrieval quality. "
        "Include synonyms, rephrasings, and different perspectives."
    )

    user = (
        f"## Original Query\n{query}\n\n"
        f"## Instructions\n"
        f"Generate {max_variants} alternative phrasings of this query.\n"
        f"Include the original query as the first item.\n"
        f"Each variant should approach the topic from a slightly different angle "
        f"(synonyms, broader/narrower scope, different emphasis).\n\n"
        f"## Response Format\n"
        f"Return ONLY a JSON array of strings. Example:\n"
        f'["{query}", "alternative phrasing 1", "alternative phrasing 2"]\n\n'
        f"{_lang_instruction(language)}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Plan
# ═══════════════════════════════════════════════════════════════════════════════


def build_plan_prompt(
    query: str,
    chunks_summary: str = "",
    answer_contract: Optional[dict] = None,
) -> list[dict]:
    """Build prompt for the plan node.

    Creates a step-by-step research plan for answering the query.
    """
    system = (
        "You are a research planning expert. "
        "Create clear, actionable plans for answering complex queries. "
        "Plans should be logical, step-by-step, and consider available information."
    )

    context_section = ""
    if chunks_summary:
        context_section = f"## Available Information\n{chunks_summary}\n\n"

    contract_section = ""
    if answer_contract:
        fmt = answer_contract.get("format_id", "prose")
        sections = answer_contract.get("output_sections", [])
        if sections:
            section_names = [s.get("title", "") for s in sections]
            contract_section = (
                f"## Output Requirements\n"
                f"Format: {fmt}\n"
                f"Required sections: {', '.join(section_names)}\n\n"
            )

    user = (
        f"## Query\n{query}\n\n"
        f"{context_section}"
        f"{contract_section}"
        f"## Instructions\n"
        f"Create a step-by-step plan for comprehensively answering this query.\n\n"
        f"## Response Format\n"
        f'Return ONLY valid JSON: {{"plan_text": "brief summary", "steps": ["step 1", "step 2", ...]}}'
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Refinement
# ═══════════════════════════════════════════════════════════════════════════════


def build_refinement_prompt(
    synthesis_text: str,
    issues: list[dict],
    answer_contract: Optional[dict] = None,
) -> list[dict]:
    """Build prompt for the refine node.

    Instructs the LLM to improve the synthesis by addressing identified issues.
    """
    system = (
        "You are an expert editor improving answer quality. "
        "Fix identified issues while preserving valid citations and accurate content. "
        "Do not introduce information that was not in the original answer."
    )

    issues_text = _format_issues(issues)

    contract_section = ""
    if answer_contract:
        tone = answer_contract.get("tone", "professional")
        contract_section = f"\n## Format Requirements\nTone: {tone}\n"
        sections = answer_contract.get("output_sections")
        if sections:
            names = [s.get("title", "") for s in sections]
            contract_section += f"Expected sections: {', '.join(names)}\n"

    user = (
        f"## Current Answer\n{synthesis_text}\n\n"
        f"## Identified Issues\n{issues_text}\n"
        f"{contract_section}\n"
        f"## Instructions\n"
        f"Produce an improved version of the answer that addresses all listed issues.\n"
        f"Preserve all valid [Card N] citations. Fix or remove invalid ones.\n"
        f"Return ONLY the improved text (no JSON wrapper needed)."
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _format_issues(issues: list[dict]) -> str:
    """Format validation issues as a numbered list."""
    if not issues:
        return "(No issues identified)"
    lines: list[str] = []
    for i, issue in enumerate(issues, 1):
        issue_type = issue.get("issue_type", "unknown")
        severity = issue.get("severity", "warning")
        message = issue.get("message", "")
        lines.append(f"{i}. [{severity}] {issue_type}: {message}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Judge Quality
# ═══════════════════════════════════════════════════════════════════════════════

# Default 7 dimensions with weights
_JUDGE_DIMENSIONS: list[dict[str, str | float]] = [
    {"id": "groundedness", "weight": 0.25, "desc": "Is the answer grounded in the provided sources?"},
    {"id": "citation_quality", "weight": 0.20, "desc": "Are citations accurate, relevant, and sufficient?"},
    {"id": "directness", "weight": 0.10, "desc": "Does the answer directly address the question?"},
    {"id": "completeness", "weight": 0.15, "desc": "Are all aspects of the question covered?"},
    {"id": "format_adherence", "weight": 0.15, "desc": "Does the output follow the requested format?"},
    {"id": "domain_value", "weight": 0.10, "desc": "Does the answer demonstrate domain expertise?"},
    {"id": "conciseness", "weight": 0.05, "desc": "Is the answer appropriately concise without losing substance?"},
]


def build_judge_prompt(
    query: str,
    synthesis_text: str,
    chunks_summary: str = "",
    dimensions: list[str] | None = None,
) -> list[dict]:
    """Build prompt for the judge_quality node.

    Instructs the LLM to score the response on each dimension (0.0-1.0)
    with brief evidence justifying each score.
    """
    system = (
        "You are a quality evaluation judge. "
        "Score the AI-generated response on each specified dimension from 0.0 to 1.0. "
        "Provide brief, specific evidence justifying each score. "
        "Be critical but fair."
    )

    # Filter dimensions if a subset is requested
    active_dims = _JUDGE_DIMENSIONS
    if dimensions:
        active_dims = [d for d in _JUDGE_DIMENSIONS if d["id"] in dimensions]
        if not active_dims:
            active_dims = _JUDGE_DIMENSIONS

    dims_text = "\n".join(
        f"- {d['id']} (weight: {d['weight']}): {d['desc']}"
        for d in active_dims
    )

    source_section = ""
    if chunks_summary:
        source_section = f"## Source Material Summary\n{chunks_summary}\n\n"

    # Build the expected JSON shape
    dims_json = ", ".join(
        f'"{d["id"]}": {{"score": 0.0, "evidence": "..."}}'
        for d in active_dims
    )

    user = (
        f"## Dimensions\n{dims_text}\n\n"
        f"## User Question\n{query}\n\n"
        f"{source_section}"
        f"## Answer to Evaluate\n{synthesis_text}\n\n"
        f"## Response Format\n"
        f"Respond ONLY with valid JSON:\n"
        f'{{"dimensions": {{{dims_json}}}}}'
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Rerank
# ═══════════════════════════════════════════════════════════════════════════════


def build_rerank_prompt(
    query: str,
    chunks: list[dict],
) -> list[dict]:
    """Build prompt for the rerank node.

    Instructs the LLM to score each chunk's relevance to the query
    from 0.0 to 1.0.
    """
    system = (
        "You are a relevance scoring expert. "
        "Score each document chunk's relevance to the query from 0.0 to 1.0. "
        "0.0 = completely irrelevant, 1.0 = perfectly relevant."
    )

    chunks_text = _format_chunks_for_rerank(chunks)

    user = (
        f"## Query\n{query}\n\n"
        f"## Document Chunks\n{chunks_text}\n\n"
        f"## Instructions\n"
        f"Score each chunk's relevance to the query.\n\n"
        f"## Response Format\n"
        f"Return ONLY a JSON array:\n"
        f'[{{"chunk_index": 0, "relevance_score": 0.0}}, ...]'
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _format_chunks_for_rerank(chunks: list[dict]) -> str:
    """Format chunks with index numbers for reranking."""
    if not chunks:
        return "(No chunks provided)"
    lines: list[str] = []
    for i, chunk in enumerate(chunks):
        content = chunk.get("content", "")
        title = chunk.get("document_title", "Unknown")
        lines.append(f"[{i}] ({title}): {content}")
    return "\n\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Intent Classification
# ═══════════════════════════════════════════════════════════════════════════════

_DEFAULT_INTENTS = [
    "general",
    "legal_question",
    "summary",
    "comparison",
    "fact_check",
    "explanation",
]


def build_intent_classify_prompt(
    query: str,
    intents: list[str] | None = None,
) -> list[dict]:
    """Build prompt for the intent_classify node.

    Classifies the query into one of the available intent categories
    with confidence and optional domain.
    """
    if intents is None:
        intents = _DEFAULT_INTENTS

    system = (
        "You are a query intent classifier. "
        "Classify the user's query into the most appropriate intent category. "
        "Be precise and confident in your classification."
    )

    intents_text = ", ".join(intents)

    user = (
        f"## Query\n{query}\n\n"
        f"## Available Intents\n{intents_text}\n\n"
        f"## Instructions\n"
        f"Classify the query into the single most appropriate intent.\n"
        f"If none fit perfectly, choose the closest match.\n\n"
        f"## Response Format\n"
        f"Return ONLY valid JSON:\n"
        f'{{"intent": "chosen_intent", "confidence": 0.0, "domain": "detected_domain"}}'
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
