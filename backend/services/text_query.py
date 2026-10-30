"""Bounded fuzzy text queries for Atlas Search (finding F56).

Every text stage of the retrieval paths (``POST /api/v1/search/text``, the text
half of ``hybrid_search``, ``FederatedSearch._search_database`` and the chat
router's ``perform_search``) sends the user's words to the Atlas Search ``text``
operator with fuzzy matching. Lucene expands each fuzzy term into up to
``maxExpansions`` variants (Atlas default 50) and rejects the whole query once
the resulting BooleanQuery exceeds 1,024 clauses (``maxClauseCount is set to
1024``). A pasted paragraph or an agent's long rewritten question with about
25 distinct terms therefore failed with HTTP 500 (finding F56 in
``docs/system-validation/INGESTION_PROMPT_TEST_LOG_2026-10-24.md``).

This module is the one place that builds the ``text`` operator:

* :func:`build_text_operator` bounds the expansion. Fuzzy matching uses
  :data:`FUZZY_OPTIONS` (``maxEdits`` 2, ``prefixLength`` 3, ``maxExpansions``
  20). A query with at most :data:`FUZZY_TERM_LIMIT` distinct terms is sent as
  before: one ``text`` clause with the original query string. A longer query
  becomes a ``compound.should`` of two ``text`` clauses: the first
  :data:`FUZZY_TERM_LIMIT` distinct terms with fuzzy matching, the following
  terms without it, and never more than :data:`TEXT_TERM_LIMIT` distinct terms
  in total. The worst case is therefore 15 × 20 + 45 = 345 clauses, well under
  Lucene's limit for any query length.
* :func:`run_text_search` runs a text pipeline and, when Atlas still answers
  with ``maxClauseCount``, retries the same stage once without fuzzy matching
  (logged at WARNING with the distinct term count). If that fails the same
  way, :class:`QueryTooLongError` tells the caller to answer with a 4xx
  instead of a 500.

The identifier path (``backend/services/identifier_search``) is untouched: its
exact ``phrase`` query carries no fuzziness, and identifier questions are short,
so their fuzzy text stage keeps the single ``text`` clause.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Dict, List, Sequence

logger = logging.getLogger(__name__)

# Fuzzy options for every text stage. ``maxExpansions`` 20 (Atlas default 50)
# caps the variants Lucene generates per term; ``prefixLength`` 3 keeps the
# first three characters exact, so short tokens produce no variants at all.
FUZZY_OPTIONS: Dict[str, int] = {"maxEdits": 2, "prefixLength": 3, "maxExpansions": 20}
# Distinct terms that get fuzzy matching. Up to this many the query is sent unchanged.
FUZZY_TERM_LIMIT = 15
# Distinct terms sent to the text index at most; the rest of a long query is dropped.
TEXT_TERM_LIMIT = 60
# Marker in the Atlas Search / Lucene error message for an oversized BooleanQuery.
MAX_CLAUSE_MARKER = "maxClauseCount"
# Characters stripped from the ends of a token before it counts as a term.
_TOKEN_PUNCTUATION = ".,;:!?()[]{}<>\"'`«»„“”‚‘’/\\|-–—…*+=#%&§"


class QueryTooLongError(ValueError):
    """The text index rejected the query even without fuzzy matching.

    Attributes:
        term_count: Distinct terms in the query, for the client-facing message.
    """

    def __init__(self, term_count: int) -> None:
        self.term_count = term_count
        super().__init__(
            f"Query too long for text search ({term_count} distinct terms): "
            "the search index rejected it even without fuzzy matching; shorten the query"
        )


def query_terms(query: str) -> List[str]:
    """Distinct terms of a query, in order of first appearance.

    Tokens are split on whitespace, stripped of surrounding punctuation and
    compared case-insensitively; the first spelling of a term is kept. This
    approximates how the index analyser counts terms closely enough for the
    clause budget (the analyser may split a token like ``39/16v`` further, which
    only lowers the fuzzy share because tokens shorter than ``prefixLength``
    produce no variants).
    """
    terms: List[str] = []
    seen: set[str] = set()
    for token in (query or "").split():
        key = token.strip(_TOKEN_PUNCTUATION).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        terms.append(token)
    return terms


def _text_clause(query: str, path: str, fuzzy: bool) -> Dict[str, Any]:
    clause: Dict[str, Any] = {"query": query, "path": path}
    if fuzzy:
        clause["fuzzy"] = dict(FUZZY_OPTIONS)
    return {"text": clause}


def build_text_operator(query: str, *, path: str = "content", fuzzy: bool = True) -> Dict[str, Any]:
    """The operator body of a ``$search`` text stage, bounded for long queries.

    Rule: with at most :data:`FUZZY_TERM_LIMIT` distinct terms the original
    query string goes into one ``text`` clause (with :data:`FUZZY_OPTIONS` when
    ``fuzzy`` is true), exactly as before this bound existed apart from the
    ``maxExpansions`` cap. With more distinct terms the query is rebuilt from
    its terms: the first :data:`FUZZY_TERM_LIMIT` terms form a fuzzy ``text``
    clause, the next ones up to :data:`TEXT_TERM_LIMIT` terms in total a plain
    ``text`` clause, joined in a ``compound.should`` (``minimumShouldMatch`` 1,
    so the scores add up like a single multi-term query). Without ``fuzzy`` a
    long query becomes one plain ``text`` clause over its first
    :data:`TEXT_TERM_LIMIT` terms.

    Args:
        query: The user's query text.
        path: Indexed field to search.
        fuzzy: Whether fuzzy matching is wanted at all (the retry passes False).

    Returns:
        A dict with either a ``text`` or a ``compound`` key, to be merged with
        ``{"index": ...}`` into the ``$search`` stage.
    """
    terms = query_terms(query)
    if len(terms) <= FUZZY_TERM_LIMIT:
        return _text_clause(query, path, fuzzy)
    bounded = terms[:TEXT_TERM_LIMIT]
    if not fuzzy:
        return _text_clause(" ".join(bounded), path, False)
    head, tail = bounded[:FUZZY_TERM_LIMIT], bounded[FUZZY_TERM_LIMIT:]
    return {
        "compound": {
            "should": [_text_clause(" ".join(head), path, True), _text_clause(" ".join(tail), path, False)],
            "minimumShouldMatch": 1,
        }
    }


def text_search_stage(query: str, *, index: str, path: str = "content", fuzzy: bool = True) -> Dict[str, Any]:
    """The complete ``{"$search": {...}}`` stage for a bounded text query."""
    return {"$search": {"index": index, **build_text_operator(query, path=path, fuzzy=fuzzy)}}


def estimated_clause_count(query: str, *, fuzzy: bool = True) -> int:
    """Upper bound of Lucene clauses the operator of :func:`build_text_operator` expands to."""
    count = min(len(query_terms(query)), TEXT_TERM_LIMIT)
    if not fuzzy:
        return count
    fuzzy_terms = min(count, FUZZY_TERM_LIMIT)
    return fuzzy_terms * FUZZY_OPTIONS["maxExpansions"] + (count - fuzzy_terms)


def is_max_clause_error(exc: BaseException) -> bool:
    """Whether an exception is Atlas Search's ``maxClauseCount`` rejection."""
    if MAX_CLAUSE_MARKER in str(exc):
        return True
    details = getattr(exc, "details", None)
    return isinstance(details, dict) and MAX_CLAUSE_MARKER in str(details.get("errmsg", ""))


