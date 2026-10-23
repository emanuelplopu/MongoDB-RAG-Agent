"""Embedding validation checks.

Runs the validation suite used to qualify an embedding provider for RecallHub
against real text samples (normally chunks pulled from the live index):

- query latency (cold + warm p50/p95)
- document throughput vs. batch size (texts/s and tokens/s)
- vector dimension and L2 normalisation
- Matryoshka (MRL) truncation stability: top-k neighbour overlap of
  truncated-and-renormalised vectors vs. full vectors
- pseudo-query retrieval sanity (first sentence of a chunk must find its chunk)
- image-embedding probe for multimodal servers (llama.cpp only)
- ranking agreement between providers and against the vectors already stored
  in the index (i.e. "how differently would the new model rank our data?")
- hardware snapshot of the host running the checks (CPU, RAM, NVIDIA GPUs)

Supported provider types: ``openai``, ``vllm``/``custom`` (OpenAI-compatible),
``llamacpp`` (OpenAI-compatible plus native ``/props`` and multimodal
``/embeddings``), and ``ollama`` (``/api/embed`` with legacy fallback).

The runner is transport-agnostic: tests inject an ``httpx.AsyncClient`` with a
``MockTransport`` through :func:`create_http_client`.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import shutil
import socket
import statistics
import struct
import time
import uuid
import zlib
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import httpx
import numpy as np
import psutil
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task prefixes
# ---------------------------------------------------------------------------

# The model-family prefix table and its detector live in
# backend/services/query_embedding.py, where the production query path applies
# the query prefix; re-exported here for the checks, the benchmark and their tests.
from backend.services.query_embedding import KNOWN_TASK_PREFIXES, detect_task_prefixes  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Typed contracts
# ---------------------------------------------------------------------------


class ProviderSpec(BaseModel):
    """Embedding provider under test."""

    provider_type: str = Field(description="openai | vllm | custom | llamacpp | ollama")
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    name: Optional[str] = None
    query_prefix: str = ""
    document_prefix: str = ""
    truncate_dim: Optional[int] = Field(default=None, ge=16, description="Client-side MRL truncation target")
    expected_dimension: Optional[int] = None

    @property
    def display_name(self) -> str:
        return self.name or f"{self.provider_type}/{self.model}"


class CheckOptions(BaseModel):
    """Tunables for a check run."""

    sample_size: int = Field(default=200, ge=10, le=2000)
    batch_sizes: List[int] = Field(default_factory=lambda: [1, 8, 32, 64])
    single_batch_limit: int = Field(default=32, ge=1, description="Max texts embedded when batch size is 1")
    truncate_dims: List[int] = Field(default_factory=lambda: [512, 256])
    latency_runs: int = Field(default=10, ge=1, le=100)
    top_k: int = Field(default=10, ge=1, le=100)
    pseudo_query_every: int = Field(default=10, ge=1)
    check_images: bool = True
    query_text: str = "Which notice period applies to the lease agreement?"


class LatencyStats(BaseModel):
    cold_ms: float
    p50_ms: float
    p95_ms: float
    runs: int


class ThroughputPoint(BaseModel):
    batch_size: int
    texts: int
    wall_ms: float
    texts_per_s: float
    tokens: int
    tokens_per_s: float
    token_source: str = Field(description="server (usage.prompt_tokens) | estimate (chars/4)")


class VectorStats(BaseModel):
    dimension: int
    expected_dimension: Optional[int] = None
    dimension_matches: Optional[bool] = None
    norm_mean: float
    norm_min: float
    norm_max: float
    normalized: bool


class TruncationCheck(BaseModel):
    dim: int
    topk_overlap: float = Field(description="Mean top-k neighbour overlap vs. full-dimension vectors (0-1)")


class RetrievalSanity(BaseModel):
    queries: int
    top1_hits: int
    top5_hits: int
    top1_rate: float
    top5_rate: float


class ImageProbe(BaseModel):
    supported: bool
    tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    vector_changed: Optional[bool] = None
    error: Optional[str] = None


class ServerCapabilities(BaseModel):
    build: Optional[str] = None
    model_alias: Optional[str] = None
    modalities: Dict[str, bool] = Field(default_factory=dict)
    context_length: Optional[int] = None
    slots: Optional[int] = None


class GpuInfo(BaseModel):
    name: str
    memory_total_mb: Optional[int] = None
    memory_used_mb: Optional[int] = None
    driver_version: Optional[str] = None
    utilization_percent: Optional[int] = None


class HardwareInfo(BaseModel):
    hostname: str
    cpu_count: Optional[int] = None
    ram_gb: Optional[float] = None
    gpus: List[GpuInfo] = Field(default_factory=list)
    note: str = (
        "Measured on the host running the checks (backend container or CLI), "
        "which is not necessarily the embedding server."
    )


class ProviderCheckResult(BaseModel):
    provider: str
    model: str
    provider_type: str
    base_url: Optional[str] = None
    success: bool = False
    error: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    query_prefix: str = ""
    document_prefix: str = ""
    truncate_dim: Optional[int] = None
    capabilities: Optional[ServerCapabilities] = None
    hardware: Optional[HardwareInfo] = None
    latency: Optional[LatencyStats] = None
    throughput: List[ThroughputPoint] = Field(default_factory=list)
    vectors: Optional[VectorStats] = None
    truncation: List[TruncationCheck] = Field(default_factory=list)
    retrieval: Optional[RetrievalSanity] = None
    image: Optional[ImageProbe] = None
    agreement_with_index: Optional[float] = Field(
        default=None, description="Top-k overlap vs. vectors currently stored in the index"
    )
    total_ms: float = 0.0


class AgreementPair(BaseModel):
    a: str
    b: str
    topk_overlap: float


class SampleInfo(BaseModel):
    source: str = Field(description="index | file")
    count: int
    avg_chars: float
    database: Optional[str] = None
    collection: Optional[str] = None
    file_name: Optional[str] = None
    reference_model: Optional[str] = Field(default=None, description="Model of the stored index vectors, if used")
    reference_dimension: Optional[int] = None


class EmbeddingChecksResult(BaseModel):
    id: str
    timestamp: datetime
    kind: str = "checks"
    sample: SampleInfo
    options: CheckOptions
    results: List[ProviderCheckResult]
    agreement: List[AgreementPair] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Vector math helpers
# ---------------------------------------------------------------------------


def _as_unit_matrix(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _topk_neighbors(matrix: np.ndarray, k: int) -> List[set]:
    """Top-k cosine neighbours of every row (self excluded) for a unit matrix."""
    sims = matrix @ matrix.T
    np.fill_diagonal(sims, -2.0)
    k = min(k, matrix.shape[0] - 1)
    if k <= 0:
        return [set() for _ in range(matrix.shape[0])]
    idx = np.argpartition(-sims, k - 1, axis=1)[:, :k]
    return [set(row.tolist()) for row in idx]


def _mean_overlap(a: List[set], b: List[set], k: int) -> float:
    if not a or not b or k <= 0:
        return 0.0
    k_eff = min(k, max(len(a[0]), 1))
    return float(statistics.mean(len(x & y) / k_eff for x, y in zip(a, b)))


def topk_overlap(vectors_a: Sequence[Sequence[float]], vectors_b: Sequence[Sequence[float]], k: int) -> float:
    """Mean top-k neighbour overlap between two embeddings of the same texts."""
    if len(vectors_a) != len(vectors_b) or len(vectors_a) < 3:
        return 0.0
    return _mean_overlap(
        _topk_neighbors(_as_unit_matrix(vectors_a), k),
        _topk_neighbors(_as_unit_matrix(vectors_b), k),
        k,
    )


def truncate_renorm(vectors: Sequence[Sequence[float]], dim: int) -> List[List[float]]:
    """Truncate Matryoshka vectors to ``dim`` and re-normalise to unit length."""
    matrix = np.asarray(vectors, dtype=np.float32)[:, :dim]
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).tolist()


def _first_sentence(text: str, max_chars: int = 200) -> str:
    sentence = re.split(r"(?<=[.!?])\s+|\n", text.strip(), maxsplit=1)[0]
    return sentence[:max_chars]


def _tiny_png(size: int = 64) -> bytes:
    """Build a small RGB gradient PNG without any imaging dependency."""
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")
        for x in range(size):
            row += bytes(((x * 4) & 255, (y * 4) & 255, ((x + y) * 2) & 255))
        rows.append(bytes(row))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


def create_http_client(timeout: float = 600.0) -> httpx.AsyncClient:
    """Create the HTTP client used by the check runner (patched in tests)."""
    return httpx.AsyncClient(timeout=timeout)


class EmbedResponse(BaseModel):
    vectors: List[List[float]]
    tokens: Optional[int] = None
    elapsed_ms: float


class EmbeddingHTTPClient:
    """Minimal async client for the embedding APIs the checks understand."""

    OPENAI_COMPATIBLE = {"openai", "vllm", "custom", "llamacpp"}

    def __init__(self, spec: ProviderSpec, http: httpx.AsyncClient):
        self.spec = spec
        self.http = http
        self.root, self.v1 = self._resolve_urls(spec)

    @staticmethod
    def _resolve_urls(spec: ProviderSpec) -> Tuple[str, str]:
        base = (spec.base_url or "").strip().rstrip("/")
        if not base:
            if spec.provider_type == "openai":
                base = "https://api.openai.com/v1"
            elif spec.provider_type == "ollama":
                base = "http://host.docker.internal:11434"
            elif spec.provider_type == "llamacpp":
                from backend.core.config import settings

                base = settings.embedder_base_url.rstrip("/")
            else:
                raise ValueError(f"base_url is required for provider type '{spec.provider_type}'")
        if base.endswith("/v1"):
            return base[:-3], base
        return base, f"{base}/v1"

    def _headers(self) -> Dict[str, str]:
        key = self.spec.api_key
        if not key and self.spec.provider_type == "openai":
            from backend.core.config import settings

            key = settings.embedding_api_key
        return {"Authorization": f"Bearer {key}"} if key else {}

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code >= 400:
            snippet = response.text[:300].replace("\n", " ")
            raise RuntimeError(f"{response.request.method} {response.request.url} -> {response.status_code}: {snippet}")

    async def embed(self, texts: Sequence[str], prefix: str = "") -> EmbedResponse:
        """Embed ``texts`` (prefix applied) and return vectors plus server token count."""
        inputs = [f"{prefix}{t}" for t in texts] if prefix else list(texts)
        start = time.perf_counter()
        if self.spec.provider_type in self.OPENAI_COMPATIBLE:
            vectors, tokens = await self._embed_openai_compatible(inputs)
        elif self.spec.provider_type == "ollama":
            vectors, tokens = await self._embed_ollama(inputs)
        else:
            raise ValueError(f"Provider type '{self.spec.provider_type}' is not supported by embedding checks")
        elapsed_ms = (time.perf_counter() - start) * 1000
        if len(vectors) != len(inputs):
            raise RuntimeError(f"Provider returned {len(vectors)} vectors for {len(inputs)} inputs")
        if self.spec.truncate_dim and vectors and len(vectors[0]) > self.spec.truncate_dim:
            vectors = truncate_renorm(vectors, self.spec.truncate_dim)
        return EmbedResponse(vectors=vectors, tokens=tokens, elapsed_ms=elapsed_ms)

    async def _embed_openai_compatible(self, inputs: List[str]) -> Tuple[List[List[float]], Optional[int]]:
        payload: Dict[str, Any] = {"model": self.spec.model, "input": inputs}
        # Only OpenAI's text-embedding-3 family honours the dimensions parameter server-side.
        if self.spec.truncate_dim and self.spec.provider_type == "openai" and self.spec.model.startswith("text-embedding-3"):
            payload["dimensions"] = self.spec.truncate_dim
        response = await self.http.post(f"{self.v1}/embeddings", json=payload, headers=self._headers())
        self._raise_for_status(response)
        data = response.json()
        items = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
        tokens = (data.get("usage") or {}).get("prompt_tokens")
        return [item["embedding"] for item in items], tokens

    async def _embed_ollama(self, inputs: List[str]) -> Tuple[List[List[float]], Optional[int]]:
        response = await self.http.post(f"{self.root}/api/embed", json={"model": self.spec.model, "input": inputs})
        if response.status_code == 404:
            # Pre-0.3 Ollama only has the single-text endpoint.
            vectors: List[List[float]] = []
            for text in inputs:
                legacy = await self.http.post(f"{self.root}/api/embeddings", json={"model": self.spec.model, "prompt": text})
                self._raise_for_status(legacy)
                vectors.append(legacy.json()["embedding"])
            return vectors, None
        self._raise_for_status(response)
        data = response.json()
        return data.get("embeddings", []), data.get("prompt_eval_count")

    async def props(self) -> Optional[Dict[str, Any]]:
        """Fetch server/model properties where the API offers them."""
        if self.spec.provider_type == "llamacpp":
            response = await self.http.get(f"{self.root}/props")
            self._raise_for_status(response)
            return response.json()
        if self.spec.provider_type == "ollama":
            response = await self.http.post(f"{self.root}/api/show", json={"model": self.spec.model})
            self._raise_for_status(response)
            return response.json()
        return None

    async def embed_image(self, png_bytes: bytes, media_marker: str) -> EmbedResponse:
        """Embed one image through llama.cpp's multimodal prompt object."""
        if self.spec.provider_type != "llamacpp":
            raise ValueError("Image embedding is only implemented for the llama.cpp server")
        payload = {
            "model": self.spec.model,
            # The flat {"input": marker, "multimodal_data": [...]} form is silently
            # ignored by llama.cpp; only the prompt object actually embeds the image.
            "input": {"prompt_string": media_marker, "multimodal_data": [base64.b64encode(png_bytes).decode()]},
        }
        start = time.perf_counter()
        response = await self.http.post(f"{self.v1}/embeddings", json=payload)
        self._raise_for_status(response)
        data = response.json()
        vector = data["data"][0]["embedding"]
        if vector and isinstance(vector[0], list):
            vector = vector[0]
        return EmbedResponse(
            vectors=[vector],
            tokens=(data.get("usage") or {}).get("prompt_tokens"),
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )


