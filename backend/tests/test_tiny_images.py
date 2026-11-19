"""Tiny raster images: the image policy, the ``tiny_image`` outcome and the GIF findings.

Web pages saved next to their ``…-Dateien`` folders and e-mail attachments leave spacers,
icons, buttons and signature logos on a corpus's shares (778 GIFs of one law-firm corpus were
still unindexed on 2026-11-19, 78 % of them under 100 x 100 px or 1 KiB). Each used to cost a
Docling conversion and end as a ``no_chunks`` failure. Since this change a raster image below
the performance config's ``images.min_pixels`` / ``images.min_bytes`` is recorded as
``tiny_image`` without a conversion, an animated GIF is OCRed as its first frame only, and a
CSS or JavaScript file under a ``.gif`` name is an unreadable file, not a CSV table
(``docs/26-FORMAT_EDGE_AUDIT.md``, "Tiny raster images").
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")
Image = pytest.importorskip("PIL.Image")

from backend.models.schemas import FileClassification, IngestionStartRequest  # noqa: E402
from backend.services.image_policy import ImagePolicyConfig, image_policy_from  # noqa: E402
from backend.services.ingestion_counters import (  # noqa: E402
    JOB_COUNTER_FIELDS,
    apply_stats_counts,
    counts_as_failure,
)

JOB = "job-tiny-1"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No INGEST_IMAGE_* in the environment, no settings file."""
    for key in list(os.environ):
        if key.startswith(ingest.IMAGE_ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)

    def no_settings(**_kwargs):
        raise RuntimeError("no settings in the test harness")

    monkeypatch.setattr(ingest, "load_settings", no_settings)


def _image(path: Path, size=(16, 16), fmt: str = "GIF", frames: int = 1, pad_to: int = 0) -> Path:
    """Write a raster image; ``frames`` > 1 makes an animated GIF; ``pad_to`` pads the file."""
    first = Image.new("RGB", size, "white")
    if frames > 1:
        # Distinct frames: PIL's GIF writer merges identical consecutive ones
        rest = [Image.new("RGB", size, (i * 9 % 256, 0, 0)) for i in range(1, frames)]
        first.save(path, fmt, save_all=True, append_images=rest, duration=100, loop=0)
    else:
        first.save(path, fmt)
    if pad_to and path.stat().st_size < pad_to:
        # A GIF trailer tolerates trailing bytes; PIL still reads the header
        with path.open("ab") as handle:
            handle.write(b"\0" * (pad_to - path.stat().st_size))
    return path


# ---------------------------------------------------------------------------
# The policy: defaults, precedence, the probe
# ---------------------------------------------------------------------------


def test_policy_defaults_and_description() -> None:
    policy = ingest.ImagePolicy()
    assert (policy.min_pixels, policy.min_bytes) == (10_000, 1_024)
    assert policy.active and policy.describe() == "min 10000 px, min 1024 bytes"
    assert not ingest.ImagePolicy(0, 0).active and ingest.ImagePolicy(0, 0).describe() == "off"
    assert ingest.ImagePolicy(min_bytes=0).describe() == "min 10000 px"


def test_policy_settings_then_environment_then_stored_section(monkeypatch) -> None:
    class _Settings:
        ingest_image_min_pixels = 2_500
        ingest_image_min_bytes = 512

    monkeypatch.setattr(ingest, "load_settings", lambda **_k: _Settings())
    assert ingest.resolve_image_policy() == ingest.ImagePolicy(2_500, 512)
    monkeypatch.setenv("INGEST_IMAGE_MIN_PIXELS", "4000")
    monkeypatch.setenv("INGEST_IMAGE_MIN_BYTES", "nonsense")  # ignored with a warning
    assert ingest.resolve_image_policy() == ingest.ImagePolicy(4_000, 512)
    stored = {"min_pixels": "0", "min_bytes": 2048.0, "unknown": 1, "min_pixels_x": None}
    assert ingest.resolve_image_policy(stored) == ingest.ImagePolicy(0, 2_048)
    assert ingest.coerce_image_policy_value("min_bytes", -1) is None, "negative: unusable"
    assert ingest.coerce_image_policy_value("min_bytes", True) is None, "a bool is not a size"


