"""Correlates host samples with activity events: history, job reports and anomaly detection.

Inputs are the ``host_samples`` written by the Windows host agent and the ``activity_events``
written by the API and the ingestion worker (see :mod:`backend.services.telemetry_store`).
Aggregations run in MongoDB (``$percentile``, ``$setWindowFields``) so a report over a long job
never loads every event into Python; only flagged samples and top-N rows come back.

Anomaly rules (thresholds in :data:`THRESHOLDS`):

* host: ``memory_pressure``, ``cpu_saturation``, ``gpu_throttling`` (thermal vs power cap),
  ``hot_gpu``, ``hot_cpu``, ``backend_unhealthy`` (down or slow /health = blocked event loop),
  ``container_memory`` (a container near its memory limit), ``container_restart``,
  ``sampling_gap`` (no agent samples: agent stopped, host asleep or rebooted);
* files: ``failed_files`` (grouped by error type), ``cancelled_files`` (timeouts/stops),
  ``unfinished_files`` (a ``file.start`` without ``file.end``: crashed or hung),
  ``slow_files`` (≥ 3× the median of the same file type and ≥ 30 s), ``empty_files``
  (no chunks: image-only or failed OCR).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

from backend.models.telemetry_history import (
    ActivityPoint,
    Anomaly,
    AnomalyReport,
    ContainerPoint,
    Episode,
    ExtStat,
    FileRow,
    HistoryResponse,
    HostPoint,
    JobReport,
    JobSummary,
    PhaseInterval,
    PhaseResources,
    StepStat,
)
from backend.services.activity_events import TERMINAL_JOB_STATUSES
from backend.services.telemetry_store import TelemetryStore, choose_bucket_seconds

logger = logging.getLogger(__name__)

__all__ = [
    "THRESHOLDS",
    "build_phase_intervals",
    "find_episodes",
    "history",
    "job_report",
    "range_anomalies",
]

#: Rule thresholds (percent, °C, ms, seconds).
THRESHOLDS: dict[str, float] = {
    "mem_pct": 90.0,
    "mem_pct_critical": 97.0,
    "cpu_pct": 95.0,
    "cpu_sustained_s": 30.0,
    "gpu_temp_c": 85.0,
    "cpu_temp_c": 95.0,
    "health_slow_ms": 2000.0,
    "container_mem_share": 0.9,
    "slow_file_ratio": 3.0,
    "slow_file_min_ms": 30_000.0,
    "unfinished_after_s": 1800.0,
    "stale_job_s": 600.0,
}

_THERMAL_REASONS = frozenset({"sw_thermal", "hw_thermal", "hw_slowdown"})
_DEFAULT_INTERVAL_S = 5.0


def _aware(value: Any) -> Any:
    """Treat naive datetimes from MongoDB as UTC."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# ── pure helpers (unit-tested) ───────────────────────────────────────────────


def build_phase_intervals(
    events: Sequence[dict[str, Any]],
    *,
    range_end: datetime,
    last_seen: Optional[dict[str, datetime]] = None,
    stale_after_s: float = THRESHOLDS["stale_job_s"],
) -> list[PhaseInterval]:
    """Turn ``job.phase`` / ``job.status`` events into phase intervals per job.

    A phase lasts until the next phase or a terminal status. A pause becomes a ``paused``
    interval and the interrupted phase resumes afterwards. An interval with no end is closed
    at ``range_end`` (``open=True``) unless the job has been silent for ``stale_after_s``
    (process killed without a final status): then it closes at the job's last event.

    Args:
        events: Events of one or more jobs (any order).
        range_end: End of the requested range (or now).
        last_seen: Latest event time per job (any kind), to detect silent jobs.
        stale_after_s: Silence after which an unfinished job counts as dead.
    """
    by_job: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        job_id = (event.get("meta") or {}).get("job_id")
        if job_id:
            by_job.setdefault(job_id, []).append(event)

    intervals: list[PhaseInterval] = []
    for job_id, job_events in by_job.items():
        job_events.sort(key=lambda e: _aware(e["ts"]))
        current: Optional[tuple[str, datetime]] = None
        resume_phase: Optional[str] = None
        for event in job_events:
            ts = _aware(event["ts"])
            kind = (event.get("meta") or {}).get("kind")
            if kind == "job.phase":
                phase = str(event.get("phase") or "unknown")
                if phase in ("completed", "stopped", "failed"):
                    continue  # terminal phase names close intervals via job.status
                if current is not None:
                    intervals.append(PhaseInterval(job_id=job_id, phase=current[0], start=current[1], end=ts))
                current = (phase, ts)
            elif kind == "job.status":
                status = str(event.get("status") or "").lower()
                if status == "paused" and current is not None and current[0] != "paused":
                    intervals.append(PhaseInterval(job_id=job_id, phase=current[0], start=current[1], end=ts))
                    resume_phase = current[0]
                    current = ("paused", ts)
                elif status == "running" and current is not None and current[0] == "paused":
                    intervals.append(PhaseInterval(job_id=job_id, phase="paused", start=current[1], end=ts))
                    current = (resume_phase or "processing", ts)
                    resume_phase = None
                elif status in TERMINAL_JOB_STATUSES and current is not None:
                    intervals.append(PhaseInterval(job_id=job_id, phase=current[0], start=current[1], end=ts))
                    current = None
        if current is not None:
            seen = _aware((last_seen or {}).get(job_id)) or _aware(job_events[-1]["ts"])
            if (range_end - seen).total_seconds() > stale_after_s:
                intervals.append(PhaseInterval(job_id=job_id, phase=current[0], start=current[1], end=max(seen, current[1])))
            else:
                intervals.append(PhaseInterval(job_id=job_id, phase=current[0], start=current[1], end=range_end, open=True))
    intervals.sort(key=lambda i: i.start)
    return intervals


