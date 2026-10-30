"""Tests for the per-file budgets of the concurrent ingestion loop (F43/F44, 2026-10-28).

Findings of ``docs/system-validation/INGESTION_PROMPT_TEST_LOG_2026-10-24.md``:

- F44: the conversion budget wrapped the whole file (conversion, chunking, embedding,
  storing), so a document converted within its budget timed out in the embedder queue while
  the embedder was slow, was not stored, and its conversion was re-queued for a retry that
  failed the same way. The budget now stops when the conversion returns; chunking, embedding
  and storing get their own budget derived from the amount of work and the embedder's recent
  throughput, and when that runs out the document is stored with the chunks embedded so far
  and the rest is embedded from the run's embedding backlog after the round.
- F43: a timed-out file appeared in the stats only after its last attempt, and the retry had
  the same 1,800 s cap. The timeout is now recorded on the first attempt (every later attempt
  replaces that record), the retry's cap grows per attempt, and the last retry of a PDF drops
  the table-structure stage.

The loop tests run the real ``_ingest_documents_concurrent`` and the real
``_ingest_single_document`` over a stubbed reader, chunker and MongoDB layer, with the real
``EmbeddingGenerator.embed_chunks_within`` over a sleeping batch call. No Docling, no network.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")
embedder_mod = pytest.importorskip("src.ingestion.embedder")
workers = pytest.importorskip("src.ingestion.conversion_workers")
from src.ingestion.chunker import DocumentChunk  # noqa: E402
from backend.tests import conversion_worker_targets as targets  # noqa: E402


@pytest.fixture(autouse=True)
def _literal_budgets(monkeypatch: pytest.MonkeyPatch):
    """Budgets in these tests are literal seconds; stats are flushed only when asked."""
    monkeypatch.setattr(ingest, "conversion_budget_factor", lambda *a, **k: 1.0)
    monkeypatch.setattr(ingest, "stats_flush_config", lambda: (1000, 3600.0))


@pytest.fixture
def embed_budget(monkeypatch: pytest.MonkeyPatch):
    """Set the embedding budget (floor, margin, cap) of the run under test."""

    def _set(floor: float, margin: float = 1.0, cap: Optional[float] = None) -> None:
        config = ingest.EmbeddingBudgetConfig(floor_seconds=floor, margin=margin, max_seconds=cap or max(floor, 3600.0))
        monkeypatch.setattr(ingest, "embedding_budget_config", lambda: config)

    return _set


@pytest.fixture
def retries(monkeypatch: pytest.MonkeyPatch):
    """Set the retry count of the global timeout config for one test."""

    def _set(count: int) -> None:
        monkeypatch.setattr(ingest.get_timeout_config(), "max_retries", count)

    return _set


def _write(tmp_path: Path, **contents: str) -> Dict[str, str]:
    """Create files named by keyword with the given contents; returns name -> path."""
    paths = {}
    for name, text in contents.items():
        file_name = name.replace("_", " ") + ".txt"
        path = tmp_path / file_name
        path.write_text(text, encoding="utf-8")
        paths[file_name] = str(path)
    return paths


def _embedder(batch_size: int, seconds_per_batch: float) -> Any:
    """A real ``EmbeddingGenerator`` whose batch call sleeps instead of calling a provider."""
    generator = embedder_mod.EmbeddingGenerator.__new__(embedder_mod.EmbeddingGenerator)
    generator.model = "stub-embedder"
    generator.batch_size = batch_size
    generator.throughput = embedder_mod.EmbeddingThroughput()
    generator.calls = 0

    async def generate_embeddings_batch(texts: List[str], task_type: Optional[str] = None) -> List[List[float]]:
        generator.calls += 1
        await asyncio.sleep(seconds_per_batch)
        return [[0.1, 0.2, 0.3] for _ in texts]

    generator.generate_embeddings_batch = generate_embeddings_batch
    return generator


class _Chunker:
    """Splits every document into ``count`` chunks of 100 tokens."""

    def __init__(self, count: int) -> None:
        self.count = count

    async def chunk_document(self, content: str, title: str, source: str, metadata=None, docling_doc=None) -> List[DocumentChunk]:
        return [
            DocumentChunk(
                content=f"{title} part {i}", index=i, start_char=0, end_char=10,
                metadata={"source": source}, token_count=100,
            )
            for i in range(self.count)
        ]


class _BudgetPipeline(ingest.DocumentIngestionPipeline):
    """Real loop and real per-document steps over a sleeping reader and a sleeping embedder."""

    def __init__(
        self,
        *,
        conversion_seconds: Optional[Dict[str, float]] = None,
        chunks: int = 4,
        batch_size: int = 1,
        seconds_per_batch: float = 0.0,
        slow_once: bool = False,
    ) -> None:
        self._initialized = True
        self.db = None
        self.documents_folders: List[str] = []
        self.chunker = _Chunker(chunks)
        self.embedder = _embedder(batch_size, seconds_per_batch)
        self.conversion_seconds = conversion_seconds or {}
        self.slow_once = slow_once
        self.conversions: List[str] = []
        self.ingest_calls: List[str] = []
        self.saved: Dict[str, Dict[str, Any]] = {}
        self.appended: List[Tuple[str, int, int]] = []
        self.stats: List[Any] = []
        self.flushes: List[List[Any]] = []

    async def _ingest_single_document(self, file_path: str, content_hash: Optional[str] = None):
        self.ingest_calls.append(Path(file_path).name)
        return await super()._ingest_single_document(file_path, content_hash)

    def _read_document(self, file_path: str, options=None):
        name = Path(file_path).name
        attempt = self.conversions.count(name)
        self.conversions.append(name)
        if not (self.slow_once and attempt > 0):
            time.sleep(self.conversion_seconds.get(name, 0.0))
        return (f"text of {name}", None)

    async def _save_to_mongodb(self, title, source, content, chunks, metadata, content_hash=None, pending_chunks=0):
        self.saved[Path(source).name] = {"chunks": len(chunks), "pending": pending_chunks}
        return f"doc-{Path(source).name}"

    async def _append_chunks_to_document(self, document_id: str, chunks, remaining: int) -> None:
        self.appended.append((document_id, len(chunks), remaining))

    async def _save_ingestion_stats(self, batch: List[Any]) -> bool:
        self.flushes.append(list(batch))
        self.stats.extend(batch)
        return True

    def stats_of(self, name: str) -> List[Any]:
        return [s for s in self.stats if s.file_name == name]


async def _run(pipe: Any, files: List[str], **kwargs: Any):
    return await pipe._ingest_documents_concurrent(
        document_files=files,
        existing_hashes={},
        incremental=True,
        max_concurrent=kwargs.pop("max_concurrent", 2),
        **kwargs,
    )


# --------------------------------------------------------------------------- F44: the two budgets


async def test_slow_embedding_after_a_timely_conversion_still_stores_the_document(tmp_path: Path, monkeypatch, embed_budget) -> None:
    """(a) The conversion clock stops when the conversion returns; the embedder may run past it."""
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 0.5)  # the old whole-file budget
    embed_budget(floor=5.0)
    paths = _write(tmp_path, book="b")
    # Conversion 0.2 s, then 3 batches x 0.4 s = 1.2 s of embedding: 1.4 s > 0.5 s in all
    pipe = _BudgetPipeline(conversion_seconds={"book.txt": 0.2}, chunks=3, batch_size=1, seconds_per_batch=0.4)

    started = time.monotonic()
    results, _ = await _run(pipe, list(paths.values()))
    elapsed = time.monotonic() - started

    assert elapsed >= 1.2
    assert pipe.saved["book.txt"] == {"chunks": 3, "pending": 0}
    assert pipe.embedder.calls == 3
    assert (results[0].document_id, results[0].chunks_created, results[0].chunks_pending) == ("doc-book.txt", 3, 0)
    (stat,) = pipe.stats_of("book.txt")
    assert (stat.success, stat.error_type, stat.chunks_created, stat.retry_count) == (True, None, 3, 0)
    assert pipe.conversions == ["book.txt"], "the file was converted again"


async def test_a_conversion_over_its_budget_still_times_out_in_thread_mode(tmp_path: Path, monkeypatch, retries, embed_budget) -> None:
    """The conversion budget itself is unchanged: a conversion that overruns it is a timeout."""
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 0.2)
    retries(0)
    embed_budget(floor=5.0)
    paths = _write(tmp_path, slow="s")
    pipe = _BudgetPipeline(conversion_seconds={"slow.txt": 0.6})

    await _run(pipe, list(paths.values()), max_concurrent=1)

    (stat,) = pipe.stats_of("slow.txt")
    assert (stat.error_type, stat.classification, stat.success) == ("timeout", "timeout", False)
    assert "slow.txt" not in pipe.saved


async def test_embedding_budget_expiry_stores_a_partial_and_the_backlog_finishes_it(tmp_path: Path, monkeypatch, embed_budget) -> None:
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 5.0)
    embed_budget(floor=0.3, margin=1.0, cap=0.3)
    paths = _write(tmp_path, book="b")
    # 4 chunks, one per batch, 0.2 s each: batches 1-2 fit (0.2 s < 0.3 s before batch 2), batch 3 does not
    pipe = _BudgetPipeline(chunks=4, batch_size=1, seconds_per_batch=0.2)
    snapshots: List[Dict[str, int]] = []

    results, _ = await _run(pipe, list(paths.values()), stats_callback=snapshots.append)

    # Stored at once with the chunks embedded so far, the rest marked pending
    assert pipe.saved["book.txt"] == {"chunks": 2, "pending": 2}
    # The backlog embedded the remaining two after the round and appended them
    assert pipe.appended == [("doc-book.txt", 2, 0)]
    assert pipe.conversions == ["book.txt"], "the file must not be reconverted"
    assert (results[0].chunks_created, results[0].chunks_pending) == (4, 0)
    partial, final = pipe.stats_of("book.txt")
    assert (partial.error_type, partial.chunks_created, partial.chunks_pending, partial.success) == ("partial", 2, 2, True)
    assert "2 pending in the embedding backlog" in partial.error_message
    assert (final.error_type, final.chunks_created, final.chunks_pending, final.supersedes) == (None, 4, 0, "partial")
    assert final.record_id == partial.record_id
    assert snapshots[-1]["partial"] == 0 and snapshots[-1]["success"] == 1


async def test_backlog_gives_up_after_its_passes_and_records_the_remainder(tmp_path: Path, monkeypatch, embed_budget) -> None:
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 5.0)
    monkeypatch.setattr(ingest, "MAX_EMBEDDING_BACKLOG_PASSES", 1)
    embed_budget(floor=0.1, margin=1.0, cap=0.1)
    paths = _write(tmp_path, book="b")
    pipe = _BudgetPipeline(chunks=4, batch_size=1, seconds_per_batch=0.2)  # one batch per pass

    results, _ = await _run(pipe, list(paths.values()))

    assert pipe.saved["book.txt"] == {"chunks": 1, "pending": 3}
    assert pipe.appended == [("doc-book.txt", 1, 2)]
    assert (results[0].chunks_created, results[0].chunks_pending) == (2, 2)
    partial, final = pipe.stats_of("book.txt")
    assert (final.error_type, final.chunks_pending, final.supersedes, final.success) == ("partial", 2, "partial", True)
    assert "gave up after 1 passes" in final.error_message


async def test_a_task_that_hangs_after_its_conversion_hits_the_hard_cap_as_an_error(tmp_path: Path, monkeypatch, retries, embed_budget) -> None:
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 5.0)
    monkeypatch.setattr(ingest, "INDEXING_HARD_CAP_GRACE_SECONDS", 0.0)
    retries(2)
    embed_budget(floor=0.1)  # hard cap = three floors (+ the zeroed grace) after the conversion
    paths = _write(tmp_path, hung="h")

    class _Hanging(_BudgetPipeline):
        async def _ingest_single_document(self, file_path: str, content_hash: Optional[str] = None):
            self.ingest_calls.append(Path(file_path).name)
            await self._convert_document(file_path)
            await asyncio.sleep(30)  # never chunks, never sets the embedding budget

    pipe = _Hanging()
    started = time.monotonic()
    await _run(pipe, list(paths.values()), max_concurrent=1)

    assert time.monotonic() - started < 10
    (stat,) = pipe.stats_of("hung.txt")
    assert (stat.error_type, stat.classification) == ("error", "error")
    assert "hard cap" in stat.error_message
    assert pipe.ingest_calls == ["hung.txt"], "a hang after the conversion is not a timeout to retry"


# --------------------------------------------------------------------------- F43: timeouts recorded at once


async def test_timeout_is_recorded_on_the_first_attempt(tmp_path: Path, monkeypatch, retries, embed_budget) -> None:
    """(c) The timeout record exists before the retry starts; the retry replaces it."""
    monkeypatch.setattr(ingest, "stats_flush_config", lambda: (1, 3600.0))  # every record flushed at once
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 0.2)
    retries(1)
    embed_budget(floor=5.0)
    paths = _write(tmp_path, slow="s")
    timeouts_flushed_at_start: List[int] = []
    snapshots: List[Dict[str, int]] = []

    class _Watching(_BudgetPipeline):
        async def _ingest_single_document(self, file_path: str, content_hash: Optional[str] = None):
            timeouts_flushed_at_start.append(sum(1 for s in self.stats if s.error_type == "timeout"))
            return await super()._ingest_single_document(file_path, content_hash)

    pipe = _Watching(conversion_seconds={"slow.txt": 0.6})
    await _run(pipe, list(paths.values()), max_concurrent=1, stats_callback=snapshots.append)

    assert timeouts_flushed_at_start == [0, 1], "the retry started before the first timeout was written"
    first, final = pipe.stats_of("slow.txt")
    assert (first.retry_count, first.error_type, first.supersedes) == (0, "timeout", None)
    assert "retry queued" in first.error_message
    assert first.retry_pending is True and first.to_dict()["retry_pending"] is True  # not final: a resume re-attempts it
    assert (final.retry_count, final.error_type, final.supersedes) == (1, "timeout", "timeout")
    assert final.retry_pending is False and "retry_pending" not in final.to_dict()
    assert final.record_id == first.record_id
    assert "retries exhausted: 2/2" in final.error_message
    assert [s["timeout"] for s in snapshots] == [1, 1]  # one file, counted once


async def test_a_successful_retry_resolves_the_timeout_record(tmp_path: Path, monkeypatch, retries, embed_budget) -> None:
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 0.2)
    retries(1)
    embed_budget(floor=5.0)
    paths = _write(tmp_path, slow="s")
    snapshots: List[Dict[str, int]] = []
    pipe = _BudgetPipeline(conversion_seconds={"slow.txt": 0.6}, slow_once=True, chunks=2)

    results, _ = await _run(pipe, list(paths.values()), max_concurrent=1, stats_callback=snapshots.append)

    assert pipe.conversions == ["slow.txt", "slow.txt"]
    assert pipe.saved["slow.txt"] == {"chunks": 2, "pending": 0}
    first, final = pipe.stats_of("slow.txt")
    assert (first.error_type, final.error_type, final.success, final.retry_count, final.supersedes) == ("timeout", None, True, 1, "timeout")
    assert final.record_id == first.record_id
    assert snapshots[-1]["timeout"] == 0 and snapshots[-1]["success"] == 1
    assert len(results) == 1 and results[0].chunks_created == 2


def test_recorder_moves_a_file_between_outcomes_when_a_record_is_superseded() -> None:
    now = datetime.now()

    def stat(error_type: Optional[str], supersedes: Optional[str] = None, record_id=None) -> Any:
        values = dict(
            file_path="/docs/a.pdf", file_name="a.pdf", file_size_bytes=10, started_at=now, completed_at=now,
            processing_time_ms=1.0, chunks_created=0, success=error_type is None, error_type=error_type,
            error_message=None, timeout_seconds=1.0, profile_key="p", supersedes=supersedes,
        )
        if record_id is not None:
            values["record_id"] = record_id
        return ingest.IngestionFileStats(**values)

    async def save(batch: List[Any]) -> bool:
        return True

    recorder = ingest.IngestionStatsRecorder(save, flush_every_files=100, flush_every_seconds=3600)
    first = stat("timeout")
    recorder.add(first)
    assert (recorder.counts["timeout"], recorder.recorded) == (1, 1)
    recorder.add(stat("timeout", supersedes="timeout", record_id=first.record_id))  # the retry timed out too
    assert (recorder.counts["timeout"], recorder.recorded) == (1, 1)
    recorder.add(stat(None, supersedes="timeout", record_id=first.record_id))  # the next retry succeeded
    assert (recorder.counts["timeout"], recorder.counts["success"], recorder.recorded) == (0, 1, 1)
    partial = stat("partial")
    recorder.add(partial)
    recorder.add(stat(None, supersedes="partial", record_id=partial.record_id))
    assert (recorder.counts["partial"], recorder.counts["success"]) == (0, 2)
    assert set(recorder.snapshot()) == set(ingest.IngestionStatsRecorder.COUNTED) | {"written"}


def _matches(doc: dict, query: dict) -> bool:
    """Enough of MongoDB's query semantics for the filters these writers and readers use."""
    for key, expected in query.items():
        value: Any = doc
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(expected, dict):
            for op, operand in expected.items():
                if op == "$ne" and value == operand:
                    return False
                if op == "$exists" and (value is not None) != bool(operand):
                    return False
                if op == "$not" and isinstance(operand, dict) and "$gt" in operand:
                    if value is not None and value > operand["$gt"]:
                        return False
                if op == "$in" and value not in operand:
                    return False
        elif value != expected:
            return False
    return True


