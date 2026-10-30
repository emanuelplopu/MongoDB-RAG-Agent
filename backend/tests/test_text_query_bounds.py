"""Bounded fuzzy text queries (F56): operator shape, clause budget, maxClauseCount retry, callers.

Covers ``backend/services/text_query.py`` and its use in ``text_search`` /
``hybrid_search`` (``backend/routers/search.py``), ``FederatedSearch._search_database``
and ``perform_search`` in ``backend/routers/chat.py``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure

from backend.agent.federated_search import FederatedSearch
from backend.models.schemas import SearchType
from backend.routers.chat import perform_search
from backend.services.identifier_search import build_exact_search_operator, detect_identifiers
from backend.services.text_query import (
    FUZZY_OPTIONS,
    FUZZY_TERM_LIMIT,
    TEXT_TERM_LIMIT,
    QueryTooLongError,
    build_text_operator,
    estimated_clause_count,
    is_max_clause_error,
    query_terms,
    run_text_search,
    text_search_stage,
)

# The live error of tick 42 (2026-10-30 02:51 UTC), as Motor raises it.
MAX_CLAUSE_MESSAGE = (
    "PlanExecutor error during aggregation :: caused by :: Remote error from mongot :: "
    "caused by :: maxClauseCount is set to 1024"
)
LUCENE_LIMIT = 1024

TEN_TERMS = "Welche Kündigungsfrist gilt für einen Mietvertrag über Geschäftsräume in Wien?"
IDENTIFIER_QUERY = "ALPO CONSULT GmbH FN 404945h Geschäftszweig"
THIRTY_TERMS = " ".join(f"begriff{i}" for i in range(30))
# Tick 42's shape: a pasted sentence with 28 distinct terms.
PASTED_SENTENCE = (
    "Die Mieterin hat den Mietzins trotz mehrfacher schriftlicher Mahnung nicht bezahlt, "
    "weshalb die Vermieterin nunmehr die gerichtliche Aufkündigung des Bestandverhältnisses "
    "wegen qualifizierten Zahlungsverzuges samt Räumung begehrt und zusätzlich Verzugszinsen fordert."
)


def _max_clause_error() -> OperationFailure:
    return OperationFailure(MAX_CLAUSE_MESSAGE, code=8, details={"errmsg": MAX_CLAUSE_MESSAGE, "code": 8})


class _AsyncCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = [dict(doc) for doc in docs]

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def _doc(chunk_id: str, similarity: float = 0.5) -> Dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "document_id": f"doc-{chunk_id}",
        "content": "x" * 300,
        "similarity": similarity,
        "metadata": {"title": "Doc"},
        "document_title": "Doc",
        "document_source": f"Akten/{chunk_id}.pdf",
    }


def _stage_kind(pipeline: List[Dict[str, Any]]) -> str:
    first = pipeline[0]
    if "$vectorSearch" in first:
        return "vector"
    return "text"


def _text_clauses(stage: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The ``text`` clause bodies of a ``$search`` stage, single or compound."""
    body = stage["$search"]
    if "text" in body:
        return [body["text"]]
    return [clause["text"] for clause in body["compound"]["should"]]


def _failing_then(canned: Dict[str, List[Dict[str, Any]]], text_failures: int) -> MagicMock:
    """``aggregate`` mock: vector pipelines answer at once, the first ``text_failures`` text pipelines raise."""
    state = {"text_failures": text_failures}

    def aggregate(pipeline: List[Dict[str, Any]]) -> _AsyncCursor:
        kind = _stage_kind(pipeline)
        if kind == "text" and state["text_failures"] > 0:
            state["text_failures"] -= 1
            raise _max_clause_error()
        return _AsyncCursor(canned[kind])

    return MagicMock(side_effect=aggregate)


# ─────────────────────────────────────────────────────────────────────────────
# Terms and operator shape
# ─────────────────────────────────────────────────────────────────────────────

class TestTerms:
    def test_distinct_terms_ignore_case_and_surrounding_punctuation(self) -> None:
        assert query_terms("Mietzins, Mietzins! (mietzins) Räumung; Räumung.") == ["Mietzins,", "Räumung;"]

    def test_empty_and_punctuation_only_tokens_do_not_count(self) -> None:
        assert query_terms("") == []
        assert query_terms("§ - ... §§") == []
        assert query_terms("§ 1 TKG 2021") == ["1", "TKG", "2021"]

    def test_sample_queries(self) -> None:
        assert len(query_terms(TEN_TERMS)) == 10
        assert len(query_terms(THIRTY_TERMS)) == 30
        assert len(query_terms(PASTED_SENTENCE)) == 28


