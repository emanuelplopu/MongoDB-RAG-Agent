"""The ``no_chunks`` audit of 2026-11-19 (``docs/26-FORMAT_EDGE_AUDIT.md``).

Of one law firm's 10,866 open ``no_chunks`` records, 10,600 were images whose OCR found no
text, Outlook attachment stubs and other files without text: expected outcomes that were
recorded as failures and retried for nothing. The rest were readable files whose text
Docling had left below a picture, or whose only text was a heading or a page header.

What these tests cover:

* the two new outcomes ``image_no_text`` / ``empty_file`` (success stats, counted on their
  own, no ``failed_documents`` row, open rows of the file resolved);
* the readers: an image's hidden OCR lines and a plain Tesseract pass, ``<!-- image -->``
  no longer counting as text, a Word file of pictures below a heading, an HTML stub, a PDF
  whose only text is its page header;
* the chunker's Markdown fallback for a document of headings only.

Docling itself is stubbed; the document surgery, the readers' decisions and the loop run.
"""

from __future__ import annotations

import asyncio
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

pytest.importorskip("docling_core")
from docling_core.types.doc import (  # noqa: E402
    BoundingBox,
    ContentLayer,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
)

ingest = pytest.importorskip("src.ingestion.ingest")
from src.ingestion import chunker as chunker_module  # noqa: E402
from src.ingestion import scan_text_fallback as fallback  # noqa: E402
from src.ingestion.placeholders import no_text_kind  # noqa: E402
from backend.services.ingestion_counters import apply_stats_counts  # noqa: E402

A4 = (595.0, 842.0)
LETTER_LINES = [
    "Sehr geehrte Frau Magistra Fischerlehner, vielen Dank fuer Ihr Schreiben vom 3. Mai.",
    "Das ist ganz normal, weil fuer das, was wir vereinbart haben, keine Frist gilt.",
]


# --------------------------------------------------------------------------- helpers


def _prov(page_no: int = 1) -> ProvenanceItem:
    return ProvenanceItem(
        page_no=page_no,
        bbox=BoundingBox(l=0, t=0, r=A4[0], b=A4[1], coord_origin=CoordOrigin.TOPLEFT),
        charspan=(0, 0),
    )


def _picture_document(children: List[str], heading: Optional[str] = None) -> DoclingDocument:
    """What Docling returns for an image or a scanned page: one picture, the OCR lines below it."""
    doc = DoclingDocument(name="image")
    doc.add_page(page_no=1, size=Size(width=A4[0], height=A4[1]))
    if heading:
        doc.add_text(label=DocItemLabel.SECTION_HEADER, text=heading, prov=_prov())
    picture = doc.add_picture(prov=_prov())
    for line in children:
        doc.add_text(label=DocItemLabel.TEXT, text=line, parent=picture, prov=_prov())
    return doc


def _png(path: Path, size=(120, 80)) -> Path:
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", size, (250, 250, 250)).save(path, "PNG")
    return path


def _body(doc: DoclingDocument) -> List[str]:
    return [getattr(item, "text", "") for item, _ in doc.iterate_items()]


class _Converter:
    def __init__(self, document: DoclingDocument) -> None:
        self.document = document
        self.converted: List[str] = []

    def convert(self, path: str) -> Any:
        self.converted.append(str(path))
        return SimpleNamespace(document=self.document)


class _ReaderPipeline(ingest.DocumentIngestionPipeline):
    def __init__(self) -> None:
        self._initialized = True
        self.db = None
        self.documents_folders: List[str] = []


@pytest.fixture
def no_tesseract(monkeypatch: pytest.MonkeyPatch):
    """OCR settings without a settings store, no Tesseract binary unless a test installs one."""
    monkeypatch.setattr(ingest, "resolve_ocr_config", lambda *a, **k: ingest.OcrConfig())
    monkeypatch.setattr(ingest, "full_page_ocr_reader", lambda config: None)
    monkeypatch.setattr(ingest, "image_noise_gate_enabled", lambda: False)
    monkeypatch.setattr(ingest, "prepare_image_for_docling", lambda path: (path, None))


