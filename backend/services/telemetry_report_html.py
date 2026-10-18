"""One-page HTML report of an ingestion job: host-agent charts plus the correlated findings.

Reuses the benchmark report renderer (:func:`backend.evaluation.synthetic_corpus.telemetry_html.build_page`)
by converting ``host_samples`` documents to its sample layout (:func:`samples_to_v2`), then adds a
section with the job's phases, step timings, slowest files and anomalies. The result is a single
self-contained file suitable for attaching to a bug report.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from backend.evaluation.synthetic_corpus.telemetry_html import build_page
from backend.models.telemetry_history import JobReport
from backend.services.telemetry_analysis import job_report
from backend.services.telemetry_store import TelemetryStore

__all__ = ["samples_to_v2", "render_job_report_html", "MAX_SAMPLES"]

#: Upper bound of samples rendered (about 55 h at 5 s); longer jobs are thinned evenly.
MAX_SAMPLES = 40_000

_GIB = 1024 ** 3
_MIB = 1024 ** 2


def _iso(ts: Any) -> Optional[str]:
    if not isinstance(ts, datetime):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.isoformat()


def _div(value: Any, by: float, digits: int = 2) -> Optional[float]:
    return round(float(value) / by, digits) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def samples_to_v2(samples: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert host-agent samples to the benchmark sampler's v2 layout (resources.py).

    Units follow that layout: host memory in GB, GPU memory in MiB, container memory and I/O in
    MB, container CPU in percent of one core. Disk and network rates are integrated into the
    cumulative MB counters the renderer differentiates; health becomes a single ``{ms, ok}``.
    """
    out: list[dict[str, Any]] = []
    t0: Optional[datetime] = None
    prev_ts: Optional[datetime] = None
    totals = {"disk_read_mb": 0.0, "disk_write_mb": 0.0, "net_recv_mb": 0.0, "net_sent_mb": 0.0}
    for sample in samples:
        ts = sample.get("ts")
        if not isinstance(ts, datetime):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        t0 = t0 or ts
        dt = (ts - prev_ts).total_seconds() if prev_ts is not None else 0.0
        prev_ts = ts
        cpu, mem, temp = sample.get("cpu") or {}, sample.get("mem") or {}, sample.get("temp") or {}
        gpu, power = sample.get("gpu") or {}, sample.get("power") or {}
        disk_io, net = sample.get("disk_io") or {}, sample.get("net") or {}
        if 0 < dt <= 120:  # integrate rates; a long gap (agent down) adds nothing
            totals["disk_read_mb"] += float(disk_io.get("read_bps") or 0) * dt / 1e6
            totals["disk_write_mb"] += float(disk_io.get("write_bps") or 0) * dt / 1e6
            totals["net_recv_mb"] += float(net.get("rx_bps") or 0) * dt / 1e6
            totals["net_sent_mb"] += float(net.get("tx_bps") or 0) * dt / 1e6

        host: dict[str, Any] = {
            "cpu_pct": cpu.get("pct"),
            "cpu_per_core": cpu.get("per_core"),
            "cpu_mhz": cpu.get("freq_mhz"),
            "ram_used_gb": _div(mem.get("used_bytes"), _GIB),
            "ram_total_gb": _div(mem.get("total_bytes"), _GIB),
            "ram_pct": mem.get("pct"),
            **{k: round(v, 1) for k, v in totals.items()},
        }
        if temp.get("cpu_pkg_c") is not None:
            host["cpu_temp_c"] = temp["cpu_pkg_c"]
        row: dict[str, Any] = {"v": 2, "t": round((ts - t0).total_seconds(), 1), "ts": _iso(ts),
                               "sample_ms": sample.get("sample_ms"), "host": {k: v for k, v in host.items() if v is not None}}
        if gpu:
            row["gpu"] = {k: v for k, v in {
                "util_pct": gpu.get("util_pct"), "mem_util_pct": gpu.get("mem_util_pct"),
                "mem_mb": _div(gpu.get("vram_used_bytes"), _MIB, 0), "mem_total_mb": _div(gpu.get("vram_total_bytes"), _MIB, 0),
                "power_w": power.get("gpu_w"), "power_limit_w": gpu.get("power_limit_w"),
                "temp_c": temp.get("gpu_c"), "mem_temp_c": temp.get("gpu_mem_c"), "tlimit_c": temp.get("gpu_headroom_c"),
                "sm_mhz": gpu.get("sm_mhz"), "mem_mhz": gpu.get("mem_mhz"), "fan_pct": gpu.get("fan_pct"),
                "pstate": gpu.get("pstate"), "reasons": gpu.get("reasons") or [],
            }.items() if v is not None}
        containers = {}
        for c in sample.get("containers") or []:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            containers[c["name"]] = {k: v for k, v in {
                "cpu_pct": round(float(c["cpu_cores"]) * 100, 1) if isinstance(c.get("cpu_cores"), (int, float)) else 0.0,
                "mem_mb": _div(c.get("mem_bytes"), 1e6, 1) or 0.0, "mem_limit_mb": _div(c.get("mem_limit_bytes"), 1e6, 1),
                "blk_read_mb": _div(c.get("blk_read_bytes"), 1e6, 1), "blk_write_mb": _div(c.get("blk_write_bytes"), 1e6, 1),
                "net_rx_mb": _div(c.get("net_rx_bytes"), 1e6, 1), "net_tx_mb": _div(c.get("net_tx_bytes"), 1e6, 1),
            }.items() if v is not None}
        if containers:
            row["containers"] = containers
        health = [h for h in sample.get("health") or [] if isinstance(h, dict)]
        if health:
            row["health"] = {"ms": max(float(h.get("ms") or 0) for h in health), "ok": all(h.get("ok") for h in health)}
        models = (sample.get("ollama") or {}).get("models")
        if isinstance(models, list):
            row["ollama"] = [{"name": m.get("name"), "vram_gib": _div(m.get("vram_bytes"), _GIB),
                              "size_gib": _div(m.get("size_bytes"), _GIB), "context_length": m.get("ctx")} for m in models]
        out.append(row)
    return out


