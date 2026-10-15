"""Spike benchmark: scanned-page OCR text vs. direct multimodal image embedding.

This settles one question against the synthetic Austrian law-firm corpus: for the
scanned (``scan_jpg``) documents, does embedding the **page image** directly with
multimodal EmbeddingGemma 2 retrieve the gold documents better than embedding the
**Docling OCR text** that the production pipeline stores today?

It is an evaluation harness, not a pipeline change. It reuses the existing gold
set, manifests and ranking scorer (:func:`.bench.score_ranking`,
:func:`.bench.load_gold`, :func:`.bench.load_manifests`) and the documented
llama.cpp multimodal request shape (see :meth:`backend.services.embedding_checks.
EmbeddingHTTPClient.embed_image` and ``docs/04-EMBEDDING_AND_QUERY_SYSTEM.md``).

Only the representation of scanned pages differs between arms; the embedding
model, dimensions and (no-)prefix convention are identical, so the comparison is
apples-to-apples:

* ``ocr_text`` - scanned pages are represented by their stored Docling OCR text
  chunks (the current production behaviour).
* ``image``    - scanned pages are represented by one direct image embedding each.
* ``hybrid``   - scanned pages contribute both their OCR text chunks and the image
  embedding.

Non-scanned documents (pdf/docx/eml/txt) are represented by their text chunks in
every arm, so they are a constant, realistic distractor set.

Two indexes are evaluated:

* ``mixed``     - all primary documents; scanned pages compete with clean text
  documents, as in production.
* ``scan_only`` - only scanned pages; isolates the raw representation quality.

Retrieval is exact in-memory cosine (no ANN approximation), so the only variable
is the vector, not the index implementation. The production stack embeds queries
and documents with **no task prefix** (the OpenAI-compatible provider sends raw
text), so this harness does the same by default to match the live baseline.

Cost is recorded per embedding phase (tokens, wall-clock, tokens/s, items/s) with
a GPU sampler (VRAM and utilisation), because image embeddings are far larger
token-wise and the throughput price is part of the answer.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import statistics
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import httpx
import numpy as np

from .bench import load_gold, load_manifests, score_ranking
from .models import CaseManifest, GoldCase

logger = logging.getLogger(__name__)

# Metrics reported from score_ranking, in report order.
DOC_METRICS = ("recall@1", "recall@3", "recall@5", "recall@10", "hit@5", "hit@10", "mrr")
SPAN_METRICS = ("span_recall@5", "span_recall@10")
ARMS = ("ocr_text", "image", "hybrid")


# ─── embedder client ───────────────────────────────────────────────────────────


@dataclass
class EmbedPhase:
    """Throughput of one embedding phase."""

    items: int = 0
    tokens: int = 0
    wall_ms: float = 0.0
    token_source: str = "server"
    per_item_tokens: List[int] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        secs = self.wall_ms / 1000 or 1e-9
        out = {
            "items": self.items,
            "tokens": self.tokens,
            "wall_s": round(self.wall_ms / 1000, 2),
            "items_per_s": round(self.items / secs, 2),
            "tokens_per_s": round(self.tokens / secs, 1),
            "token_source": self.token_source,
        }
        if self.per_item_tokens:
            out["tokens_per_item"] = {
                "mean": round(statistics.fmean(self.per_item_tokens), 1),
                "min": min(self.per_item_tokens),
                "max": max(self.per_item_tokens),
            }
        return out


class SyncEmbedder:
    """Minimal synchronous client for the llama.cpp OpenAI-compatible embedder.

    Mirrors the request shapes used by :class:`backend.services.embedding_checks.
    EmbeddingHTTPClient`: a plain ``{"input": [...]}`` for text and the multimodal
    ``{"input": {"prompt_string": marker, "multimodal_data": [b64]}}`` prompt
    object for images (the flat form is silently ignored by llama.cpp).
    """

    def __init__(self, base_url: str, timeout: float = 600.0) -> None:
        self.base = base_url.rstrip("/")
        self.v1 = self.base if self.base.endswith("/v1") else f"{self.base}/v1"
        self.root = self.base[:-3] if self.base.endswith("/v1") else self.base
        self.http = httpx.Client(timeout=timeout)
        props = self.http.get(f"{self.root}/props").json()
        self.model: str = props.get("model_alias") or "embedding"
        self.media_marker: Optional[str] = props.get("media_marker")
        self.modalities: Dict[str, bool] = props.get("modalities") or {}

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "SyncEmbedder":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def embed_texts(self, texts: Sequence[str], prefix: str = "", batch: int = 32) -> Tuple[np.ndarray, EmbedPhase]:
        """Embed ``texts`` in batches; returns a unit-normalised matrix and timings."""
        phase = EmbedPhase()
        vectors: List[List[float]] = []
        server_tokens = True
        start = time.perf_counter()
        for i in range(0, len(texts), batch):
            chunk = [f"{prefix}{t}" if prefix else t for t in texts[i : i + batch]]
            resp = self.http.post(f"{self.v1}/embeddings", json={"model": self.model, "input": chunk})
            resp.raise_for_status()
            data = resp.json()
            items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
            vectors.extend(it["embedding"] for it in items)
            tok = (data.get("usage") or {}).get("prompt_tokens")
            if tok is None:
                server_tokens = False
            else:
                phase.tokens += tok
        phase.wall_ms = (time.perf_counter() - start) * 1000
        phase.items = len(texts)
        if not server_tokens:
            phase.token_source = "estimate"
            phase.tokens = sum(len(prefix) + len(t) for t in texts) // 4
        return _unit_rows(vectors), phase

    def embed_image(self, jpg_bytes: bytes) -> Tuple[np.ndarray, int, float]:
        """Embed one page image through the multimodal prompt object."""
        if not self.media_marker:
            raise RuntimeError("embedder reports no media_marker; cannot embed images")
        payload = {
            "model": self.model,
            "input": {"prompt_string": self.media_marker, "multimodal_data": [base64.b64encode(jpg_bytes).decode()]},
        }
        start = time.perf_counter()
        resp = self.http.post(f"{self.v1}/embeddings", json=payload)
        resp.raise_for_status()
        data = resp.json()
        vector = data["data"][0]["embedding"]
        if vector and isinstance(vector[0], list):
            vector = vector[0]
        tokens = (data.get("usage") or {}).get("prompt_tokens") or 0
        return np.asarray(vector, dtype=np.float32), tokens, (time.perf_counter() - start) * 1000


def _unit_rows(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix[None, :]
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


# ─── GPU sampler ────────────────────────────────────────────────────────────────


class GpuSampler:
    """Background nvidia-smi poller for VRAM and utilisation during a phase."""

    def __init__(self, interval_s: float = 0.4) -> None:
        self.interval_s = interval_s
        self._mem: List[int] = []
        self._util: List[int] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def _sample() -> Optional[Tuple[int, int]]:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip().splitlines()
            if not out:
                return None
            mem, util = [p.strip() for p in out[0].split(",")]
            return int(float(mem)), int(float(util))
        except Exception:  # noqa: BLE001 - sampling is best-effort
            return None

    def _loop(self) -> None:
        while not self._stop.is_set():
            s = self._sample()
            if s:
                self._mem.append(s[0])
                self._util.append(s[1])
            self._stop.wait(self.interval_s)

    def __enter__(self) -> "GpuSampler":
        self._stop.clear()
        self._mem, self._util = [], []
        s = self._sample()
        if s:
            self._mem.append(s[0])
            self._util.append(s[1])
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def summary(self) -> Dict[str, Any]:
        if not self._mem:
            return {"samples": 0}
        return {
            "samples": len(self._mem),
            "vram_used_mb": {"min": min(self._mem), "max": max(self._mem), "mean": round(statistics.fmean(self._mem))},
            "util_pct": {"max": max(self._util), "mean": round(statistics.fmean(self._util))},
        }


def gpu_name() -> Optional[str]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return out or None
    except Exception:  # noqa: BLE001
        return None


# ─── corpus units ────────────────────────────────────────────────────────────────


@dataclass
class DocUnit:
    """One primary document and its retrieval representation(s)."""

    source: str  # relative path, matches gold sources and mongo document.source
    is_scan: bool
    text_chunks: List[str] = field(default_factory=list)  # OCR text (scan) or native text chunks
    image_path: Optional[Path] = None  # on-disk JPEG for scans
    fact_ids: List[str] = field(default_factory=list)
    ingested: bool = True  # whether the source had any chunk in MongoDB


def build_units(
    run_dir: Path,
    chunks_for_source: Callable[[str], List[str]],
) -> List[DocUnit]:
    """Build one :class:`DocUnit` per primary rendered file.

    Args:
        run_dir: Corpus run directory (holds ``manifest/`` and ``corpus/``).
        chunks_for_source: Returns the stored chunk texts for a document source
            (the production OCR/chunking output), in order.

    Returns:
        Units for every primary file; scans carry both their OCR chunks (if any)
        and the on-disk image path.
    """
    corpus_root = run_dir / "corpus"
    manifests: Dict[str, CaseManifest] = load_manifests(run_dir)
    units: List[DocUnit] = []
    for manifest in manifests.values():
        for rf in manifest.files:
            if rf.kind != "primary":
                continue
            chunks = chunks_for_source(rf.path)
            is_scan = rf.format == "scan_jpg"
            unit = DocUnit(
                source=rf.path,
                is_scan=is_scan,
                text_chunks=chunks,
                fact_ids=list(rf.fact_ids),
                ingested=bool(chunks),
            )
            if is_scan:
                img = corpus_root / rf.path
                unit.image_path = img if img.is_file() else None
            units.append(unit)
    return units


# ─── embedding of the whole corpus ────────────────────────────────────────────────


@dataclass
class CorpusVectors:
    """Embedded corpus: one text vector per chunk, one image vector per scan."""

    text_sources: List[str]
    text_contents: List[str]
    text_matrix: np.ndarray
    image_sources: List[str]
    image_matrix: np.ndarray
    text_phase: Dict[str, Any]
    image_phase: Dict[str, Any]
    image_gpu: Dict[str, Any]
    text_gpu: Dict[str, Any]


def embed_corpus(units: Sequence[DocUnit], embedder: SyncEmbedder, prefix: str = "") -> CorpusVectors:
    """Embed every text chunk and every scan image once."""
    text_sources: List[str] = []
    text_contents: List[str] = []
    for u in units:
        for c in u.text_chunks:
            text_sources.append(u.source)
            text_contents.append(c)

    with GpuSampler() as gpu:
        text_matrix, text_phase = embedder.embed_texts(text_contents, prefix=prefix)
    text_gpu = gpu.summary()

    image_sources: List[str] = []
    image_vectors: List[np.ndarray] = []
    phase = EmbedPhase()
    start = time.perf_counter()
    with GpuSampler() as gpu:
        for u in units:
            if not (u.is_scan and u.image_path):
                continue
            vec, tokens, _ms = embedder.embed_image(u.image_path.read_bytes())
            image_sources.append(u.source)
            image_vectors.append(vec)
            phase.items += 1
            phase.tokens += tokens
            phase.per_item_tokens.append(tokens)
    phase.wall_ms = (time.perf_counter() - start) * 1000
    image_gpu = gpu.summary()
    image_matrix = _unit_rows(image_vectors) if image_vectors else np.zeros((0, text_matrix.shape[1]), dtype=np.float32)

    return CorpusVectors(
        text_sources=text_sources,
        text_contents=text_contents,
        text_matrix=text_matrix,
        image_sources=image_sources,
        image_matrix=image_matrix,
        text_phase=text_phase.as_dict(),
        image_phase=phase.as_dict(),
        image_gpu=image_gpu,
        text_gpu=text_gpu,
    )


# ─── arm construction ──────────────────────────────────────────────────────────────


def arm_index(
    cv: CorpusVectors,
    units: Sequence[DocUnit],
    arm: str,
    scan_only: bool,
) -> Tuple[np.ndarray, List[str], List[str]]:
    """Assemble the (matrix, sources, contents) a given arm/index retrieves over."""
    scan_sources = {u.source for u in units if u.is_scan}
    rows: List[int] = []
    img_rows: List[int] = []

    for i, src in enumerate(cv.text_sources):
        src_is_scan = src in scan_sources
        if scan_only and not src_is_scan:
            continue
        if src_is_scan and arm == "image":
            continue  # scans represented only by their image in the image arm
        rows.append(i)

    if arm in ("image", "hybrid"):
        for j, src in enumerate(cv.image_sources):
            if scan_only and src not in scan_sources:
                continue
            img_rows.append(j)

    parts = [cv.text_matrix[rows]] if rows else [np.zeros((0, cv.text_matrix.shape[1]), dtype=np.float32)]
    sources = [cv.text_sources[i] for i in rows]
    contents = [cv.text_contents[i] for i in rows]
    if img_rows:
        parts.append(cv.image_matrix[img_rows])
        sources += [cv.image_sources[j] for j in img_rows]
        contents += ["" for _ in img_rows]  # images carry no quotable text
    matrix = np.vstack(parts) if parts else np.zeros((0, cv.text_matrix.shape[1]), dtype=np.float32)
    return matrix, sources, contents


# ─── evaluation ────────────────────────────────────────────────────────────────────


def rank_sources(query_vec: np.ndarray, matrix: np.ndarray, sources: List[str], contents: List[str], k: int
                 ) -> Tuple[List[str], List[str]]:
    """Top-k sources/contents by cosine similarity (matrix rows are unit vectors)."""
    if matrix.shape[0] == 0:
        return [], []
    sims = matrix @ query_vec
    k = min(k, sims.shape[0])
    top = np.argpartition(-sims, k - 1)[:k]
    top = top[np.argsort(-sims[top])]
    return [sources[i] for i in top], [contents[i] for i in top]


def evaluate_arm(
    gold: Sequence[GoldCase],
    query_matrix: np.ndarray,
    query_ids: List[str],
    matrix: np.ndarray,
    sources: List[str],
    contents: List[str],
    k: int = 10,
) -> List[Dict[str, Any]]:
    """Score every answerable gold question against one arm's index."""
    by_id = {g.id: g for g in gold}
    pos = {qid: i for i, qid in enumerate(query_ids)}
    rows: List[Dict[str, Any]] = []
    for g in gold:
        if g.unanswerable or g.id not in pos:
            continue
        qv = query_matrix[pos[g.id]]
        ranked_sources, ranked_contents = rank_sources(qv, matrix, sources, contents, k)
        row = {"id": g.id, "type": g.type, "area": g.area_id,
               "top_sources": ranked_sources[:3], **score_ranking(g, ranked_sources, ranked_contents)}
        rows.append(row)
        _ = by_id  # (kept for symmetry / future per-case detail)
    return rows


