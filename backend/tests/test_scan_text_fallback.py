"""Tests for the scan-text fallback (src/ingestion/scan_text_fallback.py, finding F35).

Docling 2.142 returns a scanned page as one ``PictureItem`` with the OCR lines as its
*children*: ``export_to_markdown()`` gives ``<!-- image -->`` and the HybridChunker produces
no chunk, so a readable scan was recorded as ``image_only_pdf: OCR found no text``. A second
variant returns no items at all for a page plain Tesseract reads. The fallback puts that text
back into the document body after the conversion, and only on pages without body text.

The synthetic documents here are the shape of the two sample files of the prompt-test log
(``Aufklärungsbogen Aesthetic.Clinic vom 16.6.2017.pdf``: one picture with 1,568 characters of
children; ``Direktzustellung_2009_03_24_4.pdf``: no items). Tesseract itself is mocked; the
rendering, the document surgery and the chunker are real.
"""

from __future__ import annotations

import asyncio
import io
import subprocess
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

pytest.importorskip("docling_core")
from docling_core.types.doc import (  # noqa: E402
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
)

ingest = pytest.importorskip("src.ingestion.ingest")
from src.ingestion import scan_text_fallback as fallback  # noqa: E402
from src.ingestion.ocr_repair import repair_docling_document  # noqa: E402

A4 = (595.0, 842.0)
HIDDEN_LINES = [
    "Aufklaerungsbogen Aesthetic.Clinic vom 16.6.2017",
    "Die Patientin wurde ueber Risiken und Nebenwirkungen gemaess $ 49 ÄrzteG aufgeklaert.",
    "Unterschrift der Patientin",
]
FULL_PAGE_TEXT = (
    "Sparkasse Oberoesterreich\nDirektzustellung vom 24.03.2009\n\n"
    "Sehr geehrte Damen und Herren,\nwir uebermitteln den Beschluss des Bezirksgerichts.\n\n"
)
FULL_PAGE_PARAGRAPHS = [
    "Sparkasse Oberoesterreich Direktzustellung vom 24.03.2009",
    "Sehr geehrte Damen und Herren, wir uebermitteln den Beschluss des Bezirksgerichts.",
]


# --------------------------------------------------------------------------- helpers


def _prov(page_no: int, box: Tuple[float, float, float, float] = (0, 0, A4[0], A4[1])) -> ProvenanceItem:
    left, top, right, bottom = box
    return ProvenanceItem(
        page_no=page_no,
        bbox=BoundingBox(l=left, t=top, r=right, b=bottom, coord_origin=CoordOrigin.TOPLEFT),
        charspan=(0, 0),
    )


def _add_hidden_picture(doc: DoclingDocument, page_no: int, lines: List[str]) -> Any:
    """A page as Docling 2.142 returns a scan: one picture, the OCR lines as its children."""
    if page_no not in doc.pages:
        doc.add_page(page_no=page_no, size=Size(width=A4[0], height=A4[1]))
    picture = doc.add_picture(prov=_prov(page_no))
    for index, line in enumerate(lines):
        doc.add_text(
            label=DocItemLabel.TEXT, text=line, parent=picture,
            prov=_prov(page_no, (50, 50 + 20 * index, 500, 70 + 20 * index)),
        )
    return picture


def _picture_only_document(lines: List[str] = HIDDEN_LINES) -> DoclingDocument:
    doc = DoclingDocument(name="scan")
    _add_hidden_picture(doc, 1, lines)
    return doc


def _body(doc: DoclingDocument) -> List[Tuple[str, str]]:
    """What the chunker sees: the body items in reading order (pictures not traversed)."""
    return [(type(item).__name__, getattr(item, "text", "")) for item, _ in doc.iterate_items()]


def _image_only_pdf(path: Path, size: Tuple[int, int] = (200, 280), pages: int = 1, shade: int = 250) -> None:
    """A PDF of ``pages`` raster images and no text layer (what a scanner produces)."""
    Image = pytest.importorskip("PIL.Image")
    frames = [Image.new("RGB", size, (shade - 5 * i, shade - 5 * i, shade - 5 * i)) for i in range(pages)]
    frames[0].save(path, "PDF", save_all=True, append_images=frames[1:])


def _text_pdf(text: str) -> bytes:
    """A minimal one-page PDF with a text layer and no image object."""
    stream = f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 400 200]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(stream)).encode() + b">>\nstream\n" + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref_at}\n%%EOF\n".encode())
    return out.getvalue()


