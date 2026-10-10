"""Tests for the embedding validation checks (service, router, CLI).

A fake embedding server is provided through ``httpx.MockTransport`` so no
network, model or MongoDB is needed. Vectors are deterministic functions of
the first characters of the (prefix-stripped) input, which makes the
pseudo-query retrieval check hit top-1 for every chunk.
"""
from __future__ import annotations

import json
import zlib
from typing import Any, Callable, Dict, List
from unittest.mock import AsyncMock, MagicMock

import httpx
import numpy as np
import pytest

from backend.services import embedding_checks as ec
from backend.services.embedding_checks import (
    CheckOptions,
    EmbeddingCheckRunner,
    ProviderSpec,
    SampleInfo,
    detect_task_prefixes,
    topk_overlap,
    truncate_renorm,
)

DIM = 768
QUERY_PREFIX, DOC_PREFIX = detect_task_prefixes("embeddinggemma-2")
MEDIA_MARKER = "<__media_test__>"


def fake_vector(text: str, dim: int = DIM) -> List[float]:
    """Deterministic unit vector keyed on the first 30 chars of the bare text."""
    bare = text
    for prefix in (QUERY_PREFIX, DOC_PREFIX, "search_query: ", "search_document: "):
        if bare.startswith(prefix):
            bare = bare[len(prefix):]
    rng = np.random.default_rng(zlib.crc32(bare[:30].encode("utf-8")))
    vec = rng.standard_normal(dim)
    return (vec / np.linalg.norm(vec)).tolist()


def sample_texts(n: int = 20) -> List[str]:
    return [f"Document {i} discusses topic number {i}. More details follow about item {i} in depth." for i in range(n)]


class FakeServer:
    """Records requests and answers like a llama.cpp / Ollama embedding server."""

    def __init__(self, *, ollama_batch: bool = True, ignore_images: bool = False, fail: bool = False):
        self.requests: List[httpx.Request] = []
        self.ollama_batch = ollama_batch
        self.ignore_images = ignore_images
        self.fail = fail

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.fail and path.endswith("/embeddings"):
            return httpx.Response(500, text="boom")
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/props":
            return httpx.Response(
                200,
                json={
                    "build_info": "b11541-test",
                    "model_alias": "embeddinggemma-2",
                    "modalities": {"vision": True, "audio": False},
                    "media_marker": MEDIA_MARKER,
                    "default_generation_settings": {"n_ctx": 8192},
                    "total_slots": 4,
                },
            )
        if path == "/v1/embeddings":
            body = json.loads(request.content)
            inputs = body["input"]
            if isinstance(inputs, dict):  # multimodal prompt object
                vec = fake_vector(MEDIA_MARKER) if self.ignore_images else fake_vector("IMAGE")
                return httpx.Response(200, json={"data": [{"index": 0, "embedding": vec}], "usage": {"prompt_tokens": 468}})
            if isinstance(inputs, str):
                inputs = [inputs]
            data = [{"index": i, "embedding": fake_vector(t)} for i, t in enumerate(inputs)]
            return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": sum(len(t) // 4 for t in inputs)}})
        if path == "/api/embed":
            if not self.ollama_batch:
                return httpx.Response(404, text="not found")
            body = json.loads(request.content)
            return httpx.Response(200, json={"embeddings": [fake_vector(t) for t in body["input"]], "prompt_eval_count": 10})
        if path == "/api/embeddings":
            body = json.loads(request.content)
            return httpx.Response(200, json={"embedding": fake_vector(body["prompt"])})
        if path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["embedding"], "model_info": {"gemma.context_length": 2048}})
        return httpx.Response(404, text=f"unhandled {path}")


@pytest.fixture
def fake_server(monkeypatch) -> Callable[..., FakeServer]:
    """Install a FakeServer as the runner's HTTP transport."""

    def _install(**kwargs: Any) -> FakeServer:
        server = FakeServer(**kwargs)
        monkeypatch.setattr(
            ec, "create_http_client", lambda timeout=600.0: httpx.AsyncClient(transport=httpx.MockTransport(server))
        )
        return server

    return _install


