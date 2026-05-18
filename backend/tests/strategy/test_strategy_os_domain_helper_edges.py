"""Strategy OS domain helper edge coverage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.agent.strategy.business_context_resolver import BusinessContextResolver
from backend.agent.strategy.models import (
    AnswerContract,
    BusinessContext,
    CitationRef,
    ColumnDef,
    EvidenceCard,
    LanguagePolicy,
    OutputSection,
    ResolvedSourcePolicy,
    RetrievedChunk,
    SourcePolicy,
    StrategyNode,
    StrategyRunState,
    TableSchema,
)
from backend.agent.strategy.nodes import synthesize as synth_mod
from backend.agent.strategy.nodes.synthesize import SynthesizeExecutor
from backend.agent.strategy.prompts import templates as prompt_templates


def _card(source_id: str = "src-1") -> EvidenceCard:
    return EvidenceCard(
        card_type="fact",
        topic="Risk",
        source_id=source_id,
        document_title="Risk Memo",
        source_excerpt="Risk excerpt with a concrete cited fact.",
        factual_basis="Memo",
        confidence=0.8,
    )


def _chunk(source_id: str = "src-1", content: str = "Risk item. Detailed body.") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"chunk-{source_id}",
        document_id=f"doc-{source_id}",
        document_title="Risk Memo",
        source_id=source_id,
        content=content,
        score=0.7,
        search_type="hybrid",
        span_start=1,
        span_end=5,
    )


def test_business_context_resolver_fallback_and_policy_edges(tmp_path: Path) -> None:
    """Exercise missing YAML dirs, ambiguous detection, and contract errors."""
    resolver = BusinessContextResolver("tenant-x", config_path=str(tmp_path))
    resolver._ensure_loaded()
    resolver._ensure_loaded()
    assert resolver._tenant_policy == {}
    assert resolver._capabilities == []
    assert resolver._answer_contracts == {}

    resolver._tenant_policy = {
        "default_capability": "fallback_cap",
        "source_policy": {
            "allow_cross_profile": True,
            "allow_cross_matter": True,
            "allow_web": True,
            "allow_personal": True,
            "allow_cloud_private": True,
            "excluded_sources": ["tenant-secret"],
            "primary_sources": ["documents"],
        },
    }
    resolver._capabilities = [
        {
            "capability_id": "medium_cap",
            "detection_keywords": ["alpha", "beta", "gamma", "delta"],
            "detection_patterns": ["["],
            "default_answer_contract_id": "missing-contract",
        },
        {
            "capability_id": "contractless",
            "detection_keywords": ["contractless"],
        },
    ]

    capability_id, confidence = resolver._detect_capability("alpha beta")
    assert capability_id == "medium_cap"
    assert 0.6 <= confidence < 0.85

    low = resolver._build_ambiguity("fallback_cap", 0.2)
    assert low.ambiguity_detected is True
    assert low.clarification_needed is True

    restricted = resolver._resolve_source_policy(
        profile_key="p1",
        matter_id="m1",
        accessible_profiles=["p2"],
        strategy_policy=SourcePolicy(
            allow_cross_profile=False,
            allow_cross_matter=False,
            allow_web=False,
            allow_personal=False,
            allow_cloud_private=False,
            require_source_spans=True,
            exclude_boilerplate=True,
            excluded_sources=["strategy-secret"],
        ),
    )
    assert restricted.allow_web is False
    assert restricted.allow_personal is False
    assert restricted.require_source_spans is True
    assert restricted.allowed_profile_ids == ["p1"]
    assert restricted.allowed_matter_ids == ["m1"]
    assert set(restricted.excluded_sources) == {"tenant-secret", "strategy-secret"}
    assert restricted.resolved_from == ["tenant", "strategy"]

    assert resolver._resolve_answer_contract("unknown") is None
    assert resolver._resolve_answer_contract("contractless") is None
    assert resolver._resolve_answer_contract("medium_cap") is None

    resolver._answer_contracts["missing-contract"] = {
        "format_id": "missing-contract",
        "table_schema": {
            "columns": [{"name": "bad", "column_type": "invalid-type"}],
        },
    }
    assert resolver._resolve_answer_contract("medium_cap") is None

    resolver._tenant_policy = None
    assert resolver._get_language_policy() is None
    resolver._tenant_policy = {}
    assert resolver._get_language_policy() is None
    resolver._tenant_policy = {"language_policy": {"primary_languages": "de"}}
    assert resolver._get_language_policy() is None


def test_prompt_template_variant_edges() -> None:
    """Cover table/prose contract instructions and empty prompt inputs."""
    table_contract = {
        "tone": "formal",
        "table_schema": {
            "columns": [
                {"name": "topic", "display_name": "Topic"},
                {"name": "source"},
            ],
            "max_rows": 3,
        },
    }
    assert "Markdown table" in prompt_templates._build_format_instruction(table_contract)
    assert "Prose response" in prompt_templates._build_format_instruction({"tone": "plain"})

    evidence_prompt = prompt_templates.build_evidence_extraction_prompt([], "query", language="it")
    assert "(No chunks provided)" in evidence_prompt[1]["content"]
    assert "Respond in it." in evidence_prompt[1]["content"]

    plan_prompt = prompt_templates.build_plan_prompt(
        "query",
        chunks_summary="summary",
        answer_contract={
            "format_id": "brief",
            "output_sections": [{"title": "Findings"}],
        },
    )
    assert "Required sections: Findings" in plan_prompt[1]["content"]

    refinement_prompt = prompt_templates.build_refinement_prompt(
        "answer",
        [],
        answer_contract={"tone": "careful", "output_sections": [{"title": "Risks"}]},
    )
    assert "(No issues identified)" in refinement_prompt[1]["content"]
    assert "Expected sections: Risks" in refinement_prompt[1]["content"]

    judge_prompt = prompt_templates.build_judge_prompt(
        "query",
        "answer",
        chunks_summary="source summary",
        dimensions=["not-a-real-dimension"],
    )
    assert "Source Material Summary" in judge_prompt[1]["content"]
    assert "groundedness" in judge_prompt[1]["content"]

    rerank_prompt = prompt_templates.build_rerank_prompt("query", [])
    assert "(No chunks provided)" in rerank_prompt[1]["content"]

    intent_prompt = prompt_templates.build_intent_classify_prompt("query")
    assert "legal_question" in intent_prompt[1]["content"]


@pytest.mark.asyncio
async def test_synthesize_helper_format_budget_and_llm_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive synthesis helper branches not reached by normal DAG tests."""
    card = _card()
    chunk = _chunk(content="Risks include alpha. This sentence has details.")

    assert synth_mod._avg_confidence([], []) == 0.0
    german = synth_mod._build_prose([], [chunk], "Risiken?", "de")
    assert "Basierend" in german
    assert "Risks include alpha" in german
    assert "Zusammengefasst" in german

    sections = [
        OutputSection(section_id="risk", title="Risks", format="list"),
        OutputSection(section_id="empty", title="Empty", format="list"),
        OutputSection(section_id="prose", title="Other", format="prose"),
    ]
    assert "- Risks include alpha" in synth_mod._build_sectioned([], [chunk], sections[:1], "q", "en")
    assert "_No evidence available" in synth_mod._build_sectioned([], [], sections[1:2], "q", "en")
    assert "Based on the available sources" in synth_mod._build_sectioned([card], [], sections[2:], "q", "en")

    contract = AnswerContract(
        format_id="sections",
        output_sections=[OutputSection(section_id="summary", title="Summary")],
        table_schema=TableSchema(columns=[ColumnDef(name="topic")], max_rows=1),
    )
    state = StrategyRunState(query="original", normalized_query="normalized")
    state.metadata["answer_contract"] = AnswerContract(format_id="meta").model_dump()
    state.metadata["strategy_budgets"] = {"max_context_tokens": 321}
    state.metadata["conversation_history"] = [
        "skip raw",
        {"role": "user", "content": "prior question"},
        {"role": "assistant", "content": ""},
    ]
    state.business_context = BusinessContext(
        capability_id="qa",
        language_policy=LanguagePolicy(primary_languages=["de"]),
        resolved_source_policy=ResolvedSourcePolicy(
            allow_web=False,
            allow_personal=False,
            allow_cross_matter=False,
            primary_sources=["documents"],
            secondary_sources=["knowledge_base"],
        ),
    )
    executor = SynthesizeExecutor()
    accessor = executor.get_state_accessor(state)

    assert executor._resolve_answer_contract(StrategyNode(node_id="n", node_type="synthesize"), accessor).format_id == "meta"
    state.business_context = SimpleNamespace(language="fr")
    assert executor._resolve_language(accessor) == "fr"
    assert executor._determine_format(AnswerContract(format_id="s", output_sections=sections[:1]), None) == "sectioned"
    assert executor._resolve_context_budget(StrategyNode(node_id="n", node_type="synthesize"), accessor) == 321
    assert "Span 1" in executor._format_raw_span(0, chunk)
    assert executor._format_source_policy(SynthesizeExecutor().get_state_accessor(StrategyRunState(query="q"))) is None
    assert executor._collect_conversation_snippets(accessor) == ["[user] prior question"]

    state.business_context = BusinessContext(
        capability_id="qa",
        resolved_source_policy=ResolvedSourcePolicy(primary_sources=["documents"]),
    )
    trace_events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        synth_mod,
        "emit_trace_event",
        lambda _state, name, payload: trace_events.append((name, payload)),
    )
    included_cards, included_chunks = executor._apply_context_budget(
        node=StrategyNode(node_id="n", node_type="synthesize"),
        accessor=executor.get_state_accessor(state),
        evidence_cards=[],
        chunks=[chunk],
        query="q",
        answer_contract=contract,
    )
    assert included_cards == []
    assert included_chunks == [chunk]
    assert trace_events and trace_events[0][0] == "context_budget_applied"

    assert executor._extract_citations_from_response("anything", []) == []
    fallback_citations = executor._extract_citations_from_response("no explicit refs", [card, _card("src-2")])
    assert [c.source_id for c in fallback_citations] == ["src-1", "src-2"]

    class EmptyLLM:
        async def complete(self, *_args, **_kwargs):
            return "   ", 5

    empty_result = await SynthesizeExecutor(EmptyLLM())._synthesize_with_llm(
        StrategyNode(node_id="n", node_type="synthesize"),
        executor.get_state_accessor(StrategyRunState(query="q")),
        [card],
        [],
        "q",
        None,
        "en",
    )
    assert empty_result is None

    long_output = executor._synthesize_template(
        StrategyNode(node_id="n", node_type="synthesize", config={"max_output_tokens": 5}),
        [card],
        [],
        "q",
        None,
        "en",
    )
    assert "Output truncated" in long_output.output_data["text"]
    assert long_output.output_data["token_count"] == 5