def _mean(rows: Sequence[Dict[str, Any]], metric: str) -> Optional[float]:
    vals = [r[metric] for r in rows if isinstance(r.get(metric), (int, float))]
    return round(statistics.fmean(vals), 3) if vals else None


def aggregate(rows: Sequence[Dict[str, Any]], subset_ids: Optional[set] = None) -> Dict[str, Any]:
    """Mean of the reported metrics over all rows (or a subset by id)."""
    sel = [r for r in rows if subset_ids is None or r["id"] in subset_ids]
    out: Dict[str, Any] = {"n": len(sel)}
    for m in (*DOC_METRICS, *SPAN_METRICS):
        out[m] = _mean(sel, m)
    return out


# ─── scan subset ───────────────────────────────────────────────────────────────────


def scan_gold_ids(gold: Sequence[GoldCase], scan_sources: set) -> set:
    """Ids of answerable gold questions whose gold documents include a scanned page."""
    ids = set()
    for g in gold:
        if g.unanswerable:
            continue
        srcs = set(g.gold_documents)
        for s in g.gold_spans:
            srcs.add(s.source)
            srcs.update(s.equivalent_sources)
        if srcs & scan_sources:
            ids.add(g.id)
    return ids


# ─── driver ────────────────────────────────────────────────────────────────────────


