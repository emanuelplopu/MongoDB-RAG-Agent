"""RecallHub's Tesseract CLI OCR model for Docling.

Docling's ``TesseractOcrCliModel`` runs orientation detection (``tesseract --psm 0
-l osd``) on every OCR rectangle and rotates the rectangle by whatever angle comes
back, without looking at the reported *orientation confidence*. On small or
sparse rectangles (a centred bold line, a Courier court extract, a stamp) that
guess is frequently wrong and the block - or the whole page - is then read
upside down as letter soup (``*qaynze6yoanp`` for ``durchgeführt.``). On the
2026-10-16 scale sample every rotation Tesseract proposed with a confidence
below 1 on clean upright pages was wrong, while genuinely rotated scans come
back with confidences of 10-20; the band in between holds both.

This subclass therefore

1. applies a detected rotation outright only when its confidence reaches
   ``osd_min_confidence``;
2. for a suppressed rotation OCRs the rectangle both ways (``rotation_retry``)
   and keeps the reading with the higher character-weighted confidence, mapping
   the boxes of a rotated reading back into the unrotated rectangle;
3. OCRs the whole page when the layout model found no text region on a page
   without a text layer (``empty_page_full_ocr``) - sparse pages such as an ERV
   footer sheet otherwise yield nothing;
4. detects the orientation once per page (``osd_once_per_page``) instead of once
   per OCR rectangle, and not at all on pages whose OCR regions are tiny
   (``osd_min_area_pt``): per-rectangle OSD on the diejuristin corpus failed with
   "Too few characters" on 37 % of the rectangles (logos, stamps, signatures) and
   started one Tesseract process per rectangle.

The model is registered with Docling's OCR factory under its own options kind
(``tesseract_recallhub``) so a pipeline that carries
:class:`RecallHubTesseractCliOcrOptions` picks it up; nothing changes for the
stock ``tesseract`` kind.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from typing import ClassVar, Dict, List, Literal, Optional, Tuple, Type

import pandas as pd
from docling.datamodel.base_models import Page
from docling.datamodel.pipeline_options import OcrOptions, TesseractCliOcrOptions
from docling.models.stages.ocr.tesseract_ocr_cli_model import TesseractOcrCliModel
from docling_core.types.doc import BoundingBox, CoordOrigin
from pydantic import Field

_log = logging.getLogger(__name__)

_OSD_ORIENTATION_KEY = "Orientation in degrees"
_OSD_ROTATE_KEY = "Rotate"
_OSD_CONFIDENCE_KEY = "Orientation confidence"
# A rotated reading replaces the unrotated one only when it is this much better.
_RETRY_MARGIN = 1.25


class RecallHubTesseractCliOcrOptions(TesseractCliOcrOptions):
    """``TesseractCliOcrOptions`` plus the orientation guard and the empty-page fallback."""

    kind: ClassVar[Literal["tesseract_recallhub"]] = "tesseract_recallhub"  # type: ignore[assignment]
    osd_min_confidence: float = Field(
        default=0.0,
        description=(
            "Apply the rotation Tesseract's orientation detection reports for an OCR "
            "rectangle outright only when its 'Orientation confidence' is at least this "
            "value; 0 applies every detected rotation (Docling's behaviour)."
        ),
    )
    rotation_retry: bool = Field(
        default=True,
        description=(
            "For a rotation suppressed by osd_min_confidence, OCR the rectangle both ways "
            "and keep the reading with the higher character-weighted confidence."
        ),
    )
    empty_page_full_ocr: bool = Field(
        default=True,
        description=(
            "OCR the whole page when the layout model found no text region on a page "
            "that has no text layer (sparse pages, footers, stamps)."
        ),
    )
    rect_dilation: int = Field(
        default=20,
        description=(
            "Dilation (page points) applied to the layout clusters before neighbouring ones "
            "are merged into one OCR rectangle; Docling's 20 merges stacked tables into one "
            "crop on which Tesseract drops columns, a small value keeps them separate."
        ),
    )
    osd_once_per_page: bool = Field(
        default=True,
        description=(
            "Run orientation detection once on the whole page and apply its result to every "
            "OCR rectangle of that page, instead of one 'tesseract --psm 0' per rectangle. "
            "Per-rectangle OSD failed on 37 % of the rectangles of the diejuristin corpus "
            "('Too few characters' on logos, stamps and signatures, ~8,000 ERROR lines per 3 h) "
            "and cost one Tesseract process per rectangle."
        ),
    )
    osd_min_area_pt: float = Field(
        default=20_000.0,
        description=(
            "Skip orientation detection on a page whose OCR rectangles cover less than this "
            "many square page points in total (a logo or a signature on a born-digital page: "
            "too few characters for OSD anyway); the page is read upright. 200 x 100 pt by default."
        ),
    )


@dataclass
class _PageOsd:
    """Orientation detection of one page, shared by its OCR rectangles."""

    page: Page
    rects: List[BoundingBox]
    done: bool = False
    frame: Optional[pd.DataFrame] = None
    suppressed_angle: Optional[int] = None  # guard-suppressed rotation (rectangles retry both ways)


def _no_rotation_frame() -> pd.DataFrame:
    """An OSD result frame that means "upright, nothing to rotate"."""
    return pd.DataFrame(
        {"key": [_OSD_ORIENTATION_KEY, _OSD_ROTATE_KEY, _OSD_CONFIDENCE_KEY], "value": [" 0", " 0", " 0.00"]}
    )


class RecallHubTesseractCliOcrModel(TesseractOcrCliModel):
    """Tesseract CLI model with verified orientation and a sparse-page fallback."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: D107 - same signature as the base class
        super().__init__(*args, **kwargs)
        self.options: RecallHubTesseractCliOcrOptions
        self._suppressed: Dict[str, int] = {}  # image file -> suppressed OSD angle
        self._page_osd: Optional[_PageOsd] = None  # the page whose rectangles are being OCRed
        # Diagnostics (read by the harness)
        self.osd_guarded = 0
        self.osd_page_runs = 0
        self.osd_skipped_tiny = 0
        self.osd_failed = 0
        self.retries_rotated = 0
        self.retries_kept = 0
        self.empty_page_fallbacks = 0

    # -- orientation guard ------------------------------------------------------------

    def _guard(self, df_osd: pd.DataFrame) -> Tuple[pd.DataFrame, Optional[int]]:
        """Apply ``osd_min_confidence``: a low-confidence rotation is zeroed and returned for the retry."""
        threshold = float(getattr(self.options, "osd_min_confidence", 0.0) or 0.0)
        if threshold <= 0:
            return df_osd, None
        confidence = _osd_value(df_osd, _OSD_CONFIDENCE_KEY)
        orientation = _osd_value(df_osd, _OSD_ORIENTATION_KEY)
        if orientation and confidence is not None and confidence < threshold:
            self.osd_guarded += 1
            # Docling's convention: Tesseract reports clockwise degrees, the pipeline rotates
            # with `image.rotate(-parsed)` where parsed = (-reported) % 360 (tesseract_utils).
            suppressed = (-int(orientation)) % 360
            _log.debug(
                "OSD rotation %s suppressed (confidence %.2f < %.2f)", int(orientation), confidence, threshold
            )
            keys = df_osd["key"].to_numpy()
            df_osd.loc[keys == _OSD_ORIENTATION_KEY, "value"] = " 0"
            df_osd.loc[keys == _OSD_ROTATE_KEY, "value"] = " 0"
            return df_osd, suppressed
        return df_osd, None

    def _perform_osd(self, ifilename: str) -> pd.DataFrame:
        """Orientation of the rectangle image ``ifilename``.

        With ``osd_once_per_page`` (default) this is the page's orientation, detected once on
        the whole page image when its first rectangle asks for it; the per-rectangle call
        Docling makes costs nothing more. Without it, one OSD per rectangle as in Docling.
        """
        state = getattr(self, "_page_osd", None)
        if not getattr(self.options, "osd_once_per_page", False) or state is None:
            df_osd, suppressed = self._guard(super()._perform_osd(ifilename))
            if suppressed:
                self._suppressed[ifilename] = suppressed
            return df_osd
        if not state.done:
            state.done = True
            state.frame, state.suppressed_angle = self._detect_page_orientation(state)
        if state.suppressed_angle:
            self._suppressed[ifilename] = state.suppressed_angle
        return state.frame.copy()

    def _detect_page_orientation(self, state: _PageOsd) -> Tuple[pd.DataFrame, Optional[int]]:
        """Run OSD on the whole page image (skipped for pages with only tiny OCR regions)."""
        page = state.page
        area = sum(rect.area() for rect in state.rects)
        min_area = float(getattr(self.options, "osd_min_area_pt", 0.0) or 0.0)
        if area < min_area:
            self.osd_skipped_tiny += 1
            _log.debug("page %s: OCR regions cover %.0f pt² (< %.0f); OSD skipped", page.page_no, area, min_area)
            return _no_rotation_frame(), None
        if page._backend is None:
            return _no_rotation_frame(), None
        fd, tmp_name = tempfile.mkstemp(suffix=".png", prefix="recallhub-osd-")
        os.close(fd)
        try:
            page._backend.get_page_image(scale=self.scale).save(tmp_name)
            self.osd_page_runs += 1
            df_osd = super()._perform_osd(tmp_name)
        except subprocess.CalledProcessError as exc:
            # "Too few characters" and the like: the page is read upright, as Docling would
            # have read the rectangle; a debug line instead of Docling's ERROR per rectangle
            self.osd_failed += 1
            stderr = exc.stderr.decode("utf-8", "replace").strip() if isinstance(exc.stderr, bytes) else exc.stderr
            _log.debug("page %s: OSD failed (%s); reading it upright", page.page_no, (stderr or "")[:200])
            return _no_rotation_frame(), None
        except Exception as exc:  # noqa: BLE001 - OSD is advisory
            self.osd_failed += 1
            _log.warning("page %s: OSD could not run (%s); reading it upright", page.page_no, exc)
            return _no_rotation_frame(), None
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        return self._guard(df_osd)

    def _run_tesseract(self, ifilename: str, osd: Optional[pd.DataFrame]):
        df_plain = super()._run_tesseract(ifilename, osd)
        angle = self._suppressed.pop(ifilename, None)
        if not angle or not getattr(self.options, "rotation_retry", False):
            return df_plain
        try:
            df_rot = self._rotated_reading(ifilename, angle, osd)
        except Exception as exc:  # noqa: BLE001 - the unrotated reading is always available
            _log.warning("rotated OCR retry failed (%s); keeping the unrotated reading", exc)
            return df_plain
        self.retries_rotated += 1
        plain_score, rot_score = _reading_score(df_plain), _reading_score(df_rot)
        keep = rot_score > plain_score * _RETRY_MARGIN
        _log.debug(
            "rotation retry (%d deg): rotated score %.0f %s unrotated %.0f; rotated: %s | unrotated: %s",
            angle, rot_score, "KEPT over" if keep else "rejected against", plain_score,
            " ".join(df_rot["text"].astype(str).tolist()[:8]), " ".join(df_plain["text"].astype(str).tolist()[:8]),
        )
        if keep:
            self.retries_kept += 1
            return df_rot
        return df_plain

    def _rotated_reading(self, ifilename: str, angle: int, osd: Optional[pd.DataFrame]) -> pd.DataFrame:
        """OCR ``ifilename`` rotated like Docling would have, boxes mapped back to the unrotated image.

        ``angle`` is Docling's parsed orientation (``(-tesseract_degrees) % 360``); the image is
        rotated with ``rotate(-angle, expand=True)`` exactly as ``TesseractOcrCliModel.__call__`` does.
        """
        from PIL import Image

        with Image.open(ifilename) as image:
            width, height = image.size
            rotated = image.rotate(-angle, expand=True)
            fd, tmp_name = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            try:
                rotated.save(tmp_name)
                _log.debug("rotation retry: %s %s -> %s %s", ifilename, image.size, tmp_name, rotated.size)
                df_rot = super()._run_tesseract(tmp_name, osd)
            finally:
                if os.path.exists(tmp_name):
                    os.remove(tmp_name)
        return map_rotated_boxes(df_rot, angle, width, height)

    # -- OCR rectangles -----------------------------------------------------------------

    def _deduplicate_rects(self, size, rects, dilation_size=0):  # type: ignore[override]
        """Merge overlapping cluster rectangles with the configured dilation instead of Docling's 20."""
        configured = getattr(self.options, "rect_dilation", None)
        if configured is not None and dilation_size:
            dilation_size = max(int(configured), 0)
        return super()._deduplicate_rects(size, rects, dilation_size=dilation_size)

    def get_ocr_rects(self, page: Page) -> List[BoundingBox]:
        rects = self._ocr_rects(page)
        # Docling asks for a page's rectangles right before it OCRs them: the page's
        # orientation is detected once for all of them (see _perform_osd)
        self._page_osd = _PageOsd(page=page, rects=list(rects))
        return rects

    def _ocr_rects(self, page: Page) -> List[BoundingBox]:
        rects = super().get_ocr_rects(page)
        if rects or not getattr(self.options, "empty_page_full_ocr", False):
            return rects
        if page.size is None or page._backend is None:
            return rects
        try:
            has_text_layer = any(True for _ in page._backend.get_text_cells())
        except Exception:  # noqa: BLE001
            has_text_layer = False
        if has_text_layer:
            return rects
        self.empty_page_fallbacks += 1
        _log.debug("no OCR region on page %s without text layer; OCRing the full page", page.page_no)
        return [BoundingBox(l=0, t=0, r=page.size.width, b=page.size.height, coord_origin=CoordOrigin.TOPLEFT)]

    @classmethod
    def get_options_type(cls) -> Type[OcrOptions]:
        return RecallHubTesseractCliOcrOptions


