"""On equal fused scores a text hit precedes a vector-only hit.

Reciprocal rank fusion scores rank ``i`` of the vector stage and rank ``i`` of the text
stage alike, and the fusions listed the vector stage first: a document only the text stage
found at rank 1 came second behind an unrelated vector hit. On a law firm's index the right
document was first for 21 % of exact-sentence queries before and 59 % after
(``docs/27-POST_INGESTION_VALIDATION.md``, section 7).
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.services.search_results import fused_order


def test_fused_order_prefers_text_on_a_tie_and_keeps_the_scores_first() -> None:
    scores = {"v0": 1 / 60, "v1": 1 / 61, "t0": 1 / 60, "both": 1 / 62 + 1 / 62, "t1": 1 / 61}
    assert fused_order(scores, {"t0", "t1", "both"}) == ["both", "t0", "v0", "t1", "v1"]
    assert fused_order(scores, set()) == ["both", "v0", "t0", "v1", "t1"], "without text ids: score, then insertion order"
    assert fused_order({}, {"t0"}) == []


class _AsyncCursor:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self._docs = [dict(d) for d in docs]

    def __aiter__(self):
        async def gen():
            for doc in self._docs:
                yield doc
        return gen()

    async def to_list(self, length: Any = None) -> List[Dict[str, Any]]:
        return list(self._docs)


def _doc(chunk_id: str, source: str) -> Dict[str, Any]:
    return {"chunk_id": chunk_id, "document_id": f"doc-{chunk_id}", "content": "Text " * 80, "similarity": 0.5, "metadata": {},
            "document_title": chunk_id, "document_source": source, "chunk_index": 0, "content_hash": f"hash-{chunk_id}"}


def test_hybrid_endpoint_puts_the_text_hit_before_the_vector_hit_of_the_same_rank(client: TestClient, mock_db) -> None:
    canned = {"vector": [_doc("v0", "a.pdf"), _doc("v1", "b.pdf")], "text": [_doc("t0", "c.pdf"), _doc("t1", "d.pdf")]}
    aggregate = mock_db.chunks_collection.aggregate
    aggregate.reset_mock()
    aggregate.side_effect = lambda pipeline: _AsyncCursor(canned["vector" if "$vectorSearch" in pipeline[0] else "text"])
    try:
        with patch("backend.routers.search.get_embedding", new=AsyncMock(return_value=[0.1] * 8)):
            response = client.post("/api/v1/search/hybrid", json={"query": "Kündigung des Mietvertrags wegen Eigenbedarfs", "match_count": 10})
    finally:
        aggregate.side_effect = None
    assert response.status_code == 200, response.text
    assert [r["chunk_id"] for r in response.json()["results"]] == ["t0", "v0", "t1", "v1"]


def test_chat_fusion_puts_the_text_hit_before_the_vector_hit_of_the_same_rank() -> None:
    federated = pytest.importorskip("backend.agent.federated_search")
    search = federated.FederatedSearch()
    content = "Sehr geehrte Damen und Herren, " * 20  # same length class for every hit
    results = [
        {"chunk_id": "v0", "search_type": "vector", "content": content, "similarity": 0.9},
        {"chunk_id": "v1", "search_type": "vector", "content": content, "similarity": 0.8},
        {"chunk_id": "t0", "search_type": "text", "content": content},
        {"chunk_id": "t1", "search_type": "text", "content": content},
    ]
    merged = search._fuse_vector_and_text(results, limit=10)
    assert [r["chunk_id"] for r in merged] == ["t0", "v0", "t1", "v1"]
