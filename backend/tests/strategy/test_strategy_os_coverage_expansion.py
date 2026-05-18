"""Coverage-oriented Strategy OS contract tests.

These tests exercise the newest Strategy OS public surfaces with lightweight
fakes: graph nodes, spec lifecycle endpoints, scheduler endpoints, strategy
management endpoints, and telemetry viewer endpoints.  The emphasis is on
real observable behavior rather than private implementation details.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from backend.agent.strategies.base import StrategyConfig, StrategyDomain, StrategyMetadata
from backend.agent.strategy.models import (
    AnswerContract,
    BusinessContext,
    BusinessContextResult,
    CitationRef,
    ColumnDef,
    EvidenceCard,
    LanguagePolicy,
    NodeOutput,
    OutputSection,
    ResolvedSourcePolicy,
    RetrievedChunk,
    StrategyBudgets,
    StrategyEdge,
    StrategyGraph,
    StrategyNode,
    StrategyRunResult,
    StrategyRunState,
    StrategySpec,
    SynthesisResult,
    TableSchema,
    ValidationIssue,
    ValidationResult,
)
from backend.agent.strategy import evolutionary_mutator as evo_mutator_module
from backend.agent.strategy import llm_helper as llm_helper_module
from backend.agent.strategy.evolution_state_store import (
    EvolutionGenerationState,
    InMemoryEvolutionStateStore,
    MongoEvolutionStateStore,
    _clone_state,
    _doc_to_state,
)
from backend.agent.strategy.evolutionary_mutator import (
    EvolutionaryMutator,
    _append_after_terminal,
    _deep_dump,
    _find_node_by_type,
    _find_node_index,
    _hydrate,
    _replace_inbound_edges,
    _retire_edges_through,
    _validate,
    add_evidence_cards_node,
    add_refinement_node,
    add_reranker_node,
    add_validation_node,
    alter_query_expansion_count,
    change_contract_strictness,
    range_inclusive,
    reduce_context_budget,
    remove_evidence_cards_node,
    remove_reranker_node,
    switch_synthesis_model,
)
from backend.agent.strategy.experiment_job_models import (
    JobProgress,
    JobStatus,
    StrategyExperimentJob,
)
from backend.agent.strategy.experiment_job_store import InMemoryExperimentJobStore
from backend.agent.strategy.experiment_runner import (
    ExperimentResult,
    InvalidJobStateError,
    StrategyExperimentRunner,
    _coerce_resource_limits,
    _extract_generation,
    _percentile,
    _resolve_synth_model,
)
from backend.agent.strategy.llm_helper import NodeLLMHelper
from backend.agent.strategy.nodes.business_context_node import BusinessContextNodeExecutor
from backend.agent.strategy.nodes.compare_candidates import (
    CompareCandidatesExecutor,
    _extract_synthesis_from_output,
    _score_candidate,
)
from backend.agent.strategy.nodes.emit_telemetry import (
    EmitTelemetryExecutor,
    _collect_metrics,
)
from backend.agent.strategy.nodes.evidence_cards import EvidenceCardExecutor
from backend.agent.strategy.nodes.intent_classify import IntentClassifyExecutor
from backend.agent.strategy.nodes.judge_quality import JudgeQualityExecutor
from backend.agent.strategy.nodes.legacy_orchestrator_pipeline import (
    LegacyOrchestratorPipelineExecutor,
)
from backend.agent.strategy.nodes.plan_node import PlanExecutor
from backend.agent.strategy.nodes.query_expand import QueryExpandExecutor
from backend.agent.strategy.nodes.refine import RefineExecutor
from backend.agent.strategy.nodes.rerank import RerankExecutor
from backend.agent.strategy.nodes.retrieve import RetrieveExecutor
from backend.agent.strategy.nodes.synthesize import SynthesizeExecutor
from backend.agent.strategy.nodes.validate_citations import ValidateCitationsExecutor
from backend.agent.strategy.nodes.validate_contract import ValidateContractExecutor
from backend.agent.strategy.promotion_manager import PromotionManager
from backend.agent.strategy.scheduler_models import ResourceLimits
from backend.agent.strategy.spec_store import InMemorySpecStore
from backend.agent.strategy.strategy_runner import StrategyRunner, StateViolationError
from backend.evaluation.models import DimensionId
from backend.routers import scheduler as scheduler_router
from backend.routers import strategies as strategies_router
from backend.routers import strategy_specs as spec_router
from backend.routers import telemetry as telemetry_router
from backend.routers.auth import UserResponse
from backend.scheduler.models import NightlyReport, StrategySchedule
from backend.tests.strategy._fakes import _FakeAsyncDB


def _node(node_id: str, node_type: str, **config: Any) -> StrategyNode:
    return StrategyNode(node_id=node_id, node_type=node_type, config=config)


def _chunk(
    chunk_id: str = "c1",
    *,
    document_id: str = "doc-1",
    score: float = 0.8,
    content: str = "Alpha facts support the answer with enough context.",
    metadata: dict[str, Any] | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        document_title=f"Document {document_id}",
        source_id=f"source-{document_id}",
        content=content,
        score=score,
        search_type="hybrid",
        metadata=metadata or {},
    )


def _card(
    source_id: str = "source-doc-1",
    *,
    topic: str = "Alpha",
    confidence: float = 0.9,
) -> EvidenceCard:
    return EvidenceCard(
        card_type="fact",
        topic=topic,
        source_id=source_id,
        document_title="Alpha Memo",
        source_excerpt="Alpha source excerpt with a concrete supporting fact.",
        factual_basis="Extracted from the memo.",
        confidence=confidence,
    )


def _synthesis(text: str = "Alpha answer cites the source.") -> SynthesisResult:
    return SynthesisResult(
        text=text,
        citations=[
            CitationRef(
                source_id="source-doc-1",
                document_title="Alpha Memo",
                quote="Alpha source excerpt",
            )
        ],
        language="en",
        token_count=len(text.split()),
        model_used="test-model",
        confidence=0.8,
    )


def _spec_payload(
    strategy_id: str = "router-spec",
    *,
    version: str = "1.0.0",
    status: str = "draft",
    capability_id: str = "qa",
) -> dict[str, Any]:
    spec = StrategySpec(
        strategy_id=strategy_id,
        version=version,
        status=status,  # type: ignore[arg-type]
        capability_id=capability_id,
        tenant_scope="default",
        graph=StrategyGraph(
            nodes=[StrategyNode(node_id="retrieve", node_type="retrieve")],
            edges=[],
            entry_node="retrieve",
            terminal_nodes=["retrieve"],
        ),
    )
    return json.loads(spec.model_dump_json())


def _admin_user() -> UserResponse:
    return UserResponse(
        id="admin",
        email="admin@example.com",
        name="Admin",
        is_active=True,
        is_admin=True,
        created_at="2026-01-01T00:00:00",
    )


@pytest.mark.asyncio
async def test_context_compare_retrieve_contract_and_telemetry_nodes(caplog: pytest.LogCaptureFixture) -> None:
    """Exercise low-level node contracts without external services."""

    class Resolver:
        async def resolve(self, **kwargs: Any) -> BusinessContext:
            assert kwargs["query"] == "normalized alpha"
            return BusinessContext(
                capability_id="qa",
                capability_confidence=0.91,
                profile_key=kwargs["profile_key"],
                tenant_id="recallhub",
            )

    empty_context = await BusinessContextNodeExecutor().execute(
        _node("ctx", "business_context"),
        StrategyRunState(query=""),
    )
    assert empty_context.status == "empty"

    context_output = await BusinessContextNodeExecutor(Resolver()).execute(
        _node(
            "ctx",
            "business_context",
            profile_key="legal",
            matter_id="m1",
            accessible_profiles=["legal"],
        ),
        StrategyRunState(query="alpha", metadata={"normalized_query": "normalized alpha"}),
    )
    assert context_output.status == "success"
    assert context_output.output_data["capability_id"] == "qa"

    class FailingResolver:
        async def resolve(self, **_: Any) -> BusinessContext:
            raise RuntimeError("resolver down")

    failed_context = await BusinessContextNodeExecutor(FailingResolver()).execute(
        _node("ctx", "business_context"),
        StrategyRunState(query="alpha"),
    )
    assert failed_context.status == "error"
    assert "resolver down" in failed_context.error_message

    syn_a = _synthesis("Longer candidate has lower confidence.")
    syn_a.confidence = 0.6
    syn_a.token_count = 40
    syn_b = _synthesis("Short wins.")
    syn_b.confidence = 0.9
    syn_b.token_count = 4
    assert _extract_synthesis_from_output(None) is None
    assert _extract_synthesis_from_output({"not": "a synthesis"}) is None
    assert _score_candidate(syn_b, "highest_confidence") > _score_candidate(syn_a, "highest_confidence")
    assert _score_candidate(syn_b, "shortest") > _score_candidate(syn_a, "shortest")

    compare_state = StrategyRunState(
        query="alpha",
        node_outputs={
            "candidate_a": NodeOutput(
                node_id="candidate_a",
                node_type="synthesize",
                output_data=syn_a.model_dump(),
            ),
            "candidate_b": NodeOutput(
                node_id="candidate_b",
                node_type="synthesize",
                output_data=syn_b.model_dump(),
            ),
            "bad": NodeOutput(node_id="bad", node_type="synthesize", status="error"),
        },
    )
    compared = await CompareCandidatesExecutor().execute(
        _node(
            "compare",
            "compare_candidates",
            candidate_node_ids=["bad", "candidate_a", "candidate_b"],
            selection_strategy="balanced",
        ),
        compare_state,
    )
    assert compared.status == "success"
    assert compared.output_data["text"] == "Short wins."

    fallback_compare = await CompareCandidatesExecutor().execute(
        _node("compare", "compare_candidates"),
        StrategyRunState(query="alpha", synthesis_result=syn_a),
    )
    assert fallback_compare.output_data["text"] == syn_a.text

    empty_compare = await CompareCandidatesExecutor().execute(
        _node("compare", "compare_candidates"),
        StrategyRunState(query="alpha"),
    )
    assert empty_compare.status == "empty"

    missing_search = await RetrieveExecutor().execute(
        _node("retrieve", "retrieve"),
        StrategyRunState(query="alpha"),
    )
    assert missing_search.status == "error"

    no_query = await RetrieveExecutor(SimpleNamespace()).execute(
        _node("retrieve", "retrieve"),
        StrategyRunState(query=""),
    )
    assert no_query.status == "empty"

    class Omitted:
        def model_dump(self) -> dict[str, str]:
            return {"source_id": "blocked", "reason": "forbidden_source"}

    class OpaqueResult:
        chunk_id = "opaque"
        document_id = "doc-opaque"
        document_title = "Opaque"
        source_id = "source-opaque"
        content = "Opaque object content"
        score = 0.71
        search_type = "hybrid"
        metadata = {"kind": "object"}

    class FederatedSearch:
        def __init__(self) -> None:
            self.requests = []

        async def search_with_policy(self, request: Any) -> tuple[list[Any], list[Any]]:
            self.requests.append(request)
            return (
                [
                    _chunk("dup").model_dump(),
                    _chunk("dup", score=0.2).model_dump(),
                    _chunk("model"),
                    OpaqueResult(),
                ],
                [Omitted(), {"source_id": "omitted"}],
            )

    policy = ResolvedSourcePolicy(
        allow_web=True,
        primary_sources=["documents"],
        resolved_from=["tenant", "strategy"],
    )
    retrieve_state = StrategyRunState.model_construct(
        query="alpha",
        metadata={"query_expand": ["alpha variant", "alpha"]},
        business_context=BusinessContext(
            capability_id="qa",
            profile_key="legal",
            resolved_source_policy=policy,
        ),
    )
    fake_search = FederatedSearch()
    retrieved = await RetrieveExecutor(fake_search).execute(
        _node("retrieve", "retrieve", top_k=2, min_score=0.1),
        retrieve_state,
    )
    assert retrieved.status == "success"
    assert len(retrieved.output_data) == 3
    assert [request.query for request in fake_search.requests] == ["alpha", "alpha variant"]
    assert fake_search.requests[0].profile_key == "legal"

    empty_retrieved = await RetrieveExecutor(
        SimpleNamespace(search_with_policy=AsyncMock(return_value=([], [])))
    ).execute(_node("retrieve", "retrieve"), StrategyRunState(query="alpha"))
    assert empty_retrieved.status == "empty"

    contract = AnswerContract(
        format_id="brief-table",
        output_sections=[
            OutputSection(section_id="summary", title="Summary", required=True),
            OutputSection(section_id="optional", title="Appendix", required=False),
        ],
        table_schema=TableSchema(columns=[ColumnDef(name="topic")]),
        tone="formal",
        max_length_tokens=12,
    )
    contract_state = StrategyRunState.model_construct(
        query="alpha",
        synthesis_result=_synthesis("Summary\n| topic | value |\n| --- | --- |\n| Alpha | one |\nTherefore concise."),
        business_context=BusinessContext(answer_contract=contract),
    )
    contract_ok = await ValidateContractExecutor().execute(
        _node("contract", "validate_contract"),
        contract_state,
    )
    assert contract_ok.output_data["passed"] is True

    contract_bad = await ValidateContractExecutor().execute(
        _node(
            "contract",
            "validate_contract",
            answer_contract=contract.model_dump(),
        ),
        StrategyRunState(
            query="alpha",
            synthesis_result=_synthesis("lol yeah this omits structure and is far too long for the configured answer contract"),
        ),
    )
    assert contract_bad.output_data["passed"] is False
    assert {
        issue["issue_type"] for issue in contract_bad.output_data["issues"]
    } >= {"missing_section", "missing_table", "tone_mismatch", "max_length_exceeded"}

    no_contract = await ValidateContractExecutor().execute(
        _node("contract", "validate_contract"),
        StrategyRunState(query="alpha", synthesis_result=_synthesis()),
    )
    assert no_contract.output_data["metadata"]["reason"] == "no_contract_defined"

    no_synthesis = await ValidateContractExecutor().execute(
        _node("contract", "validate_contract"),
        StrategyRunState(query="alpha"),
    )
    assert no_synthesis.status == "empty"

    metrics_state = StrategyRunState(
        query="alpha",
        retrieved_chunks=[_chunk()],
        evidence_cards=[_card()],
        synthesis_result=_synthesis(),
        validation_results=[],
        node_outputs={
            "a": NodeOutput(node_id="a", node_type="retrieve", duration_ms=2.25, tokens_used=3),
            "b": NodeOutput(node_id="b", node_type="synthesize", duration_ms=4.25, tokens_used=7),
        },
    )
    metrics = _collect_metrics(metrics_state)
    assert metrics["total_tokens"] == 10
    assert metrics["total_duration_ms"] == 6.5
    with caplog.at_level("DEBUG"):
        emitted = await EmitTelemetryExecutor().execute(
            _node("telemetry", "emit_telemetry"),
            metrics_state,
        )
    assert emitted.status == "success"
    assert emitted.output_data["nodes_executed"] == 2

    empty_emit = await EmitTelemetryExecutor().execute(
        _node("telemetry", "emit_telemetry"),
        StrategyRunState(query="alpha"),
    )
    assert empty_emit.status == "empty"


@pytest.mark.asyncio
async def test_llm_fallback_nodes_rerank_synthesize_judge_and_legacy_pipeline() -> None:
    """Cover LLM success, LLM fallback, and deterministic node paths."""

    class JsonHelper:
        def __init__(self, payload: Any, tokens: int = 11, fail: bool = False) -> None:
            self.payload = payload
            self.tokens = tokens
            self.fail = fail

        async def complete_json(self, *_: Any, **__: Any) -> tuple[Any, int]:
            if self.fail:
                raise RuntimeError("llm json failed")
            return self.payload, self.tokens

    intent = await IntentClassifyExecutor(JsonHelper({"intent": "custom", "confidence": "1.8", "domain": "x"})).execute(
        _node("intent", "intent_classify"),
        StrategyRunState(query="Compare legal clauses"),
    )
    assert intent.output_data == {"intent": "custom", "confidence": 1.0, "domain": "x"}

    intent_fallback = await IntentClassifyExecutor(JsonHelper([], fail=True)).execute(
        _node("intent", "intent_classify"),
        StrategyRunState(query="Please compare clause A versus clause B"),
    )
    assert intent_fallback.output_data["intent"] == "comparison"

    intent_empty = await IntentClassifyExecutor().execute(
        _node("intent", "intent_classify"),
        StrategyRunState(query=""),
    )
    assert intent_empty.status == "empty"

    plan = await PlanExecutor(JsonHelper({"plan_text": "Do it", "steps": ["one", "two", "three"]})).execute(
        _node("plan", "plan", max_steps=2),
        StrategyRunState(query="alpha", retrieved_chunks=[_chunk()]),
    )
    assert plan.output_data["steps"] == ["one", "two"]

    plan_fallback = await PlanExecutor(JsonHelper({"steps": []}, fail=True)).execute(
        _node("plan", "plan", plan_style="detailed", format="table", max_steps=4),
        StrategyRunState(query="alpha"),
    )
    assert "Format output" in plan_fallback.output_data["steps"][-1]

    query_expand = await QueryExpandExecutor(JsonHelper(["variant", "What is alpha?"])).execute(
        _node("expand", "query_expand", max_variants=3),
        StrategyRunState(query="What is alpha?"),
    )
    assert query_expand.output_data[0] == "What is alpha?"

    query_expand_fallback = await QueryExpandExecutor(JsonHelper({}, fail=True)).execute(
        _node("expand", "query_expand", max_variants=4),
        StrategyRunState(query="how does alpha work?"),
    )
    assert "explain alpha?" in query_expand_fallback.output_data

    empty_expand = await QueryExpandExecutor().execute(
        _node("expand", "query_expand"),
        StrategyRunState(query=""),
    )
    assert empty_expand.status == "empty"

    rerank_state = StrategyRunState(query="alpha", retrieved_chunks=[_chunk("a", score=0.1), _chunk("b", score=0.2)])
    reranked = await RerankExecutor(JsonHelper([
        {"chunk_index": 0, "relevance_score": 0.95},
        {"chunk_index": "bad", "relevance_score": "bad"},
        {"chunk_index": 1, "relevance_score": 0.4},
    ])).execute(_node("rerank", "rerank", min_score=0.5), rerank_state)
    assert reranked.status == "success"
    assert reranked.output_data[0]["chunk_id"] == "a"
    assert reranked.tokens_used == 11

    rerank_empty_after_filter = await RerankExecutor(JsonHelper([])).execute(
        _node("rerank", "rerank", min_score=0.99),
        StrategyRunState(query="alpha", retrieved_chunks=[_chunk("a", score=0.1)]),
    )
    assert rerank_empty_after_filter.status == "empty"

    recency = await RerankExecutor().execute(
        _node("rerank", "rerank", strategy="recency_boost", max_results=1),
        StrategyRunState(
            query="alpha",
            retrieved_chunks=[
                _chunk("new", score=0.5, metadata={"date": datetime.utcnow().isoformat()}),
                _chunk("old", score=0.5, metadata={"date": "not-a-date"}),
            ],
        ),
    )
    assert recency.output_data[0]["chunk_id"] == "new"

    diversity = await RerankExecutor().execute(
        _node("rerank", "rerank", strategy="diversity"),
        StrategyRunState(
            query="alpha",
            retrieved_chunks=[
                _chunk("d1", document_id="doc-x", score=0.9),
                _chunk("d2", document_id="doc-x", score=0.9),
            ],
        ),
    )
    assert diversity.output_data[0]["score"] >= diversity.output_data[1]["score"]

    class CompleteHelper:
        def __init__(self, text: str, *, fail: bool = False) -> None:
            self.text = text
            self.fail = fail

        async def complete(self, *_: Any, **__: Any) -> tuple[str, int]:
            if self.fail:
                raise RuntimeError("complete failed")
            return self.text, 17

    synthesis_contract = AnswerContract(
        format_id="sections",
        output_sections=[
            OutputSection(section_id="alpha", title="Alpha", format="list"),
            OutputSection(section_id="table", title="Table", format="table"),
        ],
    )
    synth_state = StrategyRunState.model_construct(
        query="alpha",
        evidence_cards=[_card("source-a", topic="Alpha"), _card("source-b", topic="Beta")],
        business_context=BusinessContext(
            answer_contract=synthesis_contract,
            language_policy=LanguagePolicy(primary_languages=["en"]),
            resolved_source_policy=ResolvedSourcePolicy(
                primary_sources=["documents"],
                secondary_sources=["web"],
                allow_web=True,
            ),
        ),
        metadata={"conversation_history": [{"role": "user", "content": "previous"}]},
    )
    llm_synth = await SynthesizeExecutor(CompleteHelper("Answer from [Card 2]")).execute(
        _node("synth", "synthesize", max_context_tokens=2000),
        synth_state,
    )
    assert llm_synth.output_data["citations"][0]["source_id"] == "source-b"
    assert llm_synth.model_used == "synthesizer_fast"

    template_synth = await SynthesizeExecutor(CompleteHelper("", fail=True)).execute(
        _node("synth", "synthesize", format_override="sectioned", max_output_tokens=5),
        synth_state,
    )
    assert template_synth.status == "success"
    assert template_synth.output_data["format_id"] == "sections"
    assert "Output truncated" in template_synth.output_data["text"]

    table_contract = AnswerContract(
        format_id="table",
        table_schema=TableSchema(
            columns=[ColumnDef(name="topic"), ColumnDef(name="source"), ColumnDef(name="confidence")],
            max_rows=1,
        ),
    )
    table_synth = await SynthesizeExecutor().execute(
        _node("synth", "synthesize", answer_contract=table_contract.model_dump()),
        StrategyRunState(query="alpha", retrieved_chunks=[_chunk("c1"), _chunk("c2")]),
    )
    assert "| topic | source | confidence |" in table_synth.output_data["text"]

    no_evidence_synth = await SynthesizeExecutor().execute(
        _node("synth", "synthesize"),
        StrategyRunState(query="alpha"),
    )
    assert no_evidence_synth.status == "empty"

    class RoleConfig:
        provider = "ollama"
        model = "judge-model"
        timeout_ms = 1000

    class Registry:
        def resolve_with_fallback(self, role: str) -> RoleConfig:
            assert role == "judge"
            return RoleConfig()

    judge_payload = {
        "dimensions": {
            dim.value: {"score": 0.4 if dim is DimensionId.CONCISENESS else 0.8, "evidence": dim.value}
            for dim in DimensionId
        }
    }

    class JudgeClient:
        async def generate(self, **_: Any) -> str:
            return "```json\n" + json.dumps(judge_payload) + "\n```"

    judged = await JudgeQualityExecutor(Registry(), JudgeClient()).execute(
        _node("judge", "judge_quality"),
        StrategyRunState(query="alpha", retrieved_chunks=[_chunk()], synthesis_result=_synthesis()),
    )
    assert judged.output_data["validator_id"] == "quality_judge_llm"
    assert judged.output_data["metadata"]["determinism_runs"] == 3
    assert judged.output_data["issues"][0]["issue_type"] == "low_conciseness"

    weak_rule_judge = await JudgeQualityExecutor().execute(
        _node("judge", "judge_quality", expected_language="de", weights={}),
        StrategyRunState(query="alpha beta gamma", synthesis_result=_synthesis("Bad...")),
    )
    assert weak_rule_judge.output_data["passed"] is False

    empty_judge = await JudgeQualityExecutor().execute(
        _node("judge", "judge_quality"),
        StrategyRunState(query="alpha"),
    )
    assert empty_judge.status == "empty"

    class Agent:
        async def process(self, **kwargs: Any) -> tuple[str, Any]:
            assert kwargs["active_profile_key"] == "legal"
            trace = SimpleNamespace(
                orchestrator_model="orch",
                search_operations=[
                    {
                        "chunks_returned": [
                            {
                                "chunk_id": "legacy",
                                "document_id": "doc-l",
                                "document_title": "Legacy",
                                "source_id": "source-l",
                                "content": "Legacy content",
                                "score": 0.7,
                                "search_type": "hybrid",
                            }
                        ]
                    },
                    SimpleNamespace(chunks_returned=[]),
                ],
                llm_calls=[{"total_tokens": 10}, SimpleNamespace(total_tokens=4)],
                phase_metrics=[{"phase": "done"}, SimpleNamespace(phase="other")],
            )
            return "Legacy response", trace

    legacy = await LegacyOrchestratorPipelineExecutor(Agent()).execute(
        _node("legacy", "legacy_orchestrator_pipeline", active_profile_key="legal"),
        StrategyRunState(query="alpha"),
    )
    assert legacy.status == "success"
    assert legacy.output_data["metrics"]["total_tokens"] == 14
    assert legacy.output_data["retrieved_chunks"][0]["chunk_id"] == "legacy"

    legacy_missing = await LegacyOrchestratorPipelineExecutor().execute(
        _node("legacy", "legacy_orchestrator_pipeline"),
        StrategyRunState(query="alpha"),
    )
    assert legacy_missing.status == "error"

    class FailingAgent:
        async def process(self, **_: Any) -> tuple[str, Any]:
            raise RuntimeError("agent down")

    legacy_error = await LegacyOrchestratorPipelineExecutor(FailingAgent()).execute(
        _node("legacy", "legacy_orchestrator_pipeline"),
        StrategyRunState(query="alpha"),
    )
    assert legacy_error.status == "error"
    assert "agent down" in legacy_error.error_message


@pytest.mark.asyncio
async def test_strategy_specs_router_crud_and_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover StrategySpec CRUD, validation, and promotion endpoint adapters."""
    store = InMemorySpecStore({})
    payload = _spec_payload()

    created = await spec_router.create_spec(payload, store=store)
    assert created["strategy_id"] == "router-spec"

    with pytest.raises(HTTPException) as conflict:
        await spec_router.create_spec(payload, store=store)
    assert conflict.value.status_code == 409

    listed = await spec_router.list_specs(status="draft", capability_id="qa", tenant_id="default", limit=100, store=store)
    assert listed.total == 1

    fetched = await spec_router.get_spec("router-spec", version=None, store=store)
    assert fetched["spec"]["strategy_id"] == "router-spec"

    with pytest.raises(HTTPException) as missing_version:
        await spec_router.get_spec("router-spec", version="9.9.9", store=store)
    assert missing_version.value.status_code == 404

    mismatch_payload = _spec_payload("other-spec", version="1.1.0")
    with pytest.raises(HTTPException) as mismatch:
        await spec_router.update_spec("router-spec", mismatch_payload, x_expected_version=1, store=store)
    assert mismatch.value.status_code == 400

    with pytest.raises(HTTPException) as version_conflict:
        await spec_router.update_spec("router-spec", payload, x_expected_version=42, store=store)
    assert version_conflict.value.status_code == 409

    updated_payload = _spec_payload("router-spec", version="1.1.0")
    updated = await spec_router.update_spec("router-spec", updated_payload, x_expected_version=1, store=store)
    assert updated["version"] == "1.1.0"

    validation = await spec_router.validate_stored_spec("router-spec", store=store)
    assert validation.valid is True

    history = await spec_router.get_spec_history("router-spec", store=store)
    assert [version.version for version in history.versions] == ["1.0.0", "1.1.0"]

    archived = await spec_router.archive_spec("router-spec", store=store)
    assert archived.status == "archived"

    with pytest.raises(HTTPException) as not_found:
        await spec_router.archive_spec("missing", store=store)
    assert not_found.value.status_code == 404

    async def failing_flush(_: str, __: str) -> bool:
        raise RuntimeError("flush failed")

    lifecycle_store = InMemorySpecStore({})
    v1 = spec_router._make_snapshot(StrategySpec(**_spec_payload("life", version="1.0.0")), 1)
    v2 = spec_router._make_snapshot(StrategySpec(**_spec_payload("life", version="2.0.0")), 2)
    await lifecycle_store.upsert(v1, expected_version=0)
    await lifecycle_store.upsert(v2, expected_version=1)
    manager = PromotionManager(store=lifecycle_store.snapshots, cooldown_days=0)
    monkeypatch.setattr(lifecycle_store, "flush_snapshot", failing_flush)
    monkeypatch.setattr(spec_router, "_invalidate_selector_cache", lambda spec: None)

    promoted = await spec_router.promote_spec(
        "life",
        "2.0.0",
        spec_router.PromotionRequest(actor="tester", reason="promote"),
        manager=manager,
        store=lifecycle_store,
    )
    assert promoted["status"] == "active"

    deprecated = await spec_router.deprecate_spec(
        "life",
        "2.0.0",
        spec_router.PromotionRequest(actor="tester", reason="deprecate"),
        manager=manager,
        store=lifecycle_store,
    )
    assert deprecated["status"] == "deprecated"

    archived_version = await spec_router.archive_spec_version(
        "life",
        "2.0.0",
        spec_router.PromotionRequest(actor="tester", reason="archive"),
        manager=manager,
        store=lifecycle_store,
    )
    assert archived_version["status"] == "archived"

    rolled_back = await spec_router.rollback_spec(
        "life",
        spec_router.RollbackRequest(target_version="2.0.0", actor="tester", reason="rollback"),
        manager=manager,
        store=lifecycle_store,
    )
    assert rolled_back["status"] == "active"

    with pytest.raises(HTTPException) as lifecycle_missing:
        await spec_router.promote_spec(
            "missing",
            "1.0.0",
            spec_router.PromotionRequest(actor="tester", reason="promote"),
            manager=manager,
            store=lifecycle_store,
        )
    assert lifecycle_missing.value.status_code == 404