class _Cursor:
    def __init__(self, rows: List[dict]) -> None:
        self.rows = rows

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for row in self.rows:
            yield row


class _Collection:
    """An in-memory collection with the write semantics ``_save_ingestion_stats`` relies on."""

    def __init__(self, docs: Optional[List[dict]] = None) -> None:
        self.docs: List[dict] = [dict(d) for d in docs or []]
        self.ops: List[Tuple[str, Any]] = []

    def find(self, query: dict, projection: Optional[dict] = None) -> _Cursor:
        self.ops.append(("find", query))
        return _Cursor([d for d in self.docs if _matches(d, query)])

    async def insert_many(self, docs: List[dict], ordered: bool = True) -> None:
        self.ops.append(("insert_many", [d.get("error_type", d.get("chunk_index")) for d in docs]))
        self.docs.extend(dict(d) for d in docs)

    async def insert_one(self, doc: dict) -> None:
        self.ops.append(("insert_one", doc.get("error_type")))
        self.docs.append(dict(doc))

    async def replace_one(self, query: dict, doc: dict, upsert: bool = False) -> None:
        self.ops.append(("replace_one", query))
        for i, existing in enumerate(self.docs):
            if _matches(existing, query):
                self.docs[i] = dict(doc)
                return
        if upsert:
            self.docs.append(dict(doc))

    async def update_one(self, query: dict, update: dict, upsert: bool = False) -> None:
        self.ops.append(("update_one", query, upsert))
        for existing in self.docs:
            if _matches(existing, query):
                existing.update(update.get("$set", {}))
                for key in update.get("$unset", {}):
                    existing.pop(key, None)
                return
        if upsert:
            self.docs.append({**query, **update.get("$set", {})})

    async def delete_many(self, query: dict) -> None:
        self.ops.append(("delete_many", query))
        self.docs = [d for d in self.docs if not _matches(d, query)]


