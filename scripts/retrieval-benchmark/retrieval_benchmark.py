#!/usr/bin/env python
"""Retrieval benchmark built from a live index: known-item probes, hard variants, identifiers, negatives.

The benchmark answers three questions about an ingested corpus:

* **False negatives** - is a document that is in the index found when it is asked for? Per file
  type, with the exact sentence, with transliterated umlauts, with keywords only, with typos and
  with a question an LLM wrote about the passage.
* **False positives** - what comes back for things that are not in the corpus (invented
  identifiers, invented names, off-topic questions) and for generic one-word queries (placeholder
  documents, tiny chunks, copies of the same content in the top ten)?
* **Identifiers** - do queries for legal identifiers and matter codes return documents that carry
  them?

Two steps, both read-only against the product:

    uv run python scripts/retrieval-benchmark/retrieval_benchmark.py generate --out <dir>
    uv run python scripts/retrieval-benchmark/retrieval_benchmark.py run --out <dir> --key-file <file>

``generate`` reads MongoDB (documents, chunks, the ``text_index`` search index) and writes
``cases.jsonl``; ``run`` sends every case to ``POST /api/v1/search/<type>`` and writes
``results.jsonl`` and ``summary.json`` / ``summary.md``. The cases contain text of the corpus:
keep the output directory outside the repository when the corpus is confidential.

Nothing here is tenant specific; pass ``--mongo``, ``--db`` and ``--api``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import pathlib
import random
import re
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from typing import Any, Iterable, Optional

import httpx
from pymongo import MongoClient

DEFAULT_MONGO = "mongodb://127.0.0.1:11017/?directConnection=true"
DEFAULT_API = "http://127.0.0.1:11001/api/v1"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"

WORD = re.compile(r"[A-Za-zÄÖÜäöüß]{3,}")
UMLAUT_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})

#: file types probed for retention; ``regex`` matches ``documents.source``
TYPE_PATTERNS: dict[str, str] = {
    "pdf": r"\.pdf$", "msg": r"\.msg$", "doc": r"\.doc$", "docx": r"\.docx$", "rtf": r"\.rtf$",
    "xlsx": r"\.xlsx?$", "image": r"\.(jpe?g|png|tiff?|bmp)$", "html": r"\.(html?|mht)$",
    "legacy_text": r"\.(as|ass|ds|eb|do|dos|dss|3|wvb|txt)$", "wbk_dot": r"\.(wbk|dot)$", "eml": r"\.eml$",
}

IDENTIFIER_PATTERNS: dict[str, str] = {
    "firmenbuch": r"\bFN\s?\d{5,6}\s?[a-z]\b",
    "court_ref": r"\b\d{1,3}\s(?:Ob|ObA|ObS|Os|R|Ra|Rs|C|Cg|Cga|E|S|Se|Nc|U|Hv|St|P|Ps|A|Fr)\s\d{1,5}/\d{2}\s?[a-z]\b",
    "grundbuch": r"\bEZ\s?\d{1,5}\b[^\n]{0,40}?\bKG\s?\d{5}\b",
}

NEGATIVE_TOPICS = [
    "Welche Drehmomente gelten für die Zylinderkopfschrauben eines Schiffsdiesels MAN B&W 6S50?",
    "Rezept für glutenfreie Sauerteigstarter mit Buchweizen und Teff",
    "Photosynthese Lichtreaktion Photosystem II Elektronentransportkette Plastochinon",
    "Trainingsplan Marathon unter drei Stunden Intervalle Laktatschwelle",
    "Konfiguration eines BGP Route Reflectors mit Cluster-ID auf Juniper MX",
    "Quantenchromodynamik Confinement Gluonen Gittereichtheorie",
    "Spielbericht Eishockey Finale Overtime Powerplay Torhüterwechsel",
    "Stricken Zopfmuster Raglan von oben Maschenprobe Alpaka",
]
NEGATIVE_NAMES = [
    "Zyxgrof Maschinenbau GmbH", "Dr. Bartholomäus Quendelhuber", "Vrbnjak & Oszkelety Treuhand OG",
    "Xanthippe Wurlitzgruber", "Kwizera-Lindqvist Holding AG", "Mag. Oktavian Przybylskowitz",
    "Flumserberg Kryotechnik KG", "Ysolde Tschentscher-Nakamura",
]
GENERIC_QUERIES = ["Vollmacht", "Honorarnote", "Klage", "Mietvertrag", "Rechnung", "Foto", "Bild", "Unterschrift",
                   "Logo", "image001", "Protokoll", "Vergleich", "Kündigung", "Grundbuchauszug", "Beschluss"]


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------

def sentences(text: str) -> Iterable[str]:
    """Candidate probe sentences of a chunk: prose lines without table or markup noise."""
    for raw in re.split(r"(?<=[.!?;:])\s+|\n+", text):
        s = " ".join(raw.split())
        if "|" in s or "<!--" in s or s.startswith("#") or "http" in s or "@" in s:
            continue
        words = s.split()
        if not 8 <= len(words) <= 16:
            continue
        good = [w for w in words if WORD.fullmatch(w.strip(".,;:()[]\"'„“”-"))]
        if len(good) < 0.75 * len(words) or sum(len(w) >= 8 for w in good) < 2:
            continue
        yield s.strip(" .;:")


def phrase_hits(chunks, phrase: str, limit: int = 40) -> list[dict]:
    """Chunks containing the exact phrase (Atlas Search ``phrase`` on the text index)."""
    pipeline = [
        {"$search": {"index": "text_index", "phrase": {"query": phrase, "path": "content"}}},
        {"$limit": limit},
        {"$project": {"document_id": 1, "len": {"$strLenCP": "$content"}}},
    ]
    return list(chunks.aggregate(pipeline, maxTimeMS=30000))


def variants(sentence: str, rng: random.Random) -> dict[str, str]:
    out = {"exact": sentence}
    if re.search(r"[äöüßÄÖÜ]", sentence):
        out["translit"] = sentence.translate(UMLAUT_MAP).lower()
    words = sorted({w for w in WORD.findall(sentence)}, key=len, reverse=True)[:5]
    if len(words) >= 4:
        rng.shuffle(words)
        out["keywords"] = " ".join(words)
    typo = sentence
    for w in sorted({w for w in WORD.findall(sentence) if len(w) >= 7}, key=len, reverse=True)[:2]:
        i = rng.randrange(2, len(w) - 2)
        typo = typo.replace(w, w[:i] + w[i + 1] + w[i] + w[i + 2:], 1)
    if typo != sentence:
        out["typo"] = typo
    return out


def llm_question(client: httpx.Client, ollama: str, model: str, passage: str) -> Optional[str]:
    """A German question about the passage that avoids its wording (local model, optional)."""
    prompt = (
        "Du erstellst Testfragen für eine Dokumentensuche in einer Rechtsanwaltskanzlei.\n"
        "Formuliere GENAU EINE konkrete Frage auf Deutsch, die sich nur mit dem folgenden Abschnitt beantworten lässt. "
        "Nenne die im Abschnitt vorkommenden Eigennamen, Beträge oder Daten, soweit nötig, damit die Frage eindeutig ist, "
        "aber übernimm keine ganzen Satzteile wörtlich. Antworte nur mit der Frage.\n\n"
        f"Abschnitt:\n{passage[:1800]}\n\nFrage:"
    )
    try:
        r = client.post(f"{ollama}/api/generate", json={"model": model, "prompt": prompt, "stream": False, "think": False,
                                                        "options": {"temperature": 0.3, "num_predict": 120}}, timeout=180)
        r.raise_for_status()
        text = (r.json().get("response") or "").strip().splitlines()
        q = next((ln.strip() for ln in text if ln.strip().endswith("?")), None)
        return q if q and 25 <= len(q) <= 300 else None
    except Exception as exc:  # noqa: BLE001 - the question variant is optional
        print(f"[llm] {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def cmd_generate(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    db = MongoClient(args.mongo, serverSelectionTimeoutMS=8000)[args.db]
    docs, chunks = db["documents"], db["chunks"]
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []
    type_patterns = dict(TYPE_PATTERNS)
    doc_classes: dict[str, list[str]] = {}
    if args.pdf_classes:  # optional {"scan": [{"path": ...}], "digital": [...]} from a source-file probe
        for cls, rows in json.load(open(args.pdf_classes, encoding="utf-8")).items():
            doc_classes["pdf_" + cls] = [r["path"] for r in rows]
    llm = httpx.Client() if args.questions else None

    def candidates(label: str, n: int) -> list[dict]:
        if label in doc_classes:
            paths = doc_classes[label][:]
            rng.shuffle(paths)
            return list(docs.find({"metadata.file_path": {"$in": paths[: n * 3]}, "metadata.word_count": {"$gte": 80}},
                                  projection={"source": 1, "content_hash": 1, "metadata": 1}))
        return list(docs.aggregate([
            {"$match": {"source": {"$regex": type_patterns[label], "$options": "i"}, "metadata.word_count": {"$gte": 80}}},
            {"$sample": {"size": n * 3}}, {"$project": {"source": 1, "content_hash": 1, "metadata": 1}},
        ]))

    labels = list(type_patterns) + list(doc_classes)
    for label in labels:
        made = 0
        for d in candidates(label, args.per_type):
            if made >= args.per_type:
                break
            chs = list(chunks.aggregate([{"$match": {"document_id": d["_id"], "token_count": {"$gte": 60}}}, {"$sample": {"size": 4}},
                                         {"$project": {"content": 1, "chunk_index": 1}}]))
            picked = None
            for ch in chs:
                for s in sorted(sentences(ch["content"]), key=len, reverse=True)[:3]:
                    hits = phrase_hits(chunks, s)
                    hit_docs = {h["document_id"] for h in hits}
                    if d["_id"] in hit_docs and len(hit_docs) <= args.max_copies:
                        picked = (s, ch, hit_docs)
                        break
                if picked:
                    break
            if not picked:
                continue
            s, ch, hit_docs = picked
            expected = {str(x) for x in hit_docs}
            base = {"group": "known_item", "type": label, "source": d["source"], "document_id": str(d["_id"]),
                    "content_hash": d.get("content_hash"), "expected_document_ids": sorted(expected), "copies": len(expected)}
            for vname, q in variants(s, rng).items():
                cases.append({**base, "variant": vname, "query": q})
            if llm is not None and made < args.questions:
                q = llm_question(llm, args.ollama, args.model, ch["content"])
                if q:
                    cases.append({**base, "variant": "question", "query": q})
            made += 1
        print(f"[generate] {label}: {made} documents", file=sys.stderr)

    # identifiers that occur in the corpus: found in a chunk sample, ground truth by phrase search
    found: dict[str, set[str]] = defaultdict(set)
    for ch in chunks.aggregate([{"$sample": {"size": args.identifier_sample}}, {"$project": {"content": 1}}]):
        for kind, pat in IDENTIFIER_PATTERNS.items():
            for m in re.finditer(pat, ch["content"]):
                found[kind].add(" ".join(m.group(0).split()))
    for kind, values in found.items():
        for ident in rng.sample(sorted(values), min(args.identifiers, len(values))):
            hits = phrase_hits(chunks, ident, limit=200)
            if not hits:
                continue
            cases.append({"group": "identifier", "type": kind, "variant": "exact", "query": ident,
                          "expected_document_ids": sorted({str(h["document_id"]) for h in hits}), "expected_substring": ident})
    # matter codes: top-level folder names that look like codes; ground truth is the path
    folders = Counter()
    for d in docs.aggregate([{"$sample": {"size": 20000}}, {"$project": {"p": "$metadata.file_path"}}]):
        parts = (d.get("p") or "").split("/")
        if len(parts) > 5:
            folders[parts[4]] += 1
    codes = [f for f, n in folders.items() if n >= 3 and re.fullmatch(r"[A-ZÄÖÜ]{1,6}(?:[- ][A-Z0-9ÄÖÜ]{1,10}){1,3}", f)]
    for code in rng.sample(sorted(codes), min(args.identifiers, len(codes))):
        cases.append({"group": "identifier", "type": "matter_code", "variant": "exact", "query": code, "expected_path_part": f"/{code}/"})

    # negatives: nothing in the corpus should answer these
    absent = []
    while len(absent) < args.negatives:
        ident = rng.choice([f"FN {rng.randrange(700000, 999999)} {rng.choice('qxyz')}",
                            f"{rng.randrange(40, 99)} Cg {rng.randrange(5000, 9999)}/{rng.randrange(60, 99)} {rng.choice('qxyz')}"])
        if not phrase_hits(chunks, ident, limit=1):
            absent.append(ident)
    for q in absent:
        cases.append({"group": "negative", "type": "absent_identifier", "variant": "exact", "query": q, "expect_no_answer": True, "expected_substring": q})
    for q in NEGATIVE_NAMES:
        if not phrase_hits(chunks, q, limit=1):
            cases.append({"group": "negative", "type": "absent_name", "variant": "exact", "query": q, "expect_no_answer": True, "expected_substring": q})
    for q in NEGATIVE_TOPICS:
        cases.append({"group": "negative", "type": "off_topic", "variant": "exact", "query": q, "expect_no_answer": True})
    for q in GENERIC_QUERIES:
        cases.append({"group": "generic", "type": "one_word", "variant": "exact", "query": q})

    for i, c in enumerate(cases):
        c["id"] = f"c{i:04d}"
    with open(out / "cases.jsonl", "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(json.dumps(Counter(f"{c['group']}/{c['variant']}" for c in cases), indent=1))
    print(f"wrote {len(cases)} cases to {out / 'cases.jsonl'}")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

class Throttle:
    """At most ``per_minute`` calls per minute across threads (the API allows 30 searches per minute)."""

    def __init__(self, per_minute: float) -> None:
        self.gap = 60.0 / per_minute
        self.lock = threading.Lock()
        self.next_at = time.monotonic()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + self.gap
        time.sleep(max(0.0, at - now))


def search(client: httpx.Client, throttle: Throttle, search_type: str, query: str, n: int) -> dict:
    for attempt in range(4):
        throttle.wait()
        t0 = time.perf_counter()
        try:
            r = client.post(f"/search/{search_type}", json={"query": query, "match_count": n})
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}", "results": [], "ms": round((time.perf_counter() - t0) * 1000)}
        ms = round((time.perf_counter() - t0) * 1000)
        if r.status_code == 429:
            time.sleep(10 + 10 * attempt)
            continue
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}: {r.text[:200]}", "results": [], "ms": ms}
        j = r.json()
        return {"results": j.get("results", []), "ms": ms, "server_ms": j.get("processing_time_ms")}
    return {"error": "rate limited", "results": [], "ms": 0}


def evaluate(case: dict, results: list[dict]) -> dict:
    exp_ids = set(case.get("expected_document_ids") or [])
    sub = (case.get("expected_substring") or "").lower()
    part = case.get("expected_path_part")
    flags = []
    for r in results:
        md = r.get("metadata") or {}
        content = r.get("content") or ""
        ok = False
        if exp_ids and str(r.get("document_id")) in exp_ids:
            ok = True
        if sub and sub in " ".join(content.lower().split()):
            ok = True
        if part and part.lower() in (md.get("file_path") or "").lower():
            ok = True
        flags.append(ok)
    rank = next((i + 1 for i, ok in enumerate(flags) if ok), None)
    contents = [" ".join((r.get("content") or "").split()) for r in results]
    return {
        "rank": rank, "n": len(results), "relevant_in_top10": sum(flags[:10]),
        "top_score": results[0].get("similarity") if results else None,
        "placeholder_in_top10": sum(1 for c in contents[:10] if re.match(r"\[(Image|Video|Audio|Error)", c)),
        "tiny_in_top10": sum(1 for c in contents[:10] if len(c) < 60),
        "duplicate_in_top10": len(contents[:10]) - len(set(contents[:10])),
        "top_sources": [(r.get("document_source") or "")[-70:] for r in results[:3]],
    }


def cmd_run(args: argparse.Namespace) -> None:
    out = pathlib.Path(args.out)
    cases = [json.loads(ln) for ln in open(out / "cases.jsonl", encoding="utf-8")]
    if args.limit:
        cases = cases[: args.limit]
    key = pathlib.Path(args.key_file).read_text(encoding="utf-8").strip()
    client = httpx.Client(base_url=args.api, headers={"X-API-Key": key}, timeout=httpx.Timeout(180, connect=10))
    throttle = Throttle(args.per_minute)
    types = args.search_types.split(",")
    jobs = []
    for c in cases:
        for st in types:
            if st != "hybrid" and (c["group"] == "known_item" and c["variant"] not in ("exact", "translit", "question", "keywords")):
                continue
            jobs.append((c, st))
    done = 0
    lock = threading.Lock()
    rows: list[dict] = []
    tag = args.tag

    def work(job: tuple[dict, str]) -> dict:
        c, st = job
        res = search(client, throttle, st, c["query"], args.match_count)
        row = {"id": c["id"], "group": c["group"], "type": c["type"], "variant": c["variant"], "search_type": st, "query": c["query"],
               "ms": res.get("ms"), "server_ms": res.get("server_ms"), "error": res.get("error"), **evaluate(c, res["results"])}
        return row

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool, open(out / f"results-{tag}.jsonl", "w", encoding="utf-8") as f:
        for row in pool.map(work, jobs):
            with lock:
                rows.append(row)
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                done += 1
                if done % 50 == 0:
                    print(f"[run] {done}/{len(jobs)}", file=sys.stderr)
    summary = summarise(rows)
    (out / f"summary-{tag}.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / f"summary-{tag}.md").write_text(render(summary, tag), encoding="utf-8")
    print(render(summary, tag))


def summarise(rows: list[dict]) -> dict:
    def rate(rs: list[dict], k: int) -> Optional[float]:
        return round(sum(1 for r in rs if r["rank"] and r["rank"] <= k) / len(rs), 3) if rs else None

    out: dict[str, Any] = {"queries": len(rows), "errors": sum(1 for r in rows if r.get("error"))}
    lat = [r["ms"] for r in rows if r.get("ms")]
    for st in sorted({r["search_type"] for r in rows}):
        v = sorted(r["ms"] for r in rows if r["search_type"] == st and r.get("ms"))
        if v:
            out.setdefault("latency_ms", {})[st] = {"p50": v[len(v) // 2], "p90": v[int(0.9 * len(v))], "max": v[-1], "n": len(v)}
    ki = [r for r in rows if r["group"] == "known_item" and not r.get("error")]
    table: dict[str, Any] = {}
    for st in sorted({r["search_type"] for r in ki}):
        for variant in ("exact", "translit", "keywords", "typo", "question"):
            rs = [r for r in ki if r["search_type"] == st and r["variant"] == variant]
            if rs:
                ranks = [1 / r["rank"] if r["rank"] else 0 for r in rs]
                table[f"{st}/{variant}"] = {"n": len(rs), "hit@1": rate(rs, 1), "hit@5": rate(rs, 5), "hit@10": rate(rs, 10), "mrr": round(statistics.mean(ranks), 3)}
    out["known_item"] = table
    by_type: dict[str, Any] = {}
    for t in sorted({r["type"] for r in ki}):
        for variant in ("exact", "question"):
            rs = [r for r in ki if r["type"] == t and r["search_type"] == "hybrid" and r["variant"] == variant]
            if rs:
                by_type[f"{t}/{variant}"] = {"n": len(rs), "hit@1": rate(rs, 1), "hit@10": rate(rs, 10)}
    out["known_item_by_type_hybrid"] = by_type
    ident: dict[str, Any] = {}
    for st in sorted({r["search_type"] for r in rows}):
        for t in sorted({r["type"] for r in rows if r["group"] == "identifier"}):
            rs = [r for r in rows if r["group"] == "identifier" and r["type"] == t and r["search_type"] == st and not r.get("error")]
            if rs:
                ident[f"{st}/{t}"] = {"n": len(rs), "hit@1": rate(rs, 1), "hit@10": rate(rs, 10),
                                      "precision@10": round(statistics.mean(r["relevant_in_top10"] / max(1, min(10, r["n"])) for r in rs), 3)}
    out["identifier"] = ident
    neg: dict[str, Any] = {}
    for st in sorted({r["search_type"] for r in rows}):
        for t in sorted({r["type"] for r in rows if r["group"] == "negative"}):
            rs = [r for r in rows if r["group"] == "negative" and r["type"] == t and r["search_type"] == st and not r.get("error")]
            if rs:
                scores = sorted(r["top_score"] for r in rs if r["top_score"] is not None)
                neg[f"{st}/{t}"] = {"n": len(rs), "returned_results": sum(1 for r in rs if r["n"] > 0), "mean_results": round(statistics.mean(r["n"] for r in rs), 1),
                                    "top_score_p50": scores[len(scores) // 2] if scores else None, "top_score_max": scores[-1] if scores else None,
                                    "wrongly_matched_substring": sum(1 for r in rs if r["rank"])}
    out["negative"] = neg
    pos_scores = {}
    for st in sorted({r["search_type"] for r in rows}):
        v = sorted(r["top_score"] for r in ki if r["search_type"] == st and r["variant"] == "question" and r["top_score"] is not None)
        if v:
            pos_scores[st] = {"p10": v[len(v) // 10], "p50": v[len(v) // 2], "p90": v[int(0.9 * len(v))]}
    out["top_score_of_real_questions"] = pos_scores
    gen = [r for r in rows if r["group"] in ("generic", "known_item", "identifier") and r["search_type"] == "hybrid" and not r.get("error") and r["n"]]
    if gen:
        out["top10_noise_hybrid"] = {
            "queries": len(gen),
            "with_placeholder": sum(1 for r in gen if r["placeholder_in_top10"]),
            "with_tiny_chunk": sum(1 for r in gen if r["tiny_in_top10"]),
            "with_duplicate_content": sum(1 for r in gen if r["duplicate_in_top10"]),
            "mean_duplicates": round(statistics.mean(r["duplicate_in_top10"] for r in gen), 2),
        }
        g1 = [r for r in rows if r["group"] == "generic" and r["search_type"] == "hybrid" and r["n"]]
        if g1:
            out["generic_one_word_hybrid"] = {"queries": len(g1), "mean_placeholder_in_top10": round(statistics.mean(r["placeholder_in_top10"] for r in g1), 2),
                                              "mean_tiny_in_top10": round(statistics.mean(r["tiny_in_top10"] for r in g1), 2),
                                              "mean_duplicates_in_top10": round(statistics.mean(r["duplicate_in_top10"] for r in g1), 2)}
    return out


def render(s: dict, tag: str) -> str:
    lines = [f"# Retrieval benchmark `{tag}`", "", f"{s['queries']} queries, {s['errors']} errors", ""]
    for title, key in (("Latency (ms)", "latency_ms"), ("Known item: search type / variant", "known_item"), ("Known item by file type (hybrid)", "known_item_by_type_hybrid"),
                       ("Identifiers", "identifier"), ("Negatives (nothing should match)", "negative"), ("Top score of real questions", "top_score_of_real_questions")):
        block = s.get(key) or {}
        if not block:
            continue
        cols = sorted({k for v in block.values() for k in v}, key=lambda k: (k != "n", k))
        lines += [f"## {title}", "", "| | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        lines += [f"| {name} | " + " | ".join(str(v.get(c, "")) for c in cols) + " |" for name, v in block.items()]
        lines.append("")
    for key in ("top10_noise_hybrid", "generic_one_word_hybrid"):
        if s.get(key):
            lines += [f"## {key}", "", "```", json.dumps(s[key], indent=1), "```", ""]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="build cases.jsonl from the live index")
    g.add_argument("--out", required=True)
    g.add_argument("--mongo", default=DEFAULT_MONGO)
    g.add_argument("--db", required=True)
    g.add_argument("--per-type", type=int, default=10, help="documents probed per file type")
    g.add_argument("--questions", type=int, default=0, help="documents per type that also get an LLM-written question (0 = none)")
    g.add_argument("--ollama", default=DEFAULT_OLLAMA)
    g.add_argument("--model", default="gemma4:12b")
    g.add_argument("--identifiers", type=int, default=12, help="identifiers per kind")
    g.add_argument("--identifier-sample", type=int, default=30000, help="chunks scanned for identifiers")
    g.add_argument("--negatives", type=int, default=16, help="invented identifiers that are verified absent")
    g.add_argument("--max-copies", type=int, default=8, help="skip probes whose sentence occurs in more documents (boilerplate)")
    g.add_argument("--pdf-classes", help="JSON {class: [{path}]} to probe PDF classes (scan, digital) separately")
    g.add_argument("--seed", type=int, default=20261111)
    g.set_defaults(func=cmd_generate)
    r = sub.add_parser("run", help="send the cases to the search API and score them")
    r.add_argument("--out", required=True)
    r.add_argument("--api", default=DEFAULT_API)
    r.add_argument("--key-file", required=True, help="file holding an API key (sent as X-API-Key)")
    r.add_argument("--search-types", default="hybrid,semantic,text")
    r.add_argument("--match-count", type=int, default=10)
    r.add_argument("--per-minute", type=float, default=27, help="request budget (the API allows 30 searches per minute and client)")
    r.add_argument("--workers", type=int, default=3)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--tag", default=time.strftime("%Y%m%d-%H%M"))
    r.set_defaults(func=cmd_run)
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
