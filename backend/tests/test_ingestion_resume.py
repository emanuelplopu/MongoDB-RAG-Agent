"""Regression: an interrupted ingestion job is resumed once, by its own process, where it stopped.

1. The API resumed a job with the config stored on it. IngestionStartRequest.model_dump()
   always holds clean_before_ingest and incremental, so a clean (or non-incremental) run
   interrupted by a restart cleaned the database again and re-ingested every file. A resume
   now continues the run (no cleaning, already ingested files skipped) once it had reached
   its files; a run interrupted before that starts over with its own flags. The job keeps
   the config it was started with.
2. The API and the ingestion worker both resumed every INTERRUPTED job at startup, so one
   job could run twice, and the worker ran jobs paused at shutdown without holding them.
   Each job now records its runner and each process resumes only its own.

API tests run the real run_ingestion and lifespan resume check over the shared harness
(test_ingestion_pause) and in-memory jobs collection (test_ingestion_stop); worker tests run
the real IngestionWorker resume, claim and job loop against the same collection.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Callable, Optional

import pytest

from backend.tests.test_ingestion_pause import FILE_NAMES, MODES, _Harness, harness  # noqa: F401 - fixture
from backend.tests.test_ingestion_stop import (  # noqa: F401 - fixtures
    JOB_ID, _JobsCollection, _no_resumed_run_left_behind, _restart, _resumed_run_finishes, jobs,
)

ingest = pytest.importorskip("src.ingestion.ingest")
ingestion = pytest.importorskip("backend.routers.ingestion")
worker_module = pytest.importorskip("backend.workers.ingestion_worker")
from backend.core.ingestion_jobs import RUNNER_API, RUNNER_WORKER  # noqa: E402
from backend.models.schemas import IngestionStartRequest, IngestionStatus  # noqa: E402

WorkerStatus = worker_module.IngestionStatus
FILES_STARTED = "2026-10-14T10:00:00"


def _shutdown_while_file_runs(harness: _Harness, number: int) -> None:
    harness.when_file_starts[number] = lambda: ingestion.graceful_shutdown_handler(harness.request.app.state.db)


def _interrupted_job(job_id: str, config: Optional[dict] = None, **fields: Any) -> dict:
    return {
        "_id": job_id, "job_id": job_id, "status": ingestion.INTERRUPTED_STATUS, "resume_paused": False,
        "config": config or {"incremental": False, "clean_before_ingest": False}, **fields,
    }


async def _until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "condition never met"
        await asyncio.sleep(0.005)


# ============== API: resume continues the run ==============

@MODES
async def test_resumed_clean_run_continues_instead_of_cleaning_again(
    harness: _Harness, jobs: _JobsCollection, max_concurrent: int
) -> None:
    harness.ingested.add("stale.md")  # data from before the run: the clean removes it
    _shutdown_while_file_runs(harness, 2)
    await asyncio.wait_for(harness.start(max_concurrent, clean_before_ingest=True), timeout=10)

    assert harness.cleans == 1 and harness.ingested == set(FILE_NAMES[:2])
    interrupted = jobs.docs[JOB_ID]
    assert interrupted["status"] == ingestion.INTERRUPTED_STATUS and interrupted["files_started_at"]

    assert await _restart(harness) == JOB_ID
    await _resumed_run_finishes()

    assert harness.cleans == 1, "the resume cleaned the database again"
    assert harness.pipelines[-1]["clean_before_ingest"] is False
    assert sorted(harness.started) == FILE_NAMES[2:], "the resume re-ingested files the run had done"
    assert harness.ingested == set(FILE_NAMES)
    final = jobs.docs[JOB_ID]
    assert final["status"] == IngestionStatus.COMPLETED
    # The job keeps the flags it was started with, and records the resume
    assert (final["config"]["clean_before_ingest"], final["config"]["incremental"]) == (True, False)
    assert final["resumed_at"] and final["runner"] == RUNNER_API
    assert final["files_started_at"] == interrupted["files_started_at"]


async def test_resume_keeps_the_selective_retry_filters(
    harness: _Harness, jobs: _JobsCollection, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue_builds: list[tuple[bool, dict]] = []

    async def record_queue_build(_pipeline: Any, incremental: bool, _state: Any, config: dict, _db: Any) -> None:
        queue_builds.append((incremental, dict(config)))

    monkeypatch.setattr(ingestion, "_build_pending_files_queue", record_queue_build)
    request = IngestionStartRequest(
        clean_before_ingest=True, incremental=False, retry_errors=True, skip_image_only_pdfs=True
    ).model_dump()
    _shutdown_while_file_runs(harness, 2)
    await asyncio.wait_for(harness.start(1, **request), timeout=10)

    assert await _restart(harness) == JOB_ID
    await _resumed_run_finishes()

    (_, first), (resumed_incremental, resumed) = queue_builds
    assert first == request
    assert resumed_incremental is True
    assert resumed == {**request, "clean_before_ingest": False, "incremental": True}
    assert jobs.docs[JOB_ID]["config"] == request


async def test_run_interrupted_before_its_first_file_starts_over_with_its_own_flags(
    harness: _Harness, jobs: _JobsCollection, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.ingested.add("stale.md")
    build_queue = ingestion._build_pending_files_queue  # the harness's no-op
    discovering = asyncio.Event()

    async def long_folder_scan(*_args: Any) -> None:
        discovering.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ingestion, "_build_pending_files_queue", long_folder_scan)
    job = harness.start(1, clean_before_ingest=True)
    await asyncio.wait_for(discovering.wait(), timeout=5)

    # Restart during discovery, before the pipeline cleaned: the shutdown hook runs, the run is cancelled
    await ingestion.graceful_shutdown_handler(harness.request.app.state.db)
    job.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(job, timeout=5)
    interrupted = jobs.docs[JOB_ID]
    assert (interrupted["status"], interrupted["files_started_at"]) == (ingestion.INTERRUPTED_STATUS, None)
    assert harness.cleans == 0 and harness.started == []

    monkeypatch.setattr(ingestion, "_build_pending_files_queue", build_queue)
    assert await _restart(harness) == JOB_ID
    await _resumed_run_finishes()

    assert harness.cleans == 1, "the requested clean was dropped"
    assert sorted(harness.started) == FILE_NAMES
    assert harness.ingested == set(FILE_NAMES)
    assert jobs.docs[JOB_ID]["status"] == IngestionStatus.COMPLETED


# ============== Ownership: each process resumes only its own jobs ==============

class _Logs:
    async def insert_many(self, _entries: list) -> None:
        return None


class _NoConfig:
    async def find_one(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _worker(jobs: _JobsCollection) -> Any:
    """An IngestionWorker on the in-memory collections (as if it had just connected)."""
    worker = worker_module.IngestionWorker()
    worker.db = {"ingestion_jobs": jobs, "ingestion_logs": _Logs(), "ingestion_config": _NoConfig()}
    return worker


async def test_api_and_worker_each_resume_only_their_own_job(harness: _Harness, jobs: _JobsCollection) -> None:
    harness.connect(1)
    jobs.docs["api-job"] = _interrupted_job("api-job", runner=RUNNER_API, files_started_at=None)
    jobs.docs["worker-job"] = _interrupted_job("worker-job", runner=RUNNER_WORKER, worker_pid=1)
    worker = _worker(jobs)

    # Both processes start: the API's resumed run has not saved anything yet when the worker looks
    assert await _restart(harness) == "api-job"
    assert await worker._resume_interrupted_jobs() == 1
    assert jobs.docs["api-job"]["status"] == ingestion.INTERRUPTED_STATUS  # not re-queued by the worker
    assert jobs.docs["worker-job"]["status"] == WorkerStatus.PENDING

    await _resumed_run_finishes()
    assert jobs.docs["api-job"]["status"] == IngestionStatus.COMPLETED
    assert sorted(harness.started) == FILE_NAMES  # by the API's run alone
    assert jobs.docs["worker-job"]["status"] == WorkerStatus.PENDING  # the API left it alone

    claimed = await worker._poll_next_job()
    assert claimed["job_id"] == "worker-job" and claimed["runner"] == RUNNER_WORKER
    assert await worker._poll_next_job() is None


async def test_worker_never_claims_a_job_the_api_runs() -> None:
    jobs = _JobsCollection()
    jobs.docs["api-job"] = {"_id": "api-job", "status": WorkerStatus.PENDING, "runner": RUNNER_API}

    assert await _worker(jobs)._poll_next_job() is None
    assert jobs.docs["api-job"]["status"] == WorkerStatus.PENDING


async def test_untagged_jobs_from_before_runners_are_resumed_by_the_api(
    harness: _Harness, jobs: _JobsCollection
) -> None:
    # Only the API created jobs; the old worker re-queued some of them, which left worker_pid behind
    harness.connect(1)
    harness.ingested.update(FILE_NAMES[:2])
    jobs.docs["legacy"] = _interrupted_job(
        "legacy", config={"incremental": False, "clean_before_ingest": True}, worker_pid=1
    )

    assert await _worker(jobs)._resume_interrupted_jobs() == 0
    assert await _restart(harness) == "legacy"
    await _resumed_run_finishes()

    # No files_started_at either: continued, not cleaned
    assert harness.cleans == 0 and sorted(harness.started) == FILE_NAMES[2:]
    legacy = jobs.docs["legacy"]
    assert (legacy["status"], legacy["runner"]) == (IngestionStatus.COMPLETED, RUNNER_API)
    assert legacy["files_started_at"]  # a later resume continues it too


# ============== Worker: resume continues the run, held if it was paused ==============

@pytest.fixture
def worker_runs(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Stub the pipeline the worker imports; each entry records how one run was set up."""
    runs: list[dict] = []

    class _Pipeline:
        def __init__(self, **kwargs: Any) -> None:
            self.run: dict = {"pipeline": kwargs}
            runs.append(self.run)
            self.documents_folders: list[str] = []

        async def ingest_documents(self, **kwargs: Any) -> list:
            self.run["ingest"] = kwargs
            return []

    async def nothing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(ingest, "DocumentIngestionPipeline", _Pipeline)
    monkeypatch.setattr(worker_module, "BACKUP_SERVICE_AVAILABLE", False)
    monkeypatch.setattr(worker_module, "_PAUSE_POLL_SECONDS", 0.01)
    monkeypatch.setattr(worker_module.IngestionWorker, "_apply_offline_mode_config", nothing)
    return runs


