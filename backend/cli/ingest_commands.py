"""``quellexctl ingest`` — ingestion planning commands.

Usage:
    quellexctl ingest estimate --profile diejuristin
    quellexctl ingest estimate --profile diejuristin --full --format json

``estimate`` forecasts the volume (files, GB, tokens, chunks), wall-clock time
and embedding cost of ingesting a profile's document folders *before* a run. It
connects to MongoDB directly (defaults to ``MONGODB_URI`` / ``MONGODB_DATABASE``)
and resolves the profile from ``profiles.yaml``, so it works without the backend
API running — but the document folders are *container* paths, so run it where
those folders are mounted (e.g. ``docker compose run --rm cli ...``). All figures
are rough; see the printed assumptions.
"""
from __future__ import annotations

import os
from typing import Optional

from backend.cli.main import HAS_TYPER, OutputFormatter, app, console, run_async
from backend.services.ingestion_estimator import IngestionEstimator, human_duration

if HAS_TYPER:
    import typer

    ingest_app = typer.Typer(help="Pre-ingestion planning (estimate volume, time and cost).", no_args_is_help=True)
    app.add_typer(ingest_app, name="ingest")
else:  # pragma: no cover - typer missing
    ingest_app = None


def _fmt_int(value: int) -> str:
    """Thousands-separated integer."""
    return f"{int(value):,}"


def _fmt_usd(value: Optional[float]) -> str:
    if not value:
        return "$0.00"
    if value < 0.01:
        return f"${value:.4f}"
    return f"${value:,.2f}"


if HAS_TYPER:

    @ingest_app.command("estimate")
    def estimate(
        profile: str = typer.Option(..., "--profile", "-p", help="Profile key to estimate"),
        incremental: bool = typer.Option(
            True, "--incremental/--full", help="Skip already-ingested files (default) or estimate a full run"
        ),
        mongodb_uri: str = typer.Option(
            os.environ.get("MONGODB_URI", "mongodb://localhost:11017/?directConnection=true"), "--mongodb-uri"
        ),
        benchmark_database: str = typer.Option(
            os.environ.get("MONGODB_DATABASE", "rag_db"),
            "--benchmark-database",
            "-d",
            help="Database holding benchmark_results (defaults to MONGODB_DATABASE)",
        ),
        embedding_model: Optional[str] = typer.Option(
            None, "--embedding-model", help="Override the embedding model used for cost/throughput"
        ),
        embedding_provider: Optional[str] = typer.Option(
            None, "--embedding-provider", help="Provider type for the fallback throughput"
        ),
        sample: int = typer.Option(
            16, "--sample", "-n", min=0, max=500, help="Text files to tokenise for the sampled token method (0 to skip)"
        ),
    ) -> None:
        """Estimate volume, time and cost of ingesting a profile's folders."""
        fmt = OutputFormatter(app.state.get("format", "table") if hasattr(app, "state") else "table")

        try:
            from src.profile import get_profile_manager
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            fmt.print_error(f"Could not load profile manager: {exc}")
            raise typer.Exit(code=1)

        pm = get_profile_manager()
        if profile not in pm.list_profiles():
            available = ", ".join(sorted(pm.list_profiles())) or "(none)"
            fmt.print_error(f"Profile '{profile}' not found. Available: {available}")
            raise typer.Exit(code=1)

        prof = pm.get_profile(profile)
        model = embedding_model or prof.embedding_model or os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
        provider = embedding_provider or os.environ.get("EMBEDDING_PROVIDER", "openai")

        async def _run():
            from pymongo import AsyncMongoClient

            client = AsyncMongoClient(mongodb_uri, serverSelectionTimeoutMS=8000)
            try:
                profile_db = client[prof.database]
                benchmark_db = client[benchmark_database]
                estimator = IngestionEstimator(
                    profile_db,
                    benchmark_db=benchmark_db,
                    documents_collection=prof.collection_documents,
                    chunks_collection=prof.collection_chunks,
                    embedding_model=model,
                    embedding_provider=provider,
                )
                return await estimator.estimate(
                    prof.documents_folders, profile, incremental=incremental, sample_files=sample
                )
            finally:
                await client.close()

        try:
            est = run_async(_run())
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            fmt.print_error(str(exc))
            raise typer.Exit(code=1)

        if fmt.format == "json":
            print(est.model_dump_json(indent=2))
            return

        mode = "incremental" if est.incremental else "full"
        rows = [
            ["Files to process", f"{_fmt_int(est.file_count)} ({mode})"],
            ["Scanned on disk", _fmt_int(est.scanned_files)],
            ["Skipped (already ingested)", _fmt_int(est.skipped_existing)],
            ["Total size", f"{est.total_size_gb:.2f} GB ({_fmt_int(est.total_bytes)} bytes)"],
            ["~ Chunks", _fmt_int(est.estimated_chunks)],
            [
                "~ Tokens",
                f"{_fmt_int(est.estimated_tokens)}  ({_fmt_int(est.estimated_tokens_low)}–"
                f"{_fmt_int(est.estimated_tokens_high)}, {est.token_method})",
            ],
            [
                "~ Time",
                f"{est.estimated_time_human}  ({human_duration(est.estimated_time_low_seconds)}–"
                f"{human_duration(est.estimated_time_high_seconds)}, {est.time_method}, "
                f"{est.concurrency}x concurrent)",
            ],
            [
                "~ Cost",
                f"{_fmt_usd(est.estimated_cost_usd)}  ({_fmt_usd(est.estimated_cost_low_usd)}–"
                f"{_fmt_usd(est.estimated_cost_high_usd)})  [{est.embedding_model}]",
            ],
        ]
        if est.estimated_embedding_seconds is not None:
            rows.append(["  of which embedding", human_duration(est.estimated_embedding_seconds)])
        rows.append(["Benchmark", est.benchmark_date or "none (run 'quellexctl bench embeddings')"])
        rows.append(["Rough estimate", "yes" if est.is_rough else "no"])

        title = f"Ingest estimate — profile '{est.profile_key}'"
        fmt.print_table(["Metric", "Value"], rows, title=title)

        if est.by_extension:
            ext_rows = [
                [
                    f".{b.extension}",
                    _fmt_int(b.file_count),
                    f"{b.total_bytes / (1024 * 1024):.1f} MB",
                    _fmt_int(b.estimated_chunks),
                    _fmt_int(b.estimated_tokens),
                    f"{b.avg_processing_time_ms:.0f} ms" if b.avg_processing_time_ms else "-",
                ]
                for b in est.by_extension
            ]
            fmt.print_table(
                ["Type", "Files", "Size", "~Chunks", "~Tokens", "Avg time/file"],
                ext_rows,
                title="By file type",
            )

        if est.missing_folders:
            console.print(
                "[yellow]warning:[/yellow] missing/unreachable folders: " + ", ".join(est.missing_folders)
            )
        for note in est.assumptions:
            console.print(f"[dim]• {note}[/dim]")
