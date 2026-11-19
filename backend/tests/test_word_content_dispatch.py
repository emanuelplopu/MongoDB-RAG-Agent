"""Word-named files are read by their content; password-protected files are a countable outcome.

From the audit of the 1,805 Word files still unreadable after the validation fixes of
2026-11-11 (``docs/26-FORMAT_EDGE_AUDIT.md``, follow-up 2026-11-19): Word 97 binaries named
``.docx``, PDFs and JPEG scans named ``.doc``, Outlook messages and an Excel workbook under
Word names, template packages saved as ``.doc``, Word 2.x files, owner files without their
``~$`` prefix, packages Docling rejects for a list definition, antiword output that is not
valid UTF-8, Word binaries whose text sits in text boxes or in pasted pictures, and files
that need a password.
"""

from __future__ import annotations

import os
import struct
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List

import pytest

from backend.services.ingestion_counters import apply_stats_counts
from src.ingestion import office_fallbacks as fallbacks
from src.ingestion import placeholders

ingest = pytest.importorskip("src.ingestion.ingest")

OLE2 = bytes.fromhex("d0cf11e0a1b11ae1")
ZIP = bytes.fromhex("504b0304")
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@pytest.fixture
def pipeline() -> ingest.DocumentIngestionPipeline:
    return ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)


@pytest.fixture
def fake_ole(monkeypatch) -> Dict[str, bytes]:
    """olefile stand-in: the streams of the container from a dict; ``None`` means a damaged file."""
    streams: Dict[str, bytes] = {}

    class _Ole:
        def __init__(self, path) -> None:
            if streams.get("__damaged__") is not None:
                raise OSError("incomplete OLE sector")

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

        def exists(self, name: str) -> bool:
            return name in streams

        def listdir(self):
            return [name.split("/") for name in streams if name != "__damaged__"]

        def openstream(self, name: str):
            return SimpleNamespace(read=lambda *a: streams[name])

    import olefile

    monkeypatch.setattr(olefile, "isOleFile", lambda path: True)
    monkeypatch.setattr(olefile, "OleFileIO", _Ole)
    return streams


def _owner_bytes(user: str = "egger") -> bytes:
    name = user.encode("cp1252")
    return (bytes([len(name)]) + name).ljust(54, b"\x00") + bytes([len(name), 0]) + user.encode("utf-16-le").ljust(106, b"\x00")


def _word2_file(text: str) -> bytes:
    body = text.encode("cp1252")
    header = bytearray(0x180)
    header[0:2] = fallbacks.WORD2_MAGIC
    struct.pack_into("<H", header, 2, 45)
    struct.pack_into("<II", header, 0x18, 0x180, 0x180 + len(body))
    return bytes(header) + body + b"\x00" * 32


def _word97_streams(main: str, textbox: str = "", header: str = "") -> Dict[str, bytes]:
    """A Word 97 ``WordDocument`` + ``1Table`` pair with one 8-bit piece holding every story."""
    fc_min = 0x800
    data = bytearray(fc_min)
    struct.pack_into("<HH", data, 0, 0xA5EC, 193)
    struct.pack_into("<H", data, 0x0A, 0x0200)
    stories = [len(main), 0, len(header), 0, 0, 0, len(textbox), 0]
    struct.pack_into("<8I", data, 0x4C, *stories)
    text = main + header + textbox
    offset = len(data)
    data += text.encode("cp1252")
    plc = struct.pack("<2I", 0, len(text)) + struct.pack("<HIH", 0, (offset * 2) | 0x40000000, 0)
    clx = b"\x02" + struct.pack("<I", len(plc)) + plc
    struct.pack_into("<II", data, 0x01A2, 16, len(clx))
    return {"WordDocument": bytes(data), "1Table": b"\x00" * 16 + clx}


