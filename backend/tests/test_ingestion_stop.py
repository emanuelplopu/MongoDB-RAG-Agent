"""Regression: a user stop ends as STOPPED and is never resumed; a restart ends as INTERRUPTED.

run_ingestion saved every cancellation as "INTERRUPTED", a user stop included: GET /status then
failed validation and the next API start resumed the stopped job. With max_concurrent_files > 1
a stop did not even get that far: gather(return_exceptions=True) swallowed the cancelled file
tasks and the run was saved as completed. A job paused when the API went down stayed "paused".

Like test_ingestion_pause, these run the real run_ingestion over the real pipeline loops
(same _Harness); the restart tests also run the real lifespan resume check against an
in-memory ingestion_jobs collection.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from backend.tests.test_ingestion_pause import FILE_NAMES, MODES, _Harness, harness  # noqa: F401 - fixture

ingest = pytest.importorskip("src.ingestion.ingest")
ingestion = pytest.importorskip("backend.routers.ingestion")
from backend.models.schemas import IngestionStatus  # noqa: E402

JOB_ID = "job-pause-test"  # the harness's job id
_real_get_job_from_db = ingestion.get_job_from_db  # the harness replaces it with a stub


class _JobsCollection:
    """In-memory ``ingestion_jobs``: the queries and updates of the stop and restart paths."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        for key, cond in query.items():
            value = doc.get(key)
            if isinstance(cond, dict):
                if value not in cond["$in"]:
                    return False
            elif value != cond:
                return False
        return True

    async def find_one(self, query: dict) -> Optional[dict]:
        return next((dict(doc) for doc in self.docs.values() if self._matches(doc, query)), None)

    async def replace_one(self, query: dict, doc: dict, upsert: bool = False) -> None:
        self.docs[query["_id"]] = dict(doc)

    async def update_one(self, query: dict, update: dict) -> SimpleNamespace:
        return self._update(query, update, limit=1)

    async def update_many(self, query: dict, update: dict) -> SimpleNamespace:
        return self._update(query, update, limit=None)

    def _update(self, query: dict, update: dict, limit: Optional[int]) -> SimpleNamespace:
        hits = [doc for doc in self.docs.values() if self._matches(doc, query)][:limit]
        for doc in hits:
            doc.update(update["$set"])
        return SimpleNamespace(modified_count=len(hits))


