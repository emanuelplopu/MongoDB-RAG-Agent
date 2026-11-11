"""Media transcription: the media config, media slots, deferral and the media run (retry_media).

Audio/video files used to go through the document path: one Docling thread per slot, the
Whisper model downloaded at first use, a size-based budget. Since 2026-11-07 they have their
own config (``MediaConfig`` in ``src/ingestion/ingest.py``, the performance config's ``media``
section), at most ``max_concurrent`` transcriptions at once, a duration-based budget, an
optional deferral to a media run, and ``retry_media`` targets the deferred records.
No Whisper model is loaded here.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")

from backend.models.schemas import FileClassification, IngestionStartRequest  # noqa: E402
from backend.services.ingestion_counters import JOB_COUNTER_FIELDS, apply_stats_counts  # noqa: E402
from backend.services.media_config import MediaTranscriptionConfig, media_config_from  # noqa: E402
from backend.services.media_run import ACTIVE_STATUSES, media_run_job, queue_media_run  # noqa: E402
from src.ingestion.conversion_workers import WorkerInit, _apply_init  # noqa: E402

JOB = "job-media-1"


@pytest.fixture(autouse=True)
def clean_media(monkeypatch):
    """No INGEST_MEDIA_* in the environment, no settings file, defaults restored afterwards."""
    for key in list(os.environ):
        if key.startswith(ingest.MEDIA_ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)

    def no_settings(**_kwargs):
        raise RuntimeError("no settings in the test harness")

    monkeypatch.setattr(ingest, "load_settings", no_settings)
    saved = ingest._media_config
    ingest._media_config = ingest.MediaConfig()
    yield
    ingest._media_config = saved


# ---------------------------------------------------------------------------
# Media files, the retry flag, the classification and the counter
# ---------------------------------------------------------------------------


def test_media_kinds_and_supported_extensions() -> None:
    assert ingest.media_kind("/x/Vorlesung.MP3") == "audio"
    assert ingest.media_kind("/x/clip.mov") == "video"
    assert ingest.media_kind("/x/akt.pdf") is None
    assert ingest.is_media_file("a.wmv") and not ingest.is_media_file("a.docx")
    assert ".wmv" in ingest.SUPPORTED_INGESTION_EXTENSIONS, "the audit's .wmv gap"
    assert ingest.MEDIA_EXTENSIONS - ingest.SUPPORTED_INGESTION_EXTENSIONS <= {".ogg"}


def test_retry_media_targets_media_deferred() -> None:
    assert ingest.RETRY_FLAG_ERROR_TYPES["retry_media"] == ingest.MEDIA_DEFERRED
    assert ingest.retry_error_types({"retry_media": True}) == {"media_deferred"}
    assert ingest.retry_error_types({"retry_media": True, "retry_timeouts": True}) == {"media_deferred", "timeout"}
    assert ingest.map_error_type_to_classification(ingest.MEDIA_DEFERRED, 0) == "media_deferred"
    assert FileClassification.MEDIA_DEFERRED.value == ingest.MEDIA_DEFERRED
    assert IngestionStartRequest(retry_media=True).model_dump()["retry_media"] is True
    assert IngestionStartRequest().retry_media is False


def test_media_deferred_is_counted_but_is_not_a_failure() -> None:
    assert JOB_COUNTER_FIELDS["media_deferred"] == "media_deferred_files"
    job: Dict[str, Any] = {}
    apply_stats_counts(job, {"media_deferred": 5, "timeout": 1, "error": 2, "no_chunks": 0})
    assert job["media_deferred_files"] == 5
    assert job["failed_files"] == 3


class _Cursor:
    def __init__(self, rows: List[dict]) -> None:
        self.rows = rows

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for row in self.rows:
            yield row


class _Failed:
    def __init__(self, rows: List[dict]) -> None:
        self.rows = rows

    def find(self, query: dict, projection: Optional[dict] = None) -> _Cursor:
        return _Cursor([r for r in self.rows if r.get("job_id") == query["job_id"] and r.get("resolved") is not True])


async def test_resume_skips_deferred_media_unless_the_job_retries_it() -> None:
    db = {"failed_documents": _Failed([
        {"job_id": JOB, "file_path": "/m/lecture.mp3", "error_type": "media_deferred", "resolved": False},
        {"job_id": JOB, "file_path": "/m/slow.pdf", "error_type": "timeout"},
    ])}
    assert await ingest.failed_paths_of_job(db, JOB) == {"/m/lecture.mp3": "media_deferred", "/m/slow.pdf": "timeout"}
    retried = await ingest.failed_paths_of_job(db, JOB, ingest.retry_error_types({"retry_media": True}))
    assert retried == {"/m/slow.pdf": "timeout"}


# ---------------------------------------------------------------------------
# MediaConfig resolution
# ---------------------------------------------------------------------------


def test_media_config_defaults() -> None:
    cfg = ingest.resolve_media_config(None)
    assert cfg == ingest.MediaConfig()
    assert (cfg.max_concurrent, cfg.threads, cfg.device, cfg.model, cfg.language) == (2, 0, "auto", "turbo", "")
    assert cfg.defer is False and cfg.video is True
    assert cfg.language_or_none() is None


def test_media_config_environment_then_stored_section(monkeypatch) -> None:
    monkeypatch.setenv("INGEST_MEDIA_MAX_CONCURRENT", "3")
    monkeypatch.setenv("INGEST_MEDIA_THREADS", "6")
    monkeypatch.setenv("INGEST_MEDIA_DEVICE", "cuda")
    monkeypatch.setenv("INGEST_MEDIA_LANGUAGE", "DE")
    monkeypatch.setenv("INGEST_MEDIA_DEFER", "true")
    monkeypatch.setenv("INGEST_MEDIA_MODEL", " ")  # empty: ignored
    env_only = ingest.resolve_media_config(None)
    assert (env_only.max_concurrent, env_only.threads, env_only.device) == (3, 6, "cuda")
    assert env_only.language == "de" and env_only.defer is True and env_only.model == "turbo"

    stored = ingest.resolve_media_config({
        "max_concurrent": 20,  # clamped to the cap
        "device": "tpu",  # unusable: the environment's value stays
        "video": "false",
        "defer": 0,
        "unknown": 1,  # ignored
        "budget_per_media_second": "2",
        "max_budget_seconds": 100,  # below the minimum budget: lifted to it
        "language": None,  # not a value
    })
    assert stored.max_concurrent == ingest.MEDIA_MAX_CONCURRENT_CAP
    assert stored.device == "cuda"
    assert stored.video is False and stored.defer is False
    assert stored.budget_per_media_second == 2.0
    assert stored.max_budget_seconds == stored.min_budget_seconds == 600.0
    assert stored.language == "de"


def test_media_config_threads_device_and_description(monkeypatch) -> None:
    cfg = ingest.MediaConfig(max_concurrent=2)
    assert cfg.resolved_threads(cpu_count=20) == 10
    assert ingest.MediaConfig(max_concurrent=8).resolved_threads(cpu_count=4) == 1
    assert ingest.MediaConfig(threads=64).resolved_threads(cpu_count=4) == ingest.MEDIA_MAX_THREADS
    assert ingest.MediaConfig(threads=3).resolved_threads(cpu_count=64) == 3

    monkeypatch.setattr(ingest, "detect_available_device", lambda: "cuda")
    assert ingest.MediaConfig().resolved_device() == "cuda"
    assert ingest.MediaConfig(device="cpu").resolved_device() == "cpu"
    monkeypatch.setattr(ingest, "detect_available_device", lambda: "mps")
    assert ingest.MediaConfig().resolved_device() == "cpu", "native Whisper runs on the CPU or CUDA only"
    assert ingest.MediaConfig(device="cuda").resolved_device() == "cuda"

    text = ingest.MediaConfig(language="de", defer=True, video=False).describe()
    assert "language de" in text and "video off" in text and "deferred" in text


def test_update_media_config_and_worker_init() -> None:
    _apply_init(WorkerInit(pool_workers=2, media={"model": "small", "threads": 3, "bogus": 1}, warm_up=False))
    cfg = ingest.get_media_config()
    assert (cfg.model, cfg.threads) == ("small", 3)
    assert not hasattr(cfg, "bogus")
    assert WorkerInit().media is None


# ---------------------------------------------------------------------------
# Budget, checkpoint lookup, ffmpeg helpers
# ---------------------------------------------------------------------------


def test_media_budget_follows_the_duration(monkeypatch) -> None:
    cfg = ingest.MediaConfig()  # 4 s per media second, 600 .. 14400
    assert ingest.media_budget_seconds("x.mp3", 0, cfg, duration=1000.0) == 4000.0
    assert ingest.media_budget_seconds("x.mp3", 0, cfg, duration=10.0) == 600.0, "the floor covers the model load"
    assert ingest.media_budget_seconds("x.mp3", 0, cfg, duration=7200.0) == 14400.0, "capped"
    # Without a duration: ~60 s of audio or ~8 s of video per MB
    monkeypatch.setattr(ingest, "media_duration_seconds", lambda path, timeout=30.0: None)
    assert ingest.media_budget_seconds("x.mp3", 16 * 1024 * 1024, cfg) == 16 * 60 * 4
    assert ingest.media_budget_seconds("x.mp4", 16 * 1024 * 1024, cfg) == 600.0
    assert ingest.media_budget_seconds("x.mp4", 160 * 1024 * 1024, ingest.MediaConfig(min_budget_seconds=1)) == 160 * 8 * 4


def test_media_budget_probes_ffprobe_once(monkeypatch) -> None:
    calls: List[str] = []

    def probe(path: str, timeout: float = 30.0) -> Optional[float]:
        calls.append(path)
        return 90.0

    monkeypatch.setattr(ingest, "media_duration_seconds", probe)
    assert ingest.media_budget_seconds("y.wav", 1, ingest.MediaConfig(budget_per_media_second=10, min_budget_seconds=1)) == 900.0
    assert calls == ["y.wav"]


def test_media_duration_without_ffprobe(monkeypatch) -> None:
    monkeypatch.setattr(ingest.shutil, "which", lambda name: None)
    assert ingest.media_duration_seconds("anything.mp3") is None


def test_whisper_checkpoint_path() -> None:
    pytest.importorskip("whisper")
    path = ingest.whisper_checkpoint_path("turbo", "/opt/docling-models")
    assert path is not None and os.path.basename(path) == "large-v3-turbo.pt"
    assert os.path.dirname(path) == "/opt/docling-models"
    assert ingest.whisper_checkpoint_path("turbo", None) is None, "no artifacts path: whisper's own cache"
    assert ingest.whisper_checkpoint_path("openai/whisper-large-v3", "/opt/docling-models") is None


def test_extract_audio_track_without_ffmpeg(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ingest.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        ingest.extract_audio_track(str(tmp_path / "clip.mp4"), str(tmp_path / "clip.wav"))


def test_torch_thread_limit_restores_the_count() -> None:
    torch = pytest.importorskip("torch")
    before = torch.get_num_threads()
    with ingest.torch_thread_limit(1):
        assert torch.get_num_threads() == 1
    assert torch.get_num_threads() == before
    with ingest.torch_thread_limit(None):
        assert torch.get_num_threads() == before


def test_reader_fails_fast_when_the_model_is_not_baked(monkeypatch, tmp_path: Path) -> None:
    pytest.importorskip("whisper")
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(tmp_path))
    audio = tmp_path / "lecture.mp3"
    audio.write_bytes(b"\x00" * 10)
    reader = ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)
    content, document = reader._transcribe_audio_local(str(audio))
    assert document is None and content.startswith("[Error: Whisper model turbo is not available under")
    assert ingest.placeholder_kind(content) == "error", "never stored, recorded as a failed read"


def test_a_partial_checkpoint_is_an_error_not_a_download(monkeypatch, tmp_path: Path) -> None:
    """The live container of 2026-11-06 held 893 MB of the 1.6 GB turbo checkpoint: whisper
    would re-download it on every load. The reader checks the SHA-256 whisper pins, once per
    file and process."""
    import hashlib

    whisper = pytest.importorskip("whisper")
    good = b"a tiny checkpoint"
    monkeypatch.setitem(whisper._MODELS, "turbo", f"https://example.invalid/{hashlib.sha256(good).hexdigest()}/large-v3-turbo.pt")
    monkeypatch.setattr(ingest, "_verified_checkpoints", {})
    checkpoint = tmp_path / "large-v3-turbo.pt"

    checkpoint.write_bytes(good[:5])
    problem = ingest.whisper_checkpoint_problem("turbo", str(tmp_path))
    assert problem is not None and "partial download" in problem and "SHA-256 mismatch" in problem

    checkpoint.write_bytes(good)
    assert ingest.whisper_checkpoint_problem("turbo", str(tmp_path)) is None
    assert str(checkpoint) in ingest._verified_checkpoints, "verified once per process"
    assert ingest.whisper_checkpoint_problem("turbo", None) is None, "whisper's own cache: not checked"
    assert ingest.whisper_checkpoint_problem("openai/whisper-large-v3", str(tmp_path)) is None

    reader = ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)
    checkpoint.write_bytes(good[:3])
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(tmp_path))
    audio = tmp_path / "lecture.mp3"
    audio.write_bytes(b"\x00" * 10)
    content, document = reader._transcribe_audio_local(str(audio))
    assert document is None and "partial download" in content and content.startswith("[Error:")


def test_video_off_is_a_skip_placeholder(tmp_path: Path) -> None:
    ingest.update_media_config(video=False)
    reader = ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)
    content, document = reader._transcribe_video(str(tmp_path / "zeuge.mp4"))
    assert document is None and content.startswith("[Skipped: video transcription is off")
    assert ingest.placeholder_kind(content) == "error"


# ---------------------------------------------------------------------------
# The API's media section and the media run job
# ---------------------------------------------------------------------------


def test_api_media_section_mirrors_the_pipeline_config() -> None:
    api_fields = set(MediaTranscriptionConfig.model_fields)
    pipeline_fields = set(ingest.MediaConfig.__dataclass_fields__)
    assert api_fields == pipeline_fields
    assert MediaTranscriptionConfig().model_dump() == {
        k: getattr(ingest.MediaConfig(), k) for k in pipeline_fields
    }
    assert media_config_from(None) == MediaTranscriptionConfig()
    assert media_config_from({"media": {"max_concurrent": 99}}) == MediaTranscriptionConfig(), "invalid: defaults"
    assert media_config_from({"media": {"language": " DE ", "device": "cuda"}}).language == "de"
    with pytest.raises(ValueError):
        MediaTranscriptionConfig(min_budget_seconds=100, max_budget_seconds=10)
    # The pipeline accepts what the API stores
    stored = media_config_from({"media": {"max_concurrent": 1, "threads": 4, "defer": True}}).model_dump()
    assert ingest.resolve_media_config(stored).defer is True


class _Jobs:
    def __init__(self, docs: List[dict]) -> None:
        self.docs = docs

    async def find_one(self, query: dict, projection: Optional[dict] = None) -> Optional[dict]:
        wanted = set(query["status"]["$in"])
        return next((d for d in self.docs if d.get("status") in wanted), None)

    async def insert_one(self, doc: dict) -> None:
        self.docs.append(doc)


def test_media_run_job_is_what_the_worker_claims() -> None:
    job = media_run_job("diejuristin", tenant="quellex")
    assert job["status"] == "pending" and job["runner"] == "worker" and job["tenant"] == "quellex"
    assert job["config"]["retry_media"] is True and job["config"]["incremental"] is True
    assert job["config"]["profile"] == "diejuristin" and job["profile"] == "diejuristin"
    assert "tenant" not in media_run_job("x")
    assert media_run_job("x", incremental=False)["config"]["incremental"] is False
    assert {"pending", "running", "paused", "PENDING", "RUNNING", "PAUSED"} <= set(ACTIVE_STATUSES)


async def test_queue_media_run_refuses_while_a_job_is_active() -> None:
    jobs = _Jobs([{"_id": "live", "status": "running", "profile": "p", "runner": "api"}])
    with pytest.raises(RuntimeError, match="live is running"):
        await queue_media_run({"ingestion_jobs": jobs}, "p")
    jobs = _Jobs([{"_id": "old", "status": "completed"}])
    job = await queue_media_run({"ingestion_jobs": jobs}, "p", tenant="quellex")
    assert jobs.docs[-1]["_id"] == job["job_id"] and jobs.docs[-1]["status"] == "pending"
    # A job the worker runs (any spelling) blocks the next one too
    jobs = _Jobs([{"_id": "w", "status": "RUNNING", "profile": "p", "runner": "worker"}])
    with pytest.raises(RuntimeError, match="w is RUNNING"):
        await queue_media_run({"ingestion_jobs": jobs}, "p")


def test_docling_device_pin_keeps_the_layout_models_off_a_media_gpu(monkeypatch) -> None:
    """docker-compose.media-gpu.yml attaches a GPU for Whisper and pins Docling to the CPU."""
    monkeypatch.setattr(ingest, "detect_available_device", lambda: "cuda")
    monkeypatch.delenv("INGEST_DOCLING_DEVICE", raising=False)
    saved = ingest._accelerator_config.device
    try:
        ingest._accelerator_config.device = "auto"
        assert ingest.get_effective_device() == "cuda", "auto follows the hardware"
        monkeypatch.setenv("INGEST_DOCLING_DEVICE", "cpu")
        assert ingest.get_effective_device() == "cpu"
        assert ingest.MediaConfig().resolved_device() == "cuda", "Whisper resolves its own device"
        monkeypatch.setenv("INGEST_DOCLING_DEVICE", "tpu")
        assert ingest.get_effective_device() == "cuda", "an unknown value is ignored"
        ingest._accelerator_config.device = "cuda"
        monkeypatch.setenv("INGEST_DOCLING_DEVICE", " CPU ")
        assert ingest.get_effective_device() == "cpu", "the pin wins over the stored accelerator config"
    finally:
        ingest._accelerator_config.device = saved


def test_media_gpu_layer_reserves_the_gpu_for_the_worker_only() -> None:
    yaml = pytest.importorskip("yaml")
    layer = yaml.safe_load((Path(__file__).resolve().parents[2] / "docker-compose.media-gpu.yml").read_text(encoding="utf-8"))
    assert list(layer["services"]) == ["ingestion-worker"], "an API container with a GPU reservation cannot start without the GPU"
    worker = layer["services"]["ingestion-worker"]
    assert "INGEST_DOCLING_DEVICE=cpu" in worker["environment"]
    device = worker["deploy"]["resources"]["reservations"]["devices"][0]
    assert device["driver"] == "nvidia" and device["capabilities"] == ["gpu"]


# ---------------------------------------------------------------------------
# Job statuses: the API and the worker speak the same spelling
# ---------------------------------------------------------------------------


def test_status_spellings_and_normalisation() -> None:
    from backend.core import ingestion_jobs as jobs_core
    from backend.models.schemas import IngestionStatus

    assert jobs_core.status_spellings("pending") == ["pending", "PENDING"]
    assert jobs_core.status_spellings(IngestionStatus.RUNNING, "PAUSED") == ["running", "RUNNING", "paused", "PAUSED"]
    assert jobs_core.ACTIVE_JOB_STATUSES == ["pending", "PENDING", "running", "RUNNING", "paused", "PAUSED"]
    assert jobs_core.normalize_status("RUNNING") == jobs_core.normalize_status(IngestionStatus.RUNNING) == "running"
    assert jobs_core.normalize_status(None) == ""
    # The stored states are the API schema's values
    for name in ("PENDING", "RUNNING", "PAUSED", "COMPLETED", "FAILED", "STOPPED", "CANCELLED"):
        assert getattr(jobs_core, f"JOB_{name}") == getattr(IngestionStatus, name).value


class _WorkerJobs:
    """``ingestion_jobs`` as the worker's poll sees it: equality and ``$in`` filters."""

    def __init__(self, docs: List[dict]) -> None:
        self.docs = docs

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        for key, wanted in query.items():
            value = doc.get(key)
            if isinstance(wanted, dict) and "$in" in wanted:
                if value not in wanted["$in"]:
                    return False
            elif value != wanted:
                return False
        return True

    async def find_one_and_update(self, query: dict, update: dict, sort=None, return_document=None) -> Optional[dict]:
        for doc in self.docs:
            if self._matches(doc, query):
                doc.update(update["$set"])
                return dict(doc)
        return None


