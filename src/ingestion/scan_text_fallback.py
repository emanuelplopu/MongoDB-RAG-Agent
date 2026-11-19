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
their own. A page whose body holds at least :data:`MIN_BODY_TEXT_CHARS` characters of text
outside pictures (text items that are not headings, table cells) is never touched, so the
normal path is unchanged. Headings do not count (the ``no_chunks`` audit of 2026-11-19,
``docs/26-FORMAT_EDGE_AUDIT.md``: a court scan whose only body item is a section header made
of its first OCR line kept the rest of the page hidden below its picture, and a heading
alone yields no chunk anyway). For the other pages, in page order:

* ``picture_children``: the text children of the page's pictures are copied into the body as
  ordinary text items, right after their picture and in reading order, so the exported
  Markdown and the chunker see them.
* ``full_page``: a page that still has fewer than :data:`MIN_BODY_TEXT_CHARS` characters of
  body text afterwards, without a native text layer but with an image object (a scan), is
  rendered with pypdfium2 at the pipeline's OCR scale and read with one Tesseract pass
  (``deu+eng``, the configured page segmentation mode and tessdata, the
  ``OCR_OMP_THREAD_LIMIT`` wrapper of :mod:`src.ingestion.ocr_tesseract`); its paragraphs
  become text items of that page, inserted in page order. When the pass at the OCR scale
  finds (nearly) nothing, it is repeated at lower scales (:data:`FULL_PAGE_SCALE_FACTORS`):
  a 1-bit 300-dpi scan rendered at 216 dpi comes out anti-aliased and Tesseract reads
  nothing, the same page at 144 or 108 dpi reads fine (``20211117095036738.pdf``, 894
  characters at scale 2, none at scale 3).

:func:`recover_image_text` applies the same two steps to the conversion of an image file
(a photographed letter, a screenshot): the picture children are copied, and a plain Tesseract
pass over the image itself at falling scales runs when the document still has no text.

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
#: The page had (nearly) no body text and was read with one full-page Tesseract pass.
FULL_PAGE = "full_page"

#: Below this many native (text-layer) characters a page counts as scanned, the same
#: threshold ``is_image_only_pdf`` applies to a whole document.
MIN_NATIVE_TEXT_CHARS = 50
#: Below this many characters of body text outside pictures (headings excluded) a page is
#: one whose text Docling left out: its picture children are copied and, for a scan, the
#: full-page pass runs.
MIN_BODY_TEXT_CHARS = 50
#: Multipliers of the OCR scale the full-page pass tries, in order, until one reads at least
#: :data:`MIN_BODY_TEXT_CHARS` characters; the longest result wins otherwise.
FULL_PAGE_SCALE_FACTORS: Tuple[float, ...] = (1.0, 2 / 3, 0.5)
#: Labels that are not body text: a heading or caption alone never makes a chunk.
HEADING_LABELS = frozenset({
    DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE, DocItemLabel.CAPTION,
    DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER,
})

#: Reads one rendered page (a ``PIL.Image.Image``) and returns its text.
PageOcr = Callable[[Any], str]


