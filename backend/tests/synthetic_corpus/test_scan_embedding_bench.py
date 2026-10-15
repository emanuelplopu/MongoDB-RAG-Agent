"""Unit tests for the scan OCR-text vs. image-embedding benchmark (no stack needed)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from backend.evaluation.synthetic_corpus import scan_embedding_bench as seb
from backend.evaluation.synthetic_corpus.models import CaseManifest, GoldCase, GoldSpan, RenderedFile


def _gold(**kw) -> GoldCase:
    base = dict(id="c-q01", dataset_id="t", case_id="c", area_id="a", type="single_fact", prompt="Wie hoch?",
                expected_tools=["search_profile_documents"], forbidden_tools=["web_search"],
                gold_documents=["A/scan - Seite 1.jpg"], nuggets=["x"], must_include=["EUR 3.300,00"],
                reference_answer="EUR 3.300,00",
                gold_spans=[GoldSpan(source="A/scan - Seite 1.jpg", quote="EUR 3.300,00")])
    base.update(kw)
    return GoldCase(**base)


def test_unit_rows_are_normalised() -> None:
    m = seb._unit_rows([[3.0, 4.0], [0.0, 0.0]])
    assert np.allclose(np.linalg.norm(m, axis=1), [1.0, 0.0])
    assert np.allclose(m[0], [0.6, 0.8])


def test_build_units_flags_scans_and_images(tmp_path: Path) -> None:
    (tmp_path / "manifest").mkdir()
    corpus = tmp_path / "corpus"
    (corpus / "A").mkdir(parents=True)
    (corpus / "A" / "scan - Seite 1.jpg").write_bytes(b"\xff\xd8\xff\xd9")  # tiny jpeg-ish
    manifest = CaseManifest(case_id="c", area_id="a", folder="A", files=[
        RenderedFile(path="A/letter.pdf", format="pdf", kind="primary", of_doc_id="D1", fact_ids=["F1"]),
        RenderedFile(path="A/scan - Seite 1.jpg", format="scan_jpg", kind="primary", of_doc_id="D2", fact_ids=["F2"]),
        RenderedFile(path="A/scan - Seite 9.jpg", format="scan_jpg", kind="primary", of_doc_id="D3", fact_ids=["F3"]),
        RenderedFile(path="A/copy.pdf", format="pdf", kind="copy", of_doc_id="D1"),
    ])
    (tmp_path / "manifest" / "a.json").write_text(manifest.model_dump_json(), encoding="utf-8")

    store = {"A/letter.pdf": ["letter text"], "A/scan - Seite 1.jpg": ["ocr one", "ocr two"]}
    units = seb.build_units(tmp_path, lambda s: store.get(s, []))

    assert len(units) == 3  # copies excluded
    by_src = {u.source: u for u in units}
    assert by_src["A/letter.pdf"].is_scan is False and by_src["A/letter.pdf"].ingested is True
    s1 = by_src["A/scan - Seite 1.jpg"]
    assert s1.is_scan and s1.image_path is not None and s1.text_chunks == ["ocr one", "ocr two"]
    s9 = by_src["A/scan - Seite 9.jpg"]
    assert s9.is_scan and s9.image_path is None and s9.ingested is False  # no image on disk, no OCR chunk


def _corpus_vectors() -> seb.CorpusVectors:
    # two text chunks for a scan page, one text chunk for a pdf; one image vector for the scan.
    text_sources = ["A/scan - Seite 1.jpg", "A/scan - Seite 1.jpg", "A/letter.pdf"]
    text_contents = ["ocr with EUR 3.300,00", "more ocr", "unrelated letter"]
    text_matrix = seb._unit_rows([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    image_sources = ["A/scan - Seite 1.jpg"]
    image_matrix = seb._unit_rows([[1.0, 1.0, 0.0]])
    return seb.CorpusVectors(text_sources, text_contents, text_matrix, image_sources, image_matrix, {}, {}, {}, {})


def test_arm_index_selects_right_vectors() -> None:
    cv = _corpus_vectors()
    units = [seb.DocUnit("A/scan - Seite 1.jpg", True, image_path=Path("x")), seb.DocUnit("A/letter.pdf", False)]

    # ocr_text mixed: both scan text chunks + the pdf chunk, no image
    m, s, c = seb.arm_index(cv, units, "ocr_text", scan_only=False)
    assert m.shape[0] == 3 and s.count("A/scan - Seite 1.jpg") == 2 and "A/letter.pdf" in s

    # image mixed: scan represented only by its image + the pdf text chunk
    m, s, c = seb.arm_index(cv, units, "image", scan_only=False)
    assert m.shape[0] == 2 and s.count("A/scan - Seite 1.jpg") == 1 and "A/letter.pdf" in s
    assert c[s.index("A/scan - Seite 1.jpg")] == ""  # image carries no text

    # hybrid mixed: both scan text chunks + scan image + pdf chunk
    m, s, c = seb.arm_index(cv, units, "hybrid", scan_only=False)
    assert m.shape[0] == 4 and s.count("A/scan - Seite 1.jpg") == 3

    # scan_only drops the pdf
    m, s, c = seb.arm_index(cv, units, "hybrid", scan_only=True)
    assert set(s) == {"A/scan - Seite 1.jpg"} and m.shape[0] == 3


def test_rank_sources_orders_by_cosine() -> None:
    cv = _corpus_vectors()
    units = [seb.DocUnit("A/scan - Seite 1.jpg", True, image_path=Path("x")), seb.DocUnit("A/letter.pdf", False)]
    m, s, c = seb.arm_index(cv, units, "ocr_text", scan_only=False)
    q = seb._unit_rows([[1.0, 0.0, 0.0]])[0]  # matches the first scan chunk exactly
    srcs, conts = seb.rank_sources(q, m, s, c, k=3)
    assert srcs[0] == "A/scan - Seite 1.jpg" and "EUR 3.300,00" in conts[0]


def test_evaluate_and_aggregate_reward_correct_source() -> None:
    cv = _corpus_vectors()
    units = [seb.DocUnit("A/scan - Seite 1.jpg", True, image_path=Path("x")), seb.DocUnit("A/letter.pdf", False)]
    gold = [_gold()]
    q = seb._unit_rows([[1.0, 0.0, 0.0]])
    m, s, c = seb.arm_index(cv, units, "ocr_text", scan_only=False)
    rows = seb.evaluate_arm(gold, q, ["c-q01"], m, s, c, k=10)
    assert rows[0]["recall@5"] == 1.0 and rows[0]["hit@5"] == 1
    agg = seb.aggregate(rows, {"c-q01"})
    assert agg["n"] == 1 and agg["recall@5"] == 1.0
    assert seb.aggregate(rows, set())["n"] == 0


def test_scan_gold_ids_detects_scan_sources() -> None:
    gold = [
        _gold(id="c-q01"),  # gold doc is a scan
        _gold(id="c-q02", gold_documents=["A/letter.pdf"], gold_spans=[GoldSpan(source="A/letter.pdf", quote="x")]),
        _gold(id="c-q03", unanswerable=True, gold_documents=[], gold_spans=[], must_include=[]),
    ]
    ids = seb.scan_gold_ids(gold, {"A/scan - Seite 1.jpg"})
    assert ids == {"c-q01"}
