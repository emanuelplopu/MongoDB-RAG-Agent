"""Unit tests for the benchmark scoring helpers (no running stack needed)."""

from __future__ import annotations

import json

import httpx

from backend.evaluation.synthetic_corpus import bench
from backend.evaluation.synthetic_corpus.models import GoldCase, GoldSpan


def _gold(**kw) -> GoldCase:
    base = dict(id="c-q01", dataset_id="t", case_id="c", area_id="a", type="exact_token", prompt="Wie hoch?",
                expected_tools=["search_profile_documents", "search_all_sources"], forbidden_tools=["web_search"],
                gold_documents=["A/doc1.pdf"], nuggets=["x"], must_include=["EUR 7.864,80"], reference_answer="EUR 7.864,80",
                gold_spans=[GoldSpan(source="A/doc1.pdf", quote="EUR 7.864,80", equivalent_sources=["A/_Ablage/doc1.pdf"])])
    base.update(kw)
    return GoldCase(**base)


def test_score_ranking_counts_copies_and_passages() -> None:
    g = _gold()
    m = bench.score_ranking(g, ["B/x.pdf", "A/_Ablage/doc1.pdf", "C/y.pdf"], ["nichts", "Forderung EUR 7.864,80 offen", "z"])
    assert m["first_relevant_rank"] == 2 and m["mrr"] == 0.5
    assert m["hit@1"] == 0 and m["hit@3"] == 1 and m["recall@3"] == 1.0
    assert m["span_hit@1"] == 0 and m["span_hit@3"] == 1


def test_value_matching_is_format_independent() -> None:
    c = bench._contains_answer_value
    assert c("Die Entlassung erfolgte am 3. März 2025.", "03.03.2025")
    assert c("am 1. Jänner 2026", "01.01.2026")
    assert not c("am 13.03.2025", "03.03.2025")
    assert c("offen sind 7.864,80 €", "EUR 7.864,80") and c("€ 1.700,-", "EUR 1.700,00") and c("1700 EUR", "EUR 1.700,00")
    assert not c("€ 1.700,50", "EUR 1.700,00")
    assert c("Geschäftszahl 4 cg 148/25z", "4 Cg 148/25z")


def test_score_chat_tools_answer_and_abstention() -> None:
    g = _gold()
    final = {"sources": [{"source": "A/doc1.pdf"}],
             "trace": {"worker_steps": [{"tool": "search_all_sources", "input": {"query": '"Kocher" AND Lohn'},
                                         "documents": [{"id": "abc"}]}]}}
    s = bench.score_chat(g, "Der Betrag ist EUR 7.864,80 [Source: \"Rechnung\"].", final, lambda ids: ["EUR 7.864,80"] * len(ids))
    assert s["tool_expected_ok"] == 1 and s["tool_forbidden_used"] == 0 and s["boolean_query"] == 1
    assert s["must_include_all"] == 1 and s["agent_hit@1"] == 1 and s["agent_span_hit@1"] == 1 and s["citations"] == 1
    u = bench.score_chat(_gold(unanswerable=True, must_include=[], gold_documents=[], gold_spans=[]),
                         "Dazu enthält die Akte keine Informationen.", {"trace": {}}, lambda ids: [])
    assert u["abstained"] == 1


def test_parse_ingest_log_stage_timings() -> None:
    log = "\n".join([
        "2026-10-11 09:47:10,000 - src.ingestion.ingest - INFO - Processing file 1/2: /app/m/A/a.pdf",
        "2026-10-11 09:47:12,500 - src.ingestion.ingest - INFO - Successfully converted a.pdf to markdown",
        "2026-10-11 09:47:12,510 - src.ingestion.ingest - INFO - Created 3 chunks",
        "2026-10-11 09:47:12,520 - src.ingestion.embedder - INFO - Generating embeddings for 3 chunks",
        "2026-10-11 09:47:13,020 - src.ingestion.ingest - INFO - Generated embeddings for 3 chunks",
        "2026-10-11 09:47:13,100 - src.ingestion.ingest - INFO - Saved document to MongoDB with ID: x",
        "2026-10-11 09:47:13,200 - src.ingestion.ingest - INFO - Processing file 2/2: /app/m/A/b.eml",
        "2026-10-11 09:47:13,300 - src.ingestion.ingest - INFO - Successfully extracted email: b.eml",
        "2026-10-11 09:47:13,310 - src.ingestion.ingest - INFO - Created 1 chunks",
        "2026-10-11 09:47:13,700 - src.ingestion.ingest - INFO - Saved document to MongoDB with ID: y",
    ])
    files = bench.parse_ingest_log(log)
    assert [f["ext"] for f in files] == ["pdf", "eml"]
    assert files[0]["convert_s"] == 2.5 and files[0]["embed_s"] == 0.5 and files[0]["chunks"] == 3 and files[0]["total_s"] == 3.2
    summary = bench.ingest_stage_summary(files)
    assert summary["overall"]["files"] == 2 and set(summary["by_ext"]) == {"pdf", "eml"}


