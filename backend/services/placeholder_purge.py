"""Remove documents whose stored content is a reader's error placeholder.

Until 2026-10-26 a file that neither Docling nor the raw-text fallback could read was
stored as a document whose whole content was ``[Error: Could not read file <name>]``,
chunked and embedded (F26 of ``docs/system-validation/INGESTION_PROMPT_TEST_LOG_2026-10-24.md``):
it counted as ingested, so it was never retried, and a question naming the file got the
error string as its source. The pipeline no longer stores such placeholders
(:class:`src.ingestion.ingest.UnreadableFileError`); this module cleans up the ones that
exist (``quellexctl ingest purge-placeholders``).

Since 2026-11-11 the purge also covers the placeholders the pipeline stored until then:
``[Image: … - OCR extracted minimal text]`` (8,600 in one corpus), ``[Video: … not yet
supported]``, ``[Audio: …]`` and ``[Empty email: …]``. The table of prefixes and the
decision "is this whole content a placeholder" are the pipeline's
(:mod:`src.ingestion.placeholders`), so the purge removes exactly what a run would no
longer store.

Each purged document is recorded in ``failed_documents``: ``error_type: error`` for a
failed read (the "retry errors" option re-queues it), ``no_chunks`` for a file without
text (``retry_no_chunks``).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.ingestion.placeholders import PLACEHOLDER_CONTENT_REGEX, placeholder_kind

#: Stored content longer than this is never a placeholder; the query skips it server-side
_MAX_PLACEHOLDER_CHARS = 2100

__all__ = ["PLACEHOLDER_CONTENT_REGEX", "PurgeResult", "find_placeholder_documents", "purge_placeholder_documents"]


@dataclass
class PurgeResult:
    """What a purge found and did."""

    dry_run: bool
    documents_found: int = 0
    documents_deleted: int = 0
    chunks_deleted: int = 0
    failures_recorded: int = 0
    samples: List[Dict[str, Any]] = field(default_factory=list)  # {id, source, content}
    by_kind: Dict[str, int] = field(default_factory=dict)  # "error" / "empty" -> documents


async def _to_list(cursor: Any) -> List[Dict[str, Any]]:
    result = cursor.to_list(length=None)
    return await result if inspect.isawaitable(result) else result


async def find_placeholder_documents(db: Any, documents_collection: str = "documents") -> List[Dict[str, Any]]:
    """Documents whose whole content is a reader placeholder.

    The anchored regular expression selects the candidates; :func:`placeholder_kind`
    decides (a real text may start with ``[Image: …]`` and go on).

    Args:
        db: The profile's database (Motor or PyMongo Async).
        documents_collection: Name of the documents collection.

    Returns:
        ``{_id, source, content, metadata, kind}`` of every placeholder document, ``kind``
        being ``"error"`` (failed read) or ``"empty"`` (no text in the file).
    """
    cursor = db[documents_collection].find(
        {"content": {"$regex": PLACEHOLDER_CONTENT_REGEX}},
        {"_id": 1, "source": 1, "content": {"$substrCP": ["$content", 0, _MAX_PLACEHOLDER_CHARS]}, "metadata": 1, "title": 1},
    )
    found: List[Dict[str, Any]] = []
    for doc in await _to_list(cursor):
        kind = placeholder_kind(doc.get("content"))
        if kind is not None:
            found.append({**doc, "kind": kind})
    return found


async def purge_placeholder_documents(
    db: Any,
    *,
    documents_collection: str = "documents",
    chunks_collection: str = "chunks",
    dry_run: bool = True,
    record_failures: bool = True,
    profile_key: Optional[str] = None,
    sample_size: int = 25,
) -> PurgeResult:
    """Delete the placeholder documents and their chunks, recording each as a failed file.

    Args:
        db: The profile's database.
        documents_collection: Name of the documents collection.
        chunks_collection: Name of the chunks collection (``document_id`` links them).
        dry_run: Only count and list; delete nothing (default).
        record_failures: Insert a ``failed_documents`` record per purged document.
        profile_key: Profile recorded on the failure records.
        sample_size: How many purged documents to list in the result.

    Returns:
        The counts (and up to ``sample_size`` samples).
    """
    docs = await find_placeholder_documents(db, documents_collection)
    result = PurgeResult(dry_run=dry_run, documents_found=len(docs))
    result.samples = [
        {"id": str(d.get("_id")), "source": d.get("source"), "content": (d.get("content") or "").strip()[:160]}
        for d in docs[:sample_size]
    ]
    for d in docs:
        result.by_kind[d.get("kind", "error")] = result.by_kind.get(d.get("kind", "error"), 0) + 1
    if dry_run or not docs:
        return result

    ids = [d["_id"] for d in docs]
    chunks = await db[chunks_collection].delete_many({"document_id": {"$in": ids}})
    result.chunks_deleted = int(getattr(chunks, "deleted_count", 0) or 0)
    deleted = await db[documents_collection].delete_many({"_id": {"$in": ids}})
    result.documents_deleted = int(getattr(deleted, "deleted_count", 0) or 0)

    if record_failures:
        now = datetime.now().isoformat()
        records = []
        for d in docs:
            metadata = d.get("metadata") or {}
            path = metadata.get("file_path") or metadata.get("path") or d.get("source") or ""
            records.append({
                "file_path": path,
                "file_name": str(path).replace("\\", "/").rsplit("/", 1)[-1],
                "file_size_bytes": metadata.get("file_size") or metadata.get("file_size_bytes") or 0,
                "error_type": "error" if d.get("kind", "error") == "error" else "no_chunks",
                "error_message": (d.get("content") or "").strip()[:500] + " (placeholder document purged)",
                "timeout_seconds": 0,
                "processing_time_ms": 0,
                "failed_at": now,
                "profile_key": profile_key or metadata.get("profile_key") or "",
                "resolved": False,
                "retry_count": 0,
                "purged_document_id": str(d.get("_id")),
            })
        if records:
            inserted = await db["failed_documents"].insert_many(records)
            result.failures_recorded = len(getattr(inserted, "inserted_ids", records))
    return result
