"""``quellexctl telemetry`` — host telemetry history: jobs, anomalies and HTML job reports.

Usage:
    quellexctl telemetry jobs --hours 168
    quellexctl telemetry job --job <job_id>                    (add --format json for the full report)
    quellexctl telemetry anomalies --hours 24
    quellexctl telemetry anomalies --job <job_id>
    quellexctl telemetry report --job <job_id> --out report.html

Reads the telemetry database directly (``MONGODB_URI``, ``TELEMETRY_DATABASE``; default
``mongodb://localhost:11017`` / ``recallhub_telemetry``), so it works on the host while the API is
down. Host samples come from the Windows host agent, activity from the API and the worker
(docs/12-TELEMETRY_SYSTEM.md).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from backend.cli.main import HAS_TYPER, OutputFormatter, app, console, run_async

if HAS_TYPER:
    import typer

    telemetry_app = typer.Typer(help="Host telemetry history: jobs, anomalies, HTML job reports.", no_args_is_help=True)
    app.add_typer(telemetry_app, name="telemetry")
else:  # pragma: no cover - typer missing
    telemetry_app = None

_DEFAULT_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:11017/?directConnection=true")
_DEFAULT_DB = os.environ.get("TELEMETRY_DATABASE", "recallhub_telemetry")


def _open_store(uri: str, database: str):
    from pymongo import AsyncMongoClient

    from backend.services.telemetry_store import TelemetryStore

    client = AsyncMongoClient(uri, tz_aware=True, tzinfo=timezone.utc, serverSelectionTimeoutMS=8000)
    return TelemetryStore(client, database, host_samples_ttl_seconds=30 * 86400, activity_events_ttl_seconds=90 * 86400)


def _fmt():
    return OutputFormatter(app.state.get("format", "table") if hasattr(app, "state") else "table")


def _local(ts: Optional[datetime]) -> str:
    return ts.astimezone().strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, datetime) else ""


if HAS_TYPER:

    @telemetry_app.command("jobs")
    def jobs(
        hours: float = typer.Option(168.0, "--hours", min=0.1, help="Look back this many hours"),
        mongodb_uri: str = typer.Option(_DEFAULT_URI, "--mongodb-uri"),
        database: str = typer.Option(_DEFAULT_DB, "--database", "-d"),
    ) -> None:
        """Ingestion jobs with telemetry in the range, newest first."""
        fmt = _fmt()

        async def _run():
            store = _open_store(mongodb_uri, database)
            try:
                end = datetime.now(timezone.utc)
                return await store.recent_jobs(end - timedelta(hours=hours), end)
            finally:
                await store.close()

        try:
            rows = run_async(_run())
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            fmt.print_error(str(exc))
            raise typer.Exit(code=1)
        fmt.print_table(["Job", "Status", "Tenant", "Source", "First", "Last", "Files", "Failed"],
                        [[r["job_id"], r.get("status") or "", r.get("tenant") or "", r.get("source") or "",
                          _local(r.get("first_ts")), _local(r.get("last_ts")), r.get("files", 0), r.get("failed", 0)]
                         for r in rows], title="Ingestion jobs")

    @telemetry_app.command("job")
    def job(
        job_id: str = typer.Option(..., "--job", "-j", help="Ingestion job id"),
        mongodb_uri: str = typer.Option(_DEFAULT_URI, "--mongodb-uri"),
        database: str = typer.Option(_DEFAULT_DB, "--database", "-d"),
    ) -> None:
        """Correlated report of one job (phases, steps, file types, anomalies)."""
        from backend.services.telemetry_analysis import job_report

        fmt = _fmt()

        async def _run():
            store = _open_store(mongodb_uri, database)
            try:
                return await job_report(store, job_id)
            finally:
                await store.close()

        try:
            report = run_async(_run())
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            fmt.print_error(str(exc))
            raise typer.Exit(code=1)
        if report is None:
            fmt.print_error(f"No telemetry for job {job_id}")
            raise typer.Exit(code=1)
        if fmt.format == "json":
            print(report.model_dump_json(indent=2))
            return
        console.print(f"[bold]Job {report.job_id}[/bold]  status={report.status}  files={report.files} "
                      f"{report.outcomes}  {report.files_per_hour or '–'} files/h  samples={report.agent_samples}")
        fmt.print_table(["Phase", "Start", "Duration s", "CPU %", "RAM max %", "GPU %", "GPU °C max"],
                        [[p.phase, _local(p.start), f"{p.duration_s:.0f}", _r(p.host.get("cpu_pct")),
                          _r(p.host.get("mem_pct_max")), _r(p.host.get("gpu_util_pct")), _r(p.host.get("gpu_temp_c_max"))]
                         for p in report.phases], title="Phases")
        fmt.print_table(["Step", "Files", "Median s", "p95 s", "Share %"],
                        [[s.step, s.files, _s(s.p50_ms), _s(s.p95_ms), s.share_pct] for s in report.steps], title="Steps")
        _print_anomalies(fmt, report.anomalies)

    @telemetry_app.command("anomalies")
    def anomalies(
        hours: float = typer.Option(24.0, "--hours", min=0.1, help="Look back this many hours (ignored with --job)"),
        job_id: Optional[str] = typer.Option(None, "--job", "-j", help="Only this ingestion job"),
        mongodb_uri: str = typer.Option(_DEFAULT_URI, "--mongodb-uri"),
        database: str = typer.Option(_DEFAULT_DB, "--database", "-d"),
    ) -> None:
        """Host, GPU, backend, container and file anomalies, most severe first."""
        from backend.services.telemetry_analysis import job_report, range_anomalies

        fmt = _fmt()

        async def _run():
            store = _open_store(mongodb_uri, database)
            try:
                if job_id:
                    report = await job_report(store, job_id)
                    return None if report is None else report.anomalies
                end = datetime.now(timezone.utc)
                return (await range_anomalies(store, end - timedelta(hours=hours), end)).anomalies
            finally:
                await store.close()

        try:
            found = run_async(_run())
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            fmt.print_error(str(exc))
            raise typer.Exit(code=1)
        if found is None:
            fmt.print_error(f"No telemetry for job {job_id}")
            raise typer.Exit(code=1)
        if fmt.format == "json":
            import json

            print(json.dumps([a.model_dump(mode="json") for a in found], indent=2))
            return
        _print_anomalies(fmt, found)

    @telemetry_app.command("report")
    def report(
        job_id: str = typer.Option(..., "--job", "-j", help="Ingestion job id"),
        out: Path = typer.Option(Path("telemetry-report.html"), "--out", "-o", help="HTML file to write"),
        mongodb_uri: str = typer.Option(_DEFAULT_URI, "--mongodb-uri"),
        database: str = typer.Option(_DEFAULT_DB, "--database", "-d"),
    ) -> None:
        """Write a self-contained HTML report (charts + findings) for one job."""
        from backend.services.telemetry_report_html import render_job_report_html

        fmt = _fmt()

        async def _run():
            store = _open_store(mongodb_uri, database)
            try:
                return await render_job_report_html(store, job_id)
            finally:
                await store.close()

        try:
            page = run_async(_run())
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            fmt.print_error(str(exc))
            raise typer.Exit(code=1)
        if page is None:
            fmt.print_error(f"No telemetry for job {job_id}")
            raise typer.Exit(code=1)
        out.write_text(page, encoding="utf-8")
        fmt.print_success(f"Wrote {out} ({len(page) // 1024} KB)")


def _print_anomalies(fmt: "OutputFormatter", found) -> None:
    if not found:
        fmt.print_success("No anomalies detected")
        return
    fmt.print_table(["Severity", "Finding", "Count", "Detail"],
                    [[a.severity, a.title, a.count, a.detail] for a in found], title="Anomalies")


def _r(value) -> str:
    return "" if value is None else f"{float(value):.1f}"


def _s(ms) -> str:
    return "" if ms is None else f"{ms / 1000:.1f}"