def _attempt_stat(error_type: Optional[str], retry_count: int, supersedes: Optional[str] = None, record_id=None, retry_pending: bool = False) -> Any:
    now = datetime.now()
    values = dict(
        file_path="/docs/a.pdf", file_name="a.pdf", file_size_bytes=10, started_at=now, completed_at=now,
        processing_time_ms=1.0, chunks_created=0 if error_type else 5, success=error_type is None,
        error_type=error_type, error_message=f"{error_type} {retry_count}", timeout_seconds=1800.0,
        profile_key="p", job_id="job-1", retry_count=retry_count, supersedes=supersedes, retry_pending=retry_pending,
    )
    if record_id is not None:
        values["record_id"] = record_id
    return ingest.IngestionFileStats(**values)


async def test_save_ingestion_stats_replaces_the_record_of_a_retried_file() -> None:
    stats, failed = _Collection(), _Collection()
    pipe = _BudgetPipeline()
    pipe.db = {"ingestion_stats": stats, "failed_documents": failed}
    save = ingest.DocumentIngestionPipeline._save_ingestion_stats

    first = _attempt_stat("timeout", 0, retry_pending=True)
    assert await save(pipe, [first]) is True
    assert [d["_id"] for d in stats.docs] == [first.record_id]
    assert [(d["error_type"], d["retry_count"], d["resolved"], d["retry_pending"]) for d in failed.docs] == [("timeout", 0, False, True)]

    again = _attempt_stat("timeout", 1, supersedes="timeout", record_id=first.record_id, retry_pending=True)
    assert await save(pipe, [again]) is True
    assert [d["retry_count"] for d in stats.docs] == [1], "the retry replaces the row instead of adding one"
    assert [(d["retry_count"], d["error_message"], d["retry_pending"]) for d in failed.docs] == [(1, "timeout 1", True)]

    done = _attempt_stat(None, 2, supersedes="timeout", record_id=first.record_id)
    assert await save(pipe, [done]) is True
    assert [(d["success"], d["retry_count"]) for d in stats.docs] == [(True, 2)]
    (row,) = failed.docs
    assert (row["resolved"], row["resolution"], row["retry_pending"]) == (True, "retry 2 ended as success", False)

    # A retry that fails in a new way resolves the timeout row and gets a row of its own
    stats, failed = _Collection(), _Collection()
    pipe.db = {"ingestion_stats": stats, "failed_documents": failed}
    first = _attempt_stat("timeout", 0, retry_pending=True)
    broken = _attempt_stat("error", 1, supersedes="timeout", record_id=first.record_id)
    assert await save(pipe, [first]) is True and await save(pipe, [broken]) is True
    assert [(d["error_type"], d["resolved"]) for d in failed.docs] == [("timeout", True), ("error", False)]


