"""Oversized chunks must not cost a whole file its embeddings.

Found 2026-10-24 on the first full ingest: the HybridChunker emitted 8,000-token chunks for
Firmenbuch extracts, the llama.cpp embedder rejected every batch containing one with
HTTP 500 ("input (8483 tokens) is too large to process") and after three retries the
file was stored with zero chunks. Three layers now guard this: the chunker cuts
oversized chunks into windows, the embedder truncates inputs to the model's window, and
a failed batch falls back to one request per chunk.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

chunker = pytest.importorskip("src.ingestion.chunker")
embedder = pytest.importorskip("src.ingestion.embedder")
DocumentChunk = chunker.DocumentChunk


def _chunk(index: int, content: str, tokens=None) -> DocumentChunk:
    return DocumentChunk(content=content, index=index, start_char=0, end_char=len(content),
                         metadata={"source": "x.pdf", "total_chunks": 1}, token_count=tokens)


# ---------------------------------------------------------------------------
# Chunker: oversized chunks are cut into windows
# ---------------------------------------------------------------------------


def test_normal_chunks_are_returned_untouched() -> None:
    chunks = [_chunk(0, "a" * 100, tokens=30), _chunk(1, "b" * 100, tokens=40)]
    assert chunker.split_oversized_chunks(chunks, 512) is chunks


def test_oversized_chunk_is_split_into_overlapping_windows() -> None:
    lines = [f"Zeile {i} " + "x" * 60 for i in range(400)]
    big = "\n".join(lines)  # ~27,000 chars, ~9,000 tokens at 3 chars/token
    chunks = [_chunk(0, "intro", tokens=2), _chunk(1, big, tokens=9000), _chunk(2, "ende", tokens=1)]
    out = chunker.split_oversized_chunks(chunks, 512)
    assert len(out) > 5
    assert [c.index for c in out] == list(range(len(out)))
    assert all(c.metadata["total_chunks"] == len(out) for c in out)
    windows = [c for c in out if "split_part" in c.metadata]
    assert windows[0].metadata["split_parts"] == len(windows)
    assert all(len(c.content) <= chunker.WINDOW_FACTOR * 512 * chunker.CHARS_PER_TOKEN for c in windows)
    assert all(c.token_count is not None and c.token_count <= chunker.OVERSIZE_FACTOR * 512 for c in windows)
    # every line of the original survives in some window
    assert all(any(line in c.content for c in windows) for line in lines[::50])
    # the overlap: the second window starts before the first one ended
    assert windows[1].content[:20] in windows[0].content or windows[1].content.split("\n")[0] in windows[0].content
    assert out[0].content == "intro" and out[-1].content == "ende"


def test_recorded_token_count_decides_and_the_counter_sizes_the_pieces() -> None:
    small = [_chunk(0, "abc " * 3000, tokens=200)]  # the chunker's own count wins over the length
    assert chunker.split_oversized_chunks(small, 512, count_tokens=lambda t: len(t)) is small
    big = [_chunk(0, "abc " * 3000, tokens=5000)]
    cut = chunker.split_oversized_chunks(big, 512, count_tokens=lambda t: len(t) // 4)
    assert len(cut) > 1
    assert all(c.token_count == len(c.content) // 4 for c in cut)  # pieces counted with the counter


# ---------------------------------------------------------------------------
# Embedder: window truncation and per-chunk fallback
# ---------------------------------------------------------------------------


def test_truncate_to_window() -> None:
    assert embedder.truncate_to_window("abc", 10) == "abc"
    assert len(embedder.truncate_to_window("x" * 100000, 2048)) == 2048 * embedder.CHARS_PER_TOKEN


@pytest.fixture
def generator(monkeypatch):
    """An EmbeddingGenerator without network: the OpenAI call is stubbed."""
    monkeypatch.setenv("EMBEDDING_API_KEY", "none")
    monkeypatch.delenv("EMBEDDING_MAX_TOKENS", raising=False)
    gen = embedder.EmbeddingGenerator.__new__(embedder.EmbeddingGenerator)
    gen.model = "embeddinggemma-2"
    gen.provider = "openai"
    gen.task_type = None
    gen.batch_size = 2
    gen.model_configs = {"embeddinggemma-2": {"dimensions": 768, "max_tokens": 2048, "provider": "openai"}}
    gen.config = {"dimensions": 768, "max_tokens": 2048}
    return gen


def test_max_input_tokens_prefers_env_then_model_table(generator, monkeypatch) -> None:
    assert generator.max_input_tokens() == 2048
    generator.model = "some-unknown-local-model"
    assert generator.max_input_tokens() == embedder.DEFAULT_MAX_INPUT_TOKENS
    monkeypatch.setenv("EMBEDDING_MAX_TOKENS", "512")
    assert generator.max_input_tokens() == 512
    monkeypatch.setenv("EMBEDDING_MAX_TOKENS", "nope")
    assert generator.max_input_tokens() == embedder.DEFAULT_MAX_INPUT_TOKENS


async def test_batch_inputs_are_truncated_before_the_call(generator, monkeypatch) -> None:
    seen = {}

    async def fake_openai(texts):
        seen["texts"] = texts
        return [[0.0] * 768 for _ in texts], None

    monkeypatch.setattr(generator, "_try_openai_embeddings", fake_openai)
    monkeypatch.setattr(generator, "_get_provider_for_model", lambda: "openai")
    monkeypatch.setattr(generator, "_sanitize_text", lambda t: t)
    await generator.generate_embeddings_batch(["short", "y" * 50000])
    assert seen["texts"][0] == "short"
    assert len(seen["texts"][1]) == 2048 * embedder.CHARS_PER_TOKEN


async def test_failed_batch_falls_back_to_single_chunks_and_drops_only_the_bad_one(generator, monkeypatch) -> None:
    calls = []

    async def fake_batch(texts, task_type=None):
        calls.append(list(texts))
        if len(texts) > 1:
            raise RuntimeError("HTTP 500 input is too large to process")
        if texts[0] == "BAD":
            raise RuntimeError("HTTP 500 input is too large to process")
        return [[1.0] * 768]

    monkeypatch.setattr(generator, "generate_embeddings_batch", fake_batch)
    chunks = [_chunk(0, "ok one"), _chunk(1, "BAD"), _chunk(2, "ok three")]
    out = await generator.embed_chunks(chunks)
    assert [c.content for c in out] == ["ok one", "ok three"]
    assert all(c.embedding == [1.0] * 768 for c in out)
    assert calls[0] == ["ok one", "BAD"]  # the batch (batch_size 2) failed first
    assert ["BAD"] in calls and ["ok three"] in calls


# ---------------------------------------------------------------------------
# API queue build: registry entries are paginated, not fetched with limit=
# ---------------------------------------------------------------------------


async def test_registry_entries_are_read_page_by_page() -> None:
    router = pytest.importorskip("backend.routers.ingestion")
    pages = {1: [SimpleNamespace(model_dump=lambda: {"file_path": "/a", "classification": "ok"})] * 2,
             2: [SimpleNamespace(model_dump=lambda: {"file_path": "/b", "classification": "timeout"})]}
    seen = []

    class _Service:
        async def list_files(self, profile_key, page, page_size):
            seen.append((profile_key, page, page_size))
            return pages.get(page, []), 3

    entries = await router._all_registry_entries(_Service(), "diejuristin", page_size=2)
    assert [e["file_path"] for e in entries] == ["/a", "/a", "/b"]
    assert seen == [("diejuristin", 1, 2), ("diejuristin", 2, 2)]
