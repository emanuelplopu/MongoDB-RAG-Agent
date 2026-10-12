"""Orchestrator robustness: loop guard, JSON parsing and retry, query repair, fallbacks, Ollama options."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.agent import orchestrator as orch_module
from backend.agent.orchestrator import (
    Orchestrator,
    _clean_query,
    _detect_repetition,
    _extract_json,
    _trim_repetition,
)
from backend.agent.schemas import (
    AgentMode,
    AgentTrace,
    DataSourceType,
    DocumentReference,
    OrchestratorPhase,
    TaskType,
    WorkerResult,
)


def _response(content: str, prompt_tokens: int = 100, completion_tokens: int = 50) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                              total_tokens=prompt_tokens + completion_tokens),
    )


def _doc(i: int = 1) -> DocumentReference:
    return DocumentReference(id=f"c{i}", document_id=f"d{i}", title=f"Doc {i}", source_type=DataSourceType.PROFILE,
                             source_database="rag_test", excerpt="Auszug", similarity_score=0.8)


@pytest.fixture
def ollama(monkeypatch):
    """An Ollama orchestrator whose model supports thinking; returns the acompletion mock."""
    monkeypatch.setattr("backend.agent.ollama_options.supports_thinking", AsyncMock(return_value=True))
    completion = AsyncMock()
    monkeypatch.setattr("litellm.acompletion", completion)
    return completion


# ── loop guard ───────────────────────────────────────────────────────────────


def test_indented_json_and_cited_answers_are_not_loops() -> None:
    plan = json.dumps({"tasks": [{"id": f"t{i}", "type": "search_profile", "query": f"Frage {i}",
                                  "sources": ["profile_synthetic-kanzlei"], "priority": 1} for i in range(8)]}, indent=4)
    assert not _detect_repetition(plan, check_lines=False)
    cite = '[Source: "2025-07-08 Gerichtlicher Vergleich zwischen Kocher und Windisch.pdf"]'
    answer = "\n".join(f"- Punkt {i}: Die Kosten wurden gegenseitig aufgehoben ({i * 100} EUR).\n  {cite}" for i in range(6))
    assert not _detect_repetition(answer)


def test_looping_output_is_detected_and_trimmed_at_the_loop() -> None:
    text = "Die Kündigungsentschädigung beträgt EUR 16.380,00 [Source: Klage]. " + "und so weiter " * 40
    assert _detect_repetition(text)
    trimmed = _trim_repetition(text)
    assert trimmed.startswith("Die Kündigungsentschädigung beträgt EUR 16.380,00")
    assert trimmed.count("und so weiter") == 1 and trimmed.endswith("[truncated: repetitive output detected]")
    lines = "Einleitung.\n" + "\n".join(["Das Gericht hat die Klage im Umfang von EUR 1.700,00 abgewiesen."] * 8)
    assert _detect_repetition(lines)
    assert _trim_repetition(lines).count("abgewiesen") == 1


def test_extract_json_handles_fences_prose_and_garbage() -> None:
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('Hier ist der Plan: {"tasks": []} Ende.') == {"tasks": []}
    assert _extract_json('{"a": 1') is None and _extract_json("") is None and _extract_json("[1, 2]") is None


def test_clean_query_strips_leaked_json_syntax() -> None:
    assert _clean_query('",\n          "Terminplan Tom Erhart Puchner Baumeister"') == '"Terminplan Tom Erhart Puchner Baumeister"'
    assert _clean_query('", "vorbereitende Tagsatzung" Datum"') == "vorbereitende Tagsatzung Datum"
    assert _clean_query('"Lea-Sophie Kocher" Kündigungsentschädigung') == '"Lea-Sophie Kocher" Kündigungsentschädigung'
    assert _clean_query("") == "" and _clean_query(None) == "" and _clean_query('",') == ""


# ── LLM call: options, retry, telemetry ──────────────────────────────────────


@pytest.mark.asyncio
async def test_json_phase_sends_ollama_options_without_thinking(ollama, monkeypatch) -> None:
    monkeypatch.setattr(orch_module.settings, "agent_ollama_num_ctx", 32768)
    monkeypatch.setattr(orch_module.settings, "agent_ollama_think_phases", "synthesize")
    ollama.return_value = _response('{"intent_summary": "x"}')
    o = Orchestrator(model="gemma4:12b", provider="ollama")
    assert (await o._call_llm("prompt", OrchestratorPhase.ANALYZE)) == {"intent_summary": "x"}
    kw = ollama.await_args.kwargs
    assert kw["num_ctx"] == 32768 and kw["reasoning_effort"] == "none" and kw["repeat_penalty"] == 1.0
    assert kw["response_format"] == {"type": "json_object"} and "stop" not in kw
    step = o.steps[-1]
    assert step.request_options["think"] is False and step.request_options["json_mode"] is True
    assert step.prompt_tokens == 100 and step.completion_tokens == 50 and not step.parse_error

    ollama.return_value = _response("Antwort.")
    await o._call_llm("prompt", OrchestratorPhase.SYNTHESIZE, expect_json=False)
    kw = ollama.await_args.kwargs
    assert "reasoning_effort" not in kw and "response_format" not in kw and kw["stop"]
    assert o.steps[-1].request_options["think"] is True


@pytest.mark.asyncio
async def test_unparsable_json_is_retried_once_and_recorded(ollama) -> None:
    ollama.side_effect = [_response("Ich plane zwei Suchen."), _response('{"tasks": []}')]
    o = Orchestrator(model="gemma4:12b", provider="ollama")
    assert (await o._call_llm("prompt", OrchestratorPhase.PLAN)) == {"tasks": []}
    assert [s.attempt for s in o.steps] == [1, 2]
    assert o.steps[0].parse_error and o.steps[0].raw_output == "Ich plane zwei Suchen." and not o.steps[1].parse_error
    assert o.degradations == ["plan_json_retry"]

    ollama.side_effect = [_response("kaputt"), _response("immer noch kaputt")]
    o = Orchestrator(model="gemma4:12b", provider="ollama")
    result = await o._call_llm("prompt", OrchestratorPhase.ANALYZE)
    assert result["parse_error"] is True and o.degradations == ["analyze_parse_failed"]


@pytest.mark.asyncio
async def test_token_limit_and_trimmed_synthesis_are_flagged(ollama) -> None:
    ollama.return_value = _response('{"decision": "sufficient"}', completion_tokens=1500)
    o = Orchestrator(model="gemma4:12b", provider="ollama")
    await o._call_llm("prompt", OrchestratorPhase.EVALUATE)
    assert o.steps[-1].hit_token_limit and "evaluate_token_limit" in o.degradations
    assert o.steps[-1].hidden_tokens_est > 1400

    loop = "Die Antwort lautet EUR 205,06. " + "Quelle Quelle Quelle " * 30
    ollama.return_value = _response(loop)
    result = await o._call_llm("prompt", OrchestratorPhase.SYNTHESIZE, expect_json=False)
    assert result["response"].startswith("Die Antwort lautet EUR 205,06.")
    assert o.steps[-1].output_truncated and o.steps[-1].raw_output == loop
    assert "synthesize_repetition_trimmed" in o.degradations


# ── phases ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_never_leaves_an_empty_primary_query() -> None:
    strategy = SimpleNamespace(process_analysis=lambda r: {**r, "intent_summary": "", "search_queries": {"primary": ""}})
    o = Orchestrator(strategy=strategy)
    o._get_prompt = lambda phase: "{user_message}|{conversation_history}"
    o._call_llm = AsyncMock(return_value={"response": "kaputt", "parse_error": True})
    result = await o.analyze("Wann endete die Berufungsfrist für Tom Erhart?")
    assert result["search_queries"]["primary"] == "Wann endete die Berufungsfrist für Tom Erhart?"
    assert result["intent_summary"] and o.degradations == ["analyze_defaults_used"]


@pytest.mark.asyncio
async def test_plan_repairs_queries_and_anchors_the_question(monkeypatch) -> None:
    monkeypatch.setattr(orch_module.settings, "agent_anchor_raw_query", True)
    o = Orchestrator()
    o._user_message = "Wann endete die Berufungsfrist für Tom Erhart?"
    o._call_llm = AsyncMock(return_value={"tasks": [
        {"id": "a", "type": "search_profile", "query": '",\n   "Terminplan Tom Erhart"'},
        {"id": "b", "type": "search_all", "query": ""},
    ]})
    analysis = {"intent_summary": "Frist", "search_queries": {"primary": "Berufungsfrist Tom Erhart"}}
    plan = await o.plan(analysis, available_sources=[])
    queries = [t.query for t in plan.tasks]
    assert queries[0] == '"Terminplan Tom Erhart"' and queries[1] == "Berufungsfrist Tom Erhart"
    assert plan.tasks[-1].id == "anchor_user_question" and plan.tasks[-1].type == TaskType.SEARCH_ALL
    assert queries[-1] == o._user_message
    assert o.degradations == ["plan_query_repaired", "plan_query_empty"]


@pytest.mark.asyncio
async def test_plan_without_tasks_falls_back_to_the_question() -> None:
    o = Orchestrator()
    o._user_message = "Welche Kosten wurden vereinbart?"
    o._call_llm = AsyncMock(return_value={"response": "kaputt", "parse_error": True})
    plan = await o.plan({"intent_summary": "", "search_queries": {"primary": ""}, "source_priority": "profile"},
                        available_sources=[])
    assert plan.tasks[0].query == "Welche Kosten wurden vereinbart?"
    assert sum(t.query == o._user_message for t in plan.tasks) == len(plan.tasks)
    assert "plan_default_tasks" in o.degradations


@pytest.mark.asyncio
async def test_unreadable_evaluation_decides_from_results() -> None:
    o = Orchestrator()
    o._call_llm = AsyncMock(return_value={"response": "", "parse_error": True})
    plan = SimpleNamespace(intent_summary="i", success_criteria="s")
    found = [WorkerResult(task_id="t", task_type=TaskType.SEARCH_ALL, query="q", documents_found=[_doc()])]
    d = await o.evaluate(plan, found, 1)
    assert d.decision == "sufficient" and d.confidence == 0.0
    d = await o.evaluate(plan, [WorkerResult(task_id="t", task_type=TaskType.SEARCH_ALL, query="q")], 2)
    assert d.decision == "cannot_answer"
    assert o.degradations == ["evaluate_fallback_sufficient", "evaluate_fallback_cannot_answer"]


@pytest.mark.asyncio
async def test_odd_evaluation_values_are_coerced() -> None:
    o = Orchestrator()
    o._call_llm = AsyncMock(return_value={"decision": "Insufficient", "confidence": "hoch", "gaps_identified": "keine"})
    d = await o.evaluate(SimpleNamespace(intent_summary="i", success_criteria="s"), [], 1)
    assert d.decision == "need_refinement" and d.confidence == 0.5 and d.gaps_identified == []
    assert o.degradations == ["evaluate_invalid_decision", "evaluate_invalid_confidence"]


def test_trace_exposes_degradations_and_step_telemetry() -> None:
    from backend.agent.schemas import OrchestratorStep

    trace = AgentTrace(orchestrator_model="gemma4:12b", worker_model="gemma4:e4b", mode=AgentMode.THINKING)
    trace.add_orchestrator_step(OrchestratorStep(phase=OrchestratorPhase.PLAN, model="gemma4:12b", input_summary="p",
                                                 reasoning="r", output_summary="o", parse_error=True, attempt=2,
                                                 request_options={"think": False}, raw_output="kaputt"))
    trace.degradations.append("plan_json_retry")
    d = trace.to_response_dict()
    assert d["degradations"] == ["plan_json_retry"]
    step = d["orchestrator_steps"][0]
    assert step["parse_error"] and step["attempt"] == 2 and step["request_options"] == {"think": False}
    assert step["raw_output"] == "kaputt"


@pytest.mark.asyncio
async def test_search_checks_for_data_without_counting_every_chunk() -> None:
    from unittest.mock import MagicMock

    from backend.agent.federated_search import FederatedSearch

    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=None)
    collection.count_documents = AsyncMock(return_value=0)
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection)
    fs = FederatedSearch()
    fs._client = MagicMock()
    fs._client.__getitem__ = MagicMock(return_value=db)
    assert await fs._search_database("rag_test", "chunks", "documents", "Frage", [0.1] * 8) == []
    collection.find_one.assert_awaited_once_with({}, {"_id": 1})
    collection.count_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_thinking_phases_get_extra_output_budget(ollama, monkeypatch) -> None:
    monkeypatch.setattr(orch_module.settings, "agent_ollama_think_phases", "synthesize")
    monkeypatch.setattr(orch_module.settings, "agent_ollama_think_budget", 4096)
    o = Orchestrator(model="gemma4:12b", provider="ollama")
    ollama.return_value = _response('{"a": 1}')
    await o._call_llm("prompt", OrchestratorPhase.PLAN)
    assert ollama.await_args.kwargs["max_tokens"] == 2000 and o.steps[-1].max_tokens == 2000
    ollama.return_value = _response("Antwort.", completion_tokens=4500)
    await o._call_llm("prompt", OrchestratorPhase.SYNTHESIZE, expect_json=False)
    assert ollama.await_args.kwargs["max_tokens"] == 8096 and o.steps[-1].max_tokens == 8096
    assert not o.steps[-1].hit_token_limit and o.steps[-1].hidden_tokens_est > 4000