def _docx(path: Path, body_xml: str, content_types: str = "wordprocessingml.document.main+xml", backslashes: bool = False) -> None:
    members = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            f'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.{content_types}"/></Types>'
        ).encode("utf-8"),
        "_rels/.rels": b"<Relationships/>",
        "word/document.xml": f'<w:document xmlns:w="{W_NS}"><w:body>{body_xml}</w:body></w:document>'.encode("utf-8"),
    }
    with zipfile.ZipFile(path, "w") as package:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            if backslashes:
                info.filename = name.replace("/", chr(92))
            package.writestr(info, data)


# ----------------------------------------------------------------------------- sniffing

def test_sniff_container_tells_the_kinds_apart() -> None:
    assert ingest.sniff_container(OLE2 + b"rest", 1000) == "ole2"
    assert ingest.sniff_container(ZIP + b"rest", 1000) == "zip"
    assert ingest.sniff_container(b"%PDF-1.4 ...", 1000) == "pdf"
    assert ingest.sniff_container(JPEG, 1000) == ".jpg"
    assert ingest.sniff_container(b"\x89PNG\r\n\x1a\n", 1000) == ".png"
    assert ingest.sniff_container(b"II*\x00", 1000) == ".tif"
    assert ingest.sniff_container(fallbacks.WORD2_MAGIC + b"-\x00", 1000) == "word2"
    assert ingest.sniff_container(_owner_bytes(), 162) == "owner"
    assert ingest.sniff_container(_owner_bytes(), 163) is None
    assert ingest.sniff_container(b"{\\rtf1 ...", 1000) is None
    assert ingest.sniff_container(b"", 0) is None


def test_office_owner_content_layout() -> None:
    assert fallbacks.is_office_owner_content(_owner_bytes("rpittner")) is True
    assert fallbacks.is_office_owner_content(_owner_bytes("egger")[:161]) is False
    assert fallbacks.is_office_owner_content(b"\x05egger" + b"\x00" * 156) is False, "no UTF-16 copy of the name"
    assert fallbacks.is_office_owner_content(b"\x00" * 162) is False


# ----------------------------------------------------------------------------- dispatch by content

def test_zero_byte_file_is_empty_not_an_error(pipeline, tmp_path: Path) -> None:
    path = tmp_path / "leer.pdf"
    path.write_bytes(b"")
    content, document = pipeline._read_document(str(path))
    assert document is None and ingest.placeholder_kind(content) == "empty" and "0 bytes" in content


def test_owner_file_without_prefix_is_empty(pipeline, tmp_path: Path) -> None:
    path = tmp_path / "UPC Kündigung.docx"
    path.write_bytes(_owner_bytes("rpittner"))
    content, document = pipeline._read_document(str(path))
    assert document is None and ingest.placeholder_kind(content) == "empty" and "owner file" in content


