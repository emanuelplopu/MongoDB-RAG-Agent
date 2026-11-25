"""Tests for the PowerPoint readers (docs/26, PowerPoint follow-up of 2026-11-19).

Legacy ``.ppt`` decks are read from their record stream (``ppt_binary_text``), their photos
from the ``Pictures`` stream (``ppt_binary_pictures``); picture-only ``.pptx`` decks go
through the same picture OCR as picture-only Word files; packages cut off by an interrupted
copy are salvaged from their local file headers (``truncated_ooxml_text``). The OLE
container is faked from a dict of streams, Docling is stubbed.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Tuple

import pytest

from src.ingestion import office_fallbacks as fallbacks

ingest = pytest.importorskip("src.ingestion.ingest")

OLE2_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")


# ----------------------------------------------------------------------------- record builders

def atom(rtype: int, body: bytes, instance: int = 0) -> bytes:
    return struct.pack("<HHI", instance << 4, rtype, len(body)) + body


def container(rtype: int, *children: bytes, instance: int = 0) -> bytes:
    body = b"".join(children)
    return struct.pack("<HHI", (instance << 4) | 0xF, rtype, len(body)) + body


def chars(text: str) -> bytes:
    return atom(fallbacks._RT_TEXT_CHARS_ATOM, text.encode("utf-16-le"))


def latin(text: str) -> bytes:
    return atom(fallbacks._RT_TEXT_BYTES_ATOM, text.encode("cp1252"))


def slide(*texts: str) -> bytes:
    return container(fallbacks._RT_SLIDE, *(chars(t) for t in texts))


def user_edit(offset_last_edit: int, offset_directory: int) -> bytes:
    body = struct.pack("<IHBBIIIIHH", 256, 0x03E8, 0, 3, offset_last_edit, offset_directory, 1, 100, 0, 0)
    return atom(fallbacks._RT_USER_EDIT_ATOM, body)


def persist_directory(entries: Dict[int, int]) -> bytes:
    body = b"".join(struct.pack("<II", (1 << 20) | persist_id, offset) for persist_id, offset in entries.items())
    return atom(fallbacks._RT_PERSIST_DIRECTORY_ATOM, body)


def current_user(offset_to_current_edit: int, encrypted: bool = False) -> bytes:
    token = fallbacks._CURRENT_USER_ENCRYPTED if encrypted else 0xE391C05F
    return atom(0x0FF6, struct.pack("<III", 20, token, offset_to_current_edit) + b"\x00" * 8)


def document(*lists: bytes) -> bytes:
    return container(fallbacks._RT_DOCUMENT, *lists)


def slide_persist(persist_id: int) -> bytes:
    return atom(fallbacks._RT_SLIDE_PERSIST_ATOM, struct.pack("<IIIII", persist_id, 0, 1, 256, 0))


def slide_list(*texts: str, instance: int = 0, refs: Tuple[int, ...] = ()) -> bytes:
    """A SlideListWithText: one SlidePersistAtom per referenced slide, then the texts."""
    return container(
        fallbacks._RT_SLIDE_LIST_WITH_TEXT,
        *(slide_persist(r) for r in refs),
        *(chars(t) for t in texts),
        instance=instance,
    )


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


# ----------------------------------------------------------------------------- ppt_binary_text

def test_ppt_text_comes_from_slide_lists_and_slides_without_the_masters(fake_ole) -> None:
    master = container(fallbacks._RT_MAIN_MASTER, chars("Titelmasterformat durch Klicken bearbeiten"))
    fake_ole["PowerPoint Document"] = (
        document(
            slide_list("Mastertextformat bearbeiten", instance=fallbacks._SLIDE_LIST_MASTERS),
            slide_list("Sponsoring\rGratwanderung\x0bzwischen Korruption und Erlaubtem", "Themenkreise\t\t"),
        )
        + master
        + slide(" ohne wirtschaftliche Gegenleistung", "§ 153 StGB Untreue")
        + container(fallbacks._RT_NOTES, latin("Notiz: Strafrahmen bis zu einem Jahr"))
    )

    text = fallbacks.ppt_binary_text("Vortrag.ppt")

    assert text == (
        "Sponsoring\nGratwanderung\nzwischen Korruption und Erlaubtem\n\n"
        "Themenkreise\n\n"
        "ohne wirtschaftliche Gegenleistung\n\n"
        "§ 153 StGB Untreue\n\n"
        "Notiz: Strafrahmen bis zu einem Jahr"
    )
    assert "master" not in text.lower(), "placeholder text of the masters is noise"


def test_incrementally_saved_deck_reads_only_the_current_copy_of_each_slide(fake_ole) -> None:
    """PowerPoint's incremental save appends the edited slide again; the persist directory says which copy is live."""
    doc = document(slide_list("Jahresabschluss 2002", refs=(2,)))
    old_slide = slide("Kennzahlen 2001 (alt)")
    new_slide = slide("Kennzahlen 2002 (neu)")
    stream = doc + old_slide
    first_directory = persist_directory({1: 0, 2: len(doc)})
    first_edit_offset = len(stream) + len(first_directory)
    stream += first_directory + user_edit(0, len(stream))
    new_slide_offset = len(stream)
    stream += new_slide
    second_directory_offset = len(stream)
    stream += persist_directory({2: new_slide_offset})
    second_edit_offset = len(stream)
    stream += user_edit(first_edit_offset, second_directory_offset)
    fake_ole["PowerPoint Document"] = stream
    fake_ole["Current User"] = current_user(second_edit_offset)

    text = fallbacks.ppt_binary_text("Jahresabschluss.ppt")

    assert text == "Jahresabschluss 2002\n\nKennzahlen 2002 (neu)"

    fake_ole["Current User"] = current_user(first_edit_offset)
    assert fallbacks.ppt_binary_text("Jahresabschluss.ppt") == "Jahresabschluss 2002\n\nKennzahlen 2001 (alt)"

    del fake_ole["Current User"]  # no directory: every copy is read (a duplicate beats a loss)
    assert fallbacks.ppt_binary_text("Jahresabschluss.ppt") == "Jahresabschluss 2002\n\nKennzahlen 2001 (alt)\n\nKennzahlen 2002 (neu)"


