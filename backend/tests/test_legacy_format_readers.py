"""Tests for the legacy and container format readers added for law-firm corpora (docs/26).

Covers the extension set and priority map, the RTF, legacy Excel, MHTML, renamed
Office and multi-page TIFF readers, the DOS-era text decoder, the byte sniffer behind
``.wvb``/``.mtt``, the AppleDouble skip in file discovery and the TIFF DPI coercion.
Docling converters are stubbed where a model or Tesseract would be needed.
"""

from __future__ import annotations

import gzip
import os
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")


class _Result:
    def __init__(self, text: str) -> None:
        self.document = SimpleNamespace(text=text)


@pytest.fixture
def pipeline() -> ingest.DocumentIngestionPipeline:
    """A pipeline object without initialising anything (only the reader methods are used)."""
    return ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)


@pytest.fixture
def stub_converter(monkeypatch):
    """Stub the Docling converters: ``convert`` returns the converted file's path as text."""
    calls: list = []

    class _Converter:
        def convert(self, path):
            calls.append(path)
            return _Result(f"converted:{Path(path).suffix}:{Path(path).read_bytes()!r}")

    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Converter())
    monkeypatch.setattr(ingest, "create_image_converter", lambda *a, **k: _Converter())
    monkeypatch.setattr(ingest, "export_document_markdown", lambda document: document.text)
    return calls


# ---------------------------------------------------------------------------
# Extension set
# ---------------------------------------------------------------------------


def test_supported_extensions_cover_the_law_firm_formats() -> None:
    for ext in (".rtf", ".dot", ".dotx", ".xls", ".mht", ".mhtml", ".tif", ".tiff", ".wvb", ".mtt",
                ".as", ".ass", ".ds", ".eb", ".do", ".dos", ".3", ".dss"):
        assert ext in ingest.SUPPORTED_INGESTION_EXTENSIONS, ext
    for ext in (".dra", ".dat", ".db", ".lnk", ".zip", ".cab", ".herunterladen"):
        assert ext not in ingest.SUPPORTED_INGESTION_EXTENSIONS, ext


def test_every_supported_extension_has_a_processing_priority(pipeline, tmp_path: Path) -> None:
    files = []
    for ext in sorted(ingest.SUPPORTED_INGESTION_EXTENSIONS):
        path = tmp_path / f"file{ext}"
        path.write_bytes(b"x")
        files.append(str(path))
    ordered = pipeline._sort_files_for_processing(files)
    assert len(ordered) == len(ingest.SUPPORTED_INGESTION_EXTENSIONS)
    # text-like formats come first, images and media last
    assert ordered[0].endswith((".txt", ".md", ".markdown", ".as", ".wvb", ".mtt", ".3", ".ds", ".do", ".dos", ".eb", ".ass", ".dss"))
    assert ordered[-1].endswith((".mp4", ".avi", ".mkv", ".mov", ".webm"))


def test_api_patterns_are_derived_from_the_pipeline_set() -> None:
    router = pytest.importorskip("backend.routers.ingestion")
    patterns = router._supported_file_patterns()
    assert "*.rtf" in patterns and "*.tif" in patterns
    assert {p[1:] for p in patterns} == set(ingest.SUPPORTED_INGESTION_EXTENSIONS)


def test_discovery_skips_appledouble_sidecars(tmp_path: Path) -> None:
    (tmp_path / "Brief.rtf").write_bytes(b"{\\rtf1 x}")
    (tmp_path / "._Brief.rtf").write_bytes(b"\x00\x05\x16\x07")
    (tmp_path / "notes.txt").write_text("x")
    p = ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)
    p.documents_folders = [str(tmp_path)]
    found = {os.path.basename(f) for f in p._find_document_files()}
    assert found == {"Brief.rtf", "notes.txt"}
    assert ingest.is_sidecar_file("._x.doc") and not ingest.is_sidecar_file("_x.doc")
    router = pytest.importorskip("backend.routers.ingestion")
    assert {os.path.basename(f) for f in router._find_files_sync([str(tmp_path)], ["*.rtf", "*.txt"])} == found


# ---------------------------------------------------------------------------
# RTF
# ---------------------------------------------------------------------------


def test_rtf_reader_decodes_the_declared_code_page(pipeline, tmp_path: Path) -> None:
    pytest.importorskip("striprtf")
    rtf = tmp_path / "Muster.rtf"
    rtf.write_bytes(b"{\\rtf1\\ansi\\ansicpg1252\\deff0 {\\fonttbl{\\f0 Arial;}}\\f0 Sehr geehrte Frau Dr. M\\'fcller,\\par gem\\'e4\\'df \\'a7 1 UGB.\\par}")
    text, document = pipeline._read_rtf_file(str(rtf))
    assert document is None
    assert "Sehr geehrte Frau Dr. Müller," in text
    assert "gemäß § 1 UGB." in text
    assert ingest.rtf_code_page(b"{\\rtf1\\ansi\\ansicpg1250 x}") == "cp1250"
    assert ingest.rtf_code_page(b"{\\rtf1\\ansi x}") == "cp1252"


