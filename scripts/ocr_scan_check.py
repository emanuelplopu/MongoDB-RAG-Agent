"""OCR quality check for a folder of scanned files, using the production Docling path.

Runs every PDF / image in an input folder through the *same* ``DocumentConverter``
configuration the ingestion worker uses (``src.ingestion.ingest.create_document_converter``
for PDFs, the ``ImageFormatOption`` branch of ``process_file`` for images) and writes,
per file, one text file per page next to a rendered page image so a human can spot-check
the OCR, plus a ``meta.json`` with scan resolution, text-layer and timing facts and a
few automatic loss heuristics. Nothing is written to MongoDB; no ingestion job is run.

Intended to run inside the ingestion-worker image, where the Docling version and OCR
engine are the ones production uses::

    docker run --rm \
      -v <sample>/in:/data/in:ro -v <sample>/out:/data/out -v <sample>/cache:/root/.cache \
      -v <repo>/scripts/ocr_scan_check.py:/app/ocr_scan_check.py:ro \
      --entrypoint python recallhub-ingestion-worker:latest /app/ocr_scan_check.py /data/in /data/out

Optional ``<name>.gold.txt`` files next to an input file (same stem) are treated as the
expected page text; the summary then reports word / line recall against them (the
synthetic corpus manifests carry such text). The output folder gets ``summary.json`` and
``summary.md``.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"}
A4_WIDTH_IN = 8.27  # fallback for images without a DPI tag: assume a portrait A4 scan

AMOUNT_RE = re.compile(r"(?:EUR|€)\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d{1,3}(?:\.\d{3})*,\d{2}\s?(?:EUR|€)")
DATE_RE = re.compile(r"\b\d{1,2}\.\s?\d{1,2}\.\s?(?:19|20)\d{2}\b")
GZ_RE = re.compile(r"\b\d{1,3}\s?[A-Za-z]{1,3}\s?\d{1,5}\s?/\s?\d{2}[a-z]?\b")  # 21 C 752/25d
NORM_RE = re.compile(r"\b(?:Ö|OE|O)NORM\s?[A-Z]\s?\d{3,5}\b|\b(?:DIN|EN|ISO)\s?\d{3,5}\b", re.IGNORECASE)
PARA_RE = re.compile(r"§\s?\d+[a-z]?")
WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]{4,}")
UMLAUT_RE = re.compile(r"[ÄÖÜäöüß]")
GARBAGE_RE = re.compile(r"[^\w\s.,;:!?()\[\]§€%/\-–—'\"„“”‚‘’+*&@#°\\|]")
VOWEL_RE = re.compile(r"[aeiouyäöüAEIOUYÄÖÜ]")
# "HandelGmbH", "Stufe3", "Eintrittsdatum:01.05.2019", "1110Wien" - tokens glued together
MERGED_RE = re.compile(r"\b[a-zäöüß]{3,}[A-ZÄÖÜ][a-zäöüß]{2,}\b|\b[A-Za-zäöüß]{3,}\d{1,}\b|\b\d{3,}[A-Za-zäöüß]{3,}\b|[A-Za-zäöüß]{3,}:\d")

log = logging.getLogger("ocr_scan_check")


@dataclass
class PageMeta:
    page_no: int
    width_px: Optional[int] = None
    height_px: Optional[int] = None
    dpi: Optional[float] = None
    dpi_source: str = ""
    text_layer_chars: int = 0
    ocr_chars: int = 0
    ocr_words: int = 0
    umlaut_chars: int = 0
    amounts: List[str] = field(default_factory=list)
    dates: List[str] = field(default_factory=list)
    case_numbers: List[str] = field(default_factory=list)
    norm_codes: List[str] = field(default_factory=list)
    paragraphs: List[str] = field(default_factory=list)
    garbage_ratio: float = 0.0
    short_line_ratio: float = 0.0
    lines: int = 0
    garbled_lines: int = 0
    garbled_examples: List[str] = field(default_factory=list)
    merged_tokens: int = 0
    merged_examples: List[str] = field(default_factory=list)


@dataclass
class FileMeta:
    file: str
    kind: str
    bytes: int
    pages: int = 0
    convert_seconds: float = 0.0
    status: str = "ok"
    error: str = ""
    markdown_chars: int = 0
    page_meta: List[PageMeta] = field(default_factory=list)
    gold: Optional[Dict[str, Any]] = None


# --------------------------------------------------------------------------------------
# Converters: mirror src/ingestion/ingest.py exactly
# --------------------------------------------------------------------------------------

@dataclass
class Variant:
    """What-if knobs for experiments; all ``None`` means the exact production configuration."""

    ocr_scale: Optional[float] = None  # docling ocr_options.scale (production: 3.0)
    force_full_page_ocr: Optional[bool] = None  # production: False
    lang: Optional[List[str]] = None  # production: [] (engine default)
    engine: Optional[str] = None  # "rapidocr", "easyocr", "tesseract", ... (production: auto)
    upscale: Optional[float] = None  # pre-processing: resample input images by this factor

    def is_production(self) -> bool:
        return all(v is None for v in (self.ocr_scale, self.force_full_page_ocr, self.lang, self.engine, self.upscale))


def _apply_variant(pipeline_options: Any, variant: Variant) -> None:
    if variant.engine:
        from docling.datamodel import pipeline_options as po

        engines = {
            "rapidocr": po.RapidOcrOptions,
            "easyocr": po.EasyOcrOptions,
            "tesseract": po.TesseractCliOcrOptions,  # the `tesseract` binary (apt tesseract-ocr + language packs)
            "tesserocr": po.TesseractOcrOptions,
        }
        pipeline_options.ocr_options = engines[variant.engine]()
    if variant.ocr_scale is not None:
        pipeline_options.ocr_options.scale = variant.ocr_scale
    if variant.force_full_page_ocr is not None:
        pipeline_options.ocr_options.force_full_page_ocr = variant.force_full_page_ocr
    if variant.lang is not None:
        pipeline_options.ocr_options.lang = variant.lang
    log.info("variant ocr_options: %s", pipeline_options.ocr_options)


def _pdf_converter(variant: Variant) -> Any:
    """The converter the worker builds for PDFs (``create_document_converter``)."""
    sys.path.insert(0, "/app")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.ingestion.ingest import create_document_converter  # type: ignore

    converter = create_document_converter()
    if not variant.is_production():
        from docling.datamodel.base_models import InputFormat

        _apply_variant(converter.format_to_options[InputFormat.PDF].pipeline_options, variant)
    return converter


def _image_converter(variant: Variant) -> Any:
    """The converter ``process_file`` builds for image files (copied, not imported)."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption

    from src.ingestion.ingest import get_effective_device  # type: ignore

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    device = get_effective_device()
    if device in ["cuda", "mps"]:
        pipeline_options.accelerator_options = {"device": device}
    if not variant.is_production():
        _apply_variant(pipeline_options, variant)
    return DocumentConverter(
        format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options)}
    )