def clip_intervals(intervals: Iterable[PhaseInterval], start: datetime, end: datetime) -> list[PhaseInterval]:
    """Intervals overlapping ``[start, end)``, clipped to it."""
    out: list[PhaseInterval] = []
    for interval in intervals:
        if interval.end <= start or interval.start >= end:
            continue
        out.append(interval.model_copy(update={"start": max(interval.start, start), "end": min(interval.end, end)}))
    return out


@dataclass(frozen=True)
class FlaggedSample:
    """A sample time, an optional value for the episode peak, and whether the rule fired."""

    ts: datetime
    value: Optional[float]
    detail: Optional[str] = None


def find_episodes(samples: Sequence[FlaggedSample], *, max_gap_s: float) -> list[Episode]:
    """Group flagged samples into episodes; a gap longer than ``max_gap_s`` starts a new one."""
    episodes: list[Episode] = []
    current: list[FlaggedSample] = []

    def close() -> None:
        if not current:
            return
        values = [s.value for s in current if s.value is not None]
        details = sorted({s.detail for s in current if s.detail})
        episodes.append(Episode(
            start=current[0].ts, end=current[-1].ts, samples=len(current),
            peak=round(max(values), 1) if values else None, detail=", ".join(details) or None,
        ))

    for sample in sorted(samples, key=lambda s: s.ts):
        if current and (sample.ts - current[-1].ts).total_seconds() > max_gap_s:
            close()
            current = []
        current.append(sample)
    close()
    return episodes


def _episode_seconds(episode: Episode, interval_s: float) -> float:
    return (episode.end - episode.start).total_seconds() + interval_s


def activity_points(rows: Sequence[dict[str, Any]]) -> list[ActivityPoint]:
    """Reshape ``{_id: {t, kind, outcome}, n, ms}`` aggregation rows into one point per bucket."""
    points: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        key = row["_id"]
        t = _aware(key["t"])
        point = points.setdefault(t, {"t": t, "files_ok": 0, "files_failed": 0, "files_other": 0, "chats": 0,
                                      "chat_errors": 0, "_fms": 0.0, "_fn": 0, "_cms": 0.0, "_cn": 0})
        n = int(row.get("n") or 0)
        ms = float(row.get("ms") or 0.0)
        if key.get("kind") == "file.end":
            outcome = key.get("outcome")
            if outcome == "ok":
                point["files_ok"] += n
            elif outcome == "error":
                point["files_failed"] += n
            else:
                point["files_other"] += n
            point["_fms"] += ms
            point["_fn"] += n
        elif key.get("kind") == "chat.request":
            point["chats"] += n
            if key.get("outcome") in ("error", "cancelled"):
                point["chat_errors"] += n
            point["_cms"] += ms
            point["_cn"] += n
    out = []
    for t in sorted(points):
        p = points[t]
        out.append(ActivityPoint(
            t=t, files_ok=p["files_ok"], files_failed=p["files_failed"], files_other=p["files_other"],
            file_ms_avg=round(p["_fms"] / p["_fn"], 1) if p["_fn"] else None,
            chats=p["chats"], chat_errors=p["chat_errors"],
            chat_ms_avg=round(p["_cms"] / p["_cn"], 1) if p["_cn"] else None,
        ))
    return out


