"""Regression: POST /ingestion/start must refuse while a run is still live in this process.

start_ingestion only returned 409 for a job whose DB status was RUNNING. A paused run
(DB status "paused") and a stopped run still finishing its in-flight files (DB status
"stopped" right after POST /stop) slipped through: the new run_ingestion reset the
module-level pause/stop flags, so the paused run resumed and the stopped run kept
processing every remaining file, both alongside the new run and on shared state.

Same _Harness as test_ingestion_pause and the in-memory jobs collection of
test_ingestion_stop, so the DB check sees the statuses the real endpoints write.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import BackgroundTasks, HTTPException

from backend.tests.test_ingestion_pause import FILE_NAMES, MODES, _Harness, harness  # noqa: F401 - fixture
from backend.tests.test_ingestion_stop import JOB_ID, _JobsCollection, jobs  # noqa: F401 - fixture

ingestion = pytest.importorskip("backend.routers.ingestion")
from backend.models.schemas import IngestionStartRequest, IngestionStatus  # noqa: E402


def _live_state() -> dict[str, Any]:
    """The module-level run state a second run_ingestion would reset."""
    return {
        "job_id": ingestion._current_job_id,
        "job_state": ingestion._current_job_state,
        "pause_requested": ingestion._pause_requested,
        "stop_requested": ingestion._stop_requested,
        "shutdown_requested": ingestion._shutdown_requested,
        "stop_status": ingestion._stop_status,
    }


async def _start_is_refused(harness: _Harness) -> None:
    """POST /start answers 409, schedules no run and leaves the live run's state alone."""
    before = _live_state()
    saves = len(harness.saved)
    tasks = BackgroundTasks()

    with pytest.raises(HTTPException) as refused:
        await ingestion.start_ingestion(harness.request, IngestionStartRequest(), tasks)

    assert refused.value.status_code == 409
    assert JOB_ID in refused.value.detail
    assert tasks.tasks == [], "a second run_ingestion was scheduled"
    assert len(harness.saved) == saves, "a new job was saved"
    assert _live_state() == before
    assert ingestion._current_job_state is before["job_state"]


@MODES
async def test_start_while_paused_is_refused_and_the_run_stays_held(
    harness: _Harness, jobs: _JobsCollection, max_concurrent: int
) -> None:
    harness.pause_when_file_starts = 1
    job = harness.start(max_concurrent)
    held = await harness.wait_until_held()
    assert jobs.docs[JOB_ID]["status"] == IngestionStatus.PAUSED  # not RUNNING: the DB check alone let it through

    await _start_is_refused(harness)

    await asyncio.sleep(0.1)  # ~10 pause polls
    assert harness.started == held, "the paused run resumed"
    assert (ingestion._pause_requested, ingestion._is_paused) == (True, True)

    await ingestion.resume_ingestion(harness.request)
    await asyncio.wait_for(job, timeout=10)
    assert sorted(harness.started) == FILE_NAMES  # each file once, by the one run
    assert jobs.docs[JOB_ID]["status"] == IngestionStatus.COMPLETED


@MODES
async def test_start_while_stopping_is_refused_and_the_run_still_stops(
    harness: _Harness, jobs: _JobsCollection, max_concurrent: int
) -> None:
    outcome: list[BaseException | None] = []

    async def stop_then_start() -> None:
        await ingestion.stop_ingestion(harness.request)
        # The pipeline would record a failure raised here as a file error: keep it for the test
        try:
            assert jobs.docs[JOB_ID]["status"] == IngestionStatus.STOPPED  # recorded while files are in flight
            await _start_is_refused(harness)
            assert ingestion._stop_requested is True
        except BaseException as exc:  # noqa: BLE001 - includes pytest's Failed
            outcome.append(exc)
        else:
            outcome.append(None)

    harness.when_file_starts[2] = stop_then_start
    await asyncio.wait_for(harness.start(max_concurrent), timeout=10)

    assert outcome == [None], outcome
    # Files in progress finish; none starts after the stop
    assert sorted(harness.started) == FILE_NAMES[:2]
    assert jobs.docs[JOB_ID]["status"] == IngestionStatus.STOPPED

    # Once the run has ended, a new start is accepted again
    tasks = BackgroundTasks()
    started = await ingestion.start_ingestion(harness.request, IngestionStartRequest(), tasks)
    assert started.status == IngestionStatus.PENDING and started.job_id != JOB_ID
    assert [task.func for task in tasks.tasks] == [ingestion.run_ingestion]