async def test_the_worker_claims_a_job_the_api_queued_for_it() -> None:
    """Until 2026-11-11 the API stored ``pending`` and the worker polled ``PENDING``: never claimed."""
    import bson

    worker_module = pytest.importorskip("backend.workers.ingestion_worker")
    from backend.core.ingestion_jobs import ACTIVE_JOB_STATUSES
    from backend.models.schemas import IngestionStatus

    assert worker_module.IngestionStatus.PENDING == IngestionStatus.PENDING.value == "pending"
    assert worker_module.IngestionStatus.RUNNING == "running" and worker_module.IngestionStatus.COMPLETED == "completed"
    assert worker_module.IngestionStatus.INTERRUPTED == "INTERRUPTED", "kept: both processes resume on exactly this value"

    # What the API's start endpoint writes for the worker, as MongoDB stores it
    api_job = bson.decode(bson.encode({
        "_id": "from-api", "status": IngestionStatus.PENDING, "runner": "worker", "created_at": "2026-11-11T10:00:00",
        "config": IngestionStartRequest(profile="p", retry_media=True).model_dump(),
    }))
    assert api_job["status"] == "pending"
    legacy_job = {"_id": "legacy", "status": "PENDING", "runner": None, "created_at": "2026-11-11T10:01:00"}
    api_run = {"_id": "api-run", "status": "pending", "runner": "api", "created_at": "2026-11-11T09:00:00"}
    jobs = _WorkerJobs([api_run, api_job, legacy_job])

    worker = worker_module.IngestionWorker()
    worker.db = {"ingestion_jobs": jobs}
    claimed = await worker._poll_next_job()
    assert claimed["job_id"] == "from-api" and claimed["status"] == "running" and claimed["runner"] == "worker"
    assert claimed["status"] in ACTIVE_JOB_STATUSES, "the API's start guard sees the job the worker runs"
    assert (await worker._poll_next_job())["job_id"] == "legacy", "a job queued by an older build is still claimed"
    assert await worker._poll_next_job() is None, "never a job the API runs"
    assert api_run["status"] == "pending"

    # The media run the CLI queues is claimed the same way
    queued = media_run_job("p")
    jobs = _WorkerJobs([queued])
    worker.db = {"ingestion_jobs": jobs}
    assert (await worker._poll_next_job())["config"]["retry_media"] is True