async def test_save_ingestion_stats_handles_an_attempt_and_its_retry_in_one_batch() -> None:
    """Review B: the first attempt's row must exist before the retry's update looks for it."""
    stats, failed = _Collection(), _Collection()
    pipe = _BudgetPipeline()
    pipe.db = {"ingestion_stats": stats, "failed_documents": failed}
    save = ingest.DocumentIngestionPipeline._save_ingestion_stats

    first = _attempt_stat("timeout", 0, retry_pending=True)
    again = _attempt_stat("timeout", 1, supersedes="timeout", record_id=first.record_id, retry_pending=True)
    done = _attempt_stat(None, 2, supersedes="timeout", record_id=first.record_id)
    assert await save(pipe, [first, again, done]) is True

    assert [(d["retry_count"], d["success"]) for d in stats.docs] == [(2, True)]
    (row,) = failed.docs
    assert (row["retry_count"], row["resolved"], row["retry_pending"]) == (2, True, False)

    # A resumed run's new first attempt merges into the row its earlier run left pending
    stats, failed = _Collection(), _Collection()
    pipe.db = {"ingestion_stats": stats, "failed_documents": failed}
    earlier = _attempt_stat("timeout", 1, retry_pending=True)
    assert await save(pipe, [earlier]) is True
    fresh = _attempt_stat("timeout", 0, retry_pending=True)
    assert await save(pipe, [fresh]) is True
    assert len(failed.docs) == 1 and failed.docs[0]["retry_count"] == 0 and failed.docs[0]["retry_pending"] is True