@pytest.fixture(scope="module")
def chunker() -> Any:
    """The pipeline's HybridChunker (needs the MiniLM tokenizer in the local Hugging Face cache)."""
    from src.ingestion.chunker import ChunkingConfig, DoclingHybridChunker

    try:
        return DoclingHybridChunker(ChunkingConfig())
    except Exception as exc:  # noqa: BLE001 - no tokenizer offline
        pytest.skip(f"chunker tokenizer unavailable: {exc}")


def _chunks(chunker: Any, doc: DoclingDocument) -> List[Any]:
    content = ingest.export_document_markdown(doc)
    return asyncio.run(chunker.chunk_document(content=content, title="t", source="s", metadata={}, docling_doc=doc))


class _ReaderPipeline(ingest.DocumentIngestionPipeline):
    """A pipeline instance for ``_read_document``; nothing behind it."""

    def __init__(self) -> None:
        self._initialized = True
        self.db = None
        self.documents_folders: List[str] = []
        self.stats: List[Any] = []

    async def _save_ingestion_stats(self, file_stats: List[Any]) -> bool:
        self.stats.extend(file_stats)
        return True


class _Converter:
    """Stands in for Docling's DocumentConverter: returns a prepared document."""

    def __init__(self, document: DoclingDocument) -> None:
        self.document = document
        self.converted: List[str] = []

    def convert(self, path: str) -> Any:
        self.converted.append(path)
        converter = self

        class _Result:
            document = converter.document

        return _Result()


@pytest.fixture
def fake_converter(monkeypatch: pytest.MonkeyPatch):
    """Replace Docling and the OCR settings in the PDF branch of ``_read_document``."""

    def install(document: DoclingDocument, page_ocr: Any = None) -> _Converter:
        converter = _Converter(document)
        monkeypatch.setattr(ingest, "create_document_converter", lambda *a, **k: converter)
        monkeypatch.setattr(ingest, "resolve_ocr_config", lambda *a, **k: ingest.OcrConfig())
        monkeypatch.setattr(ingest, "full_page_ocr_reader", lambda config: page_ocr)
        return converter

    return install


# --------------------------------------------------------------------------- variant A: picture children


def test_picture_children_text_is_copied_into_the_body_and_chunked(chunker: Any) -> None:
    doc = _picture_only_document()
    assert _body(doc) == [("PictureItem", "")]
    assert doc.export_to_markdown().strip() == "<!-- image -->"  # the F35 symptom
    assert _chunks(chunker, doc) == []

    recovery = fallback.recover_scan_text(doc, pdf_path=None)

    assert recovery is not None
    assert recovery.fallback == "picture_children"
    assert recovery.pages == {1: "picture_children"} and recovery.pictures == 1
    assert recovery.characters == sum(len(line) for line in HIDDEN_LINES)
    items = _body(doc)
    assert items[0] == ("PictureItem", "")
    assert [text for _, text in items[1:]] == HIDDEN_LINES  # right after the picture, in order
    markdown = ingest.export_document_markdown(doc)
    assert all(line in markdown for line in HIDDEN_LINES)
    chunks = _chunks(chunker, doc)
    assert chunks and any("Aufklaerungsbogen" in chunk.content for chunk in chunks)
    # the § repair is a separate step after the conversion; it reaches the recovered items
    assert repair_docling_document(doc) >= 1
    assert any("§ 49 ÄrzteG" in text for _, text in _body(doc))


def test_picture_children_are_copied_once_per_picture_and_keep_their_page() -> None:
    doc = _picture_only_document()
    fallback.recover_scan_text(doc)
    added = [item for item, _ in doc.iterate_items() if type(item).__name__ == "TextItem"]
    assert [item.prov[0].page_no for item in added] == [1, 1, 1]
    assert added[0].prov[0].bbox.t == 50  # the child's own box, not the page box
    # running it again finds body text and changes nothing
    assert fallback.recover_scan_text(doc) is None
    assert len(doc.texts) == 2 * len(HIDDEN_LINES)


def test_captions_do_not_count_as_hidden_text() -> None:
    doc = DoclingDocument(name="cap")
    doc.add_page(page_no=1, size=Size(width=A4[0], height=A4[1]))
    picture = doc.add_picture(prov=_prov(1))
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="Abbildung 1", parent=picture, prov=_prov(1))
    picture.captions.append(caption.get_ref())

    assert fallback.recover_scan_text(doc) is None
    assert len(doc.texts) == 1


