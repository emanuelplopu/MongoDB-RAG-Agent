"""Regression: PyMongo database objects raise on bool(); ingestion must compare with None."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")


class _StrictDb:
    """Mimics pymongo (Async)Database: truth-value testing is not allowed."""

    def __bool__(self) -> bool:  # noqa: D105
        raise NotImplementedError("Database objects do not implement truth value testing or bool()")


def test_save_ingestion_stats_does_not_bool_the_database() -> None:
    stub = type("Pipeline", (), {"db": _StrictDb()})()
    # empty stats must return early without evaluating bool(db)
    asyncio.run(ingest.DocumentIngestionPipeline._save_ingestion_stats(stub, []))


def test_router_does_not_bool_the_database() -> None:
    source = (Path(__file__).resolve().parents[1] / "routers" / "ingestion.py").read_text(encoding="utf-8")
    assert not re.search(r"\bif (not )?raw_db\b(?! is)", source)