class TestOperatorShape:
    def test_ten_term_query_keeps_the_single_fuzzy_text_clause(self) -> None:
        operator = build_text_operator(TEN_TERMS)
        assert operator == {
            "text": {
                "query": TEN_TERMS,
                "path": "content",
                "fuzzy": {"maxEdits": 2, "prefixLength": 3, "maxExpansions": 20},
            }
        }
        # The original string goes through untouched (punctuation included).
        assert operator["text"]["query"] is TEN_TERMS

    def test_fifteen_terms_are_still_the_short_form(self) -> None:
        query = " ".join(f"wort{i}" for i in range(FUZZY_TERM_LIMIT))
        assert build_text_operator(query) == {"text": {"query": query, "path": "content", "fuzzy": dict(FUZZY_OPTIONS)}}

    def test_identifier_query_is_unchanged_on_both_operators(self) -> None:
        identifiers = detect_identifiers(IDENTIFIER_QUERY)
        assert identifiers and identifiers[0].canonical == "FN 404945h"
        assert build_text_operator(IDENTIFIER_QUERY) == {
            "text": {"query": IDENTIFIER_QUERY, "path": "content", "fuzzy": dict(FUZZY_OPTIONS)}
        }
        exact = build_exact_search_operator(identifiers, index="text_index")
        assert "fuzzy" not in str(exact)
        assert exact["compound"]["should"][0] == {"phrase": {"query": "FN 404945h", "path": "content"}}

    def test_thirty_term_query_gets_the_bounded_compound(self) -> None:
        operator = build_text_operator(THIRTY_TERMS)
        assert list(operator) == ["compound"]
        assert operator["compound"]["minimumShouldMatch"] == 1
        fuzzy_clause, plain_clause = operator["compound"]["should"]
        assert fuzzy_clause["text"]["fuzzy"] == FUZZY_OPTIONS
        assert fuzzy_clause["text"]["query"].split() == [f"begriff{i}" for i in range(15)]
        assert "fuzzy" not in plain_clause["text"]
        assert plain_clause["text"]["query"].split() == [f"begriff{i}" for i in range(15, 30)]
        assert plain_clause["text"]["path"] == fuzzy_clause["text"]["path"] == "content"

    def test_the_pasted_sentence_of_tick_42_is_bounded(self) -> None:
        operator = build_text_operator(PASTED_SENTENCE)
        fuzzy_clause, plain_clause = operator["compound"]["should"]
        assert len(fuzzy_clause["text"]["query"].split()) == FUZZY_TERM_LIMIT
        assert len(plain_clause["text"]["query"].split()) == 28 - FUZZY_TERM_LIMIT
        assert estimated_clause_count(PASTED_SENTENCE) < LUCENE_LIMIT

    def test_duplicates_in_a_long_query_are_sent_once(self) -> None:
        query = " ".join(["Mietzins"] * 10 + [f"wort{i}" for i in range(20)])
        fuzzy_clause, plain_clause = build_text_operator(query)["compound"]["should"]
        assert fuzzy_clause["text"]["query"].split()[0] == "Mietzins"
        assert len(fuzzy_clause["text"]["query"].split()) + len(plain_clause["text"]["query"].split()) == 21

    def test_very_long_queries_are_cut_at_the_term_limit(self) -> None:
        query = " ".join(f"wort{i}" for i in range(300))
        fuzzy_clause, plain_clause = build_text_operator(query)["compound"]["should"]
        sent = fuzzy_clause["text"]["query"].split() + plain_clause["text"]["query"].split()
        assert len(sent) == TEXT_TERM_LIMIT
        assert sent[-1] == f"wort{TEXT_TERM_LIMIT - 1}"

    def test_without_fuzzy_short_queries_keep_the_string_and_long_ones_are_capped(self) -> None:
        assert build_text_operator(TEN_TERMS, fuzzy=False) == {"text": {"query": TEN_TERMS, "path": "content"}}
        long_query = " ".join(f"wort{i}" for i in range(100))
        operator = build_text_operator(long_query, fuzzy=False)
        assert list(operator) == ["text"]
        assert "fuzzy" not in operator["text"]
        assert len(operator["text"]["query"].split()) == TEXT_TERM_LIMIT

    def test_stage_carries_the_index(self) -> None:
        stage = text_search_stage(TEN_TERMS, index="text_index", path="content")
        assert stage == {"$search": {"index": "text_index", **build_text_operator(TEN_TERMS)}}

    @pytest.mark.parametrize("count", [1, 10, 15, 16, 23, 26, 28, 60, 61, 200, 1000])
    def test_clause_budget_stays_under_the_lucene_limit_for_any_length(self, count: int) -> None:
        query = " ".join(f"w{i}" for i in range(count))
        assert estimated_clause_count(query) <= FUZZY_TERM_LIMIT * FUZZY_OPTIONS["maxExpansions"] + (
            TEXT_TERM_LIMIT - FUZZY_TERM_LIMIT
        )
        assert estimated_clause_count(query) < LUCENE_LIMIT
        assert estimated_clause_count(query, fuzzy=False) <= TEXT_TERM_LIMIT

    def test_the_old_operator_would_have_exceeded_the_limit_at_tick_42(self) -> None:
        # 28 terms × the Atlas default of 50 expansions is what failed live.
        assert 28 * 50 > LUCENE_LIMIT
        assert estimated_clause_count(PASTED_SENTENCE) == FUZZY_TERM_LIMIT * 20 + 13


