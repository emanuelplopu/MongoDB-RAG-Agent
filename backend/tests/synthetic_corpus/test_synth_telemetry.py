"""Benchmark telemetry parsers (no running stack needed)."""

from __future__ import annotations

from backend.evaluation.synthetic_corpus import bench
from backend.evaluation.synthetic_corpus import telemetry as tm

OLLAMA_LOG = """\
time=2026-10-11T12:46:15.367+02:00 level=INFO source=sched.go:561 msg="llama-server model predicted to exceed available memory, evicting" predicted="21.2 GiB" predicted_num_ctx=131072
time=2026-10-11T12:46:17.533+02:00 level=INFO source=llama_server.go:436 msg="starting llama-server" cmd="C:\\\\Ollama\\\\llama-server.exe --model C:\\\\blobs\\\\sha256-5cd492fc6f08287558371a1ae07453744ea33382d1b3370eaf73ad1a3e1bce28 --port 63914 --host 127.0.0.1 -c 32768 -np 1"
time=2026-10-11T12:46:20.583+02:00 level=INFO source=llama_server.go:1374 msg="llama-server started in 3.04 seconds"
slot   operator(): id  0 | task 886 | new prompt, n_ctx_slot = 32768, n_keep = 4, task.n_tokens = 7311
slot print_timing: id  0 | task 886 | prompt eval time =    1200.00 ms /  7300 tokens (    0.16 ms per token,  6083.33 tokens per second)
slot print_timing: id  0 | task 886 |        eval time =    4620.04 ms /   137 tokens (   33.97 ms per token,    29.44 tokens per second)
slot      release: id  0 | task 886 | stop processing: n_tokens = 7448, truncated = 1
[GIN] 2026/10/11 - 20:36:18 | 200 |    1m4.808s |       127.0.0.1 | POST     "/api/generate"
[GIN] 2026/10/11 - 20:36:19 | 200 |            0s |       127.0.0.1 | GET      "/api/ps"
[GIN] 2026/10/11 - 20:36:20 | 500 |     120.5ms |       127.0.0.1 | POST     "/api/generate"
time=2026-10-11T20:36:20.000+02:00 level=ERROR source=server.go:1 msg="llama runner process has terminated" error="exit status 2"
time=2026-10-11T20:36:21.000+02:00 level=INFO source=server.go:2 msg="aborting completion request due to client closing the connection"
"""


def test_go_durations() -> None:
    assert tm.go_duration_s("1m4.808s") == 64.808 and tm.go_duration_s("120.5ms") == 0.12 and tm.go_duration_s("0s") == 0.0
    assert tm.go_duration_s("52.1µs") == 0.0 and tm.go_duration_s("1h2m") == 3720.0


def test_parse_ollama_log_slice() -> None:
    o = tm.parse_ollama_log(OLLAMA_LOG, {"sha256-5cd492fc6f08": "gemma4:e4b"})
    assert o["requests"] == 2 and o["by_status"] == {"200": 1, "500": 1} and o["server_s"] == 64.93
    assert o["prefill_tokens"] == 7300 and o["decode_tokens"] == 137 and o["decode_s"] == 4.62
    assert o["n_ctx"] == 32768 and o["max_prompt_tokens"] == 7311 and o["max_context_used"] == 7448 and o["truncated_prompts"] == 1
    assert o["loads"] == [{"model": "gemma4:e4b", "ctx": 32768}] and o["load_s"] == 3.04 and o["evictions"] == 1
    assert o["error_count"] == 1 and o["aborts"] == 1


def test_ollama_log_tail_reads_slices_and_detects_rotation(tmp_path) -> None:
    log = tmp_path / "server.log"
    log.write_bytes(b"alt\n")
    tail = tm.OllamaLogTail(log)
    start = tail.offset()
    with log.open("ab") as fh:
        fh.write(b"neu\n")
    assert tail.read_from(start) == ("neu\n", False)
    log.write_bytes(b"x\n")
    assert tail.read_from(start) == ("x\n", True)
    assert tm.OllamaLogTail(None).read_from(0) == ("", False)


def test_parse_backend_log() -> None:
    text = """\
2026-10-11 10:19:03,328 - backend.agent.orchestrator - WARNING - [req=ace064e1] Failed to parse JSON from orchestrator: {
2026-10-11 10:20:03,328 - backend.agent.orchestrator - WARNING - [req=bbb] Failed to parse JSON from orchestrator: {
2026-10-11 10:21:00,000 - backend.routers.sessions - ERROR - stream failed
Traceback (most recent call last):
  File "x.py", line 1, in <module>
httpx.ReadTimeout: timed out
2026-10-11 10:22:00,000 - backend.agent.coordinator - INFO - [req=x] fine
"""
    b = tm.parse_backend_log(text)
    assert b["warnings"] == 2 and b["errors"] == 1 and b["tracebacks"] == 1 and b["exceptions"] == {"httpx.ReadTimeout": 1}
    assert b["messages"][0] == {"message": "WARNING backend.agent.orchestrator: Failed to parse JSON from orchestrator: {", "count": 2}