def test_tiny_image_reason_by_bytes_then_pixels(tmp_path: Path) -> None:
    policy = ingest.ImagePolicy(min_pixels=10_000, min_bytes=1_024)
    spacer = _image(tmp_path / "spacer.gif", (1, 1))
    assert "bytes, below 1024" in ingest.tiny_image_reason(str(spacer), spacer.stat().st_size, policy)
    icon = _image(tmp_path / "icon.gif", (30, 30), pad_to=2_500)
    assert ingest.tiny_image_reason(str(icon), icon.stat().st_size, policy) == "30x30 px, below 10000"
    page = _image(tmp_path / "scan.png", (200, 100), fmt="PNG", pad_to=4_000)
    assert ingest.tiny_image_reason(str(page), page.stat().st_size, policy) is None
    # A thin rule: enough pixels, but 296 bytes (linie.gif, 1200 x 25 px on the shares)
    rule = _image(tmp_path / "linie.gif", (1200, 25))
    assert "bytes, below" in ingest.tiny_image_reason(str(rule), rule.stat().st_size, policy)
    # Only the dimensions a header gives: a non-image is not tiny by pixels
    css = tmp_path / "site.gif"
    css.write_text("/* CSS */ body { color: black }" * 100, encoding="utf-8")
    assert ingest.tiny_image_reason(str(css), css.stat().st_size, policy) is None
    assert ingest.tiny_image_reason(str(css), 100, policy) == "100 bytes, below 1024", "bytes need no header"
    # Never a document, never with the policy off
    assert ingest.tiny_image_reason(str(tmp_path / "akt.pdf"), 10, policy) is None
    assert ingest.tiny_image_reason(str(spacer), 10, ingest.ImagePolicy(0, 0)) is None
    assert ingest.is_raster_image_file("x.GIF") and not ingest.is_raster_image_file("x.pdf")
    assert ingest.RASTER_IMAGE_EXTENSIONS <= ingest.SUPPORTED_INGESTION_EXTENSIONS


# ---------------------------------------------------------------------------
# The outcome: retry flag, classification, counters, the API section
# ---------------------------------------------------------------------------


def test_retry_tiny_images_targets_tiny_image() -> None:
    assert ingest.RETRY_FLAG_ERROR_TYPES["retry_tiny_images"] == ingest.TINY_IMAGE == "tiny_image"
    assert ingest.retry_error_types({"retry_tiny_images": True}) == {"tiny_image"}
    assert ingest.map_error_type_to_classification(ingest.TINY_IMAGE, 0) == "tiny_image"
    assert FileClassification.TINY_IMAGE.value == ingest.TINY_IMAGE
    assert IngestionStartRequest(retry_tiny_images=True).model_dump()["retry_tiny_images"] is True
    assert IngestionStartRequest().retry_tiny_images is False
    assert ingest.TINY_IMAGE in ingest.IngestionStatsRecorder.COUNTED


def test_tiny_image_is_counted_but_is_not_a_failure() -> None:
    assert JOB_COUNTER_FIELDS["tiny_image"] == "tiny_images"
    job: Dict[str, Any] = {}
    apply_stats_counts(job, {"tiny_image": 600, "no_chunks": 2, "timeout": 1, "error": 0})
    assert job["tiny_images"] == 600 and job["failed_files"] == 3
    # The job's final failure count (API runner and worker) leaves the skips out too
    assert not counts_as_failure([f"{ingest.TINY_IMAGE_RESULT_PREFIX} (1x1 px, below 10000)"])
    assert not counts_as_failure(["Duplicate of: a.gif"]) and not counts_as_failure(["Deferred to a media run (retry_media)"])
    assert not counts_as_failure([]) and not counts_as_failure(None)
    assert counts_as_failure(["Conversion timed out"]) and counts_as_failure(["No content extracted from document"])