def _meta(samples: Sequence[Mapping[str, Any]], containers: Sequence[str]) -> dict[str, Any]:
    first = samples[0] if samples else {}
    gpu = next((s.get("gpu") for s in samples if s.get("gpu")), None) or {}
    mem = first.get("mem") or {}
    has_cpu_temp = any((s.get("temp") or {}).get("cpu_pkg_c") is not None for s in samples)
    return {
        "containers": list(containers),
        "started_at": _iso(first.get("ts")),
        "thermal_source": "cpu_package" if has_cpu_temp else None,
        "host": {"hostname": (first.get("meta") or {}).get("host"),
                 "cores_logical": (first.get("cpu") or {}).get("logical"),
                 "ram_total_gb": _div(mem.get("total_bytes"), _GIB, 1)},
        "gpu": {"name": gpu.get("name"), "mem_total_mb": _div(gpu.get("vram_total_bytes"), _MIB, 0),
                "power_limit_w": gpu.get("power_limit_w")} if gpu else {},
    }


def _thin(samples: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(samples) <= limit:
        return samples
    step = len(samples) / limit
    return [samples[int(i * step)] for i in range(limit)]


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    e = html.escape
    head = "".join(f"<th>{e(str(h))}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{e('' if c is None else str(c))}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table class='jr'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _fmt_s(ms: Optional[float]) -> str:
    return "" if ms is None else f"{ms / 1000:.1f} s"


def findings_section(report: JobReport) -> str:
    """HTML section with the job's phases, steps, file types, slowest files and anomalies."""
    e = html.escape
    parts = ["<section class='jr-wrap'><style>.jr{border-collapse:collapse;margin:.5rem 0 1.5rem;font-size:13px}"
             ".jr th,.jr td{border:1px solid #ccc4;padding:3px 8px;text-align:left}.jr th{background:#8881}"
             ".sev-critical{color:#c0392b}.sev-warning{color:#b9770e}.sev-info{color:#5d6d7e}</style>"]
    parts.append(f"<h2>Job {e(report.job_id)}</h2><p>Status <b>{e(report.status or 'unknown')}</b> · "
                 f"{report.files} files ({', '.join(f'{k} {v}' for k, v in report.outcomes.items())}) · "
                 f"{report.files_per_hour or '–'} files/h · {report.agent_samples} host samples</p>")
    parts.append("<h3>Anomalies</h3>")
    if report.anomalies:
        parts.append(_table(["Severity", "Finding", "Count", "Detail"],
                            [(a.severity, a.title, a.count, a.detail) for a in report.anomalies]))
        for a in report.anomalies:
            if a.examples:
                parts.append(f"<details><summary class='sev-{e(a.severity)}'>{e(a.title)}: examples</summary><pre>"
                             + e("\n".join(str(x) for x in a.examples[:15])) + "</pre></details>")
    else:
        parts.append("<p>None detected.</p>")
    parts.append("<h3>Phases and resources</h3>")
    parts.append(_table(["Phase", "Start (UTC)", "Duration", "CPU avg %", "RAM max %", "GPU util %", "GPU °C max", "CPU W avg", "GPU W avg"],
                        [(p.phase, p.start.strftime("%Y-%m-%d %H:%M:%S"), f"{p.duration_s:.0f} s",
                          _r(p.host.get("cpu_pct")), _r(p.host.get("mem_pct_max")), _r(p.host.get("gpu_util_pct")),
                          _r(p.host.get("gpu_temp_c_max")), _r(p.host.get("cpu_power_w")), _r(p.host.get("gpu_power_w")))
                         for p in report.phases]))
    parts.append("<h3>Where the time goes (per-file steps)</h3>")
    parts.append(_table(["Step", "Files", "Median", "p95", "Max", "Total", "Share"],
                        [(s.step, s.files, _fmt_s(s.p50_ms), _fmt_s(s.p95_ms), _fmt_s(s.max_ms), _fmt_s(s.total_ms),
                          f"{s.share_pct} %" if s.share_pct is not None else "") for s in report.steps]))
    parts.append("<h3>File types</h3>")
    parts.append(_table(["Type", "Files", "ok", "failed", "empty", "Median", "p95", "ms/page"],
                        [(x.ext, x.files, x.ok, x.failed, x.empty, _fmt_s(x.p50_ms), _fmt_s(x.p95_ms), x.ms_per_page)
                         for x in report.by_ext]))
    parts.append("<h3>Slowest files</h3>")
    parts.append(_table(["File", "Outcome", "Duration", "Pages", "Steps"],
                        [(f.path, f.outcome, _fmt_s(f.duration_ms), f.pages,
                          ", ".join(f"{k} {v / 1000:.1f}s" for k, v in sorted(f.steps.items(), key=lambda kv: -kv[1])))
                         for f in report.slowest]))
    parts.append("</section>")
    return "".join(parts)


def _r(value: Any) -> str:
    return "" if value is None else f"{float(value):.1f}"


async def render_job_report_html(store: TelemetryStore, job_id: str, *, now: Optional[datetime] = None) -> Optional[str]:
    """Full HTML report for one job, or None when the job has no telemetry."""
    report = await job_report(store, job_id, now=now)
    if report is None:
        return None
    raw = await store.host_samples_between(report.start, report.end + timedelta(seconds=1), limit=MAX_SAMPLES * 4)
    raw = _thin(raw, MAX_SAMPLES)
    names = sorted({c.get("name") for s in raw for c in (s.get("containers") or []) if isinstance(c, dict) and c.get("name")})
    samples = samples_to_v2(raw)
    processed = 0
    timeline = []
    for point in report.throughput:
        processed += point.files_ok + point.files_failed + point.files_other
        timeline.append({"t": max(0.0, (point.t - report.start).total_seconds()), "processed": processed})
    title = f"Ingestion job {job_id}"
    if samples:
        page = build_page(samples, _meta(raw, names), run_name=job_id, title=title,
                          ingest={"timeline": timeline}, containers=names)
    else:
        page = (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title></head><body>"
                f"<h1>{html.escape(title)}</h1><p>No host-agent samples cover this job (agent not running).</p></body></html>")
    section = findings_section(report)
    return page.replace("</body>", section + "</body>", 1) if "</body>" in page else page + section
