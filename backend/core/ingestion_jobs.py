"""Ownership and resume rules for ``ingestion_jobs``, shared by the API and the ingestion worker.

Both processes read the same collection, and both resume jobs left ``INTERRUPTED`` when they
start. Each job records the process that runs it in ``runner``, and each process resumes
only its own jobs, so a job is never resumed twice.
"""

from typing import Any, Dict, List, Mapping

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


# Log lines kept in a job document (``logs``). The document is updated about once a second
# while its job runs (progress, counters, heartbeat) and MongoDB writes the whole document
# on every update. Until 2026-11-11 the API runner stored its full in-memory log there when
# a run ended (50,000 lines, 13.5 MB); when the job was resumed, every progress update
# rewrote 13.5 MB, about a hundred updates queued for up to 17 minutes each, 36,000 write
# conflicts accumulated, and searches took 4-12 s instead of under one
# (docs/27-POST_INGESTION_VALIDATION.md). The complete log stays in the container log and,
# for worker jobs, in the ``ingestion_logs`` collection.
JOB_DOCUMENT_LOG_LINES = 2000


def job_log_tail(lines: List[Any]) -> List[Any]:
    """The log lines to store in a job document: the last :data:`JOB_DOCUMENT_LOG_LINES`."""
    return list(lines)[-JOB_DOCUMENT_LOG_LINES:]


def trim_job_logs_update() -> Dict[str, Any]:
    """Update operator that cuts a job document's ``logs`` to :data:`JOB_DOCUMENT_LOG_LINES` lines.

    Merge it into the update that claims or resumes a job: a document written by an older
    build is cut once, before the run starts updating it every second.
    """
    return {"$push": {"logs": {"$each": [], "$slice": -JOB_DOCUMENT_LOG_LINES}}}


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


# Job states as stored in ``ingestion_jobs``: the lower-case values of the API's schema
# (``backend.models.schemas.IngestionStatus``). The ingestion worker wrote them in upper case
# until 2026-11-11, so the two processes never understood each other: a job the API queued
# ``pending`` for the worker was never claimed (the worker polled ``PENDING``), and the API's
# start guard did not see a worker job that was ``RUNNING``. Both write the lower-case values
# now and read either spelling (documents of older builds). ``INTERRUPTED_STATUS`` stays as it is.
JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_PAUSED = "paused"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"
JOB_STOPPED = "stopped"
JOB_CANCELLED = "cancelled"


def normalize_status(value: Any) -> str:
    """A stored or enum status in the canonical lower-case spelling (``""`` for None)."""
    return str(getattr(value, "value", value) or "").lower()


def status_spellings(*statuses: Any) -> List[str]:
    """``statuses`` in both stored spellings (lower case, and the worker's former upper case).

    For ``{"status": {"$in": ...}}`` filters, so a query also finds the documents an older
    build wrote.

    Args:
        *statuses: Status strings or ``IngestionStatus`` members.
    """
    spellings: List[str] = []
    for status in statuses:
        canonical = normalize_status(status)
        for spelling in (canonical, canonical.upper()):
            if spelling and spelling not in spellings:
                spellings.append(spelling)
    return spellings


#: A job in one of these states is live: it blocks a new start and is what "the current job" means
ACTIVE_JOB_STATUSES: List[str] = status_spellings(JOB_PENDING, JOB_RUNNING, JOB_PAUSED)
