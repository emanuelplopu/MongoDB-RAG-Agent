"""Health at a glance for the machine-stats widget: ``GET /api/v1/telemetry/overview``.

Pulls together what the widget cannot get from a single host sample: the newest db-stats sample
(:mod:`backend.services.db_stats`), anomaly counts over the last day (cached for a minute, since
the widget polls), process liveness from ``activity_current`` heartbeats with the running job's
pace and ETA, chat latency percentiles from ``chat.request`` events, the newest backup per database
and the models Ollama had loaded when the host agent last looked.

Every part is best-effort: a failed read leaves its section empty rather than failing the call.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional, Sequence

from backend.services.ingestion_eta import EtaEstimate

from backend.models.telemetry_history import (
    AnomalyReport,
    OverviewAnomalies,
    OverviewBackup,
    OverviewChat,
    OverviewDb,
    OverviewEtaExtension,
    OverviewLlm,
    OverviewLlmModel,
    OverviewProcess,
    TelemetryOverview,
)
from backend.services import telemetry_analysis
from backend.services.telemetry_store import TelemetryStore

logger = logging.getLogger(__name__)

__all__ = ["AnomalyCache", "overview", "overview_db", "process_rows", "top_titles"]

#: Host samples older than this carry no useful "loaded models" information.
_LLM_SAMPLE_MAX_AGE_S = 600.0
#: Window for the running job's pace.
_PACE_WINDOW = timedelta(hours=1)


def _aware(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class AnomalyCache:
    """Remembers the last anomaly report for ``ttl_seconds`` so a polling widget does not re-run the rules."""

    def __init__(self, ttl_seconds: float = 60.0) -> None:
        self._ttl = ttl_seconds
        self._report: Optional[AnomalyReport] = None
        self._hours: Optional[float] = None
        self._stored_at = 0.0

    def get(self, hours: float) -> Optional[AnomalyReport]:
        """The cached report for ``hours`` while it is fresh."""
        if self._report is not None and self._hours == hours and time.monotonic() - self._stored_at < self._ttl:
            return self._report
        return None

    def put(self, hours: float, report: AnomalyReport) -> None:
        """Remember ``report``."""
        self._report, self._hours, self._stored_at = report, hours, time.monotonic()


def top_titles(report: AnomalyReport, limit: int = 3) -> list[str]:
    """Titles of the most severe findings (the report is already sorted)."""
    return [a.title for a in report.anomalies[:limit]]


def overview_db(sample: Optional[dict[str, Any]], now: datetime) -> Optional[OverviewDb]:
    """Condense the newest db-stats sample into the widget's database line."""
    if not sample:
        return None
    ts = _aware(sample.get("ts"))
    if ts is None:
        return None
    totals = sample.get("totals") or {}
    coverage: list[float] = []
    index_states: list[bool] = []
    lag_total = 0
    has_lag = False
    for db in sample.get("databases") or []:
        chunk_sample = db.get("sample") or {}
        if (chunk_sample.get("size") or 0) > 0 and chunk_sample.get("embedding_coverage_pct") is not None:
            coverage.append(float(chunk_sample["embedding_coverage_pct"]))
        for idx in db.get("search_indexes") or []:
            index_states.append(idx.get("status") == "READY" and idx.get("queryable") is not False)
        lag = db.get("search_lag") or {}
        if lag.get("lag") is not None:
            has_lag = True
            lag_total += int(lag["lag"])
    oplog = sample.get("oplog") or {}
    return OverviewDb(
        sampled_at=ts,
        age_seconds=round(max(0.0, (now - ts).total_seconds()), 1),
        databases=int(totals.get("databases") or 0),
        documents=int(totals.get("documents") or 0),
        chunks=int(totals.get("chunks") or 0),
        data_bytes=int(totals.get("data_bytes") or 0),
        storage_bytes=int(totals.get("storage_bytes") or 0),
        index_bytes=int(totals.get("index_bytes") or 0),
        size_on_disk_bytes=int(totals.get("size_on_disk_bytes") or 0),
        oplog_bytes=oplog.get("storage_bytes"),
        embedding_coverage_pct=min(coverage) if coverage else None,
        search_indexes_ok=all(index_states) if index_states else None,
        search_lag=lag_total if has_lag else None,
    )


