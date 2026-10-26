"""Tests for the shared query-embedding helper and its three call sites.

The query prefix must follow the configured embedding model (EmbeddingGemma gets
``task: search result | query: ``, OpenAI models nothing), be overridable through
``EMBEDDING_QUERY_PREFIX``, and reach the embedding API from the search router,
the chat router and the federated search alike. Documents are never prefixed.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.config import BackendSettings, settings
from backend.services import query_embedding as qe

GEMMA_PREFIX = "task: search result | query: "


def _mock_client(vector=None):
    """An AsyncOpenAI look-alike whose embeddings.create records its call."""
    client = MagicMock()
    client.embeddings.create = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(embedding=vector or [0.1, 0.2, 0.3])])
    )
    return client


@pytest.fixture
def gemma_settings(monkeypatch):
    monkeypatch.setattr(settings, "embedding_model", "embeddinggemma-2")
    monkeypatch.setattr(settings, "embedding_query_prefix", None)
    monkeypatch.setattr(settings, "embedding_api_key", "none")
    monkeypatch.setattr(settings, "embedding_base_url", "http://embedder:8080/v1")
    yield settings


class TestPrefixSelection:
    @pytest.mark.parametrize(
        "model, expected",
        [
            ("embeddinggemma-2", GEMMA_PREFIX),
            ("ggml-org/EmbeddingGemma-300M-GGUF:Q8_0", GEMMA_PREFIX),
            ("nomic-embed-text", "search_query: "),
            ("text-embedding-3-small", ""),
            ("text-embedding-3-large", ""),
            ("", ""),
        ],
    )
    def test_detect_task_prefixes_by_model_family(self, model, expected):
        assert qe.detect_task_prefixes(model)[0] == expected

    def test_prefix_table_is_shared_with_the_validation_checks(self):
        from backend.services import embedding_checks

        assert embedding_checks.KNOWN_TASK_PREFIXES is qe.KNOWN_TASK_PREFIXES
        assert embedding_checks.detect_task_prefixes is qe.detect_task_prefixes

    def test_auto_prefix_follows_configured_model(self, gemma_settings, monkeypatch):
        assert qe.prepare_query_text("Wie hoch ist die Kaution?") == GEMMA_PREFIX + "Wie hoch ist die Kaution?"
        monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-large")
        assert qe.prepare_query_text("Wie hoch ist die Kaution?") == "Wie hoch ist die Kaution?"

    def test_prefix_is_idempotent_and_skips_empty_text(self, gemma_settings):
        once = qe.prepare_query_text("Kaution")
        assert qe.prepare_query_text(once) == once
        assert qe.prepare_query_text("") == ""

    def test_empty_override_disables_prefix(self, gemma_settings, monkeypatch):
        monkeypatch.setattr(settings, "embedding_query_prefix", "")
        assert qe.resolve_query_prefix() == ""
        assert qe.prepare_query_text("Kaution") == "Kaution"

    def test_custom_override_is_used_verbatim(self, gemma_settings, monkeypatch):
        monkeypatch.setattr(settings, "embedding_query_prefix", "query: ")
        assert qe.prepare_query_text("Kaution") == "query: Kaution"

    def test_explicit_arguments_win_over_settings(self, gemma_settings):
        assert qe.prepare_query_text("Kaution", model="text-embedding-3-small") == "Kaution"
        assert qe.prepare_query_text("Kaution", override="q: ") == "q: Kaution"

    def test_settings_field_distinguishes_unset_from_empty(self):
        assert BackendSettings(_env_file=None).embedding_query_prefix is None
        assert BackendSettings(_env_file=None, embedding_query_prefix="").embedding_query_prefix == ""


class TestEmbedQuery:
    async def test_sends_prefixed_text_with_configured_model(self, gemma_settings):
        client = _mock_client([0.5] * 4)
        vector = await qe.embed_query("Kaution", client=client)
        assert vector == [0.5] * 4
        client.embeddings.create.assert_awaited_once_with(model="embeddinggemma-2", input=GEMMA_PREFIX + "Kaution")

    async def test_openai_model_gets_raw_text(self, gemma_settings, monkeypatch):
        monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-small")
        client = _mock_client()
        await qe.embed_query("Kaution", client=client)
        client.embeddings.create.assert_awaited_once_with(model="text-embedding-3-small", input="Kaution")

    async def test_creates_a_client_from_settings_when_none_is_given(self, gemma_settings):
        client = _mock_client()
        with patch("openai.AsyncOpenAI", return_value=client) as factory:
            await qe.embed_query("Kaution")
        factory.assert_called_once()
        assert factory.call_args.kwargs["api_key"] == "none"
        assert factory.call_args.kwargs["base_url"] == "http://embedder:8080/v1"
        assert client.embeddings.create.await_args.kwargs["input"] == GEMMA_PREFIX + "Kaution"


class TestCallSites:
    async def test_search_router_uses_the_helper(self, gemma_settings):
        from backend.routers import search

        client = _mock_client([1.0])
        with patch("openai.AsyncOpenAI", return_value=client):
            assert await search.get_embedding("Kaution") == [1.0]
        assert client.embeddings.create.await_args.kwargs["input"] == GEMMA_PREFIX + "Kaution"

    async def test_chat_router_uses_the_helper(self, gemma_settings):
        from backend.routers import chat

        client = _mock_client([2.0])
        with patch("openai.AsyncOpenAI", return_value=client):
            assert await chat.get_embedding("Kaution") == [2.0]
        assert client.embeddings.create.await_args.kwargs["input"] == GEMMA_PREFIX + "Kaution"

    async def test_federated_search_uses_the_helper_with_its_cached_client(self, gemma_settings):
        from backend.agent.federated_search import FederatedSearch

        fs = FederatedSearch(mongo_client=MagicMock())
        client = _mock_client([3.0])
        fs._embedding_client = client
        assert await fs.get_embedding("Kaution") == [3.0]
        assert await fs.get_embedding("Miete") == [3.0]
        assert client.embeddings.create.await_count == 2
        assert client.embeddings.create.await_args.kwargs["input"] == GEMMA_PREFIX + "Miete"
        assert fs._embedding_client is client


class TestRetry:
    """Query embeddings retry transient failures with backoff and may use their own endpoint (F29)."""

    @pytest.fixture(autouse=True)
    def _fast_backoff(self, gemma_settings, monkeypatch):
        monkeypatch.setattr(settings, "query_embedding_max_retries", 3)
        monkeypatch.setattr(settings, "query_embedding_retry_base_seconds", 0.0)
        monkeypatch.setattr(settings, "query_embedding_url", None)
        monkeypatch.setattr(qe.asyncio, "sleep", AsyncMock())

    def _failing_then_ok(self, failures):
        client = MagicMock()
        outcomes = list(failures) + [SimpleNamespace(data=[SimpleNamespace(embedding=[0.5])])]
        client.embeddings.create = AsyncMock(side_effect=outcomes)
        return client

    async def test_connection_errors_are_retried_until_success(self):
        import openai

        error = openai.APIConnectionError(request=MagicMock())
        client = self._failing_then_ok([error, error])
        assert await qe.embed_query("Kaution", client=client) == [0.5]
        assert client.embeddings.create.await_count == 3
        assert qe.asyncio.sleep.await_count == 2

    async def test_server_errors_and_rate_limits_are_retried(self):
        import openai

        response = MagicMock(status_code=503, headers={})
        server_error = openai.APIStatusError("busy", response=response, body=None)
        response_429 = MagicMock(status_code=429, headers={})
        rate_limit = openai.RateLimitError("slow down", response=response_429, body=None)
        client = self._failing_then_ok([server_error, rate_limit])
        assert await qe.embed_query("Kaution", client=client) == [0.5]
        assert client.embeddings.create.await_count == 3

    async def test_client_errors_are_not_retried(self):
        import openai

        response = MagicMock(status_code=400, headers={})
        bad_request = openai.BadRequestError("ill-formed UTF-8", response=response, body=None)
        client = self._failing_then_ok([bad_request])
        with pytest.raises(openai.BadRequestError):
            await qe.embed_query("Kaution", client=client)
        assert client.embeddings.create.await_count == 1

    async def test_the_budget_is_bounded(self):
        import openai

        error = openai.APITimeoutError(request=MagicMock())
        client = self._failing_then_ok([error] * 4)
        with pytest.raises(openai.APITimeoutError):
            await qe.embed_query("Kaution", client=client)
        assert client.embeddings.create.await_count == 4, "one try plus three retries"

    def test_backoff_doubles_and_is_capped(self):
        delays = [qe.retry_delay_seconds(n, base=0.5, jitter=0.0) for n in (1, 2, 3, 4, 5, 6)]
        assert delays == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]
        assert 1.0 <= qe.retry_delay_seconds(2, base=0.5) <= 1.25

    def test_query_url_override_selects_the_second_embedder(self, monkeypatch):
        assert qe.query_embedding_base_url() == "http://embedder:8080/v1"
        monkeypatch.setattr(settings, "query_embedding_url", "http://embedder-query:8080/v1")
        assert qe.query_embedding_base_url() == "http://embedder-query:8080/v1"
        with patch("openai.AsyncOpenAI") as factory:
            qe.create_embedding_client()
        kwargs = factory.call_args.kwargs
        assert kwargs["base_url"] == "http://embedder-query:8080/v1"
        assert kwargs["max_retries"] == 0, "the client's own retries would stack on ours"
        monkeypatch.setattr(settings, "query_embedding_url", "   ")
        assert qe.query_embedding_base_url() == "http://embedder:8080/v1"