# ---------------------------------------------------------------------------
# The loops: deferral, the media run, media slots
# ---------------------------------------------------------------------------


class _Pipeline(ingest.DocumentIngestionPipeline):
    """Real ``ingest_documents`` over stubbed discovery, DB lookups and per-file processing."""

    def __init__(self, files: List[str], *, media_delay: float = 0.0) -> None:
        self._initialized = True
        self.clean_before_ingest = False
        self.documents_folders: List[str] = []
        self.db = None
        self.files = files
        self.started: List[str] = []
        self.media_delay = media_delay
        self.active_media = 0
        self.max_active_media = 0
        self.max_active = 0
        self.active = 0

    def _find_document_files(self) -> List[str]:
        return list(self.files)

    async def _get_existing_sources(self) -> set:
        return {"done.mp3"}

    async def _get_existing_hashes(self) -> dict:
        return {}

    async def _ingest_single_document(self, file_path: str, content_hash: Optional[str]):
        name = Path(file_path).name
        self.started.append(name)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if ingest.is_media_file(file_path):
            self.active_media += 1
            self.max_active_media = max(self.max_active_media, self.active_media)
            await asyncio.sleep(self.media_delay)
            self.active_media -= 1
        else:
            await asyncio.sleep(0)
        self.active -= 1
        return ingest.IngestionResult(document_id=content_hash or "x", title=name,
                                      chunks_created=1, processing_time_ms=1.0, errors=[])