@pytest.mark.asyncio
async def test_scheduler_router_endpoints() -> None:
    """Cover scheduler endpoint adapters and error translations."""
    store = scheduler_router.InMemorySchedulerStore()

    empty = await scheduler_router.list_schedules(store=store)
    assert empty.schedules == []

    created = await scheduler_router.create_or_update_schedule(
        scheduler_router.CreateScheduleRequest(
            name="Nightly",
            cron="0 2 * * *",
            dataset_id="dataset",
            candidate_strategy_ids=["a", "b"],
        ),
        store=store,
    )
    assert created.name == "Nightly"

    fetched = await scheduler_router.get_schedule(created.id, store=store)
    assert fetched.id == created.id

    with pytest.raises(HTTPException) as missing_schedule:
        await scheduler_router.get_schedule("missing", store=store)
    assert missing_schedule.value.status_code == 404

    disabled = await scheduler_router.disable_schedule(created.id, store=store)
    assert disabled == {"status": "disabled"}

    with pytest.raises(HTTPException) as missing_disable:
        await scheduler_router.disable_schedule("missing", store=store)
    assert missing_disable.value.status_code == 404

    class Daemon:
        _running = True
        _current_run = None
        _shutdown_requested = False

        async def run_now(self, schedule_id: str) -> str:
            if schedule_id == "missing":
                raise scheduler_router.ScheduleNotFoundError(schedule_id)
            if schedule_id == "paused":
                raise scheduler_router.SchedulePausedError(schedule_id)
            if schedule_id == "open":
                raise scheduler_router.CircuitBreakerOpenError(schedule_id)
            if schedule_id == "boom":
                raise RuntimeError("boom")
            return "trace-123"

    daemon = Daemon()
    run_now = await scheduler_router.run_schedule_now("ok", daemon=daemon)
    assert run_now.trace_id == "trace-123"

    for schedule_id, status_code in [("missing", 404), ("paused", 409), ("open", 503), ("boom", 500)]:
        with pytest.raises(HTTPException) as exc_info:
            await scheduler_router.run_schedule_now(schedule_id, daemon=daemon)
        assert exc_info.value.status_code == status_code

    status = await scheduler_router.get_scheduler_status(daemon=daemon)
    assert status.running is True

    paused = await scheduler_router.pause_scheduler(daemon=daemon)
    assert paused == {"status": "paused"}
    assert daemon._shutdown_requested is True

    resumed = await scheduler_router.resume_scheduler(daemon=daemon)
    assert resumed == {"status": "resumed"}
    assert daemon._shutdown_requested is False

    scheduler_router._reports[:] = [
        NightlyReport(run_id="old-run", schedule_id="old", status="completed", completed_at=datetime.utcnow() - timedelta(days=1)),
        NightlyReport(run_id="new-run", schedule_id="new", status="completed", completed_at=datetime.utcnow()),
    ]
    reports = await scheduler_router.get_reports(limit=1)
    assert reports.reports[0].schedule_id == "new"
    scheduler_router._reports.clear()