# --------------------------------------------------------------------------------------
# Helpers (pure; unit-tested)
# --------------------------------------------------------------------------------------

def _osd_value(df_osd: pd.DataFrame, key: str) -> Optional[float]:
    rows = df_osd.loc[df_osd["key"] == key, "value"]
    if rows.empty:
        return None
    try:
        return float(str(rows.iloc[0]).strip())
    except ValueError:
        return None


def _reading_score(df: pd.DataFrame) -> float:
    """Character-weighted confidence of a Tesseract TSV reading (0 for an empty reading)."""
    if df is None or len(df) == 0:
        return 0.0
    conf = pd.to_numeric(df["conf"], errors="coerce").fillna(0.0).clip(lower=0.0)
    chars = df["text"].astype(str).str.strip().str.len()
    return float((conf * chars).sum())


def map_rotated_boxes(df: pd.DataFrame, angle: int, width: int, height: int) -> pd.DataFrame:
    """Map TSV boxes read on an image rotated clockwise by ``angle`` back to the unrotated image.

    Docling rotates with ``image.rotate(-angle, expand=True)`` (clockwise by ``angle``).
    ``width``/``height`` are the unrotated image size.

    Args:
        df: Tesseract TSV rows with ``left``, ``top``, ``width``, ``height`` columns.
        angle: 90, 180 or 270.
        width: Unrotated image width in pixels.
        height: Unrotated image height in pixels.

    Returns:
        A copy of ``df`` with the box columns in unrotated-image coordinates.
    """
    out = df.copy()
    left = pd.to_numeric(out["left"], errors="coerce").fillna(0.0)
    top = pd.to_numeric(out["top"], errors="coerce").fillna(0.0)
    w = pd.to_numeric(out["width"], errors="coerce").fillna(0.0)
    h = pd.to_numeric(out["height"], errors="coerce").fillna(0.0)
    angle = angle % 360
    if angle == 180:
        new_left, new_top, new_w, new_h = width - left - w, height - top - h, w, h
    elif angle == 90:
        # clockwise 90: rotated (x', y') = (H - y, x)  ->  x = y', y = H - x'
        new_left, new_top, new_w, new_h = top, height - left - w, h, w
    elif angle == 270:
        # counter-clockwise 90: rotated (x', y') = (y, W - x)  ->  x = W - y', y = x'
        new_left, new_top, new_w, new_h = width - top - h, left, h, w
    else:
        return out
    out["left"], out["top"], out["width"], out["height"] = new_left, new_top, new_w, new_h
    return out