@pytest.fixture
def corpus(tmp_path: Path) -> List[str]:
    names = ["akt.txt", "done.mp3", "vortrag.mp3", "zeuge.mp4", "notiz.docx", "aufnahme.m4a"]
    for name in names:
        (tmp_path / name).write_text(f"content of {name}", encoding="utf-8")
    return [str(tmp_path / name) for name in names]


def _run(pipe: _Pipeline, **kwargs: Any) -> List[Any]:
    return asyncio.run(pipe.ingest_documents(incremental=True, job_id=JOB, process_isolation=False, **kwargs))


def test_media_run_processes_only_media_files_not_yet_ingested(corpus: List[str]) -> None:
    pipe = _Pipeline(corpus)
    _run(pipe, media_only=True, max_concurrent_files=4)
    assert sorted(pipe.started) == ["aufnahme.m4a", "vortrag.mp3", "zeuge.mp4"], "done.mp3 is ingested already"


def test_media_only_run_ignores_the_deferral(corpus: List[str]) -> None:
    pipe = _Pipeline(corpus)
    _run(pipe, media_only=True, media_config={"defer": True}, max_concurrent_files=4)
    assert sorted(pipe.started) == ["aufnahme.m4a", "vortrag.mp3", "zeuge.mp4"]


def test_deferral_records_media_and_processes_the_documents(corpus: List[str]) -> None:
    recorded: List[ingest.IngestionFileStats] = []

    class _Recording(_Pipeline):
        async def _save_ingestion_stats(self, file_stats):
            recorded.extend(file_stats)
            return True

    pipe = _Recording(corpus)
    results = _run(pipe, media_config={"defer": True}, max_concurrent_files=4)
    assert sorted(pipe.started) == ["akt.txt", "notiz.docx"]
    deferred = [s for s in recorded if s.error_type == ingest.MEDIA_DEFERRED]
    assert sorted(s.file_name for s in deferred) == ["aufnahme.m4a", "vortrag.mp3", "zeuge.mp4"]
    assert all(s.classification == "media_deferred" and not s.success and "retry_media" in s.error_message for s in deferred)
    assert sum(1 for r in results if r.errors and "media run" in r.errors[0]) == 3

    # The sequential loop defers the same way (no stats recorder there)
    pipe = _Pipeline(corpus)
    results = _run(pipe, media_config={"defer": True}, max_concurrent_files=1)
    assert sorted(pipe.started) == ["akt.txt", "notiz.docx"]
    assert sum(1 for r in results if r.errors and "media run" in r.errors[0]) == 3


