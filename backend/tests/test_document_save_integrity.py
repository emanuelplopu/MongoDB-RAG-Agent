"""A stored document is whole: capped content, chunks present, the old version kept on failure.

Three defects of the 2026-10/11 law-firm ingestion (``docs/27-POST_INGESTION_VALIDATION.md``):

* three commentaries with 12-30 MB of text failed after 4-5.5 hours each at the insert
  ("BSON document too large"): ``documents.content`` is capped now, the chunks keep everything;
* 50 documents were stored without a single chunk while the embedder was degraded: nothing
  embedded is a failed file now, chunks the embedder rejects go to the embedding backlog;
* the previous version of a file was deleted before the new one was inserted: the new
  version is stored first now, and a half-stored one is removed again.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")
from src.ingestion.chunker import DocumentChunk  # noqa: E402


# ----------------------------------------------------------------------------- the content cap

def test_cap_leaves_ordinary_text_alone() -> None:
    text = "Sehr geehrte Frau Dr. Muster,\n" * 100
    assert ingest.cap_document_content(text, 8 * 1024 * 1024) == (text, None)


def test_cap_cuts_at_a_line_end_within_the_byte_limit() -> None:
    line = "§ 1096 ABGB: Vermieter und Verpächter sind verpflichtet, das Bestandstück zu übergeben.\n"
    text = line * 20000  # ~1.8 MB, multi-byte characters
    stored, full = ingest.cap_document_content(text, 500_000)
    assert full == len(text.encode("utf-8"))
    assert len(stored.encode("utf-8")) <= 500_000
    assert text.startswith(stored) and stored.endswith("übergeben."), "cut at a line end, never inside a character"
    assert len(stored.encode("utf-8")) > 500_000 - 4096


def test_cap_limit_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INGEST_MAX_DOCUMENT_CONTENT_MB", raising=False)
    assert ingest.max_document_content_bytes() == 8 * 1024 * 1024
    monkeypatch.setenv("INGEST_MAX_DOCUMENT_CONTENT_MB", "2")
    assert ingest.max_document_content_bytes() == 2 * 1024 * 1024
    monkeypatch.setenv("INGEST_MAX_DOCUMENT_CONTENT_MB", "64")
    assert ingest.max_document_content_bytes() == 15 * 1024 * 1024, "never above the BSON limit"
    monkeypatch.setenv("INGEST_MAX_DOCUMENT_CONTENT_MB", "viel")
    assert ingest.max_document_content_bytes() == 8 * 1024 * 1024


# ----------------------------------------------------------------------------- the save

class _Cursor:
    def __init__(self, rows: List[dict]) -> None:
        self.rows = rows

    def __aiter__(self):
        async def gen():
            for row in self.rows:
                yield row
        return gen()


class _Collection:
    """In-memory stand-in for the two collections ``_save_to_mongodb`` writes."""

    def __init__(self, log: List[str], name: str, fail_insert_many: bool = False) -> None:
        self.rows: List[dict] = []
        self.log = log
        self.name = name
        self.fail_insert_many = fail_insert_many
        self.next_id = 100

    def find(self, query: dict, projection: Optional[dict] = None) -> _Cursor:
        excluded = (query.get("_id") or {}).get("$ne")
        return _Cursor([r for r in self.rows if r.get("source") == query.get("source") and r["_id"] != excluded])

    async def insert_one(self, doc: dict) -> SimpleNamespace:
        self.next_id += 1
        self.rows.append({**doc, "_id": self.next_id})
        self.log.append(f"insert {self.name}")
        return SimpleNamespace(inserted_id=self.next_id)

    async def insert_many(self, docs: List[dict], ordered: bool = True) -> SimpleNamespace:
        if self.fail_insert_many:
            self.rows.append({**docs[0], "_id": "partial"})  # ordered=False: some rows may be in
            raise RuntimeError("connection lost while inserting chunks")
        self.rows.extend(docs)
        self.log.append(f"insert {self.name} x{len(docs)}")
        return SimpleNamespace(inserted_ids=list(range(len(docs))))

    async def delete_many(self, query: dict) -> SimpleNamespace:
        key = "document_id" if "document_id" in query else "_id"
        wanted = query[key]
        ids = set(wanted["$in"]) if isinstance(wanted, dict) else {wanted}
        before = len(self.rows)
        self.rows = [r for r in self.rows if r.get(key) not in ids]
        self.log.append(f"delete {self.name}")
        return SimpleNamespace(deleted_count=before - len(self.rows))

    async def delete_one(self, query: dict) -> SimpleNamespace:
        before = len(self.rows)
        self.rows = [r for r in self.rows if r["_id"] != query["_id"]]
        self.log.append(f"delete {self.name}")
        return SimpleNamespace(deleted_count=before - len(self.rows))


def _pipeline(fail_chunks: bool = False) -> Tuple[Any, _Collection, _Collection, List[str]]:
    log: List[str] = []
    documents, chunks = _Collection(log, "documents"), _Collection(log, "chunks", fail_insert_many=fail_chunks)
    pipe = ingest.DocumentIngestionPipeline.__new__(ingest.DocumentIngestionPipeline)
    pipe.db = {"documents": documents, "chunks": chunks}
    pipe.settings = SimpleNamespace(mongodb_collection_documents="documents", mongodb_collection_chunks="chunks")
    return pipe, documents, chunks, log


def _chunks(n: int) -> List[DocumentChunk]:
    return [DocumentChunk(content=f"Absatz {i}", index=i, start_char=0, end_char=8, metadata={}, token_count=3, embedding=[0.1, 0.2]) for i in range(n)]


async def test_oversized_text_is_stored_with_capped_content_and_all_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_MAX_DOCUMENT_CONTENT_MB", "1")
    pipe, documents, chunks, _ = _pipeline()
    text = "Kommentar zum ABGB, § 1096: Gewährleistung des Bestandgebers für den bedungenen Gebrauch.\n" * 30000  # ~2.7 MB

    document_id = await pipe._save_to_mongodb("Schwimann ABGB", "ABGB/Schwimann.pdf", text, _chunks(5), {"file_path": "/m/ABGB/Schwimann.pdf"}, "hash")

    row = documents.rows[0]
    assert str(row["_id"]) == document_id
    assert len(row["content"].encode("utf-8")) <= 1024 * 1024 and text.startswith(row["content"])
    assert row["metadata"]["content_truncated"] is True and row["metadata"]["content_bytes"] == len(text.encode("utf-8"))
    assert row["metadata"]["chunks_count"] == 5 and len(chunks.rows) == 5, "the chunks carry the whole text"


async def test_ordinary_text_has_no_truncation_marker() -> None:
    pipe, documents, _, _ = _pipeline()
    await pipe._save_to_mongodb("Brief", "A/Brief.docx", "Sehr geehrte Damen und Herren", _chunks(1), {}, "hash")
    assert documents.rows[0]["content"] == "Sehr geehrte Damen und Herren"
    assert "content_truncated" not in documents.rows[0]["metadata"]


async def test_the_new_version_is_stored_before_the_old_one_is_deleted() -> None:
    pipe, documents, chunks, log = _pipeline()
    documents.rows.append({"_id": 7, "source": "A/Brief.docx", "content": "alt"})
    chunks.rows.append({"_id": "c-old", "document_id": 7})

    document_id = await pipe._save_to_mongodb("Brief", "A/Brief.docx", "neu", _chunks(2), {}, "hash")

    assert log == ["insert documents", "insert chunks x2", "delete chunks", "delete documents"]
    assert [r["_id"] for r in documents.rows] == [int(document_id)], "one version left: the new one"
    assert {r["document_id"] for r in chunks.rows} == {int(document_id)}


async def test_a_failed_chunk_insert_keeps_the_old_version_and_removes_the_torn_one() -> None:
    pipe, documents, chunks, _ = _pipeline(fail_chunks=True)
    documents.rows.append({"_id": 7, "source": "A/Brief.docx", "content": "alt"})
    chunks.rows.append({"_id": "c-old", "document_id": 7})

    with pytest.raises(RuntimeError, match="connection lost"):
        await pipe._save_to_mongodb("Brief", "A/Brief.docx", "neu", _chunks(2), {}, "hash")

    assert [r["_id"] for r in documents.rows] == [7], "the previous version is still there, no half-stored row"
    assert [r["_id"] for r in chunks.rows] == ["c-old"]


async def test_a_document_without_chunks_is_never_stored() -> None:
    pipe, documents, chunks, _ = _pipeline()
    with pytest.raises(ingest.EmbeddingFailedError):
        await pipe._save_to_mongodb("Leistungsaufstellung", "A/Leistungsaufstellung.doc", "Text", [], {}, "hash")
    assert documents.rows == [] and chunks.rows == []
    # a document whose chunks are all still pending (embedding budget, F44) is a different case
    await pipe._save_to_mongodb("Kommentar", "A/Kommentar.pdf", "Text", [], {}, "hash", pending_chunks=12)
    assert documents.rows[0]["metadata"]["chunks_pending"] == 12


# ----------------------------------------------------------------------------- the embedding step

class _Chunker:
    def __init__(self, count: int) -> None:
        self.count = count

    async def chunk_document(self, content: str, title: str, source: str, metadata=None, docling_doc=None) -> List[DocumentChunk]:
        return [DocumentChunk(content=f"{title} Teil {i}", index=i, start_char=0, end_char=10, metadata={"source": source}, token_count=100)
                for i in range(self.count)]


class _RejectingEmbedder:
    """Drops the chunks whose index is in ``reject`` (as the real embedder does for a chunk the server refuses)."""

    def __init__(self, reject: set) -> None:
        self.reject = reject

    async def embed_chunks_within(self, chunks: List[DocumentChunk], deadline: Optional[float] = None, progress_callback=None):
        kept = [DocumentChunk(content=c.content, index=c.index, start_char=0, end_char=10, metadata=dict(c.metadata), token_count=c.token_count, embedding=[0.0])
                for c in chunks if c.index not in self.reject]
        return kept, []


class _Pipe(ingest.DocumentIngestionPipeline):
    def __init__(self, chunks: int, reject: set) -> None:
        self._initialized = True
        self.db = None
        self.documents_folders: List[str] = []
        self.chunker = _Chunker(chunks)
        self.embedder = _RejectingEmbedder(reject)
        self.saved: List[Dict[str, Any]] = []

    def get_executor(self):
        return None

    async def _convert_document(self, file_path: str):
        return "Leistungsaufstellung vom 12.12.2023, Mandant und Gegner", None

    async def _save_to_mongodb(self, title, source, content, chunks, metadata, content_hash=None, pending_chunks=0):
        self.saved.append({"chunks": len(chunks), "pending": pending_chunks})
        return "stored"


async def test_no_embedded_chunk_fails_the_file(tmp_path: Path) -> None:
    path = tmp_path / "Leistungsaufstellung_(2023-12-12).doc"
    path.write_bytes(b"x")
    pipe = _Pipe(chunks=3, reject={0, 1, 2})
    with pytest.raises(ingest.EmbeddingFailedError, match="none of the 3 chunks"):
        await pipe._ingest_single_document(str(path), "hash")
    assert pipe.saved == [], "no document row without chunks"


async def test_rejected_chunks_go_to_the_embedding_backlog(tmp_path: Path) -> None:
    path = tmp_path / "Mail von Dr. Muster.msg"
    path.write_bytes(b"x")
    pipe = _Pipe(chunks=4, reject={2})
    result = await pipe._ingest_single_document(str(path), "hash")
    assert pipe.saved == [{"chunks": 3, "pending": 1}]
    assert result.chunks_created == 3 and result.chunks_pending == 1
    assert [c.index for c in result.pending_chunks] == [2]


async def test_embed_chunks_reports_rejected_chunks_as_pending_for_the_backlog() -> None:
    pipe = _Pipe(chunks=0, reject={1})
    chunks = [DocumentChunk(content=f"Teil {i}", index=i, start_char=0, end_char=6, metadata={}, token_count=10) for i in range(3)]
    embedded, pending = await pipe._embed_chunks(chunks, None)
    assert [c.index for c in embedded] == [0, 2] and [c.index for c in pending] == [1]
    embedded, pending, rejected = await pipe._embed_chunks_detailed(chunks, None)
    assert pending == [] and [c.index for c in rejected] == [1]
