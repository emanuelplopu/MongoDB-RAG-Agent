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
}


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
    job_state["failed_files"] = (
        int(job_state["timeout_files"]) + int(job_state["error_files"]) + int(job_state["no_chunks_files"])
    )