def test_docx_that_is_an_ole2_file_goes_to_the_doc_reader(pipeline, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "Rangordnungsgesuch-57975.docx"
    path.write_bytes(OLE2 + b"\x00" * 600)
    monkeypatch.setattr(pipeline, "_read_doc_file", lambda p: (f"doc:{Path(p).name}", None))
    assert pipeline._read_document(str(path)) == ("doc:Rangordnungsgesuch-57975.docx", None)


def test_docx_that_is_rtf_goes_to_the_doc_reader(pipeline, tmp_path: Path) -> None:
    pytest.importorskip("striprtf")
    path = tmp_path / "Brief.dotx"
    path.write_bytes(rb"{\rtf1\ansi\ansicpg1252 Sehr geehrter Herr Dr. Muster!}")
    content, document = pipeline._read_document(str(path))
    assert document is None and "Sehr geehrter Herr Dr. Muster!" in content


def test_pdf_and_jpeg_under_doc_names_go_to_their_readers(pipeline, tmp_path: Path, monkeypatch) -> None:
    renamed: List[str] = []
    monkeypatch.setattr(pipeline, "_read_as_renamed", lambda p, suffix: renamed.append(suffix) or (f"read as {suffix}", None))
    pdf = tmp_path / "grundbuchauszug.doc"
    pdf.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    scan = tmp_path / "10-08-06 Schr UVS S 1.doc"
    scan.write_bytes(JPEG + b"\x00" * 300)
    assert pipeline._read_document(str(pdf)) == ("read as .pdf", None)
    assert pipeline._read_document(str(scan)) == ("read as .jpg", None)
    assert renamed == [".pdf", ".jpg"]


def test_read_as_renamed_uses_a_temporary_copy_and_names_the_original(pipeline, tmp_path: Path, monkeypatch) -> None:
    seen: List[str] = []

    def fake_read(path: str, options=None):
        seen.append(path)
        return (f"[Error: Could not read file {os.path.basename(path)} (password-protected PDF)]", None)

    monkeypatch.setattr(pipeline, "_read_document", fake_read)
    path = tmp_path / "EZ 434.doc"
    path.write_bytes(b"%PDF-1.4")
    content, _ = pipeline._read_as_renamed(str(path), ".pdf")
    assert content == "[Error: Could not read file EZ 434.doc (password-protected PDF)]"
    assert seen and seen[0].endswith(".pdf") and not os.path.exists(seen[0])


# ----------------------------------------------------------------------------- OLE2 containers

def test_ole_container_kinds(fake_ole, tmp_path: Path) -> None:
    fake_ole.update({"EncryptedPackage": b"", "\x06DataSpaces/Version": b""})
    assert fallbacks.ole_container_kind("x.docx") == ("encrypted_package", "")
    fake_ole.clear()
    fake_ole.update(_word97_streams("Text"))
    assert fallbacks.ole_container_kind("x.doc") == ("word", "")
    encrypted = bytearray(fake_ole["WordDocument"])
    struct.pack_into("<H", encrypted, 0x0A, 0x0300)
    fake_ole["WordDocument"] = bytes(encrypted)
    assert fallbacks.ole_container_kind("x.doc") == ("word_encrypted", "")
    fake_ole.clear()
    fake_ole.update({"__properties_version1.0": b"", "__substg1.0_0037001F": b""})
    assert fallbacks.ole_container_kind("x.doc") == ("msg", "")
    fake_ole.clear()
    fake_ole.update({"Workbook": b"", "\x05SummaryInformation": b""})
    assert fallbacks.ole_container_kind("x.doc") == ("excel", "")
    fake_ole.clear()
    fake_ole.update({"\x01CompObj": b"", "CONTENTS": b"", "SPELLING": b""})
    assert fallbacks.ole_container_kind("x.doc") == ("other", "CONTENTS, CompObj, SPELLING")
    fake_ole.clear()
    fake_ole["__damaged__"] = b"x"
    assert fallbacks.ole_container_kind("x.doc") == ("damaged", "OSError: incomplete OLE sector")


def test_doc_reader_routes_the_ole2_kinds(pipeline, tmp_path: Path, monkeypatch, fake_ole) -> None:
    path = tmp_path / "Schadensmeldung Rechnung.doc"
    path.write_bytes(OLE2 + b"\x00" * 600)
    monkeypatch.setattr(pipeline, "_read_msg_file", lambda p: ("mail body", None))
    monkeypatch.setattr(pipeline, "_read_xls_file", lambda p: ("| Saldo |", None))

    fake_ole.update({"__properties_version1.0": b"", "__substg1.0_0037001F": b""})
    assert pipeline._read_doc_file(str(path)) == ("mail body", None)

    fake_ole.clear()
    fake_ole.update({"Workbook": b""})
    assert pipeline._read_doc_file(str(path)) == ("| Saldo |", None)

    fake_ole.clear()
    fake_ole.update({"\x01CompObj": b"", "CONTENTS": b"", "SPELLING": b""})
    content, _ = pipeline._read_doc_file(str(path))
    assert ingest.placeholder_kind(content) == "error" and "another application" in content and "CONTENTS" in content
    assert placeholders.placeholder_error_type(content) == "error"

    fake_ole.clear()
    fake_ole["__damaged__"] = b"x"
    content, _ = pipeline._read_doc_file(str(path))
    assert ingest.placeholder_kind(content) == "error" and "damaged or truncated OLE2 file" in content


def test_password_protected_files_are_encrypted_not_error(pipeline, tmp_path: Path, monkeypatch, fake_ole) -> None:
    path = tmp_path / "Frageliste.doc"
    path.write_bytes(OLE2 + b"\x00" * 600)
    ran: List[str] = []
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/antiword")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append("antiword") or SimpleNamespace(returncode=1, stdout=b"", stderr=b""))

    fake_ole.update(_word97_streams("geheim"))
    encrypted = bytearray(fake_ole["WordDocument"])
    struct.pack_into("<H", encrypted, 0x0A, 0x0300)
    fake_ole["WordDocument"] = bytes(encrypted)
    content, _ = pipeline._read_doc_file(str(path))
    assert content == "[Error: Could not read file Frageliste.doc (password-protected Word document)]"
    assert placeholders.placeholder_error_type(content) == "encrypted"

    fake_ole.clear()
    fake_ole.update({"EncryptedPackage": b"", "EncryptionInfo": b""})
    content, _ = pipeline._read_doc_file(str(path))
    assert content == "[Error: Could not read file Frageliste.doc (password-protected Office package)]"
    assert placeholders.placeholder_error_type(content) == "encrypted"
    assert ran == [], "antiword is not run for a file that needs a password"

    assert placeholders.placeholder_error_type("[Error: Could not read file x.pdf (password-protected PDF)]") == "encrypted"
    assert placeholders.placeholder_error_type("[Error: Could not read file x.doc (not a Word document and not text)]") == "error"
    error = ingest.UnreadableFileError("Could not read file x.doc (password-protected Word document)", error_type="encrypted")
    assert error.error_type == "encrypted" and ingest.UnreadableFileError("x").error_type == "error"
    assert ingest.map_error_type_to_classification("encrypted", 0) == "error"


def test_encrypted_files_count_as_failed_files_of_the_job() -> None:
    job: Dict[str, object] = {}
    apply_stats_counts(job, {"error": 2, "encrypted": 3, "timeout": 1}, baseline={"encrypted": 4})
    assert job["error_files"] == 9 and job["failed_files"] == 10


# ----------------------------------------------------------------------------- antiword output

def test_antiword_output_with_a_stray_byte_is_not_an_error(pipeline, tmp_path: Path, monkeypatch, fake_ole) -> None:
    path = tmp_path / "Rekurs-50480.doc"
    path.write_bytes(OLE2 + b"\x00" * 600)
    fake_ole.update(_word97_streams("Rekurs"))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/antiword")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="Bezirksgericht D\xf6bling \xe2\x80\x93 5\xc2\xa0MSch 12/19 \xed Ende".encode("latin-1", "ignore").replace(b"\xf6", b"\xc3\xb6"), stderr=b""))
    content, document = pipeline._read_doc_file(str(path))
    assert document is None and content.startswith("Bezirksgericht Döbling") and "� Ende" in content


# ----------------------------------------------------------------------------- Word binaries without body text

def test_word97_text_boxes_are_read_when_the_body_is_empty_but_headers_are_not(fake_ole) -> None:
    fake_ole.update(_word97_streams("", textbox="GRUNDBUCHSAUSZUG\rEZ 961 KG 11029", header="Seite 1 von 1"))
    assert fallbacks.word_binary_text("Grundbuchsauszug.doc") == "GRUNDBUCHSAUSZUG\nEZ 961 KG 11029"
    fake_ole.update(_word97_streams("Haupttext", textbox="Textbox", header="Kopf"))
    assert fallbacks.word_binary_text("Brief.doc") == "Haupttext", "the main story wins when it has text"
    fake_ole.update(_word97_streams("", textbox="", header="Seite 1 von 1"))
    assert fallbacks.word_binary_text("Scan.doc") == "", "a page number in the header is not the document's text"


def test_pictures_of_a_word_binary_are_found_in_its_streams(fake_ole) -> None:
    jpeg = JPEG + b"\x01" * 3000 + b"\xff\xd9"
    png = b"\x89PNG\r\n\x1a\n" + b"\x02" * 2500 + b"IEND\xaeB`\x82"
    fake_ole.update({"WordDocument": b"\x00" * 100 + png + b"\x00" * 50, "Data": b"\x00" * 10 + jpeg + b"\xff\xd8\xff" + b"\x03" * 10 + b"\xff\xd9"})
    images = fallbacks.ole_embedded_images("Scan.doc")
    assert [(name, len(data)) for name, data in images] == [("picture1.png", len(png)), ("picture2.jpg", len(jpeg))]
    assert images[1][1] == jpeg, "the thumbnail-sized second JPEG is skipped"


def test_doc_without_body_text_gets_the_text_of_its_pictures(pipeline, tmp_path: Path, monkeypatch, fake_ole) -> None:
    path = tmp_path / "fotos admond.doc"
    path.write_bytes(OLE2 + b"\x00" * 600)
    fake_ole.update(_word97_streams(""))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/antiword")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"", stderr=b""))
    monkeypatch.setattr(ingest, "ole_embedded_images", lambda p: [("picture1.jpg", b"scan"), ("picture2.png", b"blank")])
    monkeypatch.setattr(pipeline, "_read_document", lambda p: ("Befund vom 12.03.2004" if Path(p).read_bytes() == b"scan" else "[Image: x - OCR extracted minimal text]", None))

    with ingest.conversion_notes() as notes:
        content, document = pipeline._read_doc_file(str(path))
    assert content == "Befund vom 12.03.2004" and document is None
    assert notes == {"ocr_fallback": "word_binary_pictures"}

    # A form in text boxes with a pasted scan: both texts
    fake_ole.update(_word97_streams("", textbox="Formular 12", header="Seite 1 von 1"))
    with ingest.conversion_notes() as notes:
        content, _ = pipeline._read_doc_file(str(path))
    assert content == "Formular 12\n\n---\n\nBefund vom 12.03.2004"
    assert notes == {"doc_reader": "word_binary_stories", "ocr_fallback": "word_binary_pictures"}

    fake_ole.update(_word97_streams("", header="Seite 1 von 1"))
    monkeypatch.setattr(ingest, "ole_embedded_images", lambda p: [])
    content, _ = pipeline._read_doc_file(str(path))
    assert ingest.placeholder_kind(content) == "empty"


# ----------------------------------------------------------------------------- Word 2.x

def test_word2_file_is_read_from_its_fib_offsets(pipeline, tmp_path: Path) -> None:
    path = tmp_path / "13-12ARN.DOC"
    path.write_bytes(_word2_file("An die\r\nArnegger & Bossanyi\r\n\tWien,am \x13ZEIT \\@ \"tt.MM.jj\"\x1403.10.95\x15\r\nBetrifft: R\xe4umungsklage"))
    with ingest.conversion_notes() as notes:
        content, document = pipeline._read_doc_file(str(path))
    assert content == "An die\nArnegger & Bossanyi\n\tWien,am 03.10.95\nBetrifft: Räumungsklage" and document is None
    assert notes == {"doc_reader": "word2_binary"}
    broken = tmp_path / "SINN.DOC"
    raw = bytearray(_word2_file("x"))
    struct.pack_into("<II", raw, 0x18, 5000, 6000)
    broken.write_bytes(bytes(raw))
    content, _ = pipeline._read_doc_file(str(broken))
    assert ingest.placeholder_kind(content) == "error" and "Word 2 file" in content


# ----------------------------------------------------------------------------- OOXML packages

def test_main_part_kind_comes_from_the_content_types(tmp_path: Path) -> None:
    document, template = tmp_path / "a.docx", tmp_path / "Brief.doc"
    _docx(document, "<w:p><w:r><w:t>x</w:t></w:r></w:p>")
    _docx(template, "<w:p/>", content_types="wordprocessingml.template.main+xml")
    assert fallbacks.ooxml_main_part_kind(str(document)) == ".docx"
    assert fallbacks.ooxml_main_part_kind(str(template)) == ".dotx"
    not_a_zip = tmp_path / "x.doc"
    not_a_zip.write_bytes(b"nope")
    assert fallbacks.ooxml_main_part_kind(str(not_a_zip)) is None


def test_template_package_under_a_doc_name_is_converted_as_a_document(pipeline, tmp_path: Path, monkeypatch) -> None:
    seen: List[bytes] = []

    class _Converter:
        def convert(self, path):
            with zipfile.ZipFile(path) as package:
                seen.append(package.read("[Content_Types].xml"))
            return SimpleNamespace(document=SimpleNamespace(text="Stellungnahme zum Vorfall"))

    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Converter())
    monkeypatch.setattr(ingest, "export_document_markdown", lambda document: document.text)
    path = tmp_path / "Stellungnahme.doc"
    _docx(path, "<w:p/>", content_types="wordprocessingml.template.main+xml", backslashes=True)
    content, document = pipeline._read_document(str(path))
    assert content == "Stellungnahme zum Vorfall" and document is not None
    assert b"wordprocessingml.document.main+xml" in seen[0] and b"template.main+xml" not in seen[0]

    docx = tmp_path / "Vorlage.docx"
    _docx(docx, "<w:p/>", content_types="wordprocessingml.template.main+xml")
    content, _ = pipeline._read_document(str(docx))
    assert content == "Stellungnahme zum Vorfall" and len(seen) == 2