@pytest.mark.asyncio
async def test_strategies_router_with_registry_metrics_and_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover legacy strategy management endpoints and A/B scoring."""

    class FakeStrategy:
        metadata = StrategyMetadata(
            id="strategy-a",
            name="Strategy A",
            version="1.0.0",
            description="A test strategy",
            domains=[StrategyDomain.GENERAL],
            tags=["fast"],
            is_default=True,
            is_legacy=False,
            author="tester",
        )
        config = StrategyConfig(custom_params={"x": 1})

        def get_analyze_prompt(self) -> str:
            return "Analyze prompt" * 30

        def get_plan_prompt(self) -> str:
            return ""

        def get_evaluate_prompt(self) -> str:
            return "Evaluate"

        def get_synthesize_prompt(self) -> str:
            return "Synthesize"

    class Registry:
        @staticmethod
        def list_strategies(domain: Any = None) -> list[Any]:
            assert domain in (None, StrategyDomain.GENERAL)
            return [FakeStrategy.metadata]

        @staticmethod
        def get_default() -> Any:
            return FakeStrategy()

        @staticmethod
        def get(strategy_id: str) -> Any:
            if strategy_id == "missing":
                raise KeyError(strategy_id)
            if strategy_id == "boom":
                raise RuntimeError("registry boom")
            return FakeStrategy()

        @staticmethod
        def get_for_domain(domain: StrategyDomain) -> Any:
            assert domain == StrategyDomain.GENERAL
            return FakeStrategy()

        @staticmethod
        def auto_detect(query: str) -> Any:
            assert query
            return FakeStrategy()

    class Metrics:
        def get_strategy_stats(self, strategy_id: str, **_: Any) -> dict[str, Any]:
            return {
                "strategy_id": strategy_id,
                "execution_count": 3,
                "avg_latency_ms": 12.5,
                "avg_iterations": 2.0,
                "avg_confidence": 0.7,
                "quality_score": 0.8,
                "quality_distribution": {"good": 2},
            }

        def compare_strategies(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "strategy_a": kwargs["strategy_a"],
                "strategy_b": kwargs["strategy_b"],
                "filters": {"domain": kwargs["domain"]},
                "comparison": {"winner": kwargs["strategy_a"], "confidence_in_winner": "high"},
            }

        def get_all_strategy_stats(self, **_: Any) -> list[dict[str, Any]]:
            return [
                {
                    "strategy_id": "strategy-a",
                    "execution_count": 3,
                    "avg_latency_ms": 12.5,
                    "avg_iterations": 2.0,
                    "avg_confidence": 0.7,
                    "quality_score": 0.8,
                    "quality_distribution": {},
                    "avg_user_feedback": None,
                }
            ]

        async def record_user_feedback(self, **kwargs: Any) -> None:
            assert kwargs["feedback_score"] == 5

    class LLMClient:
        async def complete_json(self, **_: Any) -> dict[str, Any]:
            return {
                "scores_a": {
                    "quality": 8,
                    "hallucination": 9,
                    "readability": 8,
                    "factuality": 8,
                    "relevance": 9,
                },
                "scores_b": {
                    "quality": 6,
                    "hallucination": 7,
                    "readability": 6,
                    "factuality": 6,
                    "relevance": 6,
                },
                "analysis": "A is better",
                "recommendation": "Use A",
            }

    monkeypatch.setattr(strategies_router, "StrategyRegistry", Registry)
    monkeypatch.setattr(strategies_router, "get_strategy_metrics", lambda: Metrics())
    monkeypatch.setattr(
        strategies_router,
        "get_llm_manager",
        lambda: SimpleNamespace(get_orchestrator_client=lambda: LLMClient()),
    )

    listed = await strategies_router.list_strategies(domain="general")
    assert listed[0].id == "strategy-a"

    with pytest.raises(HTTPException) as invalid_domain:
        await strategies_router.list_strategies(domain="bad")
    assert invalid_domain.value.status_code == 400

    default = await strategies_router.get_default_strategy()
    assert default.is_default is True

    detail = await strategies_router.get_strategy("strategy-a")
    assert detail.prompts_preview["plan"] == "(empty)"
    assert detail.prompts_preview["analyze"].endswith("...")

    for bad_id, code in [("", 400), ("missing", 404), ("boom", 500)]:
        with pytest.raises(HTTPException) as exc_info:
            await strategies_router.get_strategy(bad_id)
        assert exc_info.value.status_code == code

    metrics = await strategies_router.get_strategy_metrics_endpoint("strategy-a", hours=2, domain="general")
    assert metrics.execution_count == 3

    comparison = await strategies_router.compare_strategies_endpoint(
        strategies_router.CompareRequest(strategy_a="strategy-a", strategy_b="strategy-b", hours=1, domain="general")
    )
    assert comparison.winner == "strategy-a"

    for request in [
        strategies_router.CompareRequest(strategy_a="", strategy_b="b"),
        strategies_router.CompareRequest(strategy_a="a", strategy_b=""),
        strategies_router.CompareRequest(strategy_a="a", strategy_b="a"),
        strategies_router.CompareRequest(strategy_a="a", strategy_b="b", hours=0),
    ]:
        with pytest.raises(HTTPException) as exc_info:
            await strategies_router.compare_strategies_endpoint(request)
        assert exc_info.value.status_code == 400

    domain_strategy = await strategies_router.get_strategy_for_domain("general")
    assert domain_strategy.id == "strategy-a"

    with pytest.raises(HTTPException) as bad_domain:
        await strategies_router.get_strategy_for_domain("bad")
    assert bad_domain.value.status_code == 400

    detected = await strategies_router.auto_detect_strategy(strategies_router.AutoDetectRequest(query="alpha"))
    assert detected.id == "strategy-a"

    all_metrics = await strategies_router.get_all_metrics(hours=1, domain="general")
    assert all_metrics[0].strategy_id == "strategy-a"

    feedback = await strategies_router.record_feedback(
        strategies_router.FeedbackRequest(
            strategy_id="strategy-a",
            session_id="session",
            score=5,
            text="great",
        )
    )
    assert feedback["status"] == "success"

    with pytest.raises(HTTPException) as bad_feedback:
        await strategies_router.record_feedback(
            strategies_router.FeedbackRequest(strategy_id="strategy-a", session_id="s", score=6)
        )
    assert bad_feedback.value.status_code == 400

    ab_result = await strategies_router.ab_compare_responses(
        strategies_router.ABCompareResponsesRequest(
            query="alpha",
            response_a="A response",
            response_b="B response",
            strategy_a="strategy-a",
            strategy_b="strategy-b",
            latency_a_ms=100,
            latency_b_ms=200,
        ),
        req=SimpleNamespace(),
    )
    assert ab_result.quality_winner == "strategy-a"
    assert ab_result.speed_winner == "strategy-a"

    with pytest.raises(HTTPException) as empty_ab:
        await strategies_router.ab_compare_responses(
            strategies_router.ABCompareResponsesRequest(
                query="",
                response_a="A",
                response_b="B",
                strategy_a="a",
                strategy_b="b",
                latency_a_ms=1,
                latency_b_ms=2,
            ),
            req=SimpleNamespace(),
        )
    assert empty_ab.value.status_code == 400


@pytest.mark.asyncio
async def test_telemetry_router_file_status_search_and_stats(tmp_path: Path) -> None:
    """Cover telemetry admin endpoint behavior over real temporary JSONL files."""

    today = datetime.utcnow().date().isoformat()
    yesterday = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
    storage = tmp_path / "telemetry"
    raw = storage / "raw"
    raw.mkdir(parents=True)

    records = [
        {
            "record_id": "r1",
            "session_id": "s1",
            "model": "gpt-test",
            "latency_ms": 100,
            "total_tokens": 12,
            "prompt": "Alpha prompt",
            "status": "ok",
        },
        {
            "record_id": "r2",
            "session_id": "s2",
            "model": "local-test",
            "duration_ms": 300,
            "tokens": 5,
            "query": "Beta query",
            "error": "boom",
        },
    ]
    (storage / f"{today}.jsonl").write_text(
        "\n".join([json.dumps(records[0]), "", "{bad-json", json.dumps(records[1])]),
        encoding="utf-8",
    )
    (storage / f"{yesterday}.jsonl").write_text(json.dumps(records[0]) + "\n", encoding="utf-8")
    (raw / f"{today}.jsonl").write_text(json.dumps({"record_id": "raw-1", "value": True}) + "\n", encoding="utf-8")

    telemetry = SimpleNamespace(
        _enabled=True,
        _mode="dev",
        _pii_mode="both",
        _retention_days=7,
        _storage_path=storage,
        _pseudonymizer=SimpleNamespace(
            _regex=True,
            _spacy=SimpleNamespace(available=True),
            _presidio=SimpleNamespace(available=False),
        ),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(telemetry=telemetry)))
    user = _admin_user()

    status = await telemetry_router.get_telemetry_status(request, user)
    assert status.pseudonymizer_engines == ["regex", "spacy"]

    updated = await telemetry_router.update_telemetry_config(
        request,
        telemetry_router.TelemetryConfigUpdate(
            enabled=False,
            mode="production",
            pii_mode="raw_only",
            retention_days=14,
        ),
        user,
    )
    assert updated.enabled is False
    assert updated.mode == "production"
    assert (storage / "raw").exists()

    for update, detail in [
        (telemetry_router.TelemetryConfigUpdate(mode="bad"), "Invalid mode"),
        (telemetry_router.TelemetryConfigUpdate(pii_mode="bad"), "Invalid pii_mode"),
        (telemetry_router.TelemetryConfigUpdate(retention_days=0), "retention_days"),
    ]:
        with pytest.raises(HTTPException) as exc_info:
            await telemetry_router.update_telemetry_config(request, update, user)
        assert exc_info.value.status_code == 400
        assert detail in exc_info.value.detail

    missing_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(telemetry=None)))
    with pytest.raises(HTTPException) as missing_service:
        await telemetry_router.get_telemetry_status(missing_request, user)
    assert missing_service.value.status_code == 503

    files = await telemetry_router.list_telemetry_files(request, user)
    assert files["files"][0].date == today
    assert files["files"][0].has_raw is True

    protected_record = await telemetry_router.get_telemetry_record(request, "r1", "protected", user)
    assert protected_record["session_id"] == "s1"

    raw_record = await telemetry_router.get_telemetry_record(request, "raw-1", "raw", user)
    assert raw_record["value"] is True

    with pytest.raises(HTTPException) as missing_record:
        await telemetry_router.get_telemetry_record(request, "missing", "protected", user)
    assert missing_record.value.status_code == 404

    paged = await telemetry_router.get_telemetry_records(request, today, "protected", offset=0, limit=1, user=user)
    assert paged["total"] == 3
    assert paged["has_more"] is True

    raw_paged = await telemetry_router.get_telemetry_records(request, today, "raw", offset=0, limit=10, user=user)
    assert raw_paged["records"][0]["record_id"] == "raw-1"

    with pytest.raises(HTTPException) as missing_file:
        await telemetry_router.get_telemetry_records(request, "1999-01-01", "protected", user=user)
    assert missing_file.value.status_code == 404

    found = await telemetry_router.search_telemetry_records(
        request,
        telemetry_router.TelemetrySearchRequest(
            session_id="s1",
            model="gpt-test",
            date_from=yesterday,
            date_to=today,
            min_latency=50,
            query_text="alpha",
            limit=5,
        ),
        user,
    )
    assert found["total"] == 2

    stats = await telemetry_router.get_telemetry_stats(request, range="all", user=user)
    assert stats.records_count == 3
    assert stats.avg_latency_ms == 166.67
    assert stats.total_tokens == 29
    assert stats.error_rate == 0.3333
    assert stats.model_distribution["gpt-test"] == 2

    stats_30d = await telemetry_router.get_telemetry_stats(request, range="30d", user=user)
    assert stats_30d.records_count == 3


@pytest.mark.asyncio
async def test_evolution_state_store_memory_and_mongo_paths() -> None:
    """Cover evolution-state stores, clone behavior, and tolerant parsing."""
    first = EvolutionGenerationState(
        experiment_id="exp",
        generation=0,
        population_size=2,
        child_ids=["a", "b"],
    )
    cloned = _clone_state(first)
    cloned.child_ids.append("mutated")
    assert first.child_ids == ["a", "b"]

    memory = InMemoryEvolutionStateStore()
    stored = await memory.upsert_generation(first)
    stored.child_ids.append("caller-mutation")
    assert (await memory.get_latest("missing")) is None
    latest = await memory.get_latest("exp")
    assert latest is not None
    assert latest.child_ids == ["a", "b"]

    second = EvolutionGenerationState(
        experiment_id="exp",
        generation=1,
        parent_ids=["a"],
        child_ids=["c"],
        mean_fitness=0.7,
        best_fitness=0.9,
    )
    await memory.upsert_generation(second)
    generations = await memory.list_generations("exp")
    assert [row.generation for row in generations] == [0, 1]

    replacement = first.model_copy(update={"child_ids": ["replacement"]})
    replaced = await memory.upsert_generation(replacement)
    assert replaced.created_at == first.created_at
    assert (await memory.list_generations("exp"))[0].child_ids == ["replacement"]

    assert _doc_to_state({"generation": "bad"}) is None
    assert MongoEvolutionStateStore(_FakeAsyncDB()).collection is not None
    with pytest.raises(ValueError):
        MongoEvolutionStateStore(None)

    db = _FakeAsyncDB()
    mongo = MongoEvolutionStateStore(db)
    await mongo.ensure_indexes()
    await mongo.ensure_indexes()
    await mongo.upsert_generation(first)
    await mongo.upsert_generation(second)
    latest_mongo = await mongo.get_latest("exp")
    assert latest_mongo is not None
    assert latest_mongo.generation == 1
    listed_mongo = await mongo.list_generations("exp")
    assert [row.generation for row in listed_mongo] == [0, 1]


@pytest.mark.asyncio
async def test_node_llm_helper_resolution_completion_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover model-role resolution, LiteLLM parameter shaping, and JSON extraction."""
    default_helper = NodeLLMHelper()
    assert default_helper.resolve_model("any") == ("ollama", "llama3.1:8b", "http://localhost:11434")
    assert default_helper._get_api_key("openai") is None
    assert default_helper._estimate_tokens("abcdefgh") == 2
    assert NodeLLMHelper._extract_json('{"ok": true}') == {"ok": True}
    assert NodeLLMHelper._extract_json('```json\n{"ok": true}\n```') == {"ok": True}
    assert NodeLLMHelper._extract_json('```\n[1, 2]\n```') == [1, 2]
    assert NodeLLMHelper._extract_json('prefix {"ok": 1}') == {"ok": 1}
    assert NodeLLMHelper._extract_json("not json") is None

    class RoleConfig:
        def __init__(self, provider: str, model: str) -> None:
            self.provider = provider
            self.model = model
            self.timeout_ms = 100

    class Registry:
        def __init__(self, provider: str, model: str) -> None:
            self.provider = provider
            self.model = model

        def resolve_with_fallback(self, role: str) -> RoleConfig:
            assert role == "role"
            return RoleConfig(self.provider, self.model)

    class Settings:
        ollama_base_url = "http://ollama.local"
        llm_base_url = "http://compatible.local"
        agent_llm_request_timeout = 99

        def get_api_key_for_provider(self, provider: str) -> str:
            return f"key-{provider}"

    assert NodeLLMHelper(Registry("ollama", "llama3"), Settings()).resolve_model("role") == (
        "ollama",
        "llama3",
        "http://ollama.local",
    )
    assert NodeLLMHelper(Registry("openai_compatible", "mixtral"), Settings()).resolve_model("role") == (
        "openai_compatible",
        "mixtral",
        "http://compatible.local",
    )

    helper = NodeLLMHelper(Registry("openai", "gpt-5.2"), Settings())
    captured: dict[str, Any] = {}

    async def fake_acompletion(**params: Any) -> Any:
        captured.update(params)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"value": 1}'))],
            usage=None,
        )

    monkeypatch.setattr(llm_helper_module, "acompletion", fake_acompletion)
    parsed, tokens = await helper.complete_json(
        "role",
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "give json"}],
        {"temperature": 0.1, "max_tokens": 33, "seed": 7},
    )
    assert parsed == {"value": 1}
    assert tokens == 3
    assert captured["model"] == "gpt-5.2"
    assert captured["max_completion_tokens"] == 33
    assert "max_tokens" not in captured
    assert captured["api_key"] == "key-openai"
    assert captured["messages"][-1]["content"].endswith("Respond with valid JSON only.")

    helper_google = NodeLLMHelper(Registry("google", "gemini-pro"), Settings())
    text, used = await helper_google.complete("role", [{"role": "user", "content": "hi"}], {})
    assert text == '{"value": 1}'
    assert used == 3
    assert captured["model"] == "gemini/gemini-pro"
    assert "max_tokens" in captured

    async def invalid_acompletion(**_: Any) -> Any:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="plain text"))],
            usage=SimpleNamespace(total_tokens=5),
        )

    monkeypatch.setattr(llm_helper_module, "acompletion", invalid_acompletion)
    with pytest.raises(ValueError):
        await helper.complete_json("role", [{"role": "assistant", "content": "no user"}], {})

    async def failing_acompletion(**_: Any) -> Any:
        raise RuntimeError("llm down")

    monkeypatch.setattr(llm_helper_module, "acompletion", failing_acompletion)
    with pytest.raises(RuntimeError):
        await helper.complete("role", [{"role": "user", "content": "hi"}], {})