def test_a_job_that_retries_media_transcribes_despite_the_deferral(corpus: List[str]) -> None:
    pipe = _Pipeline(corpus)
    _run(pipe, media_config={"defer": True}, max_concurrent_files=4,
         retry_failed_types=ingest.retry_error_types({"retry_media": True}))
    assert sorted(pipe.started) == ["akt.txt", "aufnahme.m4a", "notiz.docx", "vortrag.mp3", "zeuge.mp4"]


def test_media_slots_cap_the_transcriptions_at_once(tmp_path: Path) -> None:
    names = [f"vortrag{i}.mp3" for i in range(6)] + ["akt.txt"]
    for name in names:
        (tmp_path / name).write_text(name, encoding="utf-8")
    files = [str(tmp_path / n) for n in names]

    pipe = _Pipeline(files, media_delay=0.05)
    _run(pipe, media_config={"max_concurrent": 2}, max_concurrent_files=8)
    assert len(pipe.started) == 7
    assert pipe.max_active_media == 2, "never more transcriptions than media slots"
    assert pipe.media_config.max_concurrent == 2 and ingest.get_media_config().max_concurrent == 2

    pipe = _Pipeline(files, media_delay=0.05)
    _run(pipe, media_config={"max_concurrent": 4}, max_concurrent_files=8)
    assert pipe.max_active_media == 4


