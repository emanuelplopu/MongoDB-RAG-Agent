"""A resumed job does not redo the files it already recorded as failed (F23).

The incremental filter only knew the ``documents`` collection, so every file that had timed
out, errored or produced no chunks before a restart was "new" again: 553 timeouts on 451
files in the first 4 h after the 2026-10-25 redeploy. The pipeline and the API's queue build
now skip the job's own ``failed_documents`` unless the request's ``retry_*`` flags ask for
that error type.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")

JOB = "job-resume-1"


class _Cursor:
    def __init__(self, rows: List[dict]) -> None:
        self.rows = rows

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for row in self.rows:
            yield row


class _Failed:
    def __init__(self, rows: List[dict]) -> None:
        self.rows = rows
        self.queries: List[dict] = []

    def find(self, query: dict, projection: Optional[dict] = None) -> _Cursor:
        self.queries.append(query)
        rows = [
            r for r in self.rows
            if r.get("job_id") == query["job_id"]
            and r.get("resolved") is not True
            and (r.get("retry_pending") is not True or "retry_pending" not in query)
        ]
        return _Cursor(rows)


def _db(rows: List[dict]) -> Dict[str, Any]:
    return {"failed_documents": _Failed(rows)}


def test_retry_flags_map_to_error_types() -> None:
    assert ingest.retry_error_types(None) == frozenset()
    assert ingest.retry_error_types({"incremental": True}) == frozenset()
    assert ingest.retry_error_types({"retry_timeouts": True, "retry_no_chunks": True}) == {"timeout", "no_chunks"}
    assert ingest.retry_error_types({"retry_errors": True, "retry_image_only_pdfs": True}) == {"error", "image_only_pdf"}


async def test_failed_paths_skip_unresolved_failures_unless_retried() -> None:
    db = _db([
        {"job_id": JOB, "file_path": "/a/slow.pdf", "error_type": "timeout", "resolved": False},
        {"job_id": JOB, "file_path": "/a/blank.pdf", "error_type": "no_chunks"},
        {"job_id": JOB, "file_path": "/a/broken.doc", "error_type": "error", "resolved": True},
        {"job_id": "other", "file_path": "/a/other.pdf", "error_type": "timeout"},
        # A timeout whose retry was still queued when the run ended is attempted again (F43)
        {"job_id": JOB, "file_path": "/a/retrying.pdf", "error_type": "timeout", "resolved": False, "retry_pending": True},
    ])
    assert await ingest.failed_paths_of_job(db, JOB) == {"/a/slow.pdf": "timeout", "/a/blank.pdf": "no_chunks"}
    assert db["failed_documents"].queries[-1]["retry_pending"] == {"$ne": True}
    assert await ingest.failed_paths_of_job(db, JOB, frozenset({"timeout"})) == {"/a/blank.pdf": "no_chunks"}
    assert await ingest.failed_paths_of_job(None, JOB) == {}
    assert await ingest.failed_paths_of_job(db, None) == {}


class _Pipeline(ingest.DocumentIngestionPipeline):
    """Real ``ingest_documents`` over stubbed discovery, DB lookups and per-file processing."""

    def __init__(self, files: List[str], db: Any) -> None:
        self._initialized = True
        self.clean_before_ingest = False
        self.documents_folders: List[str] = []
        self.db = db
        self.files = files
        self.started: List[str] = []
        self.queued: List[List[str]] = []

    def _find_document_files(self) -> List[str]:
        return list(self.files)

    async def _get_existing_sources(self) -> set:
        return {"done.txt"}

    async def _get_existing_hashes(self) -> dict:
        return {}

    async def _ingest_single_document(self, file_path: str, content_hash: Optional[str]):
        self.started.append(Path(file_path).name)
        return ingest.IngestionResult(document_id=content_hash or "x", title=Path(file_path).name,
                                      chunks_created=1, processing_time_ms=1.0, errors=[])


@pytest.fixture
def corpus(tmp_path: Path) -> List[str]:
    names = ["done.txt", "slow.pdf", "blank.pdf", "fresh.docx"]
    for name in names:
        (tmp_path / name).write_text(f"content of {name}", encoding="utf-8")
    return [str(tmp_path / name) for name in names]


def _failures(corpus: List[str]) -> List[dict]:
    by_name = {Path(p).name: p for p in corpus}
    return [
        {"job_id": JOB, "file_path": by_name["slow.pdf"], "error_type": "timeout"},
        {"job_id": JOB, "file_path": by_name["blank.pdf"], "error_type": "no_chunks"},
    ]


def test_resume_skips_the_jobs_recorded_failures(corpus: List[str]) -> None:
    pipe = _Pipeline(corpus, _db(_failures(corpus)))
    asyncio.run(pipe.ingest_documents(incremental=True, job_id=JOB, queue_callback=pipe.queued.append))
    assert pipe.started == ["fresh.docx"], "done.txt is ingested, slow.pdf and blank.pdf failed earlier"
    assert [Path(p).name for p in pipe.queued[0]] == ["fresh.docx"], "the queue callback sees the final list"


def test_retry_options_requeue_the_matching_failures(corpus: List[str]) -> None:
    pipe = _Pipeline(corpus, _db(_failures(corpus)))
    asyncio.run(pipe.ingest_documents(incremental=True, job_id=JOB,
                                      retry_failed_types=ingest.retry_error_types({"retry_timeouts": True})))
    assert sorted(pipe.started) == ["fresh.docx", "slow.pdf"]


def test_a_fresh_job_or_a_full_run_is_not_filtered(corpus: List[str]) -> None:
    pipe = _Pipeline(corpus, _db(_failures(corpus)))
    asyncio.run(pipe.ingest_documents(incremental=True, job_id="job-new"))
    assert sorted(pipe.started) == ["blank.pdf", "fresh.docx", "slow.pdf"]

    pipe = _Pipeline(corpus, _db(_failures(corpus)))
    asyncio.run(pipe.ingest_documents(incremental=False, job_id=JOB))
    assert len(pipe.started) == 4, "a non-incremental run processes everything"