def test_title_master_and_slide_number_placeholders_stay_out(fake_ole) -> None:
    """A title master is a slide container too; only the masters' list (instance 1) refers to it."""
    doc = document(
        slide_list("Hier klicken, um Master-Titelformat zu bearbeiten", instance=fallbacks._SLIDE_LIST_MASTERS, refs=(2,)),
        slide_list("Spende", refs=(3,)),
    )
    title_master = slide("Hier klicken, um Master-Textformat zu bearbeiten", "Zweite Ebene", "*")
    real_slide = slide("Strafgesetzbuch: § 153 Untreue", "*")
    stream = doc + title_master + real_slide
    directory = persist_directory({1: 0, 2: len(doc), 3: len(doc) + len(title_master)})
    directory_offset = len(stream)
    stream += directory
    edit_offset = len(stream)
    stream += user_edit(0, directory_offset)
    fake_ole["PowerPoint Document"] = stream
    fake_ole["Current User"] = current_user(edit_offset)

    assert fallbacks.ppt_binary_text("Vortrag.ppt") == "Spende\n\nStrafgesetzbuch: § 153 Untreue"


def test_ppt_binary_errors_name_the_reason(fake_ole, monkeypatch) -> None:
    with pytest.raises(fallbacks.PptBinaryError, match="no PowerPoint Document stream"):
        fallbacks.ppt_binary_text("Vortrag.ppt")

    fake_ole["PowerPoint Document"] = document(slide_list("Geheim"))
    fake_ole["Current User"] = current_user(0, encrypted=True)
    with pytest.raises(fallbacks.PptBinaryError, match="encrypted"):
        fallbacks.ppt_binary_text("Vortrag.ppt")

    del fake_ole["Current User"]
    fake_ole["PowerPoint Document"] = atom(fallbacks._RT_CRYPT_SESSION, b"\x00" * 8) + document(slide_list("Geheim"))
    with pytest.raises(fallbacks.PptBinaryError, match="encrypted"):
        fallbacks.ppt_binary_text("Vortrag.ppt")

    import olefile

    monkeypatch.setattr(olefile, "isOleFile", lambda path: False)
    with pytest.raises(fallbacks.PptBinaryError, match="not an OLE2 file"):
        fallbacks.ppt_binary_text("Vortrag.ppt")


