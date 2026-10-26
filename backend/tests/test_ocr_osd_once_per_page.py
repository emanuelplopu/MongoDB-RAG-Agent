"""Orientation detection once per page (item 6 of the 2026-10-26 prompt-test fixes).

Docling's Tesseract CLI model ran ``tesseract --psm 0`` on every OCR rectangle; on the
diejuristin corpus 37 % of those calls failed with "Too few characters" (logos, stamps,
signatures: ~8,000 ERROR lines per 3 h) and each cost a Tesseract process. The model now
detects the orientation once on the page image and reuses it for every rectangle, skips it
on pages whose OCR regions are tiny, and turns an OSD failure into an upright reading
without an ERROR line. The OCR itself (``deu+eng``) is untouched.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any, List

import pandas as pd
import pytest

ocr = pytest.importorskip("src.ingestion.ocr_tesseract")
from docling_core.types.doc import BoundingBox, CoordOrigin  # noqa: E402


def _frame(orientation: int = 0, confidence: float = 12.0) -> pd.DataFrame:
    return pd.DataFrame({
        "key": ["Orientation in degrees", "Rotate", "Orientation confidence"],
        "value": [f" {orientation}", f" {(-orientation) % 360}", f" {confidence:.2f}"],
    })


def _rect(w: float, h: float) -> BoundingBox:
    return BoundingBox(l=0, t=0, r=w, b=h, coord_origin=CoordOrigin.TOPLEFT)


class _Backend:
    def __init__(self) -> None:
        self.renders = 0

    def get_page_image(self, scale: float = 1.0, cropbox: Any = None):
        Image = pytest.importorskip("PIL.Image")
        self.renders += 1
        return Image.new("RGB", (100, 140), "white")


def _model(monkeypatch: pytest.MonkeyPatch, rects: List[BoundingBox], osd_calls: List[str], *,
           result: Any = None, **options: Any) -> Any:
    """A model without Tesseract: the base OSD and the layout rectangles are stubbed."""
    model = ocr.RecallHubTesseractCliOcrModel.__new__(ocr.RecallHubTesseractCliOcrModel)
    model.options = ocr.RecallHubTesseractCliOcrOptions(lang=["deu", "eng"], **options)
    model.scale = 3
    model._suppressed = {}
    model._page_osd = None
    for name in ("osd_guarded", "osd_page_runs", "osd_skipped_tiny", "osd_failed",
                 "retries_rotated", "retries_kept", "empty_page_fallbacks"):
        setattr(model, name, 0)

    def base_osd(self, ifilename: str) -> pd.DataFrame:
        osd_calls.append(ifilename)
        if isinstance(result, Exception):
            raise result
        return (result if result is not None else _frame()).copy()

    monkeypatch.setattr(ocr.TesseractOcrCliModel, "_perform_osd", base_osd)
    monkeypatch.setattr(ocr.TesseractOcrCliModel, "get_ocr_rects", lambda self, page: list(rects))
    return model


def _page() -> Any:
    return SimpleNamespace(page_no=3, size=SimpleNamespace(width=595, height=842), _backend=_Backend())


def test_one_osd_per_page_shared_by_every_rectangle(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[str] = []
    model = _model(monkeypatch, [_rect(400, 300), _rect(400, 200), _rect(300, 100)], calls, result=_frame(90, 15.0))
    page = _page()
    rects = model.get_ocr_rects(page)
    assert len(rects) == 3
    frames = [model._perform_osd(f"rect-{i}.png") for i in range(3)]
    assert len(calls) == 1, "Docling ran OSD three times; one page-level run now"
    assert page._backend.renders == 1 and model.osd_page_runs == 1
    assert all(ocr._osd_value(f, "Orientation in degrees") == 90 for f in frames)
    assert model._suppressed == {}, "a confident rotation is applied, not retried"


def test_next_page_detects_again(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[str] = []
    model = _model(monkeypatch, [_rect(400, 300)], calls)
    model.get_ocr_rects(_page())
    model._perform_osd("a.png")
    model.get_ocr_rects(_page())
    model._perform_osd("b.png")
    assert len(calls) == 2


def test_tiny_regions_skip_osd_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[str] = []
    model = _model(monkeypatch, [_rect(120, 40), _rect(60, 30)], calls)  # a logo and a signature
    model.get_ocr_rects(_page())
    frame = model._perform_osd("logo.png")
    assert calls == [] and model.osd_skipped_tiny == 1
    assert ocr._osd_value(frame, "Orientation in degrees") == 0


def test_osd_failure_reads_the_page_upright_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[str] = []
    failure = subprocess.CalledProcessError(1, ["tesseract"], stderr=b"Too few characters. Skipping this page")
    model = _model(monkeypatch, [_rect(400, 300)], calls, result=failure)
    model.get_ocr_rects(_page())
    frame = model._perform_osd("r.png")
    assert model.osd_failed == 1 and len(calls) == 1
    assert ocr._osd_value(frame, "Orientation in degrees") == 0
    assert ocr._osd_value(model._perform_osd("r2.png"), "Orientation in degrees") == 0 and len(calls) == 1


def test_page_level_guard_marks_every_rectangle_for_the_rotation_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[str] = []
    model = _model(monkeypatch, [_rect(400, 300), _rect(400, 300)], calls, result=_frame(180, 0.4), osd_min_confidence=1.0)
    model.get_ocr_rects(_page())
    for name in ("r1.png", "r2.png"):
        frame = model._perform_osd(name)
        assert ocr._osd_value(frame, "Orientation in degrees") == 0, "the low-confidence rotation is suppressed"
    assert model.osd_guarded == 1 and len(calls) == 1
    assert model._suppressed == {"r1.png": 180, "r2.png": 180}, "each rectangle is OCRed both ways"


def test_per_rectangle_osd_is_still_available(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[str] = []
    model = _model(monkeypatch, [_rect(400, 300), _rect(400, 300)], calls, osd_once_per_page=False)
    model.get_ocr_rects(_page())
    model._perform_osd("r1.png")
    model._perform_osd("r2.png")
    assert calls == ["r1.png", "r2.png"]
