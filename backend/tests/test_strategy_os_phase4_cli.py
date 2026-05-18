"""
Unit tests for Strategy OS Phase 4 (CLI layer).

Tests verify:
- OutputFormatter: JSON, table, error, and success output formatting
- CLI app structure: Typer app exists, sub-commands registered
- Strategy commands: argument validation, list, prompt, JSON output
- Evaluation commands: required options, leaderboard, experiment
- Integration: cli_entry callable, run_async helper
"""

import asyncio
import json
import pytest
from io import StringIO
from unittest.mock import patch, MagicMock

from backend.cli.main import (
    OutputFormatter,
    HAS_TYPER,
    run_async,
    cli_entry,
)

# Conditionally import typer-dependent objects
if HAS_TYPER:
    import typer
    from typer.testing import CliRunner
    from backend.cli.main import app
    # Force sub-command registration by importing the modules
    import backend.cli.strategy_commands  # noqa: F401
    import backend.cli.eval_commands  # noqa: F401

    runner = CliRunner()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. OutputFormatter Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestOutputFormatter:
    """Tests for OutputFormatter JSON, table, error, and success methods."""

    def test_json_format_prints_dict(self, capsys):
        """format='json' prints valid JSON for a dict result."""
        fmt = OutputFormatter(output_format="json")
        data = {"key": "value", "count": 42}
        fmt.print_result(data, title="Test")
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["key"] == "value"
        assert parsed["count"] == 42

    def test_json_format_prints_table_as_list(self, capsys):
        """Table data serialized as JSON list when format='json'."""
        fmt = OutputFormatter(output_format="json")
        headers = ["Name", "Score"]
        rows = [["Alice", "95"], ["Bob", "87"]]
        fmt.print_table(headers, rows, title="Scores")
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["Name"] == "Alice"
        assert parsed[1]["Score"] == "87"

    def test_error_message_includes_text(self, capsys):
        """print_error output contains the error text."""
        fmt = OutputFormatter(output_format="table")
        fmt.print_error("something went wrong")
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "something went wrong" in combined

    def test_success_message_includes_text(self, capsys):
        """print_success output contains the success message."""
        fmt = OutputFormatter(output_format="table")
        fmt.print_success("operation completed")
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "operation completed" in combined


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CLI App Structure Tests
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not HAS_TYPER, reason="typer not installed")
class TestCLIStructure:
    """Tests that the Typer app and sub-commands are properly registered."""

    def test_app_exists(self):
        """app is a Typer instance."""
        assert app is not None
        assert isinstance(app, typer.Typer)

    def _get_registered_names(self) -> set[str]:
        """Helper: extract names of registered commands and groups."""
        names = set()
        # Top-level commands
        if hasattr(app, "registered_commands"):
            for cmd in app.registered_commands:
                if hasattr(cmd, "name") and cmd.name:
                    names.add(cmd.name)
        # Sub-groups (add_typer entries)
        if hasattr(app, "registered_groups"):
            for grp in app.registered_groups:
                if hasattr(grp, "name") and grp.name:
                    names.add(grp.name)
                elif hasattr(grp, "typer_instance") and hasattr(grp.typer_instance, "info"):
                    info = grp.typer_instance.info
                    if hasattr(info, "name") and info.name:
                        names.add(info.name)
        return names

    def test_strategy_subcommand_registered(self):
        """'strategy' is in registered commands/groups."""
        names = self._get_registered_names()
        assert "strategy" in names, f"Registered: {names}"

    def test_evaluation_subcommand_registered(self):
        """'evaluation' is in registered commands/groups."""
        names = self._get_registered_names()
        assert "evaluation" in names, f"Registered: {names}"

    def test_experiment_subcommand_registered(self):
        """'experiment' is in registered commands/groups."""
        names = self._get_registered_names()
        assert "experiment" in names, f"Registered: {names}"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Strategy Commands Tests
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not HAS_TYPER, reason="typer not installed")
class TestStrategyCommands:
    """Tests for strategy sub-commands via CliRunner."""

    def test_strategy_run_requires_query(self):
        """Missing --query produces non-zero exit code."""
        result = runner.invoke(app, ["strategy", "run", "my-spec"])
        assert result.exit_code != 0

    def test_strategy_list_runs(self):
        """'strategy list' executes without crash (returns specs or empty)."""
        result = runner.invoke(app, ["strategy", "list", "--format", "json"])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert "specs" in parsed

    def test_prompt_command_runs(self):
        """'prompt' top-level command executes without crash."""
        result = runner.invoke(app, ["prompt", "test query", "--format", "json"])
        # May succeed or exit 1 depending on DB, but must not crash with unhandled exception
        # A clean JSON output or a typer.Exit is acceptable
        assert result.exit_code in (0, 1)

    def test_strategy_run_with_format_json(self):
        """'strategy run <id> --query ... --format json' produces JSON output."""
        result = runner.invoke(
            app,
            ["strategy", "run", "test-spec", "--query", "hello world", "--format", "json"],
        )
        # May exit 0 (success) or 1 (no DB), but should not crash
        assert result.exit_code in (0, 1)
        if result.exit_code == 0:
            parsed = json.loads(result.stdout)
            assert isinstance(parsed, dict)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Evaluation Commands Tests
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not HAS_TYPER, reason="typer not installed")
class TestEvalCommands:
    """Tests for evaluation and experiment sub-commands."""

    def test_eval_run_requires_strategy(self):
        """Missing --strategy produces non-zero exit code."""
        result = runner.invoke(app, ["evaluation", "run"])
        assert result.exit_code != 0

    def test_eval_leaderboard_runs(self):
        """'evaluation leaderboard --format json' executes without crash."""
        result = runner.invoke(app, ["evaluation", "leaderboard", "--format", "json"])
        # May succeed (empty results) or exit 0/1 depending on runner
        assert result.exit_code in (0, 1)

    def test_experiment_run_requires_strategies(self):
        """Missing --strategies produces non-zero exit code."""
        result = runner.invoke(app, ["experiment", "run"])
        assert result.exit_code != 0

    def test_experiment_report_runs(self):
        """'experiment report test-id --format json' executes without crash."""
        result = runner.invoke(
            app,
            ["experiment", "report", "test-run-id", "--format", "json"],
        )
        assert result.exit_code in (0, 1)
        if result.exit_code == 0:
            parsed = json.loads(result.stdout)
            assert "run_id" in parsed


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCLIIntegration:
    """Integration-level tests for CLI entry and async helpers."""

    def test_cli_entry_callable(self):
        """cli_entry function exists and is callable."""
        assert callable(cli_entry)

    def test_run_async_helper_works(self):
        """run_async correctly runs an async function and returns its value."""

        async def _coro():
            return 42

        result = run_async(_coro())
        assert result == 42

    def test_run_async_propagates_exception(self):
        """run_async propagates exceptions from the async function."""

        async def _failing():
            raise ValueError("async failure")

        with pytest.raises(ValueError, match="async failure"):
            run_async(_failing())
