"""Tests for :mod:`backend.scripts.seed_strategy_specs`.

Exercises idempotency, content-change updates, the operator-active
preservation rule, and the shape of the returned summary dict.

These tests reuse the in-process ``_FakeAsyncDB`` from
``test_spec_store`` to avoid pulling in a real MongoDB instance.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
import yaml

from backend.scripts.seed_strategy_specs import (
    DEFAULT_ACTIVE_STRATEGY_IDS,
    DEFAULT_SPEC_DIR,
    seed_strategy_specs,
)
from backend.tests.strategy.test_spec_store import _FakeAsyncDB


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _write_spec_yaml(
    directory: str, filename: str, payload: dict[str, Any]
) -> str:
    path = os.path.join(directory, filename)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False)
    return path


def _minimal_spec(
    strategy_id: str = "fast_evidence_v1",
    version: str = "1.0.0",
    status: str = "active",
    description: str = "seed-test spec",
) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "version": version,
        "status": status,
        "description": description,
        "graph": {
            "entry_node": "n1",
            "terminal_nodes": ["n1"],
            "nodes": [{"node_id": "n1", "node_type": "retrieve", "config": {}}],
            "edges": [],
        },
        "budgets": {
            "latency_target_ms": 5000,
            "latency_hard_limit_ms": 15000,
        },
    }


@pytest.fixture
def fake_db() -> _FakeAsyncDB:
    return _FakeAsyncDB()


@pytest.fixture
def spec_dir(tmp_path) -> str:
    d = tmp_path / "specs"
    d.mkdir()
    return str(d)


# ══════════════════════════════════════════════════════════════════════════════
# Idempotency
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_seed_inserts_then_skips_on_second_run(
    fake_db: _FakeAsyncDB, spec_dir: str
) -> None:
    _write_spec_yaml(spec_dir, "fast_evidence_v1.yaml", _minimal_spec())
    _write_spec_yaml(
        spec_dir,
        "other_v1.yaml",
        _minimal_spec(strategy_id="other_v1", status="draft"),
    )

    first = await seed_strategy_specs(fake_db, spec_dir=spec_dir)
    assert first["loaded"] == 2
    assert first["inserted"] == 2
    assert first["updated"] == 0
    assert first["skipped"] == 0
    assert "fast_evidence_v1" in first["activated"]
    assert "other_v1" not in first["activated"]
    assert first["errors"] == []

    second = await seed_strategy_specs(fake_db, spec_dir=spec_dir)
    assert second["loaded"] == 2
    assert second["inserted"] == 0
    assert second["updated"] == 0
    assert second["skipped"] == 2
    assert second["activated"] == []
    assert second["errors"] == []


@pytest.mark.asyncio
async def test_seed_updates_on_content_change(
    fake_db: _FakeAsyncDB, spec_dir: str
) -> None:
    _write_spec_yaml(spec_dir, "fast_evidence_v1.yaml", _minimal_spec())
    await seed_strategy_specs(fake_db, spec_dir=spec_dir)

    # Modify the spec body.
    modified = _minimal_spec(description="updated description")
    _write_spec_yaml(spec_dir, "fast_evidence_v1.yaml", modified)

    second = await seed_strategy_specs(fake_db, spec_dir=spec_dir)
    assert second["updated"] == 1
    assert second["skipped"] == 0
    assert second["inserted"] == 0
    assert second["errors"] == []


# ══════════════════════════════════════════════════════════════════════════════
# Active-status preservation
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_seed_does_not_downgrade_existing_active(
    fake_db: _FakeAsyncDB, spec_dir: str
) -> None:
    # A strategy id that is NOT in DEFAULT_ACTIVE_STRATEGY_IDS but an
    # operator has previously promoted to 'active' in Mongo.
    assert "deep_orchestrated_v1" not in DEFAULT_ACTIVE_STRATEGY_IDS
    _write_spec_yaml(
        spec_dir,
        "deep_orchestrated_v1.yaml",
        _minimal_spec(strategy_id="deep_orchestrated_v1", status="draft"),
    )

    # First seed → goes in as draft (because it's not in the active set).
    await seed_strategy_specs(fake_db, spec_dir=spec_dir)
    collection = fake_db["strategy_specs"]
    stored = await collection.find_one({"strategy_id": "deep_orchestrated_v1"})
    assert stored is not None

    # Simulate an operator promoting it to 'active'.
    stored["status"] = "active"
    stored["spec_data"]["status"] = "active"
    await collection.replace_one(
        {"_id": stored["_id"]}, stored
    )

    # Modify the YAML so a content-change update is triggered.
    modified = _minimal_spec(
        strategy_id="deep_orchestrated_v1",
        status="draft",
        description="new body",
    )
    _write_spec_yaml(spec_dir, "deep_orchestrated_v1.yaml", modified)

    summary = await seed_strategy_specs(fake_db, spec_dir=spec_dir)
    assert summary["updated"] == 1

    after = await collection.find_one({"strategy_id": "deep_orchestrated_v1"})
    assert after is not None
    assert after["status"] == "active"
    assert after["spec_data"]["status"] == "active"


# ══════════════════════════════════════════════════════════════════════════════
# Summary shape
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_summary_shape_contains_all_keys(
    fake_db: _FakeAsyncDB, spec_dir: str
) -> None:
    _write_spec_yaml(spec_dir, "fast_evidence_v1.yaml", _minimal_spec())
    summary = await seed_strategy_specs(fake_db, spec_dir=spec_dir)

    assert set(summary.keys()) == {
        "loaded",
        "inserted",
        "updated",
        "skipped",
        "activated",
        "errors",
    }
    assert isinstance(summary["activated"], list)
    assert isinstance(summary["errors"], list)


@pytest.mark.asyncio
async def test_seed_handles_empty_dir(
    fake_db: _FakeAsyncDB, spec_dir: str
) -> None:
    summary = await seed_strategy_specs(fake_db, spec_dir=spec_dir)
    assert summary["loaded"] == 0
    assert summary["inserted"] == 0
    assert summary["errors"] == []


@pytest.mark.asyncio
async def test_seed_rejects_none_db() -> None:
    with pytest.raises(ValueError):
        await seed_strategy_specs(None)


@pytest.mark.asyncio
async def test_seed_default_dir_contains_bundled_specs() -> None:
    """The shipped YAMLs under backend/config/strategy_specs are loadable."""
    assert os.path.isdir(DEFAULT_SPEC_DIR), DEFAULT_SPEC_DIR
    fake_db = _FakeAsyncDB()
    summary = await seed_strategy_specs(fake_db)
    assert summary["loaded"] >= 2
    assert summary["inserted"] == summary["loaded"]
    assert "fast_evidence_v1" in summary["activated"]
