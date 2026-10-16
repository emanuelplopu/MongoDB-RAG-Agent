"""One-page HTML telemetry report of a benchmark run: GPU, host, containers and the questions on one time axis.

The page is self-contained (inline CSS, SVG and a small script; no external resources), renders in light and
dark mode, and can be produced while a run is still going because it reads the files the sampler appends to:

* ``resources_<run>.jsonl`` and ``resources_<run>.meta.json`` from :class:`resources.ResourceSampler`;
* ``chat_<mode>.jsonl`` / ``chat_<mode>.summary.json`` for chat runs (question bands, outcomes, Ollama speed);
* ``ingest.json`` for the ingestion run (files processed over time).

Charts follow the data-viz rules used across the repo: one y axis per chart, thin lines, hairline grid,
categorical colours assigned in a fixed order, status colours only for outcomes, a legend for two or more
series, a crosshair tooltip that lists every series, and a per-minute table view under every chart.
"""

from __future__ import annotations

import collections
import html
import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .resources import REASON_LABELS, THERMAL_REASONS, load_series, summarize_samples

# ─── layout constants (shared with the script) ───────────────────────────────

W, H = 1000, 230
ML, MR, MT, MB = 54, 120, 12, 28
PW, PH = W - ML - MR, H - MT - MB
MAX_POINTS = 2400
MAX_HEATMAP_COLUMNS = 180

PHASE_SLOTS: dict[str, int] = {"analyze": 1, "plan": 2, "execute": 3, "synthesize": 4, "evaluate": 5, "refine": 6}
BAND_KINDS = {"power_cap": "power cap", "thermal": "thermal slowdown", "other": "other clock limit"}


# ─── data model ──────────────────────────────────────────────────────────────


@dataclass
class Series:
    """One line of a chart: a name, a value per sample (None = no reading) and its categorical colour slot."""

    name: str
    values: list[Optional[float]]
    slot: int = 1


@dataclass
class Chart:
    """A line chart over the shared time axis."""

    cid: str
    title: str
    unit: str
    series: list[Series]
    subtitle: str = ""
    y_min: float = 0.0
    y_max: Optional[float] = None
    refs: list[tuple[str, float]] = field(default_factory=list)
    bands: list[tuple[float, float, str]] = field(default_factory=list)
    decimals: int = 1


@dataclass
class QuestionMark:
    """One benchmark question on the time axis."""

    id: str
    x0: float
    x1: float
    seconds: Optional[float]
    outcome: str  # correct | incorrect | error
    stage: str
    phases: list[tuple[str, float, float]] = field(default_factory=list)
    decode_tok_s: Optional[float] = None
    prefill_tok_s: Optional[float] = None
    started_at: str = ""


@dataclass
class Timeline:
    """Shared x axis: seconds since the first sample, wall-clock per sample, and where resumed runs left gaps."""

    xs: list[float]
    epochs: list[Optional[float]]
    gap_after: list[bool]
    t0_epoch: Optional[float]

    @property
    def x_max(self) -> float:
        return self.xs[-1] if self.xs else 1.0