def _upscaled_copy(path: Path, factor: float, work_dir: Path) -> Path:
    """Pre-processing experiment: resample an image input by ``factor`` (LANCZOS)."""
    from PIL import Image

    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / (path.stem + ".png")
    with Image.open(path) as img:
        dpi = img.info.get("dpi")
        big = img.convert("L").resize((round(img.width * factor), round(img.height * factor)), Image.LANCZOS)
        save_kw = {"dpi": (dpi[0] * factor, dpi[1] * factor)} if dpi else {}
        big.save(out, "PNG", **save_kw)
    return out


# --------------------------------------------------------------------------------------
# Resolution facts
# --------------------------------------------------------------------------------------

def _pdf_page_facts(path: Path) -> List[PageMeta]:
    """Per page: text-layer chars and the DPI of the largest embedded image."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    out: List[PageMeta] = []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            pm = PageMeta(page_no=i + 1)
            try:
                pm.text_layer_chars = len(page.get_textpage().get_text_range().strip())
            except Exception as exc:  # noqa: BLE001 - diagnostics only
                log.warning("text layer read failed for %s p%d: %s", path.name, i + 1, exc)
            page_w_in = page.get_width() / 72.0
            best_area = 0
            for obj in page.get_objects(max_depth=4):
                if obj.type != pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                    continue
                try:
                    meta = obj.get_metadata()
                    px_w, px_h = meta.width, meta.height
                except Exception:  # noqa: BLE001
                    continue
                if px_w * px_h <= best_area:
                    continue
                best_area = px_w * px_h
                pm.width_px, pm.height_px = px_w, px_h
                dpi = getattr(meta, "horizontal_dpi", 0) or 0
                if dpi and dpi > 1:
                    pm.dpi, pm.dpi_source = round(float(dpi), 1), "pdf-image-metadata"
                else:
                    # image pixel width over the placed width on the page
                    left, bottom, right, top = obj.get_pos()
                    placed_in = max((right - left) / 72.0, 0.1)
                    pm.dpi, pm.dpi_source = round(px_w / placed_in, 1), "pixels/placed-width"
            if pm.width_px is None:
                pm.dpi_source = "no-embedded-image(vector/text page)"
                pm.width_px = int(page.get_width())
                pm.height_px = int(page.get_height())
            _ = page_w_in
            out.append(pm)
    finally:
        pdf.close()
    return out


def _image_page_facts(path: Path) -> List[PageMeta]:
    from PIL import Image

    with Image.open(path) as img:
        pm = PageMeta(page_no=1, width_px=img.width, height_px=img.height)
        dpi = img.info.get("dpi")
        if dpi and dpi[0] and dpi[0] > 1:
            pm.dpi, pm.dpi_source = round(float(dpi[0]), 1), "image-dpi-tag"
        else:
            pm.dpi, pm.dpi_source = round(img.width / A4_WIDTH_IN, 1), "pixels/A4-width-assumed"
        frames = getattr(img, "n_frames", 1)
        pages = [pm]
        for n in range(1, frames):
            pages.append(PageMeta(page_no=n + 1, width_px=img.width, height_px=img.height, dpi=pm.dpi, dpi_source=pm.dpi_source))
    return pages


def _render_pdf_pages(path: Path, out_dir: Path, dpi: int) -> None:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        for i in range(len(pdf)):
            pdf[i].render(scale=dpi / 72.0).to_pil().convert("RGB").save(out_dir / f"page_{i + 1:02d}.png")
    finally:
        pdf.close()


def _copy_image_page(path: Path, out_dir: Path) -> None:
    from PIL import Image

    with Image.open(path) as img:
        frames = getattr(img, "n_frames", 1)
        for n in range(frames):
            img.seek(n)
            img.convert("RGB").save(out_dir / f"page_{n + 1:02d}.png")


# --------------------------------------------------------------------------------------
# Page text from the DoclingDocument
# --------------------------------------------------------------------------------------

def _page_texts(doc: Any) -> Dict[int, List[str]]:
    """Group the document's items by page (provenance) in reading order."""
    from docling_core.types.doc import TableItem, TextItem

    pages: Dict[int, List[str]] = defaultdict(list)
    for item, _level in doc.iterate_items():
        prov = getattr(item, "prov", None) or []
        page_no = prov[0].page_no if prov else 0
        if isinstance(item, TableItem):
            try:
                pages[page_no].append(item.export_to_markdown(doc))
            except TypeError:
                pages[page_no].append(item.export_to_markdown())
        elif isinstance(item, TextItem):
            text = (item.text or "").strip()
            if text:
                pages[page_no].append(text)
    return pages