def test_phase_durations_and_summary() -> None:
    events = [{"t": 0.1, "type": "phase", "phase": "analyze", "status": "started"},
              {"t": 30.0, "type": "phase", "phase": "plan", "status": "started"},
              {"t": 80.0, "type": "response"}]
    assert bench._phase_durations(events, 81.0) == {"analyze": 29.9, "plan": 50.0}
    rows = [{"id": "q1", "type": "exact_token", "seconds": 10.0, "first_byte_s": 0.1, "phases_s": {"plan": 5.0},
             "must_include_all": 1, "must_include_rate": 1.0, "agent_hit@10": 1, "abstained": 0, "tools": ["search_all_sources"],
             "tool_expected_ok": 1, "tool_forbidden_used": 0, "tool_calls": 1, "boolean_query": 0, "citations": 2},
            {"id": "u1", "type": "unanswerable", "seconds": 8.0, "first_byte_s": 0.1, "phases_s": {}, "abstained": 1,
             "tools": ["search_all_sources"], "tool_expected_ok": 1, "tool_forbidden_used": 0, "tool_calls": 1, "boolean_query": 0,
             "citations": 0}]
    s = bench.summarize_chat(rows)
    assert s["answerable"]["must_include_all"] == 1.0 and s["unanswerable"]["abstained"] == 1.0
    assert s["tools"]["tool_names"] == {"search_all_sources": 2} and s["latency_s"]["n"] == 2


def test_request_with_retry_honours_rate_limit(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(bench.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate_limit_exceeded", "retry_after": 7})
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        r = bench.request_with_retry(http, "POST", "http://x/api/v1/search/hybrid", json={"q": 1})
    assert r.status_code == 200 and calls["n"] == 2 and 8.0 in sleeps


def test_rescore_answers_updates_stored_rows(tmp_path) -> None:
    path = tmp_path / "chat_fast.jsonl"
    path.write_text(json.dumps({"id": "c-q01", "answer": "Betrag: 7.864,80 €", "must_include_all": 0}) + "\n", encoding="utf-8")
    assert bench.rescore_answers(path, [_gold()]) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["must_include_all"] == 1


def test_nugget_coverage_tolerates_inflection_and_formats() -> None:
    n = "Jede Partei trägt ihre eigenen Vertretungskosten."
    assert bench.nugget_covered("Laut Vergleich tragen beide Parteien die eigenen Vertretungskosten selbst.", n)
    assert not bench.nugget_covered("Der Vergleich regelt die Zahlung in Raten.", n)
    assert bench.nugget_covered("Die Zahlung erfolgte am 3. März 2025.", "Zahlung am 03.03.2025 erfolgt.")
    assert not bench.nugget_covered("Die Zahlung erfolgte am 4. März 2025.", "Zahlung am 03.03.2025 erfolgt.")


def test_answer_scores_use_nuggets_when_no_values() -> None:
    g = _gold(type="single_fact", must_include=[], nuggets=["Die Kosten werden gegenseitig aufgehoben.",
                                                            "Jede Partei trägt ihre eigenen Barauslagen."])
    s = bench.answer_scores(g, "Die Kosten wurden gegenseitig aufgehoben; Barauslagen trägt jede Partei selbst.")
    assert s["nugget_recall"] == 1.0 and s["answer_ok"] == 1 and s["abstained"] == 0
    s = bench.answer_scores(g, "Dazu enthalten die Unterlagen keine Information.")
    assert s["answer_ok"] == 0 and s["abstained"] == 1
    t = bench.answer_scores(_gold(), "EUR 7.864,80 ... [truncated: repetitive output detected]")
    assert t["answer_truncated"] == 1 and t["answer_ok"] == 1


def test_query_quality_flags_empty_malformed_and_name_drift() -> None:
    q = bench.query_quality("Was hat Tom Erhart mit der Puchner Baumeister GmbH vereinbart?",
                            ["", '",\n  "Terminplan Tom Erhart"', '"Tom Ehrhart" Werkvertrag', "Puchner Baumeisters Vergleich"])
    assert q["queries_empty"] == 1 and q["queries_malformed"] == 1 and q["no_usable_query"] == 0
    assert q["name_drift"] == ["Erhart->Ehrhart"]
    assert bench.query_quality("Frage", ["", None, "None"])["no_usable_query"] == 1


def test_failure_stage_follows_pipeline_order() -> None:
    fs = bench.failure_stage
    assert fs({"answer_ok": 1, "no_usable_query": 1}, False) == "correct"
    assert fs({"answer_ok": 0, "no_usable_query": 1}, False) == "plan_no_query"
    assert fs({"answer_ok": 0, "agent_hit@10": 0}, False) == "retrieval_doc_miss"
    assert fs({"answer_ok": 0, "agent_hit@10": 1, "agent_span_hit@10": 0}, False) == "retrieval_passage_miss"
    assert fs({"answer_ok": 0, "agent_hit@10": 1, "agent_span_hit@10": 1, "abstained": 1}, False) == "synthesis_abstained"
    assert fs({"answer_ok": 0, "agent_hit@10": 1, "agent_span_hit@10": 1}, False) == "synthesis_incomplete"
    assert fs({"abstained": 0}, True) == "answered_unanswerable" and fs({"error": "HTTP 500"}, False) == "error"


def test_step_record_detects_parse_failures_and_truncation() -> None:
    bad = bench._step_record({"phase": "plan", "output": '{"response": "{ ... [truncated: repetitive output detected]", "parse_error": true}'})
    assert bad["parse_failed"] == 1 and bad["truncated"] == 1
    good = bench._step_record({"phase": "evaluate", "output": '{"decision": "sufficient", "confidence": 0.9}'})
    assert good["parse_failed"] == 0 and good["truncated"] == 0
    synth = bench._step_record({"phase": "synthesize", "output": '{"response": "Antwort"}'})
    assert synth["parse_failed"] == 0
    s = bench.summarize_chat([{"id": "q", "type": "exact_token", "seconds": 1.0, "tools": [], "failure_stage": "plan_no_query",
                               "orchestrator_steps": [bad, good]}])
    assert s["failure_stages"] == {"plan_no_query": 1}
    assert s["orchestrator_json"]["plan"] == {"calls": 1, "parse_failed": 1, "truncated": 1}
