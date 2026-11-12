"""``quellexctl ingest tidy-failures``: stale and repeated failure records are resolved, none deleted."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional

from backend.services.failed_documents_tidy import tidy_failed_documents


class _Cursor:
    def __init__(self, rows: List[dict]) -> None:
        self.rows = rows

    async def to_list(self, length: Any = None) -> List[dict]:
        return [dict(r) for r in self.rows]


class _Failed:
    def __init__(self, rows: List[dict]) -> None:
        self.rows = rows

    def find(self, query: dict, projection: Optional[dict] = None) -> _Cursor:
        return _Cursor([r for r in self.rows if r.get("resolved") is not True])

    async def update_many(self, query: dict, update: dict) -> SimpleNamespace:
        ids = set(query["_id"]["$in"])
        changed = 0
        for row in self.rows:
            if row["_id"] in ids and row.get("resolved") is not True:
                row.update(update["$set"])
                changed += 1
        return SimpleNamespace(modified_count=changed)


class _Documents:
    def __init__(self, paths: List[str]) -> None:
        self.paths = paths

    def find(self, query: dict, projection: Optional[dict] = None) -> _Cursor:
        wanted = set(query["metadata.file_path"]["$in"])
        return _Cursor([{"metadata": {"file_path": p}} for p in self.paths if p in wanted])


def _db() -> dict:
    failed = _Failed([
        {"_id": 1, "file_path": "/m/Kommentar.pdf", "error_type": "timeout", "failed_at": "2026-11-06T20:00:00", "resolved": False},
        {"_id": 2, "file_path": "/m/Honorarnote.docx", "error_type": "error", "failed_at": "2026-10-27T10:00:00", "resolved": False},
        {"_id": 3, "file_path": "/m/Honorarnote.docx", "error_type": "error", "failed_at": "2026-11-11T19:30:00", "resolved": False},
        {"_id": 4, "file_path": "/m/Honorarnote.docx", "error_type": "no_chunks", "failed_at": "2026-10-28T10:00:00", "resolved": False},
        {"_id": 5, "file_path": "/m/Foto.jpg", "error_type": "no_chunks", "failed_at": "2026-10-28T10:00:00", "resolved": False},
        {"_id": 6, "file_path": "/m/Alt.pdf", "error_type": "error", "failed_at": "2026-10-25T10:00:00", "resolved": True, "resolution": "retry 1 ended as success"},
    ])
    return {"failed_documents": failed, "documents": _Documents(["/m/Kommentar.pdf", "/m/Anderes.pdf"])}


async def test_dry_run_counts_without_changing() -> None:
    db = _db()
    result = await tidy_failed_documents(db)
    assert (result.open_records, result.indexed_files, result.repeated_records, result.resolved) == (5, 1, 1, 0)
    assert result.remaining_open == 3 and result.by_error_type == {"error": 1, "no_chunks": 2}
    assert [r.get("resolved") for r in db["failed_documents"].rows] == [False, False, False, False, False, True]


async def test_apply_resolves_indexed_and_repeated_records_and_keeps_the_newest() -> None:
    db = _db()
    result = await tidy_failed_documents(db, dry_run=False)
    assert result.resolved == 2 and result.remaining_open == 3
    rows = {r["_id"]: r for r in db["failed_documents"].rows}
    assert rows[1]["resolved"] is True and rows[1]["resolution"] == "the file is in the index"
    assert rows[2]["resolved"] is True and rows[2]["resolution"] == "repeated record of a later attempt"
    assert rows[3]["resolved"] is False, "the newest record of the read error stays open"
    assert rows[4]["resolved"] is False and rows[5]["resolved"] is False, "other error types and files are untouched"
    assert rows[6]["resolution"] == "retry 1 ended as success", "resolved records keep their history"
    again = await tidy_failed_documents(db, dry_run=False)
    assert (again.open_records, again.resolved) == (3, 0), "idempotent"