_registered = False


def register_tesseract_model() -> None:
    """Register the model with Docling's OCR factory (idempotent).

    The factory is an ``lru_cache`` keyed on the keyword call the pipeline makes,
    so the registration has to use the same call form.
    """
    global _registered
    if _registered:
        return
    from docling.models.factories import get_ocr_factory

    for allow_external in (False, True):
        factory = get_ocr_factory(allow_external_plugins=allow_external)
        if RecallHubTesseractCliOcrOptions not in factory.classes:
            factory.register(RecallHubTesseractCliOcrModel, "recallhub", __name__)
    _registered = True


# --------------------------------------------------------------------------------------
# OpenMP threads per Tesseract process
# --------------------------------------------------------------------------------------
#
# Tesseract 5 is built with OpenMP and takes up to four threads per process. That is
# fine for one or two conversions at a time; with many parallel conversions the
# processes oversubscribe the CPU and throughput collapses (measured 2026-10-23 on
# 23 cores: 8 parallel conversions 1.4 pages/s, 12 parallel 0.3 pages/s; capped to
# one thread each, 16 parallel reach 2.0 pages/s). The limit therefore follows the
# size of the ingestion thread pool. It reaches Tesseract through a generated wrapper
# command that exports OMP_THREAD_LIMIT for the Tesseract process only: exporting it
# in the ingestion process itself would also cap PyTorch's threads for the layout model.