# --------------------------------------------------------------------------- review A/D: partial documents


def _db_pipeline(documents: List[dict], chunks: Optional[List[dict]] = None) -> Tuple[Any, _Collection, _Collection]:
    from types import SimpleNamespace

    pipe = _BudgetPipeline()
    docs, chunk_rows = _Collection(documents), _Collection(chunks or [])
    pipe.db = {"documents": docs, "chunks": chunk_rows}
    pipe.settings = SimpleNamespace(mongodb_collection_documents="documents", mongodb_collection_chunks="chunks")
    return pipe, docs, chunk_rows


async def test_incremental_filters_treat_a_partially_embedded_document_as_not_ingested() -> None:
    """Review A: a resume must redo a document whose backlog never finished (restart, give-up)."""
    pipe, docs, _ = _db_pipeline([
        {"source": "done.pdf", "content_hash": "h-done", "metadata": {"chunks_count": 3}},
        {"source": "finished.pdf", "content_hash": "h-fin", "metadata": {"chunks_count": 9, "embedding_completed_at": "x"}},
        {"source": "partial.pdf", "content_hash": "h-part", "metadata": {"chunks_count": 9, "chunks_pending": 4}},
        {"source": "settled.pdf", "content_hash": "h-set", "metadata": {"chunks_count": 2, "chunks_pending": 0}},
    ])
    base = ingest.DocumentIngestionPipeline

    assert await base._get_existing_sources(pipe) == {"done.pdf", "finished.pdf", "settled.pdf"}
    assert await base._get_existing_hashes(pipe) == {"h-done": "done.pdf", "h-fin": "finished.pdf", "h-set": "settled.pdf"}
    assert ingest.complete_documents_filter() == {"metadata.chunks_pending": {"$not": {"$gt": 0}}}
    assert all(op[1].get("metadata.chunks_pending") == {"$not": {"$gt": 0}} for op in docs.ops if op[0] == "find")


