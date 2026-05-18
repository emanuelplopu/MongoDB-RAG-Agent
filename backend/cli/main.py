"""
quellexctl — Command-line interface for the Strategy OS.

Usage:
    quellexctl strategy run <spec-id> --query "..."
    quellexctl strategy list
    quellexctl evaluation run --strategy <id> --dataset <id>
    quellexctl experiment run --strategies <id1,id2> --dataset <id>
    quellexctl run prompt "query text" --profile <key>
"""
from __future__ import annotations
import asyncio
import json
import sys
from typing import Optional

try:
    import typer
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    HAS_TYPER = True
except ImportError:
    HAS_TYPER = False

# Create the main app
if HAS_TYPER:
    app = typer.Typer(
        name="quellexctl",
        help="Strategy OS command-line interface for strategy execution, evaluation, and experimentation.",
        no_args_is_help=True,
    )
    console = Console()
else:
    app = None
    console = None


# Output formatting helpers
class OutputFormatter:
    """Handles JSON vs rich table output formatting."""

    def __init__(self, output_format: str = "table"):
        self.format = output_format

    def print_result(self, data: dict, title: str = "Result") -> None:
        """Print a result dict in the configured format."""
        if self.format == "json":
            print(json.dumps(data, indent=2, default=str))
        elif HAS_TYPER:
            panel = Panel(
                json.dumps(data, indent=2, default=str),
                title=title,
                border_style="green",
            )
            console.print(panel)
        else:
            print(json.dumps(data, indent=2, default=str))

    def print_table(self, headers: list[str], rows: list[list], title: str = "") -> None:
        """Print tabular data."""
        if self.format == "json":
            data = [dict(zip(headers, row)) for row in rows]
            print(json.dumps(data, indent=2, default=str))
        elif HAS_TYPER:
            table = Table(title=title)
            for h in headers:
                table.add_column(h)
            for row in rows:
                table.add_row(*[str(c) for c in row])
            console.print(table)
        else:
            print(f"\n{title}")
            print("\t".join(headers))
            for row in rows:
                print("\t".join(str(c) for c in row))

    def print_error(self, message: str) -> None:
        """Print an error message."""
        if HAS_TYPER:
            console.print(f"[red]Error:[/red] {message}")
        else:
            print(f"Error: {message}", file=sys.stderr)

    def print_success(self, message: str) -> None:
        """Print a success message."""
        if HAS_TYPER:
            console.print(f"[green]Success:[/green] {message}")
        else:
            print(f"Success: {message}")


def run_async(coro):
    """Helper to run async functions from sync CLI context."""
    return asyncio.run(coro)


# Global options callback
if HAS_TYPER:
    @app.callback()
    def main_callback(
        tenant: str = typer.Option("recallhub", "--tenant", "-t", help="Tenant ID"),
        profile: str = typer.Option("default", "--profile", "-p", help="Profile key"),
        output_format: str = typer.Option("table", "--format", "-f", help="Output format: table or json"),
    ):
        """quellexctl — Strategy OS CLI for execution, evaluation, and experimentation."""
        # Store in context for sub-commands
        app.state = {"tenant": tenant, "profile": profile, "format": output_format}


# Sub-command groups will be added by strategy_commands.py and eval_commands.py
# They register via:
#   from backend.cli.main import app
#   strategy_app = typer.Typer(...)
#   app.add_typer(strategy_app, name="strategy")


def cli_entry():
    """Entry point for the CLI (can be called from pyproject.toml scripts)."""
    if not HAS_TYPER:
        print("Error: typer and rich packages are required. Install with: pip install typer rich")
        sys.exit(1)
    # Ensure sub-command modules are registered before dispatching.
    import backend.cli.strategy_commands  # noqa: F401
    import backend.cli.eval_commands  # noqa: F401
    import backend.cli.phase6_commands  # noqa: F401
    app()


if __name__ == "__main__":
    cli_entry()