def _epoch(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def build_timeline(samples: Sequence[Mapping[str, Any]]) -> Timeline:
    """Place every sample on one axis by wall-clock time (falls back to ``t`` when a sample has no ``ts``)."""
    epochs = [_epoch(s.get("ts")) for s in samples]
    t0 = next((e for e in epochs if e is not None), None)
    xs: list[float] = []
    last = 0.0
    for s, e in zip(samples, epochs):
        if e is not None and t0 is not None:
            last = round(e - t0, 1)
        else:
            last = round(float(s.get("t") or last), 1)
        xs.append(max(last, xs[-1] if xs else 0.0))
    dts = [b - a for a, b in zip(xs, xs[1:]) if b > a]
    gap = (statistics.median(dts) * 5) if dts else 60.0
    gap_after = [(xs[i + 1] - xs[i]) > gap for i in range(len(xs) - 1)] + [False]
    return Timeline(xs, epochs, gap_after, t0)


# ─── questions ───────────────────────────────────────────────────────────────


def question_marks(rows: Sequence[Mapping[str, Any]], timeline: Timeline) -> list[QuestionMark]:
    """Question rows of a chat run placed on the time axis (rows outside the sampled window are kept)."""
    if timeline.t0_epoch is None:
        return []
    out: list[QuestionMark] = []
    for r in rows:
        e0, e1 = _epoch(r.get("started_at")), _epoch(r.get("ended_at"))
        if e0 is None:
            continue
        x0 = round(e0 - timeline.t0_epoch, 1)
        x1 = round(e1 - timeline.t0_epoch, 1) if e1 is not None else x0 + float(r.get("seconds") or 0)
        stage = str(r.get("failure_stage") or ("error" if r.get("error") else "unknown"))
        outcome = "error" if r.get("error") else ("correct" if stage.startswith("correct") else "incorrect")
        starts = [(e.get("phase") or "?", float(e.get("t") or 0)) for e in (r.get("events") or [])
                  if e.get("type") == "phase" and e.get("status") == "started"]
        phases = [(p, x0 + t, x0 + (starts[i + 1][1] if i + 1 < len(starts) else (x1 - x0))) for i, (p, t) in enumerate(starts)]
        o = r.get("ollama") or {}
        out.append(QuestionMark(
            id=str(r.get("id")), x0=x0, x1=max(x1, x0), seconds=r.get("seconds"), outcome=outcome, stage=stage, phases=phases,
            decode_tok_s=round(o["decode_tokens"] / o["decode_s"], 1) if o.get("decode_s") else None,
            prefill_tok_s=round(o["prefill_tokens"] / o["prefill_s"], 1) if o.get("prefill_s") else None,
            started_at=str(r.get("started_at") or "")))
    out.sort(key=lambda q: q.x0)
    return out


def question_telemetry(q: QuestionMark, samples: Sequence[Mapping[str, Any]], xs: Sequence[float]) -> dict[str, Any]:
    """GPU and host figures over the samples taken while one question ran."""
    picked = [s for s, x in zip(samples, xs) if q.x0 <= x <= q.x1]
    gpu = [s["gpu"] for s in picked if isinstance(s.get("gpu"), dict)]
    host = [s["host"] for s in picked if isinstance(s.get("host"), dict)]

    def agg(items: list[Mapping[str, Any]], key: str, fn: Any) -> Optional[float]:
        vals = [i[key] for i in items if i.get(key) is not None]
        return round(fn(vals), 1) if vals else None

    return {
        "samples": len(picked),
        "gpu_temp_max": agg(gpu, "temp_c", max), "gpu_util_mean": agg(gpu, "util_pct", statistics.fmean),
        "gpu_power_mean": agg(gpu, "power_w", statistics.fmean), "vram_max_gb": (lambda v: round(v / 1024, 2) if v else None)(agg(gpu, "mem_mb", max)),
        "power_cap_share": round(100 * sum(1 for g in gpu if "power_cap" in (g.get("reasons") or [])) / len(gpu)) if gpu else None,
        "thermal_share": round(100 * sum(1 for g in gpu if THERMAL_REASONS & set(g.get("reasons") or [])) / len(gpu)) if gpu else None,
        "host_cpu_mean": agg(host, "cpu_pct", statistics.fmean), "host_ram_max": agg(host, "ram_used_gb", max),
    }


# ─── series extraction ───────────────────────────────────────────────────────


def _col(samples: Sequence[Mapping[str, Any]], *path: str, scale: float = 1.0) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    for s in samples:
        v: Any = s
        for key in path:
            v = v.get(key) if isinstance(v, dict) else None
        out.append(round(float(v) * scale, 3) if isinstance(v, (int, float)) else None)
    return out


def _rate(values: Sequence[Optional[float]], xs: Sequence[float], gap_after: Sequence[bool]) -> list[Optional[float]]:
    """Per-second rate of a cumulative counter (None at the first sample and after gaps or resets)."""
    out: list[Optional[float]] = [None]
    for i in range(1, len(values)):
        a, b, dt = values[i - 1], values[i], xs[i] - xs[i - 1]
        if a is None or b is None or dt <= 0 or gap_after[i - 1] or b < a:
            out.append(None)
        else:
            out.append(round((b - a) / dt, 3))
    return out


def _any(values: Sequence[Optional[float]]) -> bool:
    return any(v is not None for v in values)


def _bands(samples: Sequence[Mapping[str, Any]], tl: Timeline) -> list[tuple[float, float, str]]:
    """Contiguous stretches of GPU clock-event reasons, merged by kind (power cap, thermal, other)."""
    kinds: list[Optional[str]] = []
    for s in samples:
        reasons = set((s.get("gpu") or {}).get("reasons") or [])
        if not reasons:
            kinds.append(None)
        elif reasons & THERMAL_REASONS:
            kinds.append("thermal")
        elif "power_cap" in reasons:
            kinds.append("power_cap")
        else:
            kinds.append("other")
    bands: list[tuple[float, float, str]] = []
    start: Optional[float] = None
    current: Optional[str] = None
    for i, kind in enumerate(kinds):
        x = tl.xs[i]
        nxt = tl.xs[i + 1] if i + 1 < len(tl.xs) and not tl.gap_after[i] else x + (tl.xs[i] - tl.xs[i - 1] if i else 0)
        if kind != current or (i and tl.gap_after[i - 1]):
            if current is not None and start is not None:
                bands.append((start, x, current))
            start, current = (x, kind) if kind else (None, None)
        if kind and i == len(kinds) - 1 and start is not None:
            bands.append((start, nxt, kind))
    return bands


def build_charts(samples: Sequence[Mapping[str, Any]], tl: Timeline, meta: Mapping[str, Any], containers: Sequence[str],
                 ingest_timeline: Optional[Sequence[Mapping[str, Any]]] = None) -> list[Chart]:
    """Every chart the data supports, in reading order (GPU, memory, host, containers, I/O, backend)."""
    charts: list[Chart] = []
    g_meta = meta.get("gpu") or {}
    has_gpu = any(isinstance(s.get("gpu"), dict) for s in samples)
    if has_gpu:
        temp = Series("GPU", _col(samples, "gpu", "temp_c"), 1)
        series = [temp]
        if _any(mem_t := _col(samples, "gpu", "mem_temp_c")):
            series.append(Series("GPU memory", mem_t, 2))
        if _any(board := _col(samples, "host", "cpu_temp_c")):
            series.append(Series("Board thermal zone", board, 3))
        refs = []
        limits = [t + h for t, h in zip(_col(samples, "gpu", "temp_c"), _col(samples, "gpu", "tlimit_c")) if t is not None and h is not None]
        if limits:
            refs.append(("slowdown limit", round(statistics.median(limits))))
        charts.append(Chart("gpu_temp", "GPU temperature", "°C", series, "Reading per sample; the slowdown limit is the driver's T.Limit (temperature plus headroom).",
                            refs=refs, y_max=max(100.0, (refs[0][1] + 5) if refs else 0), decimals=0))
        charts.append(Chart("gpu_util", "GPU utilisation", "%", [Series("SMs busy", _col(samples, "gpu", "util_pct"), 1),
                                                                  Series("Memory controller busy", _col(samples, "gpu", "mem_util_pct"), 2)],
                            "Share of the sample period with a kernel running / memory being read or written.", y_max=100.0, decimals=0))
        p_refs = [("power limit", float(g_meta.get("power_limit_w")))] if g_meta.get("power_limit_w") else []
        charts.append(Chart("gpu_power", "GPU power draw", "W", [Series("Power", _col(samples, "gpu", "power_w"), 1)],
                            "Board power; the power cap holds the clock down whenever the draw reaches the limit.", refs=p_refs))
        c_refs = [("max boost", float(g_meta.get("sm_max_mhz")))] if g_meta.get("sm_max_mhz") else []
        charts.append(Chart("gpu_clock", "GPU clock", "MHz", [Series("SM clock", _col(samples, "gpu", "sm_mhz"), 1)],
                            "Shaded stretches: the driver reported a reason for holding the clock down.", refs=c_refs,
                            bands=_bands(samples, tl), decimals=0))
        vram = [Series("Used", _col(samples, "gpu", "mem_mb", scale=1 / 1024), 1)]
        ollama_vram = [round(sum(m.get("vram_gib") or 0 for m in (s.get("ollama") or [])), 2) if isinstance(s.get("ollama"), list) else None
                       for s in samples]
        if _any(ollama_vram) and any(v for v in ollama_vram if v):
            vram.append(Series("Ollama models", ollama_vram, 2))
        v_refs = [("total", round(float(g_meta.get("mem_total_mb")) / 1024, 1))] if g_meta.get("mem_total_mb") else []
        charts.append(Chart("vram", "VRAM", "GB", vram, "Memory in use on the GPU; Ollama's share from its loaded-model list.", refs=v_refs, decimals=2))
        if _any(fan := _col(samples, "gpu", "fan_pct")):
            charts.append(Chart("gpu_fan", "GPU fan", "%", [Series("Fan", fan, 1)], "Intended fan speed as a share of the maximum.", y_max=100.0, decimals=0))
    has_host = any(isinstance(s.get("host"), dict) for s in samples)
    if has_host:
        ram = [Series("Used", _col(samples, "host", "ram_used_gb"), 1)]
        if _any(vm := _col(samples, "host", "procs", "docker_vm", "rss_gb")):
            ram.append(Series("Docker VM", vm, 2))
        if _any(ol := _col(samples, "host", "procs", "ollama", "rss_gb")):
            ram.append(Series("Ollama processes", ol, 3))
        if _any(sw := _col(samples, "host", "swap_used_gb")) and any(sw):
            ram.append(Series("Swap", sw, 4))
        totals = [s["host"]["ram_total_gb"] for s in samples if (s.get("host") or {}).get("ram_total_gb")]
        charts.append(Chart("host_ram", "Host memory", "GB", ram, "Resident memory of the whole host, of the Docker VM and of the Ollama processes.",
                            refs=[("total", round(max(totals), 1))] if totals else [], decimals=1))
        cpu = [Series("All cores", _col(samples, "host", "cpu_pct"), 1)]
        if _any(olc := _col(samples, "host", "procs", "ollama", "cpu_pct")):
            cpu.append(Series("Ollama processes", olc, 2))
        if _any(vmc := _col(samples, "host", "procs", "docker_vm", "cpu_pct")):
            cpu.append(Series("Docker VM", vmc, 3))
        charts.append(Chart("host_cpu", "Host CPU", "%", cpu, "Share of all cores (a process at 100 % of one core on 24 cores shows as 4 %).", y_max=100.0, decimals=0))
        io = []
        for name, key, slot in (("Disk read", "disk_read_mb", 1), ("Disk write", "disk_write_mb", 2), ("Network received", "net_recv_mb", 3),
                                ("Network sent", "net_sent_mb", 4)):
            rate = _rate(_col(samples, "host", key), tl.xs, tl.gap_after)
            if _any(rate):
                io.append(Series(name, rate, slot))
        if io:
            charts.append(Chart("host_io", "Host disk and network", "MB/s", io, "Rates from the cumulative counters between samples.", decimals=1))
    names = list(containers) + sorted({n for s in samples for n in (s.get("containers") or {})} - set(containers))
    names = [n for n in names if any(n in (s.get("containers") or {}) for s in samples)][:8]
    if names:
        charts.append(Chart("ctr_cpu", "Container CPU", "% of one core",
                            [Series(n, _col(samples, "containers", n, "cpu_pct"), i + 1) for i, n in enumerate(names)],
                            "As docker stats reports it: 100 % is one core fully busy.", decimals=0))
        charts.append(Chart("ctr_mem", "Container memory", "GB",
                            [Series(n, _col(samples, "containers", n, "mem_mb", scale=1 / 1000), i + 1) for i, n in enumerate(names)],
                            "Memory each container holds (MongoDB's WiredTiger cache dominates).", decimals=2))
        blk = []
        for i, n in enumerate(names):
            rate = _rate(_col(samples, "containers", n, "blk_read_mb"), tl.xs, tl.gap_after)
            if _any(rate):
                blk.append(Series(n, rate, i + 1))
        if blk:
            charts.append(Chart("ctr_blk", "Container block reads", "MB/s", blk, "Disk reads per container between samples.", decimals=1))
    if _any(hl := _col(samples, "health", "ms")):
        charts.append(Chart("health", "Backend health round trip", "ms", [Series("/health", hl, 1)],
                            "A slow answer here means the backend's event loop was blocked.", decimals=0))
    if ingest_timeline:
        xs_i = [float(r.get("t") or 0) for r in ingest_timeline]
        processed = [r.get("processed") for r in ingest_timeline]
        values: list[Optional[float]] = []
        j = 0
        for x in tl.xs:
            while j + 1 < len(xs_i) and xs_i[j + 1] <= x:
                j += 1
            values.append(float(processed[j]) if processed[j] is not None else None)
        charts.append(Chart("ingest", "Files processed", "files", [Series("Processed", values, 1)], "Progress of the ingestion job.", decimals=0))
    return charts


# ─── SVG rendering ───────────────────────────────────────────────────────────


def nice_ticks(lo: float, hi: float, count: int = 4) -> list[float]:
    """Round tick values covering ``lo..hi`` (1, 2, 2.5, 5 × 10^k steps)."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / max(count, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)
    v = math.floor(lo / step) * step
    ticks = []
    while True:  # the last tick is at or above ``hi`` so the axis always covers the data
        ticks.append(round(v, 6))
        if v >= hi - step * 1e-6:
            return ticks
        v += step


def _x_ticks(x_max: float) -> list[float]:
    for step in (30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400):
        if x_max / step <= 9:
            break
    return [float(v) for v in range(0, int(x_max) + 1, step)]


def fmt_clock(seconds: float, x_max: float) -> str:
    """Elapsed time label: ``mm:ss`` for short runs, ``h:mm`` from one hour on."""
    s = int(round(seconds))
    if x_max >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def fmt_num(v: Optional[float], decimals: int = 1) -> str:
    if v is None:
        return "–"
    if decimals == 0:
        return f"{v:,.0f}"
    return f"{v:,.{decimals}f}".rstrip("0").rstrip(".")


def _downsample(xs: Sequence[float], charts: Sequence[Chart], gap_after: Sequence[bool]) -> tuple[list[float], list[bool]]:
    """Average long series into at most :data:`MAX_POINTS` buckets (in place for the charts)."""
    n = len(xs)
    if n <= MAX_POINTS:
        return list(xs), list(gap_after)
    size = math.ceil(n / MAX_POINTS)
    buckets = [range(i, min(i + size, n)) for i in range(0, n, size)]
    new_xs = [round(statistics.fmean(xs[j] for j in b), 1) for b in buckets]
    new_gap = [any(gap_after[j] for j in b) for b in buckets]
    for c in charts:
        for s in c.series:
            vals = []
            for b in buckets:
                clean = [s.values[j] for j in b if s.values[j] is not None]
                vals.append(round(statistics.fmean(clean), 3) if clean else None)
            s.values = vals
    return new_xs, new_gap


def _path(xs: Sequence[float], values: Sequence[Optional[float]], gap_after: Sequence[bool], x_max: float, y_lo: float, y_hi: float) -> str:
    sx = PW / x_max if x_max else 1.0
    sy = PH / (y_hi - y_lo) if y_hi > y_lo else 1.0
    parts: list[str] = []
    pen = False
    for i, (x, v) in enumerate(zip(xs, values)):
        if v is None:
            pen = False
            continue
        px = ML + x * sx
        py = MT + PH - (min(max(v, y_lo), y_hi) - y_lo) * sy
        parts.append(f"{'L' if pen else 'M'}{px:.1f},{py:.1f}")
        pen = not gap_after[i]
    return " ".join(parts)


def _y_px(v: float, y_lo: float, y_hi: float) -> float:
    return MT + PH - (min(max(v, y_lo), y_hi) - y_lo) * (PH / (y_hi - y_lo) if y_hi > y_lo else 1.0)


def render_chart(chart: Chart, xs: Sequence[float], gap_after: Sequence[bool], x_max: float, questions: Sequence[QuestionMark]) -> str:
    """One ``<figure>`` with the SVG line chart, legend and a per-minute table view."""
    values_all = [v for s in chart.series for v in s.values if v is not None]
    hi = max([chart.y_max or 0.0] + values_all + [r for _, r in chart.refs]) if (values_all or chart.refs or chart.y_max) else 1.0
    y_lo = chart.y_min
    ticks = nice_ticks(y_lo, hi if hi > y_lo else y_lo + 1)
    y_hi = chart.y_max if chart.y_max and chart.y_max >= hi else ticks[-1]
    y_hi = max(y_hi, y_lo + 1e-9)
    e = html.escape
    out = [f'<figure class="chart" data-chart="{e(chart.cid)}" tabindex="0" aria-label="{e(chart.title)} in {e(chart.unit)}">',
           f'<figcaption><span class="ctitle">{e(chart.title)}</span> <span class="cunit">{e(chart.unit)}</span>'
           + (f'<span class="csub">{e(chart.subtitle)}</span>' if chart.subtitle else "") + "</figcaption>",
           f'<svg viewBox="0 0 {W} {H}" role="img" preserveAspectRatio="none">']
    for x0, x1, kind in chart.bands:
        px0, px1 = ML + x0 / x_max * PW, ML + x1 / x_max * PW
        out.append(f'<rect class="band {e(kind)}" x="{px0:.1f}" y="{MT}" width="{max(px1 - px0, 0.5):.1f}" height="{PH}"/>')
    for t in ticks:
        if t < y_lo or t > y_hi:
            continue
        py = _y_px(t, y_lo, y_hi)
        out.append(f'<line class="grid" x1="{ML}" y1="{py:.1f}" x2="{ML + PW}" y2="{py:.1f}"/>'
                   f'<text class="tick" x="{ML - 6}" y="{py + 3.5:.1f}" text-anchor="end">{fmt_num(t, 0 if t == int(t) else 1)}</text>')
    for xt in _x_ticks(x_max):
        px = ML + xt / x_max * PW
        out.append(f'<line class="grid v" x1="{px:.1f}" y1="{MT}" x2="{px:.1f}" y2="{MT + PH}"/>'
                   f'<text class="tick" x="{px:.1f}" y="{MT + PH + 16}" text-anchor="middle">{fmt_clock(xt, x_max)}</text>')
    if len(questions) <= 240:
        for q in questions:
            px = ML + q.x0 / x_max * PW
            out.append(f'<line class="qline" x1="{px:.1f}" y1="{MT}" x2="{px:.1f}" y2="{MT + PH}"/>')
    out.append(f'<line class="axis" x1="{ML}" y1="{MT + PH}" x2="{ML + PW}" y2="{MT + PH}"/>')
    for label, value in chart.refs:
        py = _y_px(value, y_lo, y_hi)
        out.append(f'<line class="ref" x1="{ML}" y1="{py:.1f}" x2="{ML + PW}" y2="{py:.1f}"/>'
                   f'<text class="reflabel" x="{ML + PW + 4}" y="{py + 3.5:.1f}">{e(label)} {fmt_num(value, chart.decimals)}</text>')
    labels: list[tuple[float, str, int]] = []
    for s in chart.series:
        d = _path(xs, s.values, gap_after, x_max, y_lo, y_hi)
        if d:
            out.append(f'<path class="line s{s.slot}" d="{d}" vector-effect="non-scaling-stroke"/>')
        last = next((v for v in reversed(s.values) if v is not None), None)
        if last is not None:
            labels.append((_y_px(last, y_lo, y_hi), fmt_num(last, chart.decimals), s.slot))
    labels.sort()
    placed: list[float] = []
    for py, text, slot in labels:  # push end labels apart so they stay readable when lines converge
        y = py
        if placed and y - placed[-1] < 12:
            y = placed[-1] + 12
        placed.append(y)
        if y <= MT + PH + 6 and not chart.refs:
            out.append(f'<line class="key s{slot}" x1="{ML + PW + 4}" y1="{y:.1f}" x2="{ML + PW + 14}" y2="{y:.1f}" vector-effect="non-scaling-stroke"/>'
                       f'<text class="endlabel" x="{ML + PW + 18}" y="{y + 3.5:.1f}">{text}</text>')
    out.append("</svg>")
    if len(chart.series) > 1 or chart.bands:
        items = [f'<li><span class="swatch line s{s.slot}"></span>{e(s.name)}</li>' for s in chart.series]
        for kind in sorted({b[2] for b in chart.bands}):
            items.append(f'<li><span class="swatch band {e(kind)}"></span>{e(BAND_KINDS.get(kind, kind))}</li>')
        out.append('<ul class="legend">' + "".join(items) + "</ul>")
    out.append(_table_view(chart, xs, x_max))
    out.append("</figure>")
    return "".join(out)


def _table_view(chart: Chart, xs: Sequence[float], x_max: float) -> str:
    """Per-minute means of every series (the WCAG twin of the chart)."""
    e = html.escape
    buckets: dict[int, list[list[float]]] = collections.defaultdict(lambda: [[] for _ in chart.series])
    for i, x in enumerate(xs):
        for j, s in enumerate(chart.series):
            v = s.values[i]
            if v is not None:
                buckets[int(x // 60)][j].append(v)
    rows = []
    for minute in sorted(buckets):
        cells = "".join(f"<td>{fmt_num(statistics.fmean(c), chart.decimals) if c else '–'}</td>" for c in buckets[minute])
        rows.append(f"<tr><th>{fmt_clock(minute * 60, x_max)}</th>{cells}</tr>")
    head = "".join(f"<th>{e(s.name)}</th>" for s in chart.series)
    return (f'<details class="tableview"><summary>Table view, per minute ({len(rows)} rows, {e(chart.unit)})</summary>'
            f'<div class="scroll"><table><thead><tr><th>Time</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div></details>')


def render_strips(questions: Sequence[QuestionMark], x_max: float) -> str:
    """Two thin strips above the charts: question outcome and agent phase along the time axis."""
    if not questions:
        return ""
    e = html.escape
    h_strip = 16
    out = ['<figure class="strip" aria-label="Questions along the time axis"><figcaption><span class="ctitle">Questions</span>'
           '<span class="csub">Each block is one question, coloured by outcome; the row below shows the agent phase.</span></figcaption>',
           f'<svg viewBox="0 0 {W} {2 * h_strip + 22}" preserveAspectRatio="none">']
    for n, q in enumerate(questions, start=1):
        px0, px1 = ML + q.x0 / x_max * PW, ML + q.x1 / x_max * PW
        width = max(px1 - px0 - 1, 1.0)
        tip = f"{q.id}: {q.outcome} ({q.stage}), {fmt_num(q.seconds, 0)} s"
        out.append(f'<rect class="q {q.outcome}" x="{px0:.1f}" y="0" width="{width:.1f}" height="{h_strip}" data-tip="{e(tip)}"><title>{e(tip)}</title></rect>')
        if n == 1 or n % 5 == 0:
            out.append(f'<text class="tick" x="{px0 + 2:.1f}" y="{h_strip - 4}">{n}</text>')
        for phase, a, b in q.phases:
            pa, pb = ML + a / x_max * PW, ML + b / x_max * PW
            slot = PHASE_SLOTS.get(phase, 7)
            tipp = f"{q.id} {phase}: {fmt_num(b - a, 0)} s"
            out.append(f'<rect class="phase s{slot}" x="{pa:.1f}" y="{h_strip + 2}" width="{max(pb - pa - 0.5, 0.8):.1f}" height="{h_strip - 2}" data-tip="{e(tipp)}"><title>{e(tipp)}</title></rect>')
    for xt in _x_ticks(x_max):
        px = ML + xt / x_max * PW
        out.append(f'<text class="tick" x="{px:.1f}" y="{2 * h_strip + 16}" text-anchor="middle">{fmt_clock(xt, x_max)}</text>')
    out.append("</svg>")
    items = [f'<li><span class="swatch q {o}"></span>{label}</li>' for o, label in (("correct", "✓ correct"), ("incorrect", "✗ not correct"), ("error", "! error"))]
    phases_seen = sorted({p for q in questions for p, _, _ in q.phases}, key=lambda p: PHASE_SLOTS.get(p, 7))
    items += [f'<li><span class="swatch fill s{PHASE_SLOTS.get(p, 7)}"></span>{e(p)}</li>' for p in phases_seen]
    out.append('<ul class="legend">' + "".join(items) + "</ul></figure>")
    return "".join(out)


def render_bars(cid: str, title: str, unit: str, items: Sequence[tuple[str, Optional[float], str]], subtitle: str = "", decimals: int = 0) -> str:
    """A column chart over questions (one column per question, coloured by outcome)."""
    e = html.escape
    if not items or not any(v is not None for _, v, _ in items):
        return ""
    w, h, ml, mt, mb = 480, 180, 46, 10, 26
    pw, ph = w - ml - 8, h - mt - mb
    hi = max(v for _, v, _ in items if v is not None)
    ticks = nice_ticks(0, hi if hi > 0 else 1)
    y_hi = ticks[-1] or 1.0
    slot_w = pw / len(items)
    bar_w = min(24.0, max(slot_w - 2, 1.0))
    out = [f'<figure class="chart bars" aria-label="{e(title)}"><figcaption><span class="ctitle">{e(title)}</span> <span class="cunit">{e(unit)}</span>'
           + (f'<span class="csub">{e(subtitle)}</span>' if subtitle else "") + "</figcaption>",
           f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none">']
    for t in ticks:
        py = mt + ph - t / y_hi * ph
        out.append(f'<line class="grid" x1="{ml}" y1="{py:.1f}" x2="{ml + pw}" y2="{py:.1f}"/>'
                   f'<text class="tick" x="{ml - 6}" y="{py + 3.5:.1f}" text-anchor="end">{fmt_num(t, 0 if t == int(t) else 1)}</text>')
    out.append(f'<line class="axis" x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}"/>')
    for i, (label, v, outcome) in enumerate(items):
        x = ml + i * slot_w + (slot_w - bar_w) / 2
        if v is None:
            continue
        bh = v / y_hi * ph
        tip = f"{label}: {fmt_num(v, decimals)} {unit} ({outcome})"
        out.append(f'<rect class="bar {e(outcome)}" x="{x:.1f}" y="{mt + ph - bh:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" rx="2" data-tip="{e(tip)}"><title>{e(tip)}</title></rect>')
        if len(items) <= 60 and (i == 0 or (i + 1) % 5 == 0):
            out.append(f'<text class="tick" x="{x + bar_w / 2:.1f}" y="{mt + ph + 14}" text-anchor="middle">{i + 1}</text>')
    out.append("</svg>")
    rows = "".join(f"<tr><th>{e(label)}</th><td>{fmt_num(v, decimals)}</td><td>{e(outcome)}</td></tr>" for label, v, outcome in items)
    out.append(f'<details class="tableview"><summary>Table view ({len(items)} rows)</summary><div class="scroll"><table><thead><tr><th>Question</th>'
               f"<th>{e(unit)}</th><th>Outcome</th></tr></thead><tbody>{rows}</tbody></table></div></details></figure>")
    return "".join(out)


def render_heatmap(samples: Sequence[Mapping[str, Any]], xs: Sequence[float], x_max: float) -> str:
    """Per-core CPU utilisation as a sequential heatmap (cores × time buckets)."""
    cores = [s["host"]["cpu_per_core"] for s in samples if isinstance((s.get("host") or {}).get("cpu_per_core"), list)]
    if not cores:
        return ""
    n_cores = max(len(c) for c in cores)
    idx = [i for i, s in enumerate(samples) if isinstance((s.get("host") or {}).get("cpu_per_core"), list)]
    n_cols = min(MAX_HEATMAP_COLUMNS, len(idx))
    size = math.ceil(len(idx) / n_cols)
    buckets = [idx[i:i + size] for i in range(0, len(idx), size)]
    cell_h = 7
    h = MT + cell_h * n_cores + MB
    out = ['<figure class="chart heat" aria-label="CPU per core"><figcaption><span class="ctitle">CPU per core</span> <span class="cunit">%</span>'
           '<span class="csub">Mean utilisation of every logical core per time bucket, on the scale below (idle to fully busy).</span></figcaption>',
           f'<svg viewBox="0 0 {W} {h}" preserveAspectRatio="none">']
    table: list[list[Optional[float]]] = [[None] * n_cores for _ in buckets]
    for bi, b in enumerate(buckets):
        x0 = ML + xs[b[0]] / x_max * PW
        x1 = ML + (xs[b[-1] + 1] if b[-1] + 1 < len(xs) else x_max) / x_max * PW
        width = max(x1 - x0, 1.0)
        for core in range(n_cores):
            vals = [samples[i]["host"]["cpu_per_core"][core] for i in b if core < len(samples[i]["host"]["cpu_per_core"])]
            if not vals:
                continue
            v = statistics.fmean(vals)
            table[bi][core] = round(v, 1)
            step = min(7, max(1, int(v // 14.3) + 1))
            tip = f"core {core} at {fmt_clock(xs[b[0]], x_max)}: {v:.0f} %"
            out.append(f'<rect class="cell q{step}" x="{x0:.1f}" y="{MT + core * cell_h}" width="{width:.1f}" height="{cell_h}" data-tip="{html.escape(tip)}"/>')
    for core in range(0, n_cores, 4):
        out.append(f'<text class="tick" x="{ML - 6}" y="{MT + core * cell_h + cell_h:.1f}" text-anchor="end">{core}</text>')
    for xt in _x_ticks(x_max):
        px = ML + xt / x_max * PW
        out.append(f'<text class="tick" x="{px:.1f}" y="{h - 8}" text-anchor="middle">{fmt_clock(xt, x_max)}</text>')
    out.append("</svg>")
    out.append('<ul class="legend scale">' + "".join(f'<li><span class="swatch fill q{s}"></span>{"0" if s == 1 else f"{(s - 1) * 14.3:.0f}"}–{s * 14.3:.0f}</li>' for s in range(1, 8))
               + "</ul>")
    head = "".join(f"<th>{c}</th>" for c in range(n_cores))
    rows = "".join(f"<tr><th>{fmt_clock(xs[b[0]], x_max)}</th>" + "".join(f"<td>{fmt_num(v, 0)}</td>" for v in table[bi]) + "</tr>" for bi, b in enumerate(buckets))
    out.append(f'<details class="tableview"><summary>Table view ({len(buckets)} buckets × {n_cores} cores)</summary><div class="scroll"><table><thead><tr><th>Time</th>{head}</tr></thead><tbody>{rows}</tbody></table></div></details></figure>')
    return "".join(out)


# ─── page assembly ───────────────────────────────────────────────────────────


def _tile(label: str, value: str, sub: str = "", tone: str = "") -> str:
    e = html.escape
    return (f'<div class="tile {e(tone)}"><div class="tlabel">{e(label)}</div><div class="tvalue">{e(value)}</div>'
            + (f'<div class="tsub">{e(sub)}</div>' if sub else "") + "</div>")


def _dl(rows: Sequence[tuple[str, Any]]) -> str:
    e = html.escape
    return "<dl>" + "".join(f"<dt>{e(k)}</dt><dd>{e(str(v))}</dd>" for k, v in rows if v not in (None, "", [], {})) + "</dl>"


def _fmt_duration(seconds: float) -> str:
    s = int(round(seconds))
    if s >= 3600:
        return f"{s // 3600} h {(s % 3600) // 60:02d} min"
    return f"{s // 60} min {s % 60:02d} s"


def _events(samples: Sequence[Mapping[str, Any]], xs: Sequence[float], x_max: float) -> list[str]:
    """Model loads and unloads, health failures and probe errors seen in the series."""
    events: list[str] = []
    prev: Optional[frozenset[str]] = None
    for s, x in zip(samples, xs):
        if isinstance(s.get("ollama"), list):
            now = frozenset(f"{m.get('name')} ({m.get('vram_gib')} of {m.get('size_gib')} GiB in VRAM, ctx {m.get('context_length')})" for m in s["ollama"])
            if prev is not None and now != prev:
                for m in sorted(now - prev):
                    events.append(f"{fmt_clock(x, x_max)} Ollama loaded {m}")
                for m in sorted(prev - now):
                    events.append(f"{fmt_clock(x, x_max)} Ollama unloaded {m}")
            prev = now
        if isinstance(s.get("health"), dict) and not s["health"].get("ok"):
            events.append(f"{fmt_clock(x, x_max)} backend health check failed")
        for probe, err in (s.get("errors") or {}).items():
            events.append(f"{fmt_clock(x, x_max)} probe {probe} failed: {err}")
    return events[:200]


def build_page(samples: Sequence[Mapping[str, Any]], meta: Mapping[str, Any], *, run_name: str, title: str,
               question_rows: Sequence[Mapping[str, Any]] = (), summary: Optional[Mapping[str, Any]] = None,
               ingest: Optional[Mapping[str, Any]] = None, containers: Sequence[str] = ()) -> str:
    """Render the whole page from samples, machine facts and (for chat runs) the question rows and summary."""
    e = html.escape
    summary = summary or {}
    tl = build_timeline(samples)
    containers = list(containers or meta.get("containers") or [])
    agg = summarize_samples(samples, containers)
    questions = question_marks(question_rows, tl)
    x_max = max(tl.x_max, max((q.x1 for q in questions), default=0.0), 1.0)
    charts = build_charts(samples, tl, meta, containers, (ingest or {}).get("timeline"))
    xs, gap_after = _downsample(tl.xs, charts, tl.gap_after)

    g, h = agg.get("gpu") or {}, agg.get("host") or {}
    g_meta, h_meta, d_meta, o_meta = meta.get("gpu") or {}, meta.get("host") or {}, meta.get("docker") or {}, meta.get("ollama") or {}
    duration = agg.get("duration_s") or x_max
    reasons_s = g.get("reasons_s") or {}
    tiles = [_tile("Run time", _fmt_duration(duration), f"{agg.get('samples', 0)} samples every {agg.get('interval_s', '–')} s")]
    if questions:
        correct = sum(1 for q in questions if q.outcome == "correct")
        secs = [q.seconds for q in questions if q.seconds]
        tiles.append(_tile("Questions correct", f"{correct} of {len(questions)}", f"median answer {fmt_num(statistics.median(secs), 0)} s" if secs else ""))
    if g.get("temp_c"):
        hot = g["temp_c"]["max"]
        tone = "critical" if (g.get("thermal_s") or 0) > 0 else ("warning" if g.get("tlimit_min_c") is not None and g["tlimit_min_c"] <= 3 else "")
        tiles.append(_tile("GPU temperature max", f"{fmt_num(hot, 0)} °C", f"mean {fmt_num(g['temp_c']['mean'], 0)} °C"
                           + (f", headroom min {fmt_num(g['tlimit_min_c'], 0)} °C" if g.get("tlimit_min_c") is not None else ""), tone))
        cap_s = reasons_s.get("power_cap", 0.0)
        tiles.append(_tile("Time at power cap", f"{100 * cap_s / duration:.0f} %" if duration else "–", f"{_fmt_duration(cap_s)} of the run"))
        tiles.append(_tile("Thermal slowdown", _fmt_duration(g.get("thermal_s") or 0), "driver-reported thermal clock limits", "critical" if g.get("thermal_s") else ""))
    if g.get("mem_mb"):
        total = g.get("mem_total_mb") or g_meta.get("mem_total_mb")
        tiles.append(_tile("VRAM peak", f"{g['mem_mb']['max'] / 1024:.1f} GB", f"of {total / 1024:.1f} GB" if total else ""))
    if g.get("power_w"):
        limit = g.get("power_limit_w") or g_meta.get("power_limit_w")
        tiles.append(_tile("GPU power mean", f"{fmt_num(g['power_w']['mean'], 0)} W", f"limit {fmt_num(limit, 0)} W" if limit else f"max {fmt_num(g['power_w']['max'], 0)} W"))
    if g.get("sm_mhz"):
        tiles.append(_tile("SM clock median", f"{fmt_num(g['sm_mhz']['p50'], 0)} MHz", f"min {fmt_num(g['sm_mhz']['min'], 0)}, max {fmt_num(g['sm_mhz']['max'], 0)} MHz"))
    if h.get("ram_used_gb"):
        tiles.append(_tile("Host RAM peak", f"{fmt_num(h['ram_used_gb']['max'], 1)} GB", f"of {fmt_num(h.get('ram_total_gb'), 0)} GB" if h.get("ram_total_gb") else ""))
    if h.get("cpu_pct"):
        tiles.append(_tile("Host CPU mean", f"{fmt_num(h['cpu_pct']['mean'], 0)} %", f"peak {fmt_num(h['cpu_pct']['max'], 0)} %"))
    o = summary.get("ollama") or {}
    if o.get("decode_tok_s"):
        tiles.append(_tile("Ollama decode", f"{fmt_num(o['decode_tok_s'], 1)} tok/s", f"prefill {fmt_num(o.get('prefill_tok_s'), 0)} tok/s"))

    facts = [
        ("GPU", _dl([("Model", g_meta.get("name")), ("Driver", g_meta.get("driver")), ("VRAM", f"{g_meta['mem_total_mb'] / 1024:.1f} GB" if g_meta.get("mem_total_mb") else None),
                      ("Power limit", f"{fmt_num(g_meta.get('power_limit_w'), 0)} W" + (f" (max {fmt_num(g_meta.get('power_max_limit_w'), 0)} W)" if g_meta.get("power_max_limit_w") else "") if g_meta.get("power_limit_w") else None),
                      ("Max clocks", f"SM {fmt_num(g_meta.get('sm_max_mhz'), 0)} MHz, memory {fmt_num(g_meta.get('mem_max_mhz'), 0)} MHz" if g_meta.get("sm_max_mhz") else None),
                      ("PCIe", f"gen {fmt_num(g_meta.get('pcie_gen_max'), 0)} x{fmt_num(g_meta.get('pcie_width_max'), 0)}" if g_meta.get("pcie_gen_max") else None),
                      ("P-states seen", ", ".join(f"{k} ({v})" for k, v in sorted((g.get("pstates") or {}).items())) or None),
                      ("nvidia-smi fields", g_meta.get("fields"))])),
        ("Host", _dl([("Name", h_meta.get("hostname")), ("OS", h_meta.get("os")), ("CPU", h_meta.get("cpu")),
                       ("Cores", f"{h_meta.get('cores_physical')} physical, {h_meta.get('cores_logical')} logical" if h_meta.get("cores_logical") else None),
                       ("RAM", f"{fmt_num(h_meta.get('ram_total_gb'), 0)} GB" if h_meta.get("ram_total_gb") else None),
                       ("Docker engine", f"{d_meta.get('cpus')} CPUs, {fmt_num(d_meta.get('mem_gb'), 1)} GB, v{d_meta.get('version')}" if d_meta.get("cpus") else None),
                       ("Thermal sensor", meta.get("thermal_source"))])),
        ("Models", _dl([("Ollama", o_meta.get("version")), ("Models seen", ", ".join((agg.get("ollama") or {}).get("models_seen") or []) or None),
                         ("Context lengths", ", ".join(str(c) for c in (agg.get("ollama") or {}).get("context_lengths") or []) or None),
                         ("Samples with CPU offload", (agg.get("ollama") or {}).get("samples_with_offload")),
                         ("Ollama VRAM peak", f"{(agg.get('ollama') or {}).get('vram_gib_max')} GiB" if (agg.get("ollama") or {}).get("vram_gib_max") else None)])),
        ("Run", _dl([("Series", run_name), ("Started", meta.get("started_at") or (samples[0].get("ts") if samples else None)),
                      ("Ended", samples[-1].get("ts") if samples else None), ("Sampling", f"every {agg.get('interval_s')} s, {agg.get('samples')} samples"),
                      ("Probe time", f"median {(agg.get('sample_ms') or {}).get('p50')} ms" if agg.get("sample_ms") else None),
                      ("Concurrency", (summary.get("run_last_batch") or {}).get("concurrency")),
                      ("Probe errors", ", ".join(f"{k} ×{v}" for k, v in (agg.get("probe_errors") or {}).items()) or None)])),
    ]

    notes: list[str] = list(summary.get("anomalies") or [])
    for reason, secs in sorted(reasons_s.items(), key=lambda kv: -kv[1]):
        if reason in THERMAL_REASONS:
            notes.append(f"GPU under a thermal clock limit ({REASON_LABELS.get(reason, reason)}) for {_fmt_duration(secs)}")
    counters = g.get("throttle_counters_s") or {}
    if any(counters.values()):
        notes.append("Driver throttle counters over the run: " + ", ".join(f"{REASON_LABELS.get(k, k)} {_fmt_duration(v)}" for k, v in counters.items() if v))
    if h.get("swap_used_gb") and h["swap_used_gb"]["max"] > 0.5:
        notes.append(f"Swap in use: up to {fmt_num(h['swap_used_gb']['max'], 1)} GB")
    if h.get("ram_pct") and h["ram_pct"]["max"] > 90:
        notes.append(f"Host RAM reached {fmt_num(h['ram_pct']['max'], 0)} %")
    events = _events(samples, tl.xs, x_max)
    run_info = summary.get("run_last_batch") or {}
    for change in run_info.get("container_changes") or []:
        events.append(f"container: {change}")

    per_question_rows = []
    for n, q in enumerate(questions, start=1):
        qt = question_telemetry(q, samples, tl.xs)
        started = q.started_at[11:19] if len(q.started_at) >= 19 else q.started_at
        per_question_rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in (
            n, e(q.id), started, fmt_num(q.seconds, 0), e(q.outcome), e(q.stage), fmt_num(q.decode_tok_s, 1), fmt_num(q.prefill_tok_s, 0),
            fmt_num(qt["gpu_temp_max"], 0), fmt_num(qt["gpu_util_mean"], 0), fmt_num(qt["gpu_power_mean"], 0), fmt_num(qt["power_cap_share"], 0),
            fmt_num(qt["thermal_share"], 0), fmt_num(qt["vram_max_gb"], 2), fmt_num(qt["host_cpu_mean"], 0), fmt_num(qt["host_ram_max"], 1))) + "</tr>")

    chart_data = {"layout": {"w": W, "h": H, "ml": ML, "mt": MT, "pw": PW, "ph": PH}, "xs": xs, "xmax": x_max,
                  "t0": tl.t0_epoch, "charts": {}, "questions": [{"id": q.id, "x0": q.x0, "x1": q.x1, "outcome": q.outcome} for q in questions]}
    for c in charts:
        values_all = [v for s in c.series for v in s.values if v is not None]
        hi = max([c.y_max or 0.0] + values_all + [r for _, r in c.refs]) if (values_all or c.refs or c.y_max) else 1.0
        ticks = nice_ticks(c.y_min, hi if hi > c.y_min else c.y_min + 1)
        chart_data["charts"][c.cid] = {"unit": c.unit, "decimals": c.decimals, "ymin": c.y_min,
                                       "ymax": c.y_max if c.y_max and c.y_max >= hi else ticks[-1],
                                       "series": [{"name": s.name, "slot": s.slot, "values": s.values} for s in c.series]}

    question_bars = ""
    if questions:
        items = [(q.id, q.seconds, q.outcome) for q in questions]
        question_bars = '<div class="grid3">' + render_bars("q_latency", "Answer time per question", "s", items, "In the order the questions ran.") \
            + render_bars("q_decode", "Ollama decode speed per question", "tok/s", [(q.id, q.decode_tok_s, q.outcome) for q in questions],
                          "Generated tokens per second from the Ollama log slice of each question.", 1) \
            + render_bars("q_prefill", "Ollama prefill speed per question", "tok/s", [(q.id, q.prefill_tok_s, q.outcome) for q in questions],
                          "Prompt tokens processed per second.", 0) + "</div>"

    gpu_label = g_meta.get("name") or ("GPU (model not recorded: no meta file)" if g else "no GPU reading")
    body = [
        f"<header><h1>{e(title)}</h1><p class=\"lead\">Telemetry of <code>{e(run_name)}</code>: {e(gpu_label)}"
        f" on {e(h_meta.get('hostname') or 'this host')}, {_fmt_duration(duration)} sampled every {agg.get('interval_s', '–')} s.</p></header>",
        '<section class="tiles">' + "".join(tiles) + "</section>",
        '<section class="facts">' + "".join(f"<div class=\"fact\"><h2>{e(k)}</h2>{v}</div>" for k, v in facts if v != "<dl></dl>") + "</section>",
        "<section><h2>What needs a look</h2>" + ("<ul class=\"notes\">" + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul>" if notes
                                                  else "<p class=\"muted\">Nothing: no throttling, errors, reloads or memory pressure were recorded.</p>") + "</section>",
        '<section class="charts">' + render_strips(questions, x_max) + "".join(render_chart(c, xs, gap_after, x_max, questions) for c in charts)
        + render_heatmap(samples, tl.xs, x_max) + "</section>",
        question_bars,
    ]
    if per_question_rows:
        body.append('<section><details class="tableview big"><summary>Per question: timing, speed and the GPU while it ran (' + str(len(per_question_rows)) + ' rows)</summary>'
                    '<div class="scroll"><table><thead><tr><th>#</th><th>Question</th><th>Start (UTC)</th><th>s</th><th>Outcome</th><th>Stage</th>'
                    '<th>Decode tok/s</th><th>Prefill tok/s</th><th>GPU °C max</th><th>GPU % mean</th><th>W mean</th><th>Power cap %</th><th>Thermal %</th>'
                    '<th>VRAM GB max</th><th>Host CPU % mean</th><th>Host RAM GB max</th></tr></thead><tbody>' + "".join(per_question_rows)
                    + "</tbody></table></div></details></section>")
    if events:
        body.append("<section><h2>Events</h2><ul class=\"notes\">" + "".join(f"<li>{e(ev)}</li>" for ev in events) + "</ul></section>")
    body.append(f'<footer><p class="muted">Generated {datetime.now(timezone.utc).isoformat(timespec="seconds")} from resources_{e(run_name)}.jsonl. '
                "Charts use the sampler's readings; per-minute tables under every chart hold the same data without hovering.</p></footer>")
    data_json = json.dumps(chart_data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (_HTML_HEAD.replace("__TITLE__", e(title)) + "\n".join(body)
            + f'\n<script id="telemetry-data" type="application/json">{data_json}</script>\n<script>{_SCRIPT}</script>\n</body></html>')


# ─── files ───────────────────────────────────────────────────────────────────


def _load_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (json.JSONDecodeError, OSError):
        return None


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_telemetry_html(bench_dir: Path, run_name: str, out: Optional[Path] = None) -> Path:
    """Render ``telemetry_<run_name>.html`` from the files of one sampled run in ``bench_dir``.

    ``run_name`` is the suffix of the series file (``chat_thinking``, ``chat_fast_c3``, ``ingest``).
    """
    series_path = bench_dir / f"resources_{run_name}.jsonl"
    samples = load_series(series_path) if series_path.is_file() else []
    ingest = _load_json(bench_dir / "ingest.json") if run_name == "ingest" else None
    if not samples and ingest and ingest.get("resource_samples"):
        from .resources import normalize_sample

        samples = [normalize_sample(s) for s in ingest["resource_samples"]]
    if not samples:
        raise FileNotFoundError(f"no samples for {run_name}: {series_path} is missing or empty")
    meta = _load_json(bench_dir / f"resources_{run_name}.meta.json") or {}
    question_rows = _load_rows(bench_dir / f"{run_name}.jsonl") if run_name.startswith("chat_") else []
    summary = _load_json(bench_dir / f"{run_name}.summary.json") if run_name.startswith("chat_") else None
    if ingest and not summary:
        summary = {"anomalies": [], "run_last_batch": {}}
    title = f"{bench_dir.parent.parent.name} / {bench_dir.name}: {run_name.replace('_', ' ')}"
    page = build_page(samples, meta, run_name=run_name, title=title, question_rows=question_rows, summary=summary, ingest=ingest,
                      containers=meta.get("containers") or [])
    out = out or bench_dir / f"telemetry_{run_name}.html"
    out.write_text(page, encoding="utf-8")
    return out


def write_all_telemetry_html(bench_dir: Path) -> list[Path]:
    """Render a page for every ``resources_*.jsonl`` in ``bench_dir`` (failures are logged, not raised)."""
    import logging

    out: list[Path] = []
    for path in sorted(bench_dir.glob("resources_*.jsonl")):
        run_name = path.name[len("resources_"):-len(".jsonl")]
        try:
            out.append(write_telemetry_html(bench_dir, run_name))
        except Exception as exc:  # noqa: BLE001 - one bad series must not stop the report
            logging.getLogger(__name__).warning("telemetry page for %s failed: %s", run_name, exc)
    return out


# ─── static page parts ───────────────────────────────────────────────────────

_HTML_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{color-scheme:light;--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--s7:#4a3aa7;--s8:#e34948;
--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;
--q1:#cde2fb;--q2:#b7d3f6;--q3:#9ec5f4;--q4:#6da7ec;--q5:#3987e5;--q6:#256abf;--q7:#184f95}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--s7:#9085e9;--s8:#e66767;--q1:#0d366b;--q2:#104281;--q3:#184f95;--q4:#1c5cab;--q5:#256abf;--q6:#3987e5;--q7:#86b6ef}}
:root[data-theme="dark"]{color-scheme:dark;--surface:#1a1a19;--page:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--s7:#9085e9;--s8:#e66767;--q1:#0d366b;--q2:#104281;--q3:#184f95;--q4:#1c5cab;--q5:#256abf;--q6:#3987e5;--q7:#86b6ef}
*{box-sizing:border-box}
body{margin:0;padding:24px 16px 48px;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main,body>*{max-width:1100px;margin-left:auto;margin-right:auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:0 0 8px;color:var(--ink2);font-weight:600}
.lead{margin:0 0 20px;color:var(--ink2)}.muted{color:var(--muted)}code{font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:20px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 12px}
.tile.warning{border-color:var(--warning)}.tile.critical{border-color:var(--critical)}
.tlabel{font-size:12px;color:var(--ink2)}.tvalue{font-size:24px;font-weight:600;margin:2px 0}.tsub{font-size:12px;color:var(--muted)}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px;margin-bottom:20px}
.fact{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 12px}
.fact dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:2px 10px;font-size:13px}.fact dt{color:var(--muted)}.fact dd{margin:0;overflow-wrap:anywhere}
section{margin-bottom:22px}.notes{margin:0;padding-left:20px}.notes li{margin:2px 0}
figure.chart,figure.strip{margin:0 0 18px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 12px 8px}
figure:focus{outline:2px solid var(--s1);outline-offset:2px}
figcaption{margin-bottom:4px}.ctitle{font-weight:600}.cunit{color:var(--muted);margin-left:4px}.csub{display:block;font-size:12px;color:var(--muted)}
svg{display:block;width:100%;height:auto;overflow:visible}
figure.chart:not(.bars):not(.heat) svg{aspect-ratio:1000/230}
.grid{stroke:var(--grid);stroke-width:1;vector-effect:non-scaling-stroke}.grid.v{stroke:var(--grid)}.axis{stroke:var(--axis);stroke-width:1;vector-effect:non-scaling-stroke}
.ref{stroke:var(--muted);stroke-width:1;vector-effect:non-scaling-stroke}.qline{stroke:var(--axis);stroke-width:1;vector-effect:non-scaling-stroke;opacity:.6}
.tick,.reflabel,.endlabel{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}.endlabel{fill:var(--ink2)}
.line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}.key{stroke-width:2}
.s1{stroke:var(--s1)}.s2{stroke:var(--s2)}.s3{stroke:var(--s3)}.s4{stroke:var(--s4)}.s5{stroke:var(--s5)}.s6{stroke:var(--s6)}.s7{stroke:var(--s7)}.s8{stroke:var(--s8)}
rect.s1{fill:var(--s1);stroke:none}rect.s2{fill:var(--s2);stroke:none}rect.s3{fill:var(--s3);stroke:none}rect.s4{fill:var(--s4);stroke:none}
rect.s5{fill:var(--s5);stroke:none}rect.s6{fill:var(--s6);stroke:none}rect.s7{fill:var(--s7);stroke:none}rect.s8{fill:var(--s8);stroke:none}
.swatch.fill.s1{background:var(--s1)}.swatch.fill.s2{background:var(--s2)}.swatch.fill.s3{background:var(--s3)}.swatch.fill.s4{background:var(--s4)}
.swatch.fill.s5{background:var(--s5)}.swatch.fill.s6{background:var(--s6)}.swatch.fill.s7{background:var(--s7)}.swatch.fill.s8{background:var(--s8)}
.band{opacity:.14}.band.power_cap{fill:var(--warning)}.band.thermal{fill:var(--serious)}.band.other{fill:var(--muted)}
.q.correct,.bar.correct{fill:var(--good)}.q.incorrect,.bar.incorrect{fill:var(--critical)}.q.error,.bar.error{fill:var(--serious)}
.q{opacity:.55}.phase{opacity:.9}.bar{opacity:.9}.bar:hover,.q:hover,.phase:hover,.cell:hover{opacity:1;stroke:var(--ink);stroke-width:1}
.cell.q1{fill:var(--q1)}.cell.q2{fill:var(--q2)}.cell.q3{fill:var(--q3)}.cell.q4{fill:var(--q4)}.cell.q5{fill:var(--q5)}.cell.q6{fill:var(--q6)}.cell.q7{fill:var(--q7)}
.swatch.fill.q1{background:var(--q1)}.swatch.fill.q2{background:var(--q2)}.swatch.fill.q3{background:var(--q3)}.swatch.fill.q4{background:var(--q4)}.swatch.fill.q5{background:var(--q5)}.swatch.fill.q6{background:var(--q6)}.swatch.fill.q7{background:var(--q7)}
.legend{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--ink2)}
.legend li{display:flex;align-items:center;gap:6px}.swatch{display:inline-block;width:14px;height:10px;border-radius:2px}
.swatch.line{height:2px;width:16px;background:currentColor}.swatch.line.s1{color:var(--s1)}.swatch.line.s2{color:var(--s2)}.swatch.line.s3{color:var(--s3)}.swatch.line.s4{color:var(--s4)}
.swatch.line.s5{color:var(--s5)}.swatch.line.s6{color:var(--s6)}.swatch.line.s7{color:var(--s7)}.swatch.line.s8{color:var(--s8)}
.swatch.band{opacity:.5}.swatch.band.power_cap{background:var(--warning)}.swatch.band.thermal{background:var(--serious)}.swatch.band.other{background:var(--muted)}
.swatch.q.correct{background:var(--good)}.swatch.q.incorrect{background:var(--critical)}.swatch.q.error{background:var(--serious)}
.swatch.fill{opacity:.9}
.tableview{margin-top:6px;font-size:12px}.tableview summary{cursor:pointer;color:var(--muted)}.scroll{max-height:320px;overflow:auto;margin-top:6px}
table{border-collapse:collapse;font-variant-numeric:tabular-nums;white-space:nowrap}th,td{padding:2px 8px;text-align:right;border-bottom:1px solid var(--grid)}
th{color:var(--ink2);font-weight:500;position:sticky;top:0;background:var(--surface)}tbody th{text-align:left}td:first-child{text-align:left}
.tableview.big summary{font-size:14px;color:var(--ink2)}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin-bottom:18px}
.xh line{stroke:var(--ink2);stroke-width:1;vector-effect:non-scaling-stroke}.xh circle{stroke:var(--surface);stroke-width:2}
.tip{position:fixed;z-index:10;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.18);display:none;max-width:320px}
.tip .t{color:var(--muted);margin-bottom:2px}.tip .q{color:var(--ink2);margin-bottom:2px}.tip .r{display:flex;gap:8px;align-items:center}.tip .v{font-weight:600;min-width:52px;text-align:right;font-variant-numeric:tabular-nums}
.tip .k{display:inline-block;width:12px;height:2px}
@media print{.tip{display:none!important}figure{break-inside:avoid}}
</style></head><body>
"""

_SCRIPT = r"""
(function(){
  var el=document.getElementById('telemetry-data'); if(!el) return;
  var D=JSON.parse(el.textContent), L=D.layout, NS='http://www.w3.org/2000/svg';
  var tip=document.createElement('div'); tip.className='tip'; document.body.appendChild(tip);
  function text(tag,cls,s){var n=document.createElement(tag); if(cls) n.className=cls; n.textContent=s; return n;}
  function clock(s){s=Math.round(s); if(D.xmax>=3600){return Math.floor(s/3600)+':'+('0'+Math.floor((s%3600)/60)).slice(-2);} return Math.floor(s/60)+':'+('0'+(s%60)).slice(-2);}
  function wall(i){if(D.t0==null) return ''; var d=new Date((D.t0+D.xs[i])*1000); return d.toISOString().slice(11,19)+' UTC';}
  function num(v,d){if(v==null) return '–'; return v.toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:0});}
  function nearest(x){var lo=0,hi=D.xs.length-1; while(lo<hi){var mid=(lo+hi)>>1; if(D.xs[mid]<x) lo=mid+1; else hi=mid;} if(lo>0&&Math.abs(D.xs[lo-1]-x)<Math.abs(D.xs[lo]-x)) lo--; return lo;}
  function qAt(x){for(var i=0;i<D.questions.length;i++){var q=D.questions[i]; if(x>=q.x0&&x<=q.x1) return q;} return null;}
  function place(ev){var x=ev.clientX+14,y=ev.clientY+14; var r=tip.getBoundingClientRect(); if(x+r.width>window.innerWidth-8) x=ev.clientX-r.width-14; if(y+r.height>window.innerHeight-8) y=ev.clientY-r.height-14; tip.style.left=x+'px'; tip.style.top=y+'px';}
  function simple(ev){tip.replaceChildren(text('div','',ev.target.getAttribute('data-tip'))); tip.style.display='block'; place(ev);}
  document.querySelectorAll('[data-tip]').forEach(function(n){n.addEventListener('pointermove',simple); n.addEventListener('pointerleave',function(){tip.style.display='none';});});
  document.querySelectorAll('figure[data-chart]').forEach(function(fig){
    var cid=fig.getAttribute('data-chart'), c=D.charts[cid], svg=fig.querySelector('svg'); if(!c||!svg) return;
    var g=document.createElementNS(NS,'g'); g.setAttribute('class','xh'); g.style.display='none';
    var line=document.createElementNS(NS,'line'); line.setAttribute('y1',L.mt); line.setAttribute('y2',L.mt+L.ph); g.appendChild(line);
    var dots=c.series.map(function(s){var d=document.createElementNS(NS,'circle'); d.setAttribute('r',4); d.setAttribute('fill','var(--s'+s.slot+')'); g.appendChild(d); return d;});
    svg.appendChild(g); var cur=-1;
    function show(i,ev){cur=i; var x=D.xs[i], px=L.ml+x/D.xmax*L.pw; line.setAttribute('x1',px); line.setAttribute('x2',px); g.style.display='';
      var rows=[text('div','t',clock(x)+' · '+wall(i))]; var q=qAt(x); if(q) rows.push(text('div','q',q.id+' · '+q.outcome));
      c.series.forEach(function(s,k){var v=s.values[i]; if(v==null){dots[k].style.display='none';} else {dots[k].style.display=''; dots[k].setAttribute('cx',px); dots[k].setAttribute('cy',L.mt+L.ph-(Math.min(Math.max(v,c.ymin),c.ymax)-c.ymin)/(c.ymax-c.ymin)*L.ph);}
        var r=document.createElement('div'); r.className='r'; var key=document.createElement('span'); key.className='k'; key.style.background='var(--s'+s.slot+')'; r.appendChild(key);
        r.appendChild(text('span','v',num(v,c.decimals)+' '+c.unit)); r.appendChild(text('span','',s.name)); rows.push(r);});
      tip.replaceChildren.apply(tip,rows); tip.style.display='block'; if(ev) place(ev); else {var b=svg.getBoundingClientRect(); place({clientX:b.left+px/L.w*b.width, clientY:b.top+b.height/2});}}
    svg.addEventListener('pointermove',function(ev){var b=svg.getBoundingClientRect(); var px=(ev.clientX-b.left)*L.w/b.width; var x=(px-L.ml)/L.pw*D.xmax; if(x<0||x>D.xmax){return;} show(nearest(x),ev);});
    svg.addEventListener('pointerleave',function(){g.style.display='none'; tip.style.display='none';});
    fig.addEventListener('keydown',function(ev){if(ev.key==='ArrowRight'||ev.key==='ArrowLeft'){ev.preventDefault(); var i=cur<0?0:cur+(ev.key==='ArrowRight'?1:-1); i=Math.max(0,Math.min(D.xs.length-1,i)); show(i,null);} else if(ev.key==='Escape'){g.style.display='none'; tip.style.display='none';}});
    fig.addEventListener('blur',function(){g.style.display='none'; tip.style.display='none';});
  });
})();
"""