def test_rtf_reader_reports_empty_and_unreadable_files(pipeline, tmp_path: Path) -> None:
    pytest.importorskip("striprtf")
    empty = tmp_path / "leer.rtf"
    empty.write_bytes(b"{\\rtf1\\ansi }")
    assert pipeline._read_rtf_file(str(empty))[0].startswith("[Document: leer.rtf")
    assert pipeline._read_rtf_file(str(tmp_path / "missing.rtf"))[0].startswith("[Error: Could not read file missing.rtf")


# ---------------------------------------------------------------------------
# Legacy Excel
# ---------------------------------------------------------------------------


def test_xls_reader_renders_sheets_as_markdown_tables(pipeline, tmp_path: Path, monkeypatch) -> None:
    pd = pytest.importorskip("pandas")
    xls = tmp_path / "Honorar.xls"
    xls.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    frames = {
        "Kosten": pd.DataFrame([["Position", "Betrag"], ["Beratung", 120.5], [None, None]]),
        "Leer": pd.DataFrame([[None, None]]),
    }
    seen = {}

    def fake_read_excel(path, sheet_name=None, header=None, engine=None):
        seen.update(path=path, engine=engine)
        return frames

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)
    text, document = pipeline._read_xls_file(str(xls))
    assert document is None
    assert seen["engine"] == "xlrd"
    assert "## Kosten" in text and "## Leer" not in text
    assert "| Position | Betrag |" in text
    assert "| Beratung | 120.5 |" in text


def test_xls_named_html_export_goes_to_the_html_reader(pipeline, tmp_path: Path, stub_converter) -> None:
    export = tmp_path / "Export.xls"
    export.write_bytes(b"<html><body><table><tr><td>Saldo</td></tr></table></body></html>")
    text, _ = pipeline._read_xls_file(str(export))
    assert text.startswith("converted:.html:")
    assert stub_converter and stub_converter[0].endswith(".html")
    assert not os.path.exists(stub_converter[0])  # temporary copy removed


def test_markdown_table_escapes_and_pads() -> None:
    table = ingest.markdown_table([["a|b", "c\nd"], ["e"]])
    assert table.splitlines() == ["| a\\|b | c d |", "|---|---|", "| e |  |"]
    assert ingest.markdown_table([]) == ""


# ---------------------------------------------------------------------------
# MHTML, renamed Office files
# ---------------------------------------------------------------------------


def _mhtml(tmp_path: Path, html: str) -> Path:
    message = EmailMessage()
    message["From"] = "<ADVOKAT Online>"
    message["Subject"] = "ADVOKAT Online Abfrage"
    message.make_mixed()
    part = EmailMessage()
    part.set_content(html, subtype="html", charset="utf-8", cte="quoted-printable")
    message.attach(part)
    path = tmp_path / "Anfrage an Meldeamt.mht"
    path.write_bytes(bytes(message))
    return path


def test_mhtml_reader_hands_the_html_part_to_docling(pipeline, tmp_path: Path, stub_converter) -> None:
    path = _mhtml(tmp_path, "<html><body><p>Meldeauskunft für Max Muster</p></body></html>")
    text, document = pipeline._read_mhtml_file(str(path))
    assert text.startswith("converted:.html:")
    assert document is not None
    assert stub_converter[0].endswith(".html") and not os.path.exists(stub_converter[0])


def test_mhtml_without_html_part_falls_back_to_text(pipeline, tmp_path: Path) -> None:
    message = EmailMessage()
    message.set_content("Nur Text", charset="utf-8")
    path = tmp_path / "plain.mht"
    path.write_bytes(bytes(message))
    assert pipeline._read_mhtml_file(str(path))[0].strip() == "Nur Text"


TEMPLATE_CT = (
    b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    b'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
    b'wordprocessingml.template.main+xml"/></Types>'
)