@pytest.mark.asyncio
async def test_evidence_citation_and_refine_nodes() -> None:
    """Cover evidence extraction, citation validation, and refinement paths."""

    chunks = [
        _chunk(
            "legal-1",
            document_id="legal",
            score=1.2,
            content="The court held that alpha applies. However, beta conflicts with alpha.",
            metadata={},
        ).model_copy(update={"span_start": 1, "span_end": 20}),
        _chunk(
            "legal-2",
            document_id="legal2",
            score=-0.2,
            content="The court held that alpha applies. Additional legal question details.",
        ),
    ]

    empty_evidence = await EvidenceCardExecutor().execute(
        _node("evidence", "evidence_cards"),
        StrategyRunState(query="alpha"),
    )
    assert empty_evidence.status == "empty"

    class EvidenceHelper:
        def __init__(self, payload: Any, fail: bool = False) -> None:
            self.payload = payload
            self.fail = fail

        async def complete_json(self, *_: Any, **__: Any) -> tuple[Any, int]:
            if self.fail:
                raise RuntimeError("extract failed")
            return self.payload, 13

    llm_evidence = await EvidenceCardExecutor(
        EvidenceHelper(
            {
                "cards": [
                    {
                        "card_type": "fact",
                        "topic": "Alpha",
                        "source_id": chunks[0].source_id,
                        "source_excerpt": "The court held that alpha applies.",
                        "factual_basis": "Legal source",
                        "confidence": 0.95,
                    },
                    "ignore me",
                    {"card_type": "bad-type", "topic": "Bad"},
                ]
            }
        )
    ).execute(
        _node("evidence", "evidence_cards", batch_size=1),
        StrategyRunState.model_construct(
            query="alpha",
            retrieved_chunks=chunks,
            business_context=SimpleNamespace(capability_id="legal_questions", language="en"),
        ),
    )
    assert llm_evidence.status == "success"
    assert llm_evidence.tokens_used == 26
    assert llm_evidence.output_data[0]["metadata"]["extraction"] == "llm"
    assert llm_evidence.output_data[0]["span_id"] == "1-20"

    fallback_evidence = await EvidenceCardExecutor(EvidenceHelper([], fail=True)).execute(
        _node(
            "evidence",
            "evidence_cards",
            evidence_policy={"card_types": ["legal_question", "contradiction", "fact"], "min_confidence": 0.0, "max_cards": 2},
        ),
        StrategyRunState.model_construct(
            query="alpha",
            retrieved_chunks=chunks,
            business_context=SimpleNamespace(capability_id="legal_questions"),
        ),
    )
    assert fallback_evidence.status == "success"
    assert {card["card_type"] for card in fallback_evidence.output_data} <= {
        "legal_question",
        "contradiction",
        "fact",
    }

    citation_node = ValidateCitationsExecutor()
    no_synthesis = await citation_node.execute(_node("vc", "validate_citations"), StrategyRunState(query="alpha"))
    assert no_synthesis.status == "empty"

    no_citations = await citation_node.execute(
        _node("vc", "validate_citations"),
        StrategyRunState(query="alpha", synthesis_result=SynthesisResult(text="No citations")),
    )
    assert no_citations.output_data["issues"][0]["issue_type"] == "no_citations"

    mixed_citations = SynthesisResult(
        text="Alpha answer",
        citations=[
            CitationRef(source_id=chunks[0].source_id, document_title=chunks[0].document_title, quote="court held alpha applies"),
            CitationRef(source_id=chunks[1].source_id, document_title=chunks[1].document_title, quote="unrelated quote"),
            CitationRef(source_id="missing", document_title="Missing", quote="ghost"),
        ],
    )
    validation = await citation_node.execute(
        _node("vc", "validate_citations"),
        StrategyRunState(query="alpha", synthesis_result=mixed_citations, retrieved_chunks=chunks),
    )
    assert validation.output_data["passed"] is False
    assert validation.output_data["metadata"]["orphan_count"] == 1
    assert {
        issue["issue_type"] for issue in validation.output_data["issues"]
    } == {"unverifiable_quote", "orphan_citation"}

    class RefinementHelper:
        def __init__(self, text: str, fail: bool = False) -> None:
            self.text = text
            self.fail = fail

        async def complete(self, *_: Any, **__: Any) -> tuple[str, int]:
            if self.fail:
                raise RuntimeError("refine failed")
            return self.text, 23

    refine_node = RefineExecutor()
    empty_refine = await refine_node.execute(_node("refine", "refine"), StrategyRunState(query="alpha"))
    assert empty_refine.status == "empty"

    clean_refine = await refine_node.execute(
        _node("refine", "refine"),
        StrategyRunState(
            query="alpha",
            synthesis_result=_synthesis("source-doc-1 clean answer"),
            validation_results=[ValidationResult(validator_id="ok", passed=True, score=0.9)],
        ),
    )
    assert clean_refine.output_data["text"] == "source-doc-1 clean answer"

    issue = ValidationIssue(issue_type="missing_section", severity="warning", message="missing", field="Summary")
    llm_refine = await RefineExecutor(RefinementHelper("Improved answer")).execute(
        _node("refine", "refine"),
        StrategyRunState(
            query="alpha",
            synthesis_result=_synthesis("Old answer"),
            validation_results=[ValidationResult(validator_id="contract", passed=False, score=0.2, issues=[issue])],
        ),
    )
    assert llm_refine.output_data["text"] == "Improved answer"
    assert llm_refine.model_used == "synthesizer_deep"

    long_text = "source-doc-1 " + "word " * 1600
    rule_refine = await RefineExecutor(RefinementHelper("", fail=True)).execute(
        _node("refine", "refine", min_score_threshold=0.95),
        StrategyRunState(
            query="alpha",
            synthesis_result=SynthesisResult(
                text=long_text,
                citations=[
                    CitationRef(source_id="source-doc-1", document_title="Alpha Memo"),
                    CitationRef(source_id="orphan", document_title="Orphan"),
                ],
                confidence=0.8,
            ),
            validation_results=[
                ValidationResult(
                    validator_id="citation",
                    passed=False,
                    score=0.2,
                    issues=[
                        ValidationIssue(issue_type="orphan_citation", severity="error", message="orphan"),
                        ValidationIssue(issue_type="low_coverage", severity="warning", message="coverage"),
                    ],
                ),
                ValidationResult(
                    validator_id="contract",
                    passed=False,
                    score=0.2,
                    issues=[
                        ValidationIssue(issue_type="missing_section", severity="warning", message="missing", field="Summary"),
                        ValidationIssue(issue_type="max_length_exceeded", severity="warning", message="long"),
                    ],
                ),
                ValidationResult(
                    validator_id="quality",
                    passed=True,
                    score=0.5,
                    issues=[ValidationIssue(issue_type="low_quality", severity="warning", message="quality")],
                ),
            ],
        ),
    )
    assert rule_refine.status == "success"
    assert "orphan" not in [c["source_id"] for c in rule_refine.output_data["citations"]]
    assert rule_refine.output_data["confidence"] < 0.8
    assert "Trimmed" in rule_refine.output_data["text"]


