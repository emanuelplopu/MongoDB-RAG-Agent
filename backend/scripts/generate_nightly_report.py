"""Standalone runner for the Strategy OS nightly report generator.

Wires :class:`backend.agent.strategy.report_generator.NightlyReportGenerator`
to a real MongoDB connection using the same env-var conventions as the
existing :mod:`backend.scripts.seed_strategy_specs` runner so the script
can be invoked from cron, the offline installer, or by hand::

    uv run python -m backend.scripts.generate_nightly_report

Repo rule #3: this script uses ``pymongo.AsyncMongoClient`` only — Motor
is **not** introduced. A best-effort fallback to Motor is preserved for
environments running an older PyMongo without async client support, in
parity with :mod:`backend.scripts.seed_strategy_specs`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Optional

from backend.agent.strategy.report_generator import NightlyReportGenerator

logger = logging.getLogger(__name__)


def _build_client(mongodb_uri: str) -> Any:
    """Construct an async Mongo client, preferring PyMongo Async."""
    try:
        from pymongo import AsyncMongoClient  # type: ignore[attr-defined]

        return AsyncMongoClient(mongodb_uri)
    except ImportError:
        # Older PyMongo without async client: fall back to Motor for parity
        # with backend.scripts.seed_strategy_specs.
        from motor.motor_asyncio import AsyncIOMotorClient

        return AsyncIOMotorClient(mongodb_uri)


async def _run(
    *,
    mongodb_uri: str,
    database_name: str,
    output_dir: Optional[Path],
    lookback_hours: int,
    regression_threshold: float,
) -> tuple[Path, Optional[Path]]:
    """Execute the report generator and return ``(markdown_path, json_path)``."""
    client = _build_client(mongodb_uri)
    db = client[database_name]
    try:
        generator = NightlyReportGenerator(
            db,
            lookback_hours=lookback_hours,
            regression_threshold=regression_threshold,
            output_dir=output_dir,
        )
        summary = await generator.generate()
        markdown_path = (
            Path(summary.markdown_path) if summary.markdown_path else None
        )
        assert markdown_path is not None  # generator always sets this on success
        json_path = Path(summary.json_path) if summary.json_path else None
        return markdown_path, json_path
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                await result


def main() -> int:
    """CLI entrypoint. Returns the process exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    mongodb_uri = os.environ.get(
        "MONGODB_URI", "mongodb://localhost:11017"
    )
    database_name = os.environ.get("MONGODB_DATABASE", "rag_db")
    output_dir_env = os.environ.get("STRATEGY_NIGHTLY_REPORT_DIR")
    output_dir = Path(output_dir_env) if output_dir_env else None
    lookback_hours = int(os.environ.get("STRATEGY_NIGHTLY_REPORT_LOOKBACK_HOURS", "24"))
    regression_threshold = float(
        os.environ.get("STRATEGY_NIGHTLY_REPORT_REGRESSION_THRESHOLD", "0.05")
    )

    try:
        markdown_path, json_path = asyncio.run(
            _run(
                mongodb_uri=mongodb_uri,
                database_name=database_name,
                output_dir=output_dir,
                lookback_hours=lookback_hours,
                regression_threshold=regression_threshold,
            )
        )
    except Exception as exc:  # noqa: BLE001 - top-level CLI guard
        logger.exception("Nightly report generation failed: %s", exc)
        print(f"ERROR: nightly report generation failed: {exc}")
        return 1

    print(f"Markdown: {markdown_path}")
    print(f"JSON:     {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