def test_dotx_is_converted_as_a_real_docx(pipeline, tmp_path: Path, monkeypatch) -> None:
    import zipfile

    template = tmp_path / "AV.dotx"
    with zipfile.ZipFile(template, "w") as zout:
        zout.writestr("[Content_Types].xml", TEMPLATE_CT)
        zout.writestr("word/document.xml", "<w:document/>")
    seen = {}

    class _Converter:
        def convert(self, path):
            with zipfile.ZipFile(path) as zin:
                seen["path"] = path
                seen["types"] = zin.read("[Content_Types].xml").decode()
                seen["names"] = zin.namelist()
            return _Result("Aktenvermerk")

    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Converter())
    monkeypatch.setattr(ingest, "export_document_markdown", lambda document: document.text)
    text, document = pipeline._read_document(str(template))
    assert text == "Aktenvermerk" and document is not None
    assert seen["path"].endswith(".docx") and not os.path.exists(seen["path"])
    assert "wordprocessingml.document.main+xml" in seen["types"]
    assert "template.main+xml" not in seen["types"]
    assert seen["names"] == ["[Content_Types].xml", "word/document.xml"]


def test_text_payload_prefers_valid_utf8_over_the_declared_charset() -> None:
    assert ingest.decode_text_payload("Musterverträge".encode("utf-8"), "iso-8859-1") == "Musterverträge"
    assert ingest.decode_text_payload("Verträge".encode("cp1252"), "iso-8859-1") == "Verträge"
    assert ingest.decode_text_payload("Verträge".encode("cp1252"), None) == "Verträge"
    assert ingest.decode_text_payload("Verträge".encode("cp1252"), "no-such-charset") == "Verträge"


def test_mhtml_with_latin1_header_but_utf8_bytes_is_not_mojibake(pipeline, tmp_path: Path, stub_converter) -> None:
    raw = (
        b"From: <saved page>\r\nMIME-Version: 1.0\r\nContent-Type: multipart/related; boundary=\"B\"\r\n\r\n"
        b"--B\r\nContent-Type: text/html; charset=\"iso-8859-1\"\r\nContent-Transfer-Encoding: 8bit\r\n\r\n"
        + "<p>Musterverträge</p>".encode("utf-8")
        + b"\r\n--B--\r\n"
    )
    path = tmp_path / "Weka.mht"
    path.write_bytes(raw)
    text, _ = pipeline._read_mhtml_file(str(path))
    assert "\\xc3\\xa4" in text and "\\xc3\\x83" not in text  # ä written as UTF-8, not re-encoded mojibake


def test_html_handed_to_docling_declares_utf8(pipeline, tmp_path: Path, stub_converter) -> None:
    page = (
        '<html><head><meta http-equiv="Content-Type" content="text/html; charset=windows-1252">'
        "<title>Verträge</title></head><body>Muster</body></html>"
    )
    assert ingest.html_as_utf8(page).startswith('<meta charset="utf-8"><html><head><title>')
    assert "windows-1252" not in ingest.html_as_utf8(page)
    packed = tmp_path / "W.mtt"
    packed.write_bytes(gzip.compress(page.encode("cp1252")))
    text, _ = pipeline._read_document(str(packed))
    assert 'charset="utf-8"' in text
    assert "windows-1252" not in text
    assert "\\xc3\\xa4" in text  # the cp1252 ä, decoded via the page's meta tag, re-encoded as UTF-8
    assert ingest.decode_html_bytes("<p>ä</p>".encode("utf-8")) == "<p>ä</p>"
    assert ingest.decode_html_bytes(b'<meta charset="iso-8859-15"><p>\xe4</p>') == '<meta charset="iso-8859-15"><p>ä</p>'
    assert ingest.decode_html_bytes(b"<p>\xe4</p>") == "<p>ä</p>"


def test_dot_goes_to_antiword_like_doc(pipeline, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "_read_doc_file", lambda path: ("antiword:" + os.path.basename(path), None))
    assert pipeline._read_document(str(tmp_path / "Vorlage.dot")) == ("antiword:Vorlage.dot", None)
    assert pipeline._read_document(str(tmp_path / "Backup.wbk")) == ("antiword:Backup.wbk", None)


# ---------------------------------------------------------------------------
# DOS-era text system, sniffed text, gzip templates
# ---------------------------------------------------------------------------


LEGACY_SAMPLE = (
    b"\xdf\x1b&f1y3X\xdf\xc6\x00\x07\xc6\xcf\x02\xcfHerrn\nDr. Robert LANGER\xa9HANSEL\n"
    b"f\xe1\x81\xe1r J\xe1\x84\xe1nner 1995\n\xc4\nG:\xc4\x9d\x9444 C 366/93 p\x9c\x95\x84\n\n\n\nBezirksgericht\x1bE\n"
)


def test_legacy_dos_text_decodes_markers_and_drops_printer_codes() -> None:
    text = ingest.legacy_dos_text(LEGACY_SAMPLE)
    assert text.splitlines() == [
        "Herrn",
        "Dr. Robert LANGER-HANSEL",
        "für Jänner 1995",
        "",
        "G:44 C 366/93 p",
        "",
        "Bezirksgericht",
    ]
    assert ingest.legacy_dos_text(b"\x1b&f1y3X\xdf\xc6") == ""