@pytest.mark.asyncio
async def test_strategy_runner_internal_branches() -> None:
    """Cover runner status mapping, retry, merge, budget, and trace branches."""

    class Registry:
        def __init__(self, outputs: list[NodeOutput] | None = None, fail: bool = False) -> None:
            self.outputs = outputs or []
            self.fail = fail
            self.calls = 0

        async def execute(self, node: StrategyNode, state: StrategyRunState) -> NodeOutput:
            self.calls += 1
            if self.fail:
                raise RuntimeError("registry boom")
            if self.outputs:
                return self.outputs.pop(0)
            return NodeOutput(node_id=node.node_id, node_type=node.node_type)

    runner = StrategyRunner(Registry())
    assert runner._map_run_status(halt_reason=None, had_exception=False) == "success"
    assert runner._map_run_status(halt_reason="execution_error: bad", had_exception=False) == "failed"
    assert runner._map_run_status(halt_reason="latency_hard_limit_exceeded", had_exception=False) == "timed_out"
    assert runner._map_run_status(halt_reason="node_halt", had_exception=False) == "cancelled"
    assert runner._map_run_status(halt_reason=None, had_exception=True) == "failed"

    spec = StrategySpec(**_spec_payload("runner"))
    context = BusinessContext(capability_id="qa", tenant_id="tenant", profile_key="profile")
    state = runner._init_state(spec, context, query="alpha")
    assert state.metadata["tenant_id"] == "tenant"

    route_state = StrategyRunState(query="alpha")
    outputs = [
        NodeOutput(node_id="nq", node_type="normalize_query", output_data={"normalized_query": "normalized"}),
        NodeOutput(node_id="ret", node_type="retrieve", output_data=[_chunk().model_dump()]),
        NodeOutput(node_id="ev", node_type="evidence_cards", output_data=[_card().model_dump()]),
        NodeOutput(node_id="syn", node_type="synthesize", output_data=_synthesis().model_dump()),
        NodeOutput(
            node_id="val",
            node_type="validate_citations",
            output_data=ValidationResult(validator_id="v", passed=True).model_dump(),
        ),
        NodeOutput(node_id="ctx", node_type="business_context", output_data={"capability_id": "qa"}),
        NodeOutput(node_id="plan", node_type="plan", output_data={"steps": []}),
        NodeOutput(node_id="telemetry", node_type="emit_telemetry", output_data={"ignored": True}),
        NodeOutput(
            node_id="legacy",
            node_type="legacy_orchestrator_pipeline",
            output_data={
                "synthesis_result": _synthesis("legacy").model_dump(),
                "retrieved_chunks": [_chunk("legacy").model_dump()],
            },
        ),
        NodeOutput(node_id="unknown", node_type="unknown", output_data={"x": 1}),
    ]
    for output in outputs:
        runner._merge_node_output(route_state, output)
    assert route_state.normalized_query == "normalized"
    assert route_state.retrieved_chunks[0].chunk_id == "legacy"
    assert route_state.evidence_cards[0].topic == "Alpha"
    assert route_state.synthesis_result.text == "legacy"
    assert route_state.validation_results[0].validator_id == "v"
    assert route_state.metadata["plan"] == {"steps": []}
    assert route_state.metadata["node_output_unknown"] == {"x": 1}

    with pytest.raises(StateViolationError):
        runner._merge_node_output(route_state, NodeOutput(node_id="unknown", node_type="unknown"))

    failed_state = StrategyRunState(query="alpha")
    runner._merge_node_output(
        failed_state,
        NodeOutput(node_id="failed", node_type="retrieve", status="error", output_data=[_chunk().model_dump()]),
    )
    assert failed_state.retrieved_chunks == []

    budget_state = StrategyRunState(
        query="alpha",
        elapsed_ms=50,
        node_outputs={
            "a": NodeOutput(node_id="a", node_type="synthesize", tokens_used=10),
            "b": NodeOutput(node_id="b", node_type="synthesize", tokens_used=5),
        },
    )
    assert runner._check_budget(budget_state, None) is None
    assert runner._check_budget(budget_state, StrategyBudgets(latency_hard_limit_ms=1)).startswith("latency_hard_limit")
    assert runner._check_budget(budget_state, StrategyBudgets(max_context_tokens=1)).startswith("token_budget")
    assert runner._check_budget(budget_state, StrategyBudgets(max_llm_calls=1)).startswith("llm_call")
    assert runner._check_budget(budget_state, StrategyBudgets(latency_target_ms=1, latency_hard_limit_ms=1000)) is None

    retry_runner = StrategyRunner(
        Registry(
            [
                NodeOutput(node_id="r", node_type="custom", status="error", error_message="first"),
                NodeOutput(node_id="r", node_type="custom", status="success"),
            ]
        )
    )
    retried = await retry_runner._execute_node_with_retry(
        StrategyNode(node_id="r", node_type="custom", retry_config={"max_retries": 1, "delay_ms": 0}),
        StrategyRunState(query="alpha"),
    )
    assert retried.status == "success"
    assert retried.retry_count == 1

    empty_skip = await StrategyRunner(
        Registry([NodeOutput(node_id="e", node_type="custom", status="empty")])
    )._execute_node_with_retry(
        StrategyNode(node_id="e", node_type="custom", on_empty="skip"),
        StrategyRunState(query="alpha"),
    )
    assert empty_skip.status == "skipped"

    optional_skip = await StrategyRunner(
        Registry([NodeOutput(node_id="o", node_type="custom", status="error", error_message="bad")])
    )._execute_node_with_retry(
        StrategyNode(node_id="o", node_type="custom", optional=True),
        StrategyRunState(query="alpha"),
    )
    assert optional_skip.status == "skipped"

    halt_error = await StrategyRunner(
        Registry([NodeOutput(node_id="h", node_type="custom", status="empty")])
    )._execute_node_with_retry(
        StrategyNode(node_id="h", node_type="custom", on_empty="error"),
        StrategyRunState(query="alpha"),
    )
    assert halt_error.status == "error"
    assert halt_error.error_message == "Node returned empty result"

    single_error = await StrategyRunner(Registry(fail=True))._execute_single_node(
        StrategyNode(node_id="boom", node_type="custom"),
        StrategyRunState(query="alpha"),
    )
    assert single_error.status == "error"

    class SlowRegistry(Registry):
        async def execute(self, node: StrategyNode, state: StrategyRunState) -> NodeOutput:
            await __import__("asyncio").sleep(0.05)
            return NodeOutput(node_id=node.node_id, node_type=node.node_type)

    timed_out = await StrategyRunner(SlowRegistry())._execute_single_node(
        StrategyNode(node_id="slow", node_type="custom", timeout_ms=1),
        StrategyRunState(query="alpha"),
    )
    assert timed_out.status == "timed_out"

    graph = StrategyGraph(
        nodes=[
            StrategyNode(node_id="a", node_type="custom"),
            StrategyNode(node_id="b", node_type="custom"),
            StrategyNode(node_id="c", node_type="custom"),
            StrategyNode(node_id="orphan", node_type="custom"),
        ],
        edges=[
            StrategyEdge(from_node="a", to_node="b", condition="has_chunks"),
            StrategyEdge(from_node="a", to_node="c", condition="missing("),
        ],
        entry_node="a",
        terminal_nodes=["b", "c", "orphan"],
    )
    compiled = runner._compile_and_validate(graph)
    reach_state = StrategyRunState(
        query="alpha",
        retrieved_chunks=[_chunk()],
        node_outputs={"a": NodeOutput(node_id="a", node_type="custom")},
    )
    assert runner._is_node_reachable("a", compiled, reach_state) is True
    assert runner._is_node_reachable("orphan", compiled, reach_state) is True
    assert runner._is_node_reachable("b", compiled, reach_state) is True
    assert runner._is_node_reachable("c", compiled, reach_state) is False

    reach_state.node_outputs["a"] = NodeOutput(node_id="a", node_type="custom", status="error")
    assert runner._is_node_reachable("b", compiled, reach_state) is False
    compiled.node_map["a"].optional = True
    assert runner._is_node_reachable("b", compiled, reach_state) is True

    finalized = runner._finalize(route_state, None, spec)
    assert finalized.success is True
    assert finalized.total_tokens == 0
    failed_finalized = runner._finalize(StrategyRunState(query="alpha"), "node_halt", spec)
    assert failed_finalized.success is False

    class TraceStore:
        def __init__(self, fail: bool = False) -> None:
            self.fail = fail
            self.saved = None

        async def save(self, doc: Any) -> None:
            if self.fail:
                raise RuntimeError("store down")
            self.saved = doc

    trace_store = TraceStore()
    trace_runner = StrategyRunner(Registry(), run_trace_store=trace_store)
    await trace_runner._persist_trace(
        finalized,
        spec=spec,
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        halt_reason=None,
        had_exception=False,
    )
    assert trace_store.saved.status == "success"

    await StrategyRunner(Registry(), run_trace_store=TraceStore(fail=True))._persist_trace(
        finalized,
        spec=spec,
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        halt_reason="unexpected_error",
        had_exception=True,
    )

    cancelled_runner = StrategyRunner(Registry())
    cancelled_runner.cancel()
    halt = await cancelled_runner._execute_levels(compiled, StrategyRunState(query="alpha"), StrategyBudgets())
    assert halt == "cancelled"


