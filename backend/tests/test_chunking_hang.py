"""The main-process hang after a conversion (2026-11-02/03) and the post-conversion budget.

The live Quellex API froze twice with its main thread spinning at one core for 35+ minutes,
no log line, ``/health`` dead. A ``py-spy dump`` of the live process and an offline
reproduction on the same file (a 2,670 x 32 cell spreadsheet, 2.7 MB of Markdown) put the
spin in docling-core's ``HybridChunker._split_table_by_columns``: the column search
re-tokenizes the whole table at every step and the segmentation repeats whenever a piece
overflows (2 s for a 100 x 10 table, 51 s for 200 x 20, hours for the sheet), on the
event-loop thread. Three layers now guard this: oversized tables are cut into row windows
before the chunker sees them (``presplit_large_tables``), the chunker's tokenizer checks a
deadline on every token count (``DeadlineTokenizer``), and the pipeline runs OCR repair and
chunking on a thread under ``INGEST_CHUNK_BUDGET_SECONDS``, recording an overrun as a
``timeout`` and moving on.

The tests with the real chunker need the MiniLM tokenizer in the local Hugging Face cache
and are skipped without it.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from docling_core.types.doc import DoclingDocument, TableCell, TableData  # noqa: E402

from src.ingestion import chunker as chunker_mod  # noqa: E402
from src.ingestion import ingest  # noqa: E402
from src.ingestion.chunker import (  # noqa: E402
    ChunkingConfig,
    ChunkingDeadlineExceeded,
    DocumentChunk,
    presplit_large_tables,
)
from backend.tests.test_ingestion_budgets import (  # noqa: E402,F401 - the loop fixtures
    _BudgetPipeline,
    _literal_budgets,
    _run,
    _write,
    embed_budget,
)


def _table_document(rows: int, cols: int, cell_words: int = 2, name: str = "synthetic") -> DoclingDocument:
    """A document with one ``rows`` x ``cols`` table; row 0 is the header (``col<c>``)."""
    doc = DoclingDocument(name=name)
    cells = []
    for r in range(rows):
        for c in range(cols):
            text = " ".join(f"w{r}_{c}_{k}" for k in range(cell_words)) if r else f"col{c}"
            cells.append(TableCell(
                text=text, row_span=1, col_span=1,
                start_row_offset_idx=r, end_row_offset_idx=r + 1,
                start_col_offset_idx=c, end_col_offset_idx=c + 1,
                column_header=(r == 0),
            ))
    doc.add_table(data=TableData(num_rows=rows, num_cols=cols, table_cells=cells))
    return doc


def _make_chunker() -> Any:
    try:
        return chunker_mod.DoclingHybridChunker(ChunkingConfig(max_tokens=1024, embed_max_tokens=2048))
    except Exception as exc:  # noqa: BLE001 - no tokenizer offline
        pytest.skip(f"chunker tokenizer unavailable: {exc}")


@pytest.fixture(scope="module")
def chunker() -> Any:
    return _make_chunker()


# --------------------------------------------------------------------------- the root cause


def test_presplit_cuts_an_oversized_table_into_row_windows_with_the_header() -> None:
    """Every cell survives, every window repeats the header, the caller's document is untouched."""
    doc = _table_document(rows=61, cols=4, cell_words=6)
    count = lambda text: len(text) // 4  # noqa: E731 - a cheap stand-in for the tokenizer

    split_doc, split = presplit_large_tables(doc, max_tokens=96, count_tokens=count)

    assert split == 1
    assert len(doc.tables) == 1 and doc.tables[0].data.num_rows == 61, "the original is not modified"
    assert len(split_doc.tables) > 4
    assert sum(t.data.num_rows - 1 for t in split_doc.tables) == 60, "every body row lands in one window"
    for table in split_doc.tables:
        rows = sorted({c.start_row_offset_idx for c in table.data.table_cells})
        assert rows == list(range(table.data.num_rows)), "row offsets are rebased per window"
        header = [c.text for c in table.data.table_cells if c.start_row_offset_idx == 0]
        assert header == ["col0", "col1", "col2", "col3"]
    markdown = split_doc.export_to_markdown()
    original = doc.export_to_markdown()
    for r in range(1, 61):
        assert markdown.count(f"w{r}_0_0 ") == 1 and f"w{r}_0_0 " in original
    assert markdown.count("| col0 ") == len(split_doc.tables)


