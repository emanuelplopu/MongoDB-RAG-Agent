"""HTML telemetry page: built from a synthetic series and question rows (no browser, no hardware)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from backend.evaluation.synthetic_corpus import telemetry_html as th

META = {"version": 2, "started_at": "2026-10-15T00:00:00+00:00", "interval_s": 5.0, "containers": ["rag-backend-quellex", "rag-mongodb"],
        "host": {"hostname": "box", "os": "Windows", "cpu": "Ultra 9", "cores_physical": 24, "cores_logical": 24, "ram_total_gb": 256.0},
        "gpu": {"name": "NVIDIA RTX PRO 2000", "driver": "597.16", "mem_total_mb": 16311.0, "power_limit_w": 70.0, "sm_max_mhz": 3090.0, "fields": "full"},
        "docker": {"cpus": 23, "mem_gb": 23.5, "version": "29.8"}, "ollama": {"version": "0.40.2"}, "thermal_source": None}


def _series(n: int = 40, start: str = "2026-10-15T00:00:00+00:00") -> list[dict]:
    t0 = datetime.fromisoformat(start)
    rows = []
    for i in range(n):
        t = i * 5
        rows.append({
            "v": 2, "t": t, "ts": (t0 + timedelta(seconds=t)).isoformat(),
            "containers": {"rag-backend-quellex": {"cpu_pct": 1.0, "mem_mb": 250.0, "blk_read_mb": 10.0 + i}, "rag-mongodb": {"cpu_pct": 5.0, "mem_mb": 12000.0}},
            "gpu": {"util_pct": 95.0, "mem_util_pct": 40.0, "mem_mb": 9600.0, "mem_total_mb": 16311.0, "power_w": 70.0, "power_limit_w": 70.0,
                    "temp_c": 70.0 + i * 0.6, "tlimit_c": 25.0 - i * 0.6, "sm_mhz": 2000.0, "fan_pct": 60.0, "pstate": "P0",
                    "clock_reasons": "0x4", "reasons": ["power_cap"] if i > 5 else [], "throttle_us": {"power_cap": i * 5_000_000}},
            "host": {"cpu_pct": 10.0 + i % 3, "cpu_per_core": [5.0, 50.0, 10.0, 90.0], "ram_used_gb": 30.0, "ram_total_gb": 256.0, "ram_pct": 11.7,
                     "swap_used_gb": 0.0, "disk_read_mb": 1000.0 + i * 20, "disk_write_mb": 500.0, "net_sent_mb": 1.0, "net_recv_mb": 2.0,
                     "procs": {"ollama": {"rss_gb": 0.1, "cpu_pct": 1.0, "count": 2}, "docker_vm": {"rss_gb": 7.7, "cpu_pct": 2.0, "count": 1}}},
            "ollama": [{"name": "gemma4:12b", "size_gib": 7.84, "vram_gib": 7.84, "context_length": 32768, "offloaded": False}] if i > 2 else [],
            "health": {"ms": 15.0, "ok": True}, "sample_ms": 400,
        })
    return rows


def _questions() -> list[dict]:
    return [
        {"id": "q1", "started_at": "2026-10-15T00:00:10.000+00:00", "ended_at": "2026-10-15T00:01:20.000+00:00", "seconds": 70.0, "failure_stage": "correct",
         "events": [{"type": "phase", "phase": "plan", "status": "started", "t": 0.2}, {"type": "phase", "phase": "synthesize", "status": "started", "t": 20.0}],
         "ollama": {"decode_tokens": 700, "decode_s": 25.0, "prefill_tokens": 12000, "prefill_s": 10.0}},
        {"id": "q2", "started_at": "2026-10-15T00:01:25.000+00:00", "ended_at": "2026-10-15T00:02:50.000+00:00", "seconds": 85.0,
         "failure_stage": "retrieval_doc_miss", "events": [], "ollama": {}},
        {"id": "q3", "started_at": "2026-10-15T00:02:55.000+00:00", "ended_at": "2026-10-15T00:03:00.000+00:00", "seconds": 5.0, "error": "HTTP 500", "events": []},
    ]


def test_timeline_and_question_marks() -> None:
    rows = _series()
    tl = th.build_timeline(rows)
    assert tl.xs[0] == 0.0 and tl.xs[-1] == 195.0 and not any(tl.gap_after) and tl.t0_epoch is not None
    qs = th.question_marks(_questions(), tl)
    assert [q.id for q in qs] == ["q1", "q2", "q3"] and qs[0].x0 == 10.0 and qs[0].x1 == 80.0
    assert qs[0].outcome == "correct" and qs[1].outcome == "incorrect" and qs[2].outcome == "error" and qs[2].stage == "error"
    assert qs[0].phases == [("plan", 10.2, 30.0), ("synthesize", 30.0, 80.0)]
    assert qs[0].decode_tok_s == 28.0 and qs[0].prefill_tok_s == 1200.0 and qs[1].decode_tok_s is None
    qt = th.question_telemetry(qs[0], rows, tl.xs)
    assert qt["samples"] == 15 and qt["gpu_temp_max"] == 79.6 and qt["power_cap_share"] == 73 and qt["thermal_share"] == 0
    assert qt["vram_max_gb"] == 9.38 and qt["host_ram_max"] == 30.0


def test_timeline_breaks_at_resume_gaps() -> None:
    rows = _series(10) + _series(10, start="2026-10-15T01:00:00+00:00")
    tl = th.build_timeline(rows)
    assert tl.gap_after[9] and not tl.gap_after[8] and tl.xs[10] == 3600.0
    charts = th.build_charts(rows, tl, META, ["rag-backend-quellex"])
    io = next(c for c in charts if c.cid == "host_io")
    assert io.series[0].name == "Disk read" and io.series[0].values[1] == 4.0 and io.series[0].values[10] is None
    assert "M" in th._path(tl.xs, io.series[0].values, tl.gap_after, tl.x_max, 0.0, 10.0)


def test_build_charts_covers_gpu_host_and_containers() -> None:
    rows = _series()
    tl = th.build_timeline(rows)
    charts = {c.cid: c for c in th.build_charts(rows, tl, META, ["rag-backend-quellex", "rag-mongodb"])}
    assert {"gpu_temp", "gpu_util", "gpu_power", "gpu_clock", "vram", "gpu_fan", "host_ram", "host_cpu", "host_io",
            "ctr_cpu", "ctr_mem", "ctr_blk", "health"} <= set(charts)
    assert charts["gpu_temp"].refs == [("slowdown limit", 95)] and charts["gpu_power"].refs == [("power limit", 70.0)]
    assert charts["gpu_clock"].bands == [(30.0, 200.0, "power_cap")] and charts["gpu_clock"].refs == [("max boost", 3090.0)]
    assert [s.name for s in charts["vram"].series] == ["Used", "Ollama models"] and charts["vram"].refs == [("total", 15.9)]
    assert [s.name for s in charts["host_ram"].series] == ["Used", "Docker VM", "Ollama processes"]
    assert [s.name for s in charts["ctr_cpu"].series] == ["rag-backend-quellex", "rag-mongodb"]
    assert [s.slot for s in charts["ctr_mem"].series] == [1, 2]


def test_nice_ticks_and_labels() -> None:
    assert th.nice_ticks(0, 95) == [0, 25, 50, 75, 100] and th.nice_ticks(0, 0.0)[0] == 0
    assert th.fmt_clock(65, 600) == "1:05" and th.fmt_clock(3900, 7200) == "1:05"
    assert th.fmt_num(1234.5, 1) == "1,234.5" and th.fmt_num(None) == "–" and th.fmt_num(2.0, 2) == "2"


def test_page_renders_everything(tmp_path) -> None:
    bench = tmp_path / "run" / "bench" / "b1"
    bench.mkdir(parents=True)
    (bench / "resources_chat_thinking.jsonl").write_text("".join(json.dumps(r) + "\n" for r in _series()), encoding="utf-8")
    (bench / "resources_chat_thinking.meta.json").write_text(json.dumps(META), encoding="utf-8")
    (bench / "chat_thinking.jsonl").write_text("".join(json.dumps(r) + "\n" for r in _questions()), encoding="utf-8")
    (bench / "chat_thinking.summary.json").write_text(json.dumps({
        "anomalies": ["something odd"], "ollama": {"decode_tok_s": 29.1, "prefill_tok_s": 1208.3},
        "run_last_batch": {"concurrency": 1, "container_changes": ["rag-mongodb restarted"]}}), encoding="utf-8")
    out = th.write_telemetry_html(bench, "chat_thinking")
    assert out == bench / "telemetry_chat_thinking.html"
    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>") and "<title>run / b1: chat thinking</title>" in page
    assert "NVIDIA RTX PRO 2000" in page and "GPU temperature" in page and "Host memory" in page and "Container CPU" in page and "CPU per core" in page
    assert page.count('<figure class="chart" data-chart=') >= 12 and 'data-chart="gpu_temp"' in page
    assert "something odd" in page and "rag-mongodb restarted" in page and "Ollama loaded gemma4:12b" in page
    assert "Time at power cap" in page and "Questions correct" in page and "1 of 3" in page
    assert 'class="q correct"' in page and 'class="q error"' in page and "Answer time per question" in page
    assert "Table view, per minute" in page and page.count("<table>") >= 14
    assert "http://" not in page.split("<body>")[1].split("<script")[0]  # self-contained, nothing fetched
    data = json.loads(page.split('type="application/json">')[1].split("</script>")[0])
    assert data["charts"]["gpu_temp"]["series"][0]["values"][0] == 70.0 and data["questions"][0]["id"] == "q1" and data["xmax"] == 195.0


def test_page_from_v1_series_without_meta_or_gpu(tmp_path) -> None:
    bench = tmp_path / "run" / "bench" / "old"
    bench.mkdir(parents=True)
    rows = [{"t": i * 10.0, "ts": f"2026-10-11T22:59:{i * 10 % 60:02d}+00:00", "rag-backend-quellex": {"cpu_pct": 0.2, "mem_mb": 267.0},
             "ollama": [], "health": {"ms": 16.0, "ok": True}} for i in range(6)]
    (bench / "resources_chat_fast.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    page = th.write_telemetry_html(bench, "chat_fast").read_text(encoding="utf-8")
    assert "Container CPU" in page and "GPU temperature" not in page and "no GPU reading" in page and "CPU per core" not in page


def test_ingest_page_uses_timeline_and_inline_samples(tmp_path) -> None:
    bench = tmp_path / "run" / "bench" / "b2"
    bench.mkdir(parents=True)
    (bench / "ingest.json").write_text(json.dumps({"timeline": [{"t": 0, "processed": 0}, {"t": 50, "processed": 20}, {"t": 150, "processed": 60}],
                                                   "resource_samples": _series(20)}), encoding="utf-8")
    page = th.write_telemetry_html(bench, "ingest").read_text(encoding="utf-8")
    assert "Files processed" in page and 'data-chart="ingest"' in page and "Questions correct" not in page


def test_write_all_and_missing_series(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        th.write_telemetry_html(tmp_path, "chat_fast")
    assert th.write_all_telemetry_html(tmp_path) == []
