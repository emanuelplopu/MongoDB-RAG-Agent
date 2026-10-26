"""Reader placeholders are never stored, and the ones that were are purged (F26).

A file neither Docling nor the raw-text fallback could read used to be stored as a document
whose content was ``[Error: Could not read file …]``, chunked and embedded. The pipeline now
raises :class:`UnreadableFileError` (recorded as a failed file) and ``quellexctl ingest
purge-placeholders`` deletes the existing ones with their chunks.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")
from backend.services import placeholder_purge as purge  # noqa: E402


# ----------------------------------------------------------------------------- the pipeline

@pytest.mark.parametrize("content, kind", [
    ("[Error: Could not read file x.pdf]", "error"),
    ("  [Error: Could not read file x.rtf (no RTF reader installed)]\n", "error"),
    ("[Skipped: a.doc - legacy .doc format requires antiword]", "error"),
    ("[Document: leer.rtf - no text content]", "empty"),
    ("[Document: x.mht - no readable part]", "empty"),
    ("Sehr geehrte Damen und Herren, [Error: nichts]", None),
    ("[Error: first line]\nsecond line of a real document", None),
    ("", None),
    (None, None),
])
def test_placeholder_kind(content: Optional[str], kind: Optional[str]) -> None:
    assert ingest.placeholder_kind(content) == kind


class _Pipeline(ingest.DocumentIngestionPipeline):
    """Conversion stubbed; everything after it is the real per-document path."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.documents_folders: List[str] = []
        self.saved: List[Dict[str, Any]] = []

    def get_executor(self):
        return None

    async def _convert_document(self, file_path: str):
        return self.content, None

    async def _save_to_mongodb(self, **kwargs: Any) -> str:
        self.saved.append(kwargs)
        return "stored"


def test_error_placeholder_fails_the_file_instead_of_storing_it(tmp_path: Path) -> None:
    path = tmp_path / "Grundbuch_31.08.2012.pdf"
    path.write_bytes(b"%PDF-1.4 broken")
    pipe = _Pipeline("[Error: Could not read file Grundbuch_31.08.2012.pdf]")
    with pytest.raises(ingest.UnreadableFileError) as failed:
        asyncio.run(pipe._ingest_single_document(str(path), "hash-1"))
    assert "Could not read file Grundbuch_31.08.2012.pdf" in str(failed.value)
    assert pipe.saved == []


def test_empty_placeholder_is_a_file_without_chunks(tmp_path: Path) -> None:
    path = tmp_path / "leer.rtf"
    path.write_bytes(b"{\\rtf1}")
    pipe = _Pipeline("[Document: leer.rtf - no text content]")
    result = asyncio.run(pipe._ingest_single_document(str(path), "hash-2"))
    assert result.document_id == "" and result.chunks_created == 0
    assert result.errors == ["No chunks created"]
    assert pipe.saved == []


def test_concurrent_loop_records_an_unreadable_file_as_an_error(tmp_path: Path) -> None:
    """The exception reaches the loop's error branch: a failed_documents record, no document."""
    path = tmp_path / "RechnungLutz.pdf"
    path.write_bytes(b"%PDF-1.4 not really")

    class _Loop(_Pipeline):
        def __init__(self) -> None:
            super().__init__("[Error: Could not read file RechnungLutz.pdf]")
            self._initialized = True
            self.db = None
            self.stats: List[Any] = []

        async def _save_ingestion_stats(self, file_stats: List[Any]) -> bool:
            self.stats.extend(file_stats)
            return True

    pipe = _Loop()
    results, _ = asyncio.run(pipe._ingest_documents_concurrent(
        document_files=[str(path)], existing_hashes={}, incremental=False, max_concurrent=2,
    ))
    assert results[0].document_id == "" and "Could not read file" in results[0].errors[0]
    stat = pipe.stats[0]
    assert stat.success is False and stat.error_type == "error" and stat.classification == "error"
    assert "Could not read file RechnungLutz.pdf" in stat.error_message


# ----------------------------------------------------------------------------- the purge