def test_api_images_section_mirrors_the_pipeline_policy() -> None:
    assert set(ImagePolicyConfig.model_fields) == set(ingest.ImagePolicy.__dataclass_fields__)
    assert ImagePolicyConfig().model_dump() == {"min_pixels": 10_000, "min_bytes": 1_024}
    assert image_policy_from(None) == ImagePolicyConfig()
    assert image_policy_from({"images": {"min_pixels": -5}}) == ImagePolicyConfig(), "invalid: defaults"
    stored = image_policy_from({"images": {"min_pixels": 0, "min_bytes": 4096}}).model_dump()
    assert ingest.resolve_image_policy(stored) == ingest.ImagePolicy(0, 4_096)


def test_performance_config_keeps_the_stored_section_when_omitted() -> None:
    system = pytest.importorskip("backend.routers.system")
    config = system.IngestionPerformanceConfig()
    assert config.images is None, "a client that does not know the section leaves it as stored"
    assert system.IngestionPerformanceConfig(images={"min_bytes": 2048}).images.min_bytes == 2048


# ---------------------------------------------------------------------------
# The loops: skipped unconverted, recorded, the documents still processed
# ---------------------------------------------------------------------------


class _Pipeline(ingest.DocumentIngestionPipeline):
    """Real ``ingest_documents`` over stubbed discovery, DB lookups and per-file processing."""

    def __init__(self, files: List[str]) -> None:
        self._initialized = True
        self.clean_before_ingest = False
        self.documents_folders: List[str] = []
        self.db = None
        self.files = files
        self.started: List[str] = []
        self.recorded: List[Any] = []

    def _find_document_files(self) -> List[str]:
        return list(self.files)

    async def _get_existing_sources(self) -> set:
        return set()

    async def _get_existing_hashes(self) -> dict:
        return {}

    async def _save_ingestion_stats(self, file_stats):
        self.recorded.extend(file_stats)
        return True

    async def _ingest_single_document(self, file_path: str, content_hash: Optional[str]):
        name = Path(file_path).name
        self.started.append(name)
        await asyncio.sleep(0)
        return ingest.IngestionResult(document_id=content_hash or "x", title=name,
                                      chunks_created=1, processing_time_ms=1.0, errors=[])


@pytest.fixture
def corpus(tmp_path: Path) -> List[str]:
    (tmp_path / "akt.txt").write_text("Kaufvertrag", encoding="utf-8")
    _image(tmp_path / "spacer.gif", (1, 1))
    _image(tmp_path / "icon.gif", (30, 30), pad_to=2_500)
    _image(tmp_path / "scan.png", (200, 100), fmt="PNG", pad_to=4_000)
    return [str(tmp_path / n) for n in ("akt.txt", "spacer.gif", "icon.gif", "scan.png")]


def _run(pipe: _Pipeline, **kwargs: Any) -> List[Any]:
    return asyncio.run(pipe.ingest_documents(incremental=True, job_id=JOB, process_isolation=False, **kwargs))


def test_concurrent_loop_records_tiny_images_and_converts_the_rest(corpus: List[str]) -> None:
    pipe = _Pipeline(corpus)
    results = _run(pipe, max_concurrent_files=4)
    assert sorted(pipe.started) == ["akt.txt", "scan.png"]
    tiny = {s.file_name: s for s in pipe.recorded if s.error_type == ingest.TINY_IMAGE}
    assert sorted(tiny) == ["icon.gif", "spacer.gif"]
    assert all(s.classification == "tiny_image" and not s.success and "retry_tiny_images" in s.error_message
               for s in tiny.values())
    assert "30x30 px" in tiny["icon.gif"].error_message and "bytes, below 1024" in tiny["spacer.gif"].error_message
    skipped = [r for r in results if r.errors and r.errors[0].startswith(ingest.TINY_IMAGE_RESULT_PREFIX)]
    assert len(skipped) == 2 and not any(counts_as_failure(r.errors) for r in skipped)
    assert pipe.image_policy == ingest.ImagePolicy()