def _options(**overrides: Any) -> CheckOptions:
    base = dict(sample_size=50, batch_sizes=[1, 8], truncate_dims=[512, 256], latency_runs=3, pseudo_query_every=2)
    base.update(overrides)
    return CheckOptions(**base)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_detect_task_prefixes():
    assert detect_task_prefixes("embeddinggemma-2:270m") == (QUERY_PREFIX, DOC_PREFIX)
    assert detect_task_prefixes("nomic-embed-text") == ("search_query: ", "search_document: ")
    assert detect_task_prefixes("text-embedding-3-large") == ("", "")


def test_truncate_renorm_and_topk_overlap():
    vectors = [fake_vector(t) for t in sample_texts(10)]
    truncated = truncate_renorm(vectors, 256)
    assert len(truncated[0]) == 256
    assert all(abs(np.linalg.norm(v) - 1.0) < 1e-5 for v in truncated)
    assert topk_overlap(vectors, vectors, 5) == 1.0
    assert 0.0 <= topk_overlap(vectors, truncated, 5) <= 1.0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def test_runner_llamacpp_full_suite(fake_server):
    server = fake_server()
    texts = sample_texts(20)
    reference = [fake_vector(t) for t in texts]
    spec = ProviderSpec(provider_type="llamacpp", model="embeddinggemma-2", base_url="http://embedder:8080", expected_dimension=DIM)
    sample = SampleInfo(source="index", count=len(texts), avg_chars=80.0, reference_model="text-embedding-3-large")

    result = await EmbeddingCheckRunner().run([spec], texts, _options(), sample, reference_vectors=reference)

    assert result.kind == "checks" and len(result.results) == 1
    r = result.results[0]
    assert r.success, r.error
    assert r.query_prefix == QUERY_PREFIX and r.document_prefix == DOC_PREFIX
    assert r.capabilities and r.capabilities.modalities["vision"] and r.capabilities.build == "b11541-test"
    assert r.latency and r.latency.runs == 3 and r.latency.p50_ms >= 0
    assert [p.batch_size for p in r.throughput] == [1, 8]
    assert r.throughput[0].texts == 20 and r.throughput[1].texts == 20
    assert all(p.token_source == "server" and p.tokens > 0 for p in r.throughput)
    assert r.vectors and r.vectors.dimension == DIM and r.vectors.normalized and r.vectors.dimension_matches is True
    assert [t.dim for t in r.truncation] == [512, 256]
    assert all(0.0 <= t.topk_overlap <= 1.0 for t in r.truncation)
    assert r.retrieval and r.retrieval.queries == 10 and r.retrieval.top1_rate == 1.0
    assert r.image and r.image.supported and r.image.tokens == 468
    assert r.agreement_with_index == 1.0
    assert r.hardware and r.hardware.cpu_count

    # Task prefixes were applied to documents and queries respectively.
    doc_inputs = [
        t
        for req in server.requests
        if req.url.path == "/v1/embeddings" and isinstance(json.loads(req.content)["input"], list)
        for t in json.loads(req.content)["input"]
        if t.startswith("Document") or t.startswith(DOC_PREFIX) or t.startswith(QUERY_PREFIX)
    ]
    assert doc_inputs and all(t.startswith((DOC_PREFIX, QUERY_PREFIX)) for t in doc_inputs)


async def test_runner_truncate_dim_applies_client_side(fake_server):
    fake_server()
    texts = sample_texts(12)
    spec = ProviderSpec(provider_type="llamacpp", model="embeddinggemma-2", base_url="http://embedder:8080", truncate_dim=256)
    result = await EmbeddingCheckRunner().run(
        [spec], texts, _options(truncate_dims=[], check_images=False), SampleInfo(source="file", count=12, avg_chars=80.0)
    )
    r = result.results[0]
    assert r.success, r.error
    assert r.vectors and r.vectors.dimension == 256 and r.vectors.normalized and r.vectors.dimension_matches is True
    assert r.truncation == []


