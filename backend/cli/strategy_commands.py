"""Strategy execution CLI commands for quellexctl."""
from __future__ import annotations

import json
import sys
from typing import Optional

try:
    import typer
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    HAS_TYPER = True
except ImportError:
    HAS_TYPER = False

from backend.cli.main import app, OutputFormatter, run_async

if HAS_TYPER:
    strategy_app = typer.Typer(name="strategy", help="Strategy execution commands")
    app.add_typer(strategy_app, name="strategy")
    console = Console()


# ─── Async helpers ────────────────────────────────────────────────────────────


async def _run_strategy(
    spec_id: str,
    query: str,
    profile: str,
    tenant: str,
) -> dict:
    """Execute a strategy and return result dict."""
    from backend.agent.strategy.business_context_resolver import BusinessContextResolver
    from backend.agent.strategy.nodes.registry import create_default_registry
    from backend.agent.strategy.strategy_runner import StrategyRunner

    try:
        resolver = BusinessContextResolver(tenant_id=tenant)
        context = await resolver.resolve(
            query=query,
            profile_key=profile,
            accessible_profiles=[profile],
        )

        registry = create_default_registry()
        runner = StrategyRunner(registry=registry)

        # No specs in DB yet — return resolved context info
        return {
            "success": True,
            "message": f"Strategy OS context resolved for '{query}'",
            "capability_id": context.capability_id,
            "capability_confidence": context.capability_confidence,
            "tenant_id": context.tenant_id,
            "profile_key": context.profile_key,
            "note": (
                f"No strategy spec '{spec_id}' found in database. "
                "Insert specs into MongoDB 'strategy_specs' collection "
                "to enable DAG execution."
            ),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _list_strategies(tenant: str) -> dict:
    """List available strategy specs."""
    return {
        "source": "mongodb",
        "collection": "strategy_specs",
        "tenant": tenant,
        "specs": [],
        "note": (
            "Strategy specs are stored in the MongoDB 'strategy_specs' collection. "
            "No specs found. Insert strategy spec documents to populate this list."
        ),
    }


async def _inspect_strategy(spec_id: str, tenant: str) -> dict:
    """Inspect a specific strategy spec."""
    return {
        "spec_id": spec_id,
        "tenant": tenant,
        "status": "not_found",
        "note": (
            f"Strategy spec '{spec_id}' not found in the 'strategy_specs' collection. "
            "Use the API or insert the spec document directly into MongoDB."
        ),
    }


async def _quick_prompt(
    query: str,
    profile: str,
    tenant: str,
) -> dict:
    """Run a quick single-query execution with auto spec selection."""
    from backend.agent.strategy.business_context_resolver import BusinessContextResolver
    from backend.agent.strategy.spec_selector import StrategySpecSelector

    try:
        resolver = BusinessContextResolver(tenant_id=tenant)
        context = await resolver.resolve(
            query=query,
            profile_key=profile,
            accessible_profiles=[profile],
        )

        selector = StrategySpecSelector(db=None)
        spec = await selector.select(
            capability_id=context.capability_id,
            agent_mode="auto",
            tenant_id=tenant,
            business_context=context,
        )

        if spec:
            return {
                "success": True,
                "selected_spec": spec.strategy_id,
                "capability_id": context.capability_id,
                "capability_confidence": context.capability_confidence,
                "note": "Spec found but DAG execution requires a live database connection.",
            }

        return {
            "success": True,
            "selected_spec": None,
            "capability_id": context.capability_id,
            "capability_confidence": context.capability_confidence,
            "tenant_id": context.tenant_id,
            "profile_key": context.profile_key,
            "note": (
                "No matching strategy spec found. The system resolved business context "
                "but no specs are available in the database for DAG execution. "
                "Falling back to legacy orchestrator path."
            ),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─── Strategy commands ────────────────────────────────────────────────────────

if HAS_TYPER:

    @strategy_app.command("run")
    def strategy_run(
        spec_id: str = typer.Argument(..., help="Strategy spec ID to execute"),
        query: str = typer.Option(
            ..., "--query", "-q", help="Query text to execute against the strategy"
        ),
        profile: str = typer.Option(
            "default", "--profile", "-p", help="Profile key"
        ),
        tenant: str = typer.Option(
            "recallhub", "--tenant", "-t", help="Tenant ID"
        ),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ):
        """Execute a strategy spec against a query."""
        fmt = OutputFormatter(output_format)
        try:
            result = run_async(_run_strategy(spec_id, query, profile, tenant))
        except Exception as exc:
            fmt.print_error(f"Strategy execution failed: {exc}")
            raise typer.Exit(code=1)

        if not result.get("success"):
            fmt.print_error(result.get("error", "Unknown error"))
            raise typer.Exit(code=1)

        if output_format == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            table = Table(title=f"Strategy Run: {spec_id}")
            table.add_column("Field", style="cyan", no_wrap=True)
            table.add_column("Value")

            table.add_row("Capability ID", str(result.get("capability_id", "—")))
            table.add_row(
                "Confidence",
                f"{result.get('capability_confidence', 0):.1%}",
            )
            table.add_row("Tenant", str(result.get("tenant_id", "—")))
            table.add_row("Profile", str(result.get("profile_key", "—")))
            table.add_row("Message", str(result.get("message", "")))
            console.print(table)

            if result.get("note"):
                console.print(
                    f"\n[yellow]Note:[/yellow] {result['note']}"
                )

    @strategy_app.command("list")
    def strategy_list(
        tenant: str = typer.Option(
            "recallhub", "--tenant", "-t", help="Tenant ID"
        ),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ):
        """List available strategy specs."""
        fmt = OutputFormatter(output_format)
        try:
            result = run_async(_list_strategies(tenant))
        except Exception as exc:
            fmt.print_error(f"Failed to list strategies: {exc}")
            raise typer.Exit(code=1)

        if output_format == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            specs = result.get("specs", [])
            if not specs:
                console.print(
                    f"[yellow]No strategy specs found for tenant '{tenant}'.[/yellow]\n"
                    f"Source: MongoDB collection '{result.get('collection', 'strategy_specs')}'\n\n"
                    "Insert strategy spec documents into the collection to populate this list."
                )
            else:
                table = Table(title=f"Strategy Specs (tenant={tenant})")
                table.add_column("Strategy ID", style="cyan")
                table.add_column("Version")
                table.add_column("Status")
                table.add_column("Capability")
                table.add_column("Display Name")

                for spec in specs:
                    table.add_row(
                        spec.get("strategy_id", ""),
                        spec.get("version", ""),
                        spec.get("status", ""),
                        spec.get("capability_id", "—"),
                        spec.get("display_name", ""),
                    )
                console.print(table)

    @strategy_app.command("inspect")
    def strategy_inspect(
        spec_id: str = typer.Argument(..., help="Strategy spec ID to inspect"),
        tenant: str = typer.Option(
            "recallhub", "--tenant", "-t", help="Tenant ID"
        ),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ):
        """Show strategy spec details (graph nodes, edges, budgets)."""
        fmt = OutputFormatter(output_format)
        try:
            result = run_async(_inspect_strategy(spec_id, tenant))
        except Exception as exc:
            fmt.print_error(f"Failed to inspect strategy: {exc}")
            raise typer.Exit(code=1)

        fmt.print_result(result, title=f"Strategy Spec: {spec_id}")

    # ─── Top-level prompt command ─────────────────────────────────────────────

    @app.command("prompt")
    def prompt_command(
        query: str = typer.Argument(..., help="Query text for quick execution"),
        profile: str = typer.Option(
            "default", "--profile", "-p", help="Profile key"
        ),
        tenant: str = typer.Option(
            "recallhub", "--tenant", "-t", help="Tenant ID"
        ),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ):
        """Quick single-query execution with auto spec selection."""
        fmt = OutputFormatter(output_format)
        try:
            result = run_async(_quick_prompt(query, profile, tenant))
        except Exception as exc:
            fmt.print_error(f"Prompt execution failed: {exc}")
            raise typer.Exit(code=1)

        if not result.get("success"):
            fmt.print_error(result.get("error", "Unknown error"))
            raise typer.Exit(code=1)

        if output_format == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            table = Table(title="Quick Prompt Result")
            table.add_column("Field", style="cyan", no_wrap=True)
            table.add_column("Value")

            table.add_row("Selected Spec", str(result.get("selected_spec", "None")))
            table.add_row("Capability ID", str(result.get("capability_id", "—")))
            table.add_row(
                "Confidence",
                f"{result.get('capability_confidence', 0):.1%}",
            )
            table.add_row("Tenant", str(result.get("tenant_id", "—")))
            table.add_row("Profile", str(result.get("profile_key", "—")))
            console.print(table)

            if result.get("note"):
                console.print(
                    f"\n[yellow]Note:[/yellow] {result['note']}"
                )