@pytest.fixture
def jobs(harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> _JobsCollection:
    """Persist the harness's job saves and the router's direct collection access in one place."""
    collection = _JobsCollection()

    async def save_job(_db: Any, job: dict) -> None:
        harness.saved.append(dict(job))
        await collection.replace_one({"_id": job["job_id"]}, {**job, "_id": job["job_id"]})

    async def jobs_collection(_db: Any) -> _JobsCollection:
        return collection

    monkeypatch.setattr(ingestion, "save_job_to_db", save_job)
    monkeypatch.setattr(ingestion, "get_jobs_collection", jobs_collection)
    monkeypatch.setattr(ingestion, "get_job_from_db", _real_get_job_from_db)
    return collection


@pytest.fixture(autouse=True)
async def _no_resumed_run_left_behind():
    yield
    task = ingestion._active_ingestion_task
    ingestion._active_ingestion_task = None
    if task is not None and not task.done():
        ingestion._stop_requested = True
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
    ingestion._stop_requested = ingestion._shutdown_requested = False
    ingestion._pause_requested = ingestion._is_paused = False


async def _restart(harness: _Harness) -> Optional[str]:
    """What the next API start does: the lifespan's resume check, which sees only persisted jobs."""
    harness.started.clear()
    harness.pause_when_file_starts = None  # the test's pause/stop/shutdown requests were for the first run
    harness.when_file_starts.clear()
    return await ingestion.check_and_resume_interrupted_jobs(harness.request.app.state.db)


async def _resumed_run_finishes() -> None:
    await asyncio.wait_for(ingestion._active_ingestion_task, timeout=10)


@MODES
async def test_user_stop_ends_as_stopped_and_is_not_resumed(
    harness: _Harness, jobs: _JobsCollection, max_concurrent: int
) -> None:
    harness.when_file_starts[2] = lambda: ingestion.stop_ingestion(harness.request)
    await asyncio.wait_for(harness.start(max_concurrent), timeout=10)

    # Files in progress finish; none starts after the stop
    assert sorted(harness.started) == FILE_NAMES[:2]
    statuses = harness.statuses()
    assert statuses[-1] == IngestionStatus.STOPPED, statuses
    assert IngestionStatus.COMPLETED not in statuses and ingestion.INTERRUPTED_STATUS not in statuses
    final = jobs.docs[JOB_ID]
    assert final["status"] == IngestionStatus.STOPPED and final["completed_at"]
    assert final["progress_percent"] < 100

    status = await ingestion.get_ingestion_status(harness.request)  # the finished job, read from the DB
    assert (status.status, status.can_pause, status.can_stop) == (IngestionStatus.STOPPED, False, False)

    assert await _restart(harness) is None
    assert harness.started == [] and jobs.docs[JOB_ID]["status"] == IngestionStatus.STOPPED


async def test_concurrent_stop_saves_stats_of_processed_files(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved_stats: list = []

    async def record_stats(_pipeline: Any, file_stats: list) -> None:
        saved_stats.extend(file_stats)

    monkeypatch.setattr(ingest.DocumentIngestionPipeline, "_save_ingestion_stats", record_stats)
    harness.when_file_starts[2] = lambda: ingestion.stop_ingestion(harness.request)
    await asyncio.wait_for(harness.start(2), timeout=10)

    assert harness.statuses()[-1] == IngestionStatus.STOPPED
    assert sorted(stat.file_name for stat in saved_stats) == FILE_NAMES[:2]


async def test_cancel_ends_as_cancelled_and_is_not_resumed(harness: _Harness, jobs: _JobsCollection) -> None:
    harness.when_file_starts[2] = lambda: ingestion.cancel_ingestion(harness.request, JOB_ID)
    await asyncio.wait_for(harness.start(1), timeout=10)

    assert harness.started == FILE_NAMES[:2]
    assert jobs.docs[JOB_ID]["status"] == IngestionStatus.CANCELLED
    assert await _restart(harness) is None


@MODES
async def test_shutdown_ends_as_interrupted_and_resumes_on_restart(
    harness: _Harness, jobs: _JobsCollection, max_concurrent: int
) -> None:
    harness.when_file_starts[2] = lambda: ingestion.graceful_shutdown_handler(harness.request.app.state.db)
    await asyncio.wait_for(harness.start(max_concurrent), timeout=10)

    assert sorted(harness.started) == FILE_NAMES[:2]
    assert IngestionStatus.COMPLETED not in harness.statuses()
    interrupted = jobs.docs[JOB_ID]
    assert (interrupted["status"], interrupted["resume_paused"]) == (ingestion.INTERRUPTED_STATUS, False)

    status = await ingestion.get_ingestion_status(harness.request)  # raised a ValidationError before
    assert (status.status, status.can_pause, status.can_stop) == (IngestionStatus.INTERRUPTED, False, False)

    assert await _restart(harness) == JOB_ID
    await _resumed_run_finishes()
    assert sorted(harness.started) == FILE_NAMES
    assert jobs.docs[JOB_ID]["status"] == IngestionStatus.COMPLETED


@MODES
async def test_shutdown_while_paused_resumes_held_until_the_user_resumes(
    harness: _Harness, jobs: _JobsCollection, max_concurrent: int
) -> None:
    harness.pause_when_file_starts = 1
    job = harness.start(max_concurrent)
    held = await harness.wait_until_held()

    await ingestion.graceful_shutdown_handler(harness.request.app.state.db)
    await asyncio.wait_for(job, timeout=10)
    assert harness.started == held
    interrupted = jobs.docs[JOB_ID]
    assert (interrupted["status"], interrupted["resume_paused"]) == (ingestion.INTERRUPTED_STATUS, True)

    assert await _restart(harness) == JOB_ID
    await harness.wait_until_held()
    await asyncio.sleep(0.1)  # ~10 pause polls
    assert harness.started == [], "a file started before the user resumed"
    status = await ingestion.get_ingestion_status(harness.request)
    assert (status.status, status.is_paused, status.can_stop) == (IngestionStatus.PAUSED, True, True)

    await ingestion.resume_ingestion(harness.request)
    await _resumed_run_finishes()
    assert sorted(harness.started) == FILE_NAMES
    assert jobs.docs[JOB_ID]["status"] == IngestionStatus.COMPLETED


async def test_job_left_paused_by_a_crash_is_resumed_held_and_can_be_stopped(
    harness: _Harness, jobs: _JobsCollection
) -> None:
    harness.connect(2)
    jobs.docs["job-crashed"] = {
        "_id": "job-crashed", "job_id": "job-crashed", "status": IngestionStatus.PAUSED,
        "config": {"incremental": False, "clean_before_ingest": False},
    }

    assert await _restart(harness) == "job-crashed"
    marked = jobs.docs["job-crashed"]  # before the resumed run's first save
    assert (marked["status"], marked["resume_paused"]) == (ingestion.INTERRUPTED_STATUS, True)

    await harness.wait_until_held()
    assert jobs.docs["job-crashed"]["status"] == IngestionStatus.PAUSED

    await ingestion.stop_ingestion(harness.request)
    await _resumed_run_finishes()
    assert harness.started == []
    assert jobs.docs["job-crashed"]["status"] == IngestionStatus.STOPPED
    assert await _restart(harness) is None