# ── queries ──────────────────────────────────────────────────────────────────


async def _activity_series(store: TelemetryStore, start: datetime, end: datetime, bucket_s: int,
                           job_id: Optional[str] = None) -> list[ActivityPoint]:
    match: dict[str, Any] = {"ts": {"$gte": start, "$lt": end}, "meta.kind": {"$in": ["file.end", "chat.request"]}}
    if job_id is not None:
        match["meta.job_id"] = job_id
    rows = await store.aggregate_events([
        {"$match": match},
        {"$group": {
            "_id": {"t": {"$dateTrunc": {"date": "$ts", "unit": "second", "binSize": bucket_s}},
                    "kind": "$meta.kind", "outcome": "$outcome"},
            "n": {"$sum": 1},
            "ms": {"$sum": {"$ifNull": ["$duration_ms", 0]}},
        }},
    ])
    return activity_points(rows)


async def _job_events(store: TelemetryStore, job_ids: Sequence[str]) -> tuple[list[dict[str, Any]], dict[str, datetime]]:
    """All phase/status events of the given jobs plus each job's last event time."""
    if not job_ids:
        return [], {}
    events = await store.events(kinds=["job.phase", "job.status"], extra_filter={"meta.job_id": {"$in": list(job_ids)}},
                                limit=20000)
    rows = await store.aggregate_events([
        {"$match": {"meta.job_id": {"$in": list(job_ids)}}},
        {"$group": {"_id": "$meta.job_id", "last": {"$max": "$ts"}}},
    ])
    return events, {row["_id"]: _aware(row["last"]) for row in rows}


async def history(store: TelemetryStore, start: datetime, end: datetime,
                  bucket_seconds: Optional[int] = None) -> HistoryResponse:
    """Host metrics, per-container use, activity and job phases over ``[start, end)``."""
    bucket = bucket_seconds or choose_bucket_seconds(start, end)
    host_rows = await store.host_series(start, end, bucket)
    containers = await store.container_series(start, end, bucket)
    activity = await _activity_series(store, start, end, bucket)
    jobs = await store.recent_jobs(start, end, limit=100)
    events, last_seen = await _job_events(store, [j["job_id"] for j in jobs])
    phases = clip_intervals(build_phase_intervals(events, range_end=min(end, datetime.now(timezone.utc)),
                                                  last_seen=last_seen), start, end)
    return HistoryResponse(
        start=start,
        end=end,
        bucket_seconds=bucket,
        agent_samples=sum(int(r.get("samples") or 0) for r in host_rows),
        host=[HostPoint(**{**r, "t": _aware(r["t"])}) for r in host_rows],
        containers={name: [ContainerPoint(**{**p, "t": _aware(p["t"])}) for p in points] for name, points in containers.items()},
        activity=activity,
        phases=phases,
        jobs=[JobSummary(**{**j, "first_ts": _aware(j["first_ts"]), "last_ts": _aware(j["last_ts"])}) for j in jobs],
    )


# ── anomalies ────────────────────────────────────────────────────────────────


