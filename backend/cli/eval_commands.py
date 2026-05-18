"""Evaluation and experiment CLI commands for quellexctl."""
from __future__ import annotations

import json
import sys
from typing import Optional

try:
    import typer
    from rich.console import Console
    from rich.table import Table

    HAS_TYPER = True
except ImportError:
    HAS_TYPER = False

from backend.cli.main import app, OutputFormatter, run_async

if HAS_TYPER:
    eval_app = typer.Typer(name="evaluation", help="Evaluation commands")
    experiment_app = typer.Typer(name="experiment", help="Experiment commands")
    app.add_typer(eval_app, name="evaluation")
    app.add_typer(experiment_app, name="experiment")
    console = Console()


# ─── Async helpers ────────────────────────────────────────────────────────────


async def _run_evaluation(
    strategy: str,
    dataset: str,
    judge_model: Optional[str],
) -> list[dict]:
    """Instantiate EvaluationRunner (no DB) and run evaluation."""
    from backend.evaluation.runner import EvaluationRunner

    runner = EvaluationRunner(db=None)
    runs = await runner.run_evaluation(
        strategy_id=strategy,
        dataset_id=dataset,
        judge_model=judge_model,
    )
    return [
        {
            "test_case_id": r.test_case_id,
            "composite_score": r.result.composite_score,
            "weighted_sum": r.result.weighted_sum,
            "latency_score": r.result.latency_score,
            "duration_ms": round(r.duration_ms, 1),
        }
        for r in runs
    ]


async def _get_leaderboard(dataset: str, limit: int) -> list[dict]:
    """Fetch leaderboard rankings from in-memory runner."""
    from backend.evaluation.runner import EvaluationRunner

    runner = EvaluationRunner(db=None)
    return await runner.get_leaderboard(dataset_id=dataset, limit=limit)


async def _run_comparison(
    strategy_ids: list[str],
    dataset: str,
) -> dict[str, list[dict]]:
    """Run comparison across multiple strategies and return summary stats."""
    from backend.evaluation.runner import EvaluationRunner

    runner = EvaluationRunner(db=None)
    comparison = await runner.run_comparison(
        strategy_ids=strategy_ids,
        dataset_id=dataset,
    )

    summary: list[dict] = []
    for sid, runs in comparison.items():
        if not runs:
            summary.append({
                "strategy_id": sid,
                "avg_score": 0.0,
                "best_score": 0.0,
                "worst_score": 0.0,
                "runs": 0,
            })
            continue

        scores = [r.result.composite_score for r in runs]
        summary.append({
            "strategy_id": sid,
            "avg_score": sum(scores) / len(scores),
            "best_score": max(scores),
            "worst_score": min(scores),
            "runs": len(runs),
        })

    # Sort by avg_score descending
    summary.sort(key=lambda x: x["avg_score"], reverse=True)
    return {"strategies": summary, "dataset": dataset}


async def _get_experiment_report(run_id: str) -> dict:
    """Retrieve a stored experiment run report.

    Without a live DB connection from the CLI, this returns a
    placeholder structure.  A real implementation would query the
    evaluation_results collection.
    """
    return {
        "run_id": run_id,
        "status": "no_db_connection",
        "message": (
            "Experiment report requires a database connection. "
            "Use the API endpoint GET /api/evaluation/runs/{run_id} "
            "for full report data."
        ),
    }


# ─── Formatting helpers ──────────────────────────────────────────────────────


def _score_to_pct(score: float) -> str:
    """Format a 0-1 score as a percentage string."""
    return f"{score * 100:.1f}%"


def _rank_style(rank: int, total: int) -> str:
    """Return a rich color tag based on rank position."""
    if total <= 1:
        return "green"
    ratio = (rank - 1) / (total - 1)
    if ratio <= 0.33:
        return "green"
    elif ratio <= 0.66:
        return "yellow"
    return "red"


# ─── Evaluation commands ─────────────────────────────────────────────────────