# ─────────────────────────────────────────────────────────────────────────────
# Retry on maxClauseCount
# ─────────────────────────────────────────────────────────────────────────────

class TestRetry:
    def test_error_detection(self) -> None:
        assert is_max_clause_error(_max_clause_error())
        assert is_max_clause_error(RuntimeError("Remote error from mongot: maxClauseCount is set to 1024"))
        assert is_max_clause_error(OperationFailure("x", details={"errmsg": "maxClauseCount is set to 1024"}))
        assert not is_max_clause_error(OperationFailure("index text_index not found"))
        assert not is_max_clause_error(TimeoutError())

    async def test_first_failure_is_retried_without_fuzzy(self, caplog: pytest.LogCaptureFixture) -> None:
        collection = MagicMock()
        collection.aggregate = MagicMock(side_effect=[_max_clause_error(), _AsyncCursor([_doc("a"), _doc("b")])])
        pipelines: List[List[Dict[str, Any]]] = []

        def pipeline_for(fuzzy: bool) -> List[Dict[str, Any]]:
            pipeline = [text_search_stage(THIRTY_TERMS, index="text_index", fuzzy=fuzzy), {"$limit": 5}]
            pipelines.append(pipeline)
            return pipeline

        with caplog.at_level(logging.WARNING, logger="backend.services.text_query"):
            docs = await run_text_search(collection, pipeline_for, query=THIRTY_TERMS, label="Test search")

        assert [d["chunk_id"] for d in docs] == ["a", "b"]
        assert collection.aggregate.call_count == 2
        assert "fuzzy" in str(pipelines[0][0]) and "fuzzy" not in str(pipelines[1][0])
        assert "compound" not in pipelines[1][0]["$search"]
        assert collection.aggregate.call_args_list[1].args[0] == pipelines[1]
        warning = next(record for record in caplog.records if record.levelno == logging.WARNING)
        assert "30 distinct terms" in warning.getMessage()
        assert "Test search" in warning.getMessage()

    async def test_second_failure_raises_query_too_long(self) -> None:
        collection = MagicMock()
        collection.aggregate = MagicMock(side_effect=[_max_clause_error(), _max_clause_error()])
        with pytest.raises(QueryTooLongError) as excinfo:
            await run_text_search(collection, lambda fuzzy: [text_search_stage(THIRTY_TERMS, index="i", fuzzy=fuzzy)], query=THIRTY_TERMS)
        assert excinfo.value.term_count == 30
        assert "too long" in str(excinfo.value).lower()
        assert isinstance(excinfo.value.__cause__, OperationFailure)
        assert collection.aggregate.call_count == 2

    async def test_other_errors_are_not_retried(self) -> None:
        collection = MagicMock()
        collection.aggregate = MagicMock(side_effect=OperationFailure("index text_index not found"))
        with pytest.raises(OperationFailure):
            await run_text_search(collection, lambda fuzzy: [text_search_stage(TEN_TERMS, index="i", fuzzy=fuzzy)], query=TEN_TERMS)
        assert collection.aggregate.call_count == 1

    async def test_error_during_iteration_is_retried_too(self) -> None:
        class _FailingCursor:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise _max_clause_error()

        collection = MagicMock()
        collection.aggregate = MagicMock(side_effect=[_FailingCursor(), _AsyncCursor([_doc("a")])])
        docs = await run_text_search(collection, lambda fuzzy: [text_search_stage(THIRTY_TERMS, index="i", fuzzy=fuzzy)], query=THIRTY_TERMS)
        assert [d["chunk_id"] for d in docs] == ["a"]

    async def test_pymongo_async_style_awaitable_aggregate(self) -> None:
        collection = MagicMock()
        collection.aggregate = AsyncMock(return_value=_AsyncCursor([_doc("a")]))
        docs = await run_text_search(collection, lambda fuzzy: [text_search_stage(TEN_TERMS, index="i", fuzzy=fuzzy)], query=TEN_TERMS)
        assert [d["chunk_id"] for d in docs] == ["a"]


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