def test_api_resume_queue_uses_the_same_filter() -> None:
    source = Path(pytest.importorskip("backend.routers.ingestion").__file__).read_text(encoding="utf-8")
    assert source.count("documents_collection.find(complete_documents_filter(), {\"source\": 1})") == 2
    assert 'documents_collection.find({}, {"source": 1})' not in source


async def test_appending_backlog_chunks_is_idempotent() -> None:
    """Review D: a pass redone after a failed append must not leave duplicate chunk rows."""
    from bson import ObjectId

    oid = ObjectId()
    pipe, docs, chunk_rows = _db_pipeline(
        [{"_id": oid, "source": "book.pdf", "metadata": {"chunks_count": 4, "chunks_pending": 2}}],
        [{"document_id": oid, "chunk_index": 0}, {"document_id": oid, "chunk_index": 1}, {"document_id": oid, "chunk_index": 2}],  # 2 was left by a failed pass
    )
    pending = [DocumentChunk(content=f"c{i}", index=i, start_char=0, end_char=2, metadata={}, token_count=5, embedding=[0.1]) for i in (2, 3)]
    append = ingest.DocumentIngestionPipeline._append_chunks_to_document

    await append(pipe, str(oid), pending[:1], remaining=1)
    assert [op[0] for op in chunk_rows.ops] == ["delete_many", "insert_many"]
    assert chunk_rows.ops[0][1] == {"document_id": oid, "chunk_index": {"$in": [2]}}
    assert sorted(d["chunk_index"] for d in chunk_rows.docs) == [0, 1, 2]  # no duplicate of index 2
    assert docs.ops[-1][:2] == ("update_one", {"_id": oid})

    await append(pipe, str(oid), pending[1:], remaining=0)
    assert sorted(d["chunk_index"] for d in chunk_rows.docs) == [0, 1, 2, 3]
    unset_op = docs.ops[-1]
    assert unset_op[0] == "update_one" and unset_op[1] == {"_id": oid}


# --------------------------------------------------------------------------- F43: the retry itself


def _pool(size: int, **kwargs: Any) -> Any:
    return workers.ConversionProcessPool(
        size,
        target=targets.sleepy_reader,
        init=workers.WorkerInit(apply_pipeline_config=False, warm_up=False),
        ready_timeout=kwargs.pop("ready_timeout", 120.0),
        **kwargs,
    )


class _ProcessPipeline(_BudgetPipeline):
    """Converts in worker processes; the per-document steps after the conversion are trivial."""

    async def _ingest_single_document(self, file_path: str, content_hash: Optional[str] = None):
        name = Path(file_path).name
        self.ingest_calls.append(name)
        content, _ = await self._convert_document(file_path)
        self.saved[name] = {"content": content}
        return ingest.IngestionResult(document_id=f"doc-{name}", title=name, chunks_created=1, processing_time_ms=1.0, errors=[])


