"""Regression: an ingestion-queue job must never run alongside an API-started run.

POST /ingestion-queue/queue/add (and add-multiple, and schedules/{id}/run-now, which calls
add) scheduled _process_queue without checking for a live API run, and _run_ingestion_job
took over the shared run state (_current_job_id/_current_job_state) unconditionally. A queue
job added while an API run was paused or stopping ran a second pipeline next to it, the API
run's /status, /pause and /stop acted on the queue job, and when the queue job ended it
cleared _current_job_id while the API run was still live, which reopened POST /start.

The queue now waits until no run is live and claims the run in the same step as that check;
a job releases only a claim it still owns; run_ingestion does not start while another run
owns it (a queue job can claim the run between /start's check and its background run).

Same _Harness as test_ingestion_pause and the in-memory jobs collection of
test_ingestion_stop: the API run and the queue job both run the real per-file loops over
the harness's stub pipeline and files.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import BackgroundTasks

from backend.tests.test_ingestion_pause import FILE_NAMES, MODES, _Harness, harness  # noqa: F401 - fixture
from backend.tests.test_ingestion_stop import JOB_ID, _JobsCollection, jobs  # noqa: F401 - fixture

ingest = pytest.importorskip("src.ingestion.ingest")
profile_module = pytest.importorskip("src.profile")
ingestion = pytest.importorskip("backend.routers.ingestion")
ingestion_queue = pytest.importorskip("backend.routers.ingestion_queue")
from backend.models.schemas import IngestionStatus  # noqa: E402

PROFILE = "default"


class _Queue:
    """Adds jobs through POST /ingestion-queue/queue/add and runs the processor it schedules."""

    def __init__(self, harness: _Harness) -> None:
        self.harness = harness
        self.processor: Optional[asyncio.Task] = None

    async def add(self) -> dict:
        """Queue a job over the harness's files; return its live queue entry."""
        tasks = BackgroundTasks()
        added = await ingestion_queue.add_to_queue(
            self.harness.request,
            ingestion_queue.QueueRequest(profile_key=PROFILE, incremental=False),
            tasks,
            admin=None,
        )
        for task in tasks.tasks:  # The processor, unless one is already running
            self.processor = asyncio.create_task(task())
        return next(job for job in ingestion_queue._ingestion_queue if job["id"] == added["job"].id)

    @staticmethod
    async def until_ended(job: dict, timeout: float = 10.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while job["status"] in ("queued", "running"):
            assert loop.time() < deadline, f"queue job never ended: {job}"
            await asyncio.sleep(0.005)


@pytest.fixture
async def queue(harness: _Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A clean queue whose profile points at the harness's files, polling every 10 ms."""
    profile = SimpleNamespace(name="Default", documents_folders=[str(tmp_path)])
    profiles = SimpleNamespace(list_profiles=lambda: {PROFILE: profile}, switch_profile=lambda _key: True)
    monkeypatch.setattr(profile_module, "get_profile_manager", lambda *_args, **_kwargs: profiles)
    monkeypatch.setattr(ingestion_queue, "_RUN_SLOT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(ingestion_queue, "_ingestion_queue", [])
    monkeypatch.setattr(ingestion_queue, "_queue_processor_running", False)

    q = _Queue(harness)
    yield q
    # The processor sleeps between jobs, or still waits for a run a failed test left held
    if q.processor is not None and not q.processor.done():
        q.processor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await q.processor


@pytest.fixture
def owners(harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> list[tuple[Optional[str], bool]]:
    """For every file start, in order: (job owning the run, whether the API run is still live)."""
    starts: list[tuple[Optional[str], bool]] = []
    pipeline = ingest.DocumentIngestionPipeline  # The harness's stub
    ingest_one = pipeline._ingest_single_document

    async def tracked(self: Any, file_path: str, content_hash: str):
        api_live = harness.job is not None and not harness.job.done()
        starts.append((ingestion._current_job_id, api_live))
        return await ingest_one(self, file_path, content_hash)

    monkeypatch.setattr(pipeline, "_ingest_single_document", tracked)
    return starts


@MODES
async def test_queue_job_waits_while_an_api_run_is_paused(
    harness: _Harness, jobs: _JobsCollection, queue: _Queue, owners: list, max_concurrent: int
) -> None:
    harness.pause_when_file_starts = 1
    api_run = harness.start(max_concurrent)
    held = await harness.wait_until_held()

    queued = await queue.add()
    assert queue.processor is not None

    await asyncio.sleep(0.1)  # ~10 queue polls
    assert harness.started == held, "a file started while the API run was paused"
    assert ingestion._current_job_id == JOB_ID
    assert queued["status"] == "queued"  # Still removable and reorderable while it waits
    status = await ingestion.get_ingestion_status(harness.request)
    assert (status.job_id, status.status, status.is_paused) == (JOB_ID, IngestionStatus.PAUSED, True)

    await ingestion.resume_ingestion(harness.request)
    await asyncio.wait_for(api_run, timeout=10)
    await queue.until_ended(queued)

    # The API run did all its files, then the queue job ran all of its own
    queue_id = queued["id"]
    assert owners == [(JOB_ID, True)] * len(FILE_NAMES) + [(queue_id, False)] * len(FILE_NAMES)
    assert queued["status"] == "completed"
    assert jobs.docs[JOB_ID]["status"] == IngestionStatus.COMPLETED
    assert jobs.docs[queue_id]["status"] == IngestionStatus.COMPLETED
    assert (ingestion._current_job_id, ingestion._current_job_state) == (None, None)


@MODES
async def test_queue_job_waits_until_a_stopped_api_run_has_finished_its_files(
    harness: _Harness, jobs: _JobsCollection, queue: _Queue, owners: list, max_concurrent: int
) -> None:
    added: list[dict] = []
    outcome: list[BaseException | None] = []

    async def stop_then_queue() -> None:
        # The pipeline would record a failure raised here as a file error: keep it for the test
        try:
            await ingestion.stop_ingestion(harness.request)
            added.append(await queue.add())
            await asyncio.sleep(0.05)  # ~5 queue polls while the stopped run's files are in flight
            assert added[0]["status"] == "queued"
            assert ingestion._current_job_id == JOB_ID
        except BaseException as exc:  # noqa: BLE001 - includes pytest's Failed
            outcome.append(exc)
        else:
            outcome.append(None)

    harness.when_file_starts[2] = stop_then_queue
    await asyncio.wait_for(harness.start(max_concurrent), timeout=10)
    assert outcome == [None], outcome
    (queued,) = added
    await queue.until_ended(queued)

    # Files in progress finished, none started after the stop, then the queue job ran
    queue_id = queued["id"]
    assert owners == [(JOB_ID, True)] * 2 + [(queue_id, False)] * len(FILE_NAMES)
    assert jobs.docs[JOB_ID]["status"] == IngestionStatus.STOPPED
    assert (queued["status"], jobs.docs[queue_id]["status"]) == ("completed", IngestionStatus.COMPLETED)
    assert ingestion._current_job_id is None


async def test_api_run_that_lands_after_a_queue_job_claimed_the_run_does_not_start(
    harness: _Harness, jobs: _JobsCollection, queue: _Queue, owners: list
) -> None:
    db = harness.connect(1)
    late_id = "job-late-start"
    jobs.docs[late_id] = {"_id": late_id, "job_id": late_id, "status": IngestionStatus.PENDING}  # As /start saved it
    outcome: list[BaseException | None] = []

    async def late_api_run() -> None:
        # What /start's background run_ingestion does when a queue job claimed the run after /start's check
        try:
            queue_state = ingestion._current_job_state
            await ingestion.run_ingestion(late_id, {"incremental": False, "clean_before_ingest": False}, db)
            assert ingestion._current_job_id == queued["id"]
            assert ingestion._current_job_state is queue_state
        except BaseException as exc:  # noqa: BLE001 - includes pytest's Failed
            outcome.append(exc)
        else:
            outcome.append(None)

    harness.when_file_starts[2] = late_api_run
    queued = await queue.add()
    await queue.until_ended(queued)

    assert outcome == [None], outcome
    assert owners == [(queued["id"], False)] * len(FILE_NAMES)  # Only the queue job's files
    assert queued["status"] == "completed"
    late = jobs.docs[late_id]
    assert late["status"] == IngestionStatus.FAILED and queued["id"] in late["errors"][0]
    assert ingestion._current_job_id is None


def test_a_job_releases_only_its_own_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    api_state = {"job_id": JOB_ID}
    monkeypatch.setattr(ingestion, "_current_job_id", JOB_ID)
    monkeypatch.setattr(ingestion, "_current_job_state", api_state)

    assert ingestion.claim_ingestion_run("queue-job") is False
    ingestion.release_ingestion_run("queue-job")
    assert (ingestion._current_job_id, ingestion._current_job_state) == (JOB_ID, api_state)

    ingestion.release_ingestion_run(JOB_ID)
    assert (ingestion._current_job_id, ingestion._current_job_state) == (None, None)
    assert ingestion.claim_ingestion_run("queue-job") is True
    assert ingestion._current_job_id == "queue-job"