# --------------------------------------------------------------------------- placeholders and results


@pytest.mark.parametrize("content, kind", [
    ("[Image: image001.jpg - OCR extracted minimal text]", "image"),
    ("[Image: IMG_2234.JPG - no readable text]", "image"),
    ("[Document: leer.rtf - no text content]", "empty"),
    ("[Document: Fotos.docx - only pictures without text]", "empty"),
    ("[Empty email: AW_ Termin.msg]", "empty"),
    ("[Audio: diktat.dss - Olympus DSS dictation, not transcribed]", None),  # not an empty file
    ("[Video: clip.mp4 - video transcription not yet supported]", None),
    ("[Image: scan.tif - OCR not available]", None),  # an error placeholder
    ("[Error: Could not read file x.pdf]", None),
    ("Sehr geehrte Damen und Herren", None),
    ("", None),
])
def test_no_text_kind(content: str, kind: Optional[str]) -> None:
    assert no_text_kind(content) == kind


class _Pipeline(ingest.DocumentIngestionPipeline):
    """Conversion stubbed; everything after it is the real per-document path."""

    def __init__(self, content: str, document: Any = None) -> None:
        self.content = content
        self.document = document
        self.documents_folders: List[str] = []
        self.saved: List[Dict[str, Any]] = []
        self.chunker = SimpleNamespace(chunk_document_sync=lambda **kwargs: [])

    def get_executor(self):
        return None

    async def _convert_document(self, file_path: str, handle: Any = None):
        return self.content, self.document

    async def _save_to_mongodb(self, *args: Any, **kwargs: Any) -> str:
        self.saved.append(kwargs)
        return "stored"


@pytest.mark.parametrize("content, no_text", [
    ("[Image: image001.jpg - OCR extracted minimal text]", "image"),
    ("[Empty email: leer.msg]", "empty"),
    ("[Document: Vorlage.dotx - no text content]", "empty"),
    ("[Audio: diktat.dss - Olympus DSS dictation, not transcribed]", None),
    ("", "empty"),  # an empty attachment stub: no placeholder, no text
    ("<!-- image -->\n\n<!-- image -->", "empty"),  # pictures only
    ("## 26. OKTOBER 2015", None),  # text the (stubbed) chunker made nothing of: no_chunks
])
def test_ingest_single_document_reports_why_there_is_no_chunk(tmp_path: Path, content: str, no_text: Optional[str]) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"x")
    pipe = _Pipeline(content)
    result = asyncio.run(pipe._ingest_single_document(str(path), "hash"))
    assert result.document_id == "" and result.chunks_created == 0
    assert result.errors == ["No chunks created"]
    assert result.no_text == no_text
    assert pipe.saved == []


# --------------------------------------------------------------------------- the loop and the counters


class _Loop(_Pipeline):
    def __init__(self, content: str) -> None:
        super().__init__(content)
        self._initialized = True
        self.db = None
        self.stats: List[Any] = []

    async def _save_ingestion_stats(self, file_stats: List[Any]) -> bool:
        self.stats.extend(file_stats)
        return True


