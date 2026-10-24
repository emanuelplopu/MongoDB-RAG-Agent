"""Benchmark a generated corpus against a running RecallHub/Quellex stack.

Three stages, each writing JSON into ``<run_dir>/bench/<bench_name>/``:

* ``ingest``    start ingestion of the corpus into a profile, record the job
                timeline and container/GPU resources, per-file timings, search
                index build time and fact fidelity (are the planned facts still
                in the converted text and in the chunks?).
* ``retrieval`` send every answerable gold question to the search API
                (hybrid, semantic, text) and score documents and passages.
* ``chat``      send every gold question through the streaming chat endpoint
                (the path the UI uses), record per-phase timings, tool calls,
                retrieved documents and passages, and score the answer.

``report`` renders all results into ``REPORT.md``.
"""

from __future__ import annotations

import collections
import difflib
import json
import logging
import math
import re
import statistics
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from . import telemetry as tm
from .models import CaseManifest, GoldCase
from .resources import ResourceSampler
from .text_utils import normalize_for_match

logger = logging.getLogger(__name__)

_ABSTAIN = re.compile(
    r"(keine|nicht|kein)\w*\s(?:[\w-]+\s){0,6}?(information|angabe|hinweis|unterlage|dokument|vermerk|nachweis)\w*"
    r"|(nicht|keine?)\s(?:[\w-]+\s){0,4}?(enthalten|ersichtlich|vorhanden|auffindbar|dokumentiert|zu finden|hervor|vor)\b"
    r"|liegt\s(?:[\w-]+\s){0,4}?nicht\svor|lässt sich\s(?:[\w-]+\s){0,6}?nicht",
    re.IGNORECASE,
)
_CITATION = re.compile(r"\[(?:Source|Quelle)\s*:\s*\"?([^\]\"]+)\"?\]", re.IGNORECASE)