class _Cursor:
    def __init__(self, rows: List[dict]) -> None:
        self.rows = rows

    async def to_list(self, length: Any = None) -> List[dict]:
        return list(self.rows)


class _Collection:
    def __init__(self, docs: Optional[List[dict]] = None) -> None:
        self.docs = list(docs or [])
        self.deleted: List[dict] = []
        self.inserted: List[dict] = []

    def find(self, query: dict, projection: Optional[dict] = None) -> _Cursor:
        import re

        pattern = re.compile(query["content"]["$regex"])
        return _Cursor([d for d in self.docs if pattern.search(d.get("content") or "")])

    async def delete_many(self, query: dict) -> SimpleNamespace:
        self.deleted.append(query)
        key = "_id" if "_id" in query else "document_id"
        ids = set(query[key]["$in"])
        before = len(self.docs)
        self.docs = [d for d in self.docs if d.get(key) not in ids]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    async def insert_many(self, docs: List[dict]) -> SimpleNamespace:
        self.inserted.extend(docs)
        return SimpleNamespace(inserted_ids=[f"f{i}" for i in range(len(docs))])


def _db() -> Dict[str, _Collection]:
    documents = _Collection([
        {"_id": 1, "source": "P-106-10/Grundbuch_31.08.2012.pdf", "content": "[Error: Could not read file Grundbuch_31.08.2012.pdf]",
         "metadata": {"file_path": "/app/mounts/Akten/P-106-10/Grundbuch_31.08.2012.pdf", "file_size": 55}},
        {"_id": 2, "source": "KCZ-78/Geb1.pdf", "content": "  [Skipped: Geb1.doc - legacy .doc format requires antiword]", "metadata": {}},
        {"_id": 3, "source": "real.pdf", "content": "Sehr geehrte Frau Dr. Leuthner, [Error: nothing]", "metadata": {}},
        {"_id": 4, "source": "report.md", "content": "# Error: handling\nText", "metadata": {}},
    ])
    chunks = _Collection([{"_id": "c1", "document_id": 1}, {"_id": "c2", "document_id": 1}, {"_id": "c3", "document_id": 2},
                          {"_id": "c4", "document_id": 3}])
    return {"documents": documents, "chunks": chunks, "failed_documents": _Collection()}


async def test_dry_run_lists_without_deleting() -> None:
    db = _db()
    result = await purge.purge_placeholder_documents(db, dry_run=True)
    assert result.documents_found == 2 and result.documents_deleted == 0 and result.chunks_deleted == 0
    assert [s["source"] for s in result.samples] == ["P-106-10/Grundbuch_31.08.2012.pdf", "KCZ-78/Geb1.pdf"]
    assert len(db["documents"].docs) == 4 and db["failed_documents"].inserted == []


async def test_apply_deletes_documents_and_chunks_and_records_failures() -> None:
    db = _db()
    result = await purge.purge_placeholder_documents(db, dry_run=False, profile_key="diejuristin")
    assert (result.documents_deleted, result.chunks_deleted, result.failures_recorded) == (2, 3, 2)
    assert [d["_id"] for d in db["documents"].docs] == [3, 4], "real documents stay"
    assert [c["_id"] for c in db["chunks"].docs] == ["c4"]
    record = db["failed_documents"].inserted[0]
    assert record["file_path"] == "/app/mounts/Akten/P-106-10/Grundbuch_31.08.2012.pdf"
    assert record["file_name"] == "Grundbuch_31.08.2012.pdf"
    assert record["error_type"] == "error" and record["resolved"] is False and record["profile_key"] == "diejuristin"
    assert "placeholder document purged" in record["error_message"]
    datetime.fromisoformat(record["failed_at"])
    assert db["failed_documents"].inserted[1]["file_path"] == "KCZ-78/Geb1.pdf", "falls back to the source"


async def test_apply_can_skip_the_failure_records() -> None:
    db = _db()
    result = await purge.purge_placeholder_documents(db, dry_run=False, record_failures=False)
    assert result.documents_deleted == 2 and result.failures_recorded == 0 and db["failed_documents"].inserted == []