def test_presplit_leaves_small_tables_and_tableless_documents_alone() -> None:
    doc = _table_document(rows=5, cols=3)
    assert presplit_large_tables(doc, 1024, lambda t: len(t) // 4) == (doc, 0)
    empty = DoclingDocument(name="empty")
    assert presplit_large_tables(empty, 1024, lambda t: len(t) // 4) == (empty, 0)


def test_a_table_that_took_a_minute_to_chunk_is_chunked_in_seconds(chunker: Any) -> None:
    """200 x 20 cells: 51 s with the HybridChunker alone (2026-11-03), 2.3 s for 100 x 10."""
    doc = _table_document(rows=200, cols=20)
    content = doc.export_to_markdown()

    started = time.monotonic()
    chunks = chunker.chunk_document_sync(content=content, title="t", source="s", metadata={}, docling_doc=doc)
    elapsed = time.monotonic() - started

    assert elapsed < 15, f"chunking took {elapsed:.1f}s"
    assert len(chunks) > 5
    assert all(chunk.token_count <= 2048 for chunk in chunks)
    text = "\n".join(chunk.content for chunk in chunks)
    for r in range(1, 200):
        assert f"w{r}_0_0" in text, f"row {r} is missing from the chunks"
    # the chunker serializes a table as "row header, column header = value" triplets, so the
    # column names of the header row are in every chunk of every window
    assert all("col1 = " in chunk.content for chunk in chunks), "every window carries the header"


def test_the_deadline_tokenizer_stops_the_chunker(chunker: Any) -> None:
    doc = _table_document(rows=30, cols=5)
    content = doc.export_to_markdown()
    kwargs = dict(content=content, title="t", source="s", metadata={}, docling_doc=doc)

    with pytest.raises(ChunkingDeadlineExceeded):
        chunker.chunk_document_sync(deadline=time.monotonic() - 1, **kwargs)
    assert chunker.chunk_document_sync(deadline=time.monotonic() + 60, **kwargs), "a live deadline chunks as usual"
    assert chunker_mod._chunking_deadline.get() is None, "the deadline does not leak out of the call"


# --------------------------------------------------------------------------- the safety net in the loop


class _SpinningChunker:
    """A chunker thread that ignores its deadline for ``seconds`` (what a hung docling loop would do)."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.calls: List[str] = []

    def chunk_document_sync(self, content: str, title: str, source: str, metadata=None, docling_doc=None, deadline=None):
        self.calls.append(title)
        if "slow" in title:
            time.sleep(self.seconds)
        return [DocumentChunk(content=f"{title} chunk", index=0, start_char=0, end_char=5, metadata={"source": source}, token_count=10)]

    async def chunk_document(self, **kwargs):
        return self.chunk_document_sync(**kwargs)


class _CooperativeChunker(_SpinningChunker):
    """A chunker thread that stops itself at the deadline, as the real one does."""

    def chunk_document_sync(self, content: str, title: str, source: str, metadata=None, docling_doc=None, deadline=None):
        self.calls.append(title)
        if "slow" in title:
            while time.monotonic() < deadline:
                time.sleep(0.01)
            raise ChunkingDeadlineExceeded("deadline")
        return [DocumentChunk(content=f"{title} chunk", index=0, start_char=0, end_char=5, metadata={"source": source}, token_count=10)]


class _SleepingAsyncChunker:
    """An old-style chunker without ``chunk_document_sync``: awaited on the loop under the same budget."""

    async def chunk_document(self, content: str, title: str, source: str, metadata=None, docling_doc=None):
        if "slow" in title:
            await asyncio.sleep(5)
        return [DocumentChunk(content=f"{title} chunk", index=0, start_char=0, end_char=5, metadata={"source": source}, token_count=10)]


@pytest.fixture
def chunk_budget(monkeypatch: pytest.MonkeyPatch):
    def _set(seconds: float) -> None:
        monkeypatch.setattr(ingest, "chunking_budget_seconds", lambda: seconds)

    return _set


def _assert_timeout_recorded(pipe: Any, name: str, step: str) -> None:
    (stat,) = pipe.stats_of(name)
    assert (stat.error_type, stat.classification) == ("timeout", "timeout")
    assert step in stat.error_message and "post-conversion budget" in stat.error_message
    assert name not in pipe.saved


async def test_a_chunker_that_spins_is_cut_off_by_the_budget_and_recorded_as_a_timeout(tmp_path: Path, chunk_budget, embed_budget) -> None:
    """The loop stops waiting at the budget, the other file is processed, nothing is retried."""
    chunk_budget(0.3)
    embed_budget(floor=5.0)
    paths = _write(tmp_path, slow="s", fine="f")
    pipe = _BudgetPipeline()
    pipe.chunker = _SpinningChunker(seconds=2.0)

    started = time.monotonic()
    await _run(pipe, list(paths.values()), max_concurrent=2)

    assert time.monotonic() - started < 1.8, "the loop did not wait for the spinning thread"
    _assert_timeout_recorded(pipe, "slow.txt", "chunking")
    assert pipe.saved["fine.txt"]["chunks"] == 1
    assert pipe.ingest_calls.count("slow.txt") == 1, "a post-conversion timeout is not retried within the run"
    (fine_stat,) = pipe.stats_of("fine.txt")
    assert fine_stat.success


async def test_a_cooperative_chunker_stops_itself_at_the_deadline(tmp_path: Path, chunk_budget, embed_budget) -> None:
    chunk_budget(0.3)
    embed_budget(floor=5.0)
    paths = _write(tmp_path, slow="s")
    pipe = _BudgetPipeline()
    pipe.chunker = _CooperativeChunker(seconds=0.0)

    await _run(pipe, list(paths.values()), max_concurrent=1)

    _assert_timeout_recorded(pipe, "slow.txt", "chunking")


async def test_an_old_style_async_chunker_is_under_the_same_budget(tmp_path: Path, chunk_budget, embed_budget) -> None:
    chunk_budget(0.3)
    embed_budget(floor=5.0)
    paths = _write(tmp_path, slow="s", fine="f")
    pipe = _BudgetPipeline()
    pipe.chunker = _SleepingAsyncChunker()

    started = time.monotonic()
    await _run(pipe, list(paths.values()), max_concurrent=2)

    assert time.monotonic() - started < 2
    _assert_timeout_recorded(pipe, "slow.txt", "chunking")
    assert pipe.saved["fine.txt"]["chunks"] == 1


async def test_ocr_repair_runs_under_the_budget_too(tmp_path: Path, monkeypatch, chunk_budget, embed_budget) -> None:
    chunk_budget(0.3)
    embed_budget(floor=5.0)
    paths = _write(tmp_path, slow="s")
    pipe = _BudgetPipeline(chunks=1)

    def spinning_repair(text: str) -> str:
        time.sleep(1.5)
        return text

    monkeypatch.setattr(ingest, "repair_ocr_text", spinning_repair)

    started = time.monotonic()
    await _run(pipe, list(paths.values()), max_concurrent=1)

    assert time.monotonic() - started < 1.4
    _assert_timeout_recorded(pipe, "slow.txt", "OCR repair")


async def test_the_event_loop_stays_responsive_while_a_document_chunks(tmp_path: Path, chunk_budget, embed_budget) -> None:
    """What /health needs: a ticker on the loop keeps ticking while the chunker thread runs."""
    chunk_budget(5.0)
    embed_budget(floor=5.0)
    paths = _write(tmp_path, slow="s")
    pipe = _BudgetPipeline()
    pipe.chunker = _SpinningChunker(seconds=0.6)
    ticks: List[float] = []

    async def ticker() -> None:
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.05)

    task = asyncio.ensure_future(ticker())
    try:
        await _run(pipe, list(paths.values()), max_concurrent=1)
    finally:
        task.cancel()
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert gaps and max(gaps) < 0.4, f"the loop stalled for {max(gaps):.2f}s"
    assert pipe.saved["slow.txt"]["chunks"] == 1


def test_chunking_budget_reads_environment_then_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Settings:
        ingest_chunk_budget_seconds = 120.0

    monkeypatch.setattr(ingest, "load_settings", lambda *a, **k: _Settings())
    monkeypatch.delenv("INGEST_CHUNK_BUDGET_SECONDS", raising=False)
    assert ingest.chunking_budget_seconds() == 120.0
    monkeypatch.setenv("INGEST_CHUNK_BUDGET_SECONDS", "45")
    assert ingest.chunking_budget_seconds() == 45.0

    def broken(*args: Any, **kwargs: Any):
        raise RuntimeError("no MONGODB_URI")

    monkeypatch.setattr(ingest, "load_settings", broken)
    monkeypatch.delenv("INGEST_CHUNK_BUDGET_SECONDS")
    assert ingest.chunking_budget_seconds() == ingest.DEFAULT_CHUNK_BUDGET_SECONDS

    from src.settings import Settings

    assert Settings.model_fields["ingest_chunk_budget_seconds"].default == ingest.DEFAULT_CHUNK_BUDGET_SECONDS