def test_the_stored_section_and_a_disabled_policy(corpus: List[str]) -> None:
    pipe = _Pipeline(corpus)
    _run(pipe, max_concurrent_files=4, image_policy={"min_pixels": 0, "min_bytes": 0})
    assert sorted(pipe.started) == ["akt.txt", "icon.gif", "scan.png", "spacer.gif"], "off: everything converts"
    assert not [s for s in pipe.recorded if s.error_type == ingest.TINY_IMAGE]
    pipe = _Pipeline(corpus)
    _run(pipe, max_concurrent_files=4, image_policy={"min_pixels": 50_000, "min_bytes": 0})
    assert sorted(pipe.started) == ["akt.txt"], "200 x 100 px is below 50,000"


def test_sequential_loop_skips_tiny_images_the_same_way(corpus: List[str]) -> None:
    pipe = _Pipeline(corpus)
    results = _run(pipe, max_concurrent_files=1)
    assert sorted(pipe.started) == ["akt.txt", "scan.png"]
    assert sum(1 for r in results if r.errors and r.errors[0].startswith(ingest.TINY_IMAGE_RESULT_PREFIX)) == 2


def test_a_resumed_job_skips_its_tiny_image_records_unless_asked() -> None:
    class _Cursor:
        def __init__(self, docs):
            self.docs = docs

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for doc in self.docs:
                yield doc

    class _Failed:
        def __init__(self, docs):
            self.docs = docs

        def find(self, query, projection=None):
            return _Cursor([d for d in self.docs if d.get("job_id") == query["job_id"] and not d.get("resolved")])

    db = {"failed_documents": _Failed([
        {"file_path": "/x/spacer.gif", "error_type": "tiny_image", "job_id": JOB},
        {"file_path": "/x/akt.pdf", "error_type": "timeout", "job_id": JOB},
    ])}
    skip = asyncio.run(ingest.failed_paths_of_job(db, JOB))
    assert skip == {"/x/spacer.gif": "tiny_image", "/x/akt.pdf": "timeout"}
    skip = asyncio.run(ingest.failed_paths_of_job(db, JOB, ingest.retry_error_types({"retry_tiny_images": True})))
    assert skip == {"/x/akt.pdf": "timeout"}


# ---------------------------------------------------------------------------
# The reader: animated GIFs, non-images under an image name
# ---------------------------------------------------------------------------


def test_animated_gif_is_converted_as_its_first_frame(tmp_path: Path) -> None:
    banner = _image(tmp_path / "banner.gif", (120, 60), frames=27)
    assert ingest.image_frame_count(str(banner)) == 27
    path, temp = ingest.prepare_image_for_docling(str(banner))
    try:
        assert temp is not None and path == temp and path.endswith(".png")
        with Image.open(path) as frame:
            assert frame.size == (120, 60) and getattr(frame, "n_frames", 1) == 1
            assert frame.getpixel((0, 0)) == (255, 255, 255), "frame 0 (white), not the last (black)"
    finally:
        if temp and os.path.exists(temp):
            os.remove(temp)
    still = _image(tmp_path / "still.gif", (120, 60))
    assert ingest.prepare_image_for_docling(str(still)) == (str(still), None), "a static GIF passes through"


def test_a_stylesheet_under_a_gif_name_is_unreadable_not_a_table(tmp_path: Path, monkeypatch) -> None:
    css = tmp_path / "site.gif"
    css.write_text("/* CSS */\nbody { color: black }\n" * 50, encoding="utf-8")
    pipe = ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)

    def never(*_a, **_k):
        raise AssertionError("Docling must not sniff a non-image")

    monkeypatch.setattr(ingest, "create_image_converter", never)
    with pytest.raises(ingest.NotARasterImageError):
        ingest.prepare_image_for_docling(str(css))
    content, doc = pipe._read_document(str(css))
    assert doc is None and content.startswith("[Error:") and "not a raster image" in content
    assert ingest.placeholder_kind(content) == "error", "recorded as a failed read, nothing stored"