@pytest.mark.parametrize("name, content, outcome, counter", [
    ("image001.jpg", "[Image: image001.jpg - OCR extracted minimal text]", "image_no_text", "images_without_text"),
    ("ATT00001.htm", "", "empty_file", "empty_files"),
    ("leer.msg", "[Empty email: leer.msg]", "empty_file", "empty_files"),
])
def test_concurrent_loop_records_a_file_without_text_as_an_outcome_not_a_failure(
    tmp_path: Path, name: str, content: str, outcome: str, counter: str
) -> None:
    path = tmp_path / name
    path.write_bytes(b"GIF89a" if name.endswith(".jpg") else b"")
    pipe = _Loop(content)

    results, _ = asyncio.run(pipe._ingest_documents_concurrent(
        document_files=[str(path)], existing_hashes={}, incremental=False, max_concurrent=2,
    ))

    assert results[0].document_id == "" and results[0].no_text is not None
    stat = pipe.stats[0]
    assert stat.success is True, "read fine, nothing to index: not a failure"
    assert stat.error_type == outcome and stat.classification == outcome
    assert stat.chunks_created == 0
    # the counters: the outcome has a field of its own and does not count as failed
    job_state: Dict[str, Any] = {}
    apply_stats_counts(job_state, {outcome: 1, "no_chunks": 2, "success": 5})
    assert job_state[counter] == 1
    assert job_state["no_chunks_files"] == 2 and job_state["failed_files"] == 2


def test_a_file_with_text_but_no_chunk_is_still_the_no_chunks_failure(tmp_path: Path) -> None:
    path = tmp_path / "Beilage 1.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    pipe = _Loop("## 26. OKTOBER 2015\n\nhttp://www.example.org/seite")  # the stubbed chunker makes nothing of it
    asyncio.run(pipe._ingest_documents_concurrent(
        document_files=[str(path)], existing_hashes={}, incremental=False, max_concurrent=1,
    ))
    stat = pipe.stats[0]
    assert stat.success is False and stat.error_type == "no_chunks"


def test_classification_and_counted_outcomes() -> None:
    assert ingest.map_error_type_to_classification("image_no_text", 0) == "image_no_text"
    assert ingest.map_error_type_to_classification("empty_file", 0) == "empty_file"
    assert ingest.map_error_type_to_classification("no_chunks", 0) == "no_chunks"
    assert set(ingest.NO_TEXT_OUTCOMES) <= set(ingest.IngestionStatsRecorder.COUNTED)
    from backend.models.schemas import FileClassification

    assert {FileClassification.IMAGE_NO_TEXT.value, FileClassification.EMPTY_FILE.value} == set(ingest.NO_TEXT_OUTCOMES)


class _FailedCollection:
    def __init__(self) -> None:
        self.upserts: List[Any] = []
        self.resolutions: List[Any] = []

    async def update_one(self, query: dict, update: dict, upsert: bool = False) -> None:
        self.upserts.append((query, update, upsert))

    async def update_many(self, query: dict, update: dict) -> None:
        self.resolutions.append((query, update))


class _StatsCollection:
    def __init__(self) -> None:
        self.inserted: List[dict] = []

    async def insert_many(self, docs: List[dict]) -> None:
        self.inserted.extend(docs)

    async def replace_one(self, *a: Any, **k: Any) -> None:
        pass


def _stat(path: str, error_type: Optional[str], success: bool) -> Any:
    now = datetime.now()
    return ingest.IngestionFileStats(
        file_path=path, file_name=path.rsplit("/", 1)[-1], file_size_bytes=10, started_at=now, completed_at=now,
        processing_time_ms=1.0, chunks_created=0, success=success, error_type=error_type, error_message="m",
        timeout_seconds=10.0, profile_key="p", job_id="job-1", classification=error_type or "normal",
    )


async def test_save_ingestion_stats_resolves_the_open_rows_of_a_file_without_text() -> None:
    pipe = _ReaderPipeline()
    stats, failed = _StatsCollection(), _FailedCollection()
    pipe.db = {"ingestion_stats": stats, "failed_documents": failed}

    await pipe._save_ingestion_stats([
        _stat("/m/Fotos/IMG_1.jpg", "image_no_text", True),
        _stat("/m/Attachements/ATT00001.htm", "empty_file", True),
        _stat("/m/Akten/Beilage 1.pdf", "no_chunks", False),
        _stat("/m/Akten/Brief.docx", None, True),
    ])

    assert [d["file_path"] for d in stats.inserted] == [
        "/m/Fotos/IMG_1.jpg", "/m/Attachements/ATT00001.htm", "/m/Akten/Beilage 1.pdf", "/m/Akten/Brief.docx",
    ]
    assert [q["file_path"] for q, _, _ in failed.upserts] == ["/m/Akten/Beilage 1.pdf"], "only the failure gets a row"
    by_resolution = {u["$set"]["resolution"]: q["file_path"]["$in"] for q, u in failed.resolutions}
    assert by_resolution == {
        "a later attempt indexed the file": ["/m/Akten/Brief.docx"],
        "a later attempt found no text in the file": ["/m/Attachements/ATT00001.htm", "/m/Fotos/IMG_1.jpg"],
    }


