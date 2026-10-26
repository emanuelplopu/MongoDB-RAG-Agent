"""Per-extension ETA of a running ingestion job.

Until 2026-10-26 the status endpoint extrapolated ``elapsed / progress`` (2.5 weeks for the
diejuristin job) while the machine-stats widget divided the remaining files by the files
finished in the last hour (58-72 days). Both were wrong in their own way: the queue is sorted
by file type, so the pace of the small text files at its front says nothing about the scanned
PDFs behind them, and the last hour says nothing about extensions that are not being processed
right now.

This module is the one estimator both use (item 8 of the prompt-test fixes):

* the runners (``run_ingestion`` in the API, ``_process_job`` in the ingestion worker) record
  on the job how many files of each extension the run queued (``queue_by_extension``) and how
  many it has finished (``processed_by_extension``);
* :func:`measure_pace` reads the per-extension throughput from ``ingestion_stats`` (files per
  minute over the last hour, falling back to the extension's pace over the whole job);
* :func:`estimate` sums ``remaining / pace`` over the extensions. Extensions without any pace
  yet (not started) use the job's overall pace; when there is none at all they are reported
  as ``unknown_remaining``.

``job_eta`` caches the measured pace per job for :data:`PACE_TTL_SECONDS`, so the status
endpoint, the event stream and the widget can call it as often as they poll.
"""

from __future__ import annotations

import inspect
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

#: Throughput window ("files per minute right now").
PACE_WINDOW = timedelta(hours=1)
#: Measured paces are reused for this long per job.
PACE_TTL_SECONDS = 60.0

__all__ = [
    "EtaEstimate", "EtaExtension", "JobPace", "PACE_WINDOW", "PACE_TTL_SECONDS",
    "count_by_extension", "estimate", "extension_of", "job_eta", "measure_pace",
    "remaining_by_extension", "reset_cache",
]


def extension_of(name: str) -> str:
    """Lower-case extension of a path or file name, with its dot; ``""`` when there is none."""
    ext = os.path.splitext(os.path.basename(name or ""))[1]
    return ext.lower() if ext else ""


def count_by_extension(paths: Iterable[str]) -> Dict[str, int]:
    """How many of ``paths`` carry each extension."""
    counts: Dict[str, int] = {}
    for path in paths:
        ext = extension_of(path)
        counts[ext] = counts.get(ext, 0) + 1
    return counts


def remaining_by_extension(queued: Mapping[str, Any], processed: Optional[Mapping[str, Any]]) -> Dict[str, int]:
    """Queued minus processed per extension, never below zero; extensions with nothing left are dropped."""
    processed = processed or {}
    remaining: Dict[str, int] = {}
    for ext, total in queued.items():
        left = int(total or 0) - int(processed.get(ext, 0) or 0)
        if left > 0:
            remaining[str(ext)] = left
    return remaining


@dataclass
class JobPace:
    """Measured throughput of one job, in files per minute."""

    window: Dict[str, float] = field(default_factory=dict)  # per extension, last PACE_WINDOW
    lifetime: Dict[str, float] = field(default_factory=dict)  # per extension, while it was being processed
    overall: Optional[float] = None  # all extensions, whole job
    files_total: int = 0

    def for_extension(self, ext: str) -> Tuple[Optional[float], Optional[str]]:
        """The pace to use for ``ext`` and where it comes from (``window``/``lifetime``/``overall``)."""
        rate = self.window.get(ext)
        if rate:
            return rate, "window"
        rate = self.lifetime.get(ext)
        if rate:
            return rate, "lifetime"
        if self.overall:
            return self.overall, "overall"
        return None, None


@dataclass
class EtaExtension:
    extension: str
    remaining: int
    files_per_minute: Optional[float]
    seconds: Optional[float]
    pace_source: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "extension": self.extension,
            "remaining": self.remaining,
            "files_per_minute": self.files_per_minute,
            "seconds": self.seconds,
            "pace_source": self.pace_source,
        }


@dataclass
class EtaEstimate:
    seconds: Optional[float]
    method: str
    remaining_files: int
    unknown_remaining: int
    by_extension: List[EtaExtension]

    def to_dict(self) -> Dict[str, Any]:
        """The ``IngestionEta`` payload of the status API."""
        return {
            "seconds": self.seconds,
            "method": self.method,
            "remaining_files": self.remaining_files,
            "unknown_remaining": self.unknown_remaining,
            "by_extension": [row.to_dict() for row in self.by_extension],
        }


def estimate(remaining: Mapping[str, int], pace: JobPace) -> EtaEstimate:
    """ETA = sum over extensions of remaining files / that extension's pace.

    Args:
        remaining: Files left per extension (:func:`remaining_by_extension`).
        pace: Measured throughput (:func:`measure_pace`).
    """
    rows: List[EtaExtension] = []
    total_seconds = 0.0
    any_pace = False
    unknown = 0
    for ext, left in sorted(remaining.items(), key=lambda item: (-item[1], item[0])):
        left = int(left)
        if left <= 0:
            continue
        rate, source = pace.for_extension(ext)
        seconds: Optional[float] = None
        if rate:
            seconds = round(left / rate * 60.0, 1)
            total_seconds += seconds
            any_pace = True
        else:
            unknown += left
        rows.append(EtaExtension(ext, left, round(rate, 3) if rate else None, seconds, source))
    return EtaEstimate(
        seconds=round(total_seconds) if any_pace else None,
        method="per_extension",
        remaining_files=sum(int(v) for v in remaining.values() if int(v) > 0),
        unknown_remaining=unknown,
        by_extension=rows,
    )