def test_container_changes_and_isolation() -> None:
    before = {"rag-backend-quellex": {"restarts": 0, "started_at": "a", "oom_killed": False, "status": "running"}}
    after = {"rag-backend-quellex": {"restarts": 1, "started_at": "b", "oom_killed": True, "status": "running"}}
    changes = tm.container_changes(before, after)
    assert any("restarted" in c for c in changes) and any("OOM" in c for c in changes)
    gold = ["Firm/Akten/AKT-1 A gegen B/03_Gericht/x.pdf"]
    assert tm.foreign_source_share(gold, ["Firm/Akten/AKT-1 A gegen B/01_K/y.pdf", "Firm/Akten/AKT-2 C gegen D/z.pdf"]) == 0.5
    assert tm.foreign_source_share([], ["a"]) is None and tm.case_folder("loose.pdf") is None


def test_step_record_uses_explicit_trace_fields() -> None:
    s = bench._step_record({"phase": "plan", "output": "{}", "parse_error": True, "output_truncated": False, "attempt": 2,
                            "hit_token_limit": True, "prompt_tokens": 900, "completion_tokens": 2000, "hidden_tokens_est": 1800,
                            "request_options": {"think": False}, "raw_output": "kaputt"})
    assert s["parse_failed"] == 1 and s["hit_token_limit"] == 1 and s["attempt"] == 2 and s["raw_output"] == "kaputt"


def test_telemetry_summary_and_anomalies() -> None:
    rows = [{"id": f"q{i}", "type": "exact_token", "seconds": 10.0 + (100 if i == 5 else 0), "tools": [], "phases_s": {"plan": 5.0},
             "degradations": ["plan_json_retry"] if i == 0 else [],
             "orchestrator_steps": [{"phase": "plan", "attempt": 1, "parse_failed": 0, "hit_token_limit": int(i == 1), "truncated": 0,
                                     "prompt_tokens": 800, "completion_tokens": 100, "hidden_tokens_est": 0, "duration_ms": 4000,
                                     "request_options": {"think": False}}],
             "ollama": {"requests": 2, "by_status": {"200": 2}, "prefill_tokens": 1000, "prefill_s": 1.0, "decode_tokens": 300,
                        "decode_s": 10.0, "loads": [{"model": "gemma4:12b", "ctx": 32768}] if i == 2 else [], "load_s": 3.0 if i == 2 else 0,
                        "n_ctx": 32768, "max_context_used": 9000, "max_prompt_tokens": 8700},
             "backend_log": {"warnings": 1, "errors": 0, "tracebacks": 0, "exceptions": {}, "messages": [{"message": "WARNING x: y", "count": 1}]},
             "foreign_source_share": 0.8 if i == 3 else 0.0}
            for i in range(9)]
    s = bench.summarize_chat(rows)
    assert s["degradations"]["by_name"] == {"plan_json_retry": 1}
    assert s["llm_steps"]["plan"]["token_limit_hits"] == 1 and s["llm_steps"]["plan"]["think"] == {"False": 9}
    assert s["ollama"]["loads"] == 1 and s["ollama"]["decode_tok_s"] == 30.0 and s["backend_log"]["warnings"] == 9
    assert s["stability"]["outliers_over_2x_p50"] == ["q5"] and s["isolation"]["questions_mostly_foreign"] == ["q3"]
    anomalies = bench.find_anomalies(s, rows)
    assert any("cut at max_tokens" in a for a in anomalies) and any("loaded models 1 times" in a for a in anomalies)
    assert any("another case" in a for a in anomalies) and any("Latency outliers" in a for a in anomalies)


def test_abstention_covers_geht_nicht_hervor() -> None:
    assert bench._ABSTAIN.search("Aus den vorliegenden Dokumenten geht nicht hervor, was die Videoauswertung ergab.")


def test_sign_test_and_paired_comparison(tmp_path) -> None:
    import json

    assert bench.sign_test_p(0, 0) == 1.0 and bench.sign_test_p(5, 5) == 1.0
    assert round(bench.sign_test_p(8, 0), 4) == 0.0078
    for name, oks in (("a", [1, 0, 0, 1]), ("b", [1, 1, 1, 0])):
        d = tmp_path / name
        d.mkdir()
        rows = [{"id": f"q{i}", "type": "exact_token", "answer_ok": ok, "seconds": 10.0, "agent_span_hit@10": 1,
                 "phases_s": {"synthesize": 5.0}, "answer": "x" * 10} for i, ok in enumerate(oks)]
        (d / "chat_thinking.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    out = bench.compare_runs(tmp_path, "a", "b", ("thinking",))["modes"]["thinking"]
    assert out["gained"] == ["q1", "q2"] and out["lost"] == ["q3"] and out["a"]["correct"] == 0.5 and out["b"]["correct"] == 0.75
    assert out["same_retrieval"]["n"] == 4 and (tmp_path / "b" / "ab_a_vs_b.json").is_file()