def test_router_dependency_helpers_choose_expected_backends() -> None:
    """Cover helper-only dependency branches that are awkward through HTTP."""
    assert isinstance(
        spec_router.get_spec_store(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(spec_store=None, db=None)))),
        InMemorySpecStore,
    )
    shared_store = InMemorySpecStore({})
    assert spec_router.get_spec_store(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(spec_store=shared_store, db=None)))
    ) is shared_store

    db = SimpleNamespace(db={"x": "y"})
    resolved = spec_router._resolve_mongo_db(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db))))
    assert resolved == {"x": "y"}

    scheduler_store = scheduler_router.InMemorySchedulerStore()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scheduler_store=scheduler_store, db=None)))
    assert scheduler_router.get_scheduler_store(request) is scheduler_store

    daemon = scheduler_router.SchedulerDaemon(store=scheduler_store)
    request.app.state.scheduler_daemon = daemon
    assert scheduler_router.get_scheduler_daemon(request) is daemon

    fallback_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=None)))
    assert scheduler_router.get_scheduler_daemon(fallback_request) is scheduler_router._default_daemon


def _mutation_spec(
    *,
    strategy_id: str = "mutation-parent",
    include_evidence: bool = False,
    include_rerank: bool = False,
    include_budget: bool = True,
    include_contract: bool = True,
    terminal_nodes: list[str] | None = None,
) -> StrategySpec:
    nodes = [
        StrategyNode(node_id="n_expand", node_type="query_expand", config={"max_variants": 2}),
        StrategyNode(node_id="n_retrieve", node_type="retrieve"),
    ]
    edges = [StrategyEdge(from_node="n_expand", to_node="n_retrieve")]
    previous = "n_retrieve"
    if include_rerank:
        nodes.append(StrategyNode(node_id="n_rerank", node_type="rerank", config={"top_k": 12}))
        edges.append(StrategyEdge(from_node=previous, to_node="n_rerank"))
        previous = "n_rerank"
    if include_evidence:
        nodes.append(StrategyNode(node_id="n_evidence", node_type="evidence_cards"))
        edges.append(StrategyEdge(from_node=previous, to_node="n_evidence"))
        previous = "n_evidence"
    nodes.append(
        StrategyNode(
            node_id="n_synth",
            node_type="synthesize",
            model_role="synthesizer_fast",
            config={"model_role": "synthesizer_fast"},
        )
    )
    edges.append(StrategyEdge(from_node=previous, to_node="n_synth"))

    built = StrategySpec(
        strategy_id=strategy_id,
        version="2.3.4",
        status="active",
        display_name="Mutation Parent",
        capability_id="qa",
        tenant_scope="recallhub",
        tags=["keep", "parent:old", "generation:0", "mutation:old"],
        graph=StrategyGraph(
            nodes=nodes,
            edges=edges,
            entry_node="n_expand",
            terminal_nodes=terminal_nodes or ["n_synth"],
        ),
        budgets=StrategyBudgets(
            max_context_tokens=8000,
            max_output_tokens=1000,
            latency_target_ms=1000,
            latency_hard_limit_ms=2000,
        ),
        answer_contract=(
            AnswerContract(
                format_id="default_answer_v1",
                unsupported_claim_policy="flag",
            )
            if include_contract
            else None
        ),
        model_roles={"synth_model": "ollama/llama3.1"},
    )
    if not include_budget:
        object.__setattr__(built, "budgets", None)
    return built


def test_evolutionary_mutator_helpers_and_operator_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise mutation helper failure paths and operator no-op branches."""
    spec = _mutation_spec()
    dumped = _deep_dump(spec)
    assert dumped["strategy_id"] == spec.strategy_id
    assert _find_node_index(dumped, "n_expand") == 0
    assert _find_node_index({"graph": {"nodes": "bad"}}, "n") is None
    located = _find_node_by_type(dumped, "synthesize")
    assert located is not None and located[1]["node_id"] == "n_synth"
    assert _find_node_by_type({"graph": {"nodes": object()}}, "retrieve") is None

    assert _hydrate({"strategy_id": 123, "graph": {"nodes": []}}) is None
    monkeypatch.setattr(evo_mutator_module, "validate_spec", lambda _spec: ["bad graph"])
    assert _validate(spec, "forced_invalid") is None
    monkeypatch.setattr(evo_mutator_module, "validate_spec", lambda _spec: [])

    graph_dump = _deep_dump(_mutation_spec(include_evidence=True, include_rerank=True))
    _retire_edges_through(graph_dump, "n_rerank")
    assert {"from_node": "n_retrieve", "to_node": "n_evidence"} in graph_dump["graph"]["edges"]
    complex_dump = {"graph": {"edges": [{"from_node": "a", "to_node": "x"}, {"from_node": "b", "to_node": "x"}]}}
    _retire_edges_through(complex_dump, "x")
    assert complex_dump["graph"]["edges"] == []
    _retire_edges_through({"graph": {"edges": "bad"}}, "x")

    inbound_dump = {"graph": {"edges": [{"from_node": "a", "to_node": "target"}, "raw"]}}
    _replace_inbound_edges(inbound_dump, target_node="target", new_predecessor="inserted")
    assert {"from_node": "inserted", "to_node": "target"} in inbound_dump["graph"]["edges"]
    _replace_inbound_edges({"graph": {"edges": object()}}, target_node="x", new_predecessor="y")

    terminal_dump = _deep_dump(_mutation_spec(terminal_nodes=["a", "b"]))
    _append_after_terminal(terminal_dump, new_node_id="new_terminal")
    assert terminal_dump["graph"]["terminal_nodes"] == ["a", "b"]
    bad_edges_dump = {"graph": {"terminal_nodes": ["a"], "edges": object()}}
    _append_after_terminal(bad_edges_dump, new_node_id="new_terminal")

    assert reduce_context_budget(spec, factor=0) is None
    assert reduce_context_budget(_mutation_spec(include_budget=False)) is None
    assert reduce_context_budget(spec, factor=1.0) is None
    assert reduce_context_budget(spec, factor=0.5).budgets.max_context_tokens == 4000  # type: ignore[union-attr]

    assert switch_synthesis_model(spec, alternatives=()) is None
    assert switch_synthesis_model(
        _mutation_spec(),
        alternatives=("synthesizer_fast",),
    ) is None
    assert switch_synthesis_model(spec, alternatives=("synthesizer_fast", "worker")) is not None

    no_synth = spec.model_copy(deep=True)
    no_synth.graph.nodes = [n for n in no_synth.graph.nodes if n.node_type != "synthesize"]
    assert switch_synthesis_model(no_synth) is None
    assert add_evidence_cards_node(_mutation_spec(include_evidence=True)) is None
    assert add_evidence_cards_node(no_synth) is None
    assert add_evidence_cards_node(spec) is not None
    assert remove_evidence_cards_node(spec) is None
    assert remove_evidence_cards_node(_mutation_spec(include_evidence=True)) is not None

    assert add_reranker_node(_mutation_spec(include_rerank=True)) is None
    forked = _mutation_spec()
    forked.graph.edges.append(StrategyEdge(from_node="n_retrieve", to_node="n_extra"))
    forked.graph.nodes.append(StrategyNode(node_id="n_extra", node_type="emit_telemetry"))
    assert add_reranker_node(forked) is None
    assert add_reranker_node(spec) is not None
    assert remove_reranker_node(spec) is None
    assert remove_reranker_node(_mutation_spec(include_rerank=True)) is not None

    assert change_contract_strictness(_mutation_spec(include_contract=False)) is None
    assert change_contract_strictness(spec, levels=()) is None
    assert change_contract_strictness(spec, levels=("moderate",)) is None
    assert change_contract_strictness(spec, levels=("unknown",)) is None
    assert change_contract_strictness(spec).answer_contract.unsupported_claim_policy == "omit"  # type: ignore[union-attr]

    assert alter_query_expansion_count(spec, range=(0, 2)) is None
    assert alter_query_expansion_count(spec, range=(3, 2)) is None
    assert alter_query_expansion_count(no_synth, range=(2, 2)) is None
    assert alter_query_expansion_count(spec, range=(2, 2)) is None
    assert alter_query_expansion_count(spec, range=(1, 3)) is not None
    assert range_inclusive(3, 2) == []

    assert add_validation_node(spec) is not None
    assert add_validation_node(spec, node_id="n_synth") is None
    assert add_refinement_node(spec) is not None
    assert add_refinement_node(spec, node_id="n_synth") is None


def test_evolutionary_mutator_population_lineage_and_error_handling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover deterministic lineage, constructor validation, and swallowed operator failures."""
    parent = _mutation_spec()

    with pytest.raises(ValueError, match="mutations_per_parent"):
        EvolutionaryMutator(mutations_per_parent=0)
    with pytest.raises(ValueError, match="max_per_generation"):
        EvolutionaryMutator(max_per_generation=0)
    with pytest.raises(ValueError, match="requires at least one"):
        EvolutionaryMutator(operators=())

    def same_child(spec: StrategySpec) -> StrategySpec:
        return spec.model_copy(deep=True)

    def raises(_spec: StrategySpec) -> StrategySpec:
        raise RuntimeError("operator failed")

    def declines(_spec: StrategySpec) -> None:
        return None

    mutator = EvolutionaryMutator(
        operators=(("same", same_child), ("raises", raises), ("declines", declines)),
        mutations_per_parent=5,
        max_per_generation=2,
        seed=3,
    )
    children = mutator.mutate(parents=[parent, parent], generation=2)
    assert len(children) <= 2
    assert all(child.strategy_id.startswith("mutation-parent__evo_02_") for child in children)
    assert all(child.version.startswith("2.3.4+evo.2.") for child in children)
    assert all("keep" in child.tags for child in children)
    assert all("parent:mutation-parent" in child.tags for child in children)
    assert all(not any(tag == "parent:old" for tag in child.tags) for child in children)
    assert mutator.describe()["total_attempts"] >= len(children)

    assert EvolutionaryMutator(operators=(("none", declines),), seed=1).mutate(
        parents=[], generation=0
    ) == []
    with pytest.raises(ValueError, match="generation"):
        mutator.mutate(parents=[parent], generation=-1)

    monkeypatch.setattr(evo_mutator_module, "validate_spec", lambda _spec: ["lineage invalid"])
    invalid_children = EvolutionaryMutator(
        operators=(("same", same_child),),
        mutations_per_parent=1,
        max_per_generation=1,
        seed=1,
    ).mutate(parents=[parent], generation=1)
    assert invalid_children == []