def _analyse(text: str, pm: PageMeta) -> None:
    pm.ocr_chars = len(text)
    words = WORD_RE.findall(text)
    pm.ocr_words = len(words)
    pm.umlaut_chars = len(UMLAUT_RE.findall(text))
    pm.amounts = AMOUNT_RE.findall(text)[:20]
    pm.dates = DATE_RE.findall(text)[:20]
    pm.case_numbers = GZ_RE.findall(text)[:20]
    pm.norm_codes = NORM_RE.findall(text)[:20]
    pm.paragraphs = PARA_RE.findall(text)[:30]
    letters = [c for c in text if not c.isspace()]
    pm.garbage_ratio = round(len(GARBAGE_RE.findall(text)) / max(len(letters), 1), 4)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    pm.short_line_ratio = round(sum(1 for ln in lines if len(ln.strip()) <= 3) / max(len(lines), 1), 3)
    # Letter soup: a run of >= 4 alphabetic tokens of which most are not word-like
    # (no vowel, or 1-2 letters) - the way rapidocr fails on a line it cannot read.
    pm.lines = len(lines)
    for ln in lines:
        for run in re.split(r"[.,;:!?()]\s*", ln):
            toks = [t for t in re.findall(r"[A-Za-zÄÖÜäöüß]+", run)]
            if len(toks) < 4:
                continue
            bad = sum(1 for t in toks if len(t) <= 2 or not VOWEL_RE.search(t))
            if bad / len(toks) >= 0.5:
                pm.garbled_lines += 1
                if len(pm.garbled_examples) < 5:
                    pm.garbled_examples.append(run.strip()[:80])
                break
    merged = MERGED_RE.findall(text)
    pm.merged_tokens = len(merged)
    pm.merged_examples = merged[:8]


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", text)