def process_rows(docs: Sequence[dict[str, Any]], now: datetime, *, online_within_s: float,
                 pace: Optional[dict[str, int]] = None,
                 etas: Optional[dict[str, EtaEstimate]] = None) -> list[OverviewProcess]:
    """Map ``activity_current`` documents to liveness rows.

    ``pace`` is the files finished per job in the last hour (shown as files/h); ``etas`` the
    per-extension estimates of :mod:`backend.services.ingestion_eta`, the same ones the
    ingestion status API reports. A job without one falls back to remaining files at ``pace``.
    """
    rows: list[OverviewProcess] = []
    for doc in docs:
        seen = _aware(doc.get("updated_at"))
        age = max(0.0, (now - seen).total_seconds()) if seen else None
        job_id = doc.get("job_id")
        files_per_hour: Optional[float] = None
        eta: Optional[float] = None
        eta_method: Optional[str] = None
        eta_rows: list[OverviewEtaExtension] = []
        if job_id and pace is not None and job_id in pace:
            files_per_hour = float(pace[job_id])
        estimate = etas.get(job_id) if etas and job_id else None
        if estimate is not None and estimate.seconds is not None:
            eta = estimate.seconds
            eta_method = estimate.method
            eta_rows = [
                OverviewEtaExtension(extension=row.extension, remaining=row.remaining,
                                     files_per_minute=row.files_per_minute, seconds=row.seconds,
                                     pace_source=row.pace_source)
                for row in estimate.by_extension
            ]
        elif files_per_hour:
            processed, total = doc.get("processed_files"), doc.get("total_files")
            if files_per_hour > 0 and isinstance(processed, int) and isinstance(total, int) and total >= processed:
                eta = round((total - processed) / files_per_hour * 3600.0)
                eta_method = "pace"
        rows.append(OverviewProcess(
            key=str(doc.get("_id")),
            tenant=doc.get("tenant"),
            source=doc.get("source"),
            host=doc.get("host"),
            last_seen_at=seen,
            age_seconds=round(age, 1) if age is not None else None,
            online=age is not None and age <= online_within_s,
            job_id=job_id,
            job_status=doc.get("job_status"),
            phase=doc.get("phase"),
            step=doc.get("step"),
            processed_files=doc.get("processed_files"),
            total_files=doc.get("total_files"),
            files_per_hour=files_per_hour,
            eta_seconds=eta,
            eta_method=eta_method,
            eta_by_extension=eta_rows,
        ))
    rows.sort(key=lambda r: (r.source != "api", r.key))
    return rows


async def _pace(store: TelemetryStore, job_ids: Sequence[str], now: datetime) -> dict[str, int]:
    if not job_ids:
        return {}
    rows = await store.aggregate_events([
        {"$match": {"ts": {"$gte": now - _PACE_WINDOW, "$lt": now}, "meta.kind": "file.end",
                    "meta.job_id": {"$in": list(job_ids)}}},
        {"$group": {"_id": "$meta.job_id", "n": {"$sum": 1}}},
    ])
    return {str(r["_id"]): int(r["n"]) for r in rows}


async def _chat(store: TelemetryStore, hours: float, now: datetime) -> OverviewChat:
    rows = await store.aggregate_events([
        {"$match": {"ts": {"$gte": now - timedelta(hours=hours), "$lt": now}, "meta.kind": "chat.request"}},
        {"$group": {
            "_id": None,
            "n": {"$sum": 1},
            "errors": {"$sum": {"$cond": [{"$eq": ["$outcome", "error"]}, 1, 0]}},
            "p": {"$percentile": {"input": "$duration_ms", "p": [0.5, 0.95], "method": "approximate"}},
        }},
    ])
    if not rows:
        return OverviewChat(hours=hours)
    row = rows[0]
    p = row.get("p") or [None, None]
    return OverviewChat(hours=hours, requests=int(row.get("n") or 0), errors=int(row.get("errors") or 0),
                        p50_ms=p[0], p95_ms=p[1] if len(p) > 1 else None)


def _backups(sample: Optional[dict[str, Any]], now: datetime) -> list[OverviewBackup]:
    out: list[OverviewBackup] = []
    for db in (sample or {}).get("databases") or []:
        if int(((db.get("documents") or {}).get("count")) or 0) <= 0:
            continue
        backup = db.get("backup") or {}
        newest = _aware(backup.get("newest_at"))
        out.append(OverviewBackup(
            database=str(db.get("name")),
            profile_key=db.get("profile_key"),
            count=int(backup.get("count") or 0),
            newest_at=newest,
            age_seconds=round((now - newest).total_seconds()) if newest else None,
            size_bytes=backup.get("newest_size_bytes"),
            status=backup.get("newest_status"),
        ))
    return out