OMP_LIMIT_ENV = "OCR_OMP_THREAD_LIMIT"
MAX_TESSERACT_THREADS = 4
_limit_pinned_at_import: Optional[str] = os.environ.get(OMP_LIMIT_ENV)
_wrapper_lock = threading.Lock()
_wrapper_path: Optional[str] = None


def available_cpu_count() -> int:
    """CPUs this process may run on (affinity aware where the platform reports it)."""
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def tesseract_thread_limit(pool_workers: int, cpu_count: Optional[int] = None) -> int:
    """OpenMP threads each Tesseract process may use when ``pool_workers`` conversions run at once.

    The cores are shared evenly across the pool, one to four threads per process.

    Args:
        pool_workers: Size of the ingestion thread pool.
        cpu_count: CPUs available; detected when omitted.

    Returns:
        The thread limit, 1..4.
    """
    cores = cpu_count if cpu_count is not None else available_cpu_count()
    return max(1, min(MAX_TESSERACT_THREADS, cores // max(1, int(pool_workers))))


def configure_tesseract_threads(pool_workers: int, pinned: Optional[str] = None) -> int:
    """Set ``OCR_OMP_THREAD_LIMIT`` for a pool of ``pool_workers`` conversions.

    A value pinned in the environment at start-up wins (``0`` leaves OpenMP free);
    otherwise the limit is :func:`tesseract_thread_limit`. The variable is read by the
    wrapper of :func:`tesseract_command` for every Tesseract process it starts, so a
    run can change it without rebuilding its converters.

    Args:
        pool_workers: Size of the ingestion thread pool.
        pinned: The environment value to honour; the value at import when omitted.

    Returns:
        The limit in force (0 = unlimited).
    """
    pinned = _limit_pinned_at_import if pinned is None else pinned
    if pinned is not None and pinned.strip() != "":
        try:
            limit = max(0, int(pinned))
        except ValueError:
            _log.warning("Ignoring non-numeric %s=%r", OMP_LIMIT_ENV, pinned)
        else:
            os.environ[OMP_LIMIT_ENV] = str(limit)
            return limit
    limit = tesseract_thread_limit(pool_workers)
    os.environ[OMP_LIMIT_ENV] = str(limit)
    return limit


def tesseract_command(real_command: Optional[str] = None, directory: Optional[str] = None) -> str:
    """The command Docling runs for Tesseract: a wrapper applying ``OCR_OMP_THREAD_LIMIT``.

    The wrapper is a short ``sh`` script that exports ``OMP_THREAD_LIMIT`` from
    ``OCR_OMP_THREAD_LIMIT`` (unset or ``0``: OpenMP stays free) and execs the real
    binary. It is generated once per process. Where no wrapper can run (not POSIX, or
    no ``tesseract`` on PATH) the plain command is returned and the limit has no effect.

    Args:
        real_command: The Tesseract binary to wrap; resolved from PATH when omitted.
        directory: Where to write the wrapper; a temporary directory when omitted.

    Returns:
        The command to put into ``TesseractCliOcrOptions.tesseract_cmd``.
    """
    global _wrapper_path
    explicit = real_command is not None or directory is not None
    if not explicit:
        if _wrapper_path is not None:
            return _wrapper_path
        if os.name != "posix":
            return "tesseract"
    real = real_command or shutil.which("tesseract")
    if not real:
        return "tesseract"
    with _wrapper_lock:
        if not explicit and _wrapper_path is not None:
            return _wrapper_path
        target_dir = directory or tempfile.mkdtemp(prefix="recallhub-tesseract-")
        path = os.path.join(target_dir, "tesseract")
        script = (
            "#!/bin/sh\n"
            "# Generated by RecallHub (src/ingestion/ocr_tesseract.py): caps the OpenMP threads of\n"
            "# this Tesseract process to OCR_OMP_THREAD_LIMIT (unset or 0 leaves them free).\n"
            'if [ -n "$%s" ] && [ "$%s" != "0" ]; then export OMP_THREAD_LIMIT="$%s"; fi\n'
            'exec "%s" "$@"\n'
        ) % (OMP_LIMIT_ENV, OMP_LIMIT_ENV, OMP_LIMIT_ENV, real)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(script)
        os.chmod(path, 0o755)
        if not explicit:
            _wrapper_path = path
        return path