def _runner_spec(strategy_id: str = "candidate-a") -> StrategySpec:
    return StrategySpec(
        strategy_id=strategy_id,
        version="1.0.0",
        graph=StrategyGraph(
            nodes=[StrategyNode(node_id="n1", node_type="retrieve")],
            edges=[],
            entry_node="n1",
            terminal_nodes=["n1"],
        ),
        budgets=StrategyBudgets(max_context_tokens=1234, max_output_tokens=321),
    )


def _runner_job(**overrides: Any) -> StrategyExperimentJob:
    data = {
        "id": "job-extra",
        "tenant": "recallhub",
        "mode": "regression",
        "datasets": ["ds-a"],
        "strategy_ids": ["candidate-a"],
        "status": JobStatus.QUEUED,
    }
    data.update(overrides)
    return StrategyExperimentJob(**data)


class _MiniEvalRunner:
    def __init__(self, *, score_raises: bool = False) -> None:
        self.score_raises = score_raises

    async def load_dataset(self, dataset_id: str) -> list[Any]:
        return [
            SimpleNamespace(id=f"{dataset_id}-case-1", query="alpha"),
            SimpleNamespace(id=f"{dataset_id}-case-2", query="beta"),
        ]

    async def score(self, case: Any, run_result: Any) -> Any:
        if self.score_raises:
            raise RuntimeError("score failed")
        return SimpleNamespace(
            composite_score=0.75,
            latency_ms=None,
            metrics={"quality": 0.8, "ignored": "not numeric"},
        )


class _MiniResourceCollector:
    def __init__(self, *, safe: bool = True, fail_capture: bool = False) -> None:
        self.safe = safe
        self.fail_capture = fail_capture

    async def is_safe_to_run(self, limits: ResourceLimits) -> tuple[bool, list[str]]:
        return self.safe, ([] if self.safe else ["cpu high"])

    async def capture(self) -> Any:
        if self.fail_capture:
            raise RuntimeError("capture failed")
        return SimpleNamespace(id="snap-extra")