@dataclass
class ScanTextRecovery:
    """What the fallback did to one document.

    Attributes:
        pages: Page number -> variant (``PICTURE_CHILDREN``, ``FULL_PAGE`` or both joined
            with ``+``) for every page whose text was recovered.
        characters: Characters of recovered text (whitespace-stripped).
        pictures: Pictures whose children were copied into the body.
        scales: Page number -> render scale of a full-page pass that did not succeed at the
            pipeline's own scale (the lower scale that read the page).
    """

    pages: Dict[int, str] = field(default_factory=dict)
    characters: int = 0
    pictures: int = 0
    scales: Dict[int, float] = field(default_factory=dict)
    #: Set when the full-page pass failed after the picture-children copy had already changed
    #: the document: what was recovered up to then is reported, the error alongside it.
    error: Optional[str] = None

    @property
    def fallback(self) -> str:
        """The value recorded in the file's stats: one variant, or both joined with ``+``."""
        kinds: set = set()
        for value in self.pages.values():
            kinds.update(value.split("+"))
        return "+".join(sorted(kinds, key=(PICTURE_CHILDREN, FULL_PAGE).index))

    def _add_page(self, page_no: int, variant: str) -> None:
        current = self.pages.get(page_no)
        if current and variant not in current.split("+"):
            self.pages[page_no] = f"{current}+{variant}"
        elif not current:
            self.pages[page_no] = variant

    def describe(self) -> str:
        """One line for the log."""
        pages = ", ".join(str(n) for n in sorted(self.pages))
        parts = [f"{self.characters:,} characters"]
        if self.pictures:
            parts.append(f"{self.pictures} picture(s)")
        text = f"{self.fallback}: {' from '.join(parts)} on page(s) {pages}"
        if self.scales:
            text += " (lower OCR scale: " + ", ".join(f"page {n} at {s:g}" for n, s in sorted(self.scales.items())) + ")"
        if self.error:
            text += f"; full-page pass failed afterwards: {self.error}"
        return text


@dataclass
class _PageState:
    """What the converted document holds for one page."""

    items: int = 0  # body items with provenance on the page, pictures included
    body_chars: int = 0  # characters of text outside pictures: non-heading text items, table cells
    texts: List[str] = field(default_factory=list)  # every text the page shows (headings too), for the duplicate check
    hidden: List[Tuple[PictureItem, List[TextItem]]] = field(default_factory=list)

    @property
    def body_text(self) -> bool:
        """Whether the page holds enough body text to be left alone."""
        return self.body_chars >= MIN_BODY_TEXT_CHARS


def _normalised(text: str) -> str:
    return " ".join(text.casefold().split())


def _new_paragraphs(paragraphs: List[str], existing: List[str]) -> List[str]:
    """The paragraphs of a full-page pass that are not already on the page.

    The pass runs on a page that may hold a heading or copied picture children; Tesseract
    reads those again, and a web button's label must not become text by appearing twice.
    """
    known = _normalised(" ".join(existing))
    return [p for p in paragraphs if _normalised(p) not in known]


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
            text = item.text.strip()
            if text:
                for page_no in pages:
                    state(page_no).texts.append(text)
                    if item.label not in HEADING_LABELS:
                        state(page_no).body_chars += len(text)
        elif isinstance(item, TableItem):
            cells = getattr(getattr(item, "data", None), "table_cells", None) or []
            cell_texts = [(getattr(cell, "text", "") or "").strip() for cell in cells]
            chars = sum(len(text) for text in cell_texts)
            for page_no in pages:
                state(page_no).body_chars += chars
                state(page_no).texts.extend(text for text in cell_texts if text)
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


def _add_page_text(
    doc: DoclingDocument, page_no: int, size: Tuple[float, float], paragraphs: List[str], append: bool = False
) -> int:
    """Add ``paragraphs`` as text items of ``page_no`` in page order; returns the characters.

    With ``append`` the paragraphs go after the last body item instead (an image is one page:
    its picture and the copied children first, the Tesseract pass after them).
    """
    width, height = size
    if page_no not in doc.pages:
        doc.add_page(page_no=page_no, size=Size(width=width, height=height))
    if append:
        items = [item for item, _ in doc.iterate_items(with_groups=False, traverse_pictures=False)]
        anchor, after = (items[-1], True) if items else (None, True)
    else:
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