@pytest.mark.parametrize(
    ("files_started_at", "clean", "incremental"),
    [(FILES_STARTED, False, True), (None, True, False)],
    ids=["reached-files-continues", "before-first-file-starts-over"],
)
async def test_worker_resume_continues_a_run_that_reached_its_files(
    worker_runs: list[dict], files_started_at: Optional[str], clean: bool, incremental: bool
) -> None:
    jobs = _JobsCollection()
    config = IngestionStartRequest(clean_before_ingest=True, incremental=False, retry_errors=True).model_dump()
    jobs.docs["w"] = _interrupted_job("w", config, runner=RUNNER_WORKER, files_started_at=files_started_at)
    worker = _worker(jobs)

    assert await worker._resume_interrupted_jobs() == 1
    await worker._process_job(await worker._poll_next_job())

    (run,) = worker_runs
    assert run["pipeline"]["clean_before_ingest"] is clean
    assert run["ingest"]["incremental"] is incremental
    done = jobs.docs["w"]
    assert done["status"] == WorkerStatus.COMPLETED
    assert done["config"] == config  # the job keeps the flags it was started with
    assert done["files_started_at"] == files_started_at


async def test_worker_resumes_a_job_paused_at_shutdown_held_until_resumed(worker_runs: list[dict]) -> None:
    jobs = _JobsCollection()
    jobs.docs["w"] = _interrupted_job("w", runner=RUNNER_WORKER, resume_paused=True, files_started_at=FILES_STARTED)
    worker = _worker(jobs)
    await worker._resume_interrupted_jobs()
    run = asyncio.create_task(worker._process_job(await worker._poll_next_job()))

    await _until(lambda: jobs.docs["w"]["status"] == WorkerStatus.PAUSED)
    await asyncio.sleep(0.05)  # ~5 pause polls
    assert worker_runs == [], "the pipeline was set up before the job was resumed"

    jobs.docs["w"]["control_command"] = "RESUME"
    await asyncio.wait_for(run, timeout=5)
    assert len(worker_runs) == 1
    assert (jobs.docs["w"]["status"], jobs.docs["w"]["resume_paused"]) == (WorkerStatus.COMPLETED, False)