async def test_runner_ollama_legacy_fallback(fake_server):
    server = fake_server(ollama_batch=False)
    texts = sample_texts(12)
    spec = ProviderSpec(provider_type="ollama", model="nomic-embed-text", base_url="http://ollama:11434")
    result = await EmbeddingCheckRunner().run([spec], texts, _options(check_images=False), SampleInfo(source="file", count=12, avg_chars=80.0))
    r = result.results[0]
    assert r.success, r.error
    assert any(req.url.path == "/api/embeddings" for req in server.requests)
    assert all(p.token_source == "estimate" for p in r.throughput)
    assert r.image is None
    assert r.capabilities and r.capabilities.context_length == 2048


async def test_runner_reports_provider_error(fake_server):
    fake_server(fail=True)
    spec = ProviderSpec(provider_type="vllm", model="x", base_url="http://vllm:8000/v1")
    result = await EmbeddingCheckRunner().run([spec], sample_texts(12), _options(), SampleInfo(source="file", count=12, avg_chars=80.0))
    r = result.results[0]
    assert not r.success
    assert "500" in (r.error or "")


async def test_runner_cross_provider_agreement(fake_server):
    fake_server()
    texts = sample_texts(15)
    specs = [
        ProviderSpec(provider_type="llamacpp", model="embeddinggemma-2", base_url="http://embedder:8080", name="gemma-cpu"),
        ProviderSpec(provider_type="custom", model="embeddinggemma-2", base_url="http://embedder-gpu:8080/v1", name="gemma-gpu"),
    ]
    result = await EmbeddingCheckRunner().run(specs, texts, _options(check_images=False), SampleInfo(source="file", count=15, avg_chars=80.0))
    assert all(r.success for r in result.results)
    assert len(result.agreement) == 1
    assert result.agreement[0].a == "gemma-cpu" and result.agreement[0].b == "gemma-gpu"
    assert result.agreement[0].topk_overlap == 1.0


async def test_image_probe_detects_ignored_image(fake_server):
    fake_server(ignore_images=True)
    spec = ProviderSpec(provider_type="llamacpp", model="embeddinggemma-2", base_url="http://embedder:8080")
    result = await EmbeddingCheckRunner().run([spec], sample_texts(12), _options(), SampleInfo(source="file", count=12, avg_chars=80.0))
    r = result.results[0]
    assert r.success
    assert r.image and r.image.supported is False and r.image.vector_changed is False
    assert any("ignored" in w for w in r.warnings)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class _Cursor:
    """Minimal async cursor over a fixed document list."""

    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def limit(self, n: int) -> "_Cursor":
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def _index_docs(n: int = 12) -> List[Dict[str, Any]]:
    return [
        {"_id": f"c{i}", "content": t, "embedding": fake_vector(t), "metadata": {"embedding_model": "text-embedding-3-large"}}
        for i, t in enumerate(sample_texts(n))
    ]