async def host_anomalies(store: TelemetryStore, start: datetime, end: datetime,
                         interval_s: float = _DEFAULT_INTERVAL_S) -> list[Anomaly]:
    """Host, GPU, backend and container problems in ``[start, end)`` from the agent samples."""
    t = THRESHOLDS
    flagged = await store.aggregate_samples([
        {"$match": {"ts": {"$gte": start, "$lt": end}, "$or": [
            {"mem.pct": {"$gte": t["mem_pct"]}},
            {"cpu.pct": {"$gte": t["cpu_pct"]}},
            {"gpu.reasons.0": {"$exists": True}},
            {"temp.gpu_c": {"$gte": t["gpu_temp_c"]}},
            {"temp.cpu_pkg_c": {"$gte": t["cpu_temp_c"]}},
            {"health.ok": False},
            {"health.ms": {"$gte": t["health_slow_ms"]}},
        ]}},
        {"$project": {"_id": 0, "ts": 1, "mem.pct": 1, "cpu.pct": 1, "gpu.reasons": 1, "temp.gpu_c": 1,
                      "temp.cpu_pkg_c": 1, "health": 1}},
        {"$sort": {"ts": 1}},
        {"$limit": 50000},
    ])
    gap = max(2.5 * interval_s, 15.0)
    buckets: dict[str, list[FlaggedSample]] = {k: [] for k in
                                               ("mem", "cpu", "thermal", "power", "gpu_hot", "cpu_hot", "down", "slow")}
    for s in flagged:
        ts = _aware(s["ts"])
        mem = (s.get("mem") or {}).get("pct")
        cpu = (s.get("cpu") or {}).get("pct")
        reasons = set((s.get("gpu") or {}).get("reasons") or [])
        temp = s.get("temp") or {}
        health = [h for h in (s.get("health") or []) if isinstance(h, dict)]
        if mem is not None and mem >= t["mem_pct"]:
            buckets["mem"].append(FlaggedSample(ts, mem))
        if cpu is not None and cpu >= t["cpu_pct"]:
            buckets["cpu"].append(FlaggedSample(ts, cpu))
        if reasons & _THERMAL_REASONS:
            buckets["thermal"].append(FlaggedSample(ts, temp.get("gpu_c"), ", ".join(sorted(reasons & _THERMAL_REASONS))))
        elif "power_cap" in reasons:
            buckets["power"].append(FlaggedSample(ts, None, "power_cap"))
        if (temp.get("gpu_c") or 0) >= t["gpu_temp_c"]:
            buckets["gpu_hot"].append(FlaggedSample(ts, temp.get("gpu_c")))
        if (temp.get("cpu_pkg_c") or 0) >= t["cpu_temp_c"]:
            buckets["cpu_hot"].append(FlaggedSample(ts, temp.get("cpu_pkg_c")))
        for h in health:
            if h.get("ok") is False:
                buckets["down"].append(FlaggedSample(ts, None, h.get("name")))
            elif (h.get("ms") or 0) >= t["health_slow_ms"]:
                buckets["slow"].append(FlaggedSample(ts, h.get("ms"), h.get("name")))

    anomalies: list[Anomaly] = []

    def add(kind: str, key: str, severity: str, title: str, detail: str, *, min_seconds: float = 0.0,
            critical_peak: Optional[float] = None) -> None:
        episodes = [e for e in find_episodes(buckets[key], max_gap_s=gap) if _episode_seconds(e, interval_s) >= min_seconds]
        if not episodes:
            return
        sev = severity
        if critical_peak is not None and any((e.peak or 0) >= critical_peak for e in episodes):
            sev = "critical"
        anomalies.append(Anomaly(kind=kind, severity=sev, title=title, detail=detail,
                                 count=sum(e.samples for e in episodes), episodes=episodes[:50]))

    add("memory_pressure", "mem", "warning", "Host memory pressure",
        f"Host RAM at or above {t['mem_pct']:.0f} % (critical at {t['mem_pct_critical']:.0f} %).",
        critical_peak=t["mem_pct_critical"])
    add("cpu_saturation", "cpu", "warning", "Host CPU saturated",
        f"All cores at or above {t['cpu_pct']:.0f} % for {t['cpu_sustained_s']:.0f} s or longer.",
        min_seconds=t["cpu_sustained_s"])
    add("gpu_throttling", "thermal", "warning", "GPU thermal throttling",
        "The GPU driver held clocks down for temperature; embedding and OCR on the GPU slow down.")
    add("gpu_power_cap", "power", "info", "GPU at its power cap",
        "The GPU ran into its power limit (normal under full load; clocks are capped, not overheating).")
    add("hot_gpu", "gpu_hot", "warning", "GPU running hot", f"GPU temperature at or above {t['gpu_temp_c']:.0f} °C.")
    add("hot_cpu", "cpu_hot", "warning", "CPU running hot", f"CPU package at or above {t['cpu_temp_c']:.0f} °C.")
    add("backend_down", "down", "critical", "Backend unreachable",
        "The backend /health check failed: the API was down, restarting or not answering.")
    add("backend_slow", "slow", "warning", "Backend event loop blocked",
        f"The backend /health answer took {t['health_slow_ms']:.0f} ms or more: synchronous work blocked the API.")

    anomalies.extend(await _container_anomalies(store, start, end))
    gaps = await _sampling_gaps(store, start, end, interval_s)
    if gaps:
        anomalies.append(Anomaly(kind="sampling_gap", severity="info", title="Host agent gaps",
                                 detail="No host samples for a while (agent stopped, host asleep or rebooted); "
                                        "figures for these stretches are missing.",
                                 count=len(gaps), episodes=gaps[:50]))
    return anomalies