# ---------------------------------------------------------------------------
# Hardware snapshot
# ---------------------------------------------------------------------------


async def collect_hardware() -> HardwareInfo:
    """Snapshot CPU/RAM and any NVIDIA GPUs visible to this process."""
    info = HardwareInfo(
        hostname=socket.gethostname(),
        cpu_count=psutil.cpu_count(logical=True),
        ram_gb=round(psutil.virtual_memory().total / 2**30, 1),
    )
    if not shutil.which("nvidia-smi"):
        return info
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,driver_version,utilization.gpu",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        for line in out.decode(errors="replace").splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5 or not parts[0]:
                continue
            info.gpus.append(
                GpuInfo(
                    name=parts[0],
                    memory_total_mb=int(float(parts[1])) if parts[1].replace(".", "").isdigit() else None,
                    memory_used_mb=int(float(parts[2])) if parts[2].replace(".", "").isdigit() else None,
                    driver_version=parts[3] or None,
                    utilization_percent=int(float(parts[4])) if parts[4].replace(".", "").isdigit() else None,
                )
            )
    except Exception as exc:  # noqa: BLE001 - hardware info is best-effort
        logger.warning("nvidia-smi query failed: %s", exc)
    return info


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class EmbeddingCheckRunner:
    """Execute the validation checks for one or more providers on the same texts."""

    def __init__(self, http_factory: Optional[Callable[[], httpx.AsyncClient]] = None):
        self._http_factory = http_factory or create_http_client

    async def run(
        self,
        specs: Sequence[ProviderSpec],
        texts: Sequence[str],
        options: CheckOptions,
        sample: SampleInfo,
        reference_vectors: Optional[Sequence[Sequence[float]]] = None,
    ) -> EmbeddingChecksResult:
        """Run every provider and compute cross-provider agreement.

        Args:
            specs: Providers under test (same texts for all).
            texts: Sample documents without prefixes.
            options: Check tunables.
            sample: Where the texts came from.
            reference_vectors: Stored index vectors for ``texts`` (same order), if any.
        """
        if not texts:
            raise ValueError("No sample texts to run embedding checks on")
        texts = list(texts)[: options.sample_size]
        reference = list(reference_vectors)[: len(texts)] if reference_vectors else None
        if reference is not None and len(reference) != len(texts):
            reference = None

        hardware = await collect_hardware()
        results: List[ProviderCheckResult] = []
        doc_vectors: Dict[str, List[List[float]]] = {}
        async with self._http_factory() as http:
            for spec in specs:
                result, vectors = await self._run_provider(spec, texts, options, http, reference)
                result.hardware = hardware
                results.append(result)
                if vectors:
                    doc_vectors[result.provider] = vectors

        agreement: List[AgreementPair] = []
        names = list(doc_vectors)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                agreement.append(AgreementPair(a=a, b=b, topk_overlap=round(topk_overlap(doc_vectors[a], doc_vectors[b], options.top_k), 4)))

        return EmbeddingChecksResult(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            sample=sample,
            options=options,
            results=results,
            agreement=agreement,
        )

    async def _run_provider(
        self,
        spec: ProviderSpec,
        texts: List[str],
        options: CheckOptions,
        http: httpx.AsyncClient,
        reference: Optional[List[List[float]]],
    ) -> Tuple[ProviderCheckResult, Optional[List[List[float]]]]:
        started = time.perf_counter()
        query_prefix, document_prefix = spec.query_prefix, spec.document_prefix
        if not query_prefix and not document_prefix:
            query_prefix, document_prefix = detect_task_prefixes(spec.model)
        result = ProviderCheckResult(
            provider=spec.display_name,
            model=spec.model,
            provider_type=spec.provider_type,
            query_prefix=query_prefix,
            document_prefix=document_prefix,
            truncate_dim=spec.truncate_dim,
        )
        vectors: Optional[List[List[float]]] = None
        try:
            client = EmbeddingHTTPClient(spec, http)
            result.base_url = client.root
            props = await self._safe_props(client, result)
            result.capabilities = self._capabilities_from_props(spec, props)

            result.latency = await self._measure_latency(client, options, query_prefix)
            result.throughput, vectors = await self._measure_throughput(client, texts, options, document_prefix)
            result.vectors = self._vector_stats(vectors, spec)
            result.truncation = self._truncation_checks(vectors, options)
            result.retrieval = await self._retrieval_sanity(client, texts, vectors, options, query_prefix)
            if options.check_images:
                result.image = await self._image_probe(client, spec, props, result)
            if reference is not None:
                result.agreement_with_index = round(topk_overlap(vectors, reference, options.top_k), 4)
            result.success = True
        except Exception as exc:  # noqa: BLE001 - surfaced in the result, not swallowed
            logger.error("Embedding checks failed for %s: %s", spec.display_name, exc)
            result.success = False
            result.error = str(exc)
        result.total_ms = round((time.perf_counter() - started) * 1000, 1)
        return result, vectors

    @staticmethod
    async def _safe_props(client: EmbeddingHTTPClient, result: ProviderCheckResult) -> Optional[Dict[str, Any]]:
        try:
            return await client.props()
        except Exception as exc:  # noqa: BLE001 - optional metadata
            result.warnings.append(f"server properties unavailable: {exc}")
            return None

    @staticmethod
    def _capabilities_from_props(spec: ProviderSpec, props: Optional[Dict[str, Any]]) -> Optional[ServerCapabilities]:
        if not props:
            return None
        if spec.provider_type == "llamacpp":
            modalities = props.get("modalities") or {}
            return ServerCapabilities(
                build=props.get("build_info"),
                model_alias=props.get("model_alias"),
                modalities={k: bool(v) for k, v in modalities.items()},
                context_length=(props.get("default_generation_settings") or {}).get("n_ctx"),
                slots=props.get("total_slots"),
            )
        if spec.provider_type == "ollama":
            context = next(
                (v for k, v in (props.get("model_info") or {}).items() if k.endswith(".context_length")),
                None,
            )
            caps = props.get("capabilities") or []
            return ServerCapabilities(
                model_alias=spec.model,
                modalities={c: True for c in caps if isinstance(c, str)},
                context_length=int(context) if isinstance(context, (int, float)) else None,
            )
        return None

    @staticmethod
    async def _measure_latency(client: EmbeddingHTTPClient, options: CheckOptions, query_prefix: str) -> LatencyStats:
        cold = (await client.embed([options.query_text], prefix=query_prefix)).elapsed_ms
        warm = [(await client.embed([options.query_text], prefix=query_prefix)).elapsed_ms for _ in range(options.latency_runs)]
        warm_sorted = sorted(warm)
        p95_index = max(0, min(len(warm_sorted) - 1, round(0.95 * len(warm_sorted)) - 1))
        return LatencyStats(
            cold_ms=round(cold, 1),
            p50_ms=round(statistics.median(warm), 1),
            p95_ms=round(warm_sorted[p95_index], 1),
            runs=len(warm),
        )

    @staticmethod
    async def _measure_throughput(
        client: EmbeddingHTTPClient,
        texts: List[str],
        options: CheckOptions,
        document_prefix: str,
    ) -> Tuple[List[ThroughputPoint], List[List[float]]]:
        points: List[ThroughputPoint] = []
        full_vectors: Optional[List[List[float]]] = None
        for batch_size in sorted(set(b for b in options.batch_sizes if b > 0)):
            subset = texts if batch_size > 1 else texts[: options.single_batch_limit]
            vectors: List[List[float]] = []
            tokens = 0
            server_tokens = True
            start = time.perf_counter()
            for i in range(0, len(subset), batch_size):
                response = await client.embed(subset[i : i + batch_size], prefix=document_prefix)
                vectors.extend(response.vectors)
                if response.tokens is None:
                    server_tokens = False
                else:
                    tokens += response.tokens
            wall_ms = (time.perf_counter() - start) * 1000
            if not server_tokens:
                tokens = sum(len(document_prefix) + len(t) for t in subset) // 4
            points.append(
                ThroughputPoint(
                    batch_size=batch_size,
                    texts=len(subset),
                    wall_ms=round(wall_ms, 1),
                    texts_per_s=round(len(subset) / (wall_ms / 1000), 2) if wall_ms else 0.0,
                    tokens=tokens,
                    tokens_per_s=round(tokens / (wall_ms / 1000), 1) if wall_ms else 0.0,
                    token_source="server" if server_tokens else "estimate",
                )
            )
            if len(subset) == len(texts):
                full_vectors = vectors
        if full_vectors is None:
            # Only tiny batch sizes were requested; embed everything once more for the analyses.
            batch = max(options.batch_sizes or [32])
            full_vectors = []
            for i in range(0, len(texts), batch):
                full_vectors.extend((await client.embed(texts[i : i + batch], prefix=document_prefix)).vectors)
        return points, full_vectors

    @staticmethod
    def _vector_stats(vectors: List[List[float]], spec: ProviderSpec) -> VectorStats:
        matrix = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1)
        dimension = int(matrix.shape[1])
        expected = spec.truncate_dim or spec.expected_dimension
        return VectorStats(
            dimension=dimension,
            expected_dimension=expected,
            dimension_matches=(dimension == expected) if expected else None,
            norm_mean=round(float(norms.mean()), 4),
            norm_min=round(float(norms.min()), 4),
            norm_max=round(float(norms.max()), 4),
            normalized=bool(np.all(np.abs(norms - 1.0) < 1e-3)),
        )

    @staticmethod
    def _truncation_checks(vectors: List[List[float]], options: CheckOptions) -> List[TruncationCheck]:
        dimension = len(vectors[0]) if vectors else 0
        if len(vectors) < 3:
            return []
        full = _topk_neighbors(_as_unit_matrix(vectors), options.top_k)
        checks = []
        for dim in sorted({d for d in options.truncate_dims if 0 < d < dimension}, reverse=True):
            truncated = _topk_neighbors(_as_unit_matrix(truncate_renorm(vectors, dim)), options.top_k)
            checks.append(TruncationCheck(dim=dim, topk_overlap=round(_mean_overlap(full, truncated, options.top_k), 4)))
        return checks

    @staticmethod
    async def _retrieval_sanity(
        client: EmbeddingHTTPClient,
        texts: List[str],
        vectors: List[List[float]],
        options: CheckOptions,
        query_prefix: str,
    ) -> Optional[RetrievalSanity]:
        pairs = [(i, _first_sentence(texts[i])) for i in range(0, len(texts), options.pseudo_query_every)]
        pairs = [(i, q) for i, q in pairs if q]
        if not pairs or len(vectors) < 2:
            return None
        query_vectors = (await client.embed([q for _, q in pairs], prefix=query_prefix)).vectors
        docs = _as_unit_matrix(vectors)
        queries = _as_unit_matrix(query_vectors)
        order = np.argsort(-(queries @ docs.T), axis=1)
        top1 = sum(int(order[n][0] == src) for n, (src, _) in enumerate(pairs))
        top5 = sum(int(src in order[n][:5].tolist()) for n, (src, _) in enumerate(pairs))
        return RetrievalSanity(
            queries=len(pairs),
            top1_hits=top1,
            top5_hits=top5,
            top1_rate=round(top1 / len(pairs), 3),
            top5_rate=round(top5 / len(pairs), 3),
        )

    @staticmethod
    async def _image_probe(
        client: EmbeddingHTTPClient,
        spec: ProviderSpec,
        props: Optional[Dict[str, Any]],
        result: ProviderCheckResult,
    ) -> ImageProbe:
        if spec.provider_type != "llamacpp":
            return ImageProbe(supported=False, error="image probe is only implemented for the llama.cpp server")
        modalities = (props or {}).get("modalities") or {}
        marker = (props or {}).get("media_marker")
        if not modalities.get("vision") or not marker:
            return ImageProbe(supported=False, error="server reports no vision modality")
        try:
            marker_only = (await client.embed([marker])).vectors[0]
            probe = await client.embed_image(_tiny_png(), marker)
            similarity = float(_as_unit_matrix([marker_only])[0] @ _as_unit_matrix(probe.vectors)[0])
            changed = similarity < 0.999
            if not changed:
                result.warnings.append("image data was ignored by the server (vector identical to marker-only text)")
            return ImageProbe(supported=changed, tokens=probe.tokens, latency_ms=round(probe.elapsed_ms, 1), vector_changed=changed)
        except Exception as exc:  # noqa: BLE001 - optional probe
            return ImageProbe(supported=False, error=str(exc))