async def test_a_conversion_over_its_budget_is_killed_and_retried_with_a_larger_budget(tmp_path: Path, monkeypatch, retries, caplog) -> None:
    """(b) The kill-on-timeout stays; the retry gets more time and the log says how much."""
    monkeypatch.setattr(
        ingest, "calculate_file_timeout",
        lambda size, file_path=None, retry_attempt=0, **k: 1.0 * (2 ** retry_attempt),
    )
    retries(1)
    paths = _write(tmp_path, stuck="30", quick="0")
    pipe = _ProcessPipeline()
    pool = pipe._conversion_pool = _pool(2)
    try:
        with caplog.at_level(logging.INFO, logger="src.ingestion.ingest"):
            started = time.monotonic()
            results, _ = await _run(pipe, [paths["stuck.txt"], paths["quick.txt"]], max_concurrent=2)
            elapsed = time.monotonic() - started
    finally:
        await pipe._stop_conversion_pool()

    assert elapsed < 90, "a killed conversion must not be waited for"
    assert pool.kills == 2, "both attempts of the stuck conversion must have been killed"
    assert pipe.ingest_calls.count("stuck.txt") == 2
    assert "Will retry with 2s timeout." in caplog.text
    assert "timeout=2s, attempt 2" in caplog.text
    first, final = pipe.stats_of("stuck.txt")
    assert (first.retry_count, first.timeout_seconds, final.retry_count, final.timeout_seconds) == (0, 1.0, 1, 2.0)
    assert (final.error_type, final.supersedes, final.record_id == first.record_id) == ("timeout", "timeout", True)
    assert pipe.saved["quick.txt"]["content"].startswith("slept 0.0")
    assert len(results) == 2


async def test_the_last_retry_of_a_pdf_converts_without_table_structure(tmp_path: Path, monkeypatch, retries, caplog) -> None:
    monkeypatch.setattr(
        ingest, "calculate_file_timeout",
        lambda size, file_path=None, retry_attempt=0, **k: 1.0 if retry_attempt == 0 else 30.0,
    )
    retries(1)
    pdf = tmp_path / "tables.pdf"
    pdf.write_text("2", encoding="utf-8")  # 2 s: over the first budget, within the retry's
    pipe = _ProcessPipeline()
    pipe._conversion_pool = _pool(1)
    try:
        with caplog.at_level(logging.WARNING, logger="src.ingestion.ingest"):
            results, _ = await _run(pipe, [str(pdf)], max_concurrent=1)
    finally:
        await pipe._stop_conversion_pool()

    assert "Will retry with 30s timeout (table structure off)." in caplog.text
    assert pipe.saved["tables.pdf"]["content"].endswith(" table_structure=False")
    first, final = pipe.stats_of("tables.pdf")
    assert (first.error_type, final.error_type, final.success, final.retry_count) == ("timeout", None, True, 1)
    assert results[0].document_id == "doc-tables.pdf"


def test_retry_budget_lifts_the_cap_and_the_last_pdf_retry_drops_tables() -> None:
    cfg = ingest.TimeoutConfig()  # cap 1800 s, retry multiplier 1.5
    big = 500 * 1024 * 1024
    assert ingest.calculate_file_timeout(big, file_path="/x/a.pdf", config=cfg) == 1800
    assert ingest.calculate_file_timeout(big, file_path="/x/a.pdf", config=cfg, retry_attempt=1) == pytest.approx(2700)
    assert ingest.calculate_file_timeout(big, file_path="/x/a.pdf", config=cfg, retry_attempt=2) == pytest.approx(4050)
    # the concurrency factor still never lifts the cap
    assert ingest.calculate_file_timeout(big, file_path="/x/a.pdf", config=cfg, concurrency_factor=4.0) == 1800

    assert ingest.retry_conversion_options("/x/a.pdf", 0, 2) is None
    assert ingest.retry_conversion_options("/x/a.pdf", 1, 2) is None  # tables stay on until the last attempt
    assert ingest.retry_conversion_options("/x/a.pdf", 2, 2) == ingest.ConversionOptions(table_structure=False)
    assert ingest.retry_conversion_options("/x/a.PDF", 1, 1) == ingest.ConversionOptions(table_structure=False)
    assert ingest.retry_conversion_options("/x/a.docx", 2, 2) is None
    assert ingest.retry_conversion_options("/x/a.pdf", 0, 0) is None

    options = ingest.ConversionOptions(table_structure=False)
    assert options.to_dict() == {"table_structure": False} and options.describe() == "table structure off"
    assert ingest.ConversionOptions().to_dict() == {} and ingest.ConversionOptions().describe() == ""


async def test_worker_processes_receive_the_conversion_options(tmp_path: Path) -> None:
    paths = _write(tmp_path, plain="0")
    pool = _pool(1)
    try:
        content, _ = await pool.convert(paths["plain.txt"])
        assert content.startswith("slept 0.0") and "table_structure" not in content
        content, _ = await pool.convert(paths["plain.txt"], options={"table_structure": False})
        assert content.endswith(" table_structure=False")
    finally:
        pool.shutdown()


# --------------------------------------------------------------------------- budgets and throughput


