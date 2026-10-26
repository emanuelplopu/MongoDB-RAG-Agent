"""UI-started ingestion jobs go to the ingestion worker when one is alive (F7).

Until 2026-10-26 ``POST /ingestion/start`` always ran the job inside the API process, which
then sat at 1,400 % CPU and 45 GB RSS and timed out its own health checks. The job is now
queued ``PENDING`` with ``runner: worker`` for the worker to claim (``INGESTION_UI_JOB_RUNNER``:
``auto`` = when a worker reported itself alive, ``worker``, ``api``), and the pause, resume
and stop endpoints reach such a job through its ``control_command``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import BackgroundTasks, HTTPException

from backend.tests.test_ingestion_pause import _Harness, harness  # noqa: F401 - fixture
from backend.tests.test_ingestion_stop import _JobsCollection, jobs  # noqa: F401 - fixture

ingestion = pytest.importorskip("backend.routers.ingestion")
from backend.core.config import settings  # noqa: E402
from backend.core.ingestion_jobs import RUNNER_API, RUNNER_WORKER  # noqa: E402
from backend.models.schemas import IngestionStartRequest, IngestionStatus  # noqa: E402
from backend.services import worker_liveness  # noqa: E402

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------------- liveness

class _Store:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs

    async def activity_current_docs(self) -> list[dict]:
        return list(self.docs)


def _state(docs: Optional[list[dict]]) -> Any:
    return SimpleNamespace(telemetry_store=_Store(docs) if docs is not None else None)


async def test_worker_counts_as_online_within_three_heartbeats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "process_heartbeat_seconds", 30)
    fresh = {"source": "worker", "updated_at": NOW - timedelta(seconds=80)}
    stale = {"source": "worker", "updated_at": (NOW - timedelta(seconds=200)).isoformat()}
    api_only = {"source": "api", "updated_at": NOW}
    assert await worker_liveness.worker_online(_state([fresh, api_only]), now=NOW) is True
    assert await worker_liveness.worker_online(_state([stale, api_only]), now=NOW) is False
    assert await worker_liveness.worker_online(_state([]), now=NOW) is False
    assert await worker_liveness.worker_online(_state(None), now=NOW) is False, "no telemetry store: no worker known"


async def test_runner_setting_auto_api_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    alive = _state([{"source": "worker", "updated_at": NOW}])
    gone = _state([])

    async def online(state: Any, *args: Any, **kwargs: Any) -> bool:
        return state is alive

    monkeypatch.setattr(worker_liveness, "worker_online", online)
    assert await worker_liveness.choose_ui_job_runner(alive, "auto") == RUNNER_WORKER
    assert await worker_liveness.choose_ui_job_runner(gone, "auto") == RUNNER_API
    assert await worker_liveness.choose_ui_job_runner(gone, "worker") == RUNNER_WORKER
    assert await worker_liveness.choose_ui_job_runner(alive, "api") == RUNNER_API
    monkeypatch.setattr(settings, "ingestion_ui_job_runner", "worker")
    assert await worker_liveness.choose_ui_job_runner(gone) == RUNNER_WORKER


# ----------------------------------------------------------------------------- the endpoints

@pytest.fixture
def worker_alive(monkeypatch: pytest.MonkeyPatch):
    async def runner(_state: Any, configured: Optional[str] = None) -> str:
        return RUNNER_WORKER

    monkeypatch.setattr(ingestion, "choose_ui_job_runner", runner)


async def test_start_queues_the_job_for_the_worker_instead_of_running_it(
    harness: _Harness, jobs: _JobsCollection, worker_alive: None
) -> None:
    harness.connect(2)
    tasks = BackgroundTasks()
    response = await ingestion.start_ingestion(
        harness.request, IngestionStartRequest(profile="diejuristin", retry_timeouts=True), tasks
    )
    assert tasks.tasks == [], "nothing runs in the API process"
    assert response.status == IngestionStatus.PENDING and response.runner == RUNNER_WORKER
    stored = jobs.docs[response.job_id]
    assert stored["runner"] == RUNNER_WORKER and stored["status"] == IngestionStatus.PENDING
    assert stored["config"]["profile"] == "diejuristin" and stored["config"]["retry_timeouts"] is True
    assert stored["created_at"] and stored["started_at"] is None
    assert ingestion._current_job_id is None
    assert harness.started == []


async def test_a_queued_worker_job_blocks_a_second_start(harness: _Harness, jobs: _JobsCollection, worker_alive: None) -> None:
    harness.connect(2)
    first = await ingestion.start_ingestion(harness.request, IngestionStartRequest(), BackgroundTasks())
    with pytest.raises(HTTPException) as refused:
        await ingestion.start_ingestion(harness.request, IngestionStartRequest(), BackgroundTasks())
    assert refused.value.status_code == 409 and first.job_id in refused.value.detail


async def test_start_runs_in_process_when_no_worker_is_alive(
    harness: _Harness, jobs: _JobsCollection, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def runner(_state: Any, configured: Optional[str] = None) -> str:
        return RUNNER_API

    monkeypatch.setattr(ingestion, "choose_ui_job_runner", runner)
    harness.connect(2)
    tasks = BackgroundTasks()
    response = await ingestion.start_ingestion(harness.request, IngestionStartRequest(), tasks)
    assert response.runner == RUNNER_API
    assert len(tasks.tasks) == 1 and tasks.tasks[0].func is ingestion.run_ingestion


async def test_pause_resume_stop_reach_a_worker_job_through_its_control_command(
    harness: _Harness, jobs: _JobsCollection, worker_alive: None
) -> None:
    harness.connect(2)
    response = await ingestion.start_ingestion(harness.request, IngestionStartRequest(), BackgroundTasks())
    job_id = response.job_id

    # Stop before the worker claimed it: the job simply ends
    stopped = await ingestion.stop_ingestion(harness.request)
    assert stopped.success and jobs.docs[job_id]["status"] == IngestionStatus.STOPPED

    # A claimed, running worker job: PAUSE / RESUME / STOP are handed over
    jobs.docs[job_id].update({"status": IngestionStatus.RUNNING, "control_command": None})
    assert (await ingestion.pause_ingestion(harness.request)).success
    assert jobs.docs[job_id]["control_command"] == "PAUSE"
    jobs.docs[job_id].update({"status": IngestionStatus.PAUSED, "control_command": None})
    assert (await ingestion.resume_ingestion(harness.request)).success
    assert jobs.docs[job_id]["control_command"] == "RESUME"
    jobs.docs[job_id].update({"status": IngestionStatus.RUNNING, "control_command": None})
    assert (await ingestion.stop_ingestion(harness.request)).success
    assert jobs.docs[job_id]["control_command"] == "STOP"

    # No worker job and no in-process run: the endpoints still answer 400
    jobs.docs[job_id]["status"] = IngestionStatus.COMPLETED
    with pytest.raises(HTTPException) as refused:
        await ingestion.pause_ingestion(harness.request)
    assert refused.value.status_code == 400


async def test_status_reports_the_queued_worker_job(harness: _Harness, jobs: _JobsCollection, worker_alive: None,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The status endpoint shows the pending worker job (it has no started_at yet) with no live run here."""
    harness.connect(2)
    response = await ingestion.start_ingestion(harness.request, IngestionStartRequest(), BackgroundTasks())

    async def latest(_db: Any) -> Optional[dict]:
        doc = dict(jobs.docs[response.job_id])
        doc["job_id"] = doc.pop("_id")
        return doc

    monkeypatch.setattr(ingestion, "get_latest_job_from_db", latest)
    status = await ingestion.get_ingestion_status(harness.request)
    assert status.job_id == response.job_id and status.runner == RUNNER_WORKER
    assert status.status == IngestionStatus.PENDING and status.phase == "queued"
    assert status.can_pause is False and status.can_stop is False
