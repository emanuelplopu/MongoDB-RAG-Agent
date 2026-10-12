"""Tests for .eml email ingestion support in src/ingestion/ingest.py."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")


def _write_eml(path: Path, *, html: bool = False, attachment: bool = False) -> None:
    msg = EmailMessage()
    msg["From"] = "Verena Springer <verena.springer@gmx.at>"
    msg["To"] = "Mag. John Radl <j.radl@holl-schwab-law.at>"
    msg["Cc"] = "office@holl-schwab-law.at"
    msg["Subject"] = "Entlassung vom 14.03.2025 – Unterlagen"
    msg["Date"] = "Fri, 21 Mar 2025 09:15:00 +0100"
    if html:
        msg.set_content("<p>Sehr geehrter Herr Mag. Radl,</p><p>offen sind <b>EUR 7.864,80</b>.</p>", subtype="html")
    else:
        msg.set_content("Sehr geehrter Herr Mag. Radl,\n\nanbei die Gehaltsabrechnung. Offen sind EUR 7.864,80.\n")
    if attachment:
        msg.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="Gehaltsabrechnung.pdf")
    path.write_bytes(bytes(msg))


def test_eml_plain_text_extracted_with_headers(tmp_path: Path) -> None:
    path = tmp_path / "mail.eml"
    _write_eml(path, attachment=True)
    content, doc = ingest.DocumentIngestionPipeline._read_eml_file(None, str(path))
    assert doc is None
    assert content.startswith("# Entlassung vom 14.03.2025")
    assert "**From:** Verena Springer" in content and "**CC:** office@holl-schwab-law.at" in content
    assert "EUR 7.864,80" in content
    assert "- Gehaltsabrechnung.pdf" in content


def test_eml_html_body_is_stripped(tmp_path: Path) -> None:
    path = tmp_path / "mail.eml"
    _write_eml(path, html=True)
    content, _ = ingest.DocumentIngestionPipeline._read_eml_file(None, str(path))
    assert "EUR 7.864,80" in content and "<p>" not in content


def test_eml_is_a_supported_extension() -> None:
    source = Path(ingest.__file__).read_text(encoding="utf-8")
    assert '".eml",  # RFC 5322 email' in source
    assert "if file_ext == '.eml':" in source