@pytest.mark.parametrize("end", ["shutdown", "stop"])
async def test_worker_job_held_paused_is_resumable_after_shutdown_and_final_after_stop(
    worker_runs: list[dict], end: str
) -> None:
    jobs = _JobsCollection()
    jobs.docs["w"] = _interrupted_job("w", runner=RUNNER_WORKER, resume_paused=True, files_started_at=FILES_STARTED)
    worker = _worker(jobs)
    await worker._resume_interrupted_jobs()
    run = asyncio.create_task(worker._process_job(await worker._poll_next_job()))
    await _until(lambda: jobs.docs["w"]["status"] == WorkerStatus.PAUSED)

    if end == "shutdown":
        worker.shutdown_requested = True
    else:
        jobs.docs["w"]["control_command"] = "STOP"
    await asyncio.wait_for(run, timeout=5)

    assert worker_runs == []
    held = jobs.docs["w"]
    restarted = _worker(jobs)
    if end == "shutdown":
        # Not STOPPED: the next worker start resumes it, held again
        assert (held["status"], held["resume_paused"]) == (WorkerStatus.INTERRUPTED, True)
        assert await restarted._resume_interrupted_jobs() == 1
    else:
        assert held["status"] == WorkerStatus.STOPPED
        assert await restarted._resume_interrupted_jobs() == 0
