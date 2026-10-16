"""Tests for the post-OCR § repair (src/ingestion/ocr_repair.py).

The positive cases are the misreads observed in the Tesseract output of the
firm's scans and the synthetic corpus (docs/24); the negative cases are the
things that must stay untouched (amounts, numbers that merely start with 5).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.ingestion.ocr_repair import repair_docling_document, repair_ocr_text


@pytest.mark.parametrize(
    ("ocr", "expected"),
    [
        # observed on the firm's scans (Tesseract deu+eng)
        ("gemäß $ 94 ÄrzteG in oa Angelegenheit", "gemäß § 94 ÄrzteG in oa Angelegenheit"),
        ("gemäß $ 94 ArzteG", "gemäß § 94 ArzteG"),
        ("nach $ 7 Abs.3 des österreichischen Patentgesetzes", "nach § 7 Abs.3 des österreichischen Patentgesetzes"),
        ("5 10 AVG und § 83 BAO, zu vertreten", "§ 10 AVG und § 83 BAO, zu vertreten"),
        ("[$ 5 Abs 2 KSchG] zu sehen ist", "[§ 5 Abs 2 KSchG] zu sehen ist"),
        ("gemäß $ 26 Abs.1 Gerichtsgebührengesetz .", "gemäß § 26 Abs.1 Gerichtsgebührengesetz ."),
        ("Vollmacht gemäß $$ RAO erteilt", "Vollmacht gemäß §§ RAO erteilt"),
        ("gemäß $$ 8 ff RAO", "gemäß §§ 8 ff RAO"),
        ("77 GBG und $ 455 StPO", "77 GBG und § 455 StPO"),
        # observed on the synthetic scans
        ("insbesondere nach $ 1096 Abs. 1 ABGB, bleiben unberührt", "insbesondere nach § 1096 Abs. 1 ABGB, bleiben unberührt"),
        ("$1096 ABGB", "§ 1096 ABGB"),
        ("5 65 Zi 1 lit c StPO", "§ 65 Zi 1 lit c StPO"),
        # several in one text
        ("$ 94 ÄrzteG und $ 10 AVG", "§ 94 ÄrzteG und § 10 AVG"),
    ],
)
def test_repairs_section_sign(ocr: str, expected: str) -> None:
    assert repair_ocr_text(ocr) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Pauschalpreis brutto: $ 5.000,00",  # an amount, no statute follows
        "US$ 10 Mio Umsatz",
        "52 Abs. 1 ZPO",  # a real number that happens to start with 5: no space after the 5
        "Art 82 DSGVO zur Geschäftszahl 44 Cg 200/25b",
        "§ 10 AVG bleibt",  # already right
        "Stufe 5 10 Prozent",  # "5 10" without a statute
        "Top 5 ist frei",
        "",
    ],
)
def test_leaves_unrelated_text_alone(text: str) -> None:
    assert repair_ocr_text(text) == text


def test_repairs_docling_document_items_and_table_cells() -> None:
    cell = SimpleNamespace(text="$ 94 ÄrzteG")
    table = SimpleNamespace(data=SimpleNamespace(table_cells=[cell, SimpleNamespace(text="350,00")]))
    items = [SimpleNamespace(text="Vollmacht gemäß $$ RAO"), SimpleNamespace(text="nichts"), SimpleNamespace(text=None)]
    doc = SimpleNamespace(texts=items, tables=[table])
    assert repair_docling_document(doc) == 2
    assert items[0].text == "Vollmacht gemäß §§ RAO"
    assert items[1].text == "nichts"
    assert cell.text == "§ 94 ÄrzteG"
    assert repair_docling_document(None) == 0