def run(
    run_dir: Path,
    embedder_url: str,
    mongo_uri: str,
    database: str,
    k: int = 10,
    prefix: str = "",
    out_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run the full OCR-text vs. image-embedding comparison and write a JSON report."""
    from pymongo import MongoClient

    from .bench import _fidelity  # reuse the existing per-format fact-retention metric

    db = MongoClient(mongo_uri)[database]

    def chunks_for_source(source: str) -> List[str]:
        doc = db.documents.find_one({"source": source}, {"_id": 1})
        if not doc:
            return []
        return [c.get("content", "") for c in db.chunks.find({"document_id": doc["_id"]}, {"content": 1})
                if c.get("content")]

    units = build_units(run_dir, chunks_for_source)
    scan_units = [u for u in units if u.is_scan]
    scan_sources = {u.source for u in scan_units}
    gold = load_gold(run_dir)
    answerable = [g for g in gold if not g.unanswerable]
    scan_ids = scan_gold_ids(gold, scan_sources)
    scan_ocr_ids = {g.id for g in gold if g.type == "scan_ocr"}

    logger.info("units=%d scans=%d (ingested=%d, with_image=%d) | gold answerable=%d scan-subset=%d scan_ocr=%d",
                len(units), len(scan_units), sum(u.ingested for u in scan_units),
                sum(1 for u in scan_units if u.image_path), len(answerable), len(scan_ids), len(scan_ocr_ids))

    with SyncEmbedder(embedder_url) as embedder:
        if not embedder.modalities.get("vision"):
            raise RuntimeError(f"embedder at {embedder_url} reports no vision modality: {embedder.modalities}")
        cv = embed_corpus(units, embedder, prefix=prefix)
        # queries
        q_ids = [g.id for g in answerable]
        with GpuSampler() as gpu:
            query_matrix, q_phase = embedder.embed_texts([g.prompt for g in answerable], prefix=prefix)
        query_gpu = gpu.summary()

    indexes: Dict[str, Any] = {}
    for scan_only in (False, True):
        index_name = "scan_only" if scan_only else "mixed"
        arms: Dict[str, Any] = {}
        for arm in ARMS:
            matrix, sources, contents = arm_index(cv, units, arm, scan_only)
            rows = evaluate_arm(gold, query_matrix, q_ids, matrix, sources, contents, k=k)
            entry: Dict[str, Any] = {"vectors": int(matrix.shape[0]), "scan_subset": aggregate(rows, scan_ids)}
            if not scan_only:
                entry["all_answerable"] = aggregate(rows, None)
                entry["scan_ocr"] = aggregate(rows, scan_ocr_ids)
                entry["rows"] = rows
            else:
                entry["rows"] = [r for r in rows if r["id"] in scan_ids]
            arms[arm] = entry
        indexes[index_name] = arms

    fidelity = _fidelity(db, run_dir)
    scan_fidelity = fidelity.get("by_format", {}).get("scan_jpg")

    result: Dict[str, Any] = {
        "kind": "scan_embedding_bench",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "database": database,
        "k": k,
        "prefix": prefix or "(none, matches production)",
        "embedder": {"url": embedder_url, "model": embedder.model, "dim": int(cv.text_matrix.shape[1]),
                     "modalities": embedder.modalities},
        "gpu": gpu_name(),
        "corpus": {
            "units": len(units),
            "scans": len(scan_units),
            "scans_ingested_ocr": sum(u.ingested for u in scan_units),
            "scans_with_image": sum(1 for u in scan_units if u.image_path),
            "text_chunks": len(cv.text_contents),
        },
        "gold": {"answerable": len(answerable), "scan_subset": len(scan_ids), "scan_ocr": len(scan_ocr_ids),
                 "scan_subset_ids": sorted(scan_ids)},
        "ocr_fact_retention_scan_jpg": scan_fidelity,
        "cost": {
            "text_embed": cv.text_phase,
            "image_embed": cv.image_phase,
            "query_embed": q_phase.as_dict(),
            "gpu_text_phase": cv.text_gpu,
            "gpu_image_phase": cv.image_gpu,
            "gpu_query_phase": query_gpu,
        },
        "indexes": indexes,
    }

    out_path = out_path or (run_dir / "bench" / "scan_embedding" / "scan_embedding_bench.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    result["out_path"] = str(out_path)
    return result


def _print_summary(result: Dict[str, Any]) -> None:
    print(f"\nScan representation benchmark  |  model={result['embedder']['model']}  dim={result['embedder']['dim']}")
    print(f"GPU: {result['gpu']}")
    sf = result.get("ocr_fact_retention_scan_jpg") or {}
    if sf:
        print(f"OCR fact retention on scan_jpg: content={sf.get('content_recall')}  chunk={sf.get('chunk_recall')} "
              f"({sf.get('facts')} facts over {sf.get('files')} pages)")
    c = result["corpus"]
    print(f"corpus: {c['units']} docs, {c['scans']} scans (OCR'd={c['scans_ingested_ocr']}, images={c['scans_with_image']}), "
          f"{c['text_chunks']} text chunks")
    g = result["gold"]
    print(f"gold: {g['answerable']} answerable, scan-subset={g['scan_subset']}, scan_ocr={g['scan_ocr']}")

    def line(tag: str, agg: Dict[str, Any]) -> str:
        return (f"  {tag:<22} n={agg['n']:<3} "
                f"recall@5={agg['recall@5']}  recall@10={agg['recall@10']}  "
                f"hit@5={agg['hit@5']}  mrr={agg['mrr']}  span@10={agg['span_recall@10']}")

    for index_name, arms in result["indexes"].items():
        print(f"\n[{index_name} index]")
        for arm, entry in arms.items():
            print(f"  -- {arm}  ({entry['vectors']} vectors)")
            if "all_answerable" in entry:
                print(line("all_answerable", entry["all_answerable"]))
            print(line("scan_subset", entry["scan_subset"]))
            if "scan_ocr" in entry:
                print(line("scan_ocr", entry["scan_ocr"]))

    cost = result["cost"]
    print("\nCost:")
    print(f"  text  embed: {cost['text_embed']}")
    print(f"  image embed: {cost['image_embed']}")
    print(f"  VRAM text phase : {cost['gpu_text_phase'].get('vram_used_mb')}")
    print(f"  VRAM image phase: {cost['gpu_image_phase'].get('vram_used_mb')}  util={cost['gpu_image_phase'].get('util_pct')}")
    print(f"\nreport: {result.get('out_path')}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="OCR-text vs. image-embedding retrieval benchmark for scanned pages")
    parser.add_argument("--run-dir", default=r"C:\RecallHub_Synthetic\kanzlei_v1", help="corpus run directory")
    parser.add_argument("--embedder-url", default="http://127.0.0.1:11090", help="llama.cpp embedder base url")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:11017/?directConnection=true")
    parser.add_argument("--database", default="rag_synth_kanzlei_v1")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--prefix", default="", help="optional text prefix for queries+chunks (default: none, matches prod)")
    parser.add_argument("--out", default=None, help="output JSON path")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run(
        run_dir=Path(args.run_dir),
        embedder_url=args.embedder_url,
        mongo_uri=args.mongo_uri,
        database=args.database,
        k=args.k,
        prefix=args.prefix,
        out_path=Path(args.out) if args.out else None,
    )
    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