async def _container_anomalies(store: TelemetryStore, start: datetime, end: datetime) -> list[Anomaly]:
    rows = await store.aggregate_samples([
        {"$match": {"ts": {"$gte": start, "$lt": end}}},
        {"$project": {"ts": 1, "containers": 1}},
        {"$unwind": "$containers"},
        {"$group": {
            "_id": "$containers.name",
            "starts": {"$addToSet": "$containers.started_at"},
            "restart_min": {"$min": "$containers.restart_count"},
            "restart_max": {"$max": "$containers.restart_count"},
            "mem_share_max": {"$max": {"$cond": [
                {"$gt": [{"$ifNull": ["$containers.mem_limit_bytes", 0]}, 0]},
                {"$divide": ["$containers.mem_bytes", "$containers.mem_limit_bytes"]}, None]}},
            "first_seen": {"$min": "$ts"},
        }},
    ])
    anomalies: list[Anomaly] = []
    restarts: list[dict[str, Any]] = []
    memory: list[dict[str, Any]] = []
    for row in rows:
        name = row["_id"]
        starts = sorted(_aware(s) for s in (row.get("starts") or []) if isinstance(s, datetime))
        first_seen = _aware(row.get("first_seen"))
        for started in starts[1:]:
            if first_seen is None or started > first_seen:
                restarts.append({"container": name, "at": started.isoformat()})
        if (row.get("restart_max") or 0) > (row.get("restart_min") or 0) and not any(r["container"] == name for r in restarts):
            restarts.append({"container": name, "restarts": int(row["restart_max"] - row["restart_min"])})
        share = row.get("mem_share_max")
        if share is not None and share >= THRESHOLDS["container_mem_share"]:
            memory.append({"container": name, "peak_share_pct": round(share * 100, 1)})
    if restarts:
        anomalies.append(Anomaly(kind="container_restart", severity="warning", title="Container restarted",
                                 detail="A container restarted (crash, out-of-memory kill or a deploy) in this range.",
                                 count=len(restarts), examples=restarts[:20]))
    if memory:
        anomalies.append(Anomaly(kind="container_memory", severity="warning", title="Container near its memory limit",
                                 detail=f"A container used {THRESHOLDS['container_mem_share'] * 100:.0f} % or more of "
                                        "the memory it can see (the Docker VM size): risk of an out-of-memory kill.",
                                 count=len(memory), examples=memory))
    return anomalies


async def _sampling_gaps(store: TelemetryStore, start: datetime, end: datetime, interval_s: float) -> list[Episode]:
    threshold = max(3 * interval_s, 30.0)
    rows = await store.aggregate_samples([
        {"$match": {"ts": {"$gte": start, "$lt": end}}},
        {"$project": {"_id": 0, "ts": 1}},
        {"$setWindowFields": {"sortBy": {"ts": 1}, "output": {"prev": {"$shift": {"output": "$ts", "by": -1}}}}},
        {"$match": {"prev": {"$ne": None}}},
        {"$project": {"ts": 1, "prev": 1, "gap": {"$dateDiff": {"startDate": "$prev", "endDate": "$ts", "unit": "second"}}}},
        {"$match": {"gap": {"$gt": threshold}}},
        {"$sort": {"gap": -1}},
        {"$limit": 200},
    ])
    gaps = [Episode(start=_aware(r["prev"]), end=_aware(r["ts"]), samples=0, peak=float(r["gap"]),
                    detail=f"{int(r['gap'])} s without samples") for r in rows]
    return sorted(gaps, key=lambda e: e.start)