def test_ppt_pictures_skip_the_uids_and_vector_art(fake_ole) -> None:
    jpeg_one_uid = atom(0xF01D, b"\x01" * 16 + b"\xff" + b"\xff\xd8JPEG1", instance=0x46A)
    jpeg_two_uids = atom(0xF01D, b"\x02" * 32 + b"\xff" + b"\xff\xd8JPEG2", instance=0x46B)
    png = atom(0xF01E, b"\x03" * 16 + b"\xff" + b"\x89PNG3", instance=0x6E0)
    emf = atom(0xF01A, b"\x04" * 50)
    fake_ole["Pictures"] = emf + jpeg_one_uid + png + jpeg_two_uids

    assert fallbacks.ppt_binary_pictures("Fotos.ppt") == [
        ("picture1.jpg", b"\xff\xd8JPEG1"),
        ("picture2.png", b"\x89PNG3"),
        ("picture3.jpg", b"\xff\xd8JPEG2"),
    ]
    assert fallbacks.ppt_binary_pictures("Fotos.ppt", max_images=1) == [("picture1.jpg", b"\xff\xd8JPEG1")]
    assert fallbacks.ppt_binary_pictures("Fotos.ppt", max_bytes=4) == []

    del fake_ole["Pictures"]
    assert fallbacks.ppt_binary_pictures("Fotos.ppt") == []


# ----------------------------------------------------------------------------- truncated packages

SLIDE_XML = (
    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree>'
    "<p:sp><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p>"
    "<a:p><a:r><a:t>Ladies and </a:t></a:r><a:r><a:t>gentlemen</a:t></a:r></a:p><a:p/></p:txBody></p:sp>"
    "</p:spTree></p:cSld></p:sld>"
)


def _package_bytes(members: List[Tuple[str, bytes]], path: Path) -> bytes:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            z.writestr(name, data)
    return path.read_bytes()


