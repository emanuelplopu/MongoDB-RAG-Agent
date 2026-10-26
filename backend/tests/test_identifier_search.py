"""Identifier-aware retrieval (F25/F17): detection, exact query, merge/collapse, both callers.

Covers ``backend/services/identifier_search.py`` and its use in ``hybrid_search``
(``backend/routers/search.py``) and ``FederatedSearch._search_database``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.agent.federated_search import FederatedSearch
from backend.agent.schemas import DataSourceType, DocumentReference
from backend.services.identifier_search import (
    COPY_FIELDS_PROJECTION,
    EXACT_HIT_LIMIT,
    KIND_FIRMENBUCH,
    KIND_GESCHAEFTSZAHL,
    KIND_GRUNDBUCH,
    PINNED_SCORE,
    TITLE_MATCH_DOCUMENT_LIMIT,
    LegalIdentifier,
    build_exact_pipeline,
    build_exact_search_operator,
    build_source_filter,
    copy_group_key,
    detect_identifiers,
    fetch_exact_hits,
    fetch_title_hits,
    identifier_fetch_count,
    merge_identifier_results,
    vector_num_candidates,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class _AsyncCursor:
    """Async iterator over canned documents, like a Motor/PyMongo aggregate or find cursor."""

    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = [dict(doc) for doc in docs]

    def limit(self, count: int) -> "_AsyncCursor":
        self._docs = self._docs[:count]
        return self

    def sort(self, key: str, direction: int = 1) -> "_AsyncCursor":
        self._docs = sorted(self._docs, key=lambda doc: doc.get(key), reverse=direction == -1)
        return self

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def _matches(pattern: str, text: str) -> bool:
    """The module's source/title regexes with Python's engine (same constructs as PCRE)."""
    return re.search(pattern, text, re.IGNORECASE) is not None


def _title_match_collections(documents: List[Dict[str, Any]], chunks: List[Dict[str, Any]]) -> tuple[MagicMock, MagicMock]:
    """Fake documents/chunks collections whose ``find`` answers the title match with canned rows."""
    docs_collection = MagicMock()
    docs_collection.find = MagicMock(return_value=_AsyncCursor(documents))
    chunks_collection = MagicMock()
    chunks_collection.find = MagicMock(return_value=_AsyncCursor(chunks))
    return docs_collection, chunks_collection


def _doc(
    chunk_id: str,
    *,
    source: str,
    content_hash: str | None = None,
    chunk_index: int | None = None,
    content: str = "x" * 300,
    similarity: float = 0.5,
    title: str = "Doc",
) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "chunk_id": chunk_id,
        "document_id": f"doc-{chunk_id}",
        "content": content,
        "similarity": similarity,
        "metadata": {"title": title},
        "document_title": title,
        "document_source": source,
    }
    if content_hash is not None:
        doc["content_hash"] = content_hash
    if chunk_index is not None:
        doc["chunk_index"] = chunk_index
    return doc


def _pipeline_kind(pipeline: List[Dict[str, Any]]) -> str:
    first = pipeline[0]
    if "$vectorSearch" in first:
        return "vector"
    if "compound" in first.get("$search", {}):
        return "exact"
    return "text"


FIRMENBUCH_SOURCE = "F-BlatLj_GeweR/Firmenbuchabfrage, FN 404945h, ALPO CONSULT GmbH.pdf"
FINANZAMT_A = "Akten/ALPO/Finanzamt Bescheid.pdf"
FINANZAMT_B = "Documents/Kopien/Finanzamt Bescheid.pdf"
FINANZAMT_C = "Texte/alt/Finanzamt Bescheid.pdf"


FIRMENBUCH_DOCUMENT = {"_id": "doc-fb", "title": "Firmenbuchabfrage, FN 404945h, ALPO CONSULT GmbH",
                       "source": FIRMENBUCH_SOURCE, "content_hash": "H2"}
FIRMENBUCH_CHUNKS = [
    {"_id": "fb-1", "document_id": "doc-fb", "chunk_index": 1, "metadata": {"title": "Firmenbuchabfrage"},
     "content": "Geschäftszweig: Unternehmensberatung, Buchhaltung und Handel " + "x" * 250},
    {"_id": "fb-0", "document_id": "doc-fb", "chunk_index": 0, "metadata": {"title": "Firmenbuchabfrage"},
     "content": "aktuelle Firmenbuchdaten " + "x" * 280},
    {"_id": "fb-2", "document_id": "doc-fb", "chunk_index": 2, "metadata": {"title": "Firmenbuchabfrage"},
     "content": "Erstellt am 10.05.2021 " + "x" * 280},
]


def _canned_results() -> Dict[str, List[Dict[str, Any]]]:
    """Vector/text/exact results for 'ALPO CONSULT GmbH FN 404945h Geschäftszweig'.

    Three copies of the same Finanzamt letter (hash H1, chunk 0) crowd the fused
    list; the Firmenbuchabfrage chunk with the Geschäftszweig (``fb-1``) is found by
    the vector search only, its header chunk (``fb-0``) by the exact query only.
    """
    fin_a = _doc("fin-a", source=FINANZAMT_A, content_hash="H1", chunk_index=0, similarity=0.9)
    fin_b = _doc("fin-b", source=FINANZAMT_B, content_hash="H1", chunk_index=0, similarity=0.89)
    fin_c = _doc("fin-c", source=FINANZAMT_C, content_hash="H1", chunk_index=0, similarity=0.88)
    fb_1 = _doc("fb-1", source=FIRMENBUCH_SOURCE, content_hash="H2", chunk_index=1, similarity=0.6,
                content="Geschäftszweig: Unternehmensberatung, Buchhaltung und Handel " + "x" * 250)
    fb_0 = _doc("fb-0", source=FIRMENBUCH_SOURCE, content_hash="H2", chunk_index=0, similarity=12.5,
                content="Firmenbuchnummer FN 404945h ALPO CONSULT GmbH " + "x" * 250)
    other = _doc("other", source="Akten/Other/Ratenansuchen.pdf", content_hash="H3", chunk_index=0, similarity=0.7)
    return {
        "vector": [fin_a, fin_b, fb_1, fin_c],
        "text": [fin_a, fin_c, other, fin_b],
        "exact": [fb_0, dict(fb_1, similarity=11.0)],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Detection
# ─────────────────────────────────────────────────────────────────────────────

class TestDetection:
    @pytest.mark.parametrize("query", [
        "ALPO CONSULT GmbH, FN 404945h, Geschäftszweig",
        "Was ist der Geschäftszweig von FN404945h?",
        "Firmenbuch FN 404945 h",
        "fn 404945H (Firmenbuch)",
    ])
    def test_firmenbuchnummer_spellings(self, query: str) -> None:
        found = detect_identifiers(query)
        assert [i.kind for i in found] == [KIND_FIRMENBUCH]
        assert found[0].canonical == "FN 404945h"

    def test_firmenbuch_variants_cover_spaces_and_check_letter_case(self) -> None:
        (ident,) = detect_identifiers("FN 404945h")
        assert len(ident.groups) == 1
        for expected in ("FN 404945h", "FN404945h", "FN 404945 h", "404945h", "FN 404945H", "FN404945H"):
            assert expected in ident.variants
        assert len(ident.variants) == len(set(ident.variants))

    @pytest.mark.parametrize("query, canonical", [
        ("Strafsache 13 U 39/16v", "13 U 39/16v"),
        ("Protokoll 13 U 39/16 v vom 13.04.2016", "13 U 39/16v"),
        ("Urteil 17 Cg 114/11a", "17 Cg 114/11a"),
        ("038 C 1199/2017a Klage", "038 C 1199/2017a"),
        ("5 E 1149/20k Exekution", "5 E 1149/20k"),
        ("72 Cg 40/24t", "72 Cg 40/24t"),
        ("ON 12 zu 5 E 1149/20k-12", "5 E 1149/20k-12"),
        ("Akt 13 u 39/16 V", "13 u 39/16v"),
    ])
    def test_geschaeftszahl_spellings(self, query: str, canonical: str) -> None:
        found = detect_identifiers(query)
        assert [i.kind for i in found] == [KIND_GESCHAEFTSZAHL]
        assert found[0].canonical == canonical

    def test_geschaeftszahl_variants_with_and_without_space_before_check_letter(self) -> None:
        (ident,) = detect_identifiers("13 U 39/16v")
        assert set(ident.variants) == {"13 U 39/16v", "13 U 39/16 v", "13 U 39/16V", "13 U 39/16 V"}
        (spaced,) = detect_identifiers("13 U 39/16 v")
        assert set(spaced.variants) == set(ident.variants)

    def test_geschaeftszahl_variants_with_suffix_and_leading_zero(self) -> None:
        (ident,) = detect_identifiers("5 E 1149/20k-12")
        assert "5 E 1149/20k" in ident.variants
        assert "5 E 1149/20k-12" in ident.variants
        assert "5 E 1149/20 k-12" in ident.variants
        (bg,) = detect_identifiers("038 C 1199/2017a")
        assert "038 C 1199/2017a" in bg.variants
        assert "38 C 1199/2017a" in bg.variants

    def test_grundbuch_needs_ez_and_kg_together(self) -> None:
        (ident,) = detect_identifiers("Liegenschaft EZ 52 KG 11029 Lastenblatt")
        assert ident.kind == KIND_GRUNDBUCH
        assert ident.canonical == "EZ 52 KG 11029"
        assert ident.groups == (("EZ 52", "EZ52"), ("KG 11029", "KG11029"))
        assert detect_identifiers("EZ 52 Lastenblatt") == []
        assert detect_identifiers("KG 11029 Lastenblatt") == []
        (compact,) = detect_identifiers("EZ52 in KG11029")
        assert compact.canonical == "EZ 52 KG 11029"

    @pytest.mark.parametrize("query", [
        "",
        "Was ist der Geschäftszweig der ALPO CONSULT GmbH?",
        "Vertrag vom 12.03.2016 über 5 Stück zu 10/20 Euro",
        "Honorarnote vom 13 U 39/16 vom Gericht",
        "Rechnung 2024/15 und 15 kg Papier",
        "Wie hoch ist die Streitwertgrenze beim Bezirksgericht?",
        "Finanzamt Steuernummer 12 345/6789",
    ])
    def test_ordinary_queries_have_no_identifiers(self, query: str) -> None:
        assert detect_identifiers(query) == []

    def test_multiple_identifiers_keep_query_order_and_collapse_duplicates(self) -> None:
        found = detect_identifiers("FN 404945h, nochmals FN404945h, und 13 U 39/16v in EZ 52 KG 11029")
        assert [i.canonical for i in found] == ["FN 404945h", "13 U 39/16v", "EZ 52 KG 11029"]

    def test_firmenbuch_source_pattern_matches_file_names(self) -> None:
        (pattern,) = detect_identifiers("FN 404945h")[0].patterns
        for name in ("Firmenbuchabfrage, FN 404945h, ALPO CONSULT GmbH.pdf", "Auszug_FN404945h.pdf", "FN 404945 H"):
            assert _matches(pattern, name), name
        for name in ("FN 4049450h", "FN 404945hx", "Rechnung 404945h", "XFN 404945h"):
            assert not _matches(pattern, name), name

    def test_geschaeftszahl_source_pattern_tolerates_file_name_spellings(self) -> None:
        (pattern,) = detect_identifiers("17 Cg 114/11a")[0].patterns
        for name in (
            "DZ, Zustellung nach _112 ZPO, 17Cg114_11a, Beilage.pdf",
            "17 Cg 114-11a Urteil.pdf",
            "17 Cg 114/11 a",
            "017 Cg 114.11a",
        ):
            assert _matches(pattern, name), name
        for name in ("117 Cg 114/11a", "17 Cg 114/11ab", "17 Cg 1114/11a", "17 Cg 114/11"):
            assert not _matches(pattern, name), name

    def test_grundbuch_source_patterns_need_both_parts(self) -> None:
        patterns = detect_identifiers("EZ 52 KG 11029")[0].patterns
        assert len(patterns) == 2
        assert all(_matches(p, "Grundbuchsauszug EZ 52 KG 11029.pdf") for p in patterns)
        assert all(_matches(p, "EZ52_KG11029") for p in patterns)
        assert not all(_matches(p, "Grundbuchsauszug EZ 52.pdf") for p in patterns)
        assert not _matches(patterns[0], "EZ 520 KG 11029")


# ─────────────────────────────────────────────────────────────────────────────
# Exact query
# ─────────────────────────────────────────────────────────────────────────────

class TestExactQuery:
    def test_operator_is_phrase_should_without_fuzziness(self) -> None:
        operator = build_exact_search_operator(detect_identifiers("FN 404945h und 13 U 39/16v"), index="text_index")
        assert operator["index"] == "text_index"
        should = operator["compound"]["should"]
        assert operator["compound"]["minimumShouldMatch"] == 1
        assert all(set(clause) == {"phrase"} for clause in should)
        assert all(clause["phrase"]["path"] == "content" for clause in should)
        assert "fuzzy" not in str(operator)
        queries = {clause["phrase"]["query"] for clause in should}
        assert {"FN 404945h", "FN404945h", "13 U 39/16v", "13 U 39/16 v"} <= queries

    def test_grundbuch_requires_both_parts_in_the_chunk(self) -> None:
        (ident,) = detect_identifiers("EZ 52 KG 11029")
        operator = build_exact_search_operator([ident], index="text_index")
        (clause,) = operator["compound"]["should"]
        must = clause["compound"]["must"]
        assert len(must) == 2
        ez_queries = {c["phrase"]["query"] for c in must[0]["compound"]["should"]}
        kg_queries = {c["phrase"]["query"] for c in must[1]["compound"]["should"]}
        assert ez_queries == {"EZ 52", "EZ52"}
        assert kg_queries == {"KG 11029", "KG11029"}

    def test_pipeline_shape(self) -> None:
        pipeline = build_exact_pipeline(detect_identifiers("FN 404945h"), text_index="text_index", docs_collection="documents")
        assert list(pipeline[0]) == ["$search"]
        assert pipeline[1] == {"$limit": EXACT_HIT_LIMIT}
        assert pipeline[2]["$lookup"]["from"] == "documents"
        projection = pipeline[-1]["$project"]
        for field in ("chunk_id", "document_id", "content", "metadata", "document_title", "document_source"):
            assert field in projection
        assert projection["content_hash"] == COPY_FIELDS_PROJECTION["content_hash"]
        assert projection["chunk_index"] == 1
        assert projection["similarity"] == {"$meta": "searchScore"}

    async def test_fetch_exact_hits_tags_results(self) -> None:
        collection = MagicMock()
        collection.aggregate = MagicMock(return_value=_AsyncCursor([_doc("a", source="s"), _doc("b", source="t")]))
        hits = await fetch_exact_hits(collection, detect_identifiers("FN 404945h"), text_index="text_index", docs_collection="documents")
        assert [h["chunk_id"] for h in hits] == ["a", "b"]
        assert all(h["search_type"] == "exact" for h in hits)
        (pipeline,), _ = collection.aggregate.call_args
        assert "compound" in pipeline[0]["$search"]

    async def test_fetch_exact_hits_survives_a_failing_index(self) -> None:
        collection = MagicMock()
        collection.aggregate = MagicMock(side_effect=RuntimeError("index text_index not found"))
        hits = await fetch_exact_hits(collection, detect_identifiers("FN 404945h"), text_index="text_index", docs_collection="documents")
        assert hits == []

    async def test_fetch_exact_hits_without_identifiers_does_not_query(self) -> None:
        collection = MagicMock()
        assert await fetch_exact_hits(collection, [], text_index="text_index", docs_collection="documents") == []
        collection.aggregate.assert_not_called()

    def test_source_filter_shape(self) -> None:
        single = build_source_filter(detect_identifiers("FN 404945h"))
        assert set(single) == {"$or"}
        assert [list(clause) for clause in single["$or"]] == [["source"], ["title"]]
        assert single["$or"][0]["source"]["$options"] == "i"
        grundbuch = build_source_filter(detect_identifiers("EZ 52 KG 11029"))
        assert set(grundbuch) == {"$and"} and len(grundbuch["$and"]) == 2
        both = build_source_filter(detect_identifiers("FN 404945h und 13 U 39/16v"))
        assert len(both["$or"]) == 2 and all("$or" in clause for clause in both["$or"])

    async def test_fetch_title_hits_returns_the_documents_chunks_in_index_order(self) -> None:
        documents, chunks = _title_match_collections([FIRMENBUCH_DOCUMENT], FIRMENBUCH_CHUNKS)
        hits = await fetch_title_hits(documents, chunks, detect_identifiers("FN 404945h"))
        assert [h["chunk_id"] for h in hits] == ["fb-0", "fb-1", "fb-2"]
        assert all(h["search_type"] == "exact" for h in hits)
        assert hits[0]["document_source"] == FIRMENBUCH_SOURCE
        assert hits[0]["document_title"] == FIRMENBUCH_DOCUMENT["title"]
        assert hits[0]["content_hash"] == "H2" and hits[1]["chunk_index"] == 1
        assert hits[1]["content"].startswith("Geschäftszweig")
        (filter_arg, _), _ = documents.find.call_args
        assert "$or" in filter_arg
        (chunk_filter, _), _ = chunks.find.call_args
        assert chunk_filter == {"document_id": {"$in": ["doc-fb"]}}

    async def test_fetch_title_hits_caps_chunks_per_document(self) -> None:
        many = [{"_id": f"c{i}", "document_id": "doc-fb", "chunk_index": i, "content": "x" * 300, "metadata": {}} for i in range(12)]
        documents, chunks = _title_match_collections([FIRMENBUCH_DOCUMENT], many)
        hits = await fetch_title_hits(documents, chunks, detect_identifiers("FN 404945h"), chunks_per_document=3)
        assert [h["chunk_id"] for h in hits] == ["c0", "c1", "c2"]

    async def test_fetch_title_hits_without_matching_documents_skips_the_chunk_query(self) -> None:
        documents, chunks = _title_match_collections([], [])
        assert await fetch_title_hits(documents, chunks, detect_identifiers("FN 404945h")) == []
        chunks.find.assert_not_called()

    async def test_fetch_exact_hits_combines_phrase_and_title_hits_without_duplicates(self) -> None:
        documents, chunks = _title_match_collections([FIRMENBUCH_DOCUMENT], FIRMENBUCH_CHUNKS)
        chunks.aggregate = MagicMock(return_value=_AsyncCursor([
            _doc("letter", source="Akten/Brief.pdf", content_hash="H9", chunk_index=0, similarity=9.0),
            _doc("fb-1", source=FIRMENBUCH_SOURCE, content_hash="H2", chunk_index=1, similarity=8.0),
        ]))
        hits = await fetch_exact_hits(
            chunks, detect_identifiers("FN 404945h"), text_index="text_index", docs_collection="documents", documents=documents,
        )
        assert [h["chunk_id"] for h in hits] == ["letter", "fb-1", "fb-0", "fb-2"]
        assert all(h["search_type"] == "exact" for h in hits)

    async def test_fetch_exact_hits_keeps_title_hits_when_the_phrase_query_fails(self) -> None:
        documents, chunks = _title_match_collections([FIRMENBUCH_DOCUMENT], FIRMENBUCH_CHUNKS)
        chunks.aggregate = MagicMock(side_effect=RuntimeError("mongot down"))
        hits = await fetch_exact_hits(
            chunks, detect_identifiers("FN 404945h"), text_index="text_index", docs_collection="documents", documents=documents,
        )
        assert [h["chunk_id"] for h in hits] == ["fb-0", "fb-1", "fb-2"]

    async def test_fetch_exact_hits_keeps_phrase_hits_when_the_title_match_fails(self) -> None:
        documents = MagicMock()
        documents.find = MagicMock(side_effect=RuntimeError("regex too long"))
        chunks = MagicMock()
        chunks.aggregate = MagicMock(return_value=_AsyncCursor([_doc("a", source="s")]))
        hits = await fetch_exact_hits(
            chunks, detect_identifiers("FN 404945h"), text_index="text_index", docs_collection="documents", documents=documents,
        )
        assert [h["chunk_id"] for h in hits] == ["a"]

    def test_candidate_budget(self) -> None:
        assert identifier_fetch_count(10) == 50
        assert vector_num_candidates(50) == 1000
        assert vector_num_candidates(2) == 100  # never below the ordinary path's value


# ─────────────────────────────────────────────────────────────────────────────
# Merge / collapse
# ─────────────────────────────────────────────────────────────────────────────

class TestMerge:
    def test_pinned_hits_come_first_then_the_fused_list(self) -> None:
        fused = [_doc("f1", source="a"), _doc("f2", source="b"), _doc("f3", source="c")]
        exact = [_doc("e1", source="d")]
        merged = merge_identifier_results(exact, fused, limit=10)
        assert [m["chunk_id"] for m in merged] == ["e1", "f1", "f2", "f3"]
        assert merged[0]["metadata"]["exact_identifier_match"] is True
        assert merged[0]["similarity"] == PINNED_SCORE and merged[0]["rrf_score"] == PINNED_SCORE
        assert "exact_identifier_match" not in merged[1]["metadata"]
        assert merged[1]["similarity"] == 0.5  # fused scores untouched

    def test_exact_hits_the_normal_search_also_found_lead_the_pinned_block(self) -> None:
        fused = [_doc("f1", source="a"), _doc("shared", source="b")]
        exact = [_doc("only-exact", source="c"), _doc("shared", source="b")]
        merged = merge_identifier_results(exact, fused, limit=10)
        assert [m["chunk_id"] for m in merged] == ["shared", "only-exact", "f1"]

    def test_copies_collapse_into_one_representative_with_locations(self) -> None:
        fused = [
            _doc("fin-a", source=FINANZAMT_A, content_hash="H1", chunk_index=0),
            _doc("fin-b", source=FINANZAMT_B, content_hash="H1", chunk_index=0),
            _doc("fin-a-p2", source=FINANZAMT_A, content_hash="H1", chunk_index=1),
            _doc("fin-c", source=FINANZAMT_C, content_hash="H1", chunk_index=0),
        ]
        merged = merge_identifier_results([], fused, limit=10)
        assert [m["chunk_id"] for m in merged] == ["fin-a", "fin-a-p2"]
        assert merged[0]["metadata"]["locations"] == [FINANZAMT_B, FINANZAMT_C]
        assert merged[1]["metadata"]["locations"] == []

    def test_pinned_hit_absorbs_its_copies_from_the_fused_list(self) -> None:
        fused = [_doc("copy", source="Documents/Kopie.pdf", content_hash="H2", chunk_index=0)]
        exact = [_doc("orig", source="Akten/Original.pdf", content_hash="H2", chunk_index=0)]
        merged = merge_identifier_results(exact, fused, limit=10)
        assert [m["chunk_id"] for m in merged] == ["orig"]
        assert merged[0]["metadata"]["locations"] == ["Documents/Kopie.pdf"]

    def test_copies_beyond_the_limit_still_contribute_locations(self) -> None:
        fused = [_doc("a", source="one", content_hash="H", chunk_index=0)]
        fused += [_doc(f"x{i}", source=f"x{i}", content_hash=f"X{i}", chunk_index=0) for i in range(5)]
        fused.append(_doc("a-copy", source="two", content_hash="H", chunk_index=0))
        merged = merge_identifier_results([], fused, limit=2)
        assert [m["chunk_id"] for m in merged] == ["a", "x0"]
        assert merged[0]["metadata"]["locations"] == ["two"]

    def test_without_both_copy_fields_only_the_chunk_id_collapses(self) -> None:
        assert copy_group_key("H", 0, "id") == ("hash", "H", 0)
        assert copy_group_key("H", None, "id") == ("id", "id")
        assert copy_group_key(None, 0, "id") == ("id", "id")
        fused = [
            _doc("a", source="one", content_hash="H"),  # no chunk_index
            _doc("b", source="two", content_hash="H"),
            _doc("a", source="one", content_hash="H"),
        ]
        merged = merge_identifier_results([], fused, limit=10)
        assert [m["chunk_id"] for m in merged] == ["a", "b"]

    def test_limit_is_respected_and_metadata_is_copied(self) -> None:
        source_doc = _doc("a", source="one")
        merged = merge_identifier_results([source_doc], [_doc("b", source="two"), _doc("c", source="three")], limit=2)
        assert [m["chunk_id"] for m in merged] == ["a", "b"]
        assert "exact_identifier_match" not in source_doc["metadata"]
        assert source_doc["similarity"] == 0.5

    def test_full_f25_scenario(self) -> None:
        canned = _canned_results()
        fused = canned["vector"] + [doc for doc in canned["text"] if doc["chunk_id"] == "other"]
        merged = merge_identifier_results(canned["exact"], fused, limit=3)
        assert [m["chunk_id"] for m in merged] == ["fb-1", "fb-0", "fin-a"]
        assert merged[0]["document_source"] == FIRMENBUCH_SOURCE
        assert merged[2]["metadata"]["locations"] == [FINANZAMT_B, FINANZAMT_C]


# ─────────────────────────────────────────────────────────────────────────────
# hybrid_search endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestHybridEndpoint:
    @pytest.fixture
    def embedding(self):
        with patch("backend.routers.search.get_embedding", new=AsyncMock(return_value=[0.1] * 8)):
            yield

    def test_ordinary_query_bypasses_the_identifier_path(self, client: TestClient, mock_db, embedding) -> None:
        aggregate = mock_db.chunks_collection.aggregate
        aggregate.reset_mock()
        response = client.post("/api/v1/search/hybrid", json={"query": "Geschäftszweig ALPO CONSULT GmbH", "match_count": 10})
        assert response.status_code == 200, response.text
        pipelines = [call.args[0] for call in aggregate.call_args_list]
        assert [_pipeline_kind(p) for p in pipelines] == ["vector", "text"]
        vector_stage = pipelines[0][0]["$vectorSearch"]
        assert vector_stage == {**vector_stage, "numCandidates": 100, "limit": 20}
        assert pipelines[1][1] == {"$limit": 20}
        assert "content_hash" not in pipelines[0][-1]["$project"]
        assert "content_hash" not in pipelines[1][-1]["$project"]

    def test_identifier_query_pins_exact_hits_and_collapses_copies(self, client: TestClient, mock_db, embedding) -> None:
        canned = _canned_results()
        aggregate = mock_db.chunks_collection.aggregate
        aggregate.reset_mock()
        aggregate.side_effect = lambda pipeline: _AsyncCursor(canned[_pipeline_kind(pipeline)])
        try:
            response = client.post(
                "/api/v1/search/hybrid",
                json={"query": "ALPO CONSULT GmbH FN 404945h Geschäftszweig", "match_count": 3},
            )
        finally:
            aggregate.side_effect = None
        assert response.status_code == 200, response.text

        pipelines = [call.args[0] for call in aggregate.call_args_list]
        assert [_pipeline_kind(p) for p in pipelines] == ["vector", "text", "exact"]
        vector_stage = pipelines[0][0]["$vectorSearch"]
        assert vector_stage["limit"] == 15
        assert vector_stage["numCandidates"] >= 20 * vector_stage["limit"]
        assert pipelines[1][1] == {"$limit": 15}
        assert pipelines[0][-1]["$project"]["content_hash"] == "$doc_info.content_hash"
        assert pipelines[1][-1]["$project"]["chunk_index"] == 1
        assert pipelines[2][1] == {"$limit": EXACT_HIT_LIMIT}

        results = response.json()["results"]
        assert [r["chunk_id"] for r in results] == ["fb-1", "fb-0", "fin-a"]
        assert results[0]["document_source"] == FIRMENBUCH_SOURCE
        assert results[0]["metadata"]["exact_identifier_match"] is True
        assert results[0]["similarity"] == PINNED_SCORE
        assert results[2]["metadata"]["locations"] == [FINANZAMT_B, FINANZAMT_C]
        assert "exact_identifier_match" not in results[2]["metadata"]

    def test_identifier_only_in_the_file_name_still_pins_the_document(self, client: TestClient, mock_db, embedding) -> None:
        """F25 as observed live: the extract's text lost the FN, only its file name carries it."""
        canned = _canned_results()
        canned["exact"] = []  # no chunk text contains the number
        fused_only = {k: [d for d in v if not d["chunk_id"].startswith("fb-")] for k, v in canned.items()}
        collection = mock_db.chunks_collection  # the conftest mock serves documents and chunks alike
        collection.aggregate.reset_mock()
        collection.aggregate.side_effect = lambda pipeline: _AsyncCursor(fused_only[_pipeline_kind(pipeline)])
        collection.find.side_effect = lambda flt, *args, **kwargs: _AsyncCursor(
            FIRMENBUCH_CHUNKS if "document_id" in flt else [FIRMENBUCH_DOCUMENT]
        )
        try:
            response = client.post(
                "/api/v1/search/hybrid",
                json={"query": "ALPO CONSULT GmbH FN 404945h Geschäftszweig", "match_count": 4},
            )
        finally:
            collection.aggregate.side_effect = None
            collection.find.side_effect = None
        assert response.status_code == 200, response.text
        results = response.json()["results"]
        assert [r["chunk_id"] for r in results] == ["fb-0", "fb-1", "fb-2", "fin-a"]
        assert all(FIRMENBUCH_SOURCE == r["document_source"] for r in results[:3])
        assert results[0]["metadata"]["exact_identifier_match"] is True
        # Without the extract in the vector list, copy C (text rank 1) fuses above copy B (text rank 3).
        assert results[3]["metadata"]["locations"] == [FINANZAMT_C, FINANZAMT_B]


