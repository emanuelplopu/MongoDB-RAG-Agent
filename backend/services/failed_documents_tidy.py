"""Tidy ``failed_documents``: resolve records of files that are indexed, merge repeated records.

Until 2026-11-11 a failure record was resolved only by a retry inside the same run, and
every failed retry added a row. After one law firm's ingestion 136 of 155 open ``timeout``
records belonged to files that were in the index, and a retry run of the read errors
added 1,299 rows for files that already had one (``docs/27-POST_INGESTION_VALIDATION.md``).
The pipeline now resolves and updates records as it goes
(``DocumentIngestionPipeline._save_ingestion_stats``); this module cleans up what exists
(``quellexctl ingest tidy-failures``).

Nothing is deleted: superseded records are marked resolved with a ``resolution`` text, so
the history of a file stays readable.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Tuple

__all__ = ["TidyResult", "tidy_failed_documents"]

_BATCH = 500


@dataclass
class TidyResult:
    """What a tidy run found and did."""

    dry_run: bool
    open_records: int = 0
    indexed_files: int = 0  # open records whose file is in the index
    sidecar_files: int = 0  # open records of ``~$`` owner files and ``._`` forks, never documents
    repeated_records: int = 0  # older open records of a (file, error type) that has a newer one
    resolved: int = 0
    remaining_open: int = 0
    by_error_type: Dict[str, int] = field(default_factory=dict)  # open records left, per error type


def _is_sidecar(file_path: Any) -> bool:
    """Whether the record's file is an Office owner file (``~$name``) or an AppleDouble fork (``._name``)."""
    name = str(file_path or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name.startswith(("~$", "._"))


async def _to_list(cursor: Any) -> List[Dict[str, Any]]:
    result = cursor.to_list(length=None)
    return await result if inspect.isawaitable(result) else result


async def tidy_failed_documents(db: Any, *, documents_collection: str = "documents", dry_run: bool = True) -> TidyResult:
    """Resolve the open failure records that no longer describe a failure.

    Three kinds: records of a file that is in the index (stored by a later attempt), records
    of ``~$`` Office owner files and ``._`` AppleDouble forks (never documents; discovery
    skips them since 2026-11-11, so no run resolves their records: 727 of one corpus's
    1,805 open Word errors, ``docs/26-FORMAT_EDGE_AUDIT.md``), and older records of a file
    that has a newer record of the same error type.

    Args:
        db: The profile's database (PyMongo Async or Motor).
        documents_collection: Name of the documents collection.
        dry_run: Only count; change nothing (default).

    Returns:
        The counts before and after.
    """
    failed = db["failed_documents"]
    rows = await _to_list(failed.find(
        {"resolved": {"$ne": True}}, {"_id": 1, "file_path": 1, "error_type": 1, "failed_at": 1}
    ))
    result = TidyResult(dry_run=dry_run, open_records=len(rows))

    paths = sorted({row.get("file_path") for row in rows if row.get("file_path")})
    indexed: set = set()
    for start in range(0, len(paths), _BATCH):
        found = await _to_list(db[documents_collection].find(
            {"metadata.file_path": {"$in": paths[start:start + _BATCH]}}, {"metadata.file_path": 1}
        ))
        indexed.update((doc.get("metadata") or {}).get("file_path") for doc in found)

    in_index = [row for row in rows if row.get("file_path") in indexed]
    result.indexed_files = len(in_index)
    # The same prefixes as ``is_sidecar_file`` in ``src/ingestion/ingest.py`` (not imported: that module is heavy)
    sidecars = [row for row in rows if row.get("file_path") not in indexed and _is_sidecar(row.get("file_path"))]
    result.sidecar_files = len(sidecars)
    settled = indexed | {row.get("file_path") for row in sidecars}

    newest: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
    repeated: List[Dict[str, Any]] = []
    for row in sorted((r for r in rows if r.get("file_path") not in settled), key=lambda r: str(r.get("failed_at") or "")):
        key = (row.get("file_path"), row.get("error_type"))
        if key in newest:
            repeated.append(newest[key])
        newest[key] = row
    result.repeated_records = len(repeated)

    for row in newest.values():
        error_type = str(row.get("error_type"))
        result.by_error_type[error_type] = result.by_error_type.get(error_type, 0) + 1
    result.remaining_open = len(newest)
    if dry_run:
        return result

    now = datetime.now().isoformat()
    groups = (
        (in_index, "the file is in the index"),
        (sidecars, "owner or sidecar file, never a document (skipped at discovery)"),
        (repeated, "repeated record of a later attempt"),
    )
    for group, resolution in groups:
        ids = [row["_id"] for row in group]
        for start in range(0, len(ids), _BATCH):
            done = await failed.update_many(
                {"_id": {"$in": ids[start:start + _BATCH]}, "resolved": {"$ne": True}},
                {"$set": {"resolved": True, "resolved_at": now, "resolution": resolution, "retry_pending": False}},
            )
            result.resolved += int(getattr(done, "modified_count", 0) or 0)
    return result
