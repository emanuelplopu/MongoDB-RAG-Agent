"""Recover the OCR text of scanned PDF pages that Docling leaves out of the document body (F35).

Docling 2.142 classifies many scanned pages as one ``PictureItem`` covering the page. Its
OCR still runs, but the recognised lines are attached to that picture as *children*:
``export_to_markdown()`` renders ``<!-- image -->``, and the HybridChunker, which does not
traverse pictures, produces no chunk, so a perfectly readable scan ends up recorded as
``image_only_pdf: OCR found no text``. On the diejuristin ingest 484 of the first 539 pure
scans after the 2026-10-26 deploy (90 %) were recorded that way; ``Aufklärungsbogen
Aesthetic.Clinic vom 16.6.2017.pdf`` carries 1,568 characters of OCR text inside its one
picture, ``Beilage I.PDF`` 736. A second variant returns no items at all for a page that plain
Tesseract reads without trouble (``Direktzustellung_2009_03_24_4.pdf``, 209 words).

Both are repaired here, after the conversion and only on pages that produced no text of
their own. A page with a text item or a table cell in the body is never touched, so the
normal path is unchanged:

* ``picture_children``: the text children of the page's pictures are copied into the body as
  ordinary text items, right after their picture and in reading order, so the exported
  Markdown and the chunker see them.
* ``full_page``: a page without any item, without a native text layer but with an image
  object (a scan) is rendered with pypdfium2 at the pipeline's OCR scale and read with one
  Tesseract pass (``deu+eng``, the configured page segmentation mode and tessdata, the
  ``OCR_OMP_THREAD_LIMIT`` wrapper of :mod:`src.ingestion.ocr_tesseract`); its paragraphs
  become text items of that page, inserted in page order.

The ``§`` repair of :mod:`src.ingestion.ocr_repair` runs after the conversion on the whole
document and therefore covers the recovered items as well. The caller re-exports the
Markdown from the repaired document.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    PictureItem,
    ProvenanceItem,
    Size,
    TableItem,
    TextItem,
)

logger = logging.getLogger(__name__)

#: The page's OCR text was attached to a ``PictureItem`` and has been copied into the body.
PICTURE_CHILDREN = "picture_children"
#: The page had no item at all and was read with one full-page Tesseract pass.
FULL_PAGE = "full_page"

#: Below this many native (text-layer) characters a page counts as scanned, the same
#: threshold ``is_image_only_pdf`` applies to a whole document.
MIN_NATIVE_TEXT_CHARS = 50

#: Reads one rendered page (a ``PIL.Image.Image``) and returns its text.
PageOcr = Callable[[Any], str]


@dataclass
class ScanTextRecovery:
    """What the fallback did to one document.

    Attributes:
        pages: Page number -> variant (``PICTURE_CHILDREN`` or ``FULL_PAGE``) for every page
            whose text was recovered.
        characters: Characters of recovered text (whitespace-stripped).
        pictures: Pictures whose children were copied into the body.
    """

    pages: Dict[int, str] = field(default_factory=dict)
    characters: int = 0
    pictures: int = 0

    @property
    def fallback(self) -> str:
        """The value recorded in the file's stats: one variant, or both joined with ``+``."""
        kinds = sorted(set(self.pages.values()), key=(PICTURE_CHILDREN, FULL_PAGE).index)
        return "+".join(kinds)

    def describe(self) -> str:
        """One line for the log."""
        pages = ", ".join(str(n) for n in sorted(self.pages))
        parts = [f"{self.characters:,} characters"]
        if self.pictures:
            parts.append(f"{self.pictures} picture(s)")
        return f"{self.fallback}: {' from '.join(parts)} on page(s) {pages}"


@dataclass
class _PageState:
    """What the converted document holds for one page."""

    items: int = 0  # body items with provenance on the page, pictures included
    body_text: bool = False  # a text item or a table cell outside pictures
    hidden: List[Tuple[PictureItem, List[TextItem]]] = field(default_factory=list)


def _page_numbers(item: Any) -> List[int]:
    return [prov.page_no for prov in (getattr(item, "prov", None) or [])]


