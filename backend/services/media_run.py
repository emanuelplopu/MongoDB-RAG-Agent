"""Queue a media run (``retry_media``) for the ingestion worker.

A media run transcribes a profile's audio and video files only: the ones a document run
recorded as ``media_deferred`` (performance config ``media.defer``, or the hand-recorded
deferrals of 2026-11-06) and the ones it never reached. The Ingestion page queues it with
the "Transcribe media (audio/video only)" option; ``quellexctl ingest queue-media`` does the
same without the API by writing the job the ingestion worker claims
(``backend/workers/ingestion_worker.py``, ``_poll_next_job``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from backend.core.ingestion_jobs import RUNNER_WORKER, TENANT_FIELD
from backend.models.schemas import IngestionStartRequest, IngestionStatus

INGESTION_JOBS_COLLECTION = "ingestion_jobs"
#: What the worker's ``_poll_next_job`` claims (its own IngestionStatus spells the states in
#: upper case, the API's schema in lower case; a job for the worker carries the worker's spelling)
WORKER_PENDING = "PENDING"
#: A job in one of these states blocks a new one (the API's start guard does the same);
#: both spellings, since the API and the worker each write their own
ACTIVE_STATUSES = (
    IngestionStatus.PENDING.value, IngestionStatus.RUNNING.value, IngestionStatus.PAUSED.value,
    "PENDING", "RUNNING", "PAUSED",
)


def media_run_job(profile: str, *, tenant: Optional[str] = None, incremental: bool = True) -> Dict[str, Any]:
    """The ``ingestion_jobs`` document of a media run, as the API's start endpoint would write it.

    Args:
        profile: Profile key whose folders are walked.
        tenant: Tenant label recorded on the job; the worker's default when None.
        incremental: Skip media files that are already ingested (False transcribes all of them).
    """
    request = IngestionStartRequest(profile=profile, incremental=incremental, retry_media=True)
    job_id = str(uuid.uuid4())
    job: Dict[str, Any] = {
        "_id": job_id,
        "status": WORKER_PENDING,
        "runner": RUNNER_WORKER,
        "created_at": datetime.now().isoformat(),
        "started_at": None,
        "completed_at": None,
        "total_files": 0,
        "processed_files": 0,
        "failed_files": 0,
        "duplicates_skipped": 0,
        "chunks_created": 0,
        "current_file": None,
        "errors": [],
        "progress_percent": 0.0,
        "config": request.model_dump(),
        "profile": profile,
        "phase": "queued",
        "phase_message": "Media run: waiting for the ingestion worker to claim the job...",
    }
    if tenant:
        job[TENANT_FIELD] = tenant
    return job


async def queue_media_run(
    db: Any, profile: str, *, tenant: Optional[str] = None, incremental: bool = True
) -> Dict[str, Any]:
    """Insert a media run into ``ingestion_jobs`` unless a job is pending, running or paused.

    Args:
        db: The database holding ``ingestion_jobs`` (``pymongo.AsyncMongoClient`` database).
        profile: Profile key.
        tenant: Tenant label recorded on the job.
        incremental: Skip media files that are already ingested.

    Returns:
        The inserted job document (``job_id``, ``status`` ...).

    Raises:
        RuntimeError: Another job is active in that database.
    """
    jobs = db[INGESTION_JOBS_COLLECTION]
    active = await jobs.find_one(
        {"status": {"$in": list(ACTIVE_STATUSES)}}, {"_id": 1, "status": 1, "profile": 1, "runner": 1}
    )
    if active:
        raise RuntimeError(
            f"Job {active.get('_id')} is {active.get('status')} (profile {active.get('profile')}, "
            f"runner {active.get('runner') or 'api'}); queue the media run once it is done"
        )
    job = media_run_job(profile, tenant=tenant, incremental=incremental)
    await jobs.insert_one(dict(job))
    return {**job, "job_id": job["_id"]}
