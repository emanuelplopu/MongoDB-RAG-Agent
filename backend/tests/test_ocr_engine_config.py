"""Tests for the configurable Docling OCR engine (OCR_ENGINE / OCR_LANGUAGES).

Covers the pure option mapping in ``src/ingestion/ingest.py`` and the agreement
of both settings surfaces. No Docling model is downloaded: the tests only build
``ocr_options`` objects and never run a converter.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")


# ---------------------------------------------------------------------------
# Language parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("deu,eng", ("deu", "eng")),
        (" de , EN ", ("deu", "eng")),
        ("ger+english", ("deu", "eng")),
        ("deu;fra deu", ("deu", "fra")),
        ("", ("deu", "eng")),
        (None, ("deu", "eng")),
        ("chi_sim", ("chi_sim",)),  # unknown codes pass through for exotic tesseract packs
    ],
)
def test_parse_ocr_languages(raw, expected) -> None:
    assert ingest.parse_ocr_languages(raw) == expected


# ---------------------------------------------------------------------------
# Resolution from settings / environment
# ---------------------------------------------------------------------------

def test_defaults_are_tesseract_german_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCR_ENGINE", raising=False)
    monkeypatch.delenv("OCR_LANGUAGES", raising=False)
    config = ingest.resolve_ocr_config(engine=ingest.DEFAULT_OCR_ENGINE, languages=ingest.DEFAULT_OCR_LANGUAGES)
    assert config == ingest.OcrConfig(engine="tesseract", languages=("deu", "eng"))
    assert config.describe() == "tesseract lang=deu+eng"


def test_environment_overrides_engine_and_languages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_ENGINE", "EasyOCR")
    monkeypatch.setenv("OCR_LANGUAGES", "de")
    config = ingest.resolve_ocr_config()
    assert config.engine == "easyocr"
    assert config.languages == ("deu",)


def test_unknown_engine_falls_back_to_default(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("ERROR", logger=ingest.logger.name):
        config = ingest.resolve_ocr_config(engine="paddle", languages="deu")
    assert config.engine == ingest.DEFAULT_OCR_ENGINE
    assert "Unknown OCR_ENGINE 'paddle'" in caplog.text


def test_settings_surfaces_agree_on_defaults() -> None:
    from backend.core.config import BackendSettings
    from src.settings import Settings

    for cls in (Settings, BackendSettings):
        assert cls.model_fields["ocr_engine"].default == ingest.DEFAULT_OCR_ENGINE
        assert cls.model_fields["ocr_languages"].default == ingest.DEFAULT_OCR_LANGUAGES


def test_settings_apply_profile_keeps_ocr_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.profile import ProfileConfig
    from src.settings import Settings

    monkeypatch.setenv("OCR_ENGINE", "rapidocr")
    monkeypatch.setenv("OCR_LANGUAGES", "eng")
    base = Settings(mongodb_uri="mongodb://x", llm_api_key="k", embedding_api_key="k")
    profile = ProfileConfig(key="t", name="t", database="db_t")
    applied = base.apply_profile(profile)
    assert (applied.ocr_engine, applied.ocr_languages) == ("rapidocr", "eng")


# ---------------------------------------------------------------------------
# Docling option mapping (needs docling installed, no models)
# ---------------------------------------------------------------------------

def test_auto_leaves_pipeline_options_untouched() -> None:
    assert ingest.build_ocr_options(ingest.OcrConfig(engine="auto")) is None
    sentinel = object()
    pipeline_options = SimpleNamespace(ocr_options=sentinel)
    applied = ingest.apply_ocr_options(pipeline_options, ingest.OcrConfig(engine="auto"))
    assert pipeline_options.ocr_options is sentinel
    assert applied.describe() == "auto (Docling default)"


def test_build_ocr_options_maps_engines_and_language_codes() -> None:
    po = pytest.importorskip("docling.datamodel.pipeline_options")

    tess = ingest.build_ocr_options(ingest.OcrConfig(engine="tesseract", languages=("deu", "eng")))
    assert isinstance(tess, po.TesseractCliOcrOptions)
    assert tess.lang == ["deu", "eng"]

    easy = ingest.build_ocr_options(ingest.OcrConfig(engine="easyocr", languages=("deu", "eng", "fra")))
    assert isinstance(easy, po.EasyOcrOptions)
    assert easy.lang == ["de", "en", "fr"]

    rapid = ingest.build_ocr_options(ingest.OcrConfig(engine="rapidocr", languages=("deu",)))
    assert isinstance(rapid, po.RapidOcrOptions)


def test_apply_ocr_options_sets_tesseract_and_logs_once(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    po = pytest.importorskip("docling.datamodel.pipeline_options")
    monkeypatch.setattr(ingest, "_ocr_engine_logged", False)
    config = ingest.OcrConfig(engine="tesseract", languages=("deu", "eng"))

    with caplog.at_level("INFO", logger=ingest.logger.name):
        first = po.PdfPipelineOptions()
        ingest.apply_ocr_options(first, config)
        second = po.PdfPipelineOptions()
        ingest.apply_ocr_options(second, config)

    assert isinstance(first.ocr_options, po.TesseractCliOcrOptions)
    assert isinstance(second.ocr_options, po.TesseractCliOcrOptions)
    assert first.ocr_options.lang == ["deu", "eng"]
    assert caplog.text.count("Docling OCR engine: tesseract lang=deu+eng") == 1


def test_image_converter_uses_configured_engine() -> None:
    pytest.importorskip("docling.document_converter")
    from docling.datamodel import pipeline_options as po
    from docling.datamodel.base_models import InputFormat

    converter = ingest.create_image_converter(ingest.OcrConfig(engine="tesseract", languages=("eng",)))
    options = converter.format_to_options[InputFormat.IMAGE].pipeline_options
    assert options.do_ocr is True
    assert isinstance(options.ocr_options, po.TesseractCliOcrOptions)
    assert options.ocr_options.lang == ["eng"]


def test_pdf_converter_uses_configured_engine() -> None:
    pytest.importorskip("docling.document_converter")
    from docling.datamodel import pipeline_options as po
    from docling.datamodel.base_models import InputFormat

    converter = ingest.create_document_converter(ingest.OcrConfig(engine="rapidocr"))
    options = converter.format_to_options[InputFormat.PDF].pipeline_options
    assert isinstance(options.ocr_options, po.RapidOcrOptions)