def _llm(host_sample: Optional[dict[str, Any]]) -> OverviewLlm:
    if not host_sample:
        return OverviewLlm()
    models = [
        OverviewLlmModel(name=str(m.get("name")), size_bytes=m.get("size_bytes"), vram_bytes=m.get("vram_bytes"), ctx=m.get("ctx"))
        for m in ((host_sample.get("ollama") or {}).get("models") or [])
        if isinstance(m, dict) and m.get("name")
    ]
    return OverviewLlm(sampled_at=_aware(host_sample.get("ts")), models=models)


EtaLookup = Callable[[Sequence[str]], Awaitable[dict[str, EtaEstimate]]]


async def overview(store: TelemetryStore, *, hours: float = 24.0, online_within_s: float = 90.0,
                   cache: Optional[AnomalyCache] = None, now: Optional[datetime] = None,
                   eta_lookup: Optional[EtaLookup] = None) -> TelemetryOverview:
    """Build the overview; each section degrades to empty on its own error.

    Args:
        store: The tenant-scoped telemetry store.
        hours: Window for anomaly counts and chat latency.
        online_within_s: A process seen within this many seconds counts as online.
        cache: Anomaly cache shared across calls (``app.state``).
        now: Reference time (tests).
        eta_lookup: Returns the per-extension ETA of the given job ids (the router wires
            :func:`backend.services.ingestion_eta.job_eta` over the tenant's jobs); without
            one the rows fall back to remaining files at the last hour's pace.
    """
    moment = now or datetime.now(timezone.utc)

    db_sample: Optional[dict[str, Any]] = None
    try:
        db_sample = await store.latest_db_sample()
    except Exception as exc:  # noqa: BLE001
        logger.debug("overview: db sample unavailable: %s", exc)

    report = cache.get(hours) if cache else None
    if report is None:
        try:
            report = await telemetry_analysis.range_anomalies(store, moment - timedelta(hours=hours), moment, now=moment)
            if cache:
                cache.put(hours, report)
        except Exception as exc:  # noqa: BLE001
            logger.debug("overview: anomalies unavailable: %s", exc)
            report = AnomalyReport(start=moment - timedelta(hours=hours), end=moment, anomalies=[])
    counts = {"critical": 0, "warning": 0, "info": 0}
    for a in report.anomalies:
        counts[a.severity] = counts.get(a.severity, 0) + 1
    anomalies = OverviewAnomalies(hours=hours, critical=counts["critical"], warning=counts["warning"],
                                  info=counts["info"], top=top_titles(report))

    processes: list[OverviewProcess] = []
    try:
        docs = await store.activity_current_docs()
        job_ids = [d["job_id"] for d in docs if d.get("job_id")]
        pace = await _pace(store, job_ids, moment)
        etas: dict[str, EtaEstimate] = {}
        if eta_lookup is not None and job_ids:
            try:
                etas = await eta_lookup(job_ids)
            except Exception as exc:  # noqa: BLE001
                logger.debug("overview: job ETAs unavailable: %s", exc)
        processes = process_rows(docs, moment, online_within_s=online_within_s, pace=pace, etas=etas)
    except Exception as exc:  # noqa: BLE001
        logger.debug("overview: processes unavailable: %s", exc)

    try:
        chat = await _chat(store, hours, moment)
    except Exception as exc:  # noqa: BLE001
        logger.debug("overview: chat stats unavailable: %s", exc)
        chat = OverviewChat(hours=hours)

    host_sample: Optional[dict[str, Any]] = None
    try:
        host_sample = await store.latest_host_sample(max_age_seconds=_LLM_SAMPLE_MAX_AGE_S)
    except Exception as exc:  # noqa: BLE001
        logger.debug("overview: host sample unavailable: %s", exc)

    return TelemetryOverview(
        generated_at=moment,
        db=overview_db(db_sample, moment),
        anomalies=anomalies,
        processes=processes,
        chat=chat,
        backups=_backups(db_sample, moment),
        llm=_llm(host_sample),
    )