@dataclass
class BenchContext:
    """Where and against what a benchmark runs."""

    run_dir: Path
    bench_name: str
    # 127.0.0.1, not localhost: on Windows a new connection to ``localhost`` tries ::1 first and waits about
    # two seconds before falling back when the service only binds IPv4 (the stack and Ollama both do).
    base_url: str = "http://127.0.0.1:11001"
    profile: str = "synthetic-kanzlei"
    mongo_uri: str = "mongodb://localhost:11017/?directConnection=true"
    database: str = "rag_synth_kanzlei_v1"
    containers: tuple[str, ...] = ("rag-backend-quellex", "rag-ingestion-worker", "rag-mongodb", "rag-embedder-gpu", "rag-embedder")
    timeout_s: float = 3600.0
    backend_container: str = "rag-backend-quellex"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_log: Optional[Path] = None
    health_wait_s: float = 600.0
    max_consecutive_errors: int = 5
    sample_interval_s: float = 5.0
    # API key of a user of the stack (``X-API-Key``); required when the backend runs with
    # REQUIRE_AUTH=true (the Quellex tenant). Taken from RECALLHUB_API_KEY by the CLI, never from a config file.
    api_key: Optional[str] = None

    @property
    def http_headers(self) -> dict[str, str]:
        """Headers every request to the stack carries (the API key when one is set)."""
        return {"X-API-Key": self.api_key} if self.api_key else {}

    @property
    def out_dir(self) -> Path:
        d = self.run_dir / "bench" / self.bench_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def api(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1{path}"


# ─── helpers ─────────────────────────────────────────────────────────────────


def _pct(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return round(s[k], 1)


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean": round(statistics.fmean(values), 1), "p50": _pct(values, 50),
            "p95": _pct(values, 95), "max": round(max(values), 1), "sum": round(sum(values), 1)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mongo(ctx: BenchContext) -> Any:
    from pymongo import MongoClient

    return MongoClient(ctx.mongo_uri)[ctx.database]


def request_with_retry(http: httpx.Client, method: str, url: str, *, min_interval_s: float = 0.0,
                       max_retries: int = 12, **kwargs: Any) -> httpx.Response:
    """HTTP request that honours the backend rate limiter (HTTP 429 + retry_after) and optional pacing."""
    last = getattr(http, "_bench_last_request", 0.0)
    wait = min_interval_s - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)
    for _ in range(max_retries):
        r = http.request(method, url, **kwargs)
        http._bench_last_request = time.monotonic()  # type: ignore[attr-defined]
        if r.status_code != 429:
            return r
        try:
            retry_after = float(r.json().get("retry_after", 30))
        except (ValueError, json.JSONDecodeError):
            retry_after = 30.0
        logger.info("rate limited on %s; waiting %.0fs", url.rsplit("/api/v1", 1)[-1], retry_after + 1)
        time.sleep(retry_after + 1)
    return r


def load_gold(run_dir: Path) -> list[GoldCase]:
    """Gold questions of a run."""
    rows = json.loads((run_dir / "gold" / "questions.json").read_text(encoding="utf-8"))
    return [GoldCase.model_validate(r) for r in rows]


def load_manifests(run_dir: Path) -> dict[str, CaseManifest]:
    """Manifests of a run by case id."""
    out = {}
    for p in sorted((run_dir / "manifest").glob("*.json")):
        m = CaseManifest.model_validate_json(p.read_text(encoding="utf-8"))
        out[m.case_id] = m
    return out


def _write_telemetry(ctx: BenchContext, run_name: str) -> Optional[Path]:
    """Render the one-page HTML telemetry report of a sampled run; a failure is logged, never fatal."""
    from .telemetry_html import write_telemetry_html

    try:
        path = write_telemetry_html(ctx.out_dir, run_name)
    except Exception as exc:  # noqa: BLE001 - the measurements are on disk already
        logger.warning("Telemetry page for %s not written: %s", run_name, exc)
        return None
    logger.info("Telemetry page: %s", path)
    return path


# ─── ingestion ───────────────────────────────────────────────────────────────


def _ensure_search_indexes(db: Any, dimensions: int) -> dict[str, Any]:
    """Create vector and text search indexes on chunks if missing and time until READY."""
    t0 = time.monotonic()
    existing = {i["name"] for i in db.chunks.list_search_indexes()}
    to_create = []
    if "vector_index" not in existing:
        to_create.append({"name": "vector_index", "type": "vectorSearch",
                          "definition": {"fields": [{"type": "vector", "path": "embedding", "numDimensions": dimensions,
                                                     "similarity": "cosine"}]}})
    if "text_index" not in existing:
        to_create.append({"name": "text_index", "type": "search",
                          "definition": {"mappings": {"dynamic": False, "fields": {"content": {
                              "type": "string", "analyzer": "lucene.standard", "indexOptions": "offsets",
                              "store": True, "norms": "include"}}}}})
    if to_create:
        db.command({"createSearchIndexes": "chunks", "indexes": to_create})
    status: dict[str, str] = {}
    while time.monotonic() - t0 < 1800:
        status = {i["name"]: i.get("status", "?") for i in db.chunks.list_search_indexes()}
        if status and all(v == "READY" for v in status.values()) and {"vector_index", "text_index"} <= set(status):
            break
        time.sleep(3)
    return {"created": [i["name"] for i in to_create], "status": status, "seconds_to_ready": round(time.monotonic() - t0, 1)}


def _fidelity(db: Any, run_dir: Path) -> dict[str, Any]:
    """Share of planned facts that survive conversion (document content) and chunking (chunk text)."""
    from .planner import load_ledger

    manifests = load_manifests(run_dir)
    per_format: dict[str, dict[str, int]] = {}
    lost: list[dict[str, Any]] = []
    missing_docs: list[str] = []
    for cid, m in manifests.items():
        ledger = load_ledger(run_dir / "ledger" / f"{cid}.json")
        for rf in m.files:
            if rf.kind != "primary":
                continue
            doc = db.documents.find_one({"source": rf.path}, {"content": 1})
            fmt = rf.format
            stats = per_format.setdefault(fmt, {"files": 0, "ingested": 0, "facts": 0, "in_content": 0, "in_chunks": 0})
            stats["files"] += 1
            if doc is None:
                missing_docs.append(rf.path)
                continue
            stats["ingested"] += 1
            content = normalize_for_match(doc.get("content") or "")
            chunk_text = normalize_for_match(" ".join(c.get("content", "") for c in db.chunks.find({"document_id": doc["_id"]}, {"content": 1})))
            for fid in rf.fact_ids:
                value = normalize_for_match(ledger.fact(fid).value)
                stats["facts"] += 1
                in_content, in_chunks = value in content, value in chunk_text
                stats["in_content"] += int(in_content)
                stats["in_chunks"] += int(in_chunks)
                if not in_chunks and len(lost) < 40:
                    lost.append({"file": rf.path, "format": fmt, "fact": fid, "value": ledger.fact(fid).value,
                                 "in_content": in_content})
    total = {k: sum(s[k] for s in per_format.values()) for k in ("files", "ingested", "facts", "in_content", "in_chunks")}
    for s in [*per_format.values(), total]:
        s["content_recall"] = round(s["in_content"] / s["facts"], 3) if s["facts"] else None
        s["chunk_recall"] = round(s["in_chunks"] / s["facts"], 3) if s["facts"] else None
    return {"by_format": per_format, "total": total, "missing_documents": missing_docs, "lost_examples": lost}


def bench_ingest(ctx: BenchContext, *, clean: bool = True, dimensions: int = 3072, poll_s: float = 3.0) -> dict[str, Any]:
    """Ingest the corpus into ``ctx.profile`` and measure everything."""
    result: dict[str, Any] = {"started": _now(), "profile": ctx.profile, "database": ctx.database}
    if clean and not ctx.database.startswith("rag_synth"):
        raise RuntimeError(f"refusing a clean ingestion into {ctx.database!r}: only dedicated rag_synth* databases may be wiped")
    expected_files = sum(len(m.files) for m in load_manifests(ctx.run_dir).values())
    timeline: list[dict[str, Any]] = []
    sampler = ResourceSampler(ctx.containers, ctx.sample_interval_s, out_path=ctx.out_dir / "resources_ingest.jsonl",
                              ollama_url=ctx.ollama_url, health_url=ctx.base_url.rstrip("/") + "/health")
    with httpx.Client(timeout=60, headers=ctx.http_headers) as http, sampler:
        t0 = time.monotonic()
        r = http.post(ctx.api("/ingestion/start"), json={"profile": ctx.profile, "clean_before_ingest": clean, "incremental": not clean})
        r.raise_for_status()
        job_id = r.json().get("job_id")
        result["job_id"] = job_id
        status = "pending"
        while time.monotonic() - t0 < ctx.timeout_s:
            time.sleep(poll_s)
            s = http.get(ctx.api(f"/ingestion/status/{job_id}")).json()
            status = str(s.get("status", "")).lower()
            total = s.get("total_files") or 0
            if total and abs(total - expected_files) > max(5, expected_files // 10):
                http.post(ctx.api("/ingestion/stop"))
                raise RuntimeError(f"ingestion discovered {total} files but the corpus has {expected_files}; "
                                   "stopped the job (wrong profile or folder?)")
            timeline.append({"t": round(time.monotonic() - t0, 1), "status": status,
                             "processed": s.get("processed_files"), "total": s.get("total_files"),
                             "failed": s.get("failed_files"), "chunks": s.get("chunks_created"),
                             "current": (s.get("current_file") or "")[-80:]})
            if status in ("completed", "failed", "cancelled", "stopped"):
                break
        t_ingest = time.monotonic() - t0
        result["status"] = status
        result["ingest_seconds"] = round(t_ingest, 1)
        db = _mongo(ctx)
        result["search_indexes"] = _ensure_search_indexes(db, dimensions)
        result["resources"] = sampler.summary()
    first_progress = next((row["t"] for row in timeline if (row.get("processed") or 0) > 0), None)
    result["discovery_seconds"] = first_progress
    result["counts"] = {"documents": db.documents.count_documents({}), "chunks": db.chunks.count_documents({}),
                        "failed_documents": db.failed_documents.count_documents({}) if "failed_documents" in db.list_collection_names() else 0}
    tokens = list(db.chunks.aggregate([{"$group": {"_id": None, "tokens": {"$sum": "$token_count"}}}]))
    result["counts"]["chunk_tokens"] = tokens[0]["tokens"] if tokens else 0
    stats = list(db.ingestion_stats.find({}, {"_id": 0})) if "ingestion_stats" in db.list_collection_names() else []
    by_ext: dict[str, dict[str, Any]] = {}
    for st in stats:
        ext = (st.get("file_type") or st.get("extension") or Path(str(st.get("file_path", ""))).suffix or "?").lower().lstrip(".")
        e = by_ext.setdefault(ext, {"files": 0, "ok": 0, "ms": [], "chunks": [], "mb": []})
        e["files"] += 1
        e["ok"] += int(bool(st.get("success", True)))
        e["ms"].append(float(st.get("processing_time_ms") or 0))
        e["chunks"].append(float(st.get("chunks_created") or st.get("chunk_count") or 0))
        e["mb"].append(float(st.get("file_size_bytes") or st.get("file_size") or 0) / 1e6)
    result["per_format"] = {
        ext: {"files": e["files"], "ok": e["ok"], "seconds": _summary([x / 1000 for x in e["ms"]]),
              "chunks": _summary(e["chunks"]), "size_mb": _summary(e["mb"])}
        for ext, e in sorted(by_ext.items())
    }
    result["slowest_files"] = sorted(
        ({"file": str(s.get("file_path", ""))[-90:], "seconds": round(float(s.get("processing_time_ms") or 0) / 1000, 1),
          "chunks": s.get("chunks_created") or s.get("chunk_count")} for s in stats),
        key=lambda x: -x["seconds"],
    )[:8]
    result["stats_fields"] = sorted(stats[0].keys()) if stats else []
    result["fidelity"] = _fidelity(db, ctx.run_dir)
    result["timeline"] = timeline
    result["resource_samples"] = sampler.samples
    (ctx.out_dir / "ingest.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    page = _write_telemetry(ctx, "ingest")
    result["telemetry_html"] = str(page) if page else None
    return result


_LOG_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - (\S+) - \w+ - (.*)$")


def parse_ingest_log(text: str) -> list[dict[str, Any]]:
    """Per-file stage timings from the backend log of one ingestion run.

    Stages: ``convert`` (start to converted markdown), ``chunk``, ``embed`` (OpenAI
    embedding calls), ``store`` (MongoDB inserts) and ``total`` (start of this file
    to start of the next).
    """
    files: list[dict[str, Any]] = []
    cur: Optional[dict[str, Any]] = None
    for line in text.splitlines():
        m = _LOG_TS.match(line)
        if not m:
            continue
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f").timestamp()
        msg = m.group(3)
        fm = re.match(r"Processing file (\d+)/(\d+)(?: \([^)]*\))?: (.*)$", msg)
        if fm:
            if cur is not None:
                cur["next_start"] = ts
            cur = {"n": int(fm.group(1)), "of": int(fm.group(2)), "path": fm.group(3), "start": ts}
            files.append(cur)
            continue
        if cur is None:
            continue
        if msg.startswith(("Successfully converted", "Successfully extracted", "Extracted text")) and "converted" not in cur:
            cur["converted"] = ts
        elif re.match(r"Created \d+ chunks$", msg):
            cur["chunked"] = ts
            cur["chunks"] = int(msg.split()[1])
        elif msg.startswith("Generating embeddings"):
            cur["embed_start"] = ts
        elif msg.startswith("Generated embeddings") and m.group(2).endswith("ingest"):
            cur["embedded"] = ts
        elif msg.startswith("Saved document to MongoDB"):
            cur["saved"] = ts
        elif "OCR" in msg or "ocr" in m.group(2):
            cur["ocr"] = True
    out = []
    for f in files:
        def d(a: str, b: str) -> Optional[float]:
            return round(f[b] - f[a], 2) if a in f and b in f else None
        conv_end = f.get("converted", f.get("chunked"))
        out.append({
            "n": f["n"], "file": f["path"].rsplit("/", 1)[-1], "ext": Path(f["path"]).suffix.lower().lstrip("."),
            "convert_s": round(conv_end - f["start"], 2) if conv_end else None,
            "chunk_s": d("converted", "chunked"), "embed_s": d("embed_start", "embedded"), "store_s": d("embedded", "saved"),
            "total_s": round(f.get("next_start", f.get("saved", f["start"])) - f["start"], 2), "chunks": f.get("chunks"),
        })
    return out


def ingest_stage_summary(files: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-file stage timings by extension and overall."""
    def agg(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {stage: _summary([x[stage] for x in items if isinstance(x.get(stage), (int, float))])
                for stage in ("convert_s", "chunk_s", "embed_s", "store_s", "total_s")} | {
            "files": len(items), "chunks_per_file": round(statistics.fmean([x["chunks"] or 0 for x in items]), 2) if items else None}
    by_ext: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        by_ext.setdefault(f["ext"], []).append(f)
    return {"overall": agg(files), "by_ext": {e: agg(v) for e, v in sorted(by_ext.items())},
            "slowest": sorted(files, key=lambda x: -(x["total_s"] or 0))[:8]}


def attach_log_timings(ctx: BenchContext, container: str = "rag-backend-quellex") -> dict[str, Any]:
    """Parse the backend log since the benchmark start and merge stage timings into ``ingest.json``."""
    path = ctx.out_dir / "ingest.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    since = result["started"]
    log = subprocess.run(["docker", "logs", "--since", since, container], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120)
    files = parse_ingest_log(log.stdout + "\n" + log.stderr)
    result["stages"] = ingest_stage_summary(files)
    result["stage_files"] = files
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return result["stages"]


# ─── retrieval ───────────────────────────────────────────────────────────────


def _gold_groups(g: GoldCase) -> list[set[str]]:
    """One set per gold document: the document plus its exact copies."""
    groups: dict[str, set[str]] = {}
    for doc in g.gold_documents:
        groups.setdefault(doc, {doc})
    for s in g.gold_spans:
        groups.setdefault(s.source, {s.source}).update(s.equivalent_sources)
    return list(groups.values())


def score_ranking(g: GoldCase, sources: list[str], contents: list[str], k_values: tuple[int, ...] = (1, 3, 5, 10)) -> dict[str, Any]:
    """Document- and passage-level metrics for one ranked result list."""
    groups = _gold_groups(g)
    gold_any = set().union(*groups) if groups else set()
    first = next((i + 1 for i, s in enumerate(sources) if s in gold_any), None)
    out: dict[str, Any] = {"first_relevant_rank": first, "mrr": round(1 / first, 4) if first else 0.0}
    for k in k_values:
        top = set(sources[:k])
        out[f"hit@{k}"] = int(bool(top & gold_any))
        out[f"recall@{k}"] = round(sum(1 for grp in groups if grp & top) / len(groups), 3) if groups else None
    quotes = [normalize_for_match(s.quote) for s in g.gold_spans if s.quote]
    norm = [normalize_for_match(c) for c in contents]
    for k in k_values:
        joined = " ".join(norm[:k])
        out[f"span_hit@{k}"] = int(any(q in joined for q in quotes)) if quotes else None
        out[f"span_recall@{k}"] = round(sum(1 for q in quotes if q in joined) / len(quotes), 3) if quotes else None
    return out


def bench_retrieval(ctx: BenchContext, gold: list[GoldCase], modes: tuple[str, ...] = ("hybrid", "semantic", "text"),
                    k: int = 10) -> dict[str, Any]:
    """Send each answerable question to the search API and score the ranking."""
    rows = []
    with httpx.Client(timeout=120, headers=ctx.http_headers) as http:
        for g in gold:
            if g.unanswerable:
                continue
            for mode in modes:
                # the search endpoints allow 30 requests per minute per client; pace at ~28/min
                r = request_with_retry(http, "POST", ctx.api(f"/search/{mode}"), min_interval_s=2.15,
                                       json={"query": g.prompt, "match_count": k})
                ms = r.elapsed.total_seconds() * 1000
                if r.status_code != 200:
                    rows.append({"id": g.id, "mode": mode, "error": f"HTTP {r.status_code}: {r.text[:200]}", "ms": ms})
                    continue
                results = r.json().get("results", [])
                sources = [x.get("document_source", "") for x in results]
                contents = [x.get("content", "") for x in results]
                rows.append({"id": g.id, "mode": mode, "type": g.type, "area": g.area_id, "ms": round(ms, 1),
                             "n": len(results), "top_sources": sources[:5], **score_ranking(g, sources, contents)})
    result = {"started": _now(), "k": k, "modes": list(modes), "rows": rows, "summary": summarize_rows(rows, ["mode"]),
              "by_type": summarize_rows(rows, ["mode", "type"])}
    (ctx.out_dir / "retrieval.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def summarize_rows(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    """Mean of numeric metrics grouped by ``keys``; latency as p50/p95."""
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for r in rows:
        if "error" in r:
            continue
        groups.setdefault(tuple(r.get(k) for k in keys), []).append(r)
    out = []
    for key, items in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        entry: dict[str, Any] = dict(zip(keys, key))
        entry["n"] = len(items)
        metrics = sorted({m for it in items for m, v in it.items() if isinstance(v, (int, float)) and m not in ("ms", "n")})
        for m in metrics:
            vals = [it[m] for it in items if isinstance(it.get(m), (int, float))]
            if vals:
                entry[m] = round(statistics.fmean(vals), 3)
        lat = [it["ms"] for it in items if "ms" in it]
        entry["latency_ms_p50"], entry["latency_ms_p95"] = _pct(lat, 50), _pct(lat, 95)
        out.append(entry)
    return out


_BOOLEAN = re.compile(r"\b(AND|OR|NOT)\b|[\"„“”']")


def sanitize_query(query: str) -> str:
    """Remove quote characters and boolean operators the planner adds (plain-language query for hybrid search)."""
    return re.sub(r"\s+", " ", _BOOLEAN.sub(" ", query)).strip()


def bench_query_rewrite(ctx: BenchContext, gold: list[GoldCase], chat_file: str = "chat_thinking.jsonl", k: int = 10) -> dict[str, Any]:
    """Isolate the effect of the planner's query rewriting on retrieval.

    For each answered question, the planner's queries are sent to hybrid search
    as written and sanitized; results are merged round-robin and compared with
    searching the plain question (from ``retrieval.json``).
    """
    by_id = {g.id: g for g in gold}
    rows = [json.loads(x) for x in (ctx.out_dir / chat_file).read_text(encoding="utf-8").splitlines() if x.strip()]
    retrieval = json.loads((ctx.out_dir / "retrieval.json").read_text(encoding="utf-8"))
    raw = {r["id"]: r for r in retrieval["rows"] if r.get("mode") == "hybrid" and "error" not in r}
    out_rows = []
    with httpx.Client(timeout=120, headers=ctx.http_headers) as http:
        for r in rows:
            g = by_id.get(r["id"])
            queries = [str(q) for q in (r.get("queries") or []) if _usable_query(q)]
            if g is None or g.unanswerable:
                continue
            entry: dict[str, Any] = {"id": g.id, "type": g.type, "queries": queries, "no_usable_query": int(not queries),
                                     "raw_hit@10": raw.get(g.id, {}).get("hit@10"), "raw_span_hit@10": raw.get(g.id, {}).get("span_hit@10")}
            if not queries:
                # The agent searched with an empty query; nothing the planner wrote could have found the gold document.
                entry.update({"planner_hit@10": 0, "planner_span_hit@10": 0, "sanitized_hit@10": 0, "sanitized_span_hit@10": 0})
                out_rows.append(entry)
                continue
            for variant, qs in (("planner", queries), ("sanitized", [sanitize_query(q) for q in queries])):
                lists = []
                for q in dict.fromkeys(qs):
                    resp = request_with_retry(http, "POST", ctx.api("/search/hybrid"), min_interval_s=2.15,
                                              json={"query": q, "match_count": k})
                    lists.append(resp.json().get("results", []) if resp.status_code == 200 else [])
                merged, seen = [], set()
                for i in range(k):
                    for lst in lists:
                        if i < len(lst) and lst[i].get("chunk_id") not in seen:
                            seen.add(lst[i].get("chunk_id"))
                            merged.append(lst[i])
                merged = merged[:k]
                m = score_ranking(g, [x.get("document_source", "") for x in merged], [x.get("content", "") for x in merged], (1, 10))
                entry[f"{variant}_hit@10"], entry[f"{variant}_span_hit@10"] = m["hit@10"], m["span_hit@10"]
            out_rows.append(entry)

    def mean(key: str) -> Optional[float]:
        vals = [x[key] for x in out_rows if isinstance(x.get(key), (int, float))]
        return round(statistics.fmean(vals), 3) if vals else None

    result = {"n": len(out_rows), "no_usable_query": sum(x["no_usable_query"] for x in out_rows), "summary": {v: {"hit@10": mean(f"{v}_hit@10"), "span_hit@10": mean(f"{v}_span_hit@10")}
                                              for v in ("raw", "planner", "sanitized")}, "rows": out_rows}
    (ctx.out_dir / "query_rewrite.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


# ─── chat ────────────────────────────────────────────────────────────────────


_MONTHS = {"jänner": 1, "januar": 1, "jän": 1, "jan": 1, "feber": 2, "februar": 2, "feb": 2, "märz": 3, "mär": 3, "april": 4,
           "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
           "sept": 9, "oktober": 10, "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12}
_NUM_DATE = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})\b")
_WORD_DATE = re.compile(r"\b(\d{1,2})\.\s?(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{4})\b", re.IGNORECASE)
_AMOUNT = re.compile(r"(?:EUR|€)\s?(\d{1,3}(?:[.\s]\d{3})+|\d+)(?:,(\d{1,2}|-{1,2}))?"
                     r"|(\d{1,3}(?:[.\s]\d{3})+|\d+)(?:,(\d{1,2}|-{1,2}))?\s?(?:EUR|€|Euro)", re.IGNORECASE)


def _dates_in(text: str) -> set[str]:
    out = {f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for d, m, y in _NUM_DATE.findall(text)}
    out |= {f"{int(y):04d}-{_MONTHS[mon.lower()]:02d}-{int(d):02d}" for d, mon, y in _WORD_DATE.findall(text)}
    return out


def _amounts_in(text: str) -> set[str]:
    out = set()
    for a_int, a_dec, b_int, b_dec in _AMOUNT.findall(text):
        integer, dec = (a_int, a_dec) if a_int else (b_int, b_dec)
        dec = "00" if not dec or dec.startswith("-") else dec.ljust(2, "0")
        digits = re.sub(r"[.\s]", "", integer)
        out.add(f"{int(digits)}.{dec}")
    return out


def _contains_answer_value(answer: str, value: str) -> bool:
    """True if the answer states ``value``; dates and amounts are compared format-independently."""
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value):
        d, m, y = value.split(".")
        return f"{y}-{m}-{d}" in _dates_in(answer)
    if value.startswith("EUR "):
        canonical = _amounts_in(value)
        return bool(canonical) and bool(canonical & _amounts_in(answer))
    return normalize_for_match(value).lower() in normalize_for_match(answer).lower()


_STOPWORDS = frozenset(
    "aber alle allem allen aller alles als also am an andere anderen auch auf aus bei beim bis da damit dann das dass dem den "
    "der des dessen die dies diese diesem diesen dieser dieses doch durch ein eine einem einen einer eines er es eur euro für "
    "gegen hat hatte hier ihr ihre im in ist ja jede jedem jeden jeder jedes kann kein keine man mit muss nach nicht noch nur "
    "ob oder ohne sehr sein seine sich sie sind so über um und uns unter vom von vor war waren was weil wenn werden wie wir "
    "wird wurde wurden zu zum zur zwischen".split()
)
_WORD = re.compile(r"[0-9a-zäöüß]+")
_NAME = re.compile(r"\b[A-ZÄÖÜ][a-zäöüß]{3,}\b")
_TRUNCATED = "truncated: repetitive output detected"
_JSON_PHASES = ("analyze", "plan", "evaluate")
NUGGET_TOKEN_THRESHOLD = 0.6
NUGGET_RECALL_OK = 0.6


def _content_tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(normalize_for_match(text).lower()) if len(w) >= 3 and w not in _STOPWORDS]


def nugget_covered(answer: str, nugget: str, threshold: float = NUGGET_TOKEN_THRESHOLD) -> bool:
    """True if the answer states the nugget.

    Dates and amounts in the nugget must appear in the answer (format-independent), and at
    least ``threshold`` of its content words. Words are compared by their first five letters,
    a crude stemmer for German inflection.
    """
    if not _dates_in(nugget) <= _dates_in(answer) or not _amounts_in(nugget) <= _amounts_in(answer):
        return False
    tokens = [t for t in _content_tokens(nugget) if not t.isdigit()]
    if not tokens:
        return True
    answer_tokens = set(_content_tokens(answer))
    hits = sum(1 for t in tokens if any(a.startswith(t[:5]) for a in answer_tokens))
    return hits / len(tokens) >= threshold


def _usable_query(q: Any) -> bool:
    return q is not None and str(q).strip() not in ("", "None")


def _malformed_query(q: Any) -> bool:
    """Planner output that leaked JSON syntax or was cut off: newlines, a leading quote-comma, unbalanced quotes."""
    s = str(q).strip()
    return "\n" in s or s.startswith((",", '",')) or s.count('"') % 2 == 1


def name_drift(prompt: str, queries: list[str]) -> list[str]:
    """Capitalised words of the question that the queries spell differently (``Erhart->Ehrhart``)."""
    names = set(_NAME.findall(prompt))
    drift: set[str] = set()
    for w in {w for q in queries for w in _NAME.findall(str(q))} - names:
        for n in names:
            if n.startswith(w) or w.startswith(n):
                continue
            if difflib.SequenceMatcher(None, w.lower(), n.lower()).ratio() >= 0.8:
                drift.add(f"{n}->{w}")
    return sorted(drift)


def query_quality(prompt: str, queries: list[Any]) -> dict[str, Any]:
    """Tool-argument checks: empty and malformed queries, names spelled differently than in the question."""
    usable = [str(q) for q in queries if _usable_query(q)]
    return {
        "queries_empty": len(queries) - len(usable),
        "queries_malformed": sum(1 for q in usable if _malformed_query(q)),
        "no_usable_query": int(not usable),
        "name_drift": name_drift(prompt, usable),
    }


def answer_scores(g: GoldCase, answer: str) -> dict[str, Any]:
    """Answer-only metrics, recomputable from stored answers without new LLM calls.

    ``answer_ok`` is the headline: all required values for questions that have them, otherwise
    at least ``NUGGET_RECALL_OK`` of the nuggets; a draft value always makes it wrong. For
    unanswerable questions it means the answer declined.
    """
    out: dict[str, Any] = {"answer_truncated": int(_TRUNCATED in answer)}
    if g.unanswerable:
        out["abstained"] = int(bool(_ABSTAIN.search(answer)))
        out["answer_ok"] = out["abstained"]
        return out
    hits = [v for v in g.must_include if _contains_answer_value(answer, v)]
    covered = [n for n in g.nuggets if nugget_covered(answer, n)]
    out["must_include_rate"] = round(len(hits) / len(g.must_include), 3) if g.must_include else None
    out["must_include_all"] = int(len(hits) == len(g.must_include)) if g.must_include else None
    out["missing_values"] = [v for v in g.must_include if v not in hits]
    out["must_not_violations"] = [v for v in g.must_not_include if _contains_answer_value(answer, v)]
    out["nugget_recall"] = round(len(covered) / len(g.nuggets), 3) if g.nuggets else None
    out["missing_nuggets"] = [n for n in g.nuggets if n not in covered]
    out["abstained"] = int(bool(_ABSTAIN.search(answer)) and not hits and not covered)
    if g.must_include:
        ok: Optional[bool] = out["must_include_all"] == 1
    elif g.nuggets:
        ok = out["nugget_recall"] >= NUGGET_RECALL_OK
    else:
        ok = None
    out["answer_ok"] = None if ok is None else int(ok and not out["must_not_violations"])
    return out


def failure_stage(row: dict[str, Any], unanswerable: bool) -> str:
    """Where the pipeline lost the answer, checked in pipeline order (plan, retrieval, synthesis)."""
    if row.get("error"):
        return "error"
    if unanswerable:
        return "correct_decline" if row.get("abstained") else "answered_unanswerable"
    if row.get("answer_ok") == 1:
        return "correct"
    if row.get("no_usable_query"):
        return "plan_no_query"
    if not row.get("agent_hit@10"):
        return "retrieval_doc_miss"
    if row.get("agent_span_hit@10") == 0:
        return "retrieval_passage_miss"
    if row.get("answer_truncated"):
        return "synthesis_truncated"
    if row.get("abstained"):
        return "synthesis_abstained"
    if row.get("answer_ok") is None:
        return "unscored"
    return "synthesis_incomplete"


def _step_record(s: dict[str, Any]) -> dict[str, Any]:
    """Slim orchestrator step with JSON-health and budget flags.

    Uses the trace's explicit fields (``parse_error``, ``output_truncated``, ...) when the
    backend sends them; older backends are judged from the output text.
    """
    output = str(s.get("output") or "")
    rec = {k: s.get(k) for k in ("phase", "duration_ms", "tokens", "tokens_per_second", "is_cold_start", "model")}
    rec["output"] = output[:300]
    if "parse_error" in s:
        rec["parse_failed"] = int(bool(s.get("parse_error")))
        rec["truncated"] = int(bool(s.get("output_truncated")))
        for k in ("prompt_tokens", "completion_tokens", "hidden_tokens_est", "max_tokens", "attempt", "request_options", "error"):
            rec[k] = s.get(k)
        rec["hit_token_limit"] = int(bool(s.get("hit_token_limit")))
        rec["repetition_detected"] = int(bool(s.get("repetition_detected")))
        if s.get("raw_output"):
            rec["raw_output"] = str(s["raw_output"])[:1500]
    else:
        rec["parse_failed"] = int(str(s.get("phase")) in _JSON_PHASES and output.startswith('{"response"'))
        rec["truncated"] = int(_TRUNCATED in output)
    return rec


_REPETITIVE = re.compile(r"Repetitive output detected in (\w+) phase")
_ITERATION = re.compile(r"Iteration \d+: (\w+), confidence: ([\d.]+)")


def agent_log_stats(since: str, until: Optional[str] = None, container: str = "rag-backend-quellex") -> dict[str, Any]:
    """Count orchestrator LLM calls, JSON parse failures, repetition-guard truncations and evaluate decisions in the backend log."""
    cmd = ["docker", "logs", "--since", since] + (["--until", until] if until else []) + [container]
    log = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    text = log.stdout + "\n" + log.stderr
    return {
        "since": since, "until": until,
        "orchestrator_llm_calls": text.count("Orchestrator LLM call:"),
        "json_parse_failures": text.count("Failed to parse JSON from orchestrator"),
        "repetition_truncations": dict(collections.Counter(_REPETITIVE.findall(text))),
        "evaluate_decisions": dict(collections.Counter(f"{d} @ {c}" for d, c in _ITERATION.findall(text))),
    }


def rescore_answers(path: Path, gold: list[GoldCase]) -> int:
    """Recompute answer, query and failure-stage metrics of stored chat results (no new LLM calls). Returns the number of rows."""
    by_id = {g.id: g for g in gold}
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    for r in rows:
        g = by_id.get(r["id"])
        if g is None:
            continue
        r.update(answer_scores(g, r.get("answer") or ""))
        r.update(query_quality(g.prompt, r.get("queries") or []))
        r["failure_stage"] = failure_stage(r, g.unanswerable)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows), encoding="utf-8")
    return len(rows)


def rescore_chat_files(ctx: BenchContext, gold: list[GoldCase], modes: Optional[list[str]] = None,
                       log_since: Optional[str] = None, log_until: Optional[str] = None) -> dict[str, Any]:
    """Rescore stored chat results and rewrite their summaries, keeping resource and log statistics."""
    out: dict[str, Any] = {}
    for path in sorted(ctx.out_dir.glob("chat_*.jsonl")):
        mode = path.stem[len("chat_"):]
        if modes and mode not in modes:
            continue
        rescore_answers(path, gold)
        summary_path = ctx.out_dir / f"chat_{mode}.summary.json"
        old = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        summary = summarize_chat(rows)
        summary.update({k: v for k, v in old.items() if k.startswith(("resources", "agent_log", "run_"))})
        if log_since:
            summary["agent_log"] = agent_log_stats(log_since, log_until)
        summary["anomalies"] = find_anomalies(summary, rows)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        out[mode] = summary
    return out


def _stream_chat(http: httpx.Client, ctx: BenchContext, session_id: str, prompt: str, mode: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    t0 = time.monotonic()
    first_byte = None
    final: Optional[dict[str, Any]] = None
    error = None
    with http.stream("POST", ctx.api(f"/sessions/{session_id}/messages/stream"),
                     json={"content": prompt, "agent_mode": mode, "language": "de"}) as r:
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}", "seconds": time.monotonic() - t0}
        for line in r.iter_lines():
            if first_byte is None:
                first_byte = time.monotonic() - t0
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            t = round(time.monotonic() - t0, 2)
            et = ev.get("type")
            if et == "response":
                final = ev
                events.append({"t": t, "type": et})
            elif et == "error":
                error = ev.get("message")
                events.append({"t": t, "type": et, "message": error})
            else:
                slim = {k: v for k, v in ev.items() if k in ("type", "phase", "status", "duration_ms", "tokens", "tool",
                                                             "task_type", "documents_count", "iteration", "decision", "output")}
                if et == "worker_step":
                    slim["input"] = str(ev.get("input", ""))[:200]
                if "output" in slim:
                    slim["output"] = str(slim["output"])[:160]
                slim["t"] = t
                events.append(slim)
    return {"events": events, "final": final, "error": error, "seconds": round(time.monotonic() - t0, 2),
            "first_byte_s": round(first_byte or 0, 2)}


def _phase_durations(events: list[dict[str, Any]], total: float) -> dict[str, float]:
    starts = [(e["phase"], e["t"]) for e in events if e.get("type") == "phase" and e.get("status") == "started"]
    end_t = next((e["t"] for e in events if e.get("type") == "response"), total)
    out: dict[str, float] = {}
    for i, (phase, t) in enumerate(starts):
        nxt = starts[i + 1][1] if i + 1 < len(starts) else end_t
        out[phase] = round(out.get(phase, 0) + (nxt - t), 2)
    return out


def score_chat(g: GoldCase, answer: str, final: Optional[dict[str, Any]], chunk_text: Callable[[list[str]], list[str]]) -> dict[str, Any]:
    """Score tools, retrieved documents/passages and the answer for one question."""
    trace = (final or {}).get("trace") or {}
    steps = trace.get("worker_steps") or []
    tools = [s.get("tool") for s in steps]
    queries = [(s.get("input") or {}).get("query") if isinstance(s.get("input"), dict) else s.get("input") for s in steps]
    out: dict[str, Any] = {
        "tools": tools,
        "queries": [str(q)[:160] for q in queries],
        "tool_calls": len(steps),
        "tool_expected_ok": int(any(t in g.expected_tools for t in tools)),
        "tool_forbidden_used": int(any(t in g.forbidden_tools for t in tools)),
        "boolean_query": int(any(re.search(r"\b(AND|OR|NOT)\b|\"", str(q) or "") for q in queries)),
        **query_quality(g.prompt, queries),
    }
    sources = [s.get("source", "") for s in (final or {}).get("sources") or []]
    chunk_ids = [d.get("id") for s in steps for d in (s.get("documents") or []) if d.get("id")]
    contents = chunk_text(chunk_ids)
    if not g.unanswerable:
        ranking = score_ranking(g, sources, contents, (1, 5, 10))
        out.update({f"agent_{k}": v for k, v in ranking.items() if k.startswith(("hit@", "recall@", "span_hit@", "mrr"))})
    out.update(answer_scores(g, answer))
    cited = _CITATION.findall(answer)
    out["citations"] = len(cited)
    out["answer_chars"] = len(answer)
    return out


def _wait_healthy(http: httpx.Client, ctx: BenchContext) -> float:
    """Seconds spent waiting for ``/health``; raises RuntimeError after ``ctx.health_wait_s``."""
    t0 = time.monotonic()
    while True:
        try:
            if http.get(ctx.base_url.rstrip("/") + "/health", timeout=10).status_code == 200:
                return round(time.monotonic() - t0, 1)
        except httpx.HTTPError:
            pass
        if time.monotonic() - t0 > ctx.health_wait_s:
            raise RuntimeError(f"backend not healthy after {ctx.health_wait_s:.0f} s")
        time.sleep(5)


def _now_ms() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _run_question(ctx: BenchContext, http: httpx.Client, g: GoldCase, mode: str, chunk_text: Callable[[list[str]], list[str]], *,
                  keep_sessions: bool, slice_logs: bool, ollama_tail: tm.OllamaLogTail, blobs: dict[str, str]) -> dict[str, Any]:
    """Run one question and collect its result, scores and telemetry; never raises."""
    rec: dict[str, Any] = {"id": g.id, "type": g.type, "area": g.area_id, "mode": mode, "prompt": g.prompt, "started_at": _now_ms()}
    log_offset = ollama_tail.offset()
    sid = None
    try:
        rec["health_wait_s"] = _wait_healthy(http, ctx)
        resp = request_with_retry(http, "POST", ctx.api("/sessions"), json={"title": f"bench {ctx.bench_name} {g.id} {mode}"})
        resp.raise_for_status()
        sid = resp.json().get("id")
        run = _stream_chat(http, ctx, sid, g.prompt, mode)
    except Exception as exc:  # noqa: BLE001 - recorded per question, the run continues
        run = {"error": f"{type(exc).__name__}: {exc}"[:500], "events": [], "seconds": None}
    rec["ended_at"] = _now_ms()
    final = run.get("final") or {}
    answer = final.get("content") or ""
    trace = final.get("trace") or {}
    events = run.get("events", [])
    rec.update({
        "seconds": run.get("seconds"), "first_byte_s": run.get("first_byte_s"), "error": run.get("error"),
        "effective_mode": trace.get("mode"), "models": trace.get("models"),
        "phases_s": _phase_durations(events, run.get("seconds") or 0),
        "orchestrator_steps": [_step_record(s) for s in trace.get("orchestrator_steps") or []],
        "degradations": trace.get("degradations") or [],
        "iterations": trace.get("iterations"),
        "worker_failures": [{"tool": s.get("tool"), "error": str(s.get("error") or "")[:200]}
                            for s in trace.get("worker_steps") or [] if s.get("success") is False],
        "timing": trace.get("timing"), "tokens": trace.get("tokens"), "stats": final.get("stats"),
        "answer": answer, "sources": [s.get("source") for s in final.get("sources") or []],
        "stream": {"events": len(events), "error_events": sum(1 for e in events if e.get("type") == "error"),
                   "no_response_event": int(not run.get("final") and not run.get("error"))},
        "events": events,
    })
    rec.update(score_chat(g, answer, final, chunk_text))
    rec["failure_stage"] = failure_stage(rec, g.unanswerable)
    rec["foreign_source_share"] = None if g.type == "cross_case" else tm.foreign_source_share(g.gold_documents, rec["sources"])
    if slice_logs:
        text, rotated = ollama_tail.read_from(log_offset)
        rec["ollama"] = tm.parse_ollama_log(text, blobs) | {"log_rotated": rotated}
        rec["backend_log"] = tm.parse_backend_log(tm.docker_logs(ctx.backend_container, rec["started_at"], rec["ended_at"]))
    rec["ollama_ps"] = tm.ollama_ps(ctx.ollama_url)
    if sid and not keep_sessions:
        try:
            request_with_retry(http, "DELETE", ctx.api(f"/sessions/{sid}"))
        except httpx.HTTPError as exc:
            rec["cleanup_error"] = str(exc)[:200]
    return rec


def bench_chat(ctx: BenchContext, gold: list[GoldCase], mode: str, *, limit: Optional[int] = None,
               keep_sessions: bool = False, ids: Optional[list[str]] = None, concurrency: int = 1) -> dict[str, Any]:
    """Run gold questions through the streaming chat endpoint; resumable per question.

    Sequential runs slice the Ollama and backend logs per question. ``concurrency > 1`` is a
    load probe (results in ``chat_<mode>_c<N>.jsonl``): questions run in parallel, logs are
    summarised for the whole run, and each answer's sources are checked for other cases.
    """
    name = f"chat_{mode}" + (f"_c{concurrency}" if concurrency > 1 else "")
    out_path = ctx.out_dir / f"{name}.jsonl"
    done = set()
    if out_path.is_file():
        done = {json.loads(line)["id"] for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    db = _mongo(ctx)

    def chunk_text(ids_: list[str]) -> list[str]:
        from bson import ObjectId

        oids = []
        for i in ids_:
            try:
                oids.append(ObjectId(i))
            except Exception:  # noqa: BLE001 - non-ObjectId ids are skipped
                continue
        by_id = {str(c["_id"]): c.get("content", "") for c in db.chunks.find({"_id": {"$in": oids}}, {"content": 1})}
        return [by_id.get(i, "") for i in ids_]

    todo = [g for g in gold if g.id not in done and (ids is None or g.id in ids)]
    if limit:
        todo = todo[:limit]
    ollama_tail = tm.OllamaLogTail(ctx.ollama_log or tm.default_ollama_log())
    blobs = tm.ollama_model_blobs(ctx.ollama_url)
    run_info: dict[str, Any] = {"started_at": _now_ms(), "concurrency": concurrency, "questions": len(todo),
                                "ollama_version_start": (tm.ollama_get(ctx.ollama_url, "/api/version") or {}).get("version"),
                                "ollama_log": str(ollama_tail.path) if ollama_tail.path else None}
    run_log_offset = ollama_tail.offset()
    states_before = tm.container_states(list(ctx.containers))
    write_lock = threading.Lock()
    consecutive_errors = 0
    timeout = httpx.Timeout(ctx.timeout_s, read=ctx.timeout_s)

    def record(rec: dict[str, Any], n: int) -> None:
        with write_lock:
            tm.jsonl_append(out_path, rec)
        logger.info("[%d/%d] %s %s: %.0fs, stage=%s, degradations=%s, error=%s", n, len(todo), rec["id"], mode,
                    rec.get("seconds") or 0, rec.get("failure_stage"), rec.get("degradations"), rec.get("error"))

    with ResourceSampler(ctx.containers, ctx.sample_interval_s, out_path=ctx.out_dir / f"resources_{name}.jsonl", ollama_url=ctx.ollama_url,
                         health_url=ctx.base_url.rstrip("/") + "/health") as sampler, \
            tm.MongoProfiler(db, slow_ms=100) as profiler:
        if concurrency <= 1:
            with httpx.Client(timeout=timeout, headers=ctx.http_headers) as http:
                for n, g in enumerate(todo, start=1):
                    rec = _run_question(ctx, http, g, mode, chunk_text, keep_sessions=keep_sessions, slice_logs=True,
                                        ollama_tail=ollama_tail, blobs=blobs)
                    record(rec, n)
                    consecutive_errors = consecutive_errors + 1 if rec.get("error") else 0
                    if consecutive_errors >= ctx.max_consecutive_errors:
                        run_info["aborted"] = f"{consecutive_errors} consecutive errors, last: {rec.get('error')}"
                        logger.error("Stopping the run: %s", run_info["aborted"])
                        break
        else:
            def worker(g: GoldCase) -> dict[str, Any]:
                with httpx.Client(timeout=timeout, headers=ctx.http_headers) as http:
                    return _run_question(ctx, http, g, mode, chunk_text, keep_sessions=keep_sessions, slice_logs=False,
                                         ollama_tail=ollama_tail, blobs=blobs)

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(worker, g) for g in todo]
                for n, fut in enumerate(as_completed(futures), start=1):
                    record(fut.result(), n)
        resources = sampler.summary()
    run_info["ended_at"] = _now_ms()
    run_info["ollama_version_end"] = (tm.ollama_get(ctx.ollama_url, "/api/version") or {}).get("version")
    run_info["container_changes"] = tm.container_changes(states_before, tm.container_states(list(ctx.containers)))
    run_info["mongo_slow_ops"] = profiler.result
    text, rotated = ollama_tail.read_from(run_log_offset)
    run_info["ollama"] = tm.parse_ollama_log(text, blobs) | {"log_rotated": rotated}
    run_info["backend_log"] = tm.parse_backend_log(tm.docker_logs(ctx.backend_container, run_info["started_at"], run_info["ended_at"]))

    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    summary = summarize_chat(rows)
    summary["resources_last_batch"] = resources
    summary["run_last_batch"] = run_info
    try:
        summary["agent_log_last_batch"] = agent_log_stats(run_info["started_at"])
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not read the backend log for agent statistics: %s", exc)
    summary["anomalies"] = find_anomalies(summary, rows)
    (ctx.out_dir / f"{name}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    page = _write_telemetry(ctx, name)
    summary["telemetry_html"] = str(page) if page else None
    return summary


def summarize_chat(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate chat results: quality metrics, latency and per-phase timing."""
    ok = [r for r in rows if not r.get("error")]
    answerable = [r for r in ok if r["type"] != "unanswerable"]
    unans = [r for r in ok if r["type"] == "unanswerable"]

    def mean(key: str, items: list[dict[str, Any]]) -> Optional[float]:
        vals = [r[key] for r in items if isinstance(r.get(key), (int, float))]
        return round(statistics.fmean(vals), 3) if vals else None

    phases: dict[str, list[float]] = {}
    for r in ok:
        for p, s in (r.get("phases_s") or {}).items():
            phases.setdefault(p, []).append(s)
    by_type: dict[str, dict[str, Any]] = {}
    for t in sorted({r["type"] for r in ok}):
        items = [r for r in ok if r["type"] == t]
        by_type[t] = {"n": len(items), "answer_ok": mean("answer_ok", items), "must_include_all": mean("must_include_all", items),
                      "nugget_recall": mean("nugget_recall", items), "agent_hit@10": mean("agent_hit@10", items),
                      "agent_span_hit@10": mean("agent_span_hit@10", items), "abstained": mean("abstained", items),
                      "seconds_p50": _pct([r["seconds"] for r in items], 50)}
    return {
        "n": len(rows), "errors": len(rows) - len(ok),
        "answerable": {"n": len(answerable), "answer_ok": mean("answer_ok", answerable),
                       "must_include_all": mean("must_include_all", answerable), "nugget_recall": mean("nugget_recall", answerable),
                       "answers_truncated": sum(r.get("answer_truncated") or 0 for r in answerable),
                       "must_include_rate": mean("must_include_rate", answerable),
                       "must_not_violations": sum(1 for r in answerable if r.get("must_not_violations")),
                       "agent_hit@1": mean("agent_hit@1", answerable), "agent_hit@10": mean("agent_hit@10", answerable),
                       "agent_recall@10": mean("agent_recall@10", answerable), "agent_span_hit@10": mean("agent_span_hit@10", answerable),
                       "false_abstentions": mean("abstained", answerable)},
        "unanswerable": {"n": len(unans), "abstained": mean("abstained", unans)},
        "tools": {"expected_ok": mean("tool_expected_ok", ok), "forbidden_used": mean("tool_forbidden_used", ok),
                  "calls_mean": mean("tool_calls", ok), "boolean_query_rate": mean("boolean_query", ok),
                  "queries_total": sum(len(r.get("queries") or []) for r in ok),
                  "queries_empty": sum(r.get("queries_empty") or 0 for r in ok),
                  "queries_malformed": sum(r.get("queries_malformed") or 0 for r in ok),
                  "no_usable_query_rate": mean("no_usable_query", ok),
                  "name_drift_rate": round(statistics.fmean([int(bool(r.get("name_drift"))) for r in ok]), 3) if ok else None,
                  "tool_names": dict(sorted({t: sum(r["tools"].count(t) for r in ok) for r in ok for t in r["tools"]}.items()))},
        "citations_mean": mean("citations", ok),
        "latency_s": _summary([r["seconds"] for r in ok if r.get("seconds")]),
        "first_byte_s": _summary([r["first_byte_s"] for r in ok if r.get("first_byte_s")]),
        "phases_s": {p: _summary(v) for p, v in sorted(phases.items())},
        "effective_modes": dict(sorted({m: sum(1 for r in ok if r.get("effective_mode") == m) for m in {r.get("effective_mode") for r in ok}}.items(), key=lambda kv: str(kv[0]))),
        "by_type": by_type,
        "failure_stages": dict(collections.Counter(r.get("failure_stage") or "unknown" for r in rows).most_common()),
        "orchestrator_json": _orchestrator_json_health(ok),
        **telemetry_summary(rows),
    }


def telemetry_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-question telemetry: fallbacks, LLM budget use, Ollama, backend log, stability, isolation."""
    out: dict[str, Any] = {}
    deg = collections.Counter(d for r in rows for d in r.get("degradations") or [])
    out["degradations"] = {"questions_with_any": sum(1 for r in rows if r.get("degradations")), "by_name": dict(deg.most_common())}

    steps: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        for s in r.get("orchestrator_steps") or []:
            if "attempt" in s:
                steps.setdefault(str(s.get("phase")), []).append(s)
    if steps:
        def nums(items: list[dict[str, Any]], key: str) -> list[float]:
            return [float(x[key]) for x in items if isinstance(x.get(key), (int, float))]

        out["llm_steps"] = {
            phase: {"calls": len(v), "retries": sum(1 for x in v if (x.get("attempt") or 1) > 1),
                    "parse_errors": sum(x.get("parse_failed") or 0 for x in v),
                    "token_limit_hits": sum(x.get("hit_token_limit") or 0 for x in v),
                    "repetition": sum(x.get("repetition_detected") or 0 for x in v),
                    "trimmed": sum(x.get("truncated") or 0 for x in v),
                    "errors": sum(1 for x in v if x.get("error")),
                    "prompt_tokens": _summary(nums(v, "prompt_tokens")), "completion_tokens": _summary(nums(v, "completion_tokens")),
                    "hidden_tokens_est": _summary(nums(v, "hidden_tokens_est")), "seconds": _summary([d / 1000 for d in nums(v, "duration_ms")]),
                    "think": dict(collections.Counter(str((x.get("request_options") or {}).get("think")) for x in v))}
            for phase, v in sorted(steps.items())
        }

    oll = [r["ollama"] for r in rows if isinstance(r.get("ollama"), dict)]
    if oll:
        tot = {k: sum(o.get(k) or 0 for o in oll) for k in ("requests", "prefill_tokens", "decode_tokens", "truncated_prompts",
                                                          "evictions", "aborts", "error_count", "warnings")}
        prefill_s, decode_s = sum(o.get("prefill_s") or 0 for o in oll), sum(o.get("decode_s") or 0 for o in oll)
        statuses = collections.Counter()
        for o in oll:
            statuses.update(o.get("by_status") or {})
        loads = [ld for o in oll for ld in o.get("loads") or []]
        out["ollama"] = {**tot, "by_status": dict(statuses), "loads": len(loads),
                         "loads_by_model": dict(collections.Counter(f"{ld['model']} @ {ld['ctx']}" for ld in loads)),
                         "load_s": round(sum(o.get("load_s") or 0 for o in oll), 1),
                         "questions_with_model_load": sum(1 for o in oll if o.get("loads")),
                         "prefill_tok_s": round(tot["prefill_tokens"] / prefill_s, 1) if prefill_s else None,
                         "decode_tok_s": round(tot["decode_tokens"] / decode_s, 1) if decode_s else None,
                         "max_prompt_tokens": max(o.get("max_prompt_tokens") or 0 for o in oll),
                         "max_context_used": max(o.get("max_context_used") or 0 for o in oll),
                         "n_ctx": max(o.get("n_ctx") or 0 for o in oll),
                         "server_s": round(sum(o.get("server_s") or 0 for o in oll), 1),
                         "log_rotations": sum(1 for o in oll if o.get("log_rotated"))}

    blog = [r["backend_log"] for r in rows if isinstance(r.get("backend_log"), dict)]
    if blog:
        msgs: collections.Counter[str] = collections.Counter()
        exc: collections.Counter[str] = collections.Counter()
        for b in blog:
            msgs.update({m["message"]: m["count"] for m in b.get("messages") or []})
            exc.update(b.get("exceptions") or {})
        out["backend_log"] = {"warnings": sum(b["warnings"] for b in blog), "errors": sum(b["errors"] for b in blog),
                              "tracebacks": sum(b["tracebacks"] for b in blog), "exceptions": dict(exc.most_common(10)),
                              "top_messages": [{"message": m, "count": c} for m, c in msgs.most_common(15)]}

    timed = [r for r in rows if isinstance(r.get("seconds"), (int, float))]
    if len(timed) >= 6:
        third = len(timed) // 3
        p50 = _pct([r["seconds"] for r in timed], 50) or 0
        slow = sorted(timed, key=lambda r: -r["seconds"])[:5]
        out["stability"] = {
            "latency_first_third_mean_s": round(statistics.fmean(r["seconds"] for r in timed[:third]), 1),
            "latency_last_third_mean_s": round(statistics.fmean(r["seconds"] for r in timed[-third:]), 1),
            "outliers_over_2x_p50": [r["id"] for r in timed if r["seconds"] > 2 * p50],
            "slowest": [{"id": r["id"], "seconds": r["seconds"],
                         "slowest_phase": max((r.get("phases_s") or {"?": 0}).items(), key=lambda kv: kv[1])[0]} for r in slow],
        }
    out["failed_questions"] = [{"id": r["id"], "error": r.get("error")} for r in rows if r.get("error")]
    out["health_wait_s"] = round(sum(r.get("health_wait_s") or 0 for r in rows), 1)
    out["stream_anomalies"] = [r["id"] for r in rows if (r.get("stream") or {}).get("no_response_event") or (r.get("stream") or {}).get("error_events")]
    out["worker_failures"] = sum(len(r.get("worker_failures") or []) for r in rows)
    foreign = [r["foreign_source_share"] for r in rows if isinstance(r.get("foreign_source_share"), (int, float))]
    out["isolation"] = {"questions": len(foreign), "mean_foreign_source_share": round(statistics.fmean(foreign), 3) if foreign else None,
                        "questions_mostly_foreign": [r["id"] for r in rows if (r.get("foreign_source_share") or 0) > 0.5]}
    return out


def find_anomalies(summary: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    """Plain-language list of everything in a run that needs a look."""
    a: list[str] = []
    if summary.get("failed_questions"):
        a.append(f"{len(summary['failed_questions'])} questions failed: " + "; ".join(f"{e['id']}: {str(e['error'])[:80]}" for e in summary["failed_questions"][:5]))
    if summary.get("stream_anomalies"):
        a.append(f"Stream ended without an answer or with error events: {summary['stream_anomalies']}")
    for phase, s in (summary.get("llm_steps") or {}).items():
        for key, label in (("parse_errors", "unparsable JSON outputs"), ("retries", "JSON retries"), ("token_limit_hits", "outputs cut at max_tokens"),
                           ("trimmed", "outputs trimmed as loops"), ("errors", "failed LLM calls")):
            if s.get(key):
                a.append(f"{phase}: {s[key]} {label} of {s['calls']} calls")
    o = summary.get("ollama") or {}
    if o.get("truncated_prompts"):
        a.append(f"Ollama truncated {o['truncated_prompts']} prompts to fit the context")
    if o.get("loads"):
        a.append(f"Ollama loaded models {o['loads']} times ({o.get('load_s')} s): {o.get('loads_by_model')}; {o.get('evictions')} evictions")
    if any(s != "200" for s in (o.get("by_status") or {})):
        a.append(f"Ollama non-200 responses: {o.get('by_status')}")
    if o.get("aborts"):
        a.append(f"Ollama logged {o['aborts']} aborted/cancelled requests")
    if o.get("n_ctx") and o.get("max_context_used") and o["max_context_used"] > 0.9 * o["n_ctx"]:
        a.append(f"Context nearly full: {o['max_context_used']} of {o['n_ctx']} tokens")
    if o.get("log_rotations"):
        a.append("The Ollama server log rotated during the run (Ollama restarted?)")
    b = summary.get("backend_log") or {}
    if b.get("errors") or b.get("tracebacks"):
        a.append(f"Backend log: {b.get('errors')} errors, {b.get('tracebacks')} tracebacks, exceptions {b.get('exceptions')}")
    st = summary.get("stability") or {}
    if st and st["latency_last_third_mean_s"] > 1.3 * st["latency_first_third_mean_s"]:
        a.append(f"Slowdown over the run: {st['latency_first_third_mean_s']} s -> {st['latency_last_third_mean_s']} s mean latency")
    if st.get("outliers_over_2x_p50"):
        a.append(f"Latency outliers (> 2x median): {st['outliers_over_2x_p50']}")
    res = summary.get("resources_last_batch") or {}
    g = res.get("gpu") or {}
    if g.get("throttled_samples"):
        a.append(f"GPU clock held down in {g['throttled_samples']} samples, reasons {g.get('clock_reasons')}; max temp {(g.get('temp_c') or {}).get('max')} C")
    if g.get("thermal_s"):
        a.append(f"GPU thermal slowdown for {g['thermal_s']} s (clock limits by reason: {g.get('reasons_s')})")
    if g.get("tlimit_min_c") is not None and g["tlimit_min_c"] <= 3:
        a.append(f"GPU came within {g['tlimit_min_c']} C of its thermal slowdown limit")
    host = res.get("host") or {}
    if ((host.get("ram_pct") or {}).get("max") or 0) > 90:
        a.append(f"Host RAM reached {host['ram_pct']['max']} % (peak {(host.get('ram_used_gb') or {}).get('max')} GB)")
    if ((host.get("swap_used_gb") or {}).get("max") or 0) > 0.5:
        a.append(f"Host swap in use: up to {host['swap_used_gb']['max']} GB")
    if ((host.get("cpu_temp_c") or {}).get("max") or 0) > 90:
        a.append(f"Board thermal zone reached {host['cpu_temp_c']['max']} C")
    if res.get("probe_errors"):
        a.append(f"Telemetry probes failed: {res['probe_errors']}")
    hl = res.get("health") or {}
    if hl.get("failures"):
        a.append(f"Backend /health failed {hl['failures']} times while questions ran")
    if ((hl.get("ms") or {}).get("max") or 0) > 2000:
        a.append(f"Backend /health took up to {hl['ms']['max']} ms while questions ran (event loop blocked?)")
    if (res.get("ollama") or {}).get("samples_with_offload"):
        a.append(f"A model ran partly on the CPU in {res['ollama']['samples_with_offload']} samples")
    for name, v in res.items():
        if isinstance(v, dict) and (v.get("mem_drift_mb") or 0) > 300:
            a.append(f"{name} memory grew by {v['mem_drift_mb']} MB over the run")
    run = summary.get("run_last_batch") or {}
    if run.get("container_changes"):
        a.append("Containers: " + "; ".join(run["container_changes"]))
    if run.get("ollama_version_start") != run.get("ollama_version_end"):
        a.append(f"Ollama version changed during the run: {run.get('ollama_version_start')} -> {run.get('ollama_version_end')}")
    if run.get("aborted"):
        a.append(f"Run aborted: {run['aborted']}")
    slow_ops = (run.get("mongo_slow_ops") or {}).get("slow_ops")
    if slow_ops:
        a.append(f"MongoDB: {slow_ops} operations slower than {run['mongo_slow_ops'].get('slow_ms')} ms, {run['mongo_slow_ops'].get('by_kind')}")
    iso = summary.get("isolation") or {}
    if iso.get("questions_mostly_foreign"):
        a.append(f"Answers mostly sourced from another case: {iso['questions_mostly_foreign']}")
    deg = (summary.get("degradations") or {}).get("by_name") or {}
    if deg:
        a.append(f"Fallback paths taken: {deg}")
    if summary.get("worker_failures"):
        a.append(f"{summary['worker_failures']} worker tool calls failed")
    return a


def _orchestrator_json_health(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per phase: calls, JSON parse failures and repetition-guard truncations (rows recorded with step flags only)."""
    phases: dict[str, dict[str, int]] = {}
    for r in rows:
        for s in r.get("orchestrator_steps") or []:
            if "parse_failed" not in s:
                continue
            d = phases.setdefault(str(s.get("phase")), {"calls": 0, "parse_failed": 0, "truncated": 0})
            d["calls"] += 1
            d["parse_failed"] += s.get("parse_failed") or 0
            d["truncated"] += s.get("truncated") or 0
    return phases


# ─── A/B ─────────────────────────────────────────────────────────────────────


def sign_test_p(gained: int, lost: int) -> float:
    """Two-sided exact sign test on the questions that changed outcome."""
    n = gained + lost
    if n == 0:
        return 1.0
    k = min(gained, lost)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def _synth_steps(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in row.get("orchestrator_steps") or [] if s.get("phase") == "synthesize"]


def compare_runs(bench_root: Path, a: str, b: str, modes: tuple[str, ...] = ("thinking", "fast")) -> dict[str, Any]:
    """Paired comparison of two benchmark runs, question by question.

    For each mode present in both runs: correct answers in A and B, questions gained and
    lost with an exact sign test, the same restricted to questions whose retrieval outcome
    (gold passage retrieved or not) was identical in both runs, latency of the whole
    answer and of the answer-writing phase, answer length, hidden tokens and budget hits.
    Writes ``ab_<a>_vs_<b>.json`` into run B.
    """
    out: dict[str, Any] = {"a": a, "b": b, "modes": {}}
    for mode in modes:
        pa, pb = bench_root / a / f"chat_{mode}.jsonl", bench_root / b / f"chat_{mode}.jsonl"
        if not (pa.is_file() and pb.is_file()):
            continue
        ra = {r["id"]: r for r in (json.loads(x) for x in pa.read_text(encoding="utf-8").splitlines() if x.strip())}
        rb = {r["id"]: r for r in (json.loads(x) for x in pb.read_text(encoding="utf-8").splitlines() if x.strip())}
        ids = [i for i in ra if i in rb and ra[i].get("answer_ok") is not None and rb[i].get("answer_ok") is not None]
        gained = [i for i in ids if ra[i]["answer_ok"] == 0 and rb[i]["answer_ok"] == 1]
        lost = [i for i in ids if ra[i]["answer_ok"] == 1 and rb[i]["answer_ok"] == 0]
        same_retrieval = [i for i in ids if ra[i].get("agent_span_hit@10") == rb[i].get("agent_span_hit@10")]
        g2 = [i for i in gained if i in same_retrieval]
        l2 = [i for i in lost if i in same_retrieval]

        def mean(rows: dict[str, dict[str, Any]], fn: Callable[[dict[str, Any]], Any]) -> Optional[float]:
            vals = [v for v in (fn(rows[i]) for i in ids) if isinstance(v, (int, float))]
            return round(statistics.fmean(vals), 3) if vals else None

        def synth_s(r: dict[str, Any]) -> Optional[float]:
            phases = r.get("phases_s") or {}
            return phases.get("synthesize", phases.get("fast_synthesize"))

        def side(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
            return {
                "correct": mean(rows, lambda r: r["answer_ok"] if r["type"] != "unanswerable" else None),
                "unanswerable_declined": mean(rows, lambda r: r["answer_ok"] if r["type"] == "unanswerable" else None),
                "false_abstentions": mean(rows, lambda r: r.get("abstained") if r["type"] != "unanswerable" else None),
                "draft_values": sum(1 for i in ids if rows[i].get("must_not_violations")),
                "latency_s": mean(rows, lambda r: r.get("seconds")),
                "latency_p50_s": _pct([rows[i]["seconds"] for i in ids if rows[i].get("seconds")], 50),
                "answer_phase_s": mean(rows, synth_s),
                "answer_chars": mean(rows, lambda r: len(r.get("answer") or "")),
                "citations": mean(rows, lambda r: r.get("citations")),
                "hidden_tokens": mean(rows, lambda r: sum(s.get("hidden_tokens_est") or 0 for s in _synth_steps(r)) if _synth_steps(r) else None),
                "budget_hits": sum(1 for i in ids for s in _synth_steps(rows[i]) if s.get("hit_token_limit")),
                "decode_tokens": mean(rows, lambda r: (r.get("ollama") or {}).get("decode_tokens")),
            }

        out["modes"][mode] = {
            "n": len(ids), "a": side(ra), "b": side(rb),
            "gained": gained, "lost": lost, "p_sign_test": round(sign_test_p(len(gained), len(lost)), 3),
            "same_retrieval": {"n": len(same_retrieval), "gained": g2, "lost": l2, "p_sign_test": round(sign_test_p(len(g2), len(l2)), 3)},
            "by_type": {t: {"n": sum(1 for i in ids if ra[i]["type"] == t),
                            "a": round(statistics.fmean(ra[i]["answer_ok"] for i in ids if ra[i]["type"] == t), 3),
                            "b": round(statistics.fmean(rb[i]["answer_ok"] for i in ids if rb[i]["type"] == t), 3)}
                        for t in sorted({ra[i]["type"] for i in ids})},
        }
    (bench_root / b / f"ab_{a}_vs_{b}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