def test_indexing_budget_formula(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest, "INDEXING_HARD_CAP_GRACE_SECONDS", 0.0)
    cfg = ingest.EmbeddingBudgetConfig(floor_seconds=600, margin=3.0, max_seconds=14400, default_seconds_per_1k_tokens=2.0)
    assert ingest.indexing_budget(10_000, 1.0, cfg).seconds == 600  # 10 s predicted x 3 < floor
    assert ingest.indexing_budget(100_000, 4.0, cfg).seconds == pytest.approx(1200)  # 400 s predicted x 3
    assert ingest.indexing_budget(450_000, 11.5, cfg).seconds == 14400  # a book behind a slow embedder: the cap
    assert ingest.indexing_budget(100_000, None, cfg).seconds_per_1k_tokens == 2.0  # nothing measured yet
    assert ingest.indexing_budget(100_000, None, cfg).seconds == 600
    budget = ingest.indexing_budget(100_000, 4.0, cfg)
    assert budget.deadline == pytest.approx(budget.started_at + 1200)
    assert budget.hard_deadline == pytest.approx(budget.started_at + 3 * 1200)  # grace zeroed above


def test_embedding_budget_config_reads_environment_then_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Settings:
        ingest_embed_budget_floor_seconds = 120.0
        ingest_embed_budget_margin = 2.0
        ingest_embed_budget_max_seconds = 900.0

    monkeypatch.setattr(ingest, "load_settings", lambda *a, **k: _Settings())
    for name in ("INGEST_EMBED_BUDGET_FLOOR_SECONDS", "INGEST_EMBED_BUDGET_MARGIN", "INGEST_EMBED_BUDGET_MAX_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    assert ingest.embedding_budget_config() == ingest.EmbeddingBudgetConfig(floor_seconds=120.0, margin=2.0, max_seconds=900.0)
    monkeypatch.setenv("INGEST_EMBED_BUDGET_FLOOR_SECONDS", "1200")
    monkeypatch.setenv("INGEST_EMBED_BUDGET_MAX_SECONDS", "600")  # below the floor: the floor wins
    assert ingest.embedding_budget_config() == ingest.EmbeddingBudgetConfig(floor_seconds=1200.0, margin=2.0, max_seconds=1200.0)

    def broken(*args: Any, **kwargs: Any):
        raise RuntimeError("no MONGODB_URI")

    monkeypatch.setattr(ingest, "load_settings", broken)
    for name in ("INGEST_EMBED_BUDGET_FLOOR_SECONDS", "INGEST_EMBED_BUDGET_MAX_SECONDS"):
        monkeypatch.delenv(name)
    assert ingest.embedding_budget_config() == ingest.EmbeddingBudgetConfig()


def test_settings_surface_carries_the_budget_fields() -> None:
    from src.settings import Settings

    fields = Settings.model_fields
    assert fields["ingest_embed_budget_floor_seconds"].default == ingest.DEFAULT_EMBED_BUDGET_FLOOR_SECONDS
    assert fields["ingest_embed_budget_margin"].default == ingest.DEFAULT_EMBED_BUDGET_MARGIN
    assert fields["ingest_embed_budget_max_seconds"].default == ingest.DEFAULT_EMBED_BUDGET_MAX_SECONDS


def test_embedding_throughput_window() -> None:
    throughput = embedder_mod.EmbeddingThroughput(window=2)
    assert throughput.seconds_per_1k_tokens() is None
    throughput.record(1000, 10.0)
    assert throughput.seconds_per_1k_tokens() == pytest.approx(10.0)
    throughput.record(1000, 2.0)
    throughput.record(1000, 2.0)  # the 10 s batch fell out of the window
    assert throughput.seconds_per_1k_tokens() == pytest.approx(2.0)
    throughput.record(0, 5.0)  # ignored
    assert throughput.batches == 3


async def test_embed_chunks_within_stops_between_batches_and_measures_throughput() -> None:
    generator = _embedder(batch_size=2, seconds_per_batch=0.1)
    chunks = [DocumentChunk(content=f"c{i}", index=i, start_char=0, end_char=2, metadata={}, token_count=50) for i in range(6)]

    embedded, pending = await generator.embed_chunks_within(chunks, deadline=time.monotonic() + 0.15)
    assert [c.index for c in embedded] == [0, 1, 2, 3]  # batch 2 started at 0.1 s < 0.15 s, batch 3 did not
    assert [c.index for c in pending] == [4, 5]
    assert all(c.embedding == [0.1, 0.2, 0.3] for c in embedded)
    assert generator.recent_seconds_per_1k_tokens() == pytest.approx(1.0, rel=0.6)  # 0.1 s per 100 tokens

    embedded, pending = await generator.embed_chunks_within(pending)  # no deadline: everything
    assert ([c.index for c in embedded], pending) == ([4, 5], [])
    assert len(await generator.embed_chunks(chunks)) == 6  # the old entry point is unchanged
