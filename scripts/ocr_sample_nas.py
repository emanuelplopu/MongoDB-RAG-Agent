"""Draw a read-only OCR test sample (scanned pages + phone photos) from a document share.

Walks randomly chosen case folders of a share (mounted read-only, e.g. the CIFS volume
``recallhub_share_daten`` at ``/data/nas``), classifies the PDF / image files it finds and
copies a stratified sample into a bench folder::

    <out>/scans/     scanned PDF pages (page subsets re-saved with pypdfium2), scan images
    <out>/refscans/  scanned pages whose PDF carried a scanner text layer, re-saved as
                     image-only PDFs so the OCR path must read them; the text layer goes
                     next to them as <stem>.ref.txt (a reference, not ground truth)
    <out>/rendered/  born-digital pages rendered to image-only PDFs (exact text reference
                     in <stem>.ref.txt): the clean-input control
    <out>/photos/    phone photos (camera EXIF or phone aspect, no scanner DPI tag)
    <out>/small/     small images (icons, signatures, logos from mail attachments)
    <out>/manifest.jsonl, <out>/manifest.csv, <out>/sample.log

The share is never written to. Run inside the ingestion-worker image, which has
pypdfium2 and Pillow::

    docker run --rm -v recallhub_share_daten:/data/nas:ro -v <bench>:/data/bench \
      -v <repo>/scripts/ocr_sample_nas.py:/app/ocr_sample_nas.py:ro \
      --entrypoint python recallhub-ingestion-worker:latest /app/ocr_sample_nas.py /data/nas /data/bench/sample
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PDF_EXT = {".pdf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
PHOTO_EXT = {".jpg", ".jpeg"}

log = logging.getLogger("ocr_sample_nas")


@dataclass
class Entry:
    id: str
    kind: str  # scan_pdf | scan_image | refscan | rendered | photo | small
    area: str  # akten | jurx | lit | texte
    source: str  # path relative to the share root
    out_file: str  # path relative to the out dir
    source_pages: int = 0
    pages: List[int] = field(default_factory=list)  # 1-based page numbers taken from the source
    bytes: int = 0
    width_px: int = 0
    height_px: int = 0
    dpi: float = 0.0
    text_layer_chars: List[int] = field(default_factory=list)
    exif_make: str = ""
    exif_model: str = ""
    exif_orientation: int = 0
    note: str = ""


# --------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------

def _object_bounds(obj: Any) -> Tuple[float, float, float, float]:
    """(left, bottom, right, top) of a page object; pypdfium2 >= 5 renamed ``get_pos`` to ``get_bounds``."""
    getter = getattr(obj, "get_bounds", None) or getattr(obj, "get_pos")
    return tuple(float(v) for v in getter())  # type: ignore[return-value]


def _pdf_probe(path: Path, max_pages: int = 12) -> Optional[Dict[str, Any]]:
    """Page count, per-page text-layer chars and the largest embedded image of the first pages."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:  # noqa: BLE001 - encrypted / broken files are skipped
        log.debug("cannot open %s: %s", path, exc)
        return None
    try:
        n = len(pdf)
        if n == 0:
            return None
        chars: List[int] = []
        img_px: List[Tuple[int, int, float]] = []
        for i in range(min(n, max_pages)):
            page = pdf[i]
            try:
                chars.append(len(page.get_textpage().get_text_range().strip()))
            except Exception:  # noqa: BLE001
                chars.append(-1)
            best = (0, 0, 0.0)
            page_w_pt = page.get_width()
            try:
                for obj in page.get_objects(max_depth=3):
                    if obj.type != pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                        continue
                    try:
                        meta = obj.get_metadata()
                        w, h = meta.width, meta.height
                    except Exception:  # noqa: BLE001
                        continue
                    if w * h > best[0] * best[1]:
                        left, bottom, right, top = _object_bounds(obj)
                        placed_w = max(right - left, 1.0)
                        dpi = getattr(meta, "horizontal_dpi", 0) or (w / (placed_w / 72.0))
                        cover = placed_w / max(page_w_pt, 1.0)
                        best = (w, h, float(dpi)) if cover >= 0.6 else best
            except Exception:  # noqa: BLE001
                pass
            img_px.append(best)
        return {"pages": n, "chars": chars, "img": img_px}
    finally:
        pdf.close()


def classify_pdf(probe: Dict[str, Any]) -> str:
    """scan (no text layer, full-page image), refscan (full-page image + scanner text), digital, other."""
    first = probe["img"][0]
    has_image = first[0] > 0
    chars = probe["chars"][0]
    if has_image and chars <= 20:
        return "scan"
    if has_image and chars > 200:
        return "refscan"
    if not has_image and chars > 200:
        return "digital"
    return "other"


