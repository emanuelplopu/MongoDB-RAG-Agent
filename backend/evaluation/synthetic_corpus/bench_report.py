"""Render benchmark JSON results into a Markdown report."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Optional


def _load(path: Path) -> Optional[Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v * 100:.0f} %" if pct else f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _ingest_section(ing: dict[str, Any]) -> list[str]:
    c = ing.get("counts", {})
    lines = ["## Ingestion", ""]
    files = sum(v["files"] for v in ing.get("per_format", {}).values()) or (ing.get("stages") or {}).get("overall", {}).get("files")
    secs = ing.get("ingest_seconds") or 0
    lines.append(_table(["Measure", "Value"], [
        ["Job status", ing.get("status")],
        ["Wall time ingestion", f"{secs:.0f} s"],
        ["Discovery until first file done", f"{ing.get('discovery_seconds') or 0:.0f} s"],
        ["Search index build to READY", f"{ing.get('search_indexes', {}).get('seconds_to_ready', 0):.0f} s"],
        ["Files processed (stats)", files],
        ["Throughput", f"{(files or 0) / secs * 60:.1f} files/min" if secs else "–"],
        ["Documents / chunks", f"{c.get('documents')} / {c.get('chunks')}"],
        ["Tokens embedded (chunk token_count)", c.get("chunk_tokens")],
        ["Failed documents", c.get("failed_documents")],
    ]))
    stages = ing.get("stages")
    if stages:
        o = stages["overall"]
        lines += ["", "### Time per stage (from the backend log, all files)", "", _table(
            ["Stage", "files", "mean s", "p50 s", "p95 s", "max s", "total s"],
            [[name, o[key].get("n"), _fmt(o[key].get("mean")), _fmt(o[key].get("p50")), _fmt(o[key].get("p95")),
              _fmt(o[key].get("max")), _fmt(o[key].get("sum"))]
             for name, key in (("Convert (Docling, OCR)", "convert_s"), ("Chunk", "chunk_s"), ("Embed (API)", "embed_s"),
                               ("Store (MongoDB)", "store_s"), ("Total per file", "total_s"))])]
        lines += ["", "### Per format", "", _table(
            ["Format", "Files", "Convert p50 s", "Convert p95 s", "Convert max s", "Embed p50 s", "Total s", "Chunks/file"],
            [[ext, v["files"], _fmt(v["convert_s"].get("p50")), _fmt(v["convert_s"].get("p95")), _fmt(v["convert_s"].get("max")),
              _fmt(v["embed_s"].get("p50")), _fmt(v["total_s"].get("sum")), _fmt(v["chunks_per_file"])]
             for ext, v in stages["by_ext"].items()])]
        lines += ["", "Slowest files: " + "; ".join(f"{x['file'][:60]} ({x['total_s']} s)" for x in stages["slowest"][:5])]
    pf = ing.get("per_format", {})
    if pf and not stages:
        lines += ["", "### Per format (processing time per file)", "", _table(
            ["Format", "Files", "OK", "p50 s", "p95 s", "max s", "total s", "chunks/file"],
            [[ext, v["files"], v["ok"], _fmt(v["seconds"].get("p50")), _fmt(v["seconds"].get("p95")), _fmt(v["seconds"].get("max")),
              _fmt(v["seconds"].get("sum")), _fmt(v["chunks"].get("mean"))] for ext, v in pf.items()])]
    fid = ing.get("fidelity", {})
    if fid:
        rows = [[fmt, s["files"], s["ingested"], s["facts"], _fmt(s.get("content_recall"), True), _fmt(s.get("chunk_recall"), True)]
                for fmt, s in sorted(fid.get("by_format", {}).items())]
        t = fid.get("total", {})
        rows.append(["**all**", t.get("files"), t.get("ingested"), t.get("facts"), _fmt(t.get("content_recall"), True), _fmt(t.get("chunk_recall"), True)])
        lines += ["", "### Fact fidelity: are the planned values still there after conversion and chunking?", "",
                  _table(["Format", "Files", "Ingested", "Facts", "In converted text", "In chunks"], rows)]
        if fid.get("missing_documents"):
            lines += ["", f"Files not found in the database: {len(fid['missing_documents'])}"]
        if fid.get("lost_examples"):
            lines += ["", "Examples of values missing from chunks:", ""]
            lines += [f"- `{x['format']}` {x['file'].rsplit('/', 1)[-1]}: {x['value'][:80]} (in converted text: {x['in_content']})"
                      for x in fid["lost_examples"][:10]]
    res = ing.get("resources", {})
    if res:
        rows = []
        for name, v in res.items():
            if name in ("samples", "gpu"):
                continue
            rows.append([name, _fmt(v["cpu_pct"].get("mean")), _fmt(v["cpu_pct"].get("max")), _fmt(v["mem_mb"].get("max"))])
        lines += ["", "### Resources during ingestion", "", _table(["Container", "CPU % mean", "CPU % max", "Memory MB max"], rows)]
        if "gpu" in res:
            g = res["gpu"]
            lines += ["", f"GPU: utilisation mean {_fmt(g['util_pct'].get('mean'))} %, max {_fmt(g['util_pct'].get('max'))} %; "
                          f"memory max {_fmt(g['mem_mb'].get('max'))} MB; power max {_fmt(g['power_w'].get('max'))} W."]
    if ing.get("slowest_files"):
        lines += ["", "### Slowest files", "", _table(["File", "Seconds", "Chunks"],
                                                      [[f["file"], f["seconds"], f["chunks"]] for f in ing["slowest_files"]])]
    return lines


def _retrieval_section(ret: dict[str, Any]) -> list[str]:
    lines = ["## Retrieval (search API, no LLM)", ""]
    rows = [[s["mode"], s["n"], _fmt(s.get("hit@1"), True), _fmt(s.get("hit@5"), True), _fmt(s.get("hit@10"), True),
             _fmt(s.get("recall@10"), True), _fmt(s.get("mrr")), _fmt(s.get("span_hit@10"), True),
             _fmt(s.get("span_recall@10"), True), _fmt(s.get("latency_ms_p50")), _fmt(s.get("latency_ms_p95"))]
            for s in ret.get("summary", [])]
    lines.append(_table(["Mode", "n", "Hit@1", "Hit@5", "Hit@10", "Recall@10", "MRR", "Passage hit@10", "Passage recall@10",
                         "p50 ms", "p95 ms"], rows))
    by_type = [s for s in ret.get("by_type", []) if s["mode"] == "hybrid"]
    if by_type:
        lines += ["", "### Hybrid search by question type", "", _table(
            ["Type", "n", "Hit@1", "Hit@10", "Recall@10", "Passage hit@10"],
            [[s["type"], s["n"], _fmt(s.get("hit@1"), True), _fmt(s.get("hit@10"), True), _fmt(s.get("recall@10"), True),
              _fmt(s.get("span_hit@10"), True)] for s in by_type])]
    return lines


_STAGE_TEXT = {
    "correct": "Answer correct",
    "correct_decline": "Unanswerable question correctly declined",
    "answered_unanswerable": "Unanswerable question answered anyway",
    "plan_no_query": "Plan: no usable search query (empty after a JSON parse failure)",
    "retrieval_doc_miss": "Retrieval: gold document not among the agent's sources",
    "retrieval_passage_miss": "Retrieval: gold document found, gold passage not in the retrieved chunks",
    "synthesis_truncated": "Synthesis: answer cut off by the repetition guard",
    "synthesis_abstained": "Synthesis: evidence retrieved, answer says it is not in the files",
    "synthesis_incomplete": "Synthesis: evidence retrieved, answer misses values or nuggets",
    "unscored": "No values or nuggets to score",
    "error": "Request failed",
}


def _chat_section(mode: str, summary: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    a, u, t = summary.get("answerable", {}), summary.get("unanswerable", {}), summary.get("tools", {})
    models = next((r.get("models") for r in rows if r.get("models")), {})
    if set(summary.get("effective_modes") or {}) == {"fast"}:
        model_text = f"Answers by the worker model `{(models or {}).get('worker')}`; fast mode does not call the orchestrator."
    else:
        model_text = f"Models: orchestrator `{(models or {}).get('orchestrator')}`, worker `{(models or {}).get('worker')}`."
    lines = [f"## Chat, mode `{mode}`", "",
             f"{model_text} Effective modes: {summary.get('effective_modes')}. Questions: {summary.get('n')}, "
             f"errors: {summary.get('errors')}.", "",
             "### Answer quality", "",
             _table(["Measure", "Value"], [
                 ["**Correct answers** (all required values, or at least 60 % of the nuggets)", _fmt(a.get("answer_ok"), True)],
                 ["All required values in the answer", _fmt(a.get("must_include_all"), True)],
                 ["Required values found (share)", _fmt(a.get("must_include_rate"), True)],
                 ["Nugget recall (questions scored by nuggets)", _fmt(a.get("nugget_recall"), True)],
                 ["Answers containing a draft (wrong) value", a.get("must_not_violations")],
                 ["Answers cut off by the repetition guard", a.get("answers_truncated")],
                 ["False abstentions on answerable questions", _fmt(a.get("false_abstentions"), True)],
                 ["Unanswerable questions correctly declined", _fmt(u.get("abstained"), True)],
                 ["Agent sources: gold document in top 1 / top 10", f"{_fmt(a.get('agent_hit@1'), True)} / {_fmt(a.get('agent_hit@10'), True)}"],
                 ["Agent sources: gold document recall@10", _fmt(a.get("agent_recall@10"), True)],
                 ["Agent retrieved the gold passage", _fmt(a.get("agent_span_hit@10"), True)],
                 ["Citations per answer", _fmt(summary.get("citations_mean"))],
             ]),
             "", "### Tool use", "",
             _table(["Measure", "Value"], [
                 ["Expected search tool used", _fmt(t.get("expected_ok"), True)],
                 ["Forbidden tool used (web)", _fmt(t.get("forbidden_used"), True)],
                 ["Tool calls per question", _fmt(t.get("calls_mean"))],
                 ["Queries with boolean syntax or quotes", _fmt(t.get("boolean_query_rate"), True)],
                 ["Queries empty / malformed (of all queries)",
                  f"{t.get('queries_empty', '–')} / {t.get('queries_malformed', '–')} (of {t.get('queries_total', '–')})"],
                 ["Questions without any usable query", _fmt(t.get("no_usable_query_rate"), True)],
                 ["Questions with a name spelled differently in a query", _fmt(t.get("name_drift_rate"), True)],
                 ["Tools called", ", ".join(f"{k} ×{v}" for k, v in (t.get("tool_names") or {}).items())],
             ])]
    drift = sorted({d for r in rows for d in (r.get("name_drift") or [])})
    if drift:
        lines += ["", "Name drift in queries: " + ", ".join(drift[:12])]
    stages = summary.get("failure_stages") or {}
    if stages:
        lines += ["", "### Where answers were lost (first failing stage, in pipeline order)", "", _table(
            ["Stage", "Questions", "Share"],
            [[_STAGE_TEXT.get(k, k), v, _fmt(v / max(1, summary.get("n") or 1), True)] for k, v in stages.items()])]
    oj = summary.get("orchestrator_json") or {}
    log = summary.get("agent_log") or summary.get("agent_log_last_batch") or {}
    if oj or log:
        lines += ["", "### Orchestrator output health", ""]
        if oj:
            lines.append(_table(["Phase", "LLM calls", "JSON parse failures", "Cut by repetition guard"],
                                [[p, v["calls"], v["parse_failed"], v["truncated"]] for p, v in sorted(oj.items())]))
        if log:
            lines += ["", f"Backend log ({log.get('since')} to {log.get('until') or 'end'}): {log.get('orchestrator_llm_calls')} orchestrator "
                          f"LLM calls, {log.get('json_parse_failures')} JSON parse failures, repetition-guard truncations by phase "
                          f"{log.get('repetition_truncations')}, evaluate decisions {log.get('evaluate_decisions')}."]
    lat, fb = summary.get("latency_s", {}), summary.get("first_byte_s", {})
    lines += ["", "### Latency and phases", "", _table(
        ["Stage", "n", "mean s", "p50 s", "p95 s", "max s"],
        [["End to end", lat.get("n"), _fmt(lat.get("mean")), _fmt(lat.get("p50")), _fmt(lat.get("p95")), _fmt(lat.get("max"))],
         ["First byte", fb.get("n"), _fmt(fb.get("mean")), _fmt(fb.get("p50")), _fmt(fb.get("p95")), _fmt(fb.get("max"))]]
        + [[f"Phase: {p}", v.get("n"), _fmt(v.get("mean")), _fmt(v.get("p50")), _fmt(v.get("p95")), _fmt(v.get("max"))]
           for p, v in (summary.get("phases_s") or {}).items()])]
    steps = [s for r in rows for s in (r.get("orchestrator_steps") or [])]
    if steps:
        by_phase: dict[str, list[dict[str, Any]]] = {}
        for s in steps:
            by_phase.setdefault(str(s.get("phase")), []).append(s)
        lines += ["", "### LLM calls by phase (from the agent trace)", "", _table(
            ["Phase", "Calls", "mean s", "mean tokens", "mean tokens/s", "cold starts"],
            [[p, len(v), _fmt(statistics.fmean([(x.get("duration_ms") or 0) / 1000 for x in v])),
              _fmt(statistics.fmean([x.get("tokens") or 0 for x in v])),
              _fmt(statistics.fmean([x.get("tokens_per_second") or 0 for x in v])),
              sum(1 for x in v if x.get("is_cold_start"))] for p, v in sorted(by_phase.items())])]
    timing = [r.get("timing") for r in rows if r.get("timing")]
    if timing:
        def m(key: str) -> str:
            vals = [(t.get(key) or 0) / 1000 for t in timing]
            return _fmt(statistics.fmean(vals)) if vals else "–"
        lines += ["", f"Time split from the trace (mean s): orchestrator {m('orchestrator_ms')}, workers {m('worker_ms')}, "
                      f"overhead {m('overhead_ms')}, total {m('total_ms')}."]
    bt = summary.get("by_type", {})
    if bt:
        lines += ["", "### By question type", "", _table(
            ["Type", "n", "Correct", "All values", "Nugget recall", "Gold doc in top 10", "Gold passage", "Abstained", "p50 s"],
            [[k, v["n"], _fmt(v.get("answer_ok"), True), _fmt(v.get("must_include_all"), True), _fmt(v.get("nugget_recall"), True),
              _fmt(v.get("agent_hit@10"), True),
              _fmt(v.get("agent_span_hit@10"), True), _fmt(v.get("abstained"), True), _fmt(v.get("seconds_p50"))]
             for k, v in bt.items()])]
    fails = [r for r in rows if r["type"] != "unanswerable" and r.get("answer_ok") == 0][:10]
    if fails:
        lines += ["", "### Examples of wrong answers", ""]
        for r in fails:
            missing = r.get("missing_values") or [n[:60] for n in r.get("missing_nuggets") or []]
            lines.append(f"- **{r['id']}** ({r['type']}, {r.get('failure_stage')}): missing {missing}; queries "
                         f"{[q[:50] for q in r.get('queries') or []]}")
    return lines


def _comparison_section(summaries: dict[str, dict[str, Any]], title: str = "Chat modes compared") -> list[str]:
    """Side-by-side key metrics of chat runs (one column per key of ``summaries``)."""
    measures = [
        ("Correct answers", ("answerable", "answer_ok"), True),
        ("All required values", ("answerable", "must_include_all"), True),
        ("Nugget recall", ("answerable", "nugget_recall"), True),
        ("False abstentions", ("answerable", "false_abstentions"), True),
        ("Unanswerable declined", ("unanswerable", "abstained"), True),
        ("Gold document in agent sources (top 10)", ("answerable", "agent_hit@10"), True),
        ("Gold passage retrieved", ("answerable", "agent_span_hit@10"), True),
        ("Tool calls per question", ("tools", "calls_mean"), False),
        ("Questions without usable query", ("tools", "no_usable_query_rate"), True),
        ("Latency p50 s", ("latency_s", "p50"), False),
        ("Latency p95 s", ("latency_s", "p95"), False),
    ]
    rows = [[label] + [_fmt((s.get(group) or {}).get(key), pct) for s in summaries.values()]
            for label, (group, key), pct in measures]
    return [f"## {title}", "", _table(["Measure"] + [f"`{m}`" for m in summaries], rows)]


def _telemetry_section(summary: dict[str, Any]) -> list[str]:
    """Robustness, budget, Ollama, backend log, stability, resources and isolation for one chat run."""
    lines: list[str] = []
    anomalies = summary.get("anomalies")
    if anomalies is not None:
        lines += ["", "### Robustness: everything that needs a look", ""]
        lines += [f"- {a}" for a in anomalies] or ["- Nothing: no errors, fallbacks, truncations, reloads, throttling or slowdowns."]
    steps = summary.get("llm_steps") or {}
    if steps:
        lines += ["", "### LLM calls per phase (from the trace)", "", _table(
            ["Phase", "Calls", "Retries", "Unparsable", "Cut at max_tokens", "Loops trimmed", "Failed", "Prompt tok p50/max",
             "Output tok mean", "Hidden tok mean", "s p50", "Thinking"],
            [[p, s["calls"], s["retries"], s["parse_errors"], s["token_limit_hits"], s["trimmed"], s["errors"],
              f"{_fmt(s['prompt_tokens'].get('p50'))} / {_fmt(s['prompt_tokens'].get('max'))}", _fmt(s["completion_tokens"].get("mean")),
              _fmt(s["hidden_tokens_est"].get("mean")), _fmt(s["seconds"].get("p50")), s.get("think")]
             for p, s in steps.items()])]
    o = summary.get("ollama") or {}
    if o:
        lines += ["", "### Ollama server (from its log, per question)", "", _table(["Measure", "Value"], [
            ["Requests (by status)", f"{o.get('requests')} {o.get('by_status')}"],
            ["Prefill: tokens, tokens/s", f"{o.get('prefill_tokens')}, {_fmt(o.get('prefill_tok_s'))}"],
            ["Decode: tokens, tokens/s", f"{o.get('decode_tokens')}, {_fmt(o.get('decode_tok_s'))}"],
            ["Largest prompt / most context used / context size", f"{o.get('max_prompt_tokens')} / {o.get('max_context_used')} / {o.get('n_ctx')}"],
            ["Prompts truncated to fit", o.get("truncated_prompts")],
            ["Model loads (questions affected), load time", f"{o.get('loads')} ({o.get('questions_with_model_load')}), {o.get('load_s')} s"],
            ["Loads by model @ context", o.get("loads_by_model")],
            ["Evictions / aborted requests / errors", f"{o.get('evictions')} / {o.get('aborts')} / {o.get('error_count')}"],
            ["Server time for generation requests", f"{o.get('server_s')} s"],
        ])]
    b = summary.get("backend_log") or {}
    if b:
        lines += ["", "### Backend log", "", f"Warnings {b.get('warnings')}, errors {b.get('errors')}, tracebacks {b.get('tracebacks')}, "
                  f"exceptions {b.get('exceptions') or '{}'}.", ""]
        lines += [f"- {m['count']}x {m['message'][:200]}" for m in (b.get("top_messages") or [])[:10]]
    deg = summary.get("degradations") or {}
    if deg.get("by_name"):
        lines += ["", f"Fallback paths taken (questions with any: {deg.get('questions_with_any')}): {deg.get('by_name')}"]
    st = summary.get("stability") or {}
    if st:
        lines += ["", "### Stability", "", f"Mean latency first third {st['latency_first_third_mean_s']} s, last third "
                  f"{st['latency_last_third_mean_s']} s. Outliers over twice the median: {st.get('outliers_over_2x_p50') or 'none'}.", ""]
        lines += [f"- {x['id']}: {x['seconds']} s, slowest phase {x['slowest_phase']}" for x in st.get("slowest") or []]
    res = summary.get("resources_last_batch") or {}
    if res:
        rows = [[name, _fmt(v["cpu_pct"].get("mean")), _fmt(v["cpu_pct"].get("max")), _fmt(v["mem_mb"].get("max")), _fmt(v.get("mem_drift_mb"))]
                for name, v in res.items() if isinstance(v, dict) and "cpu_pct" in v]
        lines += ["", "### Resources", "", _table(["Container", "CPU % mean", "CPU % max", "Memory MB max", "Memory drift MB"], rows)]
        g = res.get("gpu") or {}
        if g:
            lines += ["", f"GPU: utilisation mean {_fmt(g['util_pct'].get('mean'))} %, memory max {_fmt(g['mem_mb'].get('max'))} MB, "
                          f"power max {_fmt(g['power_w'].get('max'))} W, temperature max {_fmt((g.get('temp_c') or {}).get('max'))} C, "
                          f"SM clock median {_fmt((g.get('sm_mhz') or {}).get('p50'))} MHz, throttled samples {g.get('throttled_samples', '–')}."]
        if res.get("ollama"):
            ol = res["ollama"]
            lines += ["", f"Ollama residency: models seen {ol.get('models_seen')}, at most {ol.get('max_models_loaded')} loaded at once, "
                          f"contexts {ol.get('context_lengths')}, samples with CPU offload {ol.get('samples_with_offload')}."]
    run = summary.get("run_last_batch") or {}
    if run:
        mongo = run.get("mongo_slow_ops") or {}
        lines += ["", f"Run: {run.get('started_at')} to {run.get('ended_at')}, concurrency {run.get('concurrency')}, Ollama "
                      f"{run.get('ollama_version_start')}. Container changes: {run.get('container_changes') or 'none'}. "
                      f"MongoDB operations over {mongo.get('slow_ms', '–')} ms: {mongo.get('slow_ops', mongo.get('error', '–'))}."]
    iso = summary.get("isolation") or {}
    if iso.get("questions"):
        lines += ["", f"Isolation: mean share of sources from another case {_fmt(iso.get('mean_foreign_source_share'), True)} "
                      f"over {iso['questions']} single-case questions; mostly foreign: {iso.get('questions_mostly_foreign') or 'none'}."]
    return lines


def write_report(bench_dir: Path, baseline_dir: Optional[Path] = None) -> Path:
    """Write ``REPORT.md`` for all results found in ``bench_dir``, optionally compared with an earlier run."""
    lines = [f"# Benchmark report: {bench_dir.parent.parent.name} / {bench_dir.name}", ""]
    run_dir = bench_dir.parent.parent
    ds = _load(run_dir / "gold" / "dataset.json") or {}
    val = (_load(run_dir / "reports" / "validation.json") or {}).get("stats", {})
    lines += [f"Corpus: {val.get('cases')} cases, {val.get('documents')} documents, {val.get('files')} files "
              f"({val.get('files_by_format')}), {val.get('questions')} gold questions. Firm: {ds.get('firm')}.", ""]
    ing = _load(bench_dir / "ingest.json")
    if ing:
        lines += _ingest_section(ing) + [""]
    ret = _load(bench_dir / "retrieval.json")
    if ret:
        lines += _retrieval_section(ret) + [""]
    qr = _load(bench_dir / "query_rewrite.json")
    if qr:
        s = qr["summary"]
        lines += ["## Effect of the planner's query rewriting on retrieval", "",
                  f"Questions: {qr['n']}, of which {qr.get('no_usable_query', 0)} had no usable planner query "
                  "(counted as misses). Hybrid search, top 10, planner queries merged round-robin.", "",
                  _table(["Query used", "Gold document in top 10", "Gold passage in top 10"],
                         [["Plain question (as in fast mode)", _fmt(s["raw"]["hit@10"], True), _fmt(s["raw"]["span_hit@10"], True)],
                          ["Planner queries as written", _fmt(s["planner"]["hit@10"], True), _fmt(s["planner"]["span_hit@10"], True)],
                          ["Planner queries without quotes/AND/OR", _fmt(s["sanitized"]["hit@10"], True), _fmt(s["sanitized"]["span_hit@10"], True)]]),
                  ""]
    summaries = {p.name[len("chat_"):-len(".summary.json")]: json.loads(p.read_text(encoding="utf-8"))
                 for p in sorted(bench_dir.glob("chat_*.summary.json"))}
    if len(summaries) > 1:
        lines += _comparison_section(summaries) + [""]
    if baseline_dir is not None:
        before = {p.name[len("chat_"):-len(".summary.json")]: json.loads(p.read_text(encoding="utf-8"))
                  for p in sorted(baseline_dir.glob("chat_*.summary.json"))}
        paired: dict[str, dict[str, Any]] = {}
        for mode in (m for m in summaries if m in before):
            paired[f"{mode} {baseline_dir.name}"] = before[mode]
            paired[f"{mode} {bench_dir.name}"] = summaries[mode]
        if paired:
            lines += _comparison_section(paired, f"Before and after: {baseline_dir.name} vs {bench_dir.name}") + [""]
    for summary_path in sorted(bench_dir.glob("chat_*.summary.json")):
        mode = summary_path.name[len("chat_"):-len(".summary.json")]
        rows_path = bench_dir / f"chat_{mode}.jsonl"
        rows = [json.loads(x) for x in rows_path.read_text(encoding="utf-8").splitlines() if x.strip()] if rows_path.is_file() else []
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        lines += _chat_section(mode, summary, rows) + _telemetry_section(summary) + [""]
    out = bench_dir / "REPORT.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