class _MiniProfiler:
    def __init__(self, *, fail: bool = False, no_id: bool = False) -> None:
        self.fail = fail
        self.no_id = no_id
        self.calls: list[dict[str, Any]] = []

    async def run_test(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("profile failed")
        return SimpleNamespace() if self.no_id else SimpleNamespace(id="profile-extra")


class _ContextAwareRunner:
    async def run(self, *, spec: StrategySpec, query: str, context: Any = None) -> StrategyRunResult:
        return StrategyRunResult(
            success=True,
            strategy_id=spec.strategy_id,
            state=StrategyRunState(run_id="run-extra", trace_id="trace-extra", query=query),
            total_duration_ms=222.0,
        )


def _make_experiment_runner(
    *,
    store: Any,
    eval_runner: Any | None = None,
    profiler: Any | None = None,
    collector: Any | None = None,
    factory: Any | None = None,
    spec_resolver: Any | None = None,
    spec_store: Any | None = None,
    throttle_ms: int = 0,
) -> StrategyExperimentRunner:
    return StrategyExperimentRunner(
        job_store=store,
        run_trace_store=SimpleNamespace(),
        runtime_profiler=profiler or _MiniProfiler(),
        resource_snapshot_collector=collector or _MiniResourceCollector(),
        evaluation_runner=eval_runner or _MiniEvalRunner(),
        strategy_runner_factory=factory or (lambda: _ContextAwareRunner()),
        spec_resolver=spec_resolver,
        spec_store=spec_store,
        progress_throttle_ms=throttle_ms,
    )


@pytest.mark.asyncio
async def test_experiment_runner_helpers_prepare_and_best_effort_paths() -> None:
    """Cover runner helper parsing, candidate expansion, throttles, and best-effort failures."""
    assert _extract_generation("base__evo_03_deadbeef") == 3
    assert _extract_generation("base") == 0
    assert _percentile([], 95) == 0.0
    assert _percentile([5], 95) == 5.0
    assert _percentile([0, 10], 95) == pytest.approx(9.5)

    synth_spec = _runner_spec()
    assert _resolve_synth_model(synth_spec) is None
    synth_spec.model_roles = {"synthesis": "provider:model-a"}
    assert _resolve_synth_model(synth_spec) == ("provider", "model-a")
    synth_spec.model_roles = {"synth_model": "provider/model-b"}
    assert _resolve_synth_model(synth_spec) == ("provider", "model-b")
    synth_spec.model_roles = {"synth_model": "plain-model"}
    assert _resolve_synth_model(synth_spec) == ("default", "plain-model")

    typed_limits = ResourceLimits(max_cpu_utilization_pct=33)
    assert _coerce_resource_limits(_runner_job(resource_limits=typed_limits)) is typed_limits
    legacy = _coerce_resource_limits(
        _runner_job(result_summary={"resource_limits": {"max_ram_usage_pct": 44}})
    )
    assert legacy.max_ram_usage_pct == 44
    assert isinstance(
        _coerce_resource_limits(_runner_job(result_summary={"resource_limits": {"max_ram_usage_pct": "bad"}})),
        ResourceLimits,
    )

    async def resolve_sid(sid: str) -> StrategySpec:
        return _runner_spec(sid)

    store = InMemoryExperimentJobStore()
    runner = _make_experiment_runner(
        store=store,
        spec_resolver=resolve_sid,
        throttle_ms=999999,
    )
    job = _runner_job(strategy_ids=[])
    assert await runner._build_candidates(job, mode_dispatcher=None) == []

    async def dispatcher(job_arg: StrategyExperimentJob, **kwargs: Any) -> list[StrategySpec]:
        assert kwargs["spec_resolver"] is not None
        assert job_arg.id == job.id
        return [_runner_spec("from-dispatcher")]

    assert [s.strategy_id for s in await runner._build_candidates(job, mode_dispatcher=dispatcher)] == [
        "from-dispatcher"
    ]
    resolved = await runner._build_candidates(
        _runner_job(strategy_ids=["one", "two"]),
        mode_dispatcher=None,
    )
    assert [s.strategy_id for s in resolved] == ["one", "two"]

    with pytest.raises(InvalidJobStateError):
        await _make_experiment_runner(store=store)._build_candidates(
            _runner_job(strategy_ids=["missing-resolver"]),
            mode_dispatcher=None,
        )

    # Unknown registered mode falls back to spec_resolver expansion when a spec store exists.
    async def resolve_fallback(sid: str) -> StrategySpec:
        return _runner_spec(f"fallback-{sid}")

    fallback_runner = _make_experiment_runner(
        store=store,
        spec_store=InMemorySpecStore(),
        spec_resolver=resolve_fallback,
    )
    fallback = await fallback_runner._build_candidates(
        _runner_job(mode="profiling", strategy_ids=["x"]),
        mode_dispatcher=None,
    )
    assert fallback[0].strategy_id == "fallback-x"

    progress = JobProgress(completed_runs=1)
    last = 1_000_000_000.0
    assert await runner._maybe_write_progress(
        job_id="job-x",
        progress=progress,
        last_progress_perf=last,
    ) == last

    class FailingProgressStore(InMemoryExperimentJobStore):
        async def update_progress(self, job_id: str, progress: JobProgress) -> StrategyExperimentJob:
            raise RuntimeError("progress unavailable")

    fail_runner = _make_experiment_runner(store=FailingProgressStore())
    assert await fail_runner._maybe_write_progress(
        job_id="missing",
        progress=progress,
        last_progress_perf=0.0,
    ) > 0.0

    class FailingGetStore(InMemoryExperimentJobStore):
        async def get(self, job_id: str) -> StrategyExperimentJob | None:
            raise RuntimeError("db unavailable")

    assert await _make_experiment_runner(store=FailingGetStore())._is_externally_cancelled("j") is False
    cancel_store = InMemoryExperimentJobStore()
    await cancel_store.create(_runner_job(id="cancel-me", status=JobStatus.CANCELLED))
    assert await _make_experiment_runner(store=cancel_store)._is_externally_cancelled("cancel-me") is True

    assert await runner._capture_runtime_profile(_runner_spec()) is None
    profile_spec = _runner_spec()
    profile_spec.model_roles = {"synth_model": "ollama/gemma3"}
    assert await _make_experiment_runner(store=store, profiler=_MiniProfiler(fail=True))._capture_runtime_profile(profile_spec) is None
    assert await _make_experiment_runner(store=store, profiler=_MiniProfiler(no_id=True))._capture_runtime_profile(profile_spec) is None
    ok_profiler = _MiniProfiler()
    assert await _make_experiment_runner(store=store, profiler=ok_profiler)._capture_runtime_profile(profile_spec) == "profile-extra"
    assert ok_profiler.calls[0]["context_tokens"] == 1234
    assert await _make_experiment_runner(
        store=store,
        collector=_MiniResourceCollector(fail_capture=True),
    )._capture_resource_snapshot() is None


@pytest.mark.asyncio
async def test_experiment_runner_case_candidate_and_bandit_edge_paths() -> None:
    """Cover TypeError fallback, append failures, fatal scoring, and bandit edge cases."""
    spec = _runner_spec("candidate-a")

    class NoContextRunner:
        async def run(self, *, spec: StrategySpec, query: str) -> Any:
            return SimpleNamespace(trace_id="trace-fallback", total_duration_ms=111.0)

    runner = _make_experiment_runner(
        store=InMemoryExperimentJobStore(),
        factory=lambda: NoContextRunner(),
    )
    run_result, failed, trace_id = await runner._run_single_case(
        candidate=spec,
        case=SimpleNamespace(user_prompt="fallback prompt", tenant_id="tenant-a", capability_id="cap-a"),
    )
    assert failed is False
    assert trace_id == "trace-fallback"
    assert run_result.trace_id == "trace-fallback"
    assert runner._extract_trace_id(None) is None
    assert runner._extract_trace_id(SimpleNamespace(trace_id="direct")) == "direct"
    context = runner._build_context(SimpleNamespace(tenant_id="tenant-a", capability_id="cap-a"))
    assert context.tenant_id == "tenant-a"
    assert context.capability_id == "cap-a"

    class RaisingRunner:
        async def run(self, **kwargs: Any) -> Any:
            raise RuntimeError("boom")

    failed_result = await _make_experiment_runner(
        store=InMemoryExperimentJobStore(),
        factory=lambda: RaisingRunner(),
    )._run_single_case(candidate=spec, case=SimpleNamespace(query="alpha"))
    assert failed_result == (None, True, None)

    class AppendFailStore(InMemoryExperimentJobStore):
        async def append_trace_id(self, job_id: str, trace_id: str) -> StrategyExperimentJob:
            raise RuntimeError("append failed")

    candidate_runner = _make_experiment_runner(
        store=AppendFailStore(),
        eval_runner=_MiniEvalRunner(score_raises=True),
    )
    result, _last, cancelled = await candidate_runner._run_candidate(
        candidate=spec,
        dataset_id="ds-a",
        cases=[SimpleNamespace(query="q1"), SimpleNamespace(query="q2")],
        job_id="job-missing",
        progress=JobProgress(),
        last_progress_perf=0.0,
    )
    assert cancelled is False
    assert result.case_count == 2
    assert result.fatal_failure_count == 2
    assert result.trace_ids == ["trace-extra", "trace-extra"]

    no_arm = await candidate_runner._run_bandit_loop(
        job=_runner_job(),
        mode=SimpleNamespace(),
        candidates=[],
        datasets=["ds-a"],
        limits=ResourceLimits(),
        progress=JobProgress(),
        last_progress_perf=0.0,
    )
    assert no_arm[0] == [] and no_arm[3] is False and no_arm[4] is False

    class EdgeMode:
        def __init__(self) -> None:
            self.calls = 0

        async def next_arm(self, *, experiment_id: str, total_pulls_so_far: int) -> str | None:
            self.calls += 1
            return "unknown" if self.calls == 1 else ("candidate-a" if self.calls == 2 else None)

        async def record_result(self, **kwargs: Any) -> None:
            raise RuntimeError("record failed")

    bandit_store = AppendFailStore()
    bandit_runner = _make_experiment_runner(store=bandit_store)
    results, violations, _last_perf, cancelled, paused = await bandit_runner._run_bandit_loop(
        job=_runner_job(id="job-bandit-extra", experiment_id="experiment-extra"),
        mode=EdgeMode(),
        candidates=[spec],
        datasets=["ds-a"],
        limits=ResourceLimits(),
        progress=JobProgress(),
        last_progress_perf=0.0,
    )
    assert violations == []
    assert cancelled is False
    assert paused is False
    assert len(results) == 1
    assert results[0].candidate_strategy_id == "candidate-a"
    assert results[0].metrics["quality"]["mean"] == pytest.approx(0.8)
    assert results[0].resource_snapshot_ids == ["snap-extra"]

    paused_result = await _make_experiment_runner(
        store=InMemoryExperimentJobStore(),
        collector=_MiniResourceCollector(safe=False),
    )._run_bandit_loop(
        job=_runner_job(),
        mode=EdgeMode(),
        candidates=[spec],
        datasets=["ds-a"],
        limits=ResourceLimits(),
        progress=JobProgress(),
        last_progress_perf=0.0,
    )
    assert paused_result[1] == ["cpu high"]
    assert paused_result[4] is True

    store = InMemoryExperimentJobStore()
    await store.create(_runner_job(id="cancel-bandit", status=JobStatus.CANCELLED))
    cancelled_result = await _make_experiment_runner(store=store)._run_bandit_loop(
        job=_runner_job(id="cancel-bandit"),
        mode=EdgeMode(),
        candidates=[spec],
        datasets=["ds-a"],
        limits=ResourceLimits(),
        progress=JobProgress(),
        last_progress_perf=0.0,
    )
    assert cancelled_result[3] is True


@pytest.mark.asyncio
async def test_experiment_runner_run_job_terminal_failure_pause_and_cancel_paths() -> None:
    """Cover run_job state transitions that are hard to hit in happy-path tests."""
    class UpdateStatusFails(InMemoryExperimentJobStore):
        async def update_status(
            self,
            job_id: str,
            status: JobStatus,
            *,
            error: str | None = None,
        ) -> StrategyExperimentJob:
            if status == JobStatus.RUNNING:
                raise RuntimeError("cannot run")
            return await super().update_status(job_id, status, error=error)

    fail_start_store = UpdateStatusFails()
    await fail_start_store.create(_runner_job(id="fail-start"))
    with pytest.raises(RuntimeError, match="cannot run"):
        await _make_experiment_runner(store=fail_start_store).run_job("fail-start")

    fail_store = InMemoryExperimentJobStore()
    await fail_store.create(_runner_job(id="fail-body"))

    async def exploding_dispatcher(*args: Any, **kwargs: Any) -> list[StrategySpec]:
        raise RuntimeError("candidate build failed")

    failed = await _make_experiment_runner(store=fail_store).run_job(
        "fail-body",
        mode_dispatcher=exploding_dispatcher,
    )
    assert failed.status == JobStatus.FAILED
    assert "candidate build failed" in (failed.error or "")

    async def resolve_candidate(sid: str) -> StrategySpec:
        return _runner_spec(sid)

    pause_store = InMemoryExperimentJobStore()
    await pause_store.create(_runner_job(id="pause-body"))
    paused = await _make_experiment_runner(
        store=pause_store,
        collector=_MiniResourceCollector(safe=False),
        spec_resolver=resolve_candidate,
    ).run_job("pause-body")
    assert paused.status == JobStatus.PAUSED

    cancel_store = InMemoryExperimentJobStore()
    await cancel_store.create(_runner_job(id="cancel-body"))

    class CancelsOnProgress(InMemoryExperimentJobStore):
        pass

    # Use the normal store but cancel from the runner factory after the first case.
    class CancellingRunner(_ContextAwareRunner):
        async def run(self, *, spec: StrategySpec, query: str, context: Any = None) -> StrategyRunResult:
            await cancel_store.update_status("cancel-body", JobStatus.CANCELLED)
            return await super().run(spec=spec, query=query, context=context)

    cancelled = await _make_experiment_runner(
        store=cancel_store,
        factory=lambda: CancellingRunner(),
        spec_resolver=resolve_candidate,
    ).run_job("cancel-body")
    assert cancelled.status == JobStatus.CANCELLED


@pytest.mark.asyncio
async def test_judge_quality_parser_variance_and_rule_scoring_edges() -> None:
    """Cover judge-quality parsing, variance checks, and rule fallback helpers."""
    judge = JudgeQualityExecutor()
    assert judge._parse_judge_response("plain text") is None
    assert judge._parse_judge_response("{}") is None
    assert judge._parse_judge_response('{"dimensions": {"completeness": {"score": 1}}}') is None
    assert judge._parse_judge_response('{"dimensions": {"completeness": {"score": "bad"}}}') is None

    def response_with(score: float) -> str:
        return json.dumps(
            {
                "dimensions": {
                    dim.value: {"score": score, "evidence": f"evidence-{dim.value}"}
                    for dim in DimensionId
                }
            }
        )

    parsed = judge._parse_judge_response(f"```json\n{response_with(1.2)}\n```")
    assert parsed is not None
    assert len(parsed) == len(DimensionId)
    assert all(0.0 <= score.score <= 1.0 for score in parsed)

    class FakeLLM:
        def __init__(self, responses: list[Any]) -> None:
            self.responses = responses

        async def generate(self, **kwargs: Any) -> str:
            item = self.responses.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

    judge.llm_client = FakeLLM([RuntimeError("one bad run")])
    assert await judge._run_with_variance_check(
        "prompt",
        {"provider": "openai", "model": "m"},
        runs=1,
    ) is None

    judge.llm_client = FakeLLM([response_with(0.6)])
    one_run = await judge._run_with_variance_check(
        "prompt",
        {"provider": "openai", "model": "m"},
        runs=1,
    )
    assert one_run is not None and one_run[0].score == pytest.approx(0.6)

    judge.llm_client = FakeLLM([response_with(0.2), response_with(0.9), response_with(0.4)])
    median = await judge._run_with_variance_check(
        "prompt",
        {"provider": "ollama", "model": "m"},
        runs=3,
    )
    assert median is not None and median[0].score == pytest.approx(0.4)

    class Registry:
        def __init__(self, provider: str) -> None:
            self.provider = provider

        def resolve_with_fallback(self, role: str) -> Any:
            return SimpleNamespace(provider=self.provider, model="judge-model", timeout_ms=1000)

    accessor = SimpleNamespace(
        get_query=lambda: "alpha beta",
        get_chunks=lambda: [_chunk(content="x" * 250)],
        get_business_context=lambda: SimpleNamespace(
            language_policy=LanguagePolicy(primary_languages=["en"])
        ),
    )
    judge.llm_client = FakeLLM([response_with(0.4)])
    judge.model_registry = Registry("openai")
    llm_output = await judge._judge_with_llm(
        _node("judge", "judge_quality"),
        accessor,
        _synthesis("alpha answer"),
    )
    assert llm_output is not None
    assert llm_output.output_data["passed"] is False
    assert llm_output.model_used == "openai/judge-model"

    failing = JudgeQualityExecutor(llm_client=FakeLLM([RuntimeError("llm down")]), model_registry=Registry("openai"))
    fallback = await failing.execute(
        _node("judge", "judge_quality", expected_language="en"),
        StrategyRunState(
            query="",
            synthesis_result=SynthesisResult(text="", citations=[]),
        ),
    )
    assert fallback.output_data["passed"] is False

    assert JudgeQualityExecutor._build_chunks_summary([]) == "(No source chunks available)"
    assert "..." in JudgeQualityExecutor._build_chunks_summary([_chunk(content="z" * 250)])
    assert JudgeQualityExecutor._score_completeness("", "text") == 0.0
    assert JudgeQualityExecutor._score_completeness("to be", "anything") == 1.0
    assert JudgeQualityExecutor._score_coherence("") == 0.0
    assert JudgeQualityExecutor._score_coherence("fragment") == pytest.approx(0.3667, rel=1e-3)
    assert JudgeQualityExecutor._score_coherence("one two three four.") < 1.0
    assert JudgeQualityExecutor._score_coherence(("word " * 65) + ".") < 1.0
    assert JudgeQualityExecutor._score_coherence("This is complete...") < 1.0
    assert JudgeQualityExecutor._score_citation_density("", []) == 0.0
    assert JudgeQualityExecutor._score_citation_density("word " * 100, []) == 0.2
    assert JudgeQualityExecutor._score_citation_density("word " * 100, [object()]) == 1.0
    assert JudgeQualityExecutor._score_language("", "en") == 1.0
    assert JudgeQualityExecutor._score_language("alpha beta", "en") == 0.8
    assert JudgeQualityExecutor._score_language("der die das und", "en") == 0.3
    assert JudgeQualityExecutor._score_language("the and is for", "en") == 1.0
    assert JudgeQualityExecutor._score_language("the and is for", "fr") == 0.8

    no_policy_accessor = SimpleNamespace(get_business_context=lambda: None)
    assert JudgeQualityExecutor._get_expected_language(
        no_policy_accessor,
        _node("judge", "judge_quality", expected_language="de"),
    ) == "de"
