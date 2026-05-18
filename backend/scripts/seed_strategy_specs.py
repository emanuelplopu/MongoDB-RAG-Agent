"""Seed the MongoDB ``strategy_specs`` collection from YAML files.

This script loads every ``StrategySpec`` defined under
``backend/config/strategy_specs/`` (via
:func:`backend.agent.strategy.spec_loader.load_all_specs`) and upserts
them into the configured Mongo collection.

The script is **idempotent**: a stable SHA-256 ``content_hash`` is
computed over the canonicalized spec body (volatile fields like
``created_at`` / ``updated_at`` / ``last_rollback_at`` are excluded)
and writes only happen when the spec is new or its content changed.

It also implements the operator-promotion-preservation rule: when a
spec already exists with ``status='active'`` we never downgrade it on
re-seed. Newly inserted specs default to ``status='draft'`` unless they
match the configured "active version" (currently ``fast_evidence_v1``).

The same module can be invoked from a FastAPI lifespan handler (see
:mod:`backend.main`) or directly from the shell::

    uv run python -m backend.scripts.seed_strategy_specs
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Any, Optional

from backend.agent.strategy.models import StrategySpec
from backend.agent.strategy.spec_loader import SpecLoaderError, load_all_specs
from backend.agent.strategy.spec_store import SPEC_COLLECTION_NAME

logger = logging.getLogger(__name__)

#: Default directory scanned for YAML strategy specs.
DEFAULT_SPEC_DIR: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "strategy_specs",
)

#: Volatile spec fields excluded from the content hash.
_VOLATILE_FIELDS: frozenset[str] = frozenset(
    {"created_at", "updated_at", "last_rollback_at", "spec_hash"}
)

#: Strategy ids that should be activated on first seed (others remain draft).
DEFAULT_ACTIVE_STRATEGY_IDS: frozenset[str] = frozenset({"fast_evidence_v1"})


__all__ = [
    "DEFAULT_ACTIVE_STRATEGY_IDS",
    "DEFAULT_SPEC_DIR",
    "compute_content_hash",
    "seed_strategy_specs",
]


def compute_content_hash(spec: StrategySpec) -> str:
    """Return a deterministic SHA-256 over the non-volatile spec body.

    Args:
        spec: The :class:`StrategySpec` to hash.

    Returns:
        A 64-character hex digest stable across runs for an unchanged
        spec definition.
    """
    body = spec.model_dump(mode="json")
    for field in _VOLATILE_FIELDS:
        body.pop(field, None)
    canonical = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_doc(
    spec: StrategySpec,
    *,
    status: str,
    content_hash: str,
    version_counter: int,
    created_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build the persisted Mongo document for a seeded spec."""
    spec_data = spec.model_dump(mode="json")
    spec_data["status"] = status
    spec_data["spec_hash"] = content_hash
    now = datetime.utcnow()
    return {
        "strategy_id": spec.strategy_id,
        "version": spec.version,
        "version_counter": int(version_counter),
        "status": status,
        "spec_hash": content_hash,
        "content_hash": content_hash,
        "spec_data": spec_data,
        "created_at": created_at or now,
        "updated_at": now,
        "seeded": True,
    }