# ─────────────────────────────────────────────────────────────────────────────
# Federated path
# ─────────────────────────────────────────────────────────────────────────────

def _federated_search_with(
    canned: Dict[str, List[Dict[str, Any]]],
    title_documents: List[Dict[str, Any]] | None = None,
    title_chunks: List[Dict[str, Any]] | None = None,
) -> tuple[FederatedSearch, MagicMock]:
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value={"_id": "x"})
    collection.aggregate = MagicMock(side_effect=lambda pipeline: _AsyncCursor(canned[_pipeline_kind(pipeline)]))
    collection.find = MagicMock(side_effect=lambda flt, *args, **kwargs: _AsyncCursor(
        (title_chunks or []) if "document_id" in flt else (title_documents or [])
    ))
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=collection)
    fs = FederatedSearch()
    fs._client = MagicMock()
    fs._client.__getitem__ = MagicMock(return_value=db)
    return fs, collection


class TestFederatedPath:
    async def test_search_database_pins_exact_hits_and_collapses_copies(self) -> None:
        fs, collection = _federated_search_with(_canned_results())
        results = await fs._search_database(
            "rag_test", "chunks", "documents",
            "ALPO CONSULT GmbH FN 404945h Geschäftszweig", [0.1] * 8, match_count=3,
        )
        pipelines = [call.args[0] for call in collection.aggregate.call_args_list]
        assert [_pipeline_kind(p) for p in pipelines] == ["vector", "text", "exact"]
        vector_stage = pipelines[0][0]["$vectorSearch"]
        assert vector_stage["limit"] == 15 and vector_stage["numCandidates"] == 300
        assert pipelines[1][1] == {"$limit": 15}
        assert pipelines[0][-1]["$project"]["content_hash"] == "$doc_info.content_hash"

        assert [r["chunk_id"] for r in results] == ["fb-1", "fb-0", "fin-a"]
        assert results[0]["rrf_score"] == PINNED_SCORE
        assert results[0]["metadata"]["exact_identifier_match"] is True
        assert results[2]["metadata"]["locations"] == [FINANZAMT_B, FINANZAMT_C]
        assert 0.5 <= results[2]["rrf_score"] <= 1.0

    async def test_search_database_pins_a_document_named_after_the_identifier(self) -> None:
        canned = _canned_results()
        canned["exact"] = []
        fused_only = {k: [d for d in v if not d["chunk_id"].startswith("fb-")] for k, v in canned.items()}
        fs, collection = _federated_search_with(fused_only, [FIRMENBUCH_DOCUMENT], FIRMENBUCH_CHUNKS)
        results = await fs._search_database(
            "rag_test", "chunks", "documents",
            "ALPO CONSULT GmbH FN 404945h Geschäftszweig", [0.1] * 8, match_count=4,
        )
        assert [r["chunk_id"] for r in results] == ["fb-0", "fb-1", "fb-2", "fin-a"]
        assert results[0]["rrf_score"] == PINNED_SCORE and results[0]["content_hash"] == "H2"
        assert results[3]["metadata"]["locations"] == [FINANZAMT_C, FINANZAMT_B]
        (doc_filter, _), _ = collection.find.call_args_list[0]
        assert "$or" in doc_filter

    async def test_search_database_ordinary_query_is_unchanged(self) -> None:
        canned = _canned_results()
        for doc in canned["vector"] + canned["text"]:
            doc.pop("content_hash", None)
            doc.pop("chunk_index", None)
        fs, collection = _federated_search_with(canned)
        results = await fs._search_database(
            "rag_test", "chunks", "documents", "Geschäftszweig ALPO CONSULT GmbH", [0.1] * 8, match_count=3,
        )
        pipelines = [call.args[0] for call in collection.aggregate.call_args_list]
        assert [_pipeline_kind(p) for p in pipelines] == ["vector", "text"]
        vector_stage = pipelines[0][0]["$vectorSearch"]
        assert vector_stage["limit"] == 6 and vector_stage["numCandidates"] == 100
        assert pipelines[1][1] == {"$limit": 6}
        assert "content_hash" not in pipelines[0][-1]["$project"]
        collection.find.assert_not_called()
        assert len(results) == 3
        assert all("exact_identifier_match" not in r["metadata"] for r in results)
        assert all("locations" not in r["metadata"] for r in results)

    async def test_search_database_text_mode_merges_exact_hits_too(self) -> None:
        fs, collection = _federated_search_with(_canned_results())
        results = await fs._search_database(
            "rag_test", "chunks", "documents", "FN 404945h", [0.1] * 8, match_count=3, search_type="text",
        )
        assert [_pipeline_kind(call.args[0]) for call in collection.aggregate.call_args_list] == ["text", "exact"]
        assert [r["chunk_id"] for r in results] == ["fb-0", "fb-1", "fin-a"]
        assert results[2]["metadata"]["locations"] == [FINANZAMT_C, FINANZAMT_B]

    def test_apply_rrf_pins_exact_hits_ahead_of_the_fusion(self) -> None:
        fs = FederatedSearch()
        results = [
            {"chunk_id": "v1", "search_type": "vector", "content": "x" * 300, "similarity": 0.9},
            {"chunk_id": "t1", "search_type": "text", "content": "x" * 300, "similarity": 0.7},
            {"chunk_id": "e1", "search_type": "exact", "content": "x" * 300, "similarity": 9.0},
        ]
        merged = fs._apply_rrf(results, limit=5)
        assert [m["chunk_id"] for m in merged] == ["e1", "v1", "t1"]
        assert merged[0]["rrf_score"] == PINNED_SCORE
        assert merged[0]["metadata"]["exact_identifier_match"] is True
        # The non-exact part went through the normal fusion (scores in its 0-1 range).
        assert all(0.0 < m["rrf_score"] <= 1.0 for m in merged[1:])
        assert merged[1]["rrf_score"] >= merged[2]["rrf_score"]

    def test_apply_rrf_keeps_strategy_fusion_for_the_non_exact_part(self) -> None:
        from types import SimpleNamespace

        fs = FederatedSearch()
        seen: Dict[str, Any] = {}

        def custom(results: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
            seen["results"] = results
            seen["limit"] = limit
            return [{"chunk_id": "custom", "metadata": {}}]

        merged = fs._apply_rrf(
            [
                {"chunk_id": "v1", "search_type": "vector", "content": "x" * 250},
                {"chunk_id": "e1", "search_type": "exact", "content": "x" * 250},
            ],
            limit=5,
            strategy=SimpleNamespace(calculate_rrf_scores=custom),
        )
        assert [m["chunk_id"] for m in merged] == ["e1", "custom"]
        assert [r["chunk_id"] for r in seen["results"]] == ["v1"]  # exact hits never reach the strategy

    def test_apply_rrf_without_exact_hits_is_the_old_fusion(self) -> None:
        fs = FederatedSearch()
        merged = fs._apply_rrf(
            [{"chunk_id": "v1", "search_type": "vector", "content": "x" * 300, "similarity": 0.9}], limit=5,
        )
        assert [m["chunk_id"] for m in merged] == ["v1"]
        assert "locations" not in merged[0].get("metadata", {})

    @staticmethod
    def _ref(chunk_id: str, score: float, source: str, **metadata: Any) -> DocumentReference:
        return DocumentReference(
            id=chunk_id, document_id=f"d-{chunk_id}", title="Doc", source_type=DataSourceType.PROFILE,
            source_database="db", document_source=source, excerpt="...", similarity_score=score, metadata=metadata,
        )

    def test_deduplicate_and_rank_collapses_by_hash_and_keeps_pinned_first(self) -> None:
        fs = FederatedSearch()
        docs = [
            self._ref("fin-a", 0.95, FINANZAMT_A, content_hash="H1", chunk_index=0),
            self._ref("fb-0", 1.0, FIRMENBUCH_SOURCE, content_hash="H2", chunk_index=0, exact_identifier_match=True),
            self._ref("fin-b", 0.97, FINANZAMT_B, content_hash="H1", chunk_index=0, locations=[FINANZAMT_C]),
            self._ref("other", 1.0, "Akten/Other.pdf", content_hash="H3", chunk_index=0),
        ]
        ranked = fs._deduplicate_and_rank(docs, limit=10)
        assert [d.id for d in ranked] == ["fb-0", "other", "fin-b"]
        assert ranked[2].metadata["locations"] == [FINANZAMT_C, FINANZAMT_A]

    def test_deduplicate_and_rank_without_hash_uses_the_chunk_id(self) -> None:
        fs = FederatedSearch()
        docs = [
            self._ref("c1", 0.8, "a.pdf"),
            self._ref("c1", 0.9, "a.pdf"),
            self._ref("c2", 0.85, "b.pdf", content_hash="H", chunk_index=0),
            self._ref("c3", 0.7, "c.pdf", content_hash="H"),  # no chunk_index: not a copy of c2
        ]
        ranked = fs._deduplicate_and_rank(docs, limit=10)
        assert [(d.id, d.similarity_score) for d in ranked] == [("c1", 0.9), ("c2", 0.85), ("c3", 0.7)]

    async def test_search_carries_copy_fields_into_document_metadata(self) -> None:
        fs, _ = _federated_search_with(_canned_results())
        fs._embedding_client = MagicMock()
        fs.get_embedding = AsyncMock(return_value=[0.1] * 8)
        fs.existing_databases = AsyncMock(return_value=None)
        documents, meta = await fs.search(
            query="ALPO CONSULT GmbH FN 404945h Geschäftszweig",
            user_id="u1", user_email="user@example.com",
            sources=["profile"], active_profile_key="p", active_profile_database="rag_p", match_count=3,
        )
        assert meta["sources_searched"] == 1
        assert [d.id for d in documents] == ["fb-1", "fb-0", "fin-a"]
        assert documents[0].metadata["exact_identifier_match"] is True
        assert documents[0].metadata["content_hash"] == "H2" and documents[0].metadata["chunk_index"] == 1
        assert documents[2].metadata["locations"] == [FINANZAMT_B, FINANZAMT_C]