def _text_children(doc: DoclingDocument, picture: PictureItem) -> List[TextItem]:
    """Text items hanging below ``picture`` (any depth), captions excluded.

    Captions are rendered with the picture by the Markdown export and the chunker's picture
    serializer, so they are not hidden; everything else under a picture is.
    """
    captions = {ref.cref for ref in picture.captions}
    out: List[TextItem] = []
    for child, _ in doc.iterate_items(root=picture, traverse_pictures=True, with_groups=False):
        if child is picture or child.self_ref == picture.self_ref:
            continue
        if isinstance(child, TextItem) and child.self_ref not in captions and child.text.strip():
            out.append(child)
    return out


def _analyse(doc: DoclingDocument) -> Dict[int, _PageState]:
    """Classify every page of ``doc`` by what its body holds (default content layers only).

    Page headers and footers (Docling's furniture layer) are not body text: a scanned page
    whose only visible item is a page number still counts as a page without text.
    """
    states: Dict[int, _PageState] = {int(n): _PageState() for n in doc.pages}

    def state(page_no: int) -> _PageState:
        return states.setdefault(page_no, _PageState())

    for item, _ in doc.iterate_items(with_groups=False, traverse_pictures=False):
        pages = _page_numbers(item)
        for page_no in pages:
            state(page_no).items += 1
        if isinstance(item, TextItem):
            if item.text.strip():
                for page_no in pages:
                    state(page_no).body_text = True
        elif isinstance(item, TableItem):
            cells = getattr(getattr(item, "data", None), "table_cells", None) or []
            if any((getattr(cell, "text", "") or "").strip() for cell in cells):
                for page_no in pages:
                    state(page_no).body_text = True
        elif isinstance(item, PictureItem):
            children = _text_children(doc, item)
            if not children:
                continue
            if not pages:  # a picture without provenance: use its children's pages
                pages = sorted({n for child in children for n in _page_numbers(child)})
            for page_no in pages:
                state(page_no).hidden.append((item, children))
    return states


def _first_prov(item: Any) -> Optional[ProvenanceItem]:
    provs = getattr(item, "prov", None) or []
    return provs[0] if provs else None


def _copy_picture_children(doc: DoclingDocument, picture: PictureItem, children: List[TextItem]) -> int:
    """Insert the text of ``children`` after ``picture`` as plain text items; returns the characters."""
    characters = 0
    anchor: Any = picture
    for child in children:
        text = child.text.strip()
        anchor = doc.insert_text(
            sibling=anchor,
            label=DocItemLabel.TEXT,
            text=text,
            prov=_first_prov(child) or _first_prov(picture),
            after=True,
        )
        characters += len(text)
    return characters


def _anchor_for_page(doc: DoclingDocument, page_no: int) -> Tuple[Optional[Any], bool]:
    """Where text of ``page_no`` belongs in the body: ``(sibling, after)``, or ``(None, True)`` to append.

    The last body item of an earlier page is the sibling to insert after; when there is none,
    the first body item is the sibling to insert before, so page order is kept either way.
    """
    last_before: Optional[Any] = None
    first: Optional[Any] = None
    for item, _ in doc.iterate_items(with_groups=False, traverse_pictures=False):
        if first is None:
            first = item
        pages = _page_numbers(item)
        if pages and max(pages) < page_no:
            last_before = item
    if last_before is not None:
        return last_before, True
    if first is not None:
        return first, False
    return None, True


def _paragraphs(text: str) -> List[str]:
    """Tesseract's blocks (blank-line separated), each with its line breaks folded into spaces."""
    out: List[str] = []
    for block in re.split(r"\n\s*\n", text or ""):
        folded = " ".join(part.strip() for part in block.splitlines() if part.strip())
        if folded:
            out.append(folded)
    return out


def _page_is_scan(page: Any) -> Tuple[bool, int, int]:
    """Whether a pdfium page has no native text layer but carries at least one image object.

    Returns:
        ``(is_scan, native_characters, image_objects)``.
    """
    import pypdfium2.raw as pdfium_c

    native = len(page.get_textpage().get_text_range().strip())
    images = sum(1 for obj in page.get_objects(max_depth=2) if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE)
    return native < MIN_NATIVE_TEXT_CHARS and images > 0, native, images