# The extension of a stats document, from its file name: ".pdf" (lower case), "" without one
_EXT_EXPR: Dict[str, Any] = {
    "$let": {
        "vars": {"parts": {"$split": [{"$ifNull": ["$file_name", ""]}, "."]}},
        "in": {
            "$cond": [
                {"$gt": [{"$size": "$$parts"}, 1]},
                {"$concat": [".", {"$toLower": {"$arrayElemAt": ["$$parts", -1]}}]},
                "",
            ]
        },
    }
}


async def _aggregate(collection: Any, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run an aggregation on a Motor or PyMongo-Async collection (the API still mixes both)."""
    cursor = collection.aggregate(pipeline)
    if inspect.isawaitable(cursor):
        cursor = await cursor
    return await cursor.to_list(length=None)


def _parse_iso(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _minutes(start: Optional[datetime], end: datetime, floor: float = 1.0) -> float:
    if start is None:
        return floor
    return max(floor, (end - start).total_seconds() / 60.0)


async def measure_pace(db: Any, job_id: str, now: Optional[datetime] = None) -> JobPace:
    """Read a job's per-extension throughput from ``ingestion_stats``.

    Args:
        db: The profile's database (raw ``AsyncDatabase``), holding ``ingestion_stats``.
        job_id: The job.
        now: Reference time (naive local time, like the stats' ``completed_at``).
    """
    moment = now or datetime.now()
    since = (moment - PACE_WINDOW).isoformat()
    stats = db["ingestion_stats"]

    window_rows = await _aggregate(stats, [
        {"$match": {"job_id": job_id, "completed_at": {"$gte": since}}},
        {"$group": {"_id": _EXT_EXPR, "n": {"$sum": 1}, "first": {"$min": "$completed_at"}}},
    ])
    lifetime_rows = await _aggregate(stats, [
        {"$match": {"job_id": job_id}},
        {"$group": {"_id": _EXT_EXPR, "n": {"$sum": 1}, "first": {"$min": "$completed_at"}, "last": {"$max": "$completed_at"}}},
    ])

    pace = JobPace()
    for row in window_rows:
        n = int(row.get("n") or 0)
        if n <= 0:
            continue
        # A run younger than the window is measured over its own span, not over 60 minutes
        span = min(PACE_WINDOW.total_seconds() / 60.0, _minutes(_parse_iso(row.get("first")), moment))
        pace.window[str(row.get("_id") or "")] = n / span

    first_overall: Optional[datetime] = None
    last_overall: Optional[datetime] = None
    for row in lifetime_rows:
        n = int(row.get("n") or 0)
        if n <= 0:
            continue
        first, last = _parse_iso(row.get("first")), _parse_iso(row.get("last"))
        pace.lifetime[str(row.get("_id") or "")] = n / _minutes(first, last or moment)
        pace.files_total += n
        if first and (first_overall is None or first < first_overall):
            first_overall = first
        if last and (last_overall is None or last > last_overall):
            last_overall = last
    if pace.files_total:
        pace.overall = pace.files_total / _minutes(first_overall, last_overall or moment)
    return pace


_pace_cache: Dict[str, Tuple[float, JobPace]] = {}


def reset_cache() -> None:
    """Forget the cached paces (tests)."""
    _pace_cache.clear()


async def job_eta(
    db: Any,
    job: Mapping[str, Any],
    now: Optional[datetime] = None,
    *,
    ttl_seconds: float = PACE_TTL_SECONDS,
) -> Optional[EtaEstimate]:
    """The ETA of a job from its per-extension queue and the measured pace.

    Args:
        db: The profile's database (raw), or ``None`` (no pace: every extension unknown).
        job: The job document or in-memory state: ``job_id``, ``queue_by_extension`` and
            ``processed_by_extension`` as the runners record them; a job without the
            per-extension queue (started before this estimator) falls back to
            ``total_files - processed_files`` at the overall pace.
        now: Reference time.
        ttl_seconds: How long a measured pace is reused for the job.

    Returns:
        The estimate, or ``None`` when nothing is known about the remaining work.
    """
    job_id = str(job.get("job_id") or job.get("_id") or "")
    queued = job.get("queue_by_extension")
    processed = job.get("processed_by_extension") or {}
    if isinstance(queued, Mapping) and queued:
        remaining = remaining_by_extension(queued, processed)
        method = "per_extension"
    else:
        total = int(job.get("total_files") or 0)
        done = int(job.get("processed_files") or 0)
        if total <= 0:
            return None
        remaining = {"*": max(0, total - done)}
        method = "overall"

    pace = JobPace()
    if db is not None and job_id:
        cached = _pace_cache.get(job_id)
        if cached and ttl_seconds > 0 and time.monotonic() - cached[0] < ttl_seconds:
            pace = cached[1]
        else:
            pace = await measure_pace(db, job_id, now)
            _pace_cache[job_id] = (time.monotonic(), pace)
    if method == "overall":
        # No per-extension queue: the overall pace is all there is
        pace = JobPace(overall=pace.overall, files_total=pace.files_total)
    result = estimate(remaining, pace)
    result.method = method
    return result