async def file_anomalies(store: TelemetryStore, start: Optional[datetime], end: Optional[datetime],
                         job_id: Optional[str] = None, *, job_running: bool = False) -> list[Anomaly]:
    """Failed, cancelled, unfinished, slow and empty files (for one job, or all jobs in a range)."""
    t = THRESHOLDS
    match: dict[str, Any] = {"meta.kind": "file.end"}
    if job_id is not None:
        match["meta.job_id"] = job_id
    if start is not None and end is not None:
        match["ts"] = {"$gte": start, "$lt": end}

    outcome_rows = await store.aggregate_events([
        {"$match": {**match, "outcome": {"$in": ["error", "cancelled", "empty"]}}},
        {"$group": {
            "_id": {"outcome": "$outcome", "error_type": "$error_type", "ext": "$file.ext"},
            "n": {"$sum": 1},
            "examples": {"$push": {"path": "$file.path", "error": "$error", "ts": "$ts"}},
        }},
        {"$project": {"n": 1, "examples": {"$slice": ["$examples", 3]}}},
        {"$sort": {"n": -1}},
    ])
    anomalies: list[Anomaly] = []
    failed = [r for r in outcome_rows if r["_id"].get("outcome") == "error"]
    if failed:
        examples = [{"error_type": r["_id"].get("error_type"), "ext": r["_id"].get("ext"), "files": r["n"],
                     "examples": [e.get("path") for e in r["examples"]], "error": (r["examples"][0] or {}).get("error")}
                    for r in failed[:20]]
        anomalies.append(Anomaly(kind="failed_files", severity="warning", title="Files failed",
                                 detail="Files that raised an error during conversion, chunking, embedding or saving.",
                                 count=sum(r["n"] for r in failed), examples=examples))
    cancelled = [r for r in outcome_rows if r["_id"].get("outcome") == "cancelled"]
    if cancelled:
        anomalies.append(Anomaly(kind="cancelled_files", severity="info", title="Files cancelled",
                                 detail="Files cut off by the per-file timeout, a stop or a shutdown.",
                                 count=sum(r["n"] for r in cancelled),
                                 examples=[{"ext": r["_id"].get("ext"), "files": r["n"],
                                            "examples": [e.get("path") for e in r["examples"]]} for r in cancelled[:20]]))
    empty = [r for r in outcome_rows if r["_id"].get("outcome") == "empty"]
    if empty:
        anomalies.append(Anomaly(kind="empty_files", severity="info", title="Files without text",
                                 detail="Files that produced no chunks (image-only pages without usable OCR, empty files).",
                                 count=sum(r["n"] for r in empty),
                                 examples=[{"ext": r["_id"].get("ext"), "files": r["n"],
                                            "examples": [e.get("path") for e in r["examples"]]} for r in empty[:20]]))

    medians = {
        r["_id"]: (r.get("p") or [None])[0]
        for r in await store.aggregate_events([
            {"$match": {**match, "outcome": "ok", "duration_ms": {"$type": "number"}}},
            {"$group": {"_id": "$file.ext", "p": {"$percentile": {"input": "$duration_ms", "p": [0.5], "method": "approximate"}}}},
        ])
    }
    # Only files that finished: cancelled ones (timeouts) are already reported as cancelled_files.
    slow_candidates = await store.events(
        kinds=["file.end"], start=match.get("ts", {}).get("$gte"), end=match.get("ts", {}).get("$lt"), job_id=job_id,
        extra_filter={"duration_ms": {"$gte": t["slow_file_min_ms"]}, "outcome": {"$in": ["ok", "empty"]}}, limit=2000,
    )
    slow: list[dict[str, Any]] = []
    for event in slow_candidates:
        ext = (event.get("file") or {}).get("ext")
        median = medians.get(ext)
        duration = event.get("duration_ms") or 0
        if median and duration >= t["slow_file_ratio"] * median:
            slow.append({"path": (event.get("file") or {}).get("path"), "ext": ext, "duration_ms": duration,
                         "median_ms": round(median, 1), "ratio": round(duration / median, 1),
                         "pages": event.get("pages"), "steps": event.get("steps"), "ts": _aware(event["ts"]).isoformat()})
    if slow:
        slow.sort(key=lambda r: r["ratio"], reverse=True)
        anomalies.append(Anomaly(kind="slow_files", severity="warning", title="Unusually slow files",
                                 detail=f"Files that took at least {t['slow_file_ratio']:.0f}× the median of their file "
                                        f"type and {t['slow_file_min_ms'] / 1000:.0f} s or more; the step timings show where.",
                                 count=len(slow), examples=slow[:25]))

    unfinished = await _unfinished_files(store, start, end, job_id, job_running=job_running)
    if unfinished:
        anomalies.append(Anomaly(kind="unfinished_files", severity="critical", title="Files that never finished",
                                 detail="A file started but no end was recorded: the process crashed, was killed or "
                                        "the file is hanging.",
                                 count=len(unfinished), examples=[u.model_dump(mode="json") for u in unfinished[:25]]))
    return anomalies


async def _unfinished_files(store: TelemetryStore, start: Optional[datetime], end: Optional[datetime],
                            job_id: Optional[str], *, job_running: bool) -> list[FileRow]:
    match: dict[str, Any] = {"meta.kind": {"$in": ["file.start", "file.end"]}}
    if job_id is not None:
        match["meta.job_id"] = job_id
    if start is not None and end is not None:
        match["ts"] = {"$gte": start, "$lt": end}
    rows = await store.aggregate_events([
        {"$match": match},
        {"$group": {
            "_id": {"job": "$meta.job_id", "path": "$file.path"},
            "starts": {"$sum": {"$cond": [{"$eq": ["$meta.kind", "file.start"]}, 1, 0]}},
            "ends": {"$sum": {"$cond": [{"$eq": ["$meta.kind", "file.end"]}, 1, 0]}},
            "last_start": {"$max": {"$cond": [{"$eq": ["$meta.kind", "file.start"]}, "$ts", None]}},
            "file": {"$first": "$file"},
        }},
        {"$match": {"$expr": {"$gt": ["$starts", "$ends"]}}},
        {"$sort": {"last_start": 1}},
        {"$limit": 500},
    ])
    now = datetime.now(timezone.utc)
    out: list[FileRow] = []
    for row in rows:
        last_start = _aware(row.get("last_start"))
        # A file of a running job that started recently is simply in progress.
        if job_running and last_start is not None and (now - last_start).total_seconds() < THRESHOLDS["unfinished_after_s"]:
            continue
        file_info = row.get("file") or {}
        out.append(FileRow(path=file_info.get("path") or row["_id"].get("path") or "?", ext=file_info.get("ext"),
                           size_bytes=file_info.get("size_bytes"), outcome="unfinished", ts=last_start))
    return out