def _add_page_text(doc: DoclingDocument, page_no: int, size: Tuple[float, float], paragraphs: List[str]) -> int:
    """Add ``paragraphs`` as text items of ``page_no`` in page order; returns the characters."""
    width, height = size
    if page_no not in doc.pages:
        doc.add_page(page_no=page_no, size=Size(width=width, height=height))
    anchor, after = _anchor_for_page(doc, page_no)
    characters = 0
    for paragraph in paragraphs:
        prov = ProvenanceItem(
            page_no=page_no,
            bbox=BoundingBox(l=0.0, t=0.0, r=width, b=height, coord_origin=CoordOrigin.TOPLEFT),
            charspan=(0, len(paragraph)),
        )
        if anchor is None:
            anchor = doc.add_text(label=DocItemLabel.TEXT, text=paragraph, prov=prov)
        else:
            anchor = doc.insert_text(sibling=anchor, label=DocItemLabel.TEXT, text=paragraph, prov=prov, after=after)
        after = True
        characters += len(paragraph)
    return characters


def _full_page_ocr(
    doc: DoclingDocument,
    pdf_path: str,
    states: Dict[int, _PageState],
    scale: float,
    ocr_page: PageOcr,
    recovery: ScanTextRecovery,
) -> None:
    """Variant B for every PDF page without any item that is a scan; fills ``recovery``.

    The candidates come from the PDF's own page count, so pages Docling did not even list
    in ``doc.pages`` (a conversion that returned nothing at all) are covered too.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_path)
    try:
        for page_no in range(1, len(pdf) + 1):
            state = states.get(page_no)
            if state is not None and (state.items or state.body_text or state.hidden):
                continue
            page = pdf[page_no - 1]
            is_scan, native, images = _page_is_scan(page)
            if not is_scan:
                logger.debug(
                    "Page %d of %s has %d native characters and %d image(s): no full-page OCR",
                    page_no, pdf_path, native, images,
                )
                continue
            width, height = page.get_size()
            image = page.render(scale=scale).to_pil()
            paragraphs = _paragraphs(ocr_page(image))
            if not paragraphs:
                continue
            recovery.characters += _add_page_text(doc, page_no, (float(width), float(height)), paragraphs)
            recovery.pages[page_no] = FULL_PAGE
    finally:
        pdf.close()


def recover_scan_text(
    doc: DoclingDocument,
    pdf_path: Optional[str] = None,
    *,
    ocr_page: Optional[PageOcr] = None,
    scale: float = 3.0,
) -> Optional[ScanTextRecovery]:
    """Put the text of scanned pages that Docling left out of the body back into ``doc``.

    Pages that produced body text (a text item or a table cell outside pictures) are left
    exactly as they are. For the other pages, in page order:

    * pages with pictures whose children carry text get that text copied into the body right
      after each picture (``picture_children``);
    * pages without any item are read with one full-page Tesseract pass when ``pdf_path`` and
      ``ocr_page`` are given and the page is a scan (no native text layer, at least one image
      object); the text's paragraphs become text items of the page (``full_page``).

    Args:
        doc: The converted document, modified in place.
        pdf_path: The source PDF (needed for ``full_page`` only).
        ocr_page: Reads one rendered page image; ``None`` disables ``full_page``.
        scale: Render scale for the full-page pass (OCR pixels per page point).

    Returns:
        What was recovered, or ``None`` when nothing was (the document is then unchanged).
    """
    states = _analyse(doc)
    recovery = ScanTextRecovery()
    copied: set = set()  # a picture spanning two pages is copied once

    for page_no in sorted(states):
        state = states[page_no]
        if state.body_text or not state.hidden:
            continue
        for picture, children in state.hidden:
            if picture.self_ref in copied:
                continue
            copied.add(picture.self_ref)
            recovery.characters += _copy_picture_children(doc, picture, children)
            recovery.pictures += 1
        recovery.pages[page_no] = PICTURE_CHILDREN

    # The PDF is opened only when Docling listed a page without any item (or no page at all):
    # a document whose pages all hold something never pays for pdfium here.
    empty_pages = not states or any(
        not (state.items or state.body_text or state.hidden) for state in states.values()
    )
    if empty_pages and pdf_path and ocr_page is not None:
        _full_page_ocr(doc, pdf_path, states, scale, ocr_page, recovery)

    return recovery if recovery.pages else None
