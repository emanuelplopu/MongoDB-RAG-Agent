"""Additional backend agent coverage tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import backend.agent as agent_module
from backend.agent.coordinator import FederatedAgent
from backend.agent.federated_search import FederatedSearch
from backend.agent.schemas import (
    AgentModeConfig,
    DataSourceType,
    DocumentReference,
    ResultQuality,
    StrategySelection,
    TaskType,
    WebReference,
    WorkerResult,
)


class TestAgentModuleLazyImports:
    """Tests for lazy exports from ``backend.agent``."""

    def test_known_exports_are_resolved_lazily(self):
        """Resolve known lazy exports without importing them eagerly."""
        assert agent_module.WorkerPool.__name__ == "WorkerPool"
        assert agent_module.FederatedSearch.__name__ == "FederatedSearch"
        assert agent_module.Orchestrator.__name__ == "Orchestrator"
        assert agent_module.FederatedAgent.__name__ == "FederatedAgent"

    def test_unknown_export_raises_attribute_error(self):
        """Unknown lazy exports should raise AttributeError."""
        with pytest.raises(AttributeError):
            _ = agent_module.DoesNotExist


class TestFederatedSearchAdditional:
    """Additional coverage for federated search helpers."""

    def test_apply_rrf_uses_strategy_when_available(self):
        """A provided strategy should short-circuit default RRF scoring."""
        search = FederatedSearch()
        strategy = SimpleNamespace(calculate_rrf_scores=lambda results, limit: [{"chunk_id": "custom"}])

        merged = search._apply_rrf(
            results=[{"chunk_id": "vector-1", "search_type": "vector", "content": "x" * 250}],
            limit=5,
            strategy=strategy,
        )

        assert merged == [{"chunk_id": "custom"}]

    def test_apply_rrf_falls_back_when_strategy_errors(self):
        """Strategy failures should fall back to the default RRF implementation."""
        search = FederatedSearch()
        strategy = SimpleNamespace(
            calculate_rrf_scores=lambda results, limit: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        merged = search._apply_rrf(
            results=[
                {
                    "chunk_id": "shared-doc",
                    "search_type": "vector",
                    "content": "x" * 300,
                    "similarity": 0.9,
                },
                {
                    "chunk_id": "shared-doc",
                    "search_type": "text",
                    "content": "x" * 300,
                    "similarity": 0.7,
                },
                {
                    "chunk_id": "text-only",
                    "search_type": "text",
                    "content": "tiny",
                    "similarity": 0.8,
                },
            ],
            limit=5,
            strategy=strategy,
        )

        assert len(merged) == 2
        assert merged[0]["chunk_id"] == "shared-doc"
        assert merged[0]["cross_match"] is True
        assert 0.0 <= merged[0]["rrf_score"] <= 1.0
        assert merged[1]["chunk_id"] == "text-only"
        assert merged[1]["cross_match"] is False


class TestFederatedAgentAdditional:
    """Additional coverage for coordinator helpers."""

    @pytest.fixture
    def bare_agent(self) -> FederatedAgent:
        """Create a coordinator instance without running the heavy constructor."""
        agent = object.__new__(FederatedAgent)
        agent.config = AgentModeConfig(worker_model="gpt-4o-mini")
        return agent

    def test_resolve_strategy_prefers_direct_instance(self, bare_agent):
        """A directly provided strategy instance should win immediately."""
        direct_strategy = SimpleNamespace(metadata=SimpleNamespace(id="direct"))

        resolved = bare_agent._resolve_strategy(direct_strategy, strategy_id=None)

        assert resolved is direct_strategy

    def test_resolve_strategy_uses_strategy_id(self, bare_agent, monkeypatch):
        """Explicit strategy ids should be resolved through the registry."""
        strategy = SimpleNamespace(metadata=SimpleNamespace(id="legal"))
        monkeypatch.setattr(
            "backend.agent.coordinator.StrategyRegistry.get",
            lambda strategy_id: strategy if strategy_id == "legal" else None,
        )

        resolved = bare_agent._resolve_strategy(None, strategy_id="legal")

        assert resolved is strategy

    def test_resolve_strategy_uses_default_when_override_fails(self, bare_agent, monkeypatch):
        """The default registry strategy should be used as a final fallback."""
        bare_agent.config.strategy_override = "missing"
        bare_agent.config.strategy = StrategySelection.AUTO
        default_strategy = SimpleNamespace(metadata=SimpleNamespace(id="default"))

        def fake_get(strategy_id: str):
            raise KeyError(strategy_id)

        monkeypatch.setattr("backend.agent.coordinator.StrategyRegistry.get", fake_get)
        monkeypatch.setattr("backend.agent.coordinator.StrategyRegistry.get_default", lambda: default_strategy)

        resolved = bare_agent._resolve_strategy(None, strategy_id=None)

        assert resolved is default_strategy

    def test_resolve_strategy_raises_when_all_options_fail(self, bare_agent, monkeypatch):
        """Strategy resolution should surface a helpful runtime error when exhausted."""
        bare_agent.config.strategy_override = "missing"
        bare_agent.config.strategy = StrategySelection.LEGAL

        def fake_get(_strategy_id: str):
            raise KeyError("missing")

        def fake_get_default():
            raise RuntimeError("no default")

        monkeypatch.setattr("backend.agent.coordinator.StrategyRegistry.get", fake_get)
        monkeypatch.setattr("backend.agent.coordinator.StrategyRegistry.get_default", fake_get_default)

        with pytest.raises(RuntimeError, match="Could not resolve any strategy"):
            bare_agent._resolve_strategy(None, strategy_id="missing-id")

    @pytest.mark.parametrize(
        ("provider", "model", "expected"),
        [
            ("openai", "gpt-4o-mini", "gpt-4o-mini"),
            ("google", "gemini-2.5-flash", "gemini/gemini-2.5-flash"),
            ("gemini", "gemini/custom", "gemini/custom"),
            ("anthropic", "claude-3-5-haiku", "anthropic/claude-3-5-haiku"),
            ("claude", "anthropic/claude-3-5-sonnet", "anthropic/claude-3-5-sonnet"),
            ("ollama", "llama3.2", "ollama/llama3.2"),
        ],
    )
    def test_get_worker_model_string(self, bare_agent, monkeypatch, provider, model, expected):
        """Worker model names should be normalized for the active provider."""
        bare_agent.config.worker_model = model
        monkeypatch.setattr("backend.agent.coordinator.settings.worker_provider", provider)

        assert bare_agent._get_worker_model_string() == expected

    @pytest.mark.asyncio
    async def test_generate_fast_response_uses_context_and_language(self, bare_agent, monkeypatch):
        """Fast response generation should compile document and web context."""
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Antwort auf Deutsch"))]
        )
        mocked_completion = AsyncMock(return_value=completion)

        monkeypatch.setattr("litellm.acompletion", mocked_completion)
        monkeypatch.setattr(
            "backend.agent.coordinator.get_agent_prompt_sync",
            lambda prompt_key: "Question: {user_message}\n\nContext:\n{context}",
        )
        monkeypatch.setattr("backend.agent.coordinator.settings.worker_provider", "openai")
        monkeypatch.setattr(
            "backend.core.config.BackendSettings.get_worker_api_key",
            lambda self: "test-key",
            raising=False,
        )

        result = await bare_agent._generate_fast_response(
            user_message="Was steht im Dokument?",
            language="de",
            results=[
                WorkerResult(
                    task_id="task-1",
                    task_type=TaskType.SEARCH_ALL,
                    query="query",
                    documents_found=[
                        DocumentReference(
                            id="doc-1",
                            document_id="d1",
                            title="Handbuch",
                            source_type=DataSourceType.PROFILE,
                            source_database="rag_test",
                            excerpt="Kurzfassung",
                            full_content="Ausfuehrlicher Inhalt",
                            similarity_score=0.9,
                        )
                    ],
                    web_links_found=[
                        WebReference(
                            url="https://example.com",
                            title="Example",
                            excerpt="Referenced web content",
                        )
                    ],
                    result_quality=ResultQuality.GOOD,
                )
            ],
        )

        assert result == "Antwort auf Deutsch"
        completion_kwargs = mocked_completion.await_args.kwargs
        assert completion_kwargs["model"] == "gpt-4o-mini"
        assert completion_kwargs["api_key"] == "test-key"
        assert "Handbuch" in completion_kwargs["messages"][0]["content"]
        assert "Referenced web content" in completion_kwargs["messages"][0]["content"]
        assert "MUST write your entire response in German" in completion_kwargs["messages"][0]["content"]
        assert completion_kwargs["max_completion_tokens"] == 1500

    @pytest.mark.asyncio
    async def test_generate_fast_response_returns_error_message_on_failure(self, bare_agent, monkeypatch):
        """LLM failures should return a user-readable fallback message."""
        monkeypatch.setattr("litellm.acompletion", AsyncMock(side_effect=RuntimeError("provider offline")))
        monkeypatch.setattr(
            "backend.agent.coordinator.get_agent_prompt_sync",
            lambda prompt_key: "Question: {user_message}\nContext: {context}",
        )
        monkeypatch.setattr("backend.agent.coordinator.settings.worker_provider", "openai")
        monkeypatch.setattr(
            "backend.core.config.BackendSettings.get_worker_api_key",
            lambda self: "test-key",
            raising=False,
        )

        result = await bare_agent._generate_fast_response(
            user_message="hello",
            language=None,
            results=[],
        )

        assert "provider offline" in result

    @pytest.mark.asyncio
    async def test_cleanup_delegates_to_worker_pool(self, bare_agent):
        """Cleanup should delegate resource disposal to the worker pool."""
        bare_agent.worker_pool = SimpleNamespace(cleanup=AsyncMock())

        await bare_agent.cleanup()

        bare_agent.worker_pool.cleanup.assert_awaited_once()
