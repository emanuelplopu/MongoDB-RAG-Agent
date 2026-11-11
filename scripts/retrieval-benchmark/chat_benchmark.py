#!/usr/bin/env python
"""Chat benchmark built from a live index: complex prompts with known sources, and prompts that must be declined.

Companion of ``retrieval_benchmark.py``. Where that script measures the search endpoints,
this one sends questions through the chat sessions API (the path the UI uses) and checks

* whether the documents that hold the answer are among the cited sources,
* whether the answer contains the reference facts (judged by a local model, labelled as such),
* whether a question about something that is not in the corpus is declined (false positives:
  invented parties, invented identifiers, false premises about a real matter).

    uv run python scripts/retrieval-benchmark/chat_benchmark.py generate --out <dir> --db <database>
    uv run python scripts/retrieval-benchmark/chat_benchmark.py run --out <dir> --key-file <file>

``generate`` writes ``chat_cases.jsonl`` (it reuses the LLM-written questions of
``cases.jsonl`` when that file is in the same directory); ``run`` writes
``chat_results-<tag>.jsonl`` and ``chat_summary-<tag>.md``. The cases quote the corpus:
keep the directory outside the repository when the corpus is confidential.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from typing import Any, Optional

import httpx
from pymongo import MongoClient

DEFAULT_MONGO = "mongodb://127.0.0.1:11017/?directConnection=true"
DEFAULT_API = "http://127.0.0.1:11001/api/v1"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"

REFUSAL = re.compile(
    r"keine (informationen|dokumente|unterlagen|angaben|hinweise|daten|treffer)|nicht (gefunden|finden|enthalten|vorhanden|hervor|bekannt|ersichtlich)"
    r"|konnte(n)? (ich )?(keine|nicht)|liegen (mir )?(keine|nicht)|no (information|documents?|results?)|could not find|not (available|contained|found)"
    r"|kann ich (leider )?(nicht|keine)|geht (aus|nicht)|lässt sich nicht",
    re.IGNORECASE,
)
INVENTED_PARTIES = ["Zyxgrof Maschinenbau GmbH", "Dr. Bartholomäus Quendelhuber", "Vrbnjak & Oszkelety Treuhand OG",
                    "Kwizera-Lindqvist Holding AG", "Flumserberg Kryotechnik KG", "Mag. Oktavian Przybylskowitz"]
UNANSWERABLE_TEMPLATES = [
    "Welche Forderung macht die {party} in ihrer Klage geltend und wie hoch ist der Streitwert?",
    "Wann wurde der Vergleich mit {party} abgeschlossen und welche Raten wurden vereinbart?",
    "Fasse das Gutachten zusammen, das {party} im Verfahren vorgelegt hat.",
]
FALSE_PREMISE_TEMPLATES = [
    "Warum wurde im Akt {code} die Klage gegen die {party} rechtskräftig abgewiesen?",
    "Welche Kaution hat die {party} im Akt {code} laut Mietvertrag hinterlegt?",
]


def ollama(client: httpx.Client, base: str, model: str, prompt: str, num_predict: int = 300, as_json: bool = False) -> str:
    body: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False, "think": False,
                            "options": {"temperature": 0.2, "num_predict": num_predict}}
    if as_json:
        body["format"] = "json"
    r = client.post(f"{base}/api/generate", json=body, timeout=300)
    r.raise_for_status()
    return (r.json().get("response") or "").strip()


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------

def matter_folders(docs, sample: int = 30000) -> dict[str, int]:
    folders: Counter = Counter()
    for d in docs.aggregate([{"$sample": {"size": sample}}, {"$project": {"p": "$metadata.file_path"}}]):
        parts = (d.get("p") or "").split("/")
        if len(parts) > 5:
            folders["/".join(parts[:5])] += 1
    return dict(folders)


def cmd_generate(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    db = MongoClient(args.mongo, serverSelectionTimeoutMS=8000)[args.db]
    docs, chunks = db["documents"], db["chunks"]
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    llm = httpx.Client()
    cases: list[dict[str, Any]] = []

    # 1. single-document questions written for the retrieval benchmark
    retrieval_cases = out / "cases.jsonl"
    if retrieval_cases.exists():
        questions = [json.loads(ln) for ln in open(retrieval_cases, encoding="utf-8")]
        by_type: dict[str, list] = defaultdict(list)
        for c in questions:
            if c.get("variant") == "question":
                by_type[c["type"]].append(c)
        for t, rows in sorted(by_type.items()):
            for c in rng.sample(rows, min(args.single_per_type, len(rows))):
                cases.append({"kind": "single_document", "type": t, "question": c["query"], "gold_document_ids": c["expected_document_ids"],
                              "gold_sources": [c["source"]]})
        idents = [c for c in questions if c.get("group") == "identifier" and c.get("type") != "matter_code" and 1 <= len(c.get("expected_document_ids") or []) <= 40]
        for c in rng.sample(idents, min(args.identifiers, len(idents))):
            cases.append({"kind": "identifier", "type": c["type"], "question": f"In welchen Dokumenten kommt {c['query']} vor und worum geht es dort?",
                          "gold_document_ids": c["expected_document_ids"], "must_mention": c["query"]})

    # 2. multi-document questions inside one matter folder
    folders = [f for f, n in matter_folders(docs).items() if n >= 8]
    rng.shuffle(folders)
    made = 0
    for folder in folders:
        if made >= args.multi:
            break
        rows = list(docs.aggregate([
            {"$match": {"metadata.file_path": {"$regex": "^" + re.escape(folder) + "/"}, "metadata.word_count": {"$gte": 150, "$lte": 6000}}},
            {"$sample": {"size": 12}}, {"$project": {"source": 1, "content_hash": 1, "title": 1}},
        ]))
        seen, picked = set(), []
        for d in rows:
            if d.get("content_hash") in seen or re.search(r"\.(jpe?g|png|gif|bmp)$", d["source"], re.I):
                continue
            seen.add(d.get("content_hash"))
            ch = next(iter(chunks.aggregate([{"$match": {"document_id": d["_id"], "token_count": {"$gte": 120}}}, {"$sample": {"size": 1}}, {"$project": {"content": 1}}])), None)
            if ch:
                picked.append((d, ch["content"]))
            if len(picked) == 2:
                break
        if len(picked) < 2:
            continue
        prompt = (
            "Du erstellst Testfragen für die Dokumentensuche einer Rechtsanwaltskanzlei. Unten stehen zwei Abschnitte aus ZWEI verschiedenen "
            "Dokumenten desselben Aktes. Formuliere EINE Frage auf Deutsch, die nur beantwortet werden kann, wenn man BEIDE Abschnitte kennt "
            "(z.B. Vergleich, zeitliche Abfolge, Zusammenhang zwischen Schreiben und Antwort). Nenne nötige Namen, Daten oder Beträge, damit die "
            "Frage eindeutig ist. Gib JSON zurück: {\"question\": \"...\", \"answer\": \"kurze Musterantwort\", \"usable\": true|false}. "
            "Setze usable auf false, wenn die Abschnitte nichts miteinander zu tun haben oder unlesbar sind.\n\n"
            f"Abschnitt A (Dokument: {picked[0][0]['source']}):\n{picked[0][1][:1500]}\n\n"
            f"Abschnitt B (Dokument: {picked[1][0]['source']}):\n{picked[1][1][:1500]}\n"
        )
        try:
            data = json.loads(ollama(llm, args.ollama, args.model, prompt, num_predict=400, as_json=True))
        except Exception as exc:  # noqa: BLE001
            print(f"[multi] {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        if not data.get("usable") or not str(data.get("question", "")).strip().endswith("?"):
            continue
        cases.append({"kind": "multi_document", "type": folder.rsplit("/", 1)[-1], "question": data["question"].strip(), "reference_answer": str(data.get("answer", "")),
                      "gold_document_ids": [str(picked[0][0]["_id"]), str(picked[1][0]["_id"])], "gold_sources": [picked[0][0]["source"], picked[1][0]["source"]],
                      "reference_passages": [picked[0][1][:1500], picked[1][1][:1500]]})
        made += 1
        print(f"[multi] {made}/{args.multi}", file=sys.stderr)

    # 3. folder-scoped listings: the ground truth is the file list of the matter
    listing = 0
    for folder in folders:
        if listing >= args.listings:
            break
        code = folder.rsplit("/", 1)[-1]
        fee_notes = list(docs.find({"metadata.file_path": {"$regex": "^" + re.escape(folder) + "/.*(Honorarnote|Honorar/|Leistungsaufstellung)", "$options": "i"}},
                                   projection={"source": 1}).limit(60))
        if not 2 <= len(fee_notes) <= 40:
            continue
        cases.append({"kind": "folder_listing", "type": code, "question": f"Welche Honorarnoten und Leistungsaufstellungen gibt es im Akt {code}? Nenne Datum und Betrag, soweit ersichtlich.",
                      "gold_document_ids": [str(d["_id"]) for d in fee_notes], "gold_sources": [d["source"] for d in fee_notes], "gold_path_part": f"{code}/"})
        listing += 1

    # 4. prompts that must be declined
    for i, party in enumerate(INVENTED_PARTIES):
        cases.append({"kind": "unanswerable", "type": "invented_party", "question": UNANSWERABLE_TEMPLATES[i % len(UNANSWERABLE_TEMPLATES)].format(party=party),
                      "expect_no_answer": True, "must_not_assert": party})
    codes = [f.rsplit("/", 1)[-1] for f in folders[:40] if re.fullmatch(r"[A-ZÄÖÜ0-9][A-ZÄÖÜ0-9 &.-]{3,24}", f.rsplit("/", 1)[-1])]
    for i, code in enumerate(rng.sample(codes, min(args.false_premises, len(codes)))):
        party = INVENTED_PARTIES[i % len(INVENTED_PARTIES)]
        cases.append({"kind": "unanswerable", "type": "false_premise", "question": FALSE_PREMISE_TEMPLATES[i % len(FALSE_PREMISE_TEMPLATES)].format(code=code, party=party),
                      "expect_no_answer": True, "must_not_assert": party})
    for q in ("Welche Drehmomente gelten für die Zylinderkopfschrauben eines Schiffsdiesels MAN B&W 6S50?",
              "Wie konfiguriere ich einen BGP Route Reflector mit Cluster-ID auf einem Juniper MX?"):
        cases.append({"kind": "unanswerable", "type": "off_topic", "question": q, "expect_no_answer": True})

    for i, c in enumerate(cases):
        c["id"] = f"q{i:03d}"
    with open(out / "chat_cases.jsonl", "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(json.dumps(Counter(c["kind"] for c in cases)), f"-> {out / 'chat_cases.jsonl'}")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def ask(client: httpx.Client, question: str, mode: Optional[str]) -> dict:
    rec: dict[str, Any] = {}
    t0 = time.perf_counter()
    session_id = None
    try:
        s = client.post("/sessions", json={"title": "chat-benchmark"})
        s.raise_for_status()
        sj = s.json()
        session_id = sj.get("id") or sj.get("_id") or sj.get("session_id")
        body: dict[str, Any] = {"content": question, "include_sources": True, "language": "de"}
        if mode:
            body["agent_mode"] = mode
        r = client.post(f"/sessions/{session_id}/messages", json=body)
        rec["ms"] = round((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            rec["error"] = f"HTTP {r.status_code}: {r.text[:200]}"
            return rec
        am = (r.json().get("assistant_message") or {})
        rec["answer"] = am.get("content") or ""
        rec["sources"] = [{k: s.get(k) for k in ("title", "source", "document_id", "score", "similarity")} for s in (am.get("sources") or [])][:12]
        ops = (((am.get("thinking") or {}).get("search") or {}).get("operations")) or []
        rec["search_queries"] = [op.get("query") for op in ops][:8]
        return rec
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["ms"] = round((time.perf_counter() - t0) * 1000)
        return rec
    finally:
        if session_id:
            try:
                client.delete(f"/sessions/{session_id}")
            except Exception:  # noqa: BLE001
                pass


def judge(llm: httpx.Client, args: argparse.Namespace, case: dict, answer: str) -> Optional[bool]:
    reference = case.get("reference_answer") or ""
    passages = "\n---\n".join(case.get("reference_passages") or [])
    if not (reference or passages) or not answer.strip():
        return None
    prompt = (
        "Bewerte die Antwort eines Assistenten. Antworte nur mit JSON {\"correct\": true|false}.\n"
        "correct ist true, wenn die Antwort die wesentlichen Fakten der Musterantwort bzw. der Referenzabschnitte richtig wiedergibt, "
        "und false, wenn sie ausweicht, etwas anderes behauptet oder die Fakten fehlen.\n\n"
        f"Frage: {case['question']}\n\nMusterantwort: {reference}\n\nReferenzabschnitte:\n{passages[:3000]}\n\nAntwort des Assistenten:\n{answer[:3000]}\n"
    )
    try:
        return bool(json.loads(ollama(llm, args.ollama, args.model, prompt, num_predict=40, as_json=True)).get("correct"))
    except Exception:  # noqa: BLE001
        return None


def cmd_run(args: argparse.Namespace) -> None:
    out = pathlib.Path(args.out)
    cases = [json.loads(ln) for ln in open(out / "chat_cases.jsonl", encoding="utf-8")]
    if args.kinds:
        wanted = set(args.kinds.split(","))
        cases = [c for c in cases if c["kind"] in wanted]
    if args.limit:
        cases = cases[: args.limit]
    key = pathlib.Path(args.key_file).read_text(encoding="utf-8").strip()
    client = httpx.Client(base_url=args.api, headers={"X-API-Key": key}, timeout=httpx.Timeout(900, connect=10))
    llm = httpx.Client()
    rows = []
    with open(out / f"chat_results-{args.tag}.jsonl", "w", encoding="utf-8") as f:
        for i, case in enumerate(cases, 1):
            rec = ask(client, case["question"], args.mode)
            answer = rec.get("answer") or ""
            gold = set(case.get("gold_document_ids") or [])
            gold_paths = {str(path).lower() for path in case.get("gold_sources") or []}
            part = (case.get("gold_path_part") or "").lower()
            # the sources of the sessions API carry the document's path, not always its id
            hits = {
                str(s.get("source") or s.get("document_id"))
                for s in rec.get("sources") or []
                if str(s.get("document_id")) in gold or str(s.get("source") or "").lower() in gold_paths
                or (part and part in str(s.get("source") or "").lower())
            }
            row = {
                "id": case["id"], "kind": case["kind"], "type": case["type"], "question": case["question"], "ms": rec.get("ms"), "error": rec.get("error"),
                "answer": answer, "n_sources": len(rec.get("sources") or []), "gold_in_sources": len(hits),
                "gold_total": max(len(gold), len(gold_paths)), "refused": bool(REFUSAL.search(answer[:600])) or len(answer.strip()) < 20,
                "sources": [str(s.get("source")) for s in (rec.get("sources") or [])], "search_queries": rec.get("search_queries"),
            }
            if case.get("must_mention"):
                row["mentions_identifier"] = case["must_mention"].lower().replace(" ", "") in answer.lower().replace(" ", "")
            if case.get("must_not_assert"):
                row["names_invented_party"] = case["must_not_assert"].lower() in answer.lower()
            if not case.get("expect_no_answer"):
                row["judged_correct"] = judge(llm, args, case, answer)
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            print(f"[chat] {i}/{len(cases)} {case['kind']} {row['ms']} ms gold {row['gold_in_sources']}/{row['gold_total']} refused {row['refused']}", file=sys.stderr)
    summary = summarise(rows)
    text = "# Chat benchmark `%s`\n\n```\n%s\n```\n" % (args.tag, json.dumps(summary, indent=1, ensure_ascii=False))
    (out / f"chat_summary-{args.tag}.md").write_text(text, encoding="utf-8")
    print(text)


def summarise(rows: list[dict]) -> dict:
    out: dict[str, Any] = {"prompts": len(rows), "errors": sum(1 for r in rows if r.get("error"))}
    lat = sorted(r["ms"] for r in rows if r.get("ms"))
    if lat:
        out["latency_s"] = {"p50": round(lat[len(lat) // 2] / 1000, 1), "p90": round(lat[int(0.9 * len(lat))] / 1000, 1), "max": round(lat[-1] / 1000, 1)}
    for kind in sorted({r["kind"] for r in rows}):
        rs = [r for r in rows if r["kind"] == kind and not r.get("error")]
        if not rs:
            continue
        block: dict[str, Any] = {"n": len(rs)}
        if kind == "unanswerable":
            block["declined"] = sum(1 for r in rs if r["refused"])
            block["answered_anyway (false positive)"] = sum(1 for r in rs if not r["refused"])
            block["cited_sources_anyway"] = sum(1 for r in rs if r["n_sources"] > 0)
        else:
            block["any_gold_document_in_sources"] = sum(1 for r in rs if r["gold_in_sources"] > 0)
            if kind == "multi_document":
                block["both_gold_documents_in_sources"] = sum(1 for r in rs if r["gold_in_sources"] >= 2)
            block["refused although answerable (false negative)"] = sum(1 for r in rs if r["refused"])
            judged = [r["judged_correct"] for r in rs if r.get("judged_correct") is not None]
            if judged:
                block["judged_correct (local model)"] = f"{sum(judged)}/{len(judged)}"
            if any("mentions_identifier" in r for r in rs):
                block["answer_mentions_identifier"] = sum(1 for r in rs if r.get("mentions_identifier"))
            block["mean_gold_share_in_sources"] = round(statistics.mean(r["gold_in_sources"] / max(1, r["gold_total"]) for r in rs), 2)
        out[kind] = block
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--out", required=True)
    g.add_argument("--mongo", default=DEFAULT_MONGO)
    g.add_argument("--db", required=True)
    g.add_argument("--ollama", default=DEFAULT_OLLAMA)
    g.add_argument("--model", default="gemma4:12b")
    g.add_argument("--single-per-type", type=int, default=1)
    g.add_argument("--identifiers", type=int, default=5)
    g.add_argument("--multi", type=int, default=10)
    g.add_argument("--listings", type=int, default=4)
    g.add_argument("--false-premises", type=int, default=4)
    g.add_argument("--seed", type=int, default=20261111)
    g.set_defaults(func=cmd_generate)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--api", default=DEFAULT_API)
    r.add_argument("--key-file", required=True)
    r.add_argument("--ollama", default=DEFAULT_OLLAMA)
    r.add_argument("--model", default="gemma4:12b")
    r.add_argument("--mode", default=None, help="agent_mode sent with every prompt (default: the product's automatic choice)")
    r.add_argument("--kinds", default="", help="comma-separated case kinds to run (single_document, identifier, multi_document, folder_listing, unanswerable)")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--tag", default=time.strftime("%Y%m%d-%H%M"))
    r.set_defaults(func=cmd_run)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