async def seed_strategy_specs(
    db: Any,
    *,
    spec_dir: Optional[str] = None,
    collection_name: str = SPEC_COLLECTION_NAME,
    active_strategy_ids: Optional[frozenset[str]] = None,
) -> dict[str, Any]:
    """Idempotently upsert YAML strategy specs into Mongo.

    Args:
        db: An async Mongo database handle (PyMongo Async or Motor).
        spec_dir: Directory to scan for ``*.yaml`` / ``*.yml`` files.
            Defaults to :data:`DEFAULT_SPEC_DIR`.
        collection_name: Mongo collection to write into. Defaults to
            :data:`backend.agent.strategy.spec_store.SPEC_COLLECTION_NAME`.
        active_strategy_ids: Strategy ids that should be created with
            ``status='active'`` on first insert. Defaults to
            :data:`DEFAULT_ACTIVE_STRATEGY_IDS`. Existing ``active``
            specs are never downgraded regardless of this value.

    Returns:
        Summary dict with keys ``loaded``, ``inserted``, ``updated``,
        ``skipped``, ``activated`` (list of strategy ids), and
        ``errors`` (list of ``{"strategy_id", "error"}`` entries).
    """
    if db is None:
        raise ValueError("seed_strategy_specs requires a non-None db handle")

    directory = spec_dir or DEFAULT_SPEC_DIR
    active_ids = active_strategy_ids or DEFAULT_ACTIVE_STRATEGY_IDS
    collection = db[collection_name]

    summary: dict[str, Any] = {
        "loaded": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "activated": [],
        "errors": [],
    }

    try:
        specs: list[StrategySpec] = load_all_specs(directory)
    except SpecLoaderError as exc:
        summary["errors"].append({"strategy_id": "*", "error": str(exc)})
        return summary

    summary["loaded"] = len(specs)
    if not specs:
        logger.info("No strategy specs found in %s; nothing to seed", directory)
        return summary

    for spec in specs:
        try:
            content_hash = compute_content_hash(spec)
            existing = await collection.find_one(
                {"strategy_id": spec.strategy_id, "version": spec.version}
            )

            if existing is None:
                desired_status = (
                    "active" if spec.strategy_id in active_ids else "draft"
                )
                doc = _build_doc(
                    spec,
                    status=desired_status,
                    content_hash=content_hash,
                    version_counter=1,
                )
                await collection.insert_one(doc)
                summary["inserted"] += 1
                if desired_status == "active":
                    summary["activated"].append(spec.strategy_id)
                logger.info(
                    "Seeded strategy spec %s@%s (status=%s)",
                    spec.strategy_id,
                    spec.version,
                    desired_status,
                )
                continue

            existing_hash = existing.get("content_hash") or existing.get(
                "spec_hash"
            )
            existing_status = existing.get("status", "draft")

            if existing_hash == content_hash:
                summary["skipped"] += 1
                logger.debug(
                    "Skipping spec %s@%s (content unchanged)",
                    spec.strategy_id,
                    spec.version,
                )
                continue

            # Content changed → update but preserve operator-active status.
            preserved_status = (
                "active"
                if existing_status == "active"
                else (
                    "active"
                    if spec.strategy_id in active_ids
                    and existing_status not in {"deprecated", "archived"}
                    else "draft"
                )
            )
            doc = _build_doc(
                spec,
                status=preserved_status,
                content_hash=content_hash,
                version_counter=int(existing.get("version_counter", 1)),
                created_at=existing.get("created_at"),
            )
            await collection.replace_one(
                {"_id": existing["_id"]},
                doc,
            )
            summary["updated"] += 1
            logger.info(
                "Updated seeded spec %s@%s (hash changed; status=%s)",
                spec.strategy_id,
                spec.version,
                preserved_status,
            )
        except Exception as exc:  # noqa: BLE001 — collect, don't crash run
            summary["errors"].append(
                {"strategy_id": spec.strategy_id, "error": str(exc)}
            )
            logger.exception(
                "Failed to seed spec %s@%s", spec.strategy_id, spec.version
            )

    logger.info(
        "Strategy spec seed summary: loaded=%d inserted=%d updated=%d "
        "skipped=%d activated=%s errors=%d",
        summary["loaded"],
        summary["inserted"],
        summary["updated"],
        summary["skipped"],
        summary["activated"],
        len(summary["errors"]),
    )
    return summary


async def _main() -> None:
    """Standalone runner: connect using env vars and seed."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    mongodb_uri = os.environ.get("MONGODB_URI", "mongodb://localhost:11017")
    database_name = os.environ.get("MONGODB_DATABASE", "rag_db")
    spec_dir = os.environ.get("STRATEGY_SPEC_DIR", DEFAULT_SPEC_DIR)

    try:
        from pymongo import AsyncMongoClient  # type: ignore[attr-defined]

        client: Any = AsyncMongoClient(mongodb_uri)
    except ImportError:
        # Older PyMongo without async client: fall back to Motor.
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(mongodb_uri)

    db = client[database_name]
    try:
        summary = await seed_strategy_specs(db, spec_dir=spec_dir)
        print("Strategy spec seed summary:")
        for key, value in summary.items():
            print(f"  {key}: {value}")
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                await result


if __name__ == "__main__":
    asyncio.run(_main())