if HAS_TYPER:

    @eval_app.command("run")
    def eval_run(
        strategy: str = typer.Option(
            ..., "--strategy", "-s", help="Strategy ID to evaluate"
        ),
        dataset: str = typer.Option(
            "default", "--dataset", "-d", help="Test case dataset ID"
        ),
        judge_model: Optional[str] = typer.Option(
            None, "--judge", "-j", help="Judge model for scoring"
        ),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ):
        """Run evaluation for a strategy against a test case dataset."""
        fmt = OutputFormatter(output_format)
        try:
            results = run_async(
                _run_evaluation(strategy, dataset, judge_model)
            )
        except Exception as exc:
            fmt.print_error(f"Evaluation failed: {exc}")
            raise typer.Exit(code=1)

        if not results:
            console.print(
                f"[yellow]No test cases found for dataset '{dataset}'.[/yellow]\n"
                "Create test cases first via the API or test case manager."
            )
            raise typer.Exit(code=0)

        if output_format == "json":
            print(json.dumps(results, indent=2, default=str))
        else:
            table = Table(title=f"Evaluation: strategy={strategy}  dataset={dataset}")
            table.add_column("Test Case ID", style="cyan", no_wrap=True)
            table.add_column("Composite", justify="right")
            table.add_column("Weighted Sum", justify="right")
            table.add_column("Latency Score", justify="right")
            table.add_column("Duration (ms)", justify="right")

            for r in results:
                table.add_row(
                    r["test_case_id"][:12] + "...",
                    _score_to_pct(r["composite_score"]),
                    _score_to_pct(r["weighted_sum"]),
                    _score_to_pct(r["latency_score"]),
                    str(r["duration_ms"]),
                )
            console.print(table)

        avg = sum(r["composite_score"] for r in results) / len(results)
        console.print(
            f"\n[bold]Average composite score:[/bold] {_score_to_pct(avg)}"
        )

    @eval_app.command("leaderboard")
    def eval_leaderboard(
        dataset: str = typer.Option(
            "default", "--dataset", "-d", help="Test case dataset ID"
        ),
        limit: int = typer.Option(
            10, "--limit", "-n", help="Max entries to show"
        ),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ):
        """Show strategy leaderboard rankings."""
        fmt = OutputFormatter(output_format)
        try:
            rankings = run_async(_get_leaderboard(dataset, limit))
        except Exception as exc:
            fmt.print_error(f"Leaderboard fetch failed: {exc}")
            raise typer.Exit(code=1)

        if not rankings:
            console.print(
                f"[yellow]No evaluation results for dataset '{dataset}'.[/yellow]\n"
                "Run evaluations first with: quellexctl evaluation run --strategy <id>"
            )
            raise typer.Exit(code=0)

        if output_format == "json":
            print(json.dumps(rankings, indent=2, default=str))
        else:
            table = Table(title=f"Leaderboard: dataset={dataset}")
            table.add_column("Rank", justify="right", style="bold")
            table.add_column("Strategy ID", style="cyan")
            table.add_column("Avg Score", justify="right")
            table.add_column("Runs", justify="right")

            total = len(rankings)
            for entry in rankings:
                color = _rank_style(entry["rank"], total)
                table.add_row(
                    f"[{color}]#{entry['rank']}[/{color}]",
                    entry["strategy_id"],
                    f"[{color}]{_score_to_pct(entry['avg_score'])}[/{color}]",
                    str(entry["runs"]),
                )
            console.print(table)

    # ─── Experiment commands ──────────────────────────────────────────────────

    @experiment_app.command("run")
    def experiment_run(
        strategies: str = typer.Option(
            ...,
            "--strategies",
            "-s",
            help="Comma-separated strategy IDs to compare",
        ),
        dataset: str = typer.Option(
            "default", "--dataset", "-d", help="Test case dataset ID"
        ),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ):
        """Compare multiple strategies (A/B testing)."""
        strategy_ids = [s.strip() for s in strategies.split(",")]
        if len(strategy_ids) < 2:
            console.print(
                "[red]Error:[/red] Provide at least 2 comma-separated strategy IDs."
            )
            raise typer.Exit(code=1)

        fmt = OutputFormatter(output_format)
        try:
            comparison = run_async(_run_comparison(strategy_ids, dataset))
        except Exception as exc:
            fmt.print_error(f"Experiment failed: {exc}")
            raise typer.Exit(code=1)

        summaries = comparison["strategies"]

        if not summaries or all(s["runs"] == 0 for s in summaries):
            console.print(
                f"[yellow]No test cases found for dataset '{dataset}'.[/yellow]\n"
                "Create test cases first via the API or test case manager."
            )
            raise typer.Exit(code=0)

        if output_format == "json":
            print(json.dumps(comparison, indent=2, default=str))
        else:
            table = Table(
                title=f"Experiment Comparison: dataset={dataset}"
            )
            table.add_column("Strategy ID", style="cyan")
            table.add_column("Avg Score", justify="right")
            table.add_column("Best Score", justify="right")
            table.add_column("Worst Score", justify="right")
            table.add_column("Runs", justify="right")

            for i, s in enumerate(summaries):
                color = _rank_style(i + 1, len(summaries))
                table.add_row(
                    s["strategy_id"],
                    f"[{color}]{_score_to_pct(s['avg_score'])}[/{color}]",
                    _score_to_pct(s["best_score"]),
                    _score_to_pct(s["worst_score"]),
                    str(s["runs"]),
                )
            console.print(table)

    @experiment_app.command("report")
    def experiment_report(
        run_id: str = typer.Argument(..., help="Experiment run ID"),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ):
        """Show detailed experiment report."""
        fmt = OutputFormatter(output_format)
        try:
            report = run_async(_get_experiment_report(run_id))
        except Exception as exc:
            fmt.print_error(f"Failed to fetch report: {exc}")
            raise typer.Exit(code=1)

        fmt.print_result(report, title=f"Experiment Report: {run_id}")
