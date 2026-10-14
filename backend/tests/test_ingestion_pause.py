"""Regression: POST /ingestion/pause must hold an API-started run between files.

The pipeline's synchronous progress_callback only schedules a fire-and-forget task, so the
pause loop that used to live in that task never held the pipeline: files kept being
processed while the job reported itself as paused. run_ingestion now passes the pipeline
an awaitable pause_checkpoint that it awaits before each file.

These tests run the real run_ingestion and the real sequential/concurrent per-file loops;
only discovery, per-document processing and persistence are stubbed.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")
ingestion = pytest.importorskip("backend.routers.ingestion")
from backend.models.schemas import IngestionStatus  # noqa: E402

FILE_NAMES = [f"doc{i}.md" for i in range(5)]
MODES = pytest.mark.parametrize("max_concurrent", [1, 2], ids=["sequential", "concurrent"])


class _Collection:
    """Async collection stub: the performance-config lookup and the final log write."""

    def __init__(self, doc: Optional[dict] = None) -> None:
        self.doc = doc

    async def find_one(self, _query: dict) -> Optional[dict]:
        return self.doc

    async def update_one(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _Harness:
    """Starts run_ingestion over real pipeline loops and records which files start."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.started: list[str] = []
        self.in_flight = 0
        # Stands in for the documents collection: sources of ingested files; survives a restart
        self.ingested: set[str] = set()
        self.cleans = 0  # pipeline _clean_databases calls
        self.pipelines: list[dict] = []  # keyword arguments of each pipeline created
        self.saved: list[dict] = []
        self.pause_when_file_starts: Optional[int] = None  # 1-based: request a pause while this file runs
        # 1-based file number -> coroutine function awaited while that file runs (e.g. a stop request)
        self.when_file_starts: dict[int, Callable[[], Awaitable[Any]]] = {}
        self.job: Optional[asyncio.Task] = None
        self.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=None)))

        files = []
        for name in FILE_NAMES:
            path = tmp_path / name
            path.write_text(f"content of {name}", encoding="utf-8")  # distinct hashes: no duplicate skips
            files.append(str(path))
        harness = self

        class _Pipeline(ingest.DocumentIngestionPipeline):
            def __init__(self, **kwargs: Any) -> None:
                harness.pipelines.append(kwargs)
                self._initialized = True
                self.clean_before_ingest = kwargs.get("clean_before_ingest", False)
                self.documents_folders: list[str] = []  # a file's source is its name
                self.db = None

            def _find_document_files(self) -> list[str]:
                return list(files)

            async def _clean_databases(self) -> None:
                harness.cleans += 1
                harness.ingested.clear()

            async def _get_existing_sources(self) -> set:
                return set(harness.ingested)

            async def _get_existing_hashes(self) -> dict:
                return {}

            async def _ingest_single_document(self, file_path: str, content_hash: str):
                harness.started.append(Path(file_path).name)
                harness.in_flight += 1
                try:
                    if len(harness.started) == harness.pause_when_file_starts:
                        await ingestion.pause_ingestion(harness.request)
                    action = harness.when_file_starts.get(len(harness.started))
                    if action is not None:
                        await action()
                    await asyncio.sleep(0.02)
                    harness.ingested.add(Path(file_path).name)
                    return ingest.IngestionResult(
                        document_id=content_hash, title=Path(file_path).name,
                        chunks_created=1, processing_time_ms=1.0, errors=[],
                    )
                finally:
                    harness.in_flight -= 1

        async def save_job(_db: Any, job: dict) -> None:
            self.saved.append(dict(job))

        async def latest_job(_db: Any) -> Optional[dict]:
            return dict(self.saved[-1]) if self.saved else None

        async def nothing(*_args: Any, **_kwargs: Any) -> None:
            return None

        async def no_metrics(*_args: Any, **_kwargs: Any) -> dict:
            return {}

        async def jobs_collection(_db: Any) -> _Collection:
            return _Collection()

        monkeypatch.setattr(ingest, "DocumentIngestionPipeline", _Pipeline)
        monkeypatch.setattr(ingestion, "save_job_to_db", save_job)
        monkeypatch.setattr(ingestion, "get_latest_job_from_db", latest_job)
        monkeypatch.setattr(ingestion, "get_job_from_db", nothing)
        monkeypatch.setattr(ingestion, "get_jobs_collection", jobs_collection)
        monkeypatch.setattr(ingestion, "_apply_offline_mode_for_ingestion", nothing)
        monkeypatch.setattr(ingestion, "_build_pending_files_queue", nothing)
        monkeypatch.setattr(ingestion, "_calculate_job_extended_metrics", no_metrics)
        monkeypatch.setattr(ingestion, "trigger_post_ingestion_backup", nothing)
        monkeypatch.setattr(ingestion, "_PAUSE_POLL_SECONDS", 0.01)

    def connect(self, max_concurrent: int) -> SimpleNamespace:
        """Give the API a stub DB whose performance config sets max_concurrent_files."""
        db = SimpleNamespace(db={"ingestion_config": _Collection({"max_concurrent_files": max_concurrent})})
        self.request.app.state.db = db
        return db

    def start(self, max_concurrent: int, **config: Any) -> asyncio.Task:
        """Start run_ingestion; ``config`` overrides the request (default: a non-incremental run)."""
        db = self.connect(max_concurrent)
        config = {"incremental": False, "clean_before_ingest": False, **config}
        self.job = asyncio.create_task(ingestion.run_ingestion("job-pause-test", config, db))
        return self.job

    async def wait_until_held(self, timeout: float = 5.0) -> list[str]:
        """Wait until the run reports paused with no file in progress; return the files started so far."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not (ingestion._is_paused and self.in_flight == 0):
            assert loop.time() < deadline, f"run never paused; started={self.started}"
            await asyncio.sleep(0.005)
        return list(self.started)

    def statuses(self) -> list[Any]:
        return [job.get("status") for job in self.saved]


@pytest.fixture
async def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    h = _Harness(tmp_path, monkeypatch)
    yield h
    # Never leave a held run or its module-level flags behind for other tests
    if h.job is not None and not h.job.done():
        ingestion._stop_requested = True
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(h.job, timeout=5)
    ingestion._stop_requested = False
    ingestion._pause_requested = False
    ingestion._is_paused = False


@MODES
async def test_no_new_file_starts_while_paused_and_resume_continues(harness: _Harness, max_concurrent: int) -> None:
    harness.pause_when_file_starts = 1
    job = harness.start(max_concurrent)

    held = await harness.wait_until_held()
    # Only files already past the pause point when pause was requested may have started
    assert 1 <= len(held) <= max_concurrent

    status = await ingestion.get_ingestion_status(harness.request)
    assert (status.status, status.is_paused, status.can_pause, status.can_stop) == (
        IngestionStatus.PAUSED, True, False, True,
    )

    await asyncio.sleep(0.3)  # ~30 pause polls
    assert harness.started == held, "a new file started while the run was paused"

    await ingestion.resume_ingestion(harness.request)
    status = await ingestion.get_ingestion_status(harness.request)
    assert (status.is_paused, status.can_pause) == (False, True)

    await asyncio.wait_for(job, timeout=10)
    assert sorted(harness.started) == FILE_NAMES

    statuses = harness.statuses()
    assert statuses.count(IngestionStatus.PAUSED) == 1, statuses
    paused_at = statuses.index(IngestionStatus.PAUSED)
    assert IngestionStatus.RUNNING in statuses[paused_at + 1:], "resume was not recorded"
    assert statuses[-1] == IngestionStatus.COMPLETED

    status = await ingestion.get_ingestion_status(harness.request)
    assert (status.status, status.is_paused) == (IngestionStatus.COMPLETED, False)


@MODES
async def test_stop_while_paused_starts_no_more_files(harness: _Harness, max_concurrent: int) -> None:
    harness.pause_when_file_starts = 1
    job = harness.start(max_concurrent)
    held = await harness.wait_until_held()

    await ingestion.stop_ingestion(harness.request)
    await asyncio.wait_for(job, timeout=10)

    assert harness.started == held
    assert (ingestion._pause_requested, ingestion._is_paused) == (False, False)
    assert harness.statuses()[-1] == IngestionStatus.STOPPED


async def test_pause_requested_during_last_file_is_not_reported_after_the_run(harness: _Harness) -> None:
    # No pause point follows the last file, so the run completes; the request must not linger
    harness.pause_when_file_starts = len(FILE_NAMES)
    job = harness.start(1)
    await asyncio.wait_for(job, timeout=10)

    assert sorted(harness.started) == FILE_NAMES
    status = await ingestion.get_ingestion_status(harness.request)
    assert (status.status, status.is_paused) == (IngestionStatus.COMPLETED, False)