# --------------------------------------------------------------------------- the normal path


def test_pages_with_body_text_are_untouched(tmp_path: Path) -> None:
    doc = _picture_only_document()
    doc.add_text(label=DocItemLabel.TEXT, text="Native text layer of the same page", prov=_prov(1))
    before_markdown = doc.export_to_markdown()
    before_texts = len(doc.texts)
    ocr_calls: List[Any] = []
    missing_pdf = tmp_path / "never-opened.pdf"  # a page with body text never opens pdfium

    outcome = fallback.recover_scan_text(doc, str(missing_pdf), ocr_page=lambda image: ocr_calls.append(image) or "x")

    assert outcome is None
    assert len(doc.texts) == before_texts
    assert doc.export_to_markdown() == before_markdown
    assert ocr_calls == []


def test_a_table_is_body_text_too() -> None:
    from docling_core.types.doc import TableCell, TableData

    doc = _picture_only_document()
    cell = TableCell(text="Betrag", start_row_offset_idx=0, end_row_offset_idx=1, start_col_offset_idx=0, end_col_offset_idx=1)
    doc.add_table(data=TableData(num_rows=1, num_cols=1, table_cells=[cell]), prov=_prov(1))
    assert fallback.recover_scan_text(doc) is None


def test_blank_page_without_an_image_is_not_ocred(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "blank.pdf"
    pdf.write_bytes(_text_pdf(""))  # no text, no image object: nothing a Tesseract pass could read
    doc = DoclingDocument(name="blank")
    calls: List[int] = []

    assert fallback.recover_scan_text(doc, str(pdf), ocr_page=lambda image: calls.append(1) or "noise") is None
    assert calls == [] and not doc.texts


def test_page_with_a_native_text_layer_is_not_ocred(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "text.pdf"
    pdf.write_bytes(_text_pdf("Beschluss des Landesgerichts fuer Zivilrechtssachen Wien ueber die Exekution"))
    doc = DoclingDocument(name="odd")  # Docling returned nothing, but the page is no scan
    calls: List[int] = []

    assert fallback.recover_scan_text(doc, str(pdf), ocr_page=lambda image: calls.append(1) or "noise") is None
    assert calls == []


# --------------------------------------------------------------------------- variant B: full page


def test_full_page_ocr_reads_an_empty_scan_page(tmp_path: Path, chunker: Any) -> None:
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "Direktzustellung.pdf"
    _image_only_pdf(pdf, size=(200, 280))
    doc = DoclingDocument(name="empty")  # no pages, no items: what Docling returned for the file
    rendered: List[Tuple[int, int]] = []

    def tesseract(image: Any) -> str:
        rendered.append(image.size)
        return FULL_PAGE_TEXT

    recovery = fallback.recover_scan_text(doc, str(pdf), ocr_page=tesseract, scale=3.0)

    assert recovery is not None
    assert recovery.fallback == "full_page" and recovery.pages == {1: "full_page"}
    assert rendered == [(600, 840)]  # the page rendered at the OCR scale (200 x 280 pt at 3 px/pt)
    assert [text for _, text in _body(doc)] == FULL_PAGE_PARAGRAPHS
    assert recovery.characters == sum(len(p) for p in FULL_PAGE_PARAGRAPHS)
    assert 1 in doc.pages and doc.pages[1].size.width == 200
    assert all(item.prov[0].page_no == 1 for item in doc.texts)
    chunks = _chunks(chunker, doc)
    assert chunks and "Direktzustellung" in chunks[0].content


def test_full_page_ocr_that_finds_nothing_leaves_the_document_empty(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "ccitt.pdf"
    _image_only_pdf(pdf)
    doc = DoclingDocument(name="empty")

    assert fallback.recover_scan_text(doc, str(pdf), ocr_page=lambda image: "  \n\n ") is None
    assert not doc.texts and not doc.pages  # still the image_only_pdf record of PR #29


def test_paragraphs_fold_lines_and_drop_blank_blocks() -> None:
    assert fallback._paragraphs("a\nb\n\n\n c  \n\n") == ["a b", "c"]
    assert fallback._paragraphs("") == []


def test_mixed_document_keeps_page_order(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "mixed.pdf"
    _image_only_pdf(pdf, pages=3)
    doc = DoclingDocument(name="mixed")
    for page_no in (1, 2, 3):
        doc.add_page(page_no=page_no, size=Size(width=200, height=280))
    doc.add_text(label=DocItemLabel.TEXT, text="Seite eins mit Text", prov=_prov(1))  # page 1: normal
    _add_hidden_picture(doc, 2, ["Seite zwei im Bild"])  # page 2: variant A
    # page 3: nothing at all -> variant B

    recovery = fallback.recover_scan_text(doc, str(pdf), ocr_page=lambda image: "Seite drei\nper Tesseract")

    assert recovery is not None
    assert recovery.pages == {2: "picture_children", 3: "full_page"}
    assert recovery.fallback == "picture_children+full_page"
    assert _body(doc) == [
        ("TextItem", "Seite eins mit Text"),
        ("PictureItem", ""),
        ("TextItem", "Seite zwei im Bild"),
        ("TextItem", "Seite drei per Tesseract"),
    ]


def test_full_page_text_of_an_early_page_is_inserted_before_later_pages(tmp_path: Path) -> None:
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "early.pdf"
    _image_only_pdf(pdf, pages=2)
    doc = DoclingDocument(name="early")
    for page_no in (1, 2):
        doc.add_page(page_no=page_no, size=Size(width=200, height=280))
    doc.add_text(label=DocItemLabel.TEXT, text="Seite zwei", prov=_prov(2))  # only page 2 has text

    recovery = fallback.recover_scan_text(doc, str(pdf), ocr_page=lambda image: "Seite eins")

    assert recovery is not None and recovery.pages == {1: "full_page"}
    assert [text for _, text in _body(doc)] == ["Seite eins", "Seite zwei"]


# --------------------------------------------------------------------------- the conversion path


def test_read_document_recovers_hidden_text_and_notes_the_fallback(tmp_path: Path, fake_converter: Any) -> None:
    pdf = tmp_path / "Aufklaerungsbogen.pdf"
    _image_only_pdf(pdf)
    converter = fake_converter(_picture_only_document())
    pipe = _ReaderPipeline()

    with ingest.conversion_notes() as notes:
        content, document = pipe._read_document(str(pdf))

    assert converter.converted == [str(pdf)]
    assert "Aufklaerungsbogen" in content and "$ 49 ÄrzteG" in content  # the § repair runs later
    assert document is converter.document
    assert notes == {"ocr_fallback": "picture_children"}


def test_read_document_runs_the_full_page_pass_for_an_empty_conversion(tmp_path: Path, fake_converter: Any) -> None:
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "Direktzustellung.pdf"
    _image_only_pdf(pdf)
    converter = fake_converter(DoclingDocument(name="empty"), page_ocr=lambda image: FULL_PAGE_TEXT)
    pipe = _ReaderPipeline()

    with ingest.conversion_notes() as notes:
        content, document = pipe._read_document(str(pdf))

    assert "Direktzustellung vom 24.03.2009" in content
    assert notes == {"ocr_fallback": "full_page"}
    assert [text for _, text in _body(document)] == FULL_PAGE_PARAGRAPHS


def test_read_document_leaves_a_pdf_with_text_alone(tmp_path: Path, fake_converter: Any) -> None:
    pdf = tmp_path / "text.pdf"
    _image_only_pdf(pdf)
    doc = _picture_only_document()
    doc.add_text(label=DocItemLabel.TEXT, text="Native text of the page", prov=_prov(1))
    expected = ingest.export_document_markdown(doc)
    texts_before = len(doc.texts)
    fake_converter(doc, page_ocr=lambda image: pytest.fail("Tesseract must not run on a page with text"))
    pipe = _ReaderPipeline()

    with ingest.conversion_notes() as notes:
        content, document = pipe._read_document(str(pdf))

    assert content == expected and document is doc and len(doc.texts) == texts_before
    assert notes == {}


def test_read_document_does_not_touch_other_formats(tmp_path: Path, fake_converter: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    docx = tmp_path / "letter.docx"
    docx.write_bytes(b"not really a docx")
    fake_converter(_picture_only_document())
    calls: List[Any] = []
    monkeypatch.setattr(fallback, "recover_scan_text", lambda *a, **k: calls.append(a) or None)
    pipe = _ReaderPipeline()

    with ingest.conversion_notes() as notes:
        pipe._read_document(str(docx))

    assert calls == [] and notes == {}


def test_read_document_survives_a_failing_fallback(
    tmp_path: Path, fake_converter: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pdf = tmp_path / "broken.pdf"
    _image_only_pdf(pdf)
    fake_converter(_picture_only_document())

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("pdfium exploded")

    monkeypatch.setattr(fallback, "recover_scan_text", boom)
    pipe = _ReaderPipeline()

    with caplog.at_level("WARNING"), ingest.conversion_notes() as notes:
        content, document = pipe._read_document(str(pdf))

    assert content.strip() == "<!-- image -->" and document is not None  # Docling's result stands
    assert notes == {}
    assert "Scan text fallback failed" in caplog.text


def test_note_conversion_is_a_no_op_outside_a_collecting_block() -> None:
    ingest.note_conversion(ocr_fallback="full_page")  # nothing to collect into: no error
    with ingest.conversion_notes() as outer:
        ingest.note_conversion(a=1)
        with ingest.conversion_notes() as inner:
            ingest.note_conversion(b=2)
        ingest.note_conversion(c=3)
    assert outer == {"a": 1, "c": 3} and inner == {"b": 2}


async def test_convert_document_brings_the_notes_back_from_the_thread_pool(tmp_path: Path) -> None:
    class _NotingPipeline(_ReaderPipeline):
        def _read_document(self, file_path: str) -> Tuple[str, Optional[Any]]:
            ingest.note_conversion(ocr_fallback="full_page")
            return ("text", None)

    pipe = _NotingPipeline()
    handle = ingest.ConversionHandle()
    token = ingest._conversion_handle_var.set(handle)
    try:
        content, document = await pipe._convert_document(str(tmp_path / "a.pdf"))
    finally:
        ingest._conversion_handle_var.reset(token)

    assert (content, document) == ("text", None)
    assert handle.notes == {"ocr_fallback": "full_page"}
    assert handle.started.is_set()


# --------------------------------------------------------------------------- the worker protocol


async def test_worker_protocol_carries_the_notes(tmp_path: Path) -> None:
    workers = pytest.importorskip("src.ingestion.conversion_workers")
    from backend.tests import conversion_worker_targets as targets

    path = tmp_path / "scan.txt"
    path.write_text("hello", encoding="utf-8")
    init = workers.WorkerInit(apply_pipeline_config=False, warm_up=False)
    noting = workers.ConversionProcessPool(1, target=targets.noting_reader, init=init, ready_timeout=120.0)
    plain = workers.ConversionProcessPool(1, target=targets.sleepy_reader, init=init, ready_timeout=120.0)
    try:
        content, document, notes = await noting.convert_with_notes(str(path))
        assert (content, document) == ("hello", None)
        assert notes["ocr_fallback"] == "full_page" and notes["pid"] in noting.live_pids()
        assert await noting.convert(str(path)) == ("hello", None)  # the two-tuple API is unchanged

        (tmp_path / "zero.txt").write_text("0", encoding="utf-8")
        content, document, notes = await plain.convert_with_notes(str(tmp_path / "zero.txt"))
        assert content.startswith("slept 0.0") and document is None and notes == {}
    finally:
        noting.shutdown()
        plain.shutdown()


# --------------------------------------------------------------------------- stats


def test_file_stats_and_results_carry_the_fallback() -> None:
    now = datetime.now()
    stat = ingest.IngestionFileStats(
        file_path="/mounts/akten/a.pdf", file_name="a.pdf", file_size_bytes=10, started_at=now, completed_at=now,
        processing_time_ms=1.0, chunks_created=3, success=True, error_type=None, error_message=None,
        timeout_seconds=10.0, profile_key="p", image_only_pdf=True, ocr_fallback="picture_children",
    )
    as_dict = stat.to_dict()
    assert as_dict["ocr_fallback"] == "picture_children"
    assert as_dict["image_only_pdf"] is True  # the PR #29 flag keeps its meaning next to it
    assert "ocr_fallback" not in replace(stat, ocr_fallback=None).to_dict()
    assert ingest.IngestionResult(document_id="", title="", chunks_created=0, processing_time_ms=0, errors=[]).ocr_fallback is None


def test_concurrent_loop_writes_the_fallback_into_the_stat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pypdfium2")
    pdf = tmp_path / "scan.pdf"
    _image_only_pdf(pdf)
    monkeypatch.delenv("INGEST_SKIP_IMAGE_ONLY_PDF", raising=False)
    monkeypatch.setattr(ingest, "load_settings", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no settings")))

    class _Loop(_ReaderPipeline):
        async def _ingest_single_document(self, file_path: str, content_hash: Optional[str]) -> Any:
            return ingest.IngestionResult(
                document_id="doc-1", title="scan.pdf", chunks_created=2, processing_time_ms=1.0, errors=[],
                ocr_fallback="picture_children",
            )

    pipe = _Loop()
    results, duplicates = asyncio.run(
        pipe._ingest_documents_concurrent(document_files=[str(pdf)], existing_hashes={}, incremental=False, max_concurrent=2)
    )

    assert duplicates == 0 and len(results) == 1
    stat = pipe.stats[0]
    assert stat.ocr_fallback == "picture_children"
    assert stat.success is True and stat.classification == "normal" and stat.chunks_created == 2
    assert stat.image_only_pdf is True  # a pure scan whose text came back through the fallback
    assert stat.to_dict()["ocr_fallback"] == "picture_children"


# --------------------------------------------------------------------------- the Tesseract pass


def test_full_page_tesseract_command_mirrors_the_pipeline_options(monkeypatch: pytest.MonkeyPatch) -> None:
    ocr = pytest.importorskip("src.ingestion.ocr_tesseract")
    captured: Dict[str, Any] = {}

    class _Done:
        stdout = "Zahlungsanweisung\n\nIBAN AT64\n".encode("utf-8")

    def fake_run(cmd: List[str], **kwargs: Any) -> _Done:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _Done()

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)
    monkeypatch.setattr(ocr, "tesseract_command", lambda *a, **k: "/tmp/recallhub-tesseract/tesseract")

    text = ocr.ocr_image_file("/tmp/page.png", ("deu", "eng"), psm=6, tessdata_path="/usr/share/tessdata")

    assert captured["cmd"] == [
        "/tmp/recallhub-tesseract/tesseract", "-l", "deu+eng", "--tessdata-dir", "/usr/share/tessdata",
        "--psm", "6", "/tmp/page.png", "stdout",
    ]
    assert captured["kwargs"]["check"] is True and captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["timeout"] == ocr.FULL_PAGE_OCR_TIMEOUT_SECONDS
    assert text.startswith("Zahlungsanweisung")

    ocr.ocr_image_file("/tmp/p.png", ("deu",))  # defaults: no psm, no tessdata flags
    assert captured["cmd"] == ["/tmp/recallhub-tesseract/tesseract", "-l", "deu", "/tmp/p.png", "stdout"]

    def slow(cmd: List[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(ocr.subprocess, "run", slow)
    assert ocr.ocr_image_file("/tmp/p.png", ("deu",)) == ""  # a timeout costs the page, not the file


def test_ocr_pil_image_writes_a_temporary_png(monkeypatch: pytest.MonkeyPatch) -> None:
    ocr = pytest.importorskip("src.ingestion.ocr_tesseract")
    Image = pytest.importorskip("PIL.Image")
    seen: Dict[str, Any] = {}

    def fake_file_ocr(path: str, languages: Any, psm: Any = None, tessdata_path: Any = None) -> str:
        seen["exists"] = Path(path).exists()
        seen["path"] = path
        seen["args"] = (tuple(languages), psm, tessdata_path)
        return "ok"

    monkeypatch.setattr(ocr, "ocr_image_file", fake_file_ocr)
    assert ocr.ocr_pil_image(Image.new("RGB", (8, 8)), ("deu", "eng"), psm=6, tessdata_path="/td") == "ok"
    assert seen["exists"] is True and seen["path"].endswith(".png")
    assert seen["args"] == (("deu", "eng"), 6, "/td")
    assert not Path(seen["path"]).exists()  # cleaned up


def test_full_page_reader_uses_the_pipeline_ocr_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    ocr = pytest.importorskip("src.ingestion.ocr_tesseract")
    seen: Dict[str, Any] = {}
    monkeypatch.setattr(ocr, "tesseract_available", lambda: True)
    monkeypatch.setattr(
        ocr, "ocr_pil_image",
        lambda image, languages, psm=None, tessdata_path=None: seen.update(languages=languages, psm=psm, tessdata=tessdata_path) or "ok",
    )
    reader = ingest.full_page_ocr_reader(ingest.OcrConfig(languages=("deu", "eng"), psm=6, tessdata_path="/td"))
    assert reader is not None and reader(object()) == "ok"
    assert seen == {"languages": ("deu", "eng"), "psm": 6, "tessdata": "/td"}

    monkeypatch.setattr(ocr, "tesseract_available", lambda: False)
    assert ingest.full_page_ocr_reader(ingest.OcrConfig()) is None  # no binary: picture children only
