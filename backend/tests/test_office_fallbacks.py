"""Fallback readers for Office files the main readers reject (``src/ingestion/office_fallbacks.py``).

From the validation of a law firm's index (``docs/27-POST_INGESTION_VALIDATION.md``): Word
packages written with backslashes in their member names (about 500 letters and fee notes),
small Word 6/95 files ``antiword`` refuses (about 490), and Word files that hold only
pictures (229).
"""

from __future__ import annotations

import struct
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Dict

import pytest

from src.ingestion import office_fallbacks as fallbacks

ingest = pytest.importorskip("src.ingestion.ingest")

BACKSLASH = chr(92)


# ----------------------------------------------------------------------------- OOXML packages

def _package(path: Path, members: Dict[str, bytes], backslashes: bool) -> None:
    """Write a zip whose member names are stored with backslashes (as the practice software does)."""
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            if backslashes:
                info.filename = name.replace("/", BACKSLASH)  # stored as is, on every platform
            z.writestr(info, data)


MEMBERS = {
    "[Content_Types].xml": b"<Types/>",
    "_rels/.rels": b"<Relationships/>",
    "word/document.xml": b"<w:document>Sehr geehrte Frau Muster!</w:document>",
    "word/media/image2.jpeg": b"second picture",
    "word/media/image1.png": b"first picture",
    "word/media/image3.emf": b"vector art",
}


def test_backslash_member_names_are_detected_as_stored(tmp_path: Path) -> None:
    bad, good = tmp_path / "Honorarnote.docx", tmp_path / "Brief.docx"
    _package(bad, MEMBERS, backslashes=True)
    _package(good, MEMBERS, backslashes=False)
    assert fallbacks.has_backslash_member_names(str(bad)) is True
    assert fallbacks.has_backslash_member_names(str(good)) is False
    not_a_zip = tmp_path / "kaputt.docx"
    not_a_zip.write_bytes(b"not a zip")
    assert fallbacks.has_backslash_member_names(str(not_a_zip)) is False


def test_normalised_copy_has_forward_slashes_and_the_same_content(tmp_path: Path) -> None:
    src, dst = tmp_path / "Honorarnote.docx", tmp_path / "copy.docx"
    _package(src, MEMBERS, backslashes=True)
    assert fallbacks.normalise_ooxml_member_names(str(src), str(dst)) == 5, "every member below a folder"
    with zipfile.ZipFile(dst) as z:
        stored = {info.orig_filename: z.read(info) for info in z.infolist()}
    assert stored == MEMBERS
    assert fallbacks.has_backslash_member_names(str(dst)) is False


def test_media_images_come_in_picture_order_without_vector_art(tmp_path: Path) -> None:
    for backslashes in (True, False):
        path = tmp_path / f"Beilage-{backslashes}.docx"
        _package(path, MEMBERS, backslashes=backslashes)
        assert fallbacks.ooxml_media_images(str(path)) == [("image1.png", b"first picture"), ("image2.jpeg", b"second picture")]
    assert fallbacks.ooxml_media_images(str(tmp_path / "missing.docx")) == []


class _Result:
    def __init__(self, text: str) -> None:
        self.document = SimpleNamespace(text=text)


def test_pipeline_converts_a_repaired_copy_of_a_backslash_package(tmp_path: Path, monkeypatch) -> None:
    converted = []

    class _Converter:
        def convert(self, path):
            with zipfile.ZipFile(path) as z:
                converted.append(sorted(info.orig_filename for info in z.infolist()))
            return _Result("Sehr geehrte Frau Muster! Anbei unsere Honorarnote.")

    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Converter())
    monkeypatch.setattr(ingest, "export_document_markdown", lambda document: document.text)
    path = tmp_path / "Honorarnote.docx"
    _package(path, MEMBERS, backslashes=True)
    pipeline = ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)

    content, document = pipeline._read_document(str(path))

    assert content == "Sehr geehrte Frau Muster! Anbei unsere Honorarnote." and document is not None
    assert converted == [sorted(MEMBERS)], "the converter saw forward slashes"
    assert list(tmp_path.iterdir()) == [path], "the original file is untouched"


def test_picture_only_word_file_gets_the_text_of_its_pictures(tmp_path: Path, monkeypatch) -> None:
    class _Converter:
        def convert(self, path):
            if str(path).endswith(".docx"):
                return _Result("<!-- image -->\n\n<!-- image -->")
            return _Result("Flugticket Wien - Rom, 12.05.2019, EUR 189,00" if Path(path).read_bytes() == b"first picture" else "")

    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Converter())
    monkeypatch.setattr(ingest, "create_image_converter", lambda *a, **k: _Converter())
    monkeypatch.setattr(ingest, "prepare_image_for_docling", lambda path: (path, None))
    monkeypatch.setattr(ingest, "export_document_markdown", lambda document: document.text)
    monkeypatch.setattr(ingest, "image_noise_gate_enabled", lambda: False)
    path = tmp_path / "Flugtickets.docx"
    _package(path, MEMBERS, backslashes=False)
    pipeline = ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)

    content, document = pipeline._read_document(str(path))

    assert content == "Flugticket Wien - Rom, 12.05.2019, EUR 189,00", "the picture without text adds nothing"
    assert document is None
    assert ingest.markdown_has_text("<!-- image -->\n\n<!-- image -->") is False
    assert ingest.markdown_has_text("<!-- image -->\n\nKaufvertrag zwischen den Parteien") is True


# ----------------------------------------------------------------------------- Word binary