def _cut_inside(data: bytes, member: str) -> bytes:
    """The package bytes up to a point inside ``member``'s data (as an interrupted copy leaves them)."""
    header = data.index(b"PK\x03\x04" + b"\x00" * 0, 0)
    pos = data.index(member.encode()) - 30
    assert pos >= header
    _sig, _ver, _flags, _method, _t, _d, _crc, csize, _usize, name_len, extra_len = struct.unpack_from("<IHHHHHIIIHH", data, pos)
    start = pos + 30 + name_len + extra_len
    return data[: start + csize // 2]


def test_truncated_pptx_yields_the_slides_before_the_cut_in_slide_order(tmp_path: Path) -> None:
    members = [
        ("[Content_Types].xml", b"<Types/>"),
        ("ppt/slides/slide10.xml", SLIDE_XML.format(title="Closing").encode()),
        ("ppt/slides/slide2.xml", SLIDE_XML.format(title="The facts").encode()),
        ("ppt/slides/slide1.xml", SLIDE_XML.format(title="Opening statement").encode()),
        ("ppt/media/image1.jpeg", bytes(range(256)) * 400),
        ("ppt/slides/slide3.xml", SLIDE_XML.format(title="Lost after the cut").encode()),
    ]
    whole = _package_bytes(members, tmp_path / "whole.pptx")
    cut = tmp_path / "5.5millionopening.pptx"
    cut.write_bytes(_cut_inside(whole, "ppt/media/image1.jpeg"))
    assert not zipfile.is_zipfile(cut)

    text, note = fallbacks.truncated_ooxml_text(str(cut))

    assert text == (
        "## Slide 1\n\nOpening statement\n\nLadies and gentlemen\n\n"
        "## Slide 2\n\nThe facts\n\nLadies and gentlemen\n\n"
        "## Slide 10\n\nClosing\n\nLadies and gentlemen"
    )
    assert note == "4 complete members, 1 cut off, 3 slide part(s)"


def test_truncated_docx_yields_its_body_and_errors_name_the_reason(tmp_path: Path) -> None:
    body = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        "<w:p><w:r><w:t>Sehr geehrte </w:t></w:r><w:r><w:t>Frau Muster!</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Mit freundlichen Grüßen</w:t></w:r></w:p></w:body></w:document>"
    ).encode("utf-8")
    whole = _package_bytes([("word/document.xml", body), ("word/media/image1.png", b"\x89PNG" * 2000)], tmp_path / "whole.docx")
    cut = tmp_path / "Brief.docx"
    cut.write_bytes(_cut_inside(whole, "word/media/image1.png"))
    assert fallbacks.truncated_ooxml_text(str(cut)) == ("Sehr geehrte Frau Muster!\n\nMit freundlichen Grüßen", "1 complete members, 1 cut off, 0 slide part(s)")

    pictures_only = _package_bytes([("ppt/media/image1.png", b"\x89PNG" * 10), ("ppt/media/image2.png", b"\x89PNG" * 2000)], tmp_path / "pics.pptx")
    cut.write_bytes(_cut_inside(pictures_only, "ppt/media/image2.png"))
    with pytest.raises(fallbacks.TruncatedPackageError, match="no text part among the 1 complete members"):
        fallbacks.truncated_ooxml_text(str(cut))

    cut.write_bytes(b"PK\x03\x04" + b"\x00" * 10)
    with pytest.raises(fallbacks.TruncatedPackageError, match="no complete package member"):
        fallbacks.truncated_ooxml_text(str(cut))

    cut.write_bytes(b"not a zip at all")
    with pytest.raises(fallbacks.TruncatedPackageError, match="not a zip package"):
        fallbacks.truncated_ooxml_text(str(cut))


# ----------------------------------------------------------------------------- pipeline wiring

class _Result:
    def __init__(self, text: str) -> None:
        self.document = SimpleNamespace(text=text)


@pytest.fixture
def pipeline() -> "ingest.DocumentIngestionPipeline":
    return ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)


def test_ppt_is_not_handed_to_docling() -> None:
    assert ".ppt" in ingest.SUPPORTED_INGESTION_EXTENSIONS
    assert ".pptx" in ingest.SUPPORTED_INGESTION_EXTENSIONS


def test_ppt_reader_reads_the_record_stream(pipeline, tmp_path: Path, fake_ole) -> None:
    path = tmp_path / "Sponsoring - Rechtsfragen.ppt"
    path.write_bytes(OLE2_MAGIC + b"\x00" * 600)
    fake_ole["PowerPoint Document"] = document(slide_list("Spenden-Sponsoring", "Verwaltungspraxis"))

    assert pipeline._read_document(str(path)) == ("Spenden-Sponsoring\n\nVerwaltungspraxis", None)


def test_ppt_reader_ocrs_the_pictures_of_a_photo_deck(pipeline, tmp_path: Path, fake_ole, monkeypatch) -> None:
    path = tmp_path / "vorlagefotos_42176.ppt"
    path.write_bytes(OLE2_MAGIC + b"\x00" * 600)
    fake_ole["PowerPoint Document"] = document(slide_list("   "))
    fake_ole["Pictures"] = atom(0xF01D, b"\x01" * 16 + b"\xff" + b"\xff\xd8photo", instance=0x46A)
    seen: list = []

    def fake_ocr(basename, pictures):
        seen.append((basename, pictures))
        return "Modelle Nr. 1946, Dr. Tschikof, 21.07.2011"

    monkeypatch.setattr(pipeline, "_ocr_picture_blobs", fake_ocr)
    assert pipeline._read_document(str(path)) == ("Modelle Nr. 1946, Dr. Tschikof, 21.07.2011", None)
    assert seen == [("vorlagefotos_42176.ppt", [("picture1.jpg", b"\xff\xd8photo")])]

    monkeypatch.setattr(pipeline, "_ocr_picture_blobs", lambda basename, pictures: "")
    content, _ = pipeline._read_document(str(path))
    assert ingest.placeholder_kind(content) == "empty", "a deck of photos without any text is empty, not an error"