def _image_probe(path: Path) -> Optional[Dict[str, Any]]:
    from PIL import Image, ImageOps

    try:
        with Image.open(path) as img:
            w, h = img.size
            dpi = img.info.get("dpi") or (0, 0)
            dpi_x = float(dpi[0]) if dpi and dpi[0] else 0.0
            make = model = ""
            orientation = 0
            try:
                exif = img.getexif()
                make = str(exif.get(271, "")).strip("\x00 ")
                model = str(exif.get(272, "")).strip("\x00 ")
                orientation = int(exif.get(274, 0) or 0)
            except Exception:  # noqa: BLE001
                pass
            _ = ImageOps
            return {"w": w, "h": h, "dpi": dpi_x, "make": make, "model": model, "orientation": orientation}
    except Exception as exc:  # noqa: BLE001
        log.debug("cannot open image %s: %s", path, exc)
        return None


def classify_image(path: Path, probe: Dict[str, Any]) -> str:
    """photo | scan_image | small."""
    w, h = probe["w"], probe["h"]
    long_side, short_side = max(w, h), max(min(w, h), 1)
    aspect = long_side / short_side
    if long_side < 1000:
        return "small"
    if probe["make"] or probe["model"]:
        return "photo"
    # Scanner output: a DPI tag of 150+ or an A4-like aspect at page resolution, no camera EXIF
    if probe["dpi"] >= 150 or (1.36 <= aspect <= 1.46 and long_side >= 2000):
        return "scan_image"
    if path.suffix.lower() in PHOTO_EXT and 1.2 <= aspect <= 1.85 and long_side >= 1500:
        return "photo"
    return "scan_image" if long_side >= 1500 else "small"


# --------------------------------------------------------------------------------------
# Writers (bench copies)
# --------------------------------------------------------------------------------------

def _slug(path: Path) -> str:
    return re.sub(r"[^\w\-]+", "_", path.stem)[:40].strip("_") or "file"


def _save_pdf_pages(src: Path, pages0: List[int], dst: Path) -> int:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(src))
    try:
        new = pdfium.PdfDocument.new()
        new.import_pages(pdf, pages=pages0)
        new.save(str(dst))
        new.close()
    finally:
        pdf.close()
    return dst.stat().st_size