# --------------------------------------------------------------------------- the scan-text fallback on images


def test_recover_image_text_copies_the_lines_below_the_picture(tmp_path: Path) -> None:
    doc = _picture_document(LETTER_LINES)
    assert doc.export_to_markdown().strip() == "<!-- image -->"
    calls: List[Any] = []

    recovery = fallback.recover_image_text(doc, str(_png(tmp_path / "letter.png")), ocr_page=lambda image: calls.append(1) or "x")

    assert recovery is not None and recovery.fallback == "picture_children"
    assert calls == [], "enough text below the picture: no Tesseract pass"
    assert all(line in ingest.export_document_markdown(doc) for line in LETTER_LINES)


def test_recover_image_text_runs_tesseract_when_the_document_stays_short(tmp_path: Path) -> None:
    doc = _picture_document(["07:29"])  # a timestamp below a screenshot of a chat
    sizes: List[Any] = []

    def tesseract(image: Any) -> str:
        sizes.append(image.size)
        return "http://www.proktologie-kiel.de/\nleistungsspektrum/krankheitsbilder/\n\n26. OKTOBER 2015"

    recovery = fallback.recover_image_text(doc, str(_png(tmp_path / "chat.png", (300, 200))), ocr_page=tesseract)

    assert recovery is not None and recovery.fallback == "picture_children+full_page"
    assert sizes == [(300, 200)]  # read at its own size, text found at once
    assert _body(doc)[1:] == ["07:29", "http://www.proktologie-kiel.de/ leistungsspektrum/krankheitsbilder/", "26. OKTOBER 2015"]


def test_recover_image_text_tries_lower_scales_and_caps_the_bitmap(tmp_path: Path) -> None:
    doc = _picture_document([])
    sizes: List[Any] = []

    def tesseract(image: Any) -> str:
        sizes.append(image.size)
        return "" if len(sizes) < 3 else "Vollmacht und Honorarvereinbarung, Wien am 17.11.2021, Paula Djordjevic"

    recovery = fallback.recover_image_text(
        doc, str(_png(tmp_path / "scan.png", (400, 200))), ocr_page=tesseract, max_pixels=20_000,
    )

    assert recovery is not None and recovery.fallback == "full_page"
    assert sizes[0] == (200, 100), "capped at max_pixels"
    assert sizes[1:] == [(133, 66), (100, 50)]  # 2/3 and 1/2 of that
    assert recovery.scales == {1: 0.5} and "lower OCR scale" in recovery.describe()
    assert _body(doc)[-1].startswith("Vollmacht und Honorarvereinbarung")


def test_tesseract_pass_does_not_repeat_what_the_page_already_shows(tmp_path: Path) -> None:
    """A web button's label (children "ABFRAGE WIEN") read again by Tesseract stays one text."""
    doc = _picture_document(["ABFRAGE WIEN"])
    recovery = fallback.recover_image_text(
        doc, str(_png(tmp_path / "abfrage.gif")), ocr_page=lambda image: "ABFRAGE  WIEN\n\nabfrage wien",
    )
    assert recovery is not None and recovery.fallback == "picture_children"
    assert _body(doc) == ["", "ABFRAGE WIEN"]
    # a page with a heading: the pass adds the lines below it, not the heading again
    doc = _picture_document([], heading="26. OKTOBER 2015")
    fallback.recover_image_text(doc, str(_png(tmp_path / "chat.png")), ocr_page=lambda image: "26. OKTOBER 2015\n\nhttp://www.proktologie-kiel.de/")
    assert _body(doc) == ["26. OKTOBER 2015", "", "http://www.proktologie-kiel.de/"]