def test_ppt_reader_goes_by_content(pipeline, tmp_path: Path, fake_ole, monkeypatch) -> None:
    renamed: list = []
    monkeypatch.setattr(pipeline, "_read_renamed_office_file", lambda path, suffix: renamed.append(suffix) or ("converted", None))
    package = tmp_path / "Neu.ppt"
    package.write_bytes(b"PK\x03\x04" + b"\x00" * 40)
    assert pipeline._read_document(str(package)) == ("converted", None)
    assert renamed == [".pptx"], "a .pptx package under a .ppt name goes to Docling"

    text = tmp_path / "Alt.ppt"
    text.write_bytes(b"This is not a presentation at all")
    content, _ = pipeline._read_document(str(text))
    assert ingest.placeholder_kind(content) == "error" and "not a PowerPoint file" in content

    ole = tmp_path / "Geheim.ppt"
    ole.write_bytes(OLE2_MAGIC + b"\x00" * 600)
    fake_ole["PowerPoint Document"] = document(slide_list("Geheim"))
    fake_ole["Current User"] = current_user(0, encrypted=True)
    content, _ = pipeline._read_document(str(ole))
    assert ingest.placeholder_kind(content) == "error" and "encrypted" in content


def test_picture_only_pptx_gets_the_text_of_its_pictures(pipeline, tmp_path: Path, monkeypatch) -> None:
    class _Converter:
        def convert(self, path):
            if str(path).endswith(".pptx"):
                return _Result("<!-- image -->\n\n<!-- image -->")
            return _Result("Besichtigung 12.03.2014, Wohllebengasse 4" if Path(path).read_bytes() == b"first photo" else "")

    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Converter())
    monkeypatch.setattr(ingest, "create_image_converter", lambda *a, **k: _Converter())
    monkeypatch.setattr(ingest, "prepare_image_for_docling", lambda path: (path, None))
    monkeypatch.setattr(ingest, "export_document_markdown", lambda document: document.text)
    monkeypatch.setattr(ingest, "image_noise_gate_enabled", lambda: False)
    path = tmp_path / "GraWe - Bilder.pptx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", b"<Types/>")
        z.writestr("ppt/media/image2.jpeg", b"second photo")
        z.writestr("ppt/media/image1.jpeg", b"first photo")

    assert pipeline._read_document(str(path)) == ("Besichtigung 12.03.2014, Wohllebengasse 4", None)


def test_truncated_pptx_is_salvaged_when_docling_rejects_it(pipeline, tmp_path: Path, monkeypatch) -> None:
    class _Converter:
        def convert(self, path):
            raise RuntimeError("MsPowerpointDocumentBackend could not load document")

    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Converter())
    whole = _package_bytes(
        [("ppt/slides/slide1.xml", SLIDE_XML.format(title="Opening statement").encode()), ("ppt/media/image2.jpeg", bytes(range(256)) * 400)],
        tmp_path / "whole.pptx",
    )
    cut = tmp_path / "Safety Rule for Doctors.pptx"
    cut.write_bytes(_cut_inside(whole, "ppt/media/image2.jpeg"))

    assert pipeline._read_document(str(cut)) == ("## Slide 1\n\nOpening statement\n\nLadies and gentlemen", None)

    complete = tmp_path / "Kaputt.pptx"
    complete.write_bytes(whole)
    content, _ = pipeline._read_document(str(complete))
    assert ingest.placeholder_kind(content) == "error", "a complete package Docling rejects keeps its own cause"
    assert "could not load document" in content