BODY = (
    "<w:p><w:r><w:t>Verwaltungsvertrag</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>Zwischen</w:t><w:tab/><w:t xml:space=\"preserve\">Frau Muster </w:t><w:br/><w:t>und</w:t></w:r></w:p>"
    "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Objekt</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>EZ 52</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    "<w:sdt><w:sdtContent><w:p><w:r><w:t>Ende</w:t></w:r></w:p></w:sdtContent></w:sdt>"
)


def test_docx_plain_text_reads_paragraphs_tables_and_content_controls(tmp_path: Path) -> None:
    path = tmp_path / "Verwaltungsvertrag-934991.docx"
    _docx(path, BODY, backslashes=True)
    assert fallbacks.docx_plain_text(str(path)) == "Verwaltungsvertrag\nZwischen\tFrau Muster \nund\nObjekt | EZ 52\nEnde"
    not_a_zip = tmp_path / "x.docx"
    not_a_zip.write_bytes(b"nope")
    assert fallbacks.docx_plain_text(str(not_a_zip)) == ""


def test_docling_failure_on_a_word_package_falls_back_to_document_xml(pipeline, tmp_path: Path, monkeypatch) -> None:
    class _Converter:
        def convert(self, path):
            raise RuntimeError("Pipeline SimplePipeline failed")

    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Converter())
    path = tmp_path / "Vorsorgevollmacht-1182388.docx"
    _docx(path, BODY)
    with ingest.conversion_notes() as notes:
        content, document = pipeline._read_document(str(path))
    assert content.startswith("Verwaltungsvertrag\nZwischen") and document is None
    assert notes == {"doc_reader": "docx_xml"}

    renamed = tmp_path / "Vorsorgevollmacht.doc"
    _docx(renamed, BODY)
    content, document = pipeline._read_document(str(renamed))
    assert content.startswith("Verwaltungsvertrag") and document is None

    empty = tmp_path / "leer.docx"
    _docx(empty, "<w:p/>")
    content, _ = pipeline._read_document(str(empty))
    assert ingest.placeholder_kind(content) == "error" and "SimplePipeline" in content, "no text in document.xml: the conversion error stands"