class TestEndpoints:
    @pytest.fixture
    def embedding(self):
        with patch("backend.routers.search.get_embedding", new=AsyncMock(return_value=[0.1] * 8)):
            yield

    def test_text_endpoint_short_query_sends_the_old_operator_shape(self, client: TestClient, mock_db) -> None:
        aggregate = mock_db.chunks_collection.aggregate
        aggregate.reset_mock()
        response = client.post("/api/v1/search/text", json={"query": TEN_TERMS, "match_count": 5})
        assert response.status_code == 200, response.text
        (pipeline,), _ = aggregate.call_args
        assert pipeline[0]["$search"]["text"] == {"query": TEN_TERMS, "path": "content", "fuzzy": dict(FUZZY_OPTIONS)}
        assert pipeline[1] == {"$limit": 5}
        assert aggregate.call_count == 1

    def test_text_endpoint_long_query_is_bounded_and_retried_once(self, client: TestClient, mock_db) -> None:
        canned = {"text": [_doc("t1"), _doc("t2")]}
        mock_db.chunks_collection.aggregate = _failing_then(canned, text_failures=1)
        response = client.post("/api/v1/search/text", json={"query": PASTED_SENTENCE, "match_count": 5})
        assert response.status_code == 200, response.text
        assert [r["chunk_id"] for r in response.json()["results"]] == ["t1", "t2"]
        calls = [call.args[0] for call in mock_db.chunks_collection.aggregate.call_args_list]
        assert len(calls) == 2
        first, second = calls
        assert "compound" in first[0]["$search"]
        assert _text_clauses(first[0])[0]["fuzzy"] == FUZZY_OPTIONS
        assert "fuzzy" not in str(second[0])
        assert len(_text_clauses(second[0])[0]["query"].split()) == 28
        assert first[1:] == second[1:]

    def test_text_endpoint_returns_400_when_the_retry_fails_too(self, client: TestClient, mock_db) -> None:
        mock_db.chunks_collection.aggregate = _failing_then({"text": []}, text_failures=2)
        response = client.post("/api/v1/search/text", json={"query": PASTED_SENTENCE, "match_count": 5})
        assert response.status_code == 400, response.text
        assert "too long" in response.json()["message"].lower()
        assert "28 distinct terms" in response.json()["message"]
        assert mock_db.chunks_collection.aggregate.call_count == 2

    def test_text_endpoint_other_errors_stay_500(self, client: TestClient, mock_db) -> None:
        mock_db.chunks_collection.aggregate = MagicMock(side_effect=OperationFailure("index text_index not found"))
        response = client.post("/api/v1/search/text", json={"query": PASTED_SENTENCE, "match_count": 5})
        assert response.status_code == 500
        assert mock_db.chunks_collection.aggregate.call_count == 1

    def test_hybrid_endpoint_short_query_text_stage_unchanged(self, client: TestClient, mock_db, embedding) -> None:
        aggregate = mock_db.chunks_collection.aggregate
        aggregate.reset_mock()
        response = client.post("/api/v1/search/hybrid", json={"query": IDENTIFIER_QUERY.replace("FN 404945h", "Beratung"), "match_count": 10})
        assert response.status_code == 200, response.text
        pipelines = [call.args[0] for call in aggregate.call_args_list]
        assert [_stage_kind(p) for p in pipelines] == ["vector", "text"]
        assert pipelines[1][0]["$search"]["text"]["fuzzy"] == FUZZY_OPTIONS
        assert pipelines[1][0]["$search"]["text"]["query"] == "ALPO CONSULT GmbH Beratung Geschäftszweig"

    def test_hybrid_endpoint_long_query_survives_the_clause_limit(self, client: TestClient, mock_db, embedding) -> None:
        canned = {"vector": [_doc("v1", 0.9), _doc("shared", 0.8)], "text": [_doc("shared", 3.0), _doc("t1", 2.0)]}
        mock_db.chunks_collection.aggregate = _failing_then(canned, text_failures=1)
        response = client.post("/api/v1/search/hybrid", json={"query": PASTED_SENTENCE, "match_count": 3})
        assert response.status_code == 200, response.text
        assert [r["chunk_id"] for r in response.json()["results"]] == ["shared", "v1", "t1"]
        pipelines = [call.args[0] for call in mock_db.chunks_collection.aggregate.call_args_list]
        assert [_stage_kind(p) for p in pipelines] == ["vector", "text", "text"]
        assert "compound" in pipelines[1][0]["$search"] and "fuzzy" not in str(pipelines[2][0])
        assert pipelines[1][1] == pipelines[2][1] == {"$limit": 6}

    def test_hybrid_endpoint_returns_400_when_both_attempts_fail(self, client: TestClient, mock_db, embedding) -> None:
        mock_db.chunks_collection.aggregate = _failing_then({"vector": [_doc("v1")], "text": []}, text_failures=2)
        response = client.post("/api/v1/search/hybrid", json={"query": PASTED_SENTENCE, "match_count": 3})
        assert response.status_code == 400, response.text
        assert "too long" in response.json()["message"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Federated search and chat
# ─────────────────────────────────────────────────────────────────────────────

def _federated_search_with(aggregate: MagicMock) -> tuple[FederatedSearch, MagicMock]:
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value={"_id": "x"})
    collection.aggregate = aggregate
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection)
    fs = FederatedSearch()
    fs._client = MagicMock()
    fs._client.__getitem__ = MagicMock(return_value=db)
    return fs, collection


