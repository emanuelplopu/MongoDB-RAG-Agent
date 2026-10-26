"""Query-side embedding shared by the search, chat and federated-search paths.

Documents are embedded as raw chunk text by ``src/ingestion/embedder.py``. Some
embedding model families (EmbeddingGemma, nomic, e5, bge) were trained with an
asymmetric task prefix on the *query* side. This module applies that prefix to
search queries only, chosen from the configured model name, so the document
vectors already stored stay valid and no re-ingestion is needed.

Measured on the synthetic law-firm gold set with EmbeddingGemma 2 (see
``docs/system-validation/SEMANTIC_SEARCH_REGRESSION_2026-10-23.md``): the query
prefix alone lifts semantic Hit@1 from 17 % to 27 % and MRR from 0.36 to 0.43;
prefixing the documents as well gave nothing, so documents are left unprefixed.

Query embeddings are retried with exponential backoff (connection errors,
timeouts, 429 and 5xx answers): on the live Quellex stack the single llama.cpp
embedder serves the ingestion batches too, and a query that lands while its
slots are busy used to fail outright (F29). ``QUERY_EMBEDDING_URL`` points
queries at a second embedder instance (compose profile ``embedder-query``).

Settings (``backend/core/config.py``):

- ``EMBEDDING_MODEL`` selects the prefix through :func:`detect_task_prefixes`.
- ``EMBEDDING_QUERY_PREFIX`` overrides it: unset = by model family, an empty
  string disables the prefix, any other value is used verbatim.
- ``QUERY_EMBEDDING_URL`` (optional) is the base URL queries are embedded at;
  ``EMBEDDING_BASE_URL`` otherwise.
- ``QUERY_EMBEDDING_MAX_RETRIES``, ``QUERY_EMBEDDING_RETRY_BASE_SECONDS`` and
  ``QUERY_EMBEDDING_TIMEOUT_SECONDS`` bound the retry.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from backend.core.config import settings

logger = logging.getLogger(__name__)

# (query_prefix, document_prefix) per model family; matched as a substring of
# the lower-cased model name. Shared with the validation checks and the
# embedding benchmark (``backend/services/embedding_checks.py``).
KNOWN_TASK_PREFIXES: Dict[str, Tuple[str, str]] = {
    "embeddinggemma": ("task: search result | query: ", "title: none | text: "),
    "nomic-embed": ("search_query: ", "search_document: "),
    "multilingual-e5": ("query: ", "passage: "),
    "e5-": ("query: ", "passage: "),
    "bge-m3": ("", ""),
    "bge-": ("Represent this sentence for searching relevant passages: ", ""),
    "snowflake-arctic-embed": ("query: ", ""),
    "gte-": ("", ""),
}

#: Longest wait between two attempts, seconds.
MAX_RETRY_DELAY_SECONDS = 8.0

_logged_prefix: Optional[Tuple[str, str]] = None


def detect_task_prefixes(model: str) -> Tuple[str, str]:
    """Return the ``(query_prefix, document_prefix)`` a model family expects.

    Args:
        model: Model identifier as configured for the provider (tags allowed).

    Returns:
        A tuple of prefixes; both empty when the family is unknown or needs none.
    """
    name = (model or "").lower()
    for key, prefixes in KNOWN_TASK_PREFIXES.items():
        if key in name:
            return prefixes
    return "", ""


def resolve_query_prefix(model: Optional[str] = None, override: Optional[str] = None) -> str:
    """The prefix to put in front of a search query before embedding it.

    Args:
        model: Embedding model name; defaults to ``settings.embedding_model``.
        override: Explicit prefix; defaults to ``settings.embedding_query_prefix``.
            ``None`` means "choose by model family", ``""`` disables the prefix.

    Returns:
        The prefix string, possibly empty.
    """
    if override is None:
        override = settings.embedding_query_prefix
    if override is not None:
        return override
    if model is None:
        model = settings.embedding_model
    return detect_task_prefixes(model)[0]


def prepare_query_text(text: str, model: Optional[str] = None, override: Optional[str] = None) -> str:
    """Prepend the query prefix to ``text`` (idempotent, empty text untouched).

    Args:
        text: The user's search query.
        model: See :func:`resolve_query_prefix`.
        override: See :func:`resolve_query_prefix`.

    Returns:
        The text to send to the embedding model.
    """
    global _logged_prefix
    prefix = resolve_query_prefix(model, override)
    effective_model = model if model is not None else settings.embedding_model
    if _logged_prefix != (effective_model, prefix):
        _logged_prefix = (effective_model, prefix)
        logger.info(f"Query embedding prefix for model {effective_model!r}: {prefix!r}")
    if not prefix or not text:
        return text
    if text.startswith(prefix):
        return text
    return prefix + text


def query_embedding_base_url() -> str:
    """The base URL search queries are embedded at (``QUERY_EMBEDDING_URL`` or ``EMBEDDING_BASE_URL``)."""
    override = (getattr(settings, "query_embedding_url", None) or "").strip()
    return override or settings.embedding_base_url


def create_embedding_client() -> Any:
    """An OpenAI-compatible async client for the query-embedding endpoint.

    The client's own retries are off: :func:`embed_query` retries with its own backoff,
    so one query never waits longer than the configured budget.
    """
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=settings.embedding_api_key,
        base_url=query_embedding_base_url(),
        max_retries=0,
        timeout=float(getattr(settings, "query_embedding_timeout_seconds", 30.0) or 30.0),
    )


def retry_delay_seconds(attempt: int, base: Optional[float] = None, jitter: Optional[float] = None) -> float:
    """Delay before retry ``attempt`` (1-based): ``base * 2 ** (attempt - 1)``, capped, plus jitter.

    Args:
        attempt: The retry number, starting at 1.
        base: First delay; ``settings.query_embedding_retry_base_seconds`` when omitted.
        jitter: Fraction of the delay added at random (tests pass 0); 0..25 % when omitted.
    """
    if base is None:
        base = float(getattr(settings, "query_embedding_retry_base_seconds", 0.5) or 0.0)
    delay = min(MAX_RETRY_DELAY_SECONDS, base * (2 ** max(0, attempt - 1)))
    if jitter is None:
        jitter = random.uniform(0.0, 0.25)
    return delay * (1.0 + jitter)


def is_retryable_embedding_error(exc: BaseException) -> bool:
    """Connection errors, timeouts, rate limits and server errors are worth a retry; 4xx are not."""
    try:
        import openai
    except ImportError:  # pragma: no cover - openai is a dependency
        return isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError))
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", None)
        return status is not None and (status >= 500 or status == 429)
    return isinstance(exc, (asyncio.TimeoutError, ConnectionError))


async def embed_query(text: str, client: Optional[Any] = None, max_retries: Optional[int] = None) -> List[float]:
    """Embed one search query with the configured model, query prefix applied.

    Retries transient failures (:func:`is_retryable_embedding_error`) up to
    ``max_retries`` times with :func:`retry_delay_seconds` between attempts, then
    raises the last error.

    Args:
        text: The search query as typed by the user or produced by the agent.
        client: An ``AsyncOpenAI``-compatible client to reuse; created per call
            when omitted (the search routers' historical behaviour).
        max_retries: Retry budget; ``settings.query_embedding_max_retries`` when omitted.

    Returns:
        The embedding vector.
    """
    if client is None:
        client = create_embedding_client()
    if max_retries is None:
        max_retries = int(getattr(settings, "query_embedding_max_retries", 3) or 0)
    prepared = prepare_query_text(text)
    attempt = 0
    while True:
        try:
            response = await client.embeddings.create(model=settings.embedding_model, input=prepared)
            return response.data[0].embedding
        except Exception as exc:  # noqa: BLE001 - classified below
            attempt += 1
            if attempt > max_retries or not is_retryable_embedding_error(exc):
                raise
            delay = retry_delay_seconds(attempt)
            logger.warning(
                f"Query embedding failed ({type(exc).__name__}: {str(exc)[:160]}); "
                f"retry {attempt}/{max_retries} in {delay:.1f}s"
            )
            await asyncio.sleep(delay)
