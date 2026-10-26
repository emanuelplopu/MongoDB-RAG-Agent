"""The shared per-extension ETA (item 8 of the 2026-10-26 prompt-test fixes).

The status endpoint used ``elapsed / progress`` and the machine-stats widget ``remaining /
files-per-hour``; they disagreed by weeks because the queue is sorted by file type. Both now
use :mod:`backend.services.ingestion_eta`: remaining files per extension (recorded by the
runners) divided by that extension's measured pace (last hour, else the job's lifetime, else
the job's overall pace).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from backend.services import ingestion_eta as eta
from backend.services import telemetry_overview as tov

NOW = datetime(2026, 10, 26, 12, 0, 0)


class _Stats:
    """``ingestion_stats`` stub answering the two aggregations by extension."""

    def __init__(self, window: Dict[str, int], lifetime: Dict[str, int], first_minutes_ago: float = 600.0) -> None:
        self.window = window
        self.lifetime = lifetime
        self.first = (NOW - timedelta(minutes=first_minutes_ago)).isoformat()
        self.calls: List[List[dict]] = []

    def aggregate(self, pipeline: List[dict]):
        self.calls.append(pipeline)
        match = pipeline[0]["$match"]
        if "completed_at" in match:
            rows = [{"_id": ext, "n": n, "first": (NOW - timedelta(minutes=59)).isoformat()} for ext, n in self.window.items()]
        else:
            rows = [{"_id": ext, "n": n, "first": self.first, "last": NOW.isoformat()} for ext, n in self.lifetime.items()]
        return _Cursor(rows)


class _Cursor:
    def __init__(self, rows: List[dict]) -> None:
        self.rows = rows

    async def to_list(self, length: Any = None) -> List[dict]:
        return list(self.rows)


class _Db(dict):
    pass


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    eta.reset_cache()


# ----------------------------------------------------------------------------- helpers

def test_extension_and_counts() -> None:
    assert eta.extension_of("/mnt/Akten/Brief.PDF") == ".pdf"
    assert eta.extension_of("C:\\x\\notes") == ""
    assert eta.count_by_extension(["a.pdf", "b.PDF", "c.doc", "d"]) == {".pdf": 2, ".doc": 1, "": 1}
    assert eta.remaining_by_extension({".pdf": 10, ".doc": 3, ".msg": 2}, {".pdf": 4, ".doc": 3, ".msg": 9}) == {".pdf": 6}


def test_estimate_sums_remaining_over_pace_and_reports_unknown() -> None:
    pace = eta.JobPace(window={".pdf": 2.0}, lifetime={".doc": 10.0}, overall=5.0)
    out = eta.estimate({".pdf": 120, ".doc": 30, ".msg": 50}, pace)
    by_ext = {row.extension: row for row in out.by_extension}
    assert by_ext[".pdf"].seconds == 3600.0 and by_ext[".pdf"].pace_source == "window"
    assert by_ext[".doc"].seconds == 180.0 and by_ext[".doc"].pace_source == "lifetime"
    assert by_ext[".msg"].seconds == 600.0 and by_ext[".msg"].pace_source == "overall"
    assert out.seconds == 3600 + 180 + 600
    assert out.unknown_remaining == 0 and out.remaining_files == 200
    assert [row.extension for row in out.by_extension] == [".pdf", ".msg", ".doc"], "largest remaining first"


def test_estimate_without_any_pace_has_no_seconds() -> None:
    out = eta.estimate({".pdf": 5}, eta.JobPace())
    assert out.seconds is None and out.unknown_remaining == 5
    assert out.to_dict()["by_extension"][0]["pace_source"] is None


# ----------------------------------------------------------------------------- measuring

async def test_measure_pace_reads_window_lifetime_and_overall() -> None:
    stats = _Stats(window={".pdf": 120}, lifetime={".pdf": 1200, ".doc": 600}, first_minutes_ago=600)
    pace = await eta.measure_pace(_Db(ingestion_stats=stats), "job-1", now=NOW)
    assert pace.window[".pdf"] == pytest.approx(120 / 59, rel=0.01), "a window younger than an hour uses its own span"
    assert pace.lifetime[".pdf"] == pytest.approx(2.0) and pace.lifetime[".doc"] == pytest.approx(1.0)
    assert pace.overall == pytest.approx(3.0) and pace.files_total == 1800
    assert all(call[0]["$match"]["job_id"] == "job-1" for call in stats.calls)


async def test_job_eta_uses_the_runners_per_extension_counters_and_caches_the_pace() -> None:
    stats = _Stats(window={".pdf": 60}, lifetime={".pdf": 600, ".doc": 300})
    db = _Db(ingestion_stats=stats)
    job = {
        "job_id": "job-1",
        "queue_by_extension": {".pdf": 1000, ".doc": 400, ".jpg": 10},
        "processed_by_extension": {".pdf": 600, ".doc": 300},
    }
    first = await eta.job_eta(db, job, now=NOW)
    assert first is not None and first.method == "per_extension"
    rows = {r.extension: r for r in first.by_extension}
    assert rows[".pdf"].remaining == 400 and rows[".pdf"].pace_source == "window"
    assert rows[".doc"].remaining == 100 and rows[".doc"].pace_source == "lifetime"
    assert rows[".jpg"].remaining == 10 and rows[".jpg"].pace_source == "overall"
    assert first.seconds == pytest.approx(400 / 1.0 * 60 + 100 / 0.5 * 60 + 10 / 1.5 * 60, rel=0.02)

    calls = len(stats.calls)
    job["processed_by_extension"][".pdf"] = 700
    second = await eta.job_eta(db, job, now=NOW)
    assert len(stats.calls) == calls, "the pace is cached per job"
    assert {r.extension: r.remaining for r in second.by_extension}[".pdf"] == 300


async def test_job_eta_falls_back_to_total_minus_processed_for_old_jobs() -> None:
    stats = _Stats(window={}, lifetime={".pdf": 600}, first_minutes_ago=300)
    out = await eta.job_eta(_Db(ingestion_stats=stats), {"job_id": "j", "total_files": 1000, "processed_files": 600}, now=NOW)
    assert out is not None and out.method == "overall"
    assert out.remaining_files == 400 and out.seconds == pytest.approx(400 / 2.0 * 60)
    assert await eta.job_eta(_Db(ingestion_stats=stats), {"job_id": "j", "total_files": 0}, now=NOW) is None


# ----------------------------------------------------------------------------- the widget

def _activity(job_id: str, processed: int, total: int) -> dict:
    return {"_id": "quellex:api", "tenant": "quellex", "source": "api", "updated_at": NOW.replace(tzinfo=timezone.utc),
            "job_id": job_id, "processed_files": processed, "total_files": total}


def test_process_rows_prefer_the_shared_estimate_over_the_pace_fallback() -> None:
    now = NOW.replace(tzinfo=timezone.utc)
    estimate = eta.estimate({".pdf": 100}, eta.JobPace(window={".pdf": 1.0}))
    rows = tov.process_rows([_activity("j1", 500, 1000)], now, online_within_s=90.0,
                            pace={"j1": 50}, etas={"j1": estimate})
    assert rows[0].files_per_hour == 50.0, "files/h still comes from the activity events"
    assert rows[0].eta_seconds == 6000 and rows[0].eta_method == "per_extension"
    assert rows[0].eta_by_extension[0].extension == ".pdf" and rows[0].eta_by_extension[0].remaining == 100


def test_process_rows_fall_back_to_remaining_at_pace_without_an_estimate() -> None:
    now = NOW.replace(tzinfo=timezone.utc)
    rows = tov.process_rows([_activity("j1", 500, 1000)], now, online_within_s=90.0, pace={"j1": 50}, etas={})
    assert rows[0].eta_seconds == 36000 and rows[0].eta_method == "pace" and rows[0].eta_by_extension == []