async def _collect(collection: Any, pipeline: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run an aggregation and return its documents (Motor or PyMongo Async collections)."""
    cursor = collection.aggregate(list(pipeline))
    if inspect.isawaitable(cursor):
        cursor = await cursor
    docs: List[Dict[str, Any]] = []
    async for doc in cursor:
        docs.append(doc)
    return docs


async def run_text_search(
    collection: Any,
    pipeline_for: Callable[[bool], Sequence[Dict[str, Any]]],
    *,
    query: str,
    label: str = "Text search",
) -> List[Dict[str, Any]]:
    """Run a text pipeline; on ``maxClauseCount`` retry once without fuzzy matching.

    Args:
        collection: The chunks collection.
        pipeline_for: Builds the full aggregation pipeline; its argument says
            whether the text stage may use fuzzy matching.
        query: The query text, for the log line's term count.
        label: Caller name for the log line.

    Returns:
        The aggregation's documents.

    Raises:
        QueryTooLongError: Both attempts were rejected with ``maxClauseCount``.
        Exception: Any other failure of the first or the second attempt, unchanged.
    """
    try:
        return await _collect(collection, pipeline_for(True))
    except Exception as exc:  # noqa: BLE001 - only the clause-limit error is retried
        if not is_max_clause_error(exc):
            raise
        term_count = len(query_terms(query))
        logger.warning(
            "%s hit Lucene's clause limit with %d distinct terms (about %d clauses); "
            "retrying once without fuzzy matching: %s",
            label, term_count, estimated_clause_count(query), exc,
        )
    try:
        return await _collect(collection, pipeline_for(False))
    except Exception as exc:  # noqa: BLE001
        if is_max_clause_error(exc):
            logger.warning(
                "%s rejected again without fuzzy matching (%d distinct terms): query too long",
                label, term_count,
            )
            raise QueryTooLongError(term_count) from exc
        raise
