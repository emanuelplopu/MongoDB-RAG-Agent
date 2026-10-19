"""Every ``quellexctl --tenant`` option falls back to ``TENANT_ID``, like the backends do.

Without it a CLI run inside a Quellex deployment silently acted as RecallHub (the hardcoded
default) unless the operator remembered ``--tenant quellex``.
"""

from __future__ import annotations

import importlib
from typing import Any, Iterator

import pytest

typer = pytest.importorskip("typer")

_COMMAND_MODULES = (
    "backend.cli.strategy_commands",
    "backend.cli.eval_commands",
    "backend.cli.phase6_commands",
    "backend.cli.bench_commands",
    "backend.cli.stress_commands",
    "backend.cli.synth_commands",
    "backend.cli.ingest_commands",
    "backend.cli.telemetry_commands",
)


def _walk(command: Any, path: str = "quellexctl") -> Iterator[tuple[str, Any]]:
    # Duck-typed: newer typer versions ship their own click classes
    yield path, command
    for name, sub in (getattr(command, "commands", None) or {}).items():
        yield from _walk(sub, f"{path} {name}")


def test_tenant_options_read_tenant_id():
    from backend.cli import main

    for module in _COMMAND_MODULES:
        importlib.import_module(module)
    root = typer.main.get_command(main.app)

    tenant_options = [
        (path, param)
        for path, command in _walk(root)
        for param in command.params
        if "--tenant" in (getattr(param, "opts", None) or []) and param.default == "recallhub"
    ]
    assert tenant_options, "expected tenant-scoped commands"
    missing = [path for path, param in tenant_options if param.envvar != "TENANT_ID"]
    assert missing == []
