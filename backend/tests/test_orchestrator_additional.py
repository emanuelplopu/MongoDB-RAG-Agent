"""Additional coverage tests for the orchestrator."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.agent.orchestrator import Orchestrator
from backend.agent.schemas import (
    DataSourceType,
    DocumentReference,
    OrchestratorPhase,
    TaskType,
    WebReference,
    WorkerResult,
)


class TestOrchestratorAdditional:
    """Additional behavioral tests for orchestrator helpers."""

    @pytest.mark.parametrize(
        ("provider", "model", "expected"),
        [
            ("openai", "gpt-4o", "gpt-4o"),
            ("google", "gemini-2.5-flash", "gemini/gemini-2.5-flash"),
            ("gemini", "gemini/custom", "gemini/custom"),
            ("anthropic", "claude-3-5-sonnet", "anthropic/claude-3-5-sonnet"),
            ("claude", "anthropic/claude-3-5-haiku", "anthropic/claude-3-5-haiku"),
            ("ollama", "llama3.2", "ollama/llama3.2"),
        ],
    )
    def test_get_model_string(self, provider, model, expected):
        """Model strings should be normalized for the configured provider."""
        orchestrator = Orchestrator(model=model, provider=provider)

        assert orchestrator._get_model_string() == expected

    def test_get_prompt_uses_strategy_when_available(self):
        """Strategy prompts should take precedence over defaults."""
        strategy = SimpleNamespace(
            get_analyze_prompt=lambda: "analyze prompt",
            get_plan_prompt=lambda: "plan prompt",
            get_evaluate_prompt=lambda: "evaluate prompt",
            get_synthesize_prompt=lambda: "synthesize prompt",
        )
        orchestrator = Orchestrator(strategy=strategy)

        assert orchestrator._get_prompt("analyze") == "analyze prompt"
        assert orchestrator._get_prompt("plan") == "plan prompt"

    def test_extract_relevant_context_includes_recent_and_matching_earlier_messages(self):
        """Context extraction should include recent and relevant earlier history."""
        orchestrator = Orchestrator()

        history = [
            {"role": "user", "content": "Please review the accounting migration timeline and deployment plan."},
            {"role": "assistant", "content": "I found a deployment checklist for the migration."},
            {"role": "user", "content": "Unrelated weather chat."},
            {"role": "assistant", "content": "Recent response one."},
            {"role": "user", "content": "Recent response two."},
        ]

        context = orchestrator._extract_relevant_context(
            "What is the deployment timeline for the accounting migration?",
            history,
        )

        assert "[Earlier relevant] user:" in context
        assert "Recent response one." in context
        assert "Recent response two." in context

    def test_extract_relevant_context_handles_empty_history(self):
        """Empty history should return the explicit placeholder."""
        orchestrator = Orchestrator()

        assert orchestrator._extract_relevant_context("query", []) == "(No previous messages)"

    def test_flatten_entities_deduplicates_lists(self):
        """Entity flattening should keep only unique list entries."""
        orchestrator = Orchestrator()

        entities = orchestrator._flatten_entities(
            {
                "people": ["Ada Lovelace", "Grace Hopper"],
                "documents": ["Spec", "Spec"],
                "invalid": "ignored",
            }
        )

        assert sorted(entities) == ["Ada Lovelace", "Grace Hopper", "Spec"]

    @pytest.mark.asyncio
    async def test_call_llm_parses_json_and_records_step(self, monkeypatch):
        """Successful JSON responses should be parsed and recorded."""
        orchestrator = Orchestrator(model="gpt-4o", provider="openai")
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='```json\n{"answer":"ok","reasoning":"why"}\n```'))],
            usage=SimpleNamespace(total_tokens=321),
        )
        mocked_completion = AsyncMock(return_value=response)

        monkeypatch.setattr("litellm.acompletion", mocked_completion)
        monkeypatch.setattr(
            "backend.core.config.BackendSettings.get_orchestrator_api_key",
            lambda self: "orchestrator-key",
            raising=False,
        )

        result = await orchestrator._call_llm("prompt", OrchestratorPhase.ANALYZE)

        assert result["answer"] == "ok"
        assert orchestrator.steps[-1].tokens_used == 321
        assert mocked_completion.await_args.kwargs["max_completion_tokens"] == 2000

    @pytest.mark.asyncio
    async def test_call_llm_uses_text_response_and_records_failures(self, monkeypatch):
        """Non-JSON and failing calls should be handled predictably."""
        orchestrator = Orchestrator(model="claude-3-5-haiku", provider="anthropic")

        non_json_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="plain text response"))],
            usage=None,
        )
        monkeypatch.setattr("litellm.acompletion", AsyncMock(return_value=non_json_response))
        monkeypatch.setattr(
            "backend.core.config.BackendSettings.get_orchestrator_api_key",
            lambda self: "orchestrator-key",
            raising=False,
        )

        result = await orchestrator._call_llm("prompt", OrchestratorPhase.SYNTHESIZE, expect_json=False)
        assert result == {"response": "plain text response"}

        monkeypatch.setattr("litellm.acompletion", AsyncMock(side_effect=RuntimeError("llm down")))
        with pytest.raises(RuntimeError, match="llm down"):
            await orchestrator._call_llm("prompt", OrchestratorPhase.PLAN)

        assert orchestrator.steps[-1].output_summary == "Failed"

    @pytest.mark.asyncio
    async def test_analyze_applies_defaults_without_strategy(self):
        """Analyze should backfill the expected schema defaults."""
        orchestrator = Orchestrator()
        orchestrator._get_prompt = lambda phase: "{user_message}|{conversation_history}"
        orchestrator._call_llm = AsyncMock(return_value={"named_entities": {"people": ["Ada"]}})

        result = await orchestrator.analyze(
            "Explain the migration plan",
            conversation_history=[{"role": "user", "content": "Earlier migration note"}],
        )

        assert result["intent_summary"] == "Explain the migration plan"
        assert result["query_type"] == "FACTUAL"
        assert result["key_entities"] == ["Ada"]
        assert result["requires_multiple_searches"] is True

    @pytest.mark.asyncio
    async def test_plan_uses_primary_search_query_and_adds_safety_document_search(self):
        """Planning should use optimized queries and add a doc-search safety net."""
        orchestrator = Orchestrator()
        orchestrator._call_llm = AsyncMock(
            return_value={
                "intent_summary": "Look up release notes",
                "reasoning": "Search the web first",
                "strategy": "parallel",
                "tasks": [
                    {
                        "id": "web-1",
                        "type": "web_search",
                        "query": "Look up release notes",
                        "priority": 1,
                    }
                ],
                "success_criteria": "Find the release notes",
                "max_iterations": 2,
            }
        )

        plan = await orchestrator.plan(
            analysis={
                "intent_summary": "Look up release notes",
                "search_queries": {"primary": "release notes 2026", "alternatives": []},
            },
            available_sources=[{"id": "profile_default", "display_name": "Default", "type": "profile"}],
        )

        assert plan.tasks[0].query == "release notes 2026"
        assert any(task.type == TaskType.SEARCH_ALL for task in plan.tasks)

    def test_create_default_tasks_covers_web_and_alternatives(self):
        """Default task creation should include document and alternative searches."""
        orchestrator = Orchestrator()

        tasks = orchestrator._create_default_tasks(
            {
                "intent_summary": "research deployment",
                "search_queries": {
                    "primary": "deployment checklist",
                    "alternatives": ["release playbook"],
                },
                "source_priority": "web",
                "requires_multi_hop": True,
            }
        )

        task_types = [task.type for task in tasks]
        assert task_types[0] == TaskType.WEB_SEARCH
        assert TaskType.SEARCH_ALL in task_types
        assert any(task.query == "release playbook" for task in tasks)

    @pytest.mark.asyncio
    async def test_evaluate_parses_follow_up_tasks(self):
        """Evaluation should parse follow-up tasks into typed models."""
        orchestrator = Orchestrator()
        orchestrator._call_llm = AsyncMock(
            return_value={
                "findings_summary": "Missing product details",
                "gaps_identified": ["Need cloud docs"],
                "decision": "need_refinement",
                "follow_up_tasks": [
                    {
                        "id": "follow-1",
                        "type": "search_cloud",
                        "query": "product launch memo",
                        "priority": 2,
                    }
                ],
                "reasoning": "We need more evidence",
                "confidence": 0.6,
            }
        )

        decision = await orchestrator.evaluate(
            plan=SimpleNamespace(intent_summary="Need product details", success_criteria="Find product details"),
            results=[],
            iteration=2,
        )

        assert decision.phase == "refinement"
        assert decision.decision == "need_refinement"
        assert decision.follow_up_tasks[0].type == TaskType.SEARCH_CLOUD

    @pytest.mark.asyncio
    async def test_synthesize_returns_model_response_or_fallback(self):
        """Synthesis should return the model answer, then fall back when empty."""
        orchestrator = Orchestrator()
        orchestrator._call_llm = AsyncMock(side_effect=[{"response": "Final answer"}, {"response": "   "}])

        result = await orchestrator.synthesize("question", [], language="de")
        assert result == "Final answer"

        fallback = await orchestrator.synthesize(
            "question",
            [
                WorkerResult(
                    task_id="task-1",
                    task_type=TaskType.SEARCH_ALL,
                    query="question",
                    documents_found=[
                        DocumentReference(
                            id="doc-1",
                            document_id="d1",
                            title="Architecture Notes",
                            source_type=DataSourceType.PROFILE,
                            source_database="rag_default",
                            excerpt="A compact excerpt",
                            full_content="A full explanation of the architecture",
                            similarity_score=0.9,
                        )
                    ],
                    web_links_found=[
                        WebReference(
                            url="https://example.com",
                            title="Example",
                            excerpt="Web excerpt",
                            fetched_content="Fetched web content",
                        )
                    ],
                )
            ],
        )

        assert "Architecture Notes" in fallback
        assert "Based on the search results" in fallback

    def test_format_results_for_prompt_includes_documents_and_links(self):
        """Prompt formatting should include task summaries and top references."""
        orchestrator = Orchestrator()
        formatted = orchestrator._format_results_for_prompt(
            [
                WorkerResult(
                    task_id="task-1",
                    task_type=TaskType.SEARCH_ALL,
                    query="deployment plan",
                    documents_found=[
                        DocumentReference(
                            id="doc-1",
                            document_id="d1",
                            title="Plan",
                            source_type=DataSourceType.PROFILE,
                            source_database="rag_default",
                            excerpt="Deployment plan excerpt",
                            similarity_score=0.8,
                        )
                    ],
                    web_links_found=[
                        WebReference(url="https://example.com", title="Example", excerpt="Link excerpt")
                    ],
                )
            ]
        )

        assert "Task task-1" in formatted
        assert "Plan" in formatted
        assert "https://example.com" in formatted
