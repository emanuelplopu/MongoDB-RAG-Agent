"""Job status counters derived from the per-file ingestion stats.

The pipeline (:class:`src.ingestion.ingest.IngestionStatsRecorder`) reports, after every
flush of ``ingestion_stats``, how many of the stats written so far ended as a timeout, an
error, without chunks, as a skipped image-only PDF or as a duplicate. The API's
``run_ingestion`` and the ingestion worker apply those counts to the job document through
:func:`apply_stats_counts`, so ``GET /api/v1/ingestion/status`` and the runs list show them
while a long job is running instead of zeros until its end (F2/F3 of
``docs/system-validation/INGESTION_PROMPT_TEST_LOG_2026-10-24.md``).
"""

from __future__ import annotations

from typing import Dict, Mapping, MutableMapping, Optional

#: Stats error type -> job document field.
JOB_COUNTER_FIELDS: Dict[str, str] = {
    "timeout": "timeout_files",
    "no_chunks": "no_chunks_files",
    "error": "error_files",
    "image_only_pdf": "image_only_pdfs",
    "image_only_ocr": "image_only_pdfs_ocr",  # pure scans whose OCR produced text (F9)
    "duplicate": "duplicates_skipped",
    # stored with part of their chunks while the rest waits in the run's embedding backlog (F44)
    "partial": "partial_files",
    # audio/video left to a media run (retry_media); not a failure
    "media_deferred": "media_deferred_files",
    # read fine, nothing to index (docs/26, the no_chunks audit of 2026-11-19); not failures
    "image_no_text": "images_without_text",  # photos, logos, drawings: OCR found no text
    "empty_file": "empty_files",  # empty attachment stubs, templates, pictures without text
    # raster images below the performance config's images.min_pixels / min_bytes, skipped
    # unconverted (retry_tiny_images converts them); not a failure
    "tiny_image": "tiny_images",
}

#: First words of the ``IngestionResult`` errors of files a run skipped on purpose: duplicates,
#: audio/video deferred to a media run, raster images below the tiny-image minimum. The job's
#: final ``failed_files`` leaves them out, as the running counters do.
SKIPPED_RESULT_PREFIXES: tuple = ("Duplicate of:", "Deferred to a media run", "Tiny image skipped")


def counts_as_failure(errors: Optional[list]) -> bool:
    """Whether a result's errors make it a failed file (not a deliberate skip).

    Args:
        errors: ``IngestionResult.errors`` of one file.
    """
    if not errors:
        return False
    first = str(errors[0])
    return not any(first.startswith(prefix) for prefix in SKIPPED_RESULT_PREFIXES)


def apply_stats_counts(
    job_state: MutableMapping[str, object],
    counts: Mapping[str, int],
    baseline: Optional[Mapping[str, int]] = None,
) -> None:
    """Set the job's per-outcome counters from the stats written so far.

    ``failed_files`` is what the job's final summary counts too: every result with an error
    other than a duplicate skip (timeouts, errors and files without chunks).

    Args:
        job_state: The job's in-memory state (keys of the job document).
        counts: The recorder's snapshot (``timeout``, ``no_chunks``, ``error``,
            ``image_only_pdf``, ``duplicate``, ...), cumulative for the current run.
        baseline: Counts by error type recorded by earlier runs of the same job (a resumed
            job); ``_calculate_job_extended_metrics`` returns that shape.
    """
    baseline = baseline or {}
    for error_type, field in JOB_COUNTER_FIELDS.items():
        job_state[field] = int(baseline.get(error_type, 0) or 0) + int(counts.get(error_type, 0) or 0)
    # Password-protected files (``encrypted``, never retried) are failed files like any
    # other read error for the job's counters; ``failed_documents.error_type`` tells them apart
    job_state["error_files"] = int(job_state["error_files"]) + int(baseline.get("encrypted", 0) or 0) + int(counts.get("encrypted", 0) or 0)
    job_state["failed_files"] = (
        int(job_state["timeout_files"]) + int(job_state["error_files"]) + int(job_state["no_chunks_files"])
    )
