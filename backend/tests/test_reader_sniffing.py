"""Readers go by a file's content, and a failed read names its cause.

From the validation of a law firm's index (``docs/27-POST_INGESTION_VALIDATION.md``):

* ``.doc`` files were ``.docx`` containers, RTF, plain and DOS-era text; all of them ended as
  "legacy .doc format requires antiword" although ``antiword`` was installed;
* Outlook messages saved under the name ``.eml`` were parsed as RFC 5322 text, and the
  binary container was indexed as the e-mail's body;
* 508 Word files failed with "Could not read file <name>" and nothing else.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")

OLE2 = bytes.fromhex("d0cf11e0a1b11ae1")


@pytest.fixture
def pipeline() -> ingest.DocumentIngestionPipeline:
    return ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)


# ----------------------------------------------------------------------------- .doc by content

def test_doc_that_is_a_docx_container_goes_to_the_docx_reader(pipeline, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "Vertrag.doc"
    path.write_bytes(bytes.fromhex("504b0304") + b"rest of the zip")
    monkeypatch.setattr(pipeline, "_read_renamed_office_file", lambda p, suffix: (f"docx:{Path(p).name}:{suffix}", "document"))
    assert pipeline._read_doc_file(str(path)) == ("docx:Vertrag.doc:.docx", "document")


def test_doc_that_is_rtf_is_read_as_rtf(pipeline, tmp_path: Path) -> None:
    pytest.importorskip("striprtf")
    path = tmp_path / "Brief.doc"
    path.write_bytes(rb"{\rtf1\ansi\ansicpg1252 Sehr geehrte Frau Dr. Muster, anbei die K\'fcndigung.}")
    content, document = pipeline._read_doc_file(str(path))
    assert document is None and "Sehr geehrte Frau Dr. Muster, anbei die Kündigung." in content


def test_doc_that_is_plain_text_is_read_as_text(pipeline, tmp_path: Path) -> None:
    path = tmp_path / "Aktenvermerk.doc"
    path.write_bytes("An: Dr. Schwarz\r\nBetreff: Räumungsklage, Tagsatzung am 12. Mai\r\n".encode("cp1252"))
    content, _ = pipeline._read_doc_file(str(path))
    assert "Räumungsklage, Tagsatzung am 12. Mai" in content


def test_doc_with_unknown_binary_content_is_an_error_not_a_document(pipeline, tmp_path: Path) -> None:
    path = tmp_path / "ALT.DOC"
    path.write_bytes(bytes(range(1, 9)) * 40 + b"\xffWPC" + bytes([0x9d, 0x8f, 0xfa, 0x12, 0x03]) * 200)
    content, document = pipeline._read_doc_file(str(path))
    assert document is None
    assert ingest.placeholder_kind(content) == "error" and "ALT.DOC" in content


def test_antiword_failure_carries_its_reason(pipeline, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "ALT_169.DOC"
    path.write_bytes(OLE2 + b"\x00" * 600)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/antiword")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="ALT_169.DOC is not a Word Document.\n"),
    )
    content, _ = pipeline._read_doc_file(str(path))
    # antiword's reason first, then why the built-in Word binary reader could not help either
    assert content.startswith("[Error: Could not read file ALT_169.DOC (antiword: ALT_169.DOC is not a Word Document.; ")
    assert "OLE2" in content and ingest.placeholder_kind(content) == "error"


def test_antiword_success_and_empty_output(pipeline, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "Klage.doc"
    path.write_bytes(OLE2 + b"\x00" * 600)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/antiword")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="Klage wegen EUR 5.000,--\n", stderr=""))
    assert pipeline._read_doc_file(str(path)) == ("Klage wegen EUR 5.000,--", None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="  \n", stderr=""))
    content, _ = pipeline._read_doc_file(str(path))
    assert ingest.placeholder_kind(content) == "empty"


def test_word_binary_without_antiword_is_reported_as_such(pipeline, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "Klage.doc"
    path.write_bytes(OLE2 + b"\x00" * 600)
    monkeypatch.setattr("shutil.which", lambda name: None)
    content, _ = pipeline._read_doc_file(str(path))
    assert content == "[Error: Could not read file Klage.doc (antiword is not installed)]"


# ----------------------------------------------------------------------------- .eml by content

def test_eml_that_is_an_outlook_container_goes_to_the_msg_reader(pipeline, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "AW- Fragen zur Liegenschaft.eml"
    path.write_bytes(OLE2 + b"\x00" * 2000)
    monkeypatch.setattr(pipeline, "_read_msg_file", lambda p: (f"msg:{Path(p).name}", None))
    assert pipeline._read_eml_file(str(path)) == ("msg:AW- Fragen zur Liegenschaft.eml", None)


def test_eml_without_headers_is_unreadable(pipeline, tmp_path: Path) -> None:
    path = tmp_path / "kaputt.eml"
    path.write_bytes(b"\x00\x01\x02 no headers here, only bytes \xff\xfe")
    content, _ = pipeline._read_eml_file(str(path))
    assert ingest.placeholder_kind(content) == "error" and "no message headers" in content


def test_real_eml_is_still_parsed(pipeline, tmp_path: Path) -> None:
    path = tmp_path / "Termin.eml"
    path.write_bytes(b"From: kanzlei@example.at\r\nTo: mandant@example.at\r\nSubject: Termin am Montag\r\n\r\nSehr geehrter Herr Huber,\r\nder Termin findet statt.\r\n")
    content, _ = pipeline._read_eml_file(str(path))
    assert content.startswith("# Termin am Montag") and "der Termin findet statt." in content


# ----------------------------------------------------------------------------- the cause of a failed conversion

class _Failing:
    def convert(self, path):
        raise KeyError("no relationship of type 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument' in collection")


def test_failed_docx_conversion_reports_the_cause_and_stores_nothing(pipeline, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Failing())
    path = tmp_path / "Gerichtskostennote.docx"
    path.write_bytes(b"PK plain ascii bytes that a text read would accept")
    content, document = pipeline._read_document(str(path))
    assert document is None and ingest.placeholder_kind(content) == "error"
    assert content.startswith("[Error: Could not read file Gerichtskostennote.docx (KeyError: ")
    assert "no relationship of type" in content and content.count("[") == 1, "the cause is inside the one bracket pair"


def test_raw_text_fallback_is_kept_for_text_formats_only(pipeline, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Failing())
    note = tmp_path / "notiz.md"
    note.write_text("# Notiz\nFrist bis 30. Juni", encoding="utf-8")
    assert pipeline._read_document(str(note)) == ("# Notiz\nFrist bis 30. Juni", None)
    deck = tmp_path / "Folien.pptx"
    deck.write_bytes(b"ascii only source of a file Docling rejected")
    content, _ = pipeline._read_document(str(deck))
    assert ingest.placeholder_kind(content) == "error", "never the file's source as document text"


def test_read_error_cause_is_one_line_without_brackets() -> None:
    cause = ingest.read_error_cause(ValueError("bad [stream]\n at offset 12"))
    assert cause == "ValueError: bad (stream) at offset 12"
    assert ingest.read_error_cause(RuntimeError("x" * 500)).__len__() == 240
    assert ingest.read_error_cause(KeyError()) == "KeyError"


def test_password_protected_pdf_is_named_as_the_cause(pipeline, tmp_path: Path, monkeypatch) -> None:
    """Docling says "could not load document"; 22 of 30 such PDFs of one corpus were password-protected."""
    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Failing())
    monkeypatch.setattr(ingest, "pdf_open_problem", lambda path: "password-protected PDF")
    path = tmp_path / "Befund.pdf"
    path.write_bytes(b"%PDF-1.7 encrypted")
    content, _ = pipeline._read_document(str(path))
    assert content == "[Error: Could not read file Befund.pdf (password-protected PDF)]"
    monkeypatch.setattr(ingest, "pdf_open_problem", lambda path: None)
    content, _ = pipeline._read_document(str(path))
    assert content.startswith("[Error: Could not read file Befund.pdf (KeyError: ")


def test_pdf_open_problem_reads_the_reason_from_pdfium(tmp_path: Path, monkeypatch) -> None:
    pdfium = pytest.importorskip("pypdfium2")

    def locked(path):
        raise RuntimeError("Failed to load document (PDFium: Incorrect password error).")

    monkeypatch.setattr(pdfium, "PdfDocument", locked)
    assert ingest.pdf_open_problem(str(tmp_path / "x.pdf")) == "password-protected PDF"

    def broken(path):
        raise RuntimeError("Failed to load document (PDFium: Data format error).")

    monkeypatch.setattr(pdfium, "PdfDocument", broken)
    assert ingest.pdf_open_problem(str(tmp_path / "x.pdf")) is None