class _FakePool:
    """Stands in for ConversionProcessPool: records what it was asked to convert."""

    instances: List["_FakePool"] = []

    def __init__(self, size: int, init: Any = None) -> None:
        self.size, self.init, self.converted, self.shut = size, init, [], False
        _FakePool.instances.append(self)
        self.kills, self.starts = 0, 0

    async def convert_with_notes(self, file_path: str, on_started=None, options=None):
        self.converted.append(Path(file_path).name)
        if on_started:
            on_started()
        return "text", None, {}

    def shutdown(self) -> None:
        self.shut = True


def test_media_files_convert_in_dedicated_media_workers(monkeypatch) -> None:
    import src.ingestion.conversion_workers as workers

    monkeypatch.setattr(workers, "ConversionProcessPool", _FakePool)
    _FakePool.instances.clear()
    pipe = _Pipeline([])
    pipe.media_config = ingest.MediaConfig(max_concurrent=2, model="small")
    ingest.update_media_config(max_concurrent=2, model="small")
    pipe._start_conversion_pool(8, media_workers=2)
    docs, media = _FakePool.instances
    assert (docs.size, media.size) == (8, 2)
    assert docs.init.media_worker is False and media.init.media_worker is True
    assert media.init.warm_up is False, "a media worker never loads the PDF models"
    assert media.init.media["model"] == "small"

    asyncio.run(pipe._convert_document("/m/vortrag.mp3"))
    asyncio.run(pipe._convert_document("/m/zeuge.mp4"))
    asyncio.run(pipe._convert_document("/m/akt.pdf"))
    assert media.converted == ["vortrag.mp3", "zeuge.mp4"] and docs.converted == ["akt.pdf"]

    asyncio.run(pipe._stop_conversion_pool())
    assert docs.shut and media.shut and pipe._media_pool is None and pipe._conversion_pool is None

    # No media workers: media files stay in the conversion pool
    _FakePool.instances.clear()
    pipe._start_conversion_pool(4)
    assert len(_FakePool.instances) == 1 and pipe._media_pool is None
    asyncio.run(pipe._convert_document("/m/vortrag.mp3"))
    assert _FakePool.instances[0].converted == ["vortrag.mp3"]
    asyncio.run(pipe._stop_conversion_pool())


