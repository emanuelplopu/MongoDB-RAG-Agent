"""Tests for the OCR resolution / orientation settings added for the scale test.

Covers the pure parts of ``src/ingestion/ingest.py`` (OCR_SCAN_DPI, OCR_IMAGE_MAX_MPX,
OCR_PSM, OCR_TESSDATA_PATH, OCR_OSD_MIN_CONFIDENCE), the converter cache and the
orientation-confidence guard of ``src/ingestion/ocr_tesseract.py``. No Docling model is
loaded: converters are only built, never run.
"""

from __future__ import annotations

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")


# ---------------------------------------------------------------------------
# Scale rules
# ---------------------------------------------------------------------------

def test_default_config_reproduces_docling_scale_for_pages() -> None:
    config = ingest.OcrConfig()
    assert config.scan_dpi == 216
    assert config.pdf_scale() == pytest.approx(3.0)
    assert config.image_max_mpx == 20
    assert ingest.OcrConfig(image_max_mpx=0).image_scale(3408, 4624, None) is None  # 0 = no cap


def test_pdf_scale_follows_scan_dpi() -> None:
    assert ingest.OcrConfig(scan_dpi=300).pdf_scale() == pytest.approx(300 / 72, abs=1e-3)
    assert ingest.OcrConfig(scan_dpi=5000).pdf_scale() == ingest._OCR_SCALE_MAX


@pytest.mark.parametrize(
    ("width", "height", "dpi", "expected"),
    [
        (3408, 4624, None, (20e6 / (3408 * 4624)) ** 0.5),  # 12 Mpx phone photo, no DPI tag: 142 Mpx at 3x -> capped to 20 Mpx
        (2480, 3508, 300.0, 3.0),  # 300-dpi A4 scan image: 4.5 Mpx at 3x, untouched
        (1654, 2339, 200.0, 3.0),  # 200-dpi scan: untouched
        (800, 600, None, 3.0),  # small image: untouched
    ],
)
def test_image_scale_caps_the_bitmap_budget(width: int, height: int, dpi, expected: float) -> None:
    config = ingest.OcrConfig(image_max_mpx=20)
    assert config.image_scale(width, height, dpi) == pytest.approx(expected, abs=1e-3)


def test_image_scale_is_clamped() -> None:
    config = ingest.OcrConfig(image_max_mpx=20)
    assert config.image_scale(100, 100, None) == 3.0
    assert config.image_scale(100000, 100000, None) == ingest._OCR_SCALE_MIN


# ---------------------------------------------------------------------------
# Resolution from the environment
# ---------------------------------------------------------------------------

def test_environment_sets_resolution_knobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_SCAN_DPI", "300")
    monkeypatch.setenv("OCR_IMAGE_MAX_MPX", "40")
    monkeypatch.setenv("OCR_PSM", "6")
    monkeypatch.setenv("OCR_TESSDATA_PATH", "/data/tessdata")
    monkeypatch.setenv("OCR_OSD_MIN_CONFIDENCE", "1.5")
    config = ingest.resolve_ocr_config(engine="tesseract", languages="deu,eng")
    assert (config.scan_dpi, config.image_max_mpx, config.psm) == (300, 40, 6)
    assert config.tessdata_path == "/data/tessdata"
    assert config.osd_min_confidence == 1.5
    assert "scan_dpi=300" in config.describe() and "osd_min_conf=1.5" in config.describe()