def _ocr_at_scales(
    render: Callable[[float], Any], scale: float, ocr_page: PageOcr
) -> Tuple[List[str], Optional[float]]:
    """Read a page at the OCR scale and, when that finds (nearly) nothing, at lower scales.

    Args:
        render: Renders the page at a given scale as a ``PIL.Image.Image``.
        scale: The pipeline's OCR scale (pixels per page point); the ladder starts there.
        ocr_page: The Tesseract pass.

    Returns:
        The paragraphs of the best pass (the first one with at least
        :data:`MIN_BODY_TEXT_CHARS` characters, else the longest) and the scale that read them
        when it was not ``scale`` itself (``None`` for the first pass or for no text at all).
    """
    best: List[str] = []
    best_scale: Optional[float] = None
    for factor in FULL_PAGE_SCALE_FACTORS:
        attempt = round(scale * factor, 4)
        paragraphs = _paragraphs(ocr_page(render(attempt)))
        chars = sum(len(p) for p in paragraphs)
        if chars > sum(len(p) for p in best):
            best, best_scale = paragraphs, (None if factor == 1.0 else attempt)
        if chars >= MIN_BODY_TEXT_CHARS:
            break
    return best, best_scale


def _full_page_ocr(
    doc: DoclingDocument,
    pdf_path: str,
    states: Dict[int, _PageState],
    scale: float,
    ocr_page: PageOcr,
    recovery: ScanTextRecovery,
) -> None:
    """Variant B for every PDF page without enough body text that is a scan; fills ``recovery``.

    The candidates come from the PDF's own page count, so pages Docling did not even list
    in ``doc.pages`` (a conversion that returned nothing at all) are covered too.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_path)
    try:
        for page_no in range(1, len(pdf) + 1):
            state = states.get(page_no)
            if state is not None and state.body_text:
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
            paragraphs, used_scale = _ocr_at_scales(lambda s: page.render(scale=s).to_pil(), scale, ocr_page)
            paragraphs = _new_paragraphs(paragraphs, state.texts if state is not None else [])
            if not paragraphs:
                continue
            recovery.characters += _add_page_text(doc, page_no, (float(width), float(height)), paragraphs)
            recovery._add_page(page_no, FULL_PAGE)
            if used_scale is not None:
                recovery.scales[page_no] = used_scale
    finally:
        pdf.close()


def _copy_hidden_text(doc: DoclingDocument, states: Dict[int, _PageState], recovery: ScanTextRecovery) -> None:
    """Variant A for every page without enough body text; the copied text counts as body text."""
    copied: set = set()  # a picture spanning two pages is copied once
    for page_no in sorted(states):
        state = states[page_no]
        if state.body_text or not state.hidden:
            continue
        for picture, children in state.hidden:
            if picture.self_ref in copied:
                continue
            copied.add(picture.self_ref)
            characters = _copy_picture_children(doc, picture, children)
            recovery.characters += characters
            state.body_chars += characters
            state.texts.extend(child.text.strip() for child in children)
            recovery.pictures += 1
        recovery._add_page(page_no, PICTURE_CHILDREN)


def recover_scan_text(
    doc: DoclingDocument,
    pdf_path: Optional[str] = None,
    *,
    ocr_page: Optional[PageOcr] = None,
    scale: float = 3.0,
) -> Optional[ScanTextRecovery]:
    """Put the text of scanned pages that Docling left out of the body back into ``doc``.

    Pages with at least :data:`MIN_BODY_TEXT_CHARS` characters of body text (text items
    that are not headings, table cells; nothing below a picture) are left exactly as they
    are. For the other pages, in page order:

    * pages with pictures whose children carry text get that text copied into the body right
      after each picture (``picture_children``);
    * pages still below the threshold afterwards are read with a full-page Tesseract pass
      when ``pdf_path`` and ``ocr_page`` are given and the page is a scan (no native text
      layer, at least one image object); the text's paragraphs become text items of the page
      (``full_page``). The pass is repeated at lower scales when the first finds nothing.

    Args:
        doc: The converted document, modified in place.
        pdf_path: The source PDF (needed for ``full_page`` only).
        ocr_page: Reads one rendered page image; ``None`` disables ``full_page``.
        scale: Render scale for the full-page pass (OCR pixels per page point).

    Returns:
        What was recovered, or ``None`` when nothing was (the document is then unchanged).
        When the full-page pass fails after picture children were already copied, the
        recovery of those pages is returned with ``error`` set instead of raising, so the
        text that is now in the document is reported.

    Raises:
        Exception: whatever pdfium or the OCR raised, when nothing had been recovered before.
    """
    states = _analyse(doc)
    recovery = ScanTextRecovery()
    _copy_hidden_text(doc, states, recovery)

    # The PDF is opened only when a page is still short of body text (or Docling listed no
    # page at all): a document whose pages all hold text never pays for pdfium here.
    short_pages = not states or any(not state.body_text for state in states.values())
    if short_pages and pdf_path and ocr_page is not None:
        try:
            _full_page_ocr(doc, pdf_path, states, scale, ocr_page, recovery)
        except Exception as exc:  # noqa: BLE001 - the document already holds what was recovered
            if not recovery.pages:
                raise
            recovery.error = f"{type(exc).__name__}: {exc}"

    return recovery if recovery.pages else None


def recover_image_text(
    doc: DoclingDocument,
    image_path: Optional[str] = None,
    *,
    ocr_page: Optional[PageOcr] = None,
    max_pixels: int = 0,
) -> Optional[ScanTextRecovery]:
    """The scan-text fallback for the conversion of one image file.

    Docling converts an image as a one-page document, so the same two losses occur: the OCR
    lines of a photographed letter or a screenshot hang below the ``PictureItem`` that covers
    the image (``IMG_5637.jpeg``: 2,874 characters of children, ``<!-- image -->`` exported),
    or the page holds nothing although a plain Tesseract pass reads it. Picture children are
    copied first; when the document still has fewer than :data:`MIN_BODY_TEXT_CHARS`
    characters of body text and ``image_path`` and ``ocr_page`` are given, the image itself
    is read with a Tesseract pass at its own size (capped at ``max_pixels``) and, when that
    finds nothing, at the lower scales of :data:`FULL_PAGE_SCALE_FACTORS`.

    Args:
        doc: The converted document, modified in place.
        image_path: The image file (a format PIL opens), or ``None`` for picture children only.
        ocr_page: Reads one ``PIL.Image.Image``; ``None`` disables the Tesseract pass.
        max_pixels: Largest bitmap handed to Tesseract (0 = the image as it is).

    Returns:
        What was recovered, or ``None`` when nothing was (the document is then unchanged).
    """
    states = _analyse(doc)
    recovery = ScanTextRecovery()
    _copy_hidden_text(doc, states, recovery)

    short = not states or any(not state.body_text for state in states.values())
    if short and image_path and ocr_page is not None:
        try:
            from PIL import Image

            with Image.open(image_path) as source:
                image = source.convert("RGB") if source.mode not in ("L", "RGB") else source.copy()
            if max_pixels > 0 and image.width * image.height > max_pixels:
                ratio = (max_pixels / (image.width * image.height)) ** 0.5
                image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))))

            def render(scale: float) -> Any:
                if scale >= 1.0:
                    return image
                return image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))

            paragraphs, used_scale = _ocr_at_scales(render, 1.0, ocr_page)
            page_no = min(states) if states else 1
            paragraphs = _new_paragraphs(paragraphs, states[page_no].texts if page_no in states else [])
            if paragraphs:
                size = doc.pages[page_no].size if page_no in doc.pages else None
                dims = (float(size.width), float(size.height)) if size is not None else (float(image.width), float(image.height))
                recovery.characters += _add_page_text(doc, page_no, dims, paragraphs, append=True)
                recovery._add_page(page_no, FULL_PAGE)
                if used_scale is not None:
                    recovery.scales[page_no] = used_scale
        except Exception as exc:  # noqa: BLE001 - the document already holds what was recovered
            if not recovery.pages:
                raise
            recovery.error = f"{type(exc).__name__}: {exc}"

    return recovery if recovery.pages else None