def test_legacy_text_reader_handles_text_rtf_and_dss_audio(pipeline, tmp_path: Path) -> None:
    pytest.importorskip("striprtf")
    letter = tmp_path / "21-6HANS.DS"
    letter.write_bytes(LEGACY_SAMPLE)
    assert "LANGER-HANSEL" in pipeline._read_document(str(letter))[0]
    rtf = tmp_path / "MV.EB"
    rtf.write_bytes(b"{\\rtf1\\ansi\\ansicpg1252 Mietvertrag \\'fcber Wohnung\\par}")
    assert "Mietvertrag über Wohnung" in pipeline._read_document(str(rtf))[0]
    dictation = tmp_path / "PITT1701.dss"
    dictation.write_bytes(b"\x02dss\x01\x00" + b"\x00" * 32)
    assert pipeline._read_document(str(dictation))[0].startswith("[Audio: PITT1701.dss - Olympus DSS")


def test_wvb_is_cp1252_text_or_rtf(pipeline, tmp_path: Path) -> None:
    pytest.importorskip("striprtf")
    plain = tmp_path / "Unternehmer.WVB"
    plain.write_bytes(b"Unternehmer i. S. des \xa7 1 UGB")
    assert pipeline._read_document(str(plain))[0] == "Unternehmer i. S. des § 1 UGB"
    rtf = tmp_path / "ss.WVB"
    rtf.write_bytes(b"{\\rtf1\\ansi\\deff0\\uc1\\ansicpg1252 Zinsen \\'e4ndern\\par}")
    assert pipeline._read_document(str(rtf))[0] == "Zinsen ändern"
    utf8 = tmp_path / "neu.wvb"
    utf8.write_bytes("﻿Gemäß § 2".encode("utf-8"))
    assert pipeline._read_document(str(utf8))[0] == "Gemäß § 2"


def test_mtt_is_gunzipped_and_sniffed(pipeline, tmp_path: Path, stub_converter) -> None:
    packed = tmp_path / "S_SCHLAG.MTT"
    packed.write_bytes(gzip.compress(b"<HTML><HEAD><TITLE>Schlagw&ouml;rter</TITLE></HEAD><BODY>x</BODY></HTML>"))
    text, _ = pipeline._read_document(str(packed))
    assert text.startswith("converted:.html:")
    broken = tmp_path / "broken.mtt"
    broken.write_bytes(b"not gzip")
    assert pipeline._read_document(str(broken))[0].startswith("[Error: Could not read file broken.mtt")


def test_sniff_text_bytes_classes() -> None:
    assert ingest.sniff_text_bytes(b"  {\\rtf1 x}")[0] == "rtf"
    assert ingest.sniff_text_bytes(b"<html>")[0] == "html"
    assert ingest.sniff_text_bytes(b"plain \xa7")[0] == "text"
    assert ingest.sniff_text_bytes(LEGACY_SAMPLE)[0] == "legacy"


# ---------------------------------------------------------------------------
# TIFF
# ---------------------------------------------------------------------------


def test_multipage_tiff_ocrs_every_page(pipeline, tmp_path: Path, monkeypatch) -> None:
    Image = pytest.importorskip("PIL.Image")
    pages = [Image.new("L", (40, 30), color=c) for c in (255, 200, 100)]
    tiff = tmp_path / "Vollmacht.tif"
    pages[0].save(tiff, save_all=True, append_images=pages[1:], dpi=(300, 300))
    assert ingest.image_frame_count(str(tiff)) == 3
    texts = iter(["Seite eins", "", "Seite drei"])

    class _Converter:
        def convert(self, path):
            assert path.endswith(".png")
            return _Result(next(texts))

    monkeypatch.setattr(ingest, "create_image_converter", lambda *a, **k: _Converter())
    monkeypatch.setattr(ingest, "export_document_markdown", lambda document: document.text)
    text, document = pipeline._read_document(str(tiff))
    assert document is None
    assert text == "Seite eins\n\n---\n\nSeite drei"


def test_tiff_dpi_tag_no_longer_forces_a_resave(tmp_path: Path) -> None:
    Image = pytest.importorskip("PIL.Image")
    tiff = tmp_path / "scan.tif"
    Image.new("L", (20, 20), color=255).save(tiff, dpi=(300, 300))
    path, temp = ingest.prepare_image_for_docling(str(tiff))
    assert path == str(tiff) and temp is None
    assert ingest._dpi_number("x") is None and ingest._dpi_number(72) == 72.0