def test_invalid_resolution_values_fall_back(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("OCR_SCAN_DPI", "lots")
    monkeypatch.setenv("OCR_IMAGE_MAX_MPX", "-5")
    monkeypatch.setenv("OCR_PSM", "42")
    monkeypatch.setenv("OCR_OSD_MIN_CONFIDENCE", "x")
    with caplog.at_level("ERROR", logger=ingest.logger.name):
        config = ingest.resolve_ocr_config(engine="tesseract", languages="deu")
    assert config.scan_dpi == ingest.DEFAULT_OCR_SCAN_DPI
    assert config.image_max_mpx == ingest.DEFAULT_OCR_IMAGE_MAX_MPX
    assert config.psm is None
    assert config.osd_min_confidence == ingest.DEFAULT_OCR_OSD_MIN_CONFIDENCE
    assert "OCR_SCAN_DPI" in caplog.text and "OCR_PSM" in caplog.text


def test_settings_surfaces_agree_on_new_defaults() -> None:
    from backend.core.config import BackendSettings
    from src.settings import Settings

    for cls in (Settings, BackendSettings):
        assert cls.model_fields["ocr_scan_dpi"].default == ingest.DEFAULT_OCR_SCAN_DPI
        assert cls.model_fields["ocr_image_max_mpx"].default == ingest.DEFAULT_OCR_IMAGE_MAX_MPX
        assert cls.model_fields["ocr_osd_min_confidence"].default == ingest.DEFAULT_OCR_OSD_MIN_CONFIDENCE
        assert cls.model_fields["ocr_psm"].default == ""
        assert cls.model_fields["ocr_tessdata_path"].default == ""


# ---------------------------------------------------------------------------
# Docling option mapping (needs docling installed, no models)
# ---------------------------------------------------------------------------

def _require_scalable_tesseract_options():
    """Docling >= 2.9x: TesseractCliOcrOptions carries scale/psm (the production image does)."""
    po = pytest.importorskip("docling.datamodel.pipeline_options")
    if "scale" not in po.TesseractCliOcrOptions.model_fields or "psm" not in po.TesseractCliOcrOptions.model_fields:
        pytest.skip("installed Docling has no scale/psm on TesseractCliOcrOptions")
    return po


def test_build_ocr_options_applies_scale_psm_and_tessdata() -> None:
    po = _require_scalable_tesseract_options()
    config = ingest.OcrConfig(engine="tesseract", languages=("deu",), psm=6, tessdata_path="/data/tessdata",
                              osd_min_confidence=0.0)
    options = ingest.build_ocr_options(config, scale=4.0)
    assert isinstance(options, po.TesseractCliOcrOptions)
    assert options.kind == "tesseract"  # guard off: Docling's stock model
    assert (options.scale, options.psm, options.path) == (4.0, 6, "/data/tessdata")
    default_options = ingest.build_ocr_options(ingest.OcrConfig(engine="tesseract"))
    assert default_options.kind == "tesseract_recallhub"  # guard on by default


def test_osd_guard_selects_recallhub_model() -> None:
    pytest.importorskip("docling.models.stages.ocr.tesseract_ocr_cli_model")
    from docling.models.factories import get_ocr_factory
    from src.ingestion.ocr_tesseract import RecallHubTesseractCliOcrModel, RecallHubTesseractCliOcrOptions

    options = ingest.build_ocr_options(ingest.OcrConfig(engine="tesseract", osd_min_confidence=2.0))
    assert isinstance(options, RecallHubTesseractCliOcrOptions)
    assert options.kind == "tesseract_recallhub"
    assert options.osd_min_confidence == 2.0
    factory = get_ocr_factory(allow_external_plugins=False)
    assert factory.classes[RecallHubTesseractCliOcrOptions] is RecallHubTesseractCliOcrModel


def test_osd_guard_suppresses_low_confidence_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    pd = pytest.importorskip("pandas")
    pytest.importorskip("docling.models.stages.ocr.tesseract_ocr_cli_model")
    from docling.models.stages.ocr.tesseract_ocr_cli_model import TesseractOcrCliModel, _parse_orientation
    from src.ingestion.ocr_tesseract import RecallHubTesseractCliOcrModel, RecallHubTesseractCliOcrOptions

    def fake_osd(self, ifilename):  # the stock OSD output for a 180-degree guess
        return pd.DataFrame({"key": ["Page number", "Orientation in degrees", "Rotate", "Orientation confidence", "Script"],
                             "value": [" 0", " 180", " 180", " 0.85", " Latin"]})

    monkeypatch.setattr(TesseractOcrCliModel, "_perform_osd", fake_osd)
    model = RecallHubTesseractCliOcrModel.__new__(RecallHubTesseractCliOcrModel)  # no tesseract probe
    model.osd_guarded = 0
    model._suppressed = {}

    model.options = RecallHubTesseractCliOcrOptions(lang=["deu"], osd_min_confidence=1.0)
    assert _parse_orientation(model._perform_osd("x.png")) == 0
    assert model.osd_guarded == 1

    model.options = RecallHubTesseractCliOcrOptions(lang=["deu"], osd_min_confidence=0.5)
    assert _parse_orientation(model._perform_osd("x.png")) == 180

    model.options = RecallHubTesseractCliOcrOptions(lang=["deu"], osd_min_confidence=0.0)
    assert _parse_orientation(model._perform_osd("x.png")) == 180


# ---------------------------------------------------------------------------
# Converter cache
# ---------------------------------------------------------------------------

def test_converters_are_cached_per_configuration_and_scale(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("docling.document_converter")
    _require_scalable_tesseract_options()
    from PIL import Image

    ingest.clear_converter_cache()
    config = ingest.OcrConfig(engine="tesseract", languages=("eng",), image_max_mpx=20)
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (3408, 4624), "white").save(photo)
    scan = tmp_path / "scan.png"
    Image.new("L", (2480, 3508), "white").save(scan, dpi=(300, 300))

    a = ingest.create_image_converter(config, image_path=str(photo))
    assert ingest.image_ocr_scale(a) == pytest.approx((20e6 / (3408 * 4624)) ** 0.5, abs=1e-3)  # capped photo
    b = ingest.create_image_converter(config, image_path=str(scan))
    assert a is b  # one converter (one pipeline, one layout model) per thread and configuration
    assert ingest.image_ocr_scale(b) == pytest.approx(3.0)  # the scale is set on the live OCR model per image
    from docling.datamodel.base_models import InputFormat

    assert a.format_to_options[InputFormat.IMAGE].pipeline_options.ocr_options.scale == pytest.approx(3.0)  # options untouched

    pdf_a = ingest.create_document_converter(config)
    pdf_b = ingest.create_document_converter(config)
    assert pdf_a is pdf_b
    assert pdf_a.format_to_options[InputFormat.PDF].pipeline_options.ocr_options.scale == pytest.approx(3.0)
    ingest.clear_converter_cache()
    assert ingest.create_document_converter(config) is not pdf_a


# ---------------------------------------------------------------------------
# Rotated-retry box mapping and reading score
# ---------------------------------------------------------------------------

def test_map_rotated_boxes_round_trips_each_angle() -> None:
    pd = pytest.importorskip("pandas")
    pytest.importorskip("docling.models.stages.ocr.tesseract_ocr_cli_model")
    from src.ingestion.ocr_tesseract import map_rotated_boxes

    width, height = 400, 300  # unrotated image
    # A word box in the unrotated image, and where it lands after a clockwise rotation by `angle`
    # (PIL rotate(-angle, expand=True)); the function must map the rotated box back onto it.
    original = {"left": 50, "top": 20, "width": 100, "height": 10}
    rotated = {
        180: {"left": 400 - 50 - 100, "top": 300 - 20 - 10, "width": 100, "height": 10},
        90: {"left": 300 - 20 - 10, "top": 50, "width": 10, "height": 100},  # rotated image is 300 x 400
        270: {"left": 20, "top": 400 - 50 - 100, "width": 10, "height": 100},
    }
    for angle, box in rotated.items():
        df = pd.DataFrame([{**box, "text": "Wort", "conf": 90.0}])
        mapped = map_rotated_boxes(df, angle, width, height).iloc[0]
        assert {k: int(mapped[k]) for k in original} == original, angle


def test_reading_score_weights_confidence_by_characters() -> None:
    pd = pytest.importorskip("pandas")
    pytest.importorskip("docling.models.stages.ocr.tesseract_ocr_cli_model")
    from src.ingestion.ocr_tesseract import _reading_score

    good = pd.DataFrame({"text": ["Dienstvertrag", "vom"], "conf": [95.0, 90.0]})
    soup = pd.DataFrame({"text": ["qaynze6", "x"], "conf": [20.0, -1]})
    assert _reading_score(good) > _reading_score(soup) * 3
    assert _reading_score(pd.DataFrame({"text": [], "conf": []})) == 0.0


# ---------------------------------------------------------------------------
# Page furniture (headers / footers) in content and chunks
# ---------------------------------------------------------------------------

def test_page_furniture_setting_defaults_on_both_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.core.config import BackendSettings
    from src.settings import Settings

    for cls in (Settings, BackendSettings):
        assert cls.model_fields["ingest_page_furniture"].default is True
    monkeypatch.setenv("INGEST_PAGE_FURNITURE", "false")
    assert ingest.include_page_furniture() is False
    assert ingest.document_content_layers() is None
    monkeypatch.setenv("INGEST_PAGE_FURNITURE", "true")
    assert ingest.include_page_furniture() is True


def test_export_document_markdown_passes_content_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("docling_core.types.doc")
    from docling_core.types.doc import ContentLayer

    class FakeDoc:
        def __init__(self) -> None:
            self.calls = []

        def export_to_markdown(self, **kwargs):
            self.calls.append(kwargs)
            return "md"

    monkeypatch.setenv("INGEST_PAGE_FURNITURE", "true")
    doc = FakeDoc()
    assert ingest.export_document_markdown(doc) == "md"
    assert doc.calls == [{"included_content_layers": {ContentLayer.BODY, ContentLayer.FURNITURE}}]

    monkeypatch.setenv("INGEST_PAGE_FURNITURE", "0")
    doc = FakeDoc()
    ingest.export_document_markdown(doc)
    assert doc.calls == [{}]


def test_chunker_serializer_provider_visits_furniture(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("docling_core.transforms.chunker.hierarchical_chunker")
    from docling_core.types.doc import ContentLayer, DoclingDocument
    from src.ingestion import chunker as chunker_mod

    monkeypatch.setenv("INGEST_PAGE_FURNITURE", "true")
    provider = chunker_mod._furniture_serializer_provider()
    assert provider is not None
    serializer = provider.get_serializer(DoclingDocument(name="t"))
    assert serializer.params.layers == {ContentLayer.BODY, ContentLayer.FURNITURE}

    monkeypatch.setenv("INGEST_PAGE_FURNITURE", "false")
    assert chunker_mod._furniture_serializer_provider() is None


# ---------------------------------------------------------------------------
# Image noise gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "score", "expected"),
    [
        ("> Pe GR a yt ik A Deu rer", 0.30, True),  # a photo of a car
        ("CO) = @ ON @» (S) (a) qu NOC OO? ES", 0.65, True),  # noise even with a middling score
        ("", None, True),
        ("Herrn Dr.med. Peter Parizek Fatinitzaweg 8 1100 Wien SCHADENNUMMER 2802950920", 0.86, False),
        ("UNFALLBERICHT hae Schuldanerkenntnis, Sondern eine Wiedergabe des nt", 0.53, False),  # a form, read poorly
        ("Heute Nachrichten und Anrufe sind Ende-zu-Ende-verschlusselt", 0.91, False),  # a screenshot
        ("ae Bae ey ET AT Deu rer", 0.90, False),  # borderline ratio but confident: the score vetoes the drop
        ("ae Bae ey ET AT", 0.90, True),  # almost no word-like tokens: soup whatever the score says
    ],
)
def test_image_text_is_noise(text: str, score, expected: bool) -> None:
    assert ingest.image_text_is_noise(text, score) is expected


def test_image_noise_gate_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.core.config import BackendSettings
    from src.settings import Settings

    for cls in (Settings, BackendSettings):
        assert cls.model_fields["ocr_image_noise_gate"].default is True
    monkeypatch.setenv("OCR_IMAGE_NOISE_GATE", "false")
    assert ingest.image_noise_gate_enabled() is False
    monkeypatch.setenv("OCR_IMAGE_NOISE_GATE", "1")
    assert ingest.image_noise_gate_enabled() is True


def test_osd_guard_stores_docling_orientation_convention(monkeypatch: pytest.MonkeyPatch) -> None:
    pd = pytest.importorskip("pandas")
    pytest.importorskip("docling.models.stages.ocr.tesseract_ocr_cli_model")
    from docling.models.stages.ocr.tesseract_ocr_cli_model import TesseractOcrCliModel
    from docling.models.stages.ocr.tesseract_utils import parse_tesseract_orientation
    from src.ingestion.ocr_tesseract import RecallHubTesseractCliOcrModel, RecallHubTesseractCliOcrOptions

    def fake_osd(self, ifilename):  # Tesseract reports 270 clockwise
        return pd.DataFrame({"key": ["Orientation in degrees", "Rotate", "Orientation confidence"],
                             "value": [" 270", " 90", " 2.1"]})

    monkeypatch.setattr(TesseractOcrCliModel, "_perform_osd", fake_osd)
    model = RecallHubTesseractCliOcrModel.__new__(RecallHubTesseractCliOcrModel)
    model.osd_guarded = 0
    model._suppressed = {}
    model.options = RecallHubTesseractCliOcrOptions(lang=["deu"], osd_min_confidence=10.0)
    model._perform_osd("rect.png")
    # the retry must rotate exactly like Docling would have for the unsuppressed rotation
    assert model._suppressed["rect.png"] == parse_tesseract_orientation("270") == 90


def test_table_structure_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.core.config import BackendSettings
    from src.settings import Settings

    for cls in (Settings, BackendSettings):
        assert cls.model_fields["ingest_table_structure"].default == ingest.DEFAULT_INGEST_TABLE_STRUCTURE
    monkeypatch.setenv("INGEST_TABLE_STRUCTURE", "false")
    assert ingest.table_structure_enabled() is False
    monkeypatch.setenv("INGEST_TABLE_STRUCTURE", "true")
    assert ingest.table_structure_enabled() is True


def test_converters_follow_table_structure_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("docling.document_converter")
    from docling.datamodel.base_models import InputFormat

    monkeypatch.setenv("INGEST_TABLE_STRUCTURE", "false")
    ingest.clear_converter_cache()
    config = ingest.OcrConfig(engine="tesseract", languages=("eng",), osd_min_confidence=0.0)
    pdf = ingest.create_document_converter(config)
    img = ingest.create_image_converter(config)
    assert pdf.format_to_options[InputFormat.PDF].pipeline_options.do_table_structure is False
    assert img.format_to_options[InputFormat.IMAGE].pipeline_options.do_table_structure is False
    ingest.clear_converter_cache()


def test_prepare_image_for_docling_resaves_zero_dpi_and_mpo(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    from PIL import Image

    plain = tmp_path / "plain.jpg"
    Image.new("RGB", (40, 30), "white").save(plain, dpi=(300, 300))
    assert ingest.prepare_image_for_docling(str(plain)) == (str(plain), None)

    # Pillow does not round-trip a (0, 0) density tag, so inject what a real screenshot reports
    zero = tmp_path / "zero.jpg"
    Image.new("RGB", (40, 30), "white").save(zero)
    real_open = Image.open

    def open_with_zero_dpi(path, *args, **kwargs):
        img = real_open(path, *args, **kwargs)
        if str(path) == str(zero):
            img.info["dpi"] = (0, 0)
        return img

    monkeypatch.setattr(Image, "open", open_with_zero_dpi)
    path, temp = ingest.prepare_image_for_docling(str(zero))
    assert temp is not None and path == temp and path.endswith(".png")
    with real_open(path) as img:
        assert img.format == "PNG" and "dpi" not in img.info
    os.remove(temp)
