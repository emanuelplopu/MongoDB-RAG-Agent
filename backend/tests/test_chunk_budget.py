"""The chunker budget is a setting, 1024 tokens by default (item 7 of the 2026-10-26 fixes).

``max_tokens`` was hard-coded to 512 in the chunker, the pipeline config, the start request
and both runners. It now comes from ``CHUNK_MAX_TOKENS`` (default 1024, EmbeddingGemma 2's
2,048-token window allows it) and contextualised chunks stay below ``EMBEDDING_MAX_TOKENS``.
"""

from __future__ import annotations

import pytest

chunker = pytest.importorskip("src.ingestion.chunker")
from backend.models.schemas import IngestionStartRequest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHUNK_MAX_TOKENS", raising=False)
    monkeypatch.delenv("EMBEDDING_MAX_TOKENS", raising=False)


def test_defaults_are_1024_and_2048_on_every_surface() -> None:
    from backend.core.config import BackendSettings
    from src.ingestion.ingest import IngestionConfig
    from src.settings import Settings

    assert chunker.chunk_max_tokens_default() == 1024
    assert chunker.embed_max_tokens_default() == 2048
    assert chunker.ChunkingConfig().max_tokens == 1024
    assert chunker.ChunkingConfig().embed_max_tokens == 2048
    assert IngestionConfig().max_tokens == 1024
    for cls in (Settings, BackendSettings):
        assert cls.model_fields["chunk_max_tokens"].default == 1024
        assert cls.model_fields["embedding_max_tokens"].default == 2048


def test_environment_overrides_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHUNK_MAX_TOKENS", "768")
    monkeypatch.setenv("EMBEDDING_MAX_TOKENS", "4096")
    assert chunker.chunk_max_tokens_default() == 768
    assert chunker.ChunkingConfig().max_tokens == 768
    assert chunker.ChunkingConfig().embed_max_tokens == 4096
    monkeypatch.setenv("CHUNK_MAX_TOKENS", "not-a-number")
    assert chunker.chunk_max_tokens_default() == 1024


def test_embedder_limit_must_cover_the_chunk_budget() -> None:
    with pytest.raises(ValueError):
        chunker.ChunkingConfig(max_tokens=2048, embed_max_tokens=1024)


def test_start_request_leaves_the_budget_to_the_setting() -> None:
    assert IngestionStartRequest().max_tokens is None
    assert IngestionStartRequest(max_tokens=512).max_tokens == 512


def _chunk(text: str, tokens: int) -> chunker.DocumentChunk:
    return chunker.DocumentChunk(content=text, index=0, start_char=0, end_char=len(text), metadata={"source": "x"},
                                 token_count=tokens)


def test_oversized_chunks_are_cut_below_the_embedder_limit() -> None:
    text = "\n".join(f"Zeile {i} der langen Firmenbuch-Tabelle mit Werten" for i in range(200))
    counter = lambda t: len(t) // 3  # noqa: E731 - the test's token model
    assert 2048 < counter(text) < 4096
    chunks = [_chunk("short", 10), _chunk(text, counter(text))]

    kept = chunker.split_oversized_chunks(list(chunks), max_tokens=1024, count_tokens=counter)
    assert len(kept) == 2, "4 x 1024 = 4096 tokens is the default oversize threshold; the table fits"

    cut = chunker.split_oversized_chunks(list(chunks), max_tokens=1024, count_tokens=counter, limit_tokens=2048)
    assert len(cut) > 2
    assert all(c.token_count <= 2048 for c in cut)
    assert [c.index for c in cut] == list(range(len(cut)))
    assert cut[0].content == "short"
    assert cut[1].metadata["split_parts"] == len(cut) - 1
