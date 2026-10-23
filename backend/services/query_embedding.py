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

Settings (``backend/core/config.py``):

- ``EMBEDDING_MODEL`` selects the prefix through :func:`detect_task_prefixes`.
- ``EMBEDDING_QUERY_PREFIX`` overrides it: unset = by model family, an empty
  string disables the prefix, any other value is used verbatim.
"""

from __future__ import annotations

import logging
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


def create_embedding_client() -> Any:
    """An OpenAI-compatible async client for the configured embedding endpoint."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=settings.embedding_api_key, base_url=settings.embedding_base_url)


async def embed_query(text: str, client: Optional[Any] = None) -> List[float]:
    """Embed one search query with the configured model, query prefix applied.

    Args:
        text: The search query as typed by the user or produced by the agent.
        client: An ``AsyncOpenAI``-compatible client to reuse; created per call
            when omitted (the search routers' historical behaviour).

    Returns:
        The embedding vector.
    """
    if client is None:
        client = create_embedding_client()
    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=prepare_query_text(text),
    )
    return response.data[0].embedding
