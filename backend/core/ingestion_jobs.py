"""Ownership and resume rules for ``ingestion_jobs``, shared by the API and the ingestion worker.

Both processes read the same collection, and both resume jobs left ``INTERRUPTED`` when they
start. Each job records the process that runs it in ``runner``, and each process resumes
only its own jobs, so a job is never resumed twice.
"""

from typing import Any, Dict, Mapping

# Persisted status of a run ended by shutdown/restart (the API reports it as "interrupted").
# Upper case because existing documents and the ingestion worker use exactly this value.
INTERRUPTED_STATUS = "INTERRUPTED"

# Value of a job's ``runner`` field: the process that runs (and resumes) it
RUNNER_API = "api"
RUNNER_WORKER = "worker"

# Filters selecting the jobs each process owns. Jobs written before ``runner`` was recorded
# were all started by the API (nothing else created jobs), so an untagged job is the API's.
API_OWNED: Dict[str, Any] = {"runner": {"$in": [RUNNER_API, None]}}
WORKER_OWNED: Dict[str, Any] = {"runner": RUNNER_WORKER}

# Job field set when its run first reaches the per-file loop: any cleaning is done and
# files may have been ingested from then on. Kept across resumes.
FILES_STARTED_FIELD = "files_started_at"

# Job field naming the tenant (``TENANT_ID``) the job belongs to. The API records its own
# tenant on every job it writes; whoever queues a job for the shared ingestion worker must
# set it too, because the worker serves every tenant and attributes the job's telemetry
# to this field (jobs without it count as shared).
TENANT_FIELD = "tenant"


def reached_files(job: Mapping[str, Any]) -> bool:
    """Return whether the job's run got as far as processing files.

    Jobs recorded before ``files_started_at`` existed count as having got there, so a
    resume never cleans them again.
    """
    return job.get(FILES_STARTED_FIELD, True) is not None


def resume_config(job: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the config to resume an interrupted job with: continue where its run stopped.

    Once the run reached its files, cleaning again would delete what it had ingested and a
    non-incremental pass would ingest it all again, so the resume runs with
    ``clean_before_ingest`` off and ``incremental`` on. A run interrupted before that had
    ingested nothing; it starts over with its own flags, so a requested clean still
    happens. Every other setting (profile, folder, chunking, selective-retry filters) is
    kept. The stored job config is not changed: it keeps the flags the job was started with.

    Args:
        job: The interrupted job as stored in ``ingestion_jobs``.

    Returns:
        The config the resumed run uses.
    """
    config = dict(job.get("config") or {})
    if not reached_files(job):
        return config
    return {**config, "clean_before_ingest": False, "incremental": True}