def _word6_stream(text: str) -> bytes:
    body = text.encode("cp1252")
    fc_min = 0x300
    header = bytearray(fc_min)
    struct.pack_into("<HH", header, 0, 0xA5DC, 101)  # Word 6
    struct.pack_into("<II", header, 0x18, fc_min, fc_min + len(body))
    struct.pack_into("<I", header, 0x34, len(body))
    return bytes(header) + body + b"\x00" * 64


def _word97_stream(pieces) -> Dict[str, bytes]:
    """A Word 97 ``WordDocument`` + ``1Table`` pair with one piece per ``(text, unicode)`` entry."""
    fc_min = 0x800
    data = bytearray(fc_min)
    struct.pack_into("<HH", data, 0, 0xA5EC, 193)
    struct.pack_into("<H", data, 0x0A, 0x0200)  # the table stream is 1Table
    positions, descriptors, cp = [0], [], 0
    for text, as_unicode in pieces:
        offset = len(data)
        if as_unicode:
            data += text.encode("utf-16-le")
            fc = offset
        else:
            data += text.encode("cp1252")
            fc = (offset * 2) | 0x40000000
        cp += len(text)
        positions.append(cp)
        descriptors.append(struct.pack("<HIH", 0, fc, 0))
    plc = struct.pack(f"<{len(positions)}I", *positions) + b"".join(descriptors)
    clx = b"\x01" + struct.pack("<H", 2) + b"\xaa\xbb" + b"\x02" + struct.pack("<I", len(plc)) + plc
    struct.pack_into("<I", data, 0x4C, cp)
    struct.pack_into("<II", data, 0x01A2, 16, len(clx))
    return {"WordDocument": bytes(data), "1Table": b"\x00" * 16 + clx}


@pytest.fixture
def fake_ole(monkeypatch):
    """Stand-in for ``olefile``: streams from a dict (building a real compound file is not the point)."""
    streams: Dict[str, bytes] = {}

    class _Ole:
        def __init__(self, path) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

        def exists(self, name: str) -> bool:
            return name in streams

        def openstream(self, name: str):
            return SimpleNamespace(read=lambda: streams[name])

    import olefile

    monkeypatch.setattr(olefile, "isOleFile", lambda path: True)
    monkeypatch.setattr(olefile, "OleFileIO", _Ole)
    return streams


def test_word6_text_is_read_from_the_stream(fake_ole) -> None:
    fake_ole["WordDocument"] = _word6_stream("GZ: 14 C 1234/95x\rDr. Max Muster\rKOSTENVERZEICHNIS\r\r\r\rNummer\x07Klient\x07Gegner\x07\rfür Mühe")
    text = fallbacks.word_binary_text("KOSTEN1.DOC")
    assert text == "GZ: 14 C 1234/95x\nDr. Max Muster\nKOSTENVERZEICHNIS\n\nNummer | Klient | Gegner | \nfür Mühe"


def test_word97_pieces_of_both_encodings_and_field_codes(fake_ole) -> None:
    fake_ole.update(_word97_stream([("Freilassungserklärung, EZ\xa0123 ", False), ("Gb 01234 Musterdorf € 500\r", True),
                                    ("Seite \x13 PAGE \x141\x15 von 3", False)]))
    text = fallbacks.word_binary_text("FREILASS.DOC")
    assert text == "Freilassungserklärung, EZ 123 Gb 01234 Musterdorf € 500\nSeite 1 von 3"


def test_word_binary_errors_name_the_reason(fake_ole, monkeypatch) -> None:
    with pytest.raises(fallbacks.WordBinaryError, match="no WordDocument stream"):
        fallbacks.word_binary_text("x.doc")
    encrypted = bytearray(_word6_stream("geheim"))
    struct.pack_into("<H", encrypted, 0x0A, 0x0100)
    fake_ole["WordDocument"] = bytes(encrypted)
    with pytest.raises(fallbacks.WordBinaryError, match="encrypted"):
        fallbacks.word_binary_text("x.doc")
    fake_ole["WordDocument"] = b"\x00" * 16
    with pytest.raises(fallbacks.WordBinaryError, match="too short"):
        fallbacks.word_binary_text("x.doc")
    import olefile

    monkeypatch.setattr(olefile, "isOleFile", lambda path: False)
    with pytest.raises(fallbacks.WordBinaryError, match="not an OLE2 file"):
        fallbacks.word_binary_text("x.doc")


def test_doc_reader_falls_back_to_the_word_binary_reader(tmp_path: Path, monkeypatch, fake_ole) -> None:
    """antiword: "the text stream of this file is too small to handle" - about 490 files of one corpus."""
    path = tmp_path / "MUSTER4.DOC"
    path.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 600)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/antiword")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="I'm afraid the text stream of this file is too small to handle.\n"))
    pipeline = ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)

    fake_ole["WordDocument"] = _word6_stream("MUSTERZEICHNUNG\r\rIch, Max Mustermann, werde als Geschäftsführer zeichnen.")
    assert pipeline._read_doc_file(str(path)) == ("MUSTERZEICHNUNG\n\nIch, Max Mustermann, werde als Geschäftsführer zeichnen.", None)

    fake_ole["WordDocument"] = _word6_stream("\r\r\r")
    content, _ = pipeline._read_doc_file(str(path))
    assert ingest.placeholder_kind(content) == "empty", "a document without body text is empty, not an error"

    fake_ole.clear()
    content, _ = pipeline._read_doc_file(str(path))
    assert ingest.placeholder_kind(content) == "error"
    assert "antiword: I'm afraid the text stream of this file is too small to handle." in content and "no WordDocument stream" in content