def test_recover_image_text_finds_nothing_in_a_photo(tmp_path: Path) -> None:
    doc = _picture_document([])
    assert fallback.recover_image_text(doc, str(_png(tmp_path / "photo.png")), ocr_page=lambda image: "  \n ") is None
    assert _body(doc) == [""]  # the picture, untouched


def test_full_page_pass_of_a_pdf_tries_lower_scales(tmp_path: Path) -> None:
    pdfium = pytest.importorskip("pypdfium2")  # noqa: F841
    Image = pytest.importorskip("PIL.Image")
    pdf = tmp_path / "Vollmacht.pdf"
    Image.new("RGB", (200, 280), (245, 245, 245)).save(pdf, "PDF")
    doc = DoclingDocument(name="empty")
    sizes: List[Any] = []

    def tesseract(image: Any) -> str:
        sizes.append(image.size)
        return "" if len(sizes) == 1 else "Vollmacht und Honorarvereinbarung vom 17.11.2021 fuer Frau Paula Djordjevic"

    recovery = fallback.recover_scan_text(doc, str(pdf), ocr_page=tesseract, scale=3.0)

    assert recovery is not None and recovery.pages == {1: "full_page"}
    assert sizes == [(600, 840), (400, 560)]  # scale 3, then 2
    assert recovery.scales == {1: 2.0}


# --------------------------------------------------------------------------- the readers


def test_image_reader_recovers_hidden_ocr_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_tesseract: Any) -> None:
    image = _png(tmp_path / "IMG_5637.jpeg")
    converter = _Converter(_picture_document(LETTER_LINES))
    monkeypatch.setattr(ingest, "create_image_converter", lambda *a, **k: converter)
    pipe = _ReaderPipeline()

    with ingest.conversion_notes() as notes:
        content, document = pipe._read_document(str(image))

    assert all(line in content for line in LETTER_LINES) and document is converter.document
    assert notes == {"ocr_fallback": "picture_children"}


def test_image_reader_uses_a_plain_tesseract_pass_when_docling_finds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_tesseract: Any
) -> None:
    image = _png(tmp_path / "Unbenannt2.PNG")
    monkeypatch.setattr(ingest, "create_image_converter", lambda *a, **k: _Converter(_picture_document([])))
    monkeypatch.setattr(ingest, "full_page_ocr_reader", lambda config: (lambda img: "Neue Nachricht von Hello: confirm received, empty box"))
    pipe = _ReaderPipeline()

    with ingest.conversion_notes() as notes:
        content, document = pipe._read_document(str(image))

    assert "Neue Nachricht von Hello" in content and document is not None
    assert notes == {"ocr_fallback": "full_page"}


def test_image_without_text_is_a_placeholder_not_a_picture_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_tesseract: Any
) -> None:
    """``<!-- image -->`` (14 characters) passed the former ``> 10`` check and became content."""
    image = _png(tmp_path / "DSC_21129.JPG")
    monkeypatch.setattr(ingest, "create_image_converter", lambda *a, **k: _Converter(_picture_document([])))
    pipe = _ReaderPipeline()

    content, document = pipe._read_document(str(image))

    assert content == "[Image: DSC_21129.JPG - OCR extracted minimal text]" and document is None
    assert no_text_kind(content) == "image"


def test_image_reader_ignores_a_heading_made_of_the_first_ocr_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_tesseract: Any
) -> None:
    image = _png(tmp_path / "image001_V18.jpg")
    doc = _picture_document(["GENERALPLANER", "Wien, Rotenturmstrasse 12", "Telefon 01 / 512 34 56"], heading="PROJEKTENTWICKLER")
    monkeypatch.setattr(ingest, "create_image_converter", lambda *a, **k: _Converter(doc))
    pipe = _ReaderPipeline()

    content, _ = pipe._read_document(str(image))

    assert "PROJEKTENTWICKLER" in content and "Rotenturmstrasse" in content