def _render_pages_to_image_pdf(src: Path, pages0: List[int], dst: Path, dpi: int, ref_path: Path) -> Dict[str, Any]:
    """Render pages to bitmaps and write an image-only PDF plus the text layer as reference."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(src))
    images = []
    ref_parts: List[str] = []
    try:
        for i in pages0:
            page = pdf[i]
            bitmap = page.render(scale=dpi / 72.0)
            images.append(bitmap.to_pil().convert("RGB"))
            try:
                ref_parts.append(page.get_textpage().get_text_range())
            except Exception:  # noqa: BLE001
                ref_parts.append("")
    finally:
        pdf.close()
    first, rest = images[0], images[1:]
    first.save(str(dst), "PDF", resolution=dpi, save_all=True, append_images=rest, quality=92)
    ref_path.write_text("\n\f\n".join(ref_parts), encoding="utf-8")
    return {"w": first.width, "h": first.height}


# --------------------------------------------------------------------------------------
# Walk
# --------------------------------------------------------------------------------------

def _case_folders(root: Path, rng: random.Random) -> List[Tuple[str, Path]]:
    pools: List[Tuple[str, Path]] = []
    for area, sub in (("akten", "ADVOKAT Akten"), ("jurx", "jurXpert/documents")):
        base = root / sub
        if base.is_dir():
            folders = [p for p in base.iterdir() if p.is_dir()]
            rng.shuffle(folders)
            pools.extend((area, p) for p in folders)
    # LITERATUR / Texte: first-level sub-folders as units
    for area, sub in (("lit", "LITERATUR"), ("texte", "Texte")):
        base = root / sub
        if base.is_dir():
            folders = [p for p in base.iterdir() if p.is_dir()]
            rng.shuffle(folders)
            pools.extend((area, p) for p in folders)
    rng.shuffle(pools)
    return pools


def _walk_files(folder: Path, max_files: int = 4000) -> Iterable[Path]:
    count = 0
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "~"))]
        for name in filenames:
            ext = Path(name).suffix.lower()
            if ext in PDF_EXT or ext in IMAGE_EXT:
                yield Path(dirpath) / name
                count += 1
                if count >= max_files:
                    return


class Sampler:
    def __init__(self, root: Path, out: Path, rng: random.Random, args: argparse.Namespace) -> None:
        self.root, self.out, self.rng, self.args = root, out, rng, args
        self.entries: List[Entry] = []
        self.pages = {"scan": 0, "refscan": 0, "rendered": 0}
        self.count = {"photo": 0, "small": 0, "scan_image": 0}
        self.area_pages: Dict[str, int] = {"akten": 0, "jurx": 0, "lit": 0, "texte": 0}
        self.area_quota = {"akten": 0.50, "jurx": 0.32, "lit": 0.10, "texte": 0.08}
        self.seq = 0
        self.seen: Dict[str, int] = {}
        for sub in ("scans", "refscans", "rendered", "photos", "small"):
            (out / sub).mkdir(parents=True, exist_ok=True)
        self.manifest = (out / "manifest.jsonl").open("a", encoding="utf-8")

    def done(self) -> bool:
        a = self.args
        return (
            self.pages["scan"] >= a.scan_pages
            and self.pages["refscan"] >= a.ref_pages
            and self.pages["rendered"] >= a.rendered_pages
            and self.count["photo"] >= a.photos
            and self.count["small"] >= a.small
        )

    def _next_id(self) -> str:
        self.seq += 1
        return f"{self.seq:05d}"

    def _record(self, e: Entry) -> None:
        self.entries.append(e)
        self.manifest.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")
        self.manifest.flush()

    def _pick_pages(self, n: int, want: int) -> List[int]:
        """Up to ``want`` pages, 0-based: the first pages most of the time, a random window otherwise."""
        want = min(want, n)
        if n <= want or self.rng.random() < 0.6:
            return list(range(want))
        start = self.rng.randint(0, n - want)
        return list(range(start, start + want))

    def scan_area_open(self, area: str) -> bool:
        return self.area_pages[area] < self.area_quota[area] * self.args.scan_pages * 1.15

    # -- per-file handlers -------------------------------------------------------------

    def handle_pdf(self, path: Path, area: str) -> None:
        a = self.args
        need_scan = self.pages["scan"] < a.scan_pages and self.scan_area_open(area)
        need_ref = self.pages["refscan"] < a.ref_pages
        need_rendered = self.pages["rendered"] < a.rendered_pages
        if not (need_scan or need_ref or need_rendered):
            return
        probe = _pdf_probe(path)
        if not probe:
            return
        cls = classify_pdf(probe)
        self.seen[f"pdf_{cls}"] = self.seen.get(f"pdf_{cls}", 0) + 1
        rel = str(path.relative_to(self.root))
        if cls == "scan" and need_scan:
            pages0 = self._pick_pages(probe["pages"], a.max_pages_per_file)
            eid = self._next_id()
            dst = self.out / "scans" / f"{eid}_{area}_{_slug(path)}.pdf"
            size = _save_pdf_pages(path, pages0, dst)
            w, h, dpi = probe["img"][0]
            self._record(Entry(eid, "scan_pdf", area, rel, str(dst.relative_to(self.out)), probe["pages"],
                               [p + 1 for p in pages0], size, w, h, round(dpi, 1),
                               [probe["chars"][p] for p in pages0 if p < len(probe["chars"])]))
            self.pages["scan"] += len(pages0)
            self.area_pages[area] += len(pages0)
        elif cls == "refscan" and need_ref:
            pages0 = self._pick_pages(probe["pages"], a.max_pages_per_file)
            w, h, dpi = probe["img"][0]
            render_dpi = int(min(max(dpi, 150), 300)) if dpi else 300
            eid = self._next_id()
            dst = self.out / "refscans" / f"{eid}_{area}_{_slug(path)}.pdf"
            info = _render_pages_to_image_pdf(path, pages0, dst, render_dpi, dst.with_suffix(".ref.txt"))
            self._record(Entry(eid, "refscan", area, rel, str(dst.relative_to(self.out)), probe["pages"],
                               [p + 1 for p in pages0], dst.stat().st_size, info["w"], info["h"], render_dpi,
                               [probe["chars"][p] for p in pages0 if p < len(probe["chars"])],
                               note=f"scanner text layer; embedded image {w}x{h}@{dpi:.0f}dpi"))
            self.pages["refscan"] += len(pages0)
        elif cls == "digital" and need_rendered:
            pages0 = self._pick_pages(probe["pages"], 2)
            eid = self._next_id()
            dst = self.out / "rendered" / f"{eid}_{area}_{_slug(path)}.pdf"
            info = _render_pages_to_image_pdf(path, pages0, dst, 300, dst.with_suffix(".ref.txt"))
            self._record(Entry(eid, "rendered", area, rel, str(dst.relative_to(self.out)), probe["pages"],
                               [p + 1 for p in pages0], dst.stat().st_size, info["w"], info["h"], 300,
                               [probe["chars"][p] for p in pages0 if p < len(probe["chars"])],
                               note="born-digital page rendered at 300 dpi; exact text reference"))
            self.pages["rendered"] += len(pages0)

    def handle_image(self, path: Path, area: str) -> None:
        a = self.args
        probe = _image_probe(path)
        if not probe:
            return
        cls = classify_image(path, probe)
        self.seen[f"img_{cls}"] = self.seen.get(f"img_{cls}", 0) + 1
        rel = str(path.relative_to(self.root))
        if cls == "photo":
            if self.count["photo"] >= a.photos:
                return
            sub, kind = "photos", "photo"
        elif cls == "small":
            if self.count["small"] >= a.small:
                return
            sub, kind = "small", "small"
        else:
            if self.pages["scan"] >= a.scan_pages or self.count["scan_image"] >= a.scan_images:
                return
            sub, kind = "scans", "scan_image"
        eid = self._next_id()
        dst = self.out / sub / f"{eid}_{area}_{_slug(path)}{path.suffix.lower()}"
        shutil.copyfile(path, dst)
        self._record(Entry(eid, kind, area, rel, str(dst.relative_to(self.out)), 1, [1], dst.stat().st_size,
                           probe["w"], probe["h"], round(probe["dpi"], 1), [], probe["make"], probe["model"],
                           probe["orientation"]))
        if kind == "scan_image":
            self.pages["scan"] += 1
            self.area_pages[area] += 1
            self.count["scan_image"] += 1
        else:
            self.count[kind] += 1

    # -- main loop -----------------------------------------------------------------------

    def run(self) -> None:
        a = self.args
        t0 = time.time()
        folders = _case_folders(self.root, self.rng)
        log.info("%d candidate folders", len(folders))
        for idx, (area, folder) in enumerate(folders):
            if self.done() or idx >= a.max_folders or time.time() - t0 > a.max_minutes * 60:
                break
            files = list(_walk_files(folder))
            self.rng.shuffle(files)
            pdfs = [f for f in files if f.suffix.lower() in PDF_EXT][: a.pdfs_per_folder]
            imgs = [f for f in files if f.suffix.lower() in IMAGE_EXT][: a.images_per_folder]
            for f in pdfs:
                try:
                    self.handle_pdf(f, area)
                except Exception as exc:  # noqa: BLE001
                    log.warning("pdf %s: %s", f, exc)
            for f in imgs:
                try:
                    self.handle_image(f, area)
                except Exception as exc:  # noqa: BLE001
                    log.warning("image %s: %s", f, exc)
            if idx % 25 == 0:
                log.info("folder %d/%d (%s) pages=%s images=%s area=%s seen=%s %.0fs", idx, len(folders), area,
                         self.pages, self.count, self.area_pages, self.seen, time.time() - t0)
        self.manifest.close()
        self._write_csv()
        log.info("done: pages=%s images=%s area=%s seen=%s entries=%d in %.0fs", self.pages, self.count,
                 self.area_pages, self.seen, len(self.entries), time.time() - t0)

    def _write_csv(self) -> None:
        with (self.out / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "kind", "area", "out_file", "source", "source_pages", "pages", "bytes", "width_px",
                        "height_px", "dpi", "text_layer_chars", "exif_make", "exif_model", "exif_orientation", "note"])
            for e in self.entries:
                w.writerow([e.id, e.kind, e.area, e.out_file, e.source, e.source_pages, " ".join(map(str, e.pages)),
                            e.bytes, e.width_px, e.height_px, e.dpi, " ".join(map(str, e.text_layer_chars)),
                            e.exif_make, e.exif_model, e.exif_orientation, e.note])


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="share root (read-only)")
    ap.add_argument("out", type=Path, help="bench output folder")
    ap.add_argument("--scan-pages", type=int, default=1000)
    ap.add_argument("--scan-images", type=int, default=150, help="of the scan pages, at most this many scan images")
    ap.add_argument("--ref-pages", type=int, default=150, help="pages with a scanner text layer (reference)")
    ap.add_argument("--rendered-pages", type=int, default=100, help="born-digital pages rendered as images (control)")
    ap.add_argument("--photos", type=int, default=1000)
    ap.add_argument("--small", type=int, default=60)
    ap.add_argument("--max-pages-per-file", type=int, default=4)
    ap.add_argument("--pdfs-per-folder", type=int, default=4)
    ap.add_argument("--images-per-folder", type=int, default=8)
    ap.add_argument("--max-folders", type=int, default=4000)
    ap.add_argument("--max-minutes", type=float, default=90)
    ap.add_argument("--seed", type=int, default=20261016)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(args.out / "sample.log", encoding="utf-8")])
    Sampler(args.root, args.out, random.Random(args.seed), args).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
