"""Registers the synthetic corpus generator as ``quellexctl synth``."""
from __future__ import annotations

from backend.cli.main import HAS_TYPER, app

if HAS_TYPER:
    from backend.evaluation.synthetic_corpus.cli import app as synth_app

    app.add_typer(synth_app, name="synth", help="Synthetic Austrian law-firm corpus generator.")