def test_a_run_with_media_starts_media_workers_unless_deferred(monkeypatch, corpus: List[str]) -> None:
    started: List[tuple] = []

    class _Tracking(_Pipeline):
        def _start_conversion_pool(self, size: int, media_workers: int = 0):
            started.append((size, media_workers))
            self._conversion_pool = None  # keep converting through the stubbed reader

        async def _stop_conversion_pool(self) -> None:
            return None

    monkeypatch.setattr(ingest, "process_isolation_enabled", lambda value=None: True)

    def run_isolated(pipe: _Tracking, **kwargs: Any) -> None:
        asyncio.run(pipe.ingest_documents(incremental=True, job_id=JOB, process_isolation=True, **kwargs))

    run_isolated(_Tracking(corpus), media_config={"max_concurrent": 3}, max_concurrent_files=8)
    run_isolated(_Tracking(corpus), media_config={"max_concurrent": 3, "defer": True}, max_concurrent_files=8)
    run_isolated(_Tracking([c for c in corpus if not ingest.is_media_file(c)]), max_concurrent_files=2)
    run_isolated(_Tracking(corpus), media_config={"max_concurrent": 8}, max_concurrent_files=2)
    assert started == [(8, 3), (8, 0), (2, 0), (2, 2)], "media workers only with media files, never more than the slots"


