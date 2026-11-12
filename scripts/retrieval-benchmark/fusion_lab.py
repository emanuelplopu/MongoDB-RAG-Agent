#!/usr/bin/env python
"""Offline what-if for the hybrid ranking: candidate windows and fusion variants on the benchmark cases.

``retrieval_benchmark.py run`` measures the product as deployed. This script answers
"what would another candidate window or another fusion do" without deploying anything:
it runs the vector and the text stage directly against MongoDB (the same operators the
API builds), caches the ranked lists, and scores fusion variants on them.

    uv run python scripts/retrieval-benchmark/fusion_lab.py collect --out <dir> --db <database>
    uv run python scripts/retrieval-benchmark/fusion_lab.py score --out <dir>

``collect`` needs the query embedder (``--embedder``, the server behind ``QUERY_EMBEDDING_URL``)
and reads ``cases.jsonl``; ``score`` reads the cached lists only. Read-only.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import httpx
from pymongo import MongoClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from backend.services.text_query import is_max_clause_error, text_search_stage  # noqa: E402

QUERY_PREFIX = "task: search result | query: "  # EmbeddingGemma (backend/services/query_embedding.py)
VECTOR_CONFIGS: Dict[str, Dict[str, Any]] = {
    "nc100_l20": {"numCandidates": 100, "limit": 20},  # ordinary hybrid query before 2026-11-11
    "nc300_l30": {"numCandidates": 300, "limit": 30},  # 10 x limit
    "nc600_l30": {"numCandidates": 600, "limit": 30},  # 20 x limit, three times the requested ten
}
PROJECT = {"document_id": 1, "chunk_index": 1, "len": {"$strLenCP": "$content"}, "head": {"$substrCP": ["$content", 0, 60]}}


def embed(client: httpx.Client, url: str, model: str, query: str) -> List[float]:
    r = client.post(f"{url}/embeddings", json={"input": QUERY_PREFIX + query, "model": model}, timeout=60)
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def rows(cursor) -> List[dict]:
    return [{"chunk": str(d["_id"]), "doc": str(d["document_id"]), "idx": d.get("chunk_index"), "len": d.get("len"), "score": d.get("score"),
             "ph": (d.get("head") or "").lstrip().startswith(("[Image:", "[Video:", "[Error", "[Audio:"))} for d in cursor]


def cmd_collect(args: argparse.Namespace) -> None:
    out = pathlib.Path(args.out)
    cases = [json.loads(ln) for ln in open(out / "cases.jsonl", encoding="utf-8")]
    cases = [c for c in cases if c["group"] == "known_item" and c["variant"] in args.variants.split(",")]
    if args.limit:
        cases = cases[: args.limit]
    chunks = MongoClient(args.mongo, serverSelectionTimeoutMS=8000)[args.db]["chunks"]
    http = httpx.Client()
    done = 0
    with open(out / "fusion_lists.jsonl", "w", encoding="utf-8") as f:
        for c in cases:
            rec: Dict[str, Any] = {"id": c["id"], "type": c["type"], "variant": c["variant"], "expected": c["expected_document_ids"], "vector": {}, "ms": {}}
            try:
                vector = embed(http, args.embedder, args.model, c["query"])
                for name, cfg in VECTOR_CONFIGS.items():
                    t0 = time.perf_counter()
                    rec["vector"][name] = rows(chunks.aggregate([
                        {"$vectorSearch": {"index": args.vector_index, "queryVector": vector, "path": "embedding", **cfg}},
                        {"$project": {**PROJECT, "score": {"$meta": "vectorSearchScore"}}},
                    ]))
                    rec["ms"][name] = round((time.perf_counter() - t0) * 1000)
                if done < args.exact:
                    t0 = time.perf_counter()
                    rec["vector"]["exact_l30"] = rows(chunks.aggregate([
                        {"$vectorSearch": {"index": args.vector_index, "queryVector": vector, "path": "embedding", "exact": True, "limit": 30}},
                        {"$project": {**PROJECT, "score": {"$meta": "vectorSearchScore"}}},
                    ], maxTimeMS=300000))
                    rec["ms"]["exact_l30"] = round((time.perf_counter() - t0) * 1000)

                def text(fuzzy: bool) -> List[dict]:
                    return rows(chunks.aggregate([
                        text_search_stage(c["query"], index=args.text_index, fuzzy=fuzzy), {"$limit": 30},
                        {"$project": {**PROJECT, "score": {"$meta": "searchScore"}}},
                    ]))

                t0 = time.perf_counter()
                try:
                    rec["text"] = text(True)
                except Exception as exc:  # noqa: BLE001 - the API retries without fuzziness on maxClauseCount
                    if not is_max_clause_error(exc):
                        raise
                    rec["text"] = text(False)
                rec["ms"]["text"] = round((time.perf_counter() - t0) * 1000)
            except Exception as exc:  # noqa: BLE001
                rec["error"] = f"{type(exc).__name__}: {exc}"[:200]
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            done += 1
            if done % 25 == 0:
                print(f"[collect] {done}/{len(cases)}", file=sys.stderr)


# ---------------------------------------------------------------------------
# fusion variants
# ---------------------------------------------------------------------------

Ranked = List[dict]


def rrf(vector: Ranked, text: Ranked, w_vector: float = 1.0, w_text: float = 1.0, k: int = 60, text_first: bool = False) -> Ranked:
    """Reciprocal rank fusion; ties keep the order of first insertion (the API inserts the vector list first)."""
    scores: Dict[str, float] = {}
    first: Dict[str, dict] = {}
    legs = [(text, w_text), (vector, w_vector)] if text_first else [(vector, w_vector), (text, w_text)]
    for leg, weight in legs:
        for rank, row in enumerate(leg):
            scores[row["chunk"]] = scores.get(row["chunk"], 0.0) + weight / (k + rank)
            first.setdefault(row["chunk"], row)
    return [first[c] for c, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)]


def collapse(ranked: Ranked) -> Ranked:
    """One row per document chunk position is what the copy collapse of the API approximates here: one row per document."""
    seen, out = set(), []
    for row in ranked:
        if row.get("ph"):
            continue
        key = (row["doc"], row.get("idx"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def per_document(ranked: Ranked, cap: int) -> Ranked:
    """At most ``cap`` chunks of one document, in rank order."""
    counts: Dict[str, int] = {}
    out = []
    for row in ranked:
        counts[row["doc"]] = counts.get(row["doc"], 0) + 1
        if counts[row["doc"]] <= cap:
            out.append(row)
    return out


VARIANTS: Dict[str, Callable[[dict], Ranked]] = {
    "deployed: nc100, top 20 each, RRF, vector wins ties": lambda r: rrf(r["vector"]["nc100_l20"], r["text"][:20]),
    "cycle 1: nc600, top 30 each, placeholders dropped": lambda r: collapse(rrf(r["vector"]["nc600_l30"], r["text"])),
    "+ text wins ties": lambda r: collapse(rrf(r["vector"]["nc600_l30"], r["text"], text_first=True)),
    "+ text weight 1.5": lambda r: collapse(rrf(r["vector"]["nc600_l30"], r["text"], w_text=1.5, text_first=True)),
    "+ text weight 2": lambda r: collapse(rrf(r["vector"]["nc600_l30"], r["text"], w_text=2.0, text_first=True)),
    "+ text weight 3": lambda r: collapse(rrf(r["vector"]["nc600_l30"], r["text"], w_text=3.0, text_first=True)),
    "+ text weight 2, 2 chunks per document": lambda r: per_document(collapse(rrf(r["vector"]["nc600_l30"], r["text"], w_text=2.0, text_first=True)), 2),
    "text only": lambda r: collapse(r["text"]),
    "vector only nc100": lambda r: r["vector"]["nc100_l20"],
    "vector only nc600": lambda r: r["vector"]["nc600_l30"],
    "vector only nc300": lambda r: r["vector"]["nc300_l30"],
    "cycle 1 with nc300": lambda r: collapse(rrf(r["vector"]["nc300_l30"], r["text"])),
    "nc300 + text wins ties + text weight 2": lambda r: collapse(rrf(r["vector"]["nc300_l30"], r["text"], w_text=2.0, text_first=True)),
    "nc100 top 20 + text wins ties + text weight 2": lambda r: collapse(rrf(r["vector"]["nc100_l20"], r["text"], w_text=2.0, text_first=True)),
}


def first_rank(ranked: Ranked, expected: Sequence[str]) -> Optional[int]:
    wanted = set(expected)
    return next((i + 1 for i, row in enumerate(ranked[:10]) if row["doc"] in wanted), None)


def cmd_score(args: argparse.Namespace) -> None:
    out = pathlib.Path(args.out)
    recs = [json.loads(ln) for ln in open(out / "fusion_lists.jsonl", encoding="utf-8")]
    recs = [r for r in recs if not r.get("error")]
    groups = sorted({r["variant"] for r in recs})
    lines = [f"# Fusion lab: {len(recs)} queries", ""]
    header = "| ranking | " + " | ".join(f"{g}: hit@1 / hit@10 / MRR" for g in groups) + " |"
    lines += [header, "|---|" + "---|" * len(groups)]
    for name, fn in VARIANTS.items():
        cells = []
        for g in groups:
            ranks = [first_rank(fn(r), r["expected"]) for r in recs if r["variant"] == g]
            n = len(ranks)
            hit1 = sum(1 for x in ranks if x == 1) / n
            hit10 = sum(1 for x in ranks if x) / n
            mrr = statistics.mean(1 / x if x else 0 for x in ranks)
            cells.append(f"{hit1:.2f} / {hit10:.2f} / {mrr:.2f} (n {n})")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    exact = [r for r in recs if "exact_l30" in r["vector"]]
    if exact:
        lines += ["", f"## ANN recall against exact search ({len(exact)} queries, top 10)", "", "| config | mean overlap with the exact top 10 | median ms |", "|---|---|---|"]
        for name in VECTOR_CONFIGS:
            overlap = [len({x["chunk"] for x in r["vector"][name][:10]} & {x["chunk"] for x in r["vector"]["exact_l30"][:10]}) / 10 for r in exact]
            ms = [r["ms"][name] for r in exact]
            lines.append(f"| {name} | {statistics.mean(overlap):.2f} | {int(statistics.median(ms))} |")
        lines.append(f"| exact | 1.00 | {int(statistics.median(r['ms']['exact_l30'] for r in exact))} |")
        ranks = [first_rank(r["vector"]["exact_l30"], r["expected"]) for r in exact]
        lines.append("")
        lines.append(f"Exact vector search finds the document in its top 10 for {sum(1 for x in ranks if x)}/{len(ranks)} of these queries.")
    ms = {name: int(statistics.median(r["ms"][name] for r in recs if name in r["ms"])) for name in list(VECTOR_CONFIGS) + ["text"]}
    lines += ["", f"Median stage time (ms): {ms}"]
    text = "\n".join(lines)
    (out / "fusion_lab.md").write_text(text, encoding="utf-8")
    print(text)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--out", required=True)
    c.add_argument("--db", required=True)
    c.add_argument("--mongo", default="mongodb://127.0.0.1:11017/?directConnection=true")
    c.add_argument("--embedder", default="http://127.0.0.1:11091/v1")
    c.add_argument("--model", default="embeddinggemma-2")
    c.add_argument("--vector-index", default="vector_index")
    c.add_argument("--text-index", default="text_index")
    c.add_argument("--variants", default="exact,question,keywords")
    c.add_argument("--exact", type=int, default=0, help="queries that also run an exact (ENN) vector search: a full scan of every vector, minutes per query on millions of chunks")
    c.add_argument("--limit", type=int, default=0)
    c.set_defaults(func=cmd_collect)
    s = sub.add_parser("score")
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_score)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
