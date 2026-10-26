#!/usr/bin/env python
"""Periodic prompt test against freshly ingested documents of the live Quellex stack.

Run while an ingestion job is in progress (for example every three hours):

    uv run python scripts/ingestion-prompt-test/prompt_test.py tick

Each ``tick``

1. reads the live job status from the API and compares it with the previous tick,
2. inspects the documents inserted since the previous tick (sizes, extensions,
   folders, duplicates, suspect OCR, re-ingested files),
3. scans the backend and worker container logs for errors since the previous tick,
4. runs retrieval retention probes (a distinctive sentence of a new document is
   used as the query; the document should come back on top),
5. sends a few chat prompts through the sessions API and records answer,
   sources, latency and simple quality checks,
6. writes ``tick-NN.json`` and ``tick-NN.md`` into the state directory and
   prints the markdown summary.

Other subcommands: ``ask`` (one chat prompt), ``search`` (one search),
``sample`` (snippets of recent documents to craft questions from) and ``doc``
(print one document).

Authentication: ``X-API-Key`` read from ``<state-dir>/api-key.txt`` (or
``--key-file`` / ``PROMPT_TEST_API_KEY``). Nothing in this script is tenant
specific beyond the defaults; override ``--api``, ``--mongo`` and ``--db``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import random
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from typing import Any, Optional

import bson
import httpx
from pymongo import MongoClient

DEFAULT_API = "http://127.0.0.1:11001/api/v1"
DEFAULT_MONGO = "mongodb://127.0.0.1:11017/?directConnection=true"
DEFAULT_DB = "rag_diejuristin"
DEFAULT_STATE_DIR = r"C:\RecallHub-Verify\bench\prompt-test-2026-10-24"
CONTAINERS = ("rag-backend-quellex", "rag-ingestion-worker")
UTC = dt.timezone.utc

DE_WORDS = {"der", "die", "das", "und", "nicht", "ist", "ein", "eine", "mit", "für", "von", "zu", "im",
            "auf", "dem", "den", "wird", "sind", "werden", "bei", "nach", "dokument", "keine", "wurde"}
EN_WORDS = {"the", "and", "is", "of", "to", "in", "that", "with", "for", "are", "this", "document",
            "not", "it", "as", "on", "was", "there", "no", "information"}
REFUSAL_PATTERNS = [
    r"keine (informationen|dokumente|unterlagen|angaben|hinweise)", r"nicht (gefunden|finden)",
    r"konnte ich (keine|nicht)", r"liegen (mir )?keine", r"no (information|documents?) (found|available)",
    r"could not find", r"i (don't|do not) have", r"not (available|contained) in",
]
GENERIC_TITLES = {"grundbuchauszug", "grundbuchsauszug", "firmenbuchauszug", "kontoauszug", "beilage",
                  "scan", "dokument", "document", "unbenannt", "img", "image"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def utcnow() -> dt.datetime:
    return dt.datetime.now(UTC)


def iso(d: dt.datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def oid_at(d: dt.datetime) -> bson.ObjectId:
    return bson.ObjectId.from_datetime(d)


def truncate(s: Any, n: int = 160) -> str:
    s = str(s).replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


MARKDOWN_NOISE = re.compile(r"<!--.*?-->|[|#*_=\-–—]{1,}|\[\\?_+\]")


def text_quality(text: str) -> Optional[dict]:
    """Cheap OCR-quality heuristics for a piece of text (markdown table/image noise removed first)."""
    cleaned = MARKDOWN_NOISE.sub(" ", text)
    toks = re.findall(r"[^\W\d_]+", cleaned)
    if not toks:
        return None
    long_toks = [t for t in toks if len(t) >= 5]
    vowelless = sum(1 for t in long_toks if not re.search(r"[aeiouyäöüAEIOUYÄÖÜ]", t))
    nonspace = max(1, len(re.sub(r"\s", "", cleaned)))
    alnum = len(re.findall(r"[^\W_]", cleaned))
    short = sum(1 for t in toks if len(t) <= 2)
    return {
        "tokens": len(toks),
        "alpha_ratio": round(alnum / nonspace, 3),
        "vowelless_ratio": round(vowelless / max(1, len(long_toks)), 3),
        "short_ratio": round(short / len(toks), 3),
        "replacement_chars": text.count("�"),
    }


def is_suspect(q: Optional[dict]) -> bool:
    return bool(q) and q["tokens"] >= 30 and (
        q["vowelless_ratio"] > 0.2 or q["alpha_ratio"] < 0.6 or q["short_ratio"] > 0.45
        or q.get("replacement_chars", 0) > 20
    )


def guess_language(text: str) -> str:
    words = re.findall(r"[a-zäöüß]+", text.lower())
    de = sum(1 for w in words if w in DE_WORDS)
    en = sum(1 for w in words if w in EN_WORDS)
    if de == en == 0:
        return "unknown"
    return "de" if de >= en else "en"


def looks_like_refusal(text: str) -> bool:
    t = text.lower()
    return any(re.search(p, t) for p in REFUSAL_PATTERNS)


def basename(source: str) -> str:
    return source.replace("\\", "/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# context / state
# ---------------------------------------------------------------------------

class Ctx:
    def __init__(self, args: argparse.Namespace) -> None:
        self.state_dir = pathlib.Path(args.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        key = os.environ.get("PROMPT_TEST_API_KEY")
        key_file = pathlib.Path(args.key_file) if args.key_file else self.state_dir / "api-key.txt"
        if not key and key_file.exists():
            key = key_file.read_text(encoding="utf-8").strip()
        if not key:
            sys.exit(f"No API key: set PROMPT_TEST_API_KEY or create {key_file}")
        self.http = httpx.Client(base_url=args.api, headers={"X-API-Key": key},
                                 timeout=httpx.Timeout(900, connect=10))
        self.dbh = MongoClient(args.mongo, serverSelectionTimeoutMS=5000)[args.db]
        self.args = args

    @property
    def state_file(self) -> pathlib.Path:
        return self.state_dir / "state.json"

    def load_state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {"tick": 0, "last_tick_at": None, "last_status": None}

    def save_state(self, state: dict) -> None:
        self.state_file.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def api_get(ctx: Ctx, path: str) -> dict:
    try:
        r = ctx.http.get(path)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}: {truncate(r.text, 200)}"}
        return r.json()
    except Exception as exc:  # noqa: BLE001 - report, do not crash the tick
        return {"error": f"{type(exc).__name__}: {exc}"}


def search(ctx: Ctx, query: str, search_type: str = "hybrid", n: int = 10) -> dict:
    t0 = time.perf_counter()
    try:
        r = ctx.http.post(f"/search/{search_type}", json={"query": query, "match_count": n})
        if r.status_code == 429:  # api_search allows 30 requests/min per client; wait once and retry
            wait = 5
            try:
                wait = int(r.json().get("retry_after") or wait)
            except Exception:  # noqa: BLE001
                pass
            print(f"[search] rate limited, sleeping {wait + 1}s", file=sys.stderr)
            time.sleep(wait + 1)
            r = ctx.http.post(f"/search/{search_type}", json={"query": query, "match_count": n})
        ms = round((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}: {truncate(r.text, 200)}", "ms": ms, "results": []}
        j = r.json()
        return {"ms": ms, "results": j.get("results", []), "server_ms": j.get("processing_time_ms")}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "ms": round((time.perf_counter() - t0) * 1000),
                "results": []}


def chat(ctx: Ctx, question: str, mode: Optional[str] = None, language: Optional[str] = "de",
         expect: Optional[str] = None, title: str = "prompt-test", keep_session: bool = False) -> dict:
    """Send one question through a fresh chat session and evaluate the answer."""
    rec: dict[str, Any] = {"question": question, "mode": mode or "default", "expect": expect}
    t0 = time.perf_counter()
    session_id = None
    try:
        s = ctx.http.post("/sessions", json={"title": title})
        if s.status_code != 200:
            rec["error"] = f"create session HTTP {s.status_code}: {truncate(s.text, 200)}"
            return rec
        sj = s.json()
        session_id = sj.get("id") or sj.get("_id") or sj.get("session_id")
        rec["session_id"] = session_id
        body: dict[str, Any] = {"content": question, "include_sources": True}
        if mode:
            body["agent_mode"] = mode
        if language:
            body["language"] = language
        r = ctx.http.post(f"/sessions/{session_id}/messages", json=body)
        rec["http_status"] = r.status_code
        rec["ms"] = round((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            rec["error"] = f"HTTP {r.status_code}: {truncate(r.text, 300)}"
            return rec
        j = r.json()
        am = j.get("assistant_message") or {}
        answer = am.get("content") or ""
        sources = am.get("sources") or []
        rec["answer"] = answer
        rec["answer_chars"] = len(answer)
        rec["sources"] = [{k: v for k, v in s.items() if k in ("title", "source", "score", "similarity", "document_id")}
                          for s in sources][:10]
        rec["n_sources"] = len(sources)
        rec["stats"] = am.get("stats")
        rec["model"] = am.get("model")
        thinking = am.get("thinking") or {}
        ops = ((thinking.get("search") or {}).get("operations")) or []
        rec["search_ops"] = [{k: op.get(k) for k in ("index_type", "query", "results_count", "duration_ms", "top_score")}
                             for op in ops][:12]
        tr = j.get("agent_trace") or {}
        if tr:
            rec["trace"] = {k: tr.get(k) for k in ("mode", "models", "iterations", "timing", "tokens")}
            rec["trace"]["orchestrator_steps"] = len(tr.get("orchestrator_steps") or [])
            rec["trace"]["worker_steps"] = len(tr.get("worker_steps") or [])
        src_text = " ".join(f"{s.get('title', '')} {s.get('source', '')}" for s in sources)
        rec["checks"] = {
            "empty_answer": len(answer.strip()) < 20,
            "no_sources": len(sources) == 0,
            "language": guess_language(answer),
            "refusal": looks_like_refusal(answer),
            "expect_in_answer": (expect.lower() in answer.lower()) if expect else None,
            "expect_in_sources": (expect.lower() in src_text.lower()) if expect else None,
        }
        return rec
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["ms"] = round((time.perf_counter() - t0) * 1000)
        return rec
    finally:
        if session_id and not keep_session:
            try:
                ctx.http.delete(f"/sessions/{session_id}")
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# database analysis
# ---------------------------------------------------------------------------

def analyse_new_docs(ctx: Ctx, since: dt.datetime, until: dt.datetime, sample_n: int = 300) -> dict:
    db = ctx.dbh
    match = {"_id": {"$gte": oid_at(since), "$lt": oid_at(until)}}
    out: dict[str, Any] = {"window": [iso(since), iso(until)]}
    out["new_docs"] = db.documents.count_documents(match)
    out["new_chunks"] = db.chunks.count_documents(match)
    out["total_docs"] = db.documents.estimated_document_count()
    out["total_chunks"] = db.chunks.estimated_document_count()
    out["reingested_in_place"] = db.documents.count_documents(
        {"_id": {"$lt": oid_at(since)}, "metadata.ingestion_date": {"$gte": since.strftime("%Y-%m-%dT%H:%M:%S")}}
    )

    def agg(pipe: list[dict]) -> list[dict]:
        return list(db.documents.aggregate([{"$match": match}, *pipe], allowDiskUse=True))

    out["by_ext"] = [(r["_id"], r["n"]) for r in agg([
        {"$project": {"ext": {"$toLower": {"$arrayElemAt": [{"$split": ["$source", "."]}, -1]}}}},
        {"$group": {"_id": "$ext", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 12}])]
    out["by_top_folder"] = [(r["_id"], r["n"]) for r in agg([
        {"$project": {"top": {"$arrayElemAt": [{"$split": ["$source", "/"]}, 0]}}},
        {"$group": {"_id": "$top", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 12}])]
    out["content_buckets"] = [(r["_id"], r["n"]) for r in agg([
        {"$project": {"l": {"$strLenCP": "$content"}}},
        {"$bucket": {"groupBy": "$l", "boundaries": [0, 1, 50, 200, 1000, 5000, 20000, 100000, 10 ** 9],
                     "default": "other", "output": {"n": {"$sum": 1}}}}])]
    out["zero_chunk_docs"] = db.documents.count_documents({**match, "metadata.chunks_count": 0})
    out["tiny_docs"] = [
        {"source": d["source"], "chars": len(d["content"]), "snippet": truncate(d["content"], 100)}
        for d in db.documents.find({**match, "$expr": {"$lt": [{"$strLenCP": "$content"}, 200]}},
                                   {"source": 1, "content": 1}).limit(10)
    ]
    dup = agg([{"$group": {"_id": "$content_hash", "n": {"$sum": 1}, "srcs": {"$push": "$source"}}},
               {"$match": {"n": {"$gt": 1}}}, {"$sort": {"n": -1}}, {"$limit": 5}])
    dup_tot = agg([{"$group": {"_id": "$content_hash", "n": {"$sum": 1}}}, {"$match": {"n": {"$gt": 1}}},
                   {"$group": {"_id": None, "groups": {"$sum": 1}, "extra": {"$sum": {"$subtract": ["$n", 1]}}}}])
    out["dup_groups"] = dup_tot[0]["groups"] if dup_tot else 0
    out["dup_extra_docs"] = dup_tot[0]["extra"] if dup_tot else 0
    out["dup_examples"] = [{"n": r["n"], "sources": [truncate(s, 90) for s in r["srcs"][:3]]} for r in dup]
    cs = list(db.chunks.aggregate([{"$match": match}, {"$group": {
        "_id": None, "n": {"$sum": 1}, "avg_tokens": {"$avg": "$token_count"}, "max_tokens": {"$max": "$token_count"},
        "over_512": {"$sum": {"$cond": [{"$gt": ["$token_count", 512]}, 1, 0]}},
        "under_20": {"$sum": {"$cond": [{"$lt": ["$token_count", 20]}, 1, 0]}}}}]))
    out["chunk_stats"] = ({k: (round(v, 1) if isinstance(v, float) else v) for k, v in cs[0].items() if k != "_id"}
                          if cs else {})
    sample = list(db.documents.aggregate([
        {"$match": {**match, "$expr": {"$gte": [{"$strLenCP": "$content"}, 200]}}},
        {"$sample": {"size": sample_n}}, {"$project": {"source": 1, "content": 1}}]))
    suspects = []
    for d in sample:
        q = text_quality(d["content"][:6000])
        if is_suspect(q):
            suspects.append({"source": d["source"], **q, "snippet": truncate(d["content"], 120)})
    out["quality_sampled"] = len(sample)
    out["suspect_ocr"] = len(suspects)
    out["suspect_examples"] = suspects[:8]
    out["_sample_docs"] = sample  # internal, used for probes
    return out


def pick_probes(content: str, limit: int = 3) -> list[str]:
    """Candidate probe sentences, most distinctive first (digits and long words score higher)."""
    cands = []
    for line in content.splitlines():
        line = line.strip()
        if not line or "http" in line or "|" in line or line.startswith("#"):
            continue
        words = line.split()
        if not 8 <= len(words) <= 28:
            continue
        q = text_quality(line)
        if not q or q["alpha_ratio"] < 0.7 or is_suspect({**q, "tokens": 30}):
            continue
        distinct_long = len({w.lower().strip(".,;:()\"'") for w in words if len(w) >= 6})
        has_digits = 1 if re.search(r"\d", line) else 0
        cands.append((distinct_long + 2 * has_digits, line))
    cands.sort(key=lambda c: c[0], reverse=True)
    return [c[1][:300] for c in cands[:limit]]


def is_boilerplate(ctx: Ctx, probe: str) -> bool:
    """True when the exact sentence occurs in several different documents (form text, footers)."""
    res = search(ctx, probe, "text", 10)
    if res.get("error"):
        return False
    needle = probe.lower()[:80]
    owners = {r.get("document_id") for r in res["results"] if needle in (r.get("content") or "").lower()}
    return len(owners) >= 4


def run_probes(ctx: Ctx, docs: list[dict], n: int) -> dict:
    """Retrieval retention: can a distinctive sentence of a new document find that document?"""
    db = ctx.dbh
    random.shuffle(docs)
    probes = []
    boilerplate_skipped = 0
    for d in docs:
        if len(probes) >= n:
            break
        probe = None
        for cand in pick_probes(d["content"]):
            if is_boilerplate(ctx, cand):
                boilerplate_skipped += 1
                continue
            probe = cand
            break
        if not probe:
            continue
        full = db.documents.find_one({"_id": d["_id"]}, {"content_hash": 1, "title": 1})
        dup_sources = {x["source"] for x in db.documents.find({"content_hash": full["content_hash"]}, {"source": 1})}
        rec: dict[str, Any] = {"document_id": str(d["_id"]), "source": d["source"], "title": full.get("title"),
                               "probe": probe, "dup_sources": len(dup_sources)}
        for st in ("hybrid", "semantic"):
            res = search(ctx, probe, st, 10)
            rank = next((i + 1 for i, r in enumerate(res["results"]) if r.get("document_id") == str(d["_id"])), None)
            dup_rank = next((i + 1 for i, r in enumerate(res["results"]) if r.get("document_source") in dup_sources), None)
            rec[st] = {"rank": rank, "dup_rank": dup_rank, "ms": res["ms"], "error": res.get("error"),
                       "top1": truncate(res["results"][0].get("document_source", ""), 90) if res["results"] else None}
        probes.append(rec)
    summary = {}
    for st in ("hybrid", "semantic"):
        ranks = [p[st]["dup_rank"] for p in probes if not p[st].get("error")]
        summary[st] = {
            "n": len(ranks),
            "hit@1": sum(1 for r in ranks if r == 1),
            "hit@5": sum(1 for r in ranks if r and r <= 5),
            "hit@10": sum(1 for r in ranks if r),
            "median_ms": statistics.median([p[st]["ms"] for p in probes]) if probes else None,
            "errors": sum(1 for p in probes if p[st].get("error")),
        }
    summary["boilerplate_skipped"] = boilerplate_skipped
    return {"summary": summary, "probes": probes}


# ---------------------------------------------------------------------------
# container logs
# ---------------------------------------------------------------------------

LOG_PATTERNS = [
    ("timeout", re.compile(r"TIMEOUT \(attempt (\d)/(\d)\) processing (.+?): (\d+)s elapsed")),
    ("osd_failed", re.compile(r"OSD failed")),
    ("no_chunks", re.compile(r"No chunks created for (.+)$")),
    ("traceback", re.compile(r"^Traceback \(most recent call last\)")),
    ("http_5xx", re.compile(r'" 5\d\d ')),
    ("embedding_error", re.compile(r"embedd\w* .*(error|fail)", re.I)),
    ("mongo_error", re.compile(r"(pymongo|OperationFailure|ServerSelectionTimeout|DuplicateKey)", re.I)),
    ("image_only", re.compile(r"image-only|image only", re.I)),
    ("skipped", re.compile(r"Skipp(ed|ing) (file|document)", re.I)),
]
NORMALIZE = [(re.compile(r"^\S+ \S+ - "), ""), (re.compile(r"/app/mounts/\S+"), "<path>"),
             (re.compile(r"0x[0-9a-f]+"), "0x#"), (re.compile(r"\d+"), "#")]


def container_stats() -> list[str]:
    """One-line CPU / memory / PID snapshot per stack container (docker stats)."""
    try:
        p = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}} pids={{.PIDs}}",
                            *CONTAINERS, "rag-mongodb", "rag-embedder-gpu"], capture_output=True, text=True, timeout=60)
        return [ln for ln in p.stdout.splitlines() if ln.strip()]
    except Exception as exc:  # noqa: BLE001
        return [f"docker stats failed: {type(exc).__name__}: {exc}"]


def scan_logs(container: str, since: dt.datetime) -> dict:
    try:
        p = subprocess.run(["docker", "logs", "--since", iso(since), container], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=180)
        lines = (p.stdout + p.stderr).splitlines()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    counts: Counter = Counter()
    examples: dict[str, list[str]] = {}
    err_msgs: Counter = Counter()
    timeouts: list[dict] = []
    for ln in lines:
        matched = False
        for name, rx in LOG_PATTERNS:
            m = rx.search(ln)
            if m:
                matched = True
                counts[name] += 1
                examples.setdefault(name, [])
                if len(examples[name]) < 2:
                    examples[name].append(truncate(ln, 220))
                if name == "timeout":
                    timeouts.append({"attempt": m.group(1), "file": basename(m.group(3)), "s": int(m.group(4))})
        if not matched and (" - ERROR - " in ln or " - WARNING - " in ln or "ERROR:" in ln):
            level = "error" if "ERROR" in ln else "warning"
            counts[f"other_{level}"] += 1
            msg = ln
            for rx, rep in NORMALIZE:
                msg = rx.sub(rep, msg)
            err_msgs[truncate(msg, 150)] += 1
    return {
        "lines": len(lines),
        "counts": dict(counts),
        "examples": examples,
        "top_messages": err_msgs.most_common(12),
        "timeout_files": timeouts[-15:],
        "timeout_unique_files": len({t["file"] for t in timeouts}),
    }


# ---------------------------------------------------------------------------
# tick
# ---------------------------------------------------------------------------

def default_chat_prompts(probe_docs: list[dict]) -> list[dict]:
    prompts = [{"question": "Welche Unterlagen gibt es zur ALPO CONSULT GmbH (FN 404945h) und was ist ihr Geschäftszweig?",
                "expect": "ALPO", "kind": "control"}]
    hits = [p for p in probe_docs if p["hybrid"].get("dup_rank")]
    if hits:
        p = hits[0]
        prompts.append({"question": "In welchem Dokument steht der folgende Satz und worum geht es in diesem Dokument? "
                                    f"\"{p['probe']}\"",
                        "expect": basename(p["source"]).rsplit(".", 1)[0][:25], "kind": "sentence-lookup"})
    first_hit = hits[0] if hits else None
    distinctive = [p for p in probe_docs if p.get("title") and len(p["title"]) >= 12
                   and p["title"].lower().split()[0].strip(",.;:") not in GENERIC_TITLES
                   and not re.match(r"^(ez|kg|fn|tz)\b", p["title"].lower()) and p is not first_hit]
    if distinctive:
        p = distinctive[0]
        prompts.append({"question": f"Fasse das Dokument \"{p['title']}\" zusammen: Worum geht es, wer sind die "
                                    "Beteiligten, welche Daten und Beträge werden genannt?",
                        "expect": p["title"][:20], "kind": "title-summary"})
    return prompts


def render_md(t: dict) -> str:
    st, prev = t["status"], t.get("status_delta") or {}
    L = [f"# Prompt test tick {t['tick']} — {t['at']}", ""]
    L.append(f"Window: {t['window'][0]} → {t['window'][1]} ({t['window_hours']:.2f} h)")
    L.append("")
    L.append("## Ingestion job")
    if "error" in st:
        L.append(f"- status error: {st['error']}")
    else:
        L.append(f"- {st.get('status')} / {st.get('phase')}: {st.get('processed_files')} of {st.get('total_files')} files "
                 f"({st.get('progress_percent', 0):.2f} %), documents {st.get('document_count')}, chunks {st.get('chunks_created')}, "
                 f"failed {st.get('failed_files')}, rate {st.get('processing_rate')} files/min, "
                 f"elapsed {round((st.get('elapsed_seconds') or 0) / 3600, 1)} h, "
                 f"ETA {round((st.get('estimated_remaining_seconds') or 0) / 86400, 1)} d")
        if prev and (prev.get("processed_files") or 0) < 0:
            L.append("- job counters are lower than at the last tick: the job was restarted or resumed in between")
        elif prev:
            L.append(f"- since last tick: +{prev.get('processed_files')} files, +{prev.get('document_count')} documents "
                     f"(job counter), +{prev.get('chunks_created')} chunks (job counter)")
        L.append(f"- current file: {truncate(st.get('current_file'), 120)}")
        if st.get("errors"):
            L.append(f"- job errors ({len(st['errors'])}): {truncate(st['errors'][:3], 300)}")
    f = t.get("failed_summary") or {}
    L.append(f"- failed-documents summary: {truncate(f, 200)}")
    d = t["db"]
    L.append("")
    L.append("## Database (documents inserted in the window)")
    L.append(f"- new documents {d['new_docs']}, new chunks {d['new_chunks']} (totals {d['total_docs']} / {d['total_chunks']}); "
             f"re-ingested in place {d['reingested_in_place']}; zero-chunk docs {d['zero_chunk_docs']}")
    L.append(f"- by extension: {d['by_ext']}")
    L.append(f"- by top folder: {d['by_top_folder'][:8]}")
    L.append(f"- content length buckets (lower bound → n): {d['content_buckets']}")
    L.append(f"- chunk stats: {d['chunk_stats']}")
    L.append(f"- duplicate content: {d['dup_groups']} groups, {d['dup_extra_docs']} redundant documents; e.g. {d['dup_examples'][:2]}")
    L.append(f"- suspect OCR: {d['suspect_ocr']} of {d['quality_sampled']} sampled")
    for s in d["suspect_examples"][:5]:
        L.append(f"  - {truncate(s['source'], 80)} (alpha {s['alpha_ratio']}, vowelless {s['vowelless_ratio']}, "
                 f"short {s['short_ratio']}): {s['snippet']}")
    for s in d["tiny_docs"][:5]:
        L.append(f"  - tiny ({s['chars']} chars): {truncate(s['source'], 80)}: {s['snippet']}")
    L.append("")
    L.append("## Containers")
    for ln in t.get("container_stats") or []:
        L.append(f"- {ln}")
    L.append("")
    L.append("## Container logs since last tick")
    for name, lg in t["logs"].items():
        if "error" in lg:
            L.append(f"- {name}: {lg['error']}")
            continue
        L.append(f"- {name}: {lg['lines']} lines; {lg['counts']}; distinct timeout files {lg['timeout_unique_files']}")
        for msg, n in lg["top_messages"][:6]:
            L.append(f"  - {n}× {msg}")
        for name2, exs in list(lg["examples"].items())[:6]:
            L.append(f"  - e.g. {name2}: {exs[0]}")
    L.append("")
    p = t["probes"]["summary"]
    L.append("## Retrieval retention probes")
    for st_name in ("hybrid", "semantic"):
        s = p[st_name]
        L.append(f"- {st_name}: n={s['n']} hit@1={s['hit@1']} hit@5={s['hit@5']} hit@10={s['hit@10']} "
                 f"median {s['median_ms']} ms, errors {s['errors']}")
    L.append(f"- boilerplate sentences skipped while choosing probes: {p.get('boilerplate_skipped', 0)}")
    for pr in t["probes"]["probes"]:
        h, se = pr["hybrid"], pr["semantic"]
        flag = "" if (h.get("dup_rank") or 99) <= 5 else "  **MISS**"
        L.append(f"  - hyb {h.get('dup_rank')}/sem {se.get('dup_rank')} ({pr['dup_sources']} copies) "
                 f"{truncate(pr['source'], 70)} ← “{truncate(pr['probe'], 90)}”{flag}")
        if flag:
            L.append(f"    top1 hybrid: {h.get('top1')}; top1 semantic: {se.get('top1')}")
    L.append("")
    L.append("## Chat prompts")
    for c in t["chat"]:
        if c.get("error"):
            L.append(f"- [{c.get('kind')}] ERROR after {c.get('ms')} ms: {c['error']} — {truncate(c['question'], 100)}")
            continue
        ck = c["checks"]
        tr = c.get("trace") or {}
        L.append(f"- [{c.get('kind')}] {c['ms'] / 1000:.0f} s, {c['answer_chars']} chars, {c['n_sources']} sources, "
                 f"lang {ck['language']}, refusal {ck['refusal']}, expect in answer/sources "
                 f"{ck['expect_in_answer']}/{ck['expect_in_sources']}, iterations {tr.get('iterations')}, "
                 f"tokens {truncate(tr.get('tokens'), 80)}")
        L.append(f"  - Q: {truncate(c['question'], 160)}")
        L.append(f"  - A: {truncate(c['answer'], 400)}")
        L.append(f"  - sources: {[truncate(s.get('source') or s.get('title'), 60) for s in c['sources'][:5]]}")
    return "\n".join(L)


def cmd_tick(ctx: Ctx) -> None:
    args = ctx.args
    state = ctx.load_state()
    now = utcnow()
    if args.since:
        since = parse_iso(args.since)
    elif state.get("last_tick_at"):
        since = parse_iso(state["last_tick_at"])
    else:
        since = now - dt.timedelta(hours=args.window_hours)
    tick_no = state["tick"] + 1
    t: dict[str, Any] = {"tick": tick_no, "at": iso(now), "window": [iso(since), iso(now)],
                         "window_hours": (now - since).total_seconds() / 3600}
    print(f"[tick {tick_no}] window {t['window'][0]} → {t['window'][1]}", file=sys.stderr)
    t["status"] = api_get(ctx, "/ingestion/status")
    t["failed_summary"] = api_get(ctx, "/ingestion/failed-documents/summary")
    prev = state.get("last_status") or {}
    if prev and "error" not in t["status"]:
        t["status_delta"] = {k: (t["status"].get(k) or 0) - (prev.get(k) or 0)
                             for k in ("processed_files", "document_count", "chunks_created")}
    print("[tick] analysing database …", file=sys.stderr)
    t["db"] = analyse_new_docs(ctx, since, now)
    sample_docs = t["db"].pop("_sample_docs")
    print("[tick] scanning container logs …", file=sys.stderr)
    t["container_stats"] = container_stats()
    t["logs"] = {c: scan_logs(c, since) for c in CONTAINERS}
    print(f"[tick] retrieval probes ({args.probes}) …", file=sys.stderr)
    t["probes"] = run_probes(ctx, sample_docs, args.probes)
    t["chat"] = []
    if not args.no_chat:
        prompts = default_chat_prompts(t["probes"]["probes"])
        if args.chat_prompts:
            prompts = json.loads(pathlib.Path(args.chat_prompts).read_text(encoding="utf-8"))
        for i, p in enumerate(prompts[: args.chat]):
            print(f"[tick] chat {i + 1}/{min(len(prompts), args.chat)}: {truncate(p['question'], 80)}", file=sys.stderr)
            rec = chat(ctx, p["question"], mode=p.get("mode") or args.mode, expect=p.get("expect"),
                       title=f"prompt-test tick {tick_no}")
            rec["kind"] = p.get("kind", "custom")
            t["chat"].append(rec)
    out_json = ctx.state_dir / f"tick-{tick_no:02d}.json"
    out_json.write_text(json.dumps(t, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    md = render_md(t)
    (ctx.state_dir / f"tick-{tick_no:02d}.md").write_text(md, encoding="utf-8")
    if "error" not in t["status"]:
        state["last_status"] = {k: t["status"].get(k) for k in ("processed_files", "document_count", "chunks_created", "status")}
    state.update({"tick": tick_no, "last_tick_at": iso(now)})
    ctx.save_state(state)
    print(md)
    print(f"\n(saved {out_json})")


def cmd_ask(ctx: Ctx) -> None:
    rec = chat(ctx, ctx.args.q, mode=ctx.args.mode, expect=ctx.args.expect, language=ctx.args.lang,
               title="prompt-test ask", keep_session=ctx.args.keep_session)
    state = ctx.load_state()
    f = ctx.state_dir / f"asks-{state['tick']:02d}.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
    if rec.get("error"):
        print(f"ERROR after {rec.get('ms')} ms: {rec['error']}")
        return
    ck = rec["checks"]
    tr = rec.get("trace") or {}
    print(f"[{rec['mode']}] {rec['ms'] / 1000:.0f} s, {rec['answer_chars']} chars, {rec['n_sources']} sources, "
          f"lang {ck['language']}, refusal {ck['refusal']}, expect {ck['expect_in_answer']}/{ck['expect_in_sources']}, "
          f"iterations {tr.get('iterations')}, timing {tr.get('timing')}, tokens {tr.get('tokens')}")
    print("search ops:", rec["search_ops"])
    print("sources:")
    for s in rec["sources"]:
        print("  -", truncate(s.get("source") or s.get("title"), 110), s.get("score") or s.get("similarity") or "")
    print("answer:\n" + rec["answer"])


def cmd_search(ctx: Ctx) -> None:
    res = search(ctx, ctx.args.q, ctx.args.type, ctx.args.n)
    if res.get("error"):
        print("ERROR", res["error"])
        return
    print(f"{ctx.args.type}: {len(res['results'])} results in {res['ms']} ms (server {res.get('server_ms')})")
    for i, r in enumerate(res["results"], 1):
        print(f"{i:2d}. {r.get('similarity', 0):.3f} {truncate(r.get('document_source'), 90)} | {truncate(r.get('content'), 110)}")


def cmd_sample(ctx: Ctx) -> None:
    since = parse_iso(ctx.args.since) if ctx.args.since else utcnow() - dt.timedelta(hours=ctx.args.window_hours)
    docs = list(ctx.dbh.documents.aggregate([
        {"$match": {"_id": {"$gte": oid_at(since)}, "$expr": {"$gte": [{"$strLenCP": "$content"}, ctx.args.min_chars]}}},
        {"$sample": {"size": ctx.args.n}}, {"$project": {"source": 1, "title": 1, "content": 1}}]))
    for d in docs:
        print(f"=== {d['_id']} | {d['source']} | {len(d['content'])} chars ===")
        print(d["content"][: ctx.args.chars])
        print()


def cmd_doc(ctx: Ctx) -> None:
    query: dict[str, Any]
    try:
        query = {"_id": bson.ObjectId(ctx.args.id)}
    except Exception:  # noqa: BLE001 - not an ObjectId, treat as source path
        query = {"source": ctx.args.id}
    d = ctx.dbh.documents.find_one(query)
    if not d:
        print("not found")
        return
    print(json.dumps({k: v for k, v in d.items() if k != "content"}, default=str, ensure_ascii=False, indent=1))
    print(d["content"][: ctx.args.chars])


def main(argv: Optional[list[str]] = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--mongo", default=DEFAULT_MONGO)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.add_argument("--key-file", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("tick", help="run one full test round")
    s.add_argument("--window-hours", type=float, default=3.0, help="window when no previous tick exists")
    s.add_argument("--since", default=None, help="ISO timestamp overriding the window start")
    s.add_argument("--probes", type=int, default=12)
    s.add_argument("--chat", type=int, default=3, help="max chat prompts")
    s.add_argument("--chat-prompts", default=None, help="JSON file with [{question, expect, mode, kind}]")
    s.add_argument("--mode", default=None, help="agent mode for chat prompts: auto|thinking|fast")
    s.add_argument("--no-chat", action="store_true")
    s.set_defaults(func=cmd_tick)
    s = sub.add_parser("ask", help="one chat prompt")
    s.add_argument("--q", required=True)
    s.add_argument("--mode", default=None)
    s.add_argument("--expect", default=None)
    s.add_argument("--lang", default="de")
    s.add_argument("--keep-session", action="store_true")
    s.set_defaults(func=cmd_ask)
    s = sub.add_parser("search", help="one search")
    s.add_argument("--q", required=True)
    s.add_argument("--type", default="hybrid", choices=["hybrid", "semantic", "text"])
    s.add_argument("--n", type=int, default=10)
    s.set_defaults(func=cmd_search)
    s = sub.add_parser("sample", help="snippets of recent documents")
    s.add_argument("--n", type=int, default=5)
    s.add_argument("--window-hours", type=float, default=3.0)
    s.add_argument("--since", default=None)
    s.add_argument("--min-chars", type=int, default=600)
    s.add_argument("--chars", type=int, default=1200)
    s.set_defaults(func=cmd_sample)
    s = sub.add_parser("doc", help="print one document by id or source")
    s.add_argument("--id", required=True)
    s.add_argument("--chars", type=int, default=3000)
    s.set_defaults(func=cmd_doc)
    args = ap.parse_args(argv)
    ctx = Ctx(args)
    args.func(ctx)


if __name__ == "__main__":
    main()