def test_html_stub_is_an_empty_file_and_visible_text_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Converter(DoclingDocument(name="empty")))
    pipe = _ReaderPipeline()
    stub = tmp_path / "ATT00001.htm"
    stub.write_bytes(b'<html><body dir="auto" role="textbox" aria-label="Inhalt der E-Mail"><head><meta charset="utf-8"></head><br></body></html>')
    page = tmp_path / "2.html"
    page.write_bytes(
        b"<!DOCTYPE html><html><head><title>Programmatic Advertising - Wasserfall</title>"
        b"<script>var x = 'nicht sichtbar';</script><style>.a{}</style></head>"
        b"<body><!-- Kommentar --><div>Erkl&auml;rung der Auktion</div></body></html>"
    )

    assert pipe._read_document(str(stub)) == ("[Document: ATT00001.htm - no text content]", None)
    with ingest.conversion_notes() as notes:
        content, document = pipe._read_document(str(page))
    assert content == "Programmatic Advertising - Wasserfall Erklärung der Auktion" and document is None
    assert notes == {"html_fallback": "visible_text"}


def _docx(path: Path, pictures: int) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
        for n in range(1, pictures + 1):
            z.writestr(f"word/media/image{n}.jpeg", b"picture bytes")
    return path


def test_word_file_of_pictures_below_a_heading_gets_the_pictures_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = DoclingDocument(name="album")
    doc.add_text(label=DocItemLabel.SECTION_HEADER, text="Lichtbildkonvolut vom 4.11.2021", prov=None)
    doc.add_picture()
    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Converter(doc))
    monkeypatch.setattr(ingest.DocumentIngestionPipeline, "_ocr_office_pictures", lambda self, path: "Kennzeichen W-2057KP, Schaden am Heck")
    pipe = _ReaderPipeline()

    content, document = pipe._read_document(str(_docx(tmp_path / "Fotos.docx", 1)))

    assert content == "## Lichtbildkonvolut vom 4.11.2021\n\nKennzeichen W-2057KP, Schaden am Heck" and document is None


def test_word_file_of_pictures_without_text_is_an_empty_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = DoclingDocument(name="pictures")
    doc.add_picture()
    doc.add_picture()
    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Converter(doc))
    monkeypatch.setattr(ingest.DocumentIngestionPipeline, "_ocr_office_pictures", lambda self, path: "")
    pipe = _ReaderPipeline()

    assert pipe._read_document(str(_docx(tmp_path / "Dok1.docx", 2))) == ("[Document: Dok1.docx - only pictures without text]", None)
    assert pipe._read_document(str(_docx(tmp_path / "leer.docx", 0))) == ("[Document: leer.docx - no text content]", None)


def test_word_file_with_a_heading_only_keeps_the_heading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = DoclingDocument(name="heading")
    doc.add_text(label=DocItemLabel.SECTION_HEADER, text="Stellungnahme Vitamin B12 vom 10.08.2018", prov=None)
    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Converter(doc))
    monkeypatch.setattr(ingest.DocumentIngestionPipeline, "_ocr_office_pictures", lambda self, path: "")
    pipe = _ReaderPipeline()

    content, document = pipe._read_document(str(_docx(tmp_path / "Stellungnahme.docx", 0)))

    assert content.strip() == "## Stellungnahme Vitamin B12 vom 10.08.2018" and document is doc  # the chunker's fallback makes the chunk