class TestFederatedAndChat:
    async def test_search_database_short_query_text_stage_unchanged(self) -> None:
        canned = {"vector": [_doc("v1", 0.9)], "text": [_doc("t1", 2.0)]}
        fs, collection = _federated_search_with(_failing_then(canned, text_failures=0))
        results = await fs._search_database("rag_test", "chunks", "documents", TEN_TERMS, [0.1] * 8, match_count=3)
        pipelines = [call.args[0] for call in collection.aggregate.call_args_list]
        assert [_stage_kind(p) for p in pipelines] == ["vector", "text"]
        assert pipelines[1][0]["$search"] == {
            "index": pipelines[1][0]["$search"]["index"],
            "text": {"query": TEN_TERMS, "path": "content", "fuzzy": dict(FUZZY_OPTIONS)},
        }
        assert {r["chunk_id"] for r in results} == {"v1", "t1"}

    async def test_search_database_long_query_is_bounded_and_retried(self) -> None:
        canned = {"vector": [_doc("v1", 0.9)], "text": [_doc("t1", 2.0)]}
        fs, collection = _federated_search_with(_failing_then(canned, text_failures=1))
        results = await fs._search_database("rag_test", "chunks", "documents", THIRTY_TERMS, [0.1] * 8, match_count=3)
        pipelines = [call.args[0] for call in collection.aggregate.call_args_list]
        assert [_stage_kind(p) for p in pipelines] == ["vector", "text", "text"]
        assert "compound" in pipelines[1][0]["$search"]
        assert "fuzzy" not in str(pipelines[2][0])
        assert {r["chunk_id"] for r in results} == {"v1", "t1"}

    async def test_search_database_keeps_vector_results_when_the_text_stage_is_too_long(self) -> None:
        canned = {"vector": [_doc("v1", 0.9)], "text": []}
        fs, collection = _federated_search_with(_failing_then(canned, text_failures=2))
        results = await fs._search_database("rag_test", "chunks", "documents", THIRTY_TERMS, [0.1] * 8, match_count=3)
        assert [r["chunk_id"] for r in results] == ["v1"]
        assert collection.aggregate.call_count == 3

    async def test_chat_perform_search_uses_the_bounded_operator_and_retries(self) -> None:
        canned = {"vector": [_doc("v1", 0.9)], "text": [_doc("t1", 2.0)]}
        db = MagicMock()
        db.chunks_collection = MagicMock()
        db.chunks_collection.aggregate = _failing_then(canned, text_failures=1)
        with patch("backend.routers.chat.get_embedding", new=AsyncMock(return_value=[0.1] * 8)):
            results, thinking = await perform_search(db, PASTED_SENTENCE, SearchType.HYBRID, 3)
        assert {r["chunk_id"] for r in results} == {"v1", "t1"}
        assert thinking.total_results == 2
        pipelines = [call.args[0] for call in db.chunks_collection.aggregate.call_args_list]
        assert [_stage_kind(p) for p in pipelines] == ["vector", "text", "text"]
        assert "compound" in pipelines[1][0]["$search"]