def test_media_workers_keep_the_whisper_converter(monkeypatch, tmp_path: Path) -> None:
    pytest.importorskip("docling")
    import docling.document_converter as dc

    class _Doc:
        pass

    class _Result:
        def __init__(self) -> None:
            from docling.datamodel.base_models import ConversionStatus

            self.status, self.errors, self.document = ConversionStatus.SUCCESS, [], _Doc()

    built: List[Any] = []

    class _FakeConverter:
        def __init__(self, format_options=None) -> None:
            built.append(self)

        def convert(self, path, raises_on_error=True):
            return _Result()

    monkeypatch.setattr(dc, "DocumentConverter", _FakeConverter)
    monkeypatch.setattr(ingest, "export_document_markdown", lambda document: "transcript")
    monkeypatch.setattr(ingest, "media_duration_seconds", lambda path, timeout=30.0: 60.0)
    monkeypatch.setattr(ingest, "_whisper_converters", {})
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"\x00")
    reader = ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)
    cfg = ingest.MediaConfig(model="turbo")

    ingest.enable_media_model_cache(False)
    reader._run_whisper(str(audio), "a.mp3", cfg, "cpu", 2)
    reader._run_whisper(str(audio), "a.mp3", cfg, "cpu", 2)
    assert len(built) == 2 and not ingest._whisper_converters, "a conversion worker loads per file"

    ingest.enable_media_model_cache(True)
    try:
        content, document = reader._run_whisper(str(audio), "a.mp3", cfg, "cpu", 2)
        reader._run_whisper(str(audio), "a.mp3", cfg, "cpu", 2)
        assert len(built) == 3 and content == "transcript" and isinstance(document, _Doc)
        assert list(ingest._whisper_converters) == [("turbo", "cpu", 2)]
        reader._run_whisper(str(audio), "a.mp3", cfg, "cuda", 2)
        assert len(built) == 4, "one converter per model/device/threads"
    finally:
        ingest.enable_media_model_cache(False)
    assert not ingest._whisper_converters


def test_the_run_resolves_the_media_config_for_the_readers(corpus: List[str]) -> None:
    pipe = _Pipeline(corpus)
    _run(pipe, media_config={"language": "de", "threads": 5, "model": "small"}, max_concurrent_files=2)
    cfg = ingest.get_media_config()
    assert (cfg.language, cfg.threads, cfg.model) == ("de", 5, "small")
    assert pipe.media_config == cfg
