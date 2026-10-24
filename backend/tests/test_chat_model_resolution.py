"""Legacy chat route: the LiteLLM model string follows ``LLM_PROVIDER``.

``POST /api/v1/chat`` used to hand the bare ``LLM_MODEL`` (``gemma4:12b``) to
LiteLLM, which then failed with "LLM Provider NOT provided" on an Ollama-only
deployment while the sessions API, which prefixes the provider, kept working.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.core.config import settings
from backend.routers.chat import resolve_chat_model


OLLAMA_URL = "http://host.docker.internal:11434"


def _llm_response(text: str) -> SimpleNamespace:
    """Mimic the parts of a LiteLLM ``ModelResponse`` the chat loop reads."""
    message = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(total_tokens=12, prompt_tokens=10, completion_tokens=2)
    return SimpleNamespace(choices=[choice], usage=usage)


def _configure(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)


class TestResolveChatModel:
    """Unit tests for the provider-to-prefix mapping."""

    def test_ollama_provider_prefixes_bare_model(self, monkeypatch: pytest.MonkeyPatch):
        _configure(monkeypatch, llm_provider="ollama", llm_model="gemma4:12b")
        assert resolve_chat_model() == ("ollama/gemma4:12b", "ollama")

    def test_openai_provider_passes_model_through(self, monkeypatch: pytest.MonkeyPatch):
        _configure(monkeypatch, llm_provider="openai", llm_model="gpt-5.2")
        assert resolve_chat_model() == ("gpt-5.2", "openai")

    def test_explicit_prefix_wins_over_provider_setting(self, monkeypatch: pytest.MonkeyPatch):
        _configure(monkeypatch, llm_provider="openai", llm_model="gpt-5.2")
        assert resolve_chat_model("ollama/gemma4:12b") == ("ollama/gemma4:12b", "ollama")

    def test_unknown_provider_keeps_model_unchanged(self, monkeypatch: pytest.MonkeyPatch):
        # The .env.example default: OpenRouter with an already-prefixed model name.
        _configure(monkeypatch, llm_provider="openrouter", llm_model="anthropic/claude-haiku-4.5")
        assert resolve_chat_model() == ("anthropic/claude-haiku-4.5", "anthropic")

        _configure(monkeypatch, llm_provider="openrouter", llm_model="some-model")
        assert resolve_chat_model() == ("some-model", "openrouter")

    def test_provider_name_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch):
        _configure(monkeypatch, llm_provider="Ollama", llm_model="gemma4:12b")
        assert resolve_chat_model() == ("ollama/gemma4:12b", "ollama")


class TestLegacyChatRouteProvider:
    """``POST /api/v1/chat`` calls LiteLLM with the provider-prefixed model."""

    def test_ollama_model_is_prefixed_and_uses_ollama_base_url(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        _configure(
            monkeypatch,
            llm_provider="ollama",
            llm_model="gemma4:12b",
            llm_api_key="",
            llm_base_url=OLLAMA_URL,
            ollama_base_url=OLLAMA_URL,
        )
        acompletion = AsyncMock(return_value=_llm_response("Servus aus Ollama"))

        with patch("litellm.acompletion", acompletion):
            response = client.post(
                "/api/v1/chat",
                json={"message": "Hallo", "conversation_id": "chat-ollama-prefix-test"},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["message"] == "Servus aus Ollama"
        assert "LLM Provider NOT provided" not in body["message"]

        acompletion.assert_awaited_once()
        kwargs = acompletion.await_args.kwargs
        assert kwargs["model"] == "ollama/gemma4:12b"
        assert kwargs["api_base"] == OLLAMA_URL
        assert kwargs["api_key"] == ""

    def test_openai_model_is_passed_through_unchanged(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        _configure(
            monkeypatch,
            llm_provider="openai",
            llm_model="gpt-5.2",
            llm_api_key="sk-test",
            llm_base_url="https://api.openai.com/v1",
            ollama_base_url=OLLAMA_URL,
        )
        acompletion = AsyncMock(return_value=_llm_response("Hello from OpenAI"))

        with patch("litellm.acompletion", acompletion):
            response = client.post(
                "/api/v1/chat",
                json={"message": "Hello", "conversation_id": "chat-openai-passthrough-test"},
            )

        assert response.status_code == 200, response.text
        assert response.json()["message"] == "Hello from OpenAI"

        acompletion.assert_awaited_once()
        kwargs = acompletion.await_args.kwargs
        assert kwargs["model"] == "gpt-5.2"
        assert kwargs["api_base"] == "https://api.openai.com/v1"
        assert kwargs["api_key"] == "sk-test"
