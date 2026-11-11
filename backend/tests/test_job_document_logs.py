"""A job document carries a bounded number of log lines.

The API runner stored its whole in-memory log (50,000 lines, 13.5 MB) in the job document
when a run ended. The document is updated about once a second while its job runs and
MongoDB writes the whole document each time: when that job was resumed, about a hundred
updates queued for up to 17 minutes each and searches took 4-12 s
(``docs/27-POST_INGESTION_VALIDATION.md``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from backend.core.ingestion_jobs import JOB_DOCUMENT_LOG_LINES, job_log_tail, trim_job_logs_update


def test_job_log_tail_keeps_the_last_lines() -> None:
    lines = [{"message": f"line {i}"} for i in range(50_000)]
    tail = job_log_tail(lines)
    assert len(tail) == JOB_DOCUMENT_LOG_LINES == 2000
    assert tail[0]["message"] == "line 48000" and tail[-1]["message"] == "line 49999"
    assert job_log_tail([{"message": "only"}]) == [{"message": "only"}]


def test_trim_update_cuts_the_array_without_adding_lines() -> None:
    assert trim_job_logs_update() == {"$push": {"logs": {"$each": [], "$slice": -2000}}}


async def test_worker_claim_trims_the_logs_and_does_not_load_them() -> None:
    worker_module = pytest.importorskip("backend.workers.ingestion_worker")
    calls: List[Dict[str, Any]] = []

    class _Jobs:
        async def find_one_and_update(self, query: dict, update: dict, **kwargs: Any) -> Optional[dict]:
            calls.append({"query": query, "update": update, **kwargs})
            return {"_id": "job-1", "status": "running", "config": {}}

    worker = worker_module.IngestionWorker.__new__(worker_module.IngestionWorker)
    worker.db = {worker_module.INGESTION_JOBS_COLLECTION: _Jobs()}
    worker.pid = 4711

    job = await worker._poll_next_job()

    assert job is not None and job["job_id"] == "job-1"
    (call,) = calls
    assert call["update"]["$push"] == {"logs": {"$each": [], "$slice": -JOB_DOCUMENT_LOG_LINES}}
    assert call["update"]["$set"]["runner"] == "worker"
    assert call["projection"] == {"logs": 0}, "13.5 MB of old log lines are not read into the worker"
