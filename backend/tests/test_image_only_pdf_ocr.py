"""Tests for image-only (pure scan) PDF handling in the concurrent ingestion loop.

The concurrent loop of src/ingestion/ingest.py used to skip every PDF without a text layer before
OCR ran (~30% of the diejuristin PDFs, see docs/26-FORMAT_EDGE_AUDIT.md). It now OCRs them like the
sequential loop does and keeps the skip behind ``INGEST_SKIP_IMAGE_ONLY_PDF`` (default off).

The tests run the real per-file loops; only the per-document processing and the stats persistence
are stubbed.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")

SCAN = "scan_blank.pdf"  # image-only PDF whose OCR finds nothing
SCAN_WITH_TEXT = "scan_penal_order.pdf"  # image-only PDF whose OCR recovers text
TEXT = "text_layer.pdf"  # PDF with a real text layer


def _text_pdf(text: str) -> bytes:
    """A minimal valid one-page PDF with a text layer."""
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
    out.write(
        f"trailer\n<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    )
    return out.getvalue()


def _image_only_pdf(path: Path, shade: int) -> None:
    """A PDF holding one raster image and no text layer (what a scanner produces)."""
    Image = pytest.importorskip("PIL.Image")
    # distinct pixels per file keep the content hashes apart (no duplicate skips)
    Image.new("RGB", (200, 280), (shade, shade, shade)).save(path, "PDF")


class _Pipeline(ingest.DocumentIngestionPipeline):
    """Pipeline whose per-document processing is a stub; the file loops are the real ones."""

    def __init__(self, chunks_by_name: Dict[str, int]) -> None:
        self._initialized = True
        self.db = None
        self.documents_folders: List[str] = []
        self.chunks_by_name = chunks_by_name
        self.processed: List[str] = []
        self.stats: List[Any] = []
        self.attempts: Dict[str, int] = {}

    async def _ingest_single_document(self, file_path: str, content_hash: Optional[str]):
        name = Path(file_path).name
        self.processed.append(name)
        self.attempts[name] = self.attempts.get(name, 0) + 1
        chunks = self.chunks_by_name.get(name, 1)
        return ingest.IngestionResult(
            document_id=content_hash if chunks else "",
            title=name,
            chunks_created=chunks,
            processing_time_ms=1.0,
            errors=[] if chunks else ["No chunks created"],
        )

    async def _save_ingestion_stats(self, file_stats: List[Any]) -> None:
        self.stats.extend(file_stats)

    def stat(self, name: str) -> Any:
        return next(s for s in self.stats if s.file_name == name)


@pytest.fixture
def corpus(tmp_path: Path) -> List[str]:
    """A blank scan, a scan with recoverable text and a text-layer PDF; needs pdfium and Pillow."""
    pytest.importorskip("pypdfium2")
    _image_only_pdf(tmp_path / SCAN, 250)
    _image_only_pdf(tmp_path / SCAN_WITH_TEXT, 120)
    (tmp_path / TEXT).write_bytes(
        _text_pdf("Beschluss des Landesgerichts fuer Zivilrechtssachen Wien ueber die Exekution")
    )
    return [str(tmp_path / name) for name in (SCAN, SCAN_WITH_TEXT, TEXT)]


@pytest.fixture
def detector_calls(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """Records is_image_only_pdf calls while keeping the real detection."""
    calls: List[str] = []
    real = ingest.is_image_only_pdf

    def spy(file_path: str, *args: Any, **kwargs: Any):
        calls.append(Path(file_path).name)
        return real(file_path, *args, **kwargs)

    monkeypatch.setattr(ingest, "is_image_only_pdf", spy)
    return calls


def _run_concurrent(pipe: _Pipeline, files: List[str], **kwargs: Any):
    return asyncio.run(
        pipe._ingest_documents_concurrent(
            document_files=files, existing_hashes={}, incremental=False, max_concurrent=2, **kwargs
        )
    )


# --------------------------------------------------------------------------- setting

def test_setting_defaults_scan_ocr_on_every_surface() -> None:
    from backend.core.config import BackendSettings
    from src.settings import Settings

    assert ingest.DEFAULT_INGEST_SKIP_IMAGE_ONLY_PDF is False
    for cls in (Settings, BackendSettings):
        assert cls.model_fields["ingest_skip_image_only_pdf"].default is False


def test_resolver_env_beats_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Settings:
        ingest_skip_image_only_pdf = True

    monkeypatch.setattr(ingest, "load_settings", lambda *a, **k: _Settings())
    monkeypatch.delenv("INGEST_SKIP_IMAGE_ONLY_PDF", raising=False)
    assert ingest.skip_image_only_pdf_enabled() is True  # from settings
    for raw, expected in (("false", False), ("0", False), ("true", True), ("1", True), (" On ", True)):
        monkeypatch.setenv("INGEST_SKIP_IMAGE_ONLY_PDF", raw)
        assert ingest.skip_image_only_pdf_enabled() is expected, raw


def test_resolver_falls_back_to_ocr_when_settings_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any):
        raise RuntimeError("no MONGODB_URI")

    monkeypatch.setattr(ingest, "load_settings", broken)
    monkeypatch.delenv("INGEST_SKIP_IMAGE_ONLY_PDF", raising=False)
    assert ingest.skip_image_only_pdf_enabled() is False


def test_settings_apply_profile_keeps_the_flag() -> None:
    from src.profile import ProfileConfig
    from src.settings import Settings

    base = Settings(
        mongodb_uri="mongodb://x", llm_api_key="k", embedding_api_key="k", ingest_skip_image_only_pdf=True
    )
    applied = base.apply_profile(ProfileConfig(key="t", name="t", database="db_t"))
    assert applied.ingest_skip_image_only_pdf is True


# --------------------------------------------------------------------------- concurrent loop

def test_concurrent_loop_ocrs_image_only_pdfs_by_default(
    corpus: List[str], detector_calls: List[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("INGEST_SKIP_IMAGE_ONLY_PDF", raising=False)
    monkeypatch.setattr(ingest, "load_settings", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    pipe = _Pipeline({SCAN: 0, SCAN_WITH_TEXT: 4, TEXT: 2})

    results, duplicates = _run_concurrent(pipe, corpus)

    assert sorted(pipe.processed) == sorted([SCAN, SCAN_WITH_TEXT, TEXT])  # every PDF reached OCR
    # no pre-check before OCR; one post-check per finished PDF records whether it was a pure scan (F9)
    assert sorted(detector_calls) == sorted([SCAN, SCAN_WITH_TEXT, TEXT])
    assert duplicates == 0 and len(results) == 3
    assert pipe.stat(SCAN_WITH_TEXT).classification == "normal"
    assert pipe.stat(SCAN_WITH_TEXT).chunks_created == 4
    assert pipe.stat(SCAN_WITH_TEXT).success is True
    assert pipe.stat(SCAN_WITH_TEXT).image_only_pdf is True  # a pure scan whose OCR produced text
    assert pipe.stat(TEXT).classification == "normal"
    assert pipe.stat(TEXT).image_only_pdf is False
    # a genuinely blank scan is not pre-skipped; it is processed, and its empty OCR is recorded
    # as image_only_pdf (not no_chunks) so it is visible in the failed-documents summary
    blank = pipe.stat(SCAN)
    assert (blank.classification, blank.error_type, blank.success) == ("image_only_pdf", "image_only_pdf", False)
    assert blank.chunks_created == 0 and blank.image_only_pdf is True
    assert "OCR found no text" in blank.error_message
    assert blank.to_dict()["image_only_pdf"] is True and "image_only_pdf" not in _stat_dict_without_flag(pipe)


def _stat_dict_without_flag(pipe: _Pipeline) -> dict:
    """The stored form of a stat whose file was never checked (no PDF) carries no flag."""
    stat = pipe.stats[0]
    stat.image_only_pdf = None
    return stat.to_dict()


def test_concurrent_loop_skips_scans_when_the_flag_is_set(
    corpus: List[str], detector_calls: List[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INGEST_SKIP_IMAGE_ONLY_PDF", "true")
    pipe = _Pipeline({TEXT: 2})

    results, _ = _run_concurrent(pipe, corpus)

    assert pipe.processed == [TEXT]  # scans never reach Docling/OCR
    # the pre-check per PDF, plus the post-check of the one PDF that was processed (F9)
    assert sorted(detector_calls) == sorted([SCAN, SCAN_WITH_TEXT, TEXT, TEXT])
    assert [r.title for r in results] == [TEXT]  # the skip leaves no result, as before
    for name in (SCAN, SCAN_WITH_TEXT):
        skipped = pipe.stat(name)
        assert skipped.classification == "image_only_pdf"
        assert skipped.error_type == "image_only_pdf"
        assert skipped.success is True and skipped.chunks_created == 0
        assert skipped.content_hash  # the registry can still track the file
    assert pipe.stat(TEXT).classification == "normal"


def test_flag_read_once_per_run(corpus: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
    reads: List[int] = []
    monkeypatch.setattr(
        ingest, "skip_image_only_pdf_enabled", lambda: reads.append(1) or False
    )
    _run_concurrent(_Pipeline({}), corpus)
    assert len(reads) == 1


def test_stats_are_saved_for_ocred_scans_like_any_other_file(
    corpus: List[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INGEST_SKIP_IMAGE_ONLY_PDF", "false")
    pipe = _Pipeline({SCAN: 0, SCAN_WITH_TEXT: 3})
    _run_concurrent(pipe, corpus, job_id="job-1")
    scan = pipe.stat(SCAN_WITH_TEXT)
    assert scan.job_id == "job-1"
    assert scan.timeout_seconds == pytest.approx(
        ingest.calculate_file_timeout(scan.file_size_bytes, file_path=corpus[1])
    )
    assert scan.content_hash == ingest.compute_file_hash(corpus[1])


def test_ocr_of_a_scan_keeps_the_timeout_retry(corpus: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_SKIP_IMAGE_ONLY_PDF", "false")
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 0.05)

    class _SlowFirstTry(_Pipeline):
        async def _ingest_single_document(self, file_path: str, content_hash: Optional[str]):
            if Path(file_path).name == SCAN_WITH_TEXT and not self.attempts.get(SCAN_WITH_TEXT):
                self.attempts[SCAN_WITH_TEXT] = 1
                await asyncio.sleep(1)  # OCR slower than the per-file timeout
            return await super()._ingest_single_document(file_path, content_hash)

    pipe = _SlowFirstTry({SCAN_WITH_TEXT: 3})
    results, _ = _run_concurrent(pipe, corpus)

    assert pipe.attempts[SCAN_WITH_TEXT] == 2  # timed out once, retried, then succeeded
    attempts = [s for s in pipe.stats if s.file_name == SCAN_WITH_TEXT]
    # The timeout is recorded at once and the retry's record replaces it (same row, F43)
    assert [s.error_type for s in attempts] == ["timeout", None]
    assert len({s.record_id for s in attempts}) == 1 and attempts[1].supersedes == "timeout"
    retried = attempts[-1]
    assert (retried.classification, retried.success, retried.chunks_created) == ("normal", True, 3)
    assert any(r.title == SCAN_WITH_TEXT and r.chunks_created == 3 for r in results)


def test_exhausted_timeouts_of_a_scan_are_recorded_as_timeout(
    corpus: List[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INGEST_SKIP_IMAGE_ONLY_PDF", "false")
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 0.05)

    class _AlwaysSlow(_Pipeline):
        async def _ingest_single_document(self, file_path: str, content_hash: Optional[str]):
            if Path(file_path).name == SCAN:
                await asyncio.sleep(1)
            return await super()._ingest_single_document(file_path, content_hash)

    pipe = _AlwaysSlow({TEXT: 2, SCAN_WITH_TEXT: 2})
    _run_concurrent(pipe, corpus)

    stuck = pipe.stat(SCAN)
    assert (stuck.classification, stuck.error_type, stuck.success) == ("timeout", "timeout", False)
    assert pipe.stat(TEXT).classification == "normal"  # one hung scan does not stall the others


# --------------------------------------------------------------------------- sequential loop

@pytest.mark.parametrize("flag", ["true", "false"])
def test_sequential_loop_is_unchanged_and_never_prechecks(
    corpus: List[str], detector_calls: List[str], monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    monkeypatch.setenv("INGEST_SKIP_IMAGE_ONLY_PDF", flag)
    pipe = _Pipeline({SCAN: 0, SCAN_WITH_TEXT: 2, TEXT: 2})

    results, _ = asyncio.run(
        pipe._ingest_documents_sequential(document_files=corpus, existing_hashes={}, incremental=False)
    )

    assert pipe.processed == [SCAN, SCAN_WITH_TEXT, TEXT]  # scans were always OCRed here
    assert detector_calls == []
    assert len(results) == 3