def test_checks_endpoint_samples_index(client, mock_db, fake_server):
    fake_server()
    docs = _index_docs()
    mock_db.chunks_collection.find = MagicMock(side_effect=lambda *a, **k: _Cursor(list(docs)))
    mock_db._current_chunks_collection = "chunks"

    payload = {
        "providers": [{"provider_type": "llamacpp", "model": "embeddinggemma-2", "base_url": "http://embedder:8080"}],
        "options": {"sample_size": 50, "batch_sizes": [1, 8], "truncate_dims": [256], "latency_runs": 2, "pseudo_query_every": 3},
        "sample": {"source": "index", "limit": 50},
    }
    response = client.post("/api/v1/benchmark/checks", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "checks"
    assert body["sample"]["source"] == "index"
    assert body["sample"]["collection"] == "chunks"
    assert body["sample"]["reference_model"] == "text-embedding-3-large"
    assert body["sample"]["reference_dimension"] == DIM
    result = body["results"][0]
    assert result["success"], result["error"]
    assert result["agreement_with_index"] == 1.0
    assert result["vectors"]["dimension"] == DIM
    mock_db.db["benchmark_results"].insert_one.assert_awaited()
    stored = mock_db.db["benchmark_results"].insert_one.await_args.args[0]
    assert stored["kind"] == "checks" and "_id" in stored


def test_checks_endpoint_requires_sample_content(client, mock_db, fake_server):
    fake_server()
    mock_db.chunks_collection.find = MagicMock(side_effect=lambda *a, **k: _Cursor([]))
    payload = {"providers": [{"provider_type": "llamacpp", "model": "embeddinggemma-2", "base_url": "http://embedder:8080"}]}
    response = client.post("/api/v1/benchmark/checks", json=payload)
    assert response.status_code == 400
    assert "No chunks" in response.json()["message"]


def test_checks_endpoint_file_source(client, fake_server):
    fake_server()
    import base64

    text = "\n\n".join(sample_texts(12))
    payload = {
        "providers": [{"provider_type": "llamacpp", "model": "embeddinggemma-2", "base_url": "http://embedder:8080"}],
        "options": {"batch_sizes": [8], "truncate_dims": [], "latency_runs": 1, "check_images": False},
        "sample": {"source": "file", "file_name": "notes.txt", "file_content": base64.b64encode(text.encode()).decode(), "chunk_config": {"chunk_size": 120, "chunk_overlap": 0, "max_tokens": 512}},
    }
    response = client.post("/api/v1/benchmark/checks", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sample"]["source"] == "file" and body["sample"]["file_name"] == "notes.txt"
    assert body["results"][0]["success"]
    assert body["results"][0]["agreement_with_index"] is None


def test_checks_history_endpoint(client, mock_db):
    response = client.get("/api/v1/benchmark/checks/results?limit=5")
    assert response.status_code == 200
    assert response.json() == {"results": [], "total": 0}
    query = mock_db.db["benchmark_results"].find.call_args.args[0]
    assert query == {"kind": "checks"}


def test_providers_endpoint_lists_llamacpp(client, monkeypatch):
    """Discovery reports the llama.cpp server when its health endpoint answers."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "embedder" and request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.host == "embedder" and request.url.path == "/props":
            return httpx.Response(200, json={"model_alias": "embeddinggemma-2", "modalities": {"vision": True}})
        return httpx.Response(503, text="down")

    real_client = httpx.AsyncClient

    def patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("backend.services.embedding_benchmark.httpx.AsyncClient", patched)
    response = client.get("/api/v1/benchmark/providers")
    assert response.status_code == 200
    llamacpp = response.json()["llamacpp"]
    assert llamacpp["available"] is True
    assert llamacpp["url"] == "http://embedder:8080"
    assert llamacpp["models"][0] == {"id": "embeddinggemma-2", "name": "embeddinggemma-2", "dimension": 768, "multimodal": True}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_bench_embeddings(monkeypatch, fake_server):
    from backend.cli.main import HAS_TYPER

    if not HAS_TYPER:
        pytest.skip("typer not installed")
    from typer.testing import CliRunner

    from backend.cli.main import app
    import backend.cli.bench_commands as bench

    fake_server()
    texts = sample_texts(12)

    async def loader(uri: str, database: str, collection: str, limit: int):
        assert (database, collection, limit) == ("rag_parhelion", "chunks", 12)
        return texts, [fake_vector(t) for t in texts], "text-embedding-3-large"

    monkeypatch.setattr(bench, "SAMPLE_LOADER", loader)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--format", "json", "bench", "embeddings",
            "--provider", "llamacpp", "--model", "embeddinggemma-2", "--base-url", "http://embedder:8080",
            "--database", "rag_parhelion", "--sample", "12", "--batch-sizes", "8", "--truncate-dims", "256",
            "--latency-runs", "1", "--no-images",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["sample"]["reference_model"] == "text-embedding-3-large"
    assert payload["results"][0]["success"] and payload["results"][0]["agreement_with_index"] == 1.0