def _gold_report(gold_text: str, ocr_text: str) -> Dict[str, Any]:
    """Word and line recall of the expected text in the OCR text."""
    gold_n, ocr_n = _normalize(gold_text), _normalize(ocr_text)
    gold_words = [w for w in WORD_RE.findall(gold_n)]
    ocr_vocab = set(WORD_RE.findall(ocr_n))
    missing_words = [w for w in dict.fromkeys(gold_words) if w not in ocr_vocab]
    word_recall = 1 - len([w for w in gold_words if w not in ocr_vocab]) / max(len(gold_words), 1)
    lines = [ln.strip() for ln in gold_text.splitlines() if len(ln.strip()) >= 12]
    missing_lines = [ln for ln in lines if _normalize(ln) not in ocr_n]
    specials = {
        "amounts": AMOUNT_RE.findall(gold_text),
        "dates": DATE_RE.findall(gold_text),
        "case_numbers": GZ_RE.findall(gold_text),
        "norm_codes": NORM_RE.findall(gold_text),
        "paragraphs": PARA_RE.findall(gold_text),
    }
    special_hits = {k: [v for v in vals if _normalize(v) in ocr_n] for k, vals in specials.items()}
    return {
        "gold_words": len(gold_words),
        "word_recall": round(word_recall, 3),
        "line_recall": round(1 - len(missing_lines) / max(len(lines), 1), 3),
        "missing_words": missing_words[:40],
        "missing_lines": missing_lines[:15],
        "specials_found": {k: f"{len(special_hits[k])}/{len(v)}" for k, v in specials.items() if v},
        "specials_missing": {k: [x for x in v if x not in special_hits[k]] for k, v in specials.items() if v},
    }


# --------------------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------------------

