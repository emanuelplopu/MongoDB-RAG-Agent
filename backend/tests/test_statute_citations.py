"""Statute citations as identifiers (F34): detection, text stage, semantic guarantee, both callers.

Covers the statute part of ``backend/services/identifier_search.py`` and its use
in ``hybrid_search`` (``backend/routers/search.py``) and
``FederatedSearch._search_database`` / ``_apply_rrf``. The PR #28 kinds (FN,
Grundbuch, Geschäftszahl) and ordinary questions must be untouched.
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
    KIND_GESCHAEFTSZAHL,
    KIND_STATUTE,
    PINNED_SCORE,
    SEMANTIC_GUARANTEE_COUNT,
    TEXT_FUZZY,
    build_exact_search_operator,
    detect_identifiers,
    fetch_exact_hits,
    fetch_title_hits,
    fuzzy_remainder,
    has_statute_citation,
    merge_identifier_results,
    plan_text_stage,
    semantic_guarantee,
)
from backend.tests.test_identifier_search import _AsyncCursor, _doc

F34_QUERY = "Was regelt § 1 TKG 2021 (Zweck und Ziele) laut dem Gesetzestext in der Fassung vom 18.08.2022?"
F34_QUERY_WITHOUT_CITATION = "Was regelt Zweck und Ziele laut dem Gesetzestext in der Fassung vom 18.08.2022?"
STATUTE_SOURCE = "FP-DIETR-MAGENTA/Recherche/TKG\xa02021, Fassung vom 18.08.2022.pdf"
STATUTE_TITLE = "TKG\xa02021, Fassung vom 18.08.2022"
SECTION_TEXT = (
    "Zweck und Ziele\n- § 1. (1) Zweck dieses Bundesgesetzes ist es, durch Förderung des Wettbewerbes "
    "im Bereich der elektronischen Kommunikation die Versorgung der Bevölkerung " + "x" * 300
)
TOC_TEXT = "Allgemeines\n- § 1. Zweck und Ziele\n- § 2. Ausnahmen vom Anwendungsbereich\n- § 3. Zuschüsse " + "x" * 200
CITING_TEXT = "Begriffsbestimmungen (Verbraucher gemäß § 1 des Bundesgesetz vom 8. März 1979) " + "x" * 300


def _matches(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


# ─────────────────────────────────────────────────────────────────────────────
# Detection
# ─────────────────────────────────────────────────────────────────────────────

class TestStatuteDetection:
    @pytest.mark.parametrize("query, canonical", [
        (F34_QUERY, "§ 1 TKG 2021"),
        ("§ 365 ZPO", "§ 365 ZPO"),
        ("Was sagt § 3 Abs 1 MRG zum Mietzins?", "§ 3 Abs 1 MRG"),
        ("§ 3 Abs. 1 MRG", "§ 3 Abs 1 MRG"),
        ("Art 6 EMRK", "Art 6 EMRK"),
        ("Art. 6 Abs 1 EMRK", "Art 6 Abs 1 EMRK"),
        ("Artikel 10 B-VG", "Art 10 B-VG"),
        ("§§ 332, 365 ZPO", "§§ 332, 365 ZPO"),
        ("§ 52d ÄrzteG", "§ 52d ÄrzteG"),
        ("§ 1 Abs 1 Z 3 lit a UrhG", "§ 1 Abs 1 Z 3 lit a UrhG"),
        ("§ 1295 des ABGB", "§ 1295 ABGB"),
        ("§ 16 Abs 3 WEG", "§ 16 Abs 3 WEG"),
    ])
    def test_citations(self, query: str, canonical: str) -> None:
        found = detect_identifiers(query)
        assert [i.kind for i in found] == [KIND_STATUTE]
        assert found[0].canonical == canonical
        assert found[0].length > 0 and query[found[0].position:found[0].end].strip() != ""

    @pytest.mark.parametrize("query, canonical", [
        ("Was regelt das TKG 2021 zur Netzneutralität?", "TKG 2021"),
        ("Telekommunikationsgesetz 2021", "Telekommunikationsgesetz 2021"),
        ("Gewerbeordnung 1994", "Gewerbeordnung 1994"),
        ("EStG 1988 Werbungskosten", "EStG 1988"),
    ])
    def test_bare_statute_names_with_a_year(self, query: str, canonical: str) -> None:
        (ident,) = detect_identifiers(query)
        assert ident.kind == KIND_STATUTE
        assert ident.canonical == canonical
        # A bare name pins nothing: no phrase group, no title pattern, no section.
        assert ident.groups == () and ident.patterns == () and ident.section_patterns == ()
        assert ident.name_variants[0] == canonical

    def test_the_f34_query_yields_one_identifier_not_the_bare_name_too(self) -> None:
        assert [i.canonical for i in detect_identifiers(F34_QUERY)] == ["§ 1 TKG 2021"]

    @pytest.mark.parametrize("query", [
        "Wie hoch ist die Streitwertgrenze beim Bezirksgericht?",
        F34_QUERY_WITHOUT_CITATION,
        "Was war der Umsatz im GJ 2021?",           # Geschäftsjahr, not a statute
        "Bilanz der XY AG 2021",                    # two-letter company form
        "OGH 2021 Entscheidung",                    # a court
        "§ 5 der Satzung regelt die Kündigung",     # no statute name after the section
        "Artikel 5 des Vertrags",
        "Was regelt § 7?",
        "§ 1 Satzung",
        "Ziel 1 der Richtlinie",
        "Rechnung Nr 2021/15",
        "KG 2021",
        "COVID 2020 Maßnahmen",
        "Vertrag 2021",
        "Honorarnote vom 13 U 39/16 vom Gericht",
    ])
    def test_ordinary_questions_do_not_trigger(self, query: str) -> None:
        assert detect_identifiers(query) == []
        assert not has_statute_citation([])

    def test_aktenzeichen_still_take_the_pr28_path(self) -> None:
        (gz,) = detect_identifiers("Strafsache 13 U 39/16v")
        assert gz.kind == KIND_GESCHAEFTSZAHL
        assert not has_statute_citation([gz])
        both = detect_identifiers("Urteil 17 Cg 114/11a zu § 52d ÄrzteG")
        assert [(i.kind, i.canonical) for i in both] == [
            (KIND_GESCHAEFTSZAHL, "17 Cg 114/11a"), (KIND_STATUTE, "§ 52d ÄrzteG"),
        ]

    def test_citation_variants_always_name_the_statute(self) -> None:
        (ident,) = detect_identifiers("§ 1 TKG 2021")
        assert ident.variants == ("§ 1 TKG 2021", "§ 1 TKG")
        assert ident.name_variants == ("TKG 2021", "TKG")
        (mrg,) = detect_identifiers("§ 3 Abs 1 MRG")
        assert set(mrg.variants) == {"§ 3 MRG", "§ 3 Abs 1 MRG", "§ 3 Abs. 1 MRG"}
        (art,) = detect_identifiers("Art 6 EMRK")
        assert set(art.variants) == {"Art 6 EMRK", "Art. 6 EMRK", "Artikel 6 EMRK"}
        (zpo,) = detect_identifiers("§§ 332, 365 ZPO")
        assert set(zpo.variants) == {"§ 332 ZPO", "§ 365 ZPO", "§§ 332, 365 ZPO"}
        assert len(zpo.section_patterns) == 2
        for ident in (ident, mrg, art, zpo):
            # Under lucene.standard "§ 1" is the bare token "1"; no variant may be that.
            assert all(re.search(r"[A-Za-zÄÖÜ]{2}", v) for v in ident.variants)

    def test_title_pattern_tolerates_the_no_break_space_and_needs_the_year_when_given(self) -> None:
        (pattern,) = detect_identifiers("§ 1 TKG 2021")[0].patterns
        for name in (STATUTE_SOURCE, STATUTE_TITLE, "TKG_2021.pdf", "TKG 2021", "tkg2021"):
            assert _matches(pattern, name), name
        for name in ("Gesetzestexte/TKG Novelle 05.pdf", "ATKG 2021", "TKG 2003"):
            assert not _matches(pattern, name), name
        (bare,) = detect_identifiers("§ 365 ZPO")[0].patterns
        assert _matches(bare, "DZ, Zustellung nach _112 ZPO, 17Cg114_11a, Beilage.pdf")
        assert not _matches(bare, "ZPOX.pdf")

    def test_section_pattern_matches_the_ris_heading_but_not_other_sections(self) -> None:
        (pattern,) = detect_identifiers("§ 1 TKG 2021")[0].section_patterns
        for text in (SECTION_TEXT, TOC_TEXT, "§1 (1) Zweck", "§§ 1 bis 3", "§\xa01."):
            assert re.search(pattern, text), text
        for text in ("§ 10. Frequenzen", "§ 1a. Begriffe", "§ 11 Abs. 4", "Ziel 1: Sicherung"):
            assert not re.search(pattern, text), text
        (art,) = detect_identifiers("Art 6 EMRK")[0].section_patterns
        assert re.search(art, "Artikel 6 – Recht auf ein faires Verfahren")
        assert not re.search(art, "Art. 60")


# ─────────────────────────────────────────────────────────────────────────────
# Text stage and remainder
# ─────────────────────────────────────────────────────────────────────────────

def _old_text_operator(query: str) -> Dict[str, Any]:
    return {"index": "text_index", "text": {"query": query, "path": "content", "fuzzy": {"maxEdits": 2, "prefixLength": 3}}}


class TestTextStage:
    @pytest.mark.parametrize("query", [
        F34_QUERY_WITHOUT_CITATION,
        "ALPO CONSULT GmbH FN 404945h Geschäftszweig",
        "Strafsache 13 U 39/16v",
        "Liegenschaft EZ 52 KG 11029",
    ])
    def test_queries_without_a_statute_keep_the_old_fuzzy_operator(self, query: str) -> None:
        plan = plan_text_stage(query, detect_identifiers(query), index="text_index")
        assert plan.mode == "fuzzy"
        assert plan.operator == _old_text_operator(query)
        assert plan.describe() == "fuzzy text stage"

    def test_statute_query_is_a_phrase_compound_without_pure_numbers(self) -> None:
        identifiers = detect_identifiers(F34_QUERY)
        plan = plan_text_stage(F34_QUERY, identifiers, index="text_index")
        assert plan.mode == "statute"
        compound = plan.operator["compound"]
        assert plan.operator["index"] == "text_index" and compound["minimumShouldMatch"] == 1
        phrases = [c["phrase"]["query"] for c in compound["should"] if "phrase" in c]
        assert phrases == ["§ 1 TKG 2021", "§ 1 TKG", "TKG 2021", "TKG"] == list(plan.phrases)
        (text_clause,) = [c["text"] for c in compound["should"] if "text" in c]
        assert text_clause["query"] == plan.remainder == "Was regelt (Zweck und Ziele) laut dem Gesetzestext in der Fassung vom"
        assert text_clause["fuzzy"] == TEXT_FUZZY == {"maxEdits": 2, "prefixLength": 3}
        for token in text_clause["query"].split():
            assert re.search(r"[A-Za-zÄÖÜäöüß]", token), token
        assert "18.08.2022" not in text_clause["query"] and "2021" not in text_clause["query"]
        assert "fuzzy" not in str([c for c in compound["should"] if "phrase" in c])
        assert "statute phrase stage" in plan.describe()

    def test_a_citation_alone_has_no_fuzzy_clause(self) -> None:
        plan = plan_text_stage("§ 365 ZPO", detect_identifiers("§ 365 ZPO"), index="text_index")
        assert all("phrase" in c for c in plan.operator["compound"]["should"])
        assert plan.remainder == ""

    def test_remainder_keeps_other_identifier_kinds_and_drops_numbers(self) -> None:
        query = "Wie wurde § 52d ÄrzteG in 17 Cg 114/11a am 12.03.2016 angewendet?"
        assert fuzzy_remainder(query, detect_identifiers(query)) == "Wie wurde in Cg 114/11a am angewendet?"

    def test_bare_statute_name_gets_phrases_but_no_exact_operator(self) -> None:
        identifiers = detect_identifiers("Was regelt das TKG 2021 zur Netzneutralität?")
        plan = plan_text_stage("Was regelt das TKG 2021 zur Netzneutralität?", identifiers, index="text_index")
        assert plan.phrases == ("TKG 2021", "TKG")
        assert build_exact_search_operator(identifiers, index="text_index")["compound"]["should"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Title hits of the statute's own text
# ─────────────────────────────────────────────────────────────────────────────

STATUTE_DOCUMENT = {"_id": "doc-tkg", "title": STATUTE_TITLE, "source": STATUTE_SOURCE, "content_hash": "HT"}
STATUTE_CHUNKS = [
    {"_id": "tkg-2", "document_id": "doc-tkg", "chunk_index": 2, "metadata": {}, "content": TOC_TEXT},
    {"_id": "tkg-16", "document_id": "doc-tkg", "chunk_index": 16, "metadata": {}, "content": SECTION_TEXT},
    {"_id": "tkg-29", "document_id": "doc-tkg", "chunk_index": 29, "metadata": {}, "content": CITING_TEXT},
]


def _collections(documents: List[Dict[str, Any]], chunks: List[Dict[str, Any]]) -> tuple[MagicMock, MagicMock]:
    docs_collection = MagicMock()
    docs_collection.find = MagicMock(return_value=_AsyncCursor(documents))
    chunks_collection = MagicMock()
    chunks_collection.find = MagicMock(return_value=_AsyncCursor(chunks))
    return docs_collection, chunks_collection


class TestStatuteTitleHits:
    async def test_section_headings_beat_chunks_that_merely_cite_the_section(self) -> None:
        documents, chunks = _collections([STATUTE_DOCUMENT], STATUTE_CHUNKS)
        hits = await fetch_title_hits(documents, chunks, detect_identifiers("§ 1 TKG 2021"))
        assert [h["chunk_id"] for h in hits] == ["tkg-2", "tkg-16"]
        assert all(h["search_type"] == "exact" and h["content_hash"] == "HT" for h in hits)
        chunk_filter = chunks.find.call_args.args[0]
        assert chunk_filter["document_id"] == {"$in": ["doc-tkg"]}
        assert "$regex" in chunk_filter["content"]

    async def test_without_a_heading_the_citing_chunks_are_taken(self) -> None:
        documents, chunks = _collections([STATUTE_DOCUMENT], [STATUTE_CHUNKS[2]])
        hits = await fetch_title_hits(documents, chunks, detect_identifiers("§ 1 TKG 2021"))
        assert [h["chunk_id"] for h in hits] == ["tkg-29"]

    async def test_bare_statute_name_matches_no_document(self) -> None:
        documents, chunks = _collections([STATUTE_DOCUMENT], STATUTE_CHUNKS)
        assert await fetch_title_hits(documents, chunks, detect_identifiers("TKG 2021")) == []
        documents.find.assert_not_called()
        collection = MagicMock()
        collection.aggregate = MagicMock(side_effect=AssertionError("no phrase query for a bare name"))
        hits = await fetch_exact_hits(
            collection, detect_identifiers("TKG 2021"), text_index="text_index", docs_collection="documents",
            documents=documents,
        )
        assert hits == []


# ─────────────────────────────────────────────────────────────────────────────
# Fusion: the F34 scenario with mocked stage results
# ─────────────────────────────────────────────────────────────────────────────

def _f34_stage_results() -> Dict[str, List[Dict[str, Any]]]:
    """Vector/text results shaped like tick 21: the statute leads the vector list,
    twelve chunks found by both stages (COVID guideline "Ziel 1", Syndikatsvertrag
    tables "GJ 2021", an e-mail with a number column) fill the fused top 10."""
    statute_16 = _doc("tkg-16", source=STATUTE_SOURCE, content_hash="HT", chunk_index=16,
                      content=SECTION_TEXT, similarity=0.895, title=STATUTE_TITLE)
    statute_2 = _doc("tkg-2", source=STATUTE_SOURCE, content_hash="HT", chunk_index=2,
                     content=TOC_TEXT, similarity=0.89, title=STATUTE_TITLE)
    noise = [
        _doc(f"noise-{n}", source=f"Akten/Other/doc{n}.pdf", content_hash=f"N{n}", chunk_index=0,
             similarity=0.8 - n / 100, content=f"Ziel 1: Sicherung von Unternehmensstandorten GJ 2021 {n} " + "x" * 300)
        for n in range(1, 13)
    ]
    return {"vector": [statute_16, statute_2, *noise], "text": list(noise)}


def _raw_rrf(stages: Dict[str, List[Dict[str, Any]]], k: int = 60) -> List[Dict[str, Any]]:
    """The fusion of ``hybrid_search`` (equal weights, raw scores), full list."""
    scores: Dict[str, float] = {}
    docs: Dict[str, Dict[str, Any]] = {}
    for name in ("vector", "text"):
        for rank, doc in enumerate(stages[name]):
            cid = str(doc["chunk_id"])
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            docs.setdefault(cid, doc)
    fused = []
    for cid, score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
        doc = dict(docs[cid])
        doc["similarity"] = score
        fused.append(doc)
    return fused


def fused_doc_score(fused: List[Dict[str, Any]], chunk_id: str) -> float:
    """The score the merge is handed for a guaranteed doc (the vector doc's own, here)."""
    return next(d["similarity"] for d in _f34_stage_results()["vector"] if d["chunk_id"] == chunk_id)


class TestSemanticGuaranteeMerge:
    def test_without_a_citation_the_old_ranking_drops_the_statute(self) -> None:
        stages = _f34_stage_results()
        fused = _raw_rrf(stages)
        identifiers = detect_identifiers(F34_QUERY_WITHOUT_CITATION)
        assert semantic_guarantee(identifiers, stages["vector"]) == []
        old = merge_identifier_results([], fused, 10)
        assert [d["chunk_id"] for d in old] == [d["chunk_id"] for d in fused[:10]]
        assert "tkg-16" not in {d["chunk_id"] for d in old}  # F34 as observed

    def test_with_the_citation_the_semantic_top_3_land_in_the_window(self) -> None:
        stages = _f34_stage_results()
        fused = _raw_rrf(stages)
        identifiers = detect_identifiers(F34_QUERY)
        guaranteed = semantic_guarantee(identifiers, stages["vector"])
        assert [d["chunk_id"] for d in guaranteed] == ["tkg-16", "tkg-2", "noise-1"]
        assert len(guaranteed) == SEMANTIC_GUARANTEE_COUNT
        merged = merge_identifier_results([], fused, 10, guaranteed=guaranteed)
        ids = [d["chunk_id"] for d in merged]
        assert ids[:3] == ["tkg-16", "tkg-2", "noise-1"]
        assert len(ids) == 10 and len(set(ids)) == 10
        assert merged[0]["metadata"] == {"title": STATUTE_TITLE, "locations": [], "semantic_guarantee": True, "vector_rank": 1}
        # Guaranteed results keep their own score; no pinned score and no exact flag.
        assert merged[0]["similarity"] == fused_doc_score(fused, "tkg-16") != PINNED_SCORE
        assert "exact_identifier_match" not in merged[0]["metadata"]
        assert "semantic_guarantee" not in merged[3]["metadata"]
        # The rest follows the fused order.
        assert ids[3:] == [d["chunk_id"] for d in fused if d["chunk_id"] not in ids[:3]][:7]

    def test_pinned_block_is_cut_so_the_guaranteed_results_fit(self) -> None:
        stages = _f34_stage_results()
        fused = _raw_rrf(stages)
        exact = [_doc(f"cite-{n}", source=f"Akten/Magenta/Schriftsatz{n}.pdf", content_hash=f"C{n}", chunk_index=0,
                      content="gemäß § 1 TKG 2021 " + "x" * 300, similarity=9.0 - n) for n in range(12)]
        guaranteed = semantic_guarantee(detect_identifiers(F34_QUERY), stages["vector"])
        merged = merge_identifier_results(exact, fused, 10, guaranteed=guaranteed)
        ids = [d["chunk_id"] for d in merged]
        assert ids == [f"cite-{n}" for n in range(7)] + ["tkg-16", "tkg-2", "noise-1"]
        assert all(d["similarity"] == PINNED_SCORE for d in merged[:7])
        assert merged[7]["metadata"]["semantic_guarantee"] is True

    def test_a_pinned_statute_chunk_is_not_promoted_twice(self) -> None:
        stages = _f34_stage_results()
        fused = _raw_rrf(stages)
        exact = [dict(stages["vector"][1], similarity=0.0, search_type="exact"),   # tkg-2, the TOC
                 dict(stages["vector"][0], similarity=0.0, search_type="exact")]   # tkg-16
        guaranteed = semantic_guarantee(detect_identifiers(F34_QUERY), stages["vector"])
        merged = merge_identifier_results(exact, fused, 10, guaranteed=guaranteed)
        ids = [d["chunk_id"] for d in merged]
        # Both statute chunks are in the fused list; the section text (vector #1) leads its TOC entry (#2).
        assert ids[:3] == ["tkg-16", "tkg-2", "noise-1"]
        assert merged[0]["metadata"]["exact_identifier_match"] is True and merged[0]["metadata"]["vector_rank"] == 1
        assert merged[1]["metadata"]["vector_rank"] == 2
        assert "semantic_guarantee" not in merged[0]["metadata"]
        assert merged[2]["metadata"]["semantic_guarantee"] is True and merged[2]["metadata"]["vector_rank"] == 3
        assert len(ids) == 10


# ─────────────────────────────────────────────────────────────────────────────
# hybrid_search endpoint
# ─────────────────────────────────────────────────────────────────────────────

def _serve_by_call_order(stages: Dict[str, List[Dict[str, Any]]], exact: List[Dict[str, Any]]):
    """The vector, text and exact pipelines run in this order; serve each its canned rows."""
    order = iter([stages["vector"], stages["text"], exact])

    def side_effect(pipeline: List[Dict[str, Any]]) -> _AsyncCursor:
        return _AsyncCursor(next(order))

    return side_effect


class TestHybridEndpoint:
    @pytest.fixture
    def embedding(self):
        with patch("backend.routers.search.get_embedding", new=AsyncMock(return_value=[0.1] * 8)):
            yield

    def test_statute_query_takes_the_phrase_stage_and_returns_the_statute(self, client: TestClient, mock_db, embedding) -> None:
        stages = _f34_stage_results()
        collection = mock_db.chunks_collection  # serves documents and chunks alike
        collection.aggregate.reset_mock()
        collection.aggregate.side_effect = _serve_by_call_order(stages, [])
        collection.find.side_effect = lambda flt, *args, **kwargs: _AsyncCursor(
            STATUTE_CHUNKS if "document_id" in flt else [STATUTE_DOCUMENT]
        )
        try:
            response = client.post("/api/v1/search/hybrid", json={"query": F34_QUERY, "match_count": 10})
        finally:
            collection.aggregate.side_effect = None
            collection.find.side_effect = None
        assert response.status_code == 200, response.text

        pipelines = [call.args[0] for call in collection.aggregate.call_args_list]
        assert len(pipelines) == 3
        assert pipelines[0][0]["$vectorSearch"]["limit"] == 50
        text_stage = pipelines[1][0]["$search"]
        assert "text" not in text_stage and text_stage["compound"]["minimumShouldMatch"] == 1
        assert pipelines[1][1] == {"$limit": 50}
        exact_stage = pipelines[2][0]["$search"]
        assert [c["phrase"]["query"] for c in exact_stage["compound"]["should"]] == ["§ 1 TKG 2021", "§ 1 TKG"]

        results = response.json()["results"]
        ids = [r["chunk_id"] for r in results]
        assert ids[0] == "tkg-16", ids
        assert results[0]["document_source"] == STATUTE_SOURCE
        assert results[0]["metadata"]["exact_identifier_match"] is True
        assert results[0]["metadata"]["vector_rank"] == 1
        assert ids[1] == "tkg-2"
        assert results[2]["chunk_id"] == "noise-1" and results[2]["metadata"]["semantic_guarantee"] is True
        assert len(ids) == 10

    def test_statute_query_without_statute_document_still_lands_the_semantic_top(self, client: TestClient, mock_db, embedding) -> None:
        stages = _f34_stage_results()
        collection = mock_db.chunks_collection
        collection.aggregate.reset_mock()
        collection.aggregate.side_effect = _serve_by_call_order(stages, [])
        try:
            response = client.post("/api/v1/search/hybrid", json={"query": F34_QUERY, "match_count": 10})
        finally:
            collection.aggregate.side_effect = None
        assert response.status_code == 200, response.text
        results = response.json()["results"]
        assert [r["chunk_id"] for r in results][:3] == ["tkg-16", "tkg-2", "noise-1"]
        assert results[0]["metadata"]["semantic_guarantee"] is True

    def test_same_query_without_the_citation_keeps_the_old_ranking(self, client: TestClient, mock_db, embedding) -> None:
        stages = _f34_stage_results()
        collection = mock_db.chunks_collection
        collection.aggregate.reset_mock()
        collection.aggregate.side_effect = _serve_by_call_order(stages, [])
        try:
            response = client.post(
                "/api/v1/search/hybrid", json={"query": F34_QUERY_WITHOUT_CITATION, "match_count": 10},
            )
        finally:
            collection.aggregate.side_effect = None
        assert response.status_code == 200, response.text
        pipelines = [call.args[0] for call in collection.aggregate.call_args_list]
        assert len(pipelines) == 2
        assert pipelines[1][0]["$search"] == {
            "index": pipelines[1][0]["$search"]["index"],
            "text": {"query": F34_QUERY_WITHOUT_CITATION, "path": "content", "fuzzy": {"maxEdits": 2, "prefixLength": 3}},
        }
        assert pipelines[0][0]["$vectorSearch"]["limit"] == 20
        collection.find.assert_not_called()
        results = response.json()["results"]
        assert [r["chunk_id"] for r in results] == [d["chunk_id"] for d in _raw_rrf(stages)[:10]]
        assert "tkg-16" not in {r["chunk_id"] for r in results}
        assert all("semantic_guarantee" not in r["metadata"] and "locations" not in r["metadata"] for r in results)


# ─────────────────────────────────────────────────────────────────────────────
# Federated path
# ─────────────────────────────────────────────────────────────────────────────

def _federated(stages: Dict[str, List[Dict[str, Any]]], exact: List[Dict[str, Any]],
               title_documents: List[Dict[str, Any]] | None = None,
               title_chunks: List[Dict[str, Any]] | None = None) -> tuple[FederatedSearch, MagicMock]:
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value={"_id": "x"})
    collection.aggregate = MagicMock(side_effect=_serve_by_call_order(stages, exact))
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
    async def test_search_database_statute_query(self) -> None:
        fs, collection = _federated(_f34_stage_results(), [], [STATUTE_DOCUMENT], STATUTE_CHUNKS)
        results = await fs._search_database("rag_test", "chunks", "documents", F34_QUERY, [0.1] * 8, match_count=10)
        pipelines = [call.args[0] for call in collection.aggregate.call_args_list]
        assert len(pipelines) == 3 and "compound" in pipelines[1][0]["$search"]
        ids = [r["chunk_id"] for r in results]
        assert ids[:3] == ["tkg-16", "tkg-2", "noise-1"]
        assert results[0]["rrf_score"] == PINNED_SCORE and results[0]["metadata"]["vector_rank"] == 1
        assert results[2]["metadata"]["semantic_guarantee"] is True and 0.5 <= results[2]["rrf_score"] <= 1.0
        assert len(ids) == 10

    async def test_search_database_without_citation_is_unchanged(self) -> None:
        stages = _f34_stage_results()
        fs, collection = _federated(stages, [])
        results = await fs._search_database(
            "rag_test", "chunks", "documents", F34_QUERY_WITHOUT_CITATION, [0.1] * 8, match_count=10,
        )
        pipelines = [call.args[0] for call in collection.aggregate.call_args_list]
        assert len(pipelines) == 2 and "text" in pipelines[1][0]["$search"]
        collection.find.assert_not_called()
        assert "tkg-16" not in {r["chunk_id"] for r in results}
        assert all("semantic_guarantee" not in r["metadata"] for r in results)

    def test_apply_rrf_guarantees_the_vector_top_3_only_with_a_statute(self) -> None:
        fs = FederatedSearch()
        stages = _f34_stage_results()
        results = [dict(d, search_type="vector") for d in stages["vector"]] + [dict(d, search_type="text") for d in stages["text"]]
        with_statute = fs._apply_rrf([dict(r) for r in results], limit=10, identifiers=detect_identifiers(F34_QUERY))
        assert [m["chunk_id"] for m in with_statute][:2] == ["tkg-16", "tkg-2"]
        assert with_statute[0]["metadata"]["semantic_guarantee"] is True
        without = fs._apply_rrf([dict(r) for r in results], limit=10, identifiers=detect_identifiers(F34_QUERY_WITHOUT_CITATION))
        assert "tkg-16" not in {m["chunk_id"] for m in without}
        assert "locations" not in without[0].get("metadata", {})
        aktenzeichen = fs._apply_rrf([dict(r) for r in results], limit=10, identifiers=detect_identifiers("13 U 39/16v"))
        assert [m["chunk_id"] for m in aktenzeichen] == [m["chunk_id"] for m in without]

    def test_deduplicate_and_rank_keeps_guaranteed_documents_ahead_of_fused_ones(self) -> None:
        def ref(chunk_id: str, score: float, **metadata: Any) -> DocumentReference:
            return DocumentReference(
                id=chunk_id, document_id=f"d-{chunk_id}", title="Doc", source_type=DataSourceType.PROFILE,
                source_database="db", document_source=f"{chunk_id}.pdf", excerpt="...", similarity_score=score,
                metadata=metadata,
            )

        fs = FederatedSearch()
        ranked = fs._deduplicate_and_rank([
            ref("fused-high", 0.99),
            ref("statute", 0.6, semantic_guarantee=True, vector_rank=1),
            ref("pinned", 1.0, exact_identifier_match=True),
            ref("fused-low", 0.7),
        ], limit=10)
        assert [d.id for d in ranked] == ["pinned", "statute", "fused-high", "fused-low"]