def test_pdf_whose_only_text_is_the_page_header_keeps_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_tesseract: Any) -> None:
    pdf = tmp_path / "Beilage 41.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    doc = DoclingDocument(name="exhibit")
    doc.add_page(page_no=1, size=Size(width=A4[0], height=A4[1]))
    doc.add_text(label=DocItemLabel.PAGE_HEADER, text="Hinterlegt am 07.12.2018 - 15:20   ./41", prov=_prov(), content_layer=ContentLayer.FURNITURE)
    doc.add_picture(prov=_prov())
    monkeypatch.delenv("INGEST_PAGE_FURNITURE", raising=False)
    monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: _Converter(doc))
    pipe = _ReaderPipeline()

    with ingest.conversion_notes() as notes:
        content, document = pipe._read_document(str(pdf))

    assert "Hinterlegt am 07.12.2018" in content and document is doc
    assert notes == {"furniture_text": True}


@pytest.mark.parametrize("markdown, has_text, has_prose", [
    ("<!-- image -->\n\n<!-- image -->", False, False),
    ("<!-- image -->", False, False),
    ("## Lichtbildkonvolut vom 4.11.2021\n\n<!-- image -->", True, False),
    ("## 26. OKTOBER 2015\n\n<!-- image -->\n\n## 28. OKTOBER 2015", True, False),
    ("Sehr geehrte Frau Muster, anbei die Honorarnote.", True, True),
    ("## Titel\n\nSehr geehrte Frau Muster, anbei die Honorarnote.", True, True),
])
def test_markdown_text_and_prose(markdown: str, has_text: bool, has_prose: bool) -> None:
    assert ingest.markdown_has_text(markdown) is has_text
    assert ingest.markdown_has_prose(markdown) is has_prose


def test_html_visible_text(tmp_path: Path) -> None:
    page = tmp_path / "page.htm"
    page.write_bytes("<html><head><title>Inhalt</title><style>p{}</style></head><body><p>Erster&nbsp;Absatz</p>\r\n<script>x()</script><noscript>nein</noscript> Ende</body></html>".encode("cp1252"))
    assert ingest.html_visible_text(str(page)) == "Inhalt Erster Absatz Ende"  # the entity, as a plain space
    assert ingest.html_visible_text(str(tmp_path / "missing.htm")) == ""


# --------------------------------------------------------------------------- the chunker


def _make_chunker() -> Any:
    try:
        return chunker_module.DoclingHybridChunker(chunker_module.ChunkingConfig())
    except Exception as exc:  # noqa: BLE001 - no tokenizer offline
        pytest.skip(f"chunker tokenizer unavailable: {exc}")


def test_markdown_without_images() -> None:
    assert chunker_module.markdown_without_images("<!-- image -->\n\nText\n\n<!-- image -->").strip() == "Text"
    assert chunker_module.markdown_without_images(None) == ""


def test_chunker_falls_back_to_the_markdown_for_a_document_of_headings() -> None:
    chunker = _make_chunker()
    doc = DoclingDocument(name="album")
    doc.add_text(label=DocItemLabel.SECTION_HEADER, text="26. OKTOBER 2015", prov=None)
    doc.add_picture()
    doc.add_text(label=DocItemLabel.SECTION_HEADER, text="28. OKTOBER 2015", prov=None)
    markdown = ingest.export_document_markdown(doc)
    assert "<!-- image -->" in markdown

    chunks = chunker.chunk_document_sync(content=markdown, title="t", source="s", docling_doc=doc)

    assert len(chunks) == 1 and chunks[0].metadata["chunk_method"] == "simple_fallback"
    assert "26. OKTOBER 2015" in chunks[0].content and "28. OKTOBER 2015" in chunks[0].content
    assert "<!-- image -->" not in chunks[0].content


def test_chunker_makes_nothing_of_pictures_only() -> None:
    chunker = _make_chunker()
    doc = DoclingDocument(name="pictures")
    doc.add_picture()
    assert chunker.chunk_document_sync(content="<!-- image -->", title="t", source="s", docling_doc=doc) == []
    assert chunker.chunk_document_sync(content="<!-- image -->\n\n<!-- image -->", title="t", source="s", docling_doc=None) == []