async def range_anomalies(store: TelemetryStore, start: datetime, end: datetime) -> AnomalyReport:
    """Host and file anomalies of every job in ``[start, end)``, most severe first."""
    anomalies = await host_anomalies(store, start, end) + await file_anomalies(store, start, end)
    return AnomalyReport(start=start, end=end, anomalies=_sorted_anomalies(anomalies))


def _sorted_anomalies(anomalies: list[Anomaly]) -> list[Anomaly]:
    rank = {"critical": 0, "warning": 1, "info": 2}
    return sorted(anomalies, key=lambda a: (rank.get(a.severity, 3), -a.count))


# ── job report ───────────────────────────────────────────────────────────────


async def job_report(store: TelemetryStore, job_id: str, *, now: Optional[datetime] = None) -> Optional[JobReport]:
    """Correlated report of one ingestion job, or None when no events exist for it."""
    now = now or datetime.now(timezone.utc)
    job_events = await store.events(job_id=job_id, kinds=["job.phase", "job.status"], limit=20000)
    bounds = await store.aggregate_events([
        {"$match": {"meta.job_id": job_id}},
        {"$group": {"_id": None, "first": {"$min": "$ts"}, "last": {"$max": "$ts"},
                    "tenant": {"$last": "$meta.tenant"}, "source": {"$last": "$meta.source"}}},
    ])
    if not bounds:
        return None
    first, last = _aware(bounds[0]["first"]), _aware(bounds[0]["last"])
    statuses = [e for e in job_events if (e.get("meta") or {}).get("kind") == "job.status"]
    status = str(statuses[-1].get("status")) if statuses else None
    finished = status in TERMINAL_JOB_STATUSES
    running = not finished and (now - last).total_seconds() < THRESHOLDS["stale_job_s"]
    end = last if not running else now
    window_end = end + timedelta(seconds=1)

    intervals = build_phase_intervals(job_events, range_end=end, last_seen={job_id: last})
    phases: list[PhaseResources] = []
    for interval in intervals:
        stop = max(interval.end, interval.start + timedelta(seconds=1))
        phases.append(PhaseResources(
            phase=interval.phase, start=interval.start, end=interval.end,
            duration_s=round((interval.end - interval.start).total_seconds(), 1),
            host=await store.host_summary(interval.start, stop),
            containers=await store.container_summary(interval.start, stop),
        ))

    match = {"meta.job_id": job_id, "meta.kind": "file.end"}
    totals_rows = await store.aggregate_events([
        {"$match": match},
        {"$group": {"_id": "$outcome", "n": {"$sum": 1}, "bytes": {"$sum": {"$ifNull": ["$file.size_bytes", 0]}},
                    "pages": {"$sum": {"$ifNull": ["$pages", 0]}}, "chunks": {"$sum": {"$ifNull": ["$chunks", 0]}}}},
    ])
    outcomes = {str(r["_id"]): int(r["n"]) for r in totals_rows}
    files = sum(outcomes.values())

    step_rows = await store.aggregate_events([
        {"$match": match},
        {"$project": {"steps": {"$objectToArray": {"$ifNull": ["$steps", {}]}}}},
        {"$unwind": "$steps"},
        {"$group": {"_id": "$steps.k", "files": {"$sum": 1},
                    "p": {"$percentile": {"input": "$steps.v", "p": [0.5, 0.95], "method": "approximate"}},
                    "max": {"$max": "$steps.v"}, "total": {"$sum": "$steps.v"}}},
        {"$sort": {"total": -1}},
    ])
    all_steps_ms = sum(float(r.get("total") or 0) for r in step_rows) or None
    steps = [StepStat(step=str(r["_id"]).removesuffix("_ms"), files=int(r["files"]),
                      p50_ms=_r1((r.get("p") or [None, None])[0]), p95_ms=_r1((r.get("p") or [None, None])[1]),
                      max_ms=_r1(r.get("max")), total_ms=_r1(r.get("total")),
                      share_pct=_r1(100 * float(r.get("total") or 0) / all_steps_ms) if all_steps_ms else None)
             for r in step_rows]

    ext_rows = await store.aggregate_events([
        {"$match": match},
        {"$group": {"_id": "$file.ext", "files": {"$sum": 1},
                    "ok": {"$sum": {"$cond": [{"$eq": ["$outcome", "ok"]}, 1, 0]}},
                    "failed": {"$sum": {"$cond": [{"$eq": ["$outcome", "error"]}, 1, 0]}},
                    "empty": {"$sum": {"$cond": [{"$eq": ["$outcome", "empty"]}, 1, 0]}},
                    "p": {"$percentile": {"input": "$duration_ms", "p": [0.5, 0.95], "method": "approximate"}},
                    "max": {"$max": "$duration_ms"}, "total_ms": {"$sum": {"$ifNull": ["$duration_ms", 0]}},
                    "bytes": {"$sum": {"$ifNull": ["$file.size_bytes", 0]}},
                    "pages": {"$sum": {"$ifNull": ["$pages", 0]}},
                    "paged_ms": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$pages", 0]}, 0]}, "$duration_ms", 0]}}}},
        {"$sort": {"files": -1}},
    ])
    by_ext = [ExtStat(ext=str(r["_id"] or "?"), files=int(r["files"]), ok=int(r["ok"]), failed=int(r["failed"]),
                      empty=int(r["empty"]), p50_ms=_r1((r.get("p") or [None, None])[0]),
                      p95_ms=_r1((r.get("p") or [None, None])[1]), max_ms=_r1(r.get("max")),
                      bytes=int(r.get("bytes") or 0) or None, pages=int(r.get("pages") or 0) or None,
                      ms_per_page=_r1(float(r["paged_ms"]) / float(r["pages"])) if r.get("pages") else None)
              for r in ext_rows]

    slowest = [_file_row(e) for e in await store.aggregate_events([
        {"$match": {**match, "duration_ms": {"$type": "number"}}},
        {"$sort": {"duration_ms": -1}},
        {"$limit": 25},
        {"$project": {"_id": 0}},
    ])]

    unfinished = await _unfinished_files(store, None, None, job_id, job_running=running)
    host = await host_anomalies(store, first, window_end)
    files_anoms = await file_anomalies(store, None, None, job_id, job_running=running)
    failures = next((a.examples for a in files_anoms if a.kind == "failed_files"), [])
    span = (end - first).total_seconds()
    bucket = choose_bucket_seconds(first, window_end, target_points=120)
    agent_samples = await store.count_host_samples(first, window_end)

    return JobReport(
        job_id=job_id,
        status=status,
        tenant=bounds[0].get("tenant"),
        source=bounds[0].get("source"),
        start=first,
        end=end,
        running=running,
        files=files,
        outcomes=outcomes,
        total_bytes=sum(int(r.get("bytes") or 0) for r in totals_rows) or None,
        total_pages=sum(int(r.get("pages") or 0) for r in totals_rows) or None,
        total_chunks=sum(int(r.get("chunks") or 0) for r in totals_rows) or None,
        files_per_hour=round(files / span * 3600, 1) if span > 0 and files else None,
        phases=phases,
        steps=steps,
        by_ext=by_ext,
        slowest=slowest,
        failures=failures,
        unfinished=unfinished[:50],
        throughput=await _activity_series(store, first, window_end, bucket, job_id=job_id),
        throughput_bucket_seconds=bucket,
        anomalies=_sorted_anomalies(host + files_anoms),
        agent_samples=agent_samples,
    )


def _r1(value: Any) -> Optional[float]:
    return round(float(value), 1) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _file_row(event: dict[str, Any]) -> FileRow:
    file_info = event.get("file") or {}
    return FileRow(
        path=file_info.get("path") or "?", ext=file_info.get("ext"), outcome=event.get("outcome"),
        duration_ms=_r1(event.get("duration_ms")), size_bytes=file_info.get("size_bytes"), pages=event.get("pages"),
        chunks=event.get("chunks"), steps={str(k).removesuffix("_ms"): float(v) for k, v in (event.get("steps") or {}).items()
                                           if isinstance(v, (int, float))},
        error_type=event.get("error_type"), error=event.get("error"), ts=_aware(event.get("ts")),
    )