def _iter_inputs(in_dir: Path) -> Iterable[Path]:
    for p in sorted(in_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in PDF_EXT | IMAGE_EXT and not p.name.endswith(".gold.txt"):
            yield p


def _safe_name(path: Path, in_dir: Path) -> str:
    rel = path.relative_to(in_dir).with_suffix("")
    return re.sub(r"[^\w\-.]+", "_", str(rel))[:120]


def process(in_dir: Path, out_dir: Path, render_dpi: int, limit: Optional[int], variant: Variant) -> List[FileMeta]:
    import docling

    results: List[FileMeta] = []
    pdf_conv = img_conv = None
    for idx, path in enumerate(_iter_inputs(in_dir)):
        if limit and idx >= limit:
            break
        kind = "pdf" if path.suffix.lower() in PDF_EXT else "image"
        fm = FileMeta(file=str(path.relative_to(in_dir)), kind=kind, bytes=path.stat().st_size)
        target = out_dir / _safe_name(path, in_dir)
        target.mkdir(parents=True, exist_ok=True)
        log.info("[%d] %s (%s, %.0f KB)", idx + 1, fm.file, kind, fm.bytes / 1024)
        try:
            fm.page_meta = _pdf_page_facts(path) if kind == "pdf" else _image_page_facts(path)
            if kind == "pdf":
                _render_pdf_pages(path, target, render_dpi)
            else:
                _copy_image_page(path, target)
        except Exception as exc:  # noqa: BLE001 - keep going, record it
            log.warning("page facts/render failed for %s: %s", fm.file, exc)
        try:
            if kind == "pdf":
                pdf_conv = pdf_conv or _pdf_converter(variant)
                conv = pdf_conv
            else:
                img_conv = img_conv or _image_converter(variant)
                conv = img_conv
            convert_path = path
            if variant.upscale and kind == "image":
                convert_path = _upscaled_copy(path, variant.upscale, out_dir / "_upscaled")
            t0 = time.perf_counter()
            result = conv.convert(str(convert_path))
            fm.convert_seconds = round(time.perf_counter() - t0, 2)
            doc = result.document
            markdown = doc.export_to_markdown()
            fm.markdown_chars = len(markdown)
            (target / "document.md").write_text(markdown, encoding="utf-8")
            texts = _page_texts(doc)
            fm.pages = max(len(fm.page_meta), max(texts.keys(), default=0))
            by_no = {pm.page_no: pm for pm in fm.page_meta}
            for page_no in range(1, fm.pages + 1):
                page_text = "\n".join(texts.get(page_no, []))
                (target / f"page_{page_no:02d}.txt").write_text(page_text, encoding="utf-8")
                pm = by_no.setdefault(page_no, PageMeta(page_no=page_no))
                _analyse(page_text, pm)
            if 0 in texts:
                (target / "page_00_unplaced.txt").write_text("\n".join(texts[0]), encoding="utf-8")
            fm.page_meta = [by_no[k] for k in sorted(by_no)]
            gold_path = path.with_name(path.stem + ".gold.txt")
            if gold_path.exists():
                fm.gold = _gold_report(gold_path.read_text(encoding="utf-8", errors="replace"), markdown)
                (target / "gold.txt").write_text(gold_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - record the failure per file
            log.exception("conversion failed for %s", fm.file)
            fm.status, fm.error = "error", f"{type(exc).__name__}: {exc}"
        (target / "meta.json").write_text(json.dumps(asdict(fm), ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(fm)
    (out_dir / "summary.json").write_text(
        json.dumps({"docling_version": docling.__version__ if hasattr(docling, "__version__") else _pkg_version("docling"),
                    "variant": asdict(variant), "files": [asdict(r) for r in results]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "summary.md").write_text(_summary_md(results), encoding="utf-8")
    return results


def _pkg_version(name: str) -> str:
    from importlib.metadata import version

    return version(name)


def _summary_md(results: List[FileMeta]) -> str:
    rows = ["| # | file | kind | pages | dpi | text layer | s | words | umlauts | amounts | dates | GZ | garbled lines | merged | gold word rec. | gold line rec. |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        dpis = sorted({pm.dpi for pm in r.page_meta if pm.dpi})
        dpi = f"{dpis[0]:.0f}" if len(dpis) == 1 else (f"{dpis[0]:.0f}-{dpis[-1]:.0f}" if dpis else "?")
        tl = sum(pm.text_layer_chars for pm in r.page_meta)
        words = sum(pm.ocr_words for pm in r.page_meta)
        uml = sum(pm.umlaut_chars for pm in r.page_meta)
        amounts = sum(len(pm.amounts) for pm in r.page_meta)
        dates = sum(len(pm.dates) for pm in r.page_meta)
        gz = sum(len(pm.case_numbers) for pm in r.page_meta)
        garbled = f"{sum(pm.garbled_lines for pm in r.page_meta)}/{sum(pm.lines for pm in r.page_meta)}"
        merged = sum(pm.merged_tokens for pm in r.page_meta)
        gw = r.gold["word_recall"] if r.gold else ""
        gl = r.gold["line_recall"] if r.gold else ""
        status = r.file if r.status == "ok" else f"{r.file} **{r.status}: {r.error[:60]}**"
        rows.append(f"| {i} | {status} | {r.kind} | {r.pages} | {dpi} | {tl} | {r.convert_seconds} | {words} | {uml} | {amounts} | {dates} | {gz} | {garbled} | {merged} | {gw} | {gl} |")
    return "\n".join(rows) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("in_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--render-dpi", type=int, default=110, help="DPI of the page PNGs written for spot-checks")
    ap.add_argument("--limit", type=int, default=None, help="stop after N input files")
    ap.add_argument("-v", "--verbose", action="store_true")
    exp = ap.add_argument_group("what-if experiments (leave unset for the production configuration)")
    exp.add_argument("--ocr-scale", type=float, default=None, help="docling ocr_options.scale (production 3.0)")
    exp.add_argument("--force-full-page-ocr", action="store_true", default=None)
    exp.add_argument("--lang", default=None, help="comma-separated OCR languages, e.g. german,english")
    exp.add_argument("--engine", default=None, help="OCR engine kind registered in docling (rapidocr, easyocr, tesseract, ...)")
    exp.add_argument("--upscale", type=float, default=None, help="resample image inputs by this factor before OCR")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("docling").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    variant = Variant(ocr_scale=args.ocr_scale, force_full_page_ocr=args.force_full_page_ocr,
                      lang=args.lang.split(",") if args.lang else None, engine=args.engine, upscale=args.upscale)
    log.info("configuration: %s", "production" if variant.is_production() else asdict(variant))
    results = process(args.in_dir, args.out_dir, args.render_dpi, args.limit, variant)
    ok = sum(1 for r in results if r.status == "ok")
    log.info("done: %d files, %d ok, %d pages, outputs in %s", len(results), ok, sum(r.pages for r in results), args.out_dir)
    print((args.out_dir / "summary.md").read_text(encoding="utf-8"))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
