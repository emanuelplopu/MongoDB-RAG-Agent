"""Tests for email (.msg/.eml) attachment text extraction in src/ingestion/ingest.py.

Covers the pure helper (noise filtering, caps, failure isolation, placeholder rejection),
the env/settings resolver, and the .eml / .msg wiring behind the MSG_EXTRACT_ATTACHMENTS flag.
"""

from __future__ import annotations

import os
from email.message import EmailMessage
from pathlib import Path

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")


# --------------------------------------------------------------------------- helpers

def _ext_reader(path: str):
    """A fake document reader: returns deterministic text keyed by the file extension."""
    ext = os.path.splitext(path)[1].lower()
    return (f"EXTRACTED CONTENT for a {ext} file with enough text to pass", None)


# --------------------------------------------------------------------------- resolver

def test_resolve_config_defaults_off(monkeypatch):
    for var in ("MSG_EXTRACT_ATTACHMENTS", "MSG_ATTACHMENT_MAX_MB", "MSG_ATTACHMENT_MAX_COUNT"):
        monkeypatch.delenv(var, raising=False)
    # Force the settings fallback to fail so we test the module defaults deterministically.
    monkeypatch.setattr(ingest, "load_settings", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    enabled, max_bytes, max_count = ingest.resolve_msg_attachment_config()
    assert enabled is False
    assert max_bytes == 25 * 1024 * 1024
    assert max_count == 20


def test_resolve_config_from_env(monkeypatch):
    monkeypatch.setenv("MSG_EXTRACT_ATTACHMENTS", "true")
    monkeypatch.setenv("MSG_ATTACHMENT_MAX_MB", "5")
    monkeypatch.setenv("MSG_ATTACHMENT_MAX_COUNT", "3")
    enabled, max_bytes, max_count = ingest.resolve_msg_attachment_config()
    assert enabled is True
    assert max_bytes == 5 * 1024 * 1024
    assert max_count == 3


def test_text_extensions_exclude_email_and_media():
    exts = ingest.email_attachment_text_extensions()
    assert ".pdf" in exts and ".doc" in exts and ".docx" in exts and ".jpg" in exts
    assert ".msg" not in exts and ".eml" not in exts
    assert ".mp3" not in exts and ".mp4" not in exts and ".wmv" not in exts


# --------------------------------------------------------------------------- helper

def test_helper_extracts_documents_and_skips_noise():
    atts = [
        ("Klage.pdf", b"%PDF-1.4 fake"),
        ("smime.p7s", b"signature-bytes"),
        ("Vertrag.docx", b"PK\x03\x04 fake docx"),
        ("logo.png", b"x" * 1024),                 # tiny inline image -> skipped
        ("beilage.mp3", b"audio-bytes" * 100),     # media -> skipped
    ]
    blocks = ingest.extract_email_attachment_text(atts, _ext_reader, max_bytes=0, max_count=0)
    assert len(blocks) == 2
    assert blocks[0].startswith("### Attachment: Klage.pdf")
    assert any(b.startswith("### Attachment: Vertrag.docx") for b in blocks)
    assert all("smime.p7s" not in b and "logo.png" not in b and "beilage.mp3" not in b for b in blocks)


def test_helper_large_image_is_extracted():
    atts = [("Scan.jpg", b"x" * (60 * 1024))]  # >= 50KB -> treated as a document scan
    blocks = ingest.extract_email_attachment_text(atts, _ext_reader, max_bytes=0, max_count=0)
    assert len(blocks) == 1 and blocks[0].startswith("### Attachment: Scan.jpg")


def test_helper_respects_count_cap():
    atts = [("a.pdf", b"%PDF"), ("b.pdf", b"%PDF"), ("c.pdf", b"%PDF")]
    blocks = ingest.extract_email_attachment_text(atts, _ext_reader, max_bytes=0, max_count=1)
    assert len(blocks) == 1


def test_helper_respects_size_cap():
    atts = [("big.pdf", b"x" * 2048), ("small.pdf", b"%PDF")]
    blocks = ingest.extract_email_attachment_text(atts, _ext_reader, max_bytes=1024, max_count=0)
    assert len(blocks) == 1 and blocks[0].startswith("### Attachment: small.pdf")


def test_helper_isolates_reader_failures():
    def flaky(path: str):
        if path.endswith(".pdf"):
            raise ValueError("boom")
        return ("good text that is long enough", None)

    atts = [("bad.pdf", b"%PDF"), ("good.docx", b"PK")]
    blocks = ingest.extract_email_attachment_text(atts, flaky, max_bytes=0, max_count=0)
    assert len(blocks) == 1 and blocks[0].startswith("### Attachment: good.docx")


def test_helper_rejects_placeholder_and_empty_output():
    def placeholder_reader(path: str):
        return ("[Error: Could not read file x]", None)

    atts = [("x.pdf", b"%PDF"), ("y.docx", b"PK")]
    blocks = ingest.extract_email_attachment_text(atts, placeholder_reader, max_bytes=0, max_count=0)
    assert blocks == []


# --------------------------------------------------------------------------- .eml wiring

def _write_eml_with_text_attachment(path: Path) -> None:
    msg = EmailMessage()
    msg["From"] = "kanzlei@example.at"
    msg["To"] = "mandant@example.at"
    msg["Subject"] = "Unterlagen"
    msg.set_content("Anbei die Unterlagen wie besprochen.")
    msg.add_attachment(
        "Aktenvermerk: offener Betrag EUR 1.234,56 gemaess Vereinbarung.".encode("utf-8"),
        maintype="text", subtype="plain", filename="anhang.txt",
    )
    path.write_bytes(bytes(msg))


def test_eml_attachments_listed_only_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MSG_EXTRACT_ATTACHMENTS", "false")
    path = tmp_path / "mail.eml"
    _write_eml_with_text_attachment(path)
    content, _ = ingest.DocumentIngestionPipeline._read_eml_file(None, str(path))
    assert "- anhang.txt" in content
    assert "## Attachment contents" not in content
    assert "EUR 1.234,56" not in content


def test_eml_attachment_text_extracted_when_enabled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MSG_EXTRACT_ATTACHMENTS", "true")
    path = tmp_path / "mail.eml"
    _write_eml_with_text_attachment(path)
    pipe = object.__new__(ingest.DocumentIngestionPipeline)  # _read_document needs no init for .txt
    content, _ = pipe._read_eml_file(str(path))
    assert "- anhang.txt" in content
    assert "## Attachment contents" in content
    assert "### Attachment: anhang.txt" in content
    assert "EUR 1.234,56" in content


# --------------------------------------------------------------------------- .msg wiring

class _FakeAttachment:
    def __init__(self, name: str, data: bytes):
        self.longFilename = name
        self.shortFilename = name
        self.data = data


class _FakeMessage:
    def __init__(self, _path):
        self.subject = "Mandat"
        self.sender = "kanzlei@example.at"
        self.to = "mandant@example.at"
        self.cc = None
        self.date = "2025-03-21"
        self.body = "Anbei die Unterlagen."
        self.htmlBody = None
        self.attachments = [
            _FakeAttachment("Schriftsatz.txt", b"Streitwert EUR 9.999,00 laut Beilage."),
            _FakeAttachment("smime.p7s", b"signature"),
        ]

    def close(self):
        pass


def test_msg_attachment_text_extracted_when_enabled(monkeypatch, tmp_path: Path):
    extract_msg = pytest.importorskip("extract_msg")
    monkeypatch.setenv("MSG_EXTRACT_ATTACHMENTS", "true")
    monkeypatch.setattr(extract_msg, "Message", _FakeMessage)
    dummy = tmp_path / "mail.msg"
    dummy.write_bytes(b"not-a-real-msg")  # the fake Message ignores the path
    pipe = object.__new__(ingest.DocumentIngestionPipeline)
    content, doc = pipe._read_msg_file(str(dummy))
    assert doc is None
    assert "- Schriftsatz.txt" in content and "- smime.p7s" in content
    assert "## Attachment contents" in content
    assert "### Attachment: Schriftsatz.txt" in content
    assert "EUR 9.999,00" in content
    assert "### Attachment: smime.p7s" not in content  # signature is noise


def test_msg_attachments_listed_only_by_default(monkeypatch, tmp_path: Path):
    extract_msg = pytest.importorskip("extract_msg")
    monkeypatch.setenv("MSG_EXTRACT_ATTACHMENTS", "false")
    monkeypatch.setattr(extract_msg, "Message", _FakeMessage)
    dummy = tmp_path / "mail.msg"
    dummy.write_bytes(b"not-a-real-msg")
    content, _ = ingest.DocumentIngestionPipeline._read_msg_file(None, str(dummy))
    assert "- Schriftsatz.txt" in content
    assert "## Attachment contents" not in content
    assert "EUR 9.999,00" not in content
