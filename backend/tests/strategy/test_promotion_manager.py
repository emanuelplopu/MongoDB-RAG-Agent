"""Tests for ``backend.agent.strategy.promotion_manager.PromotionManager``.

Covers the lifecycle state machine (promote / deprecate / archive),
capability-scoped demotion semantics, the rollback cooldown window, and the
audit log invariants. Uses an isolated in-memory store per test so tests do
not contaminate each other or the router-level singleton.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import pytest

from backend.agent.strategy.models import (
    StrategyGraph,
    StrategyNode,
    StrategySpec,
)
from backend.agent.strategy.promotion_manager import (
    InvalidStateTransitionError,
    PromotionManager,
    ROLLBACK_COOLDOWN_DAYS,
    RollbackCooldownError,
)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _make_spec(
    strategy_id: str,
    version: str,
    *,
    capability_id: str | None = "cap-billing",
    status: str = "draft",
) -> StrategySpec:
    """Build a minimal valid StrategySpec for tests."""
    graph = StrategyGraph(
        nodes=[StrategyNode(node_id="n1", node_type="retrieve")],
        edges=[],
        entry_node="n1",
        terminal_nodes=["n1"],
    )
    return StrategySpec(
        strategy_id=strategy_id,
        version=version,
        capability_id=capability_id,
        status=status,  # type: ignore[arg-type]
        graph=graph,
    )


def _add_snapshot(
    store: dict[str, list[dict[str, Any]]],
    spec: StrategySpec,
    *,
    version_counter: int = 1,
) -> dict[str, Any]:
    """Insert a snapshot directly into the store (bypassing the CRUD router)."""
    spec_data = json.loads(spec.model_dump_json())
    spec_data["spec_hash"] = "test-hash"
    snap = {
        "spec_data": spec_data,
        "version": spec.version,
        "version_counter": version_counter,
        "status": spec.status,
        "created_at": datetime.utcnow(),
        "spec_hash": "test-hash",
    }
    store.setdefault(spec.strategy_id, []).append(snap)
    return snap


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def store() -> dict[str, list[dict[str, Any]]]:
    """Fresh in-memory snapshot store, isolated per test."""
    return {}


@pytest.fixture
def manager(store: dict[str, list[dict[str, Any]]]) -> PromotionManager:
    """Promotion manager bound to the per-test store."""
    return PromotionManager(store=store)


# ══════════════════════════════════════════════════════════════════════════════
# Promote
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_promote_demotes_existing_active_for_capability(
    store: dict[str, list[dict[str, Any]]], manager: PromotionManager
) -> None:
    incumbent = _make_spec("strategy-a", "1.0.0", status="active")
    challenger = _make_spec("strategy-b", "1.0.0", status="draft")
    incumbent_snap = _add_snapshot(store, incumbent)
    challenger_snap = _add_snapshot(store, challenger)

    spec = await manager.promote(
        "strategy-b", "1.0.0", actor="alice", reason="ship v2 of capability"
    )

    assert spec.strategy_id == "strategy-b"
    assert spec.status == "active"
    assert challenger_snap["status"] == "active"
    assert incumbent_snap["status"] == "deprecated"
    assert incumbent_snap["spec_data"]["status"] == "deprecated"


@pytest.mark.asyncio
async def test_promote_rejects_archived(
    store: dict[str, list[dict[str, Any]]], manager: PromotionManager
) -> None:
    archived = _make_spec("strategy-a", "1.0.0", status="archived")
    _add_snapshot(store, archived)

    with pytest.raises(InvalidStateTransitionError, match="expected status 'draft'"):
        await manager.promote(
            "strategy-a", "1.0.0", actor="alice", reason="resurrect"
        )


@pytest.mark.asyncio
async def test_promote_rejects_already_active(
    store: dict[str, list[dict[str, Any]]], manager: PromotionManager
) -> None:
    active = _make_spec("strategy-a", "1.0.0", status="active")
    _add_snapshot(store, active)

    with pytest.raises(InvalidStateTransitionError):
        await manager.promote(
            "strategy-a", "1.0.0", actor="alice", reason="redundant"
        )


@pytest.mark.asyncio
async def test_promote_unknown_spec_raises_key_error(
    manager: PromotionManager,
) -> None:
    with pytest.raises(KeyError):
        await manager.promote(
            "missing", "1.0.0", actor="alice", reason="nope"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Deprecate / archive
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_deprecate_active_succeeds(
    store: dict[str, list[dict[str, Any]]], manager: PromotionManager
) -> None:
    snap = _add_snapshot(store, _make_spec("strategy-a", "1.0.0", status="active"))

    spec = await manager.deprecate(
        "strategy-a", "1.0.0", actor="bob", reason="superseded"
    )

    assert spec.status == "deprecated"
    assert snap["status"] == "deprecated"


@pytest.mark.asyncio
async def test_archive_active_rejected(
    store: dict[str, list[dict[str, Any]]], manager: PromotionManager
) -> None:
    _add_snapshot(store, _make_spec("strategy-a", "1.0.0", status="active"))

    with pytest.raises(
        InvalidStateTransitionError, match="expected status 'deprecated'"
    ):
        await manager.archive(
            "strategy-a", "1.0.0", actor="bob", reason="cleanup"
        )


@pytest.mark.asyncio
async def test_archive_deprecated_succeeds(
    store: dict[str, list[dict[str, Any]]], manager: PromotionManager
) -> None:
    snap = _add_snapshot(
        store, _make_spec("strategy-a", "1.0.0", status="deprecated")
    )

    spec = await manager.archive(
        "strategy-a", "1.0.0", actor="bob", reason="end of life"
    )

    assert spec.status == "archived"
    assert snap["status"] == "archived"


# ══════════════════════════════════════════════════════════════════════════════
# Rollback + cooldown
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rollback_within_cooldown_raises(
    store: dict[str, list[dict[str, Any]]], manager: PromotionManager
) -> None:
    _add_snapshot(
        store,
        _make_spec("strategy-a", "1.0.0", status="deprecated"),
        version_counter=1,
    )
    _add_snapshot(
        store,
        _make_spec("strategy-a", "2.0.0", status="active"),
        version_counter=2,
    )

    first = await manager.rollback(
        "strategy-a", "1.0.0", actor="carol", reason="regression"
    )
    assert first.version == "1.0.0"
    assert first.status == "active"

    # Re-deprecate v1 manually to simulate another active/deprecated swap.
    snap_v1 = store["strategy-a"][0]
    snap_v2 = store["strategy-a"][1]
    snap_v1["status"] = "deprecated"
    snap_v1["spec_data"]["status"] = "deprecated"
    snap_v2["status"] = "active"
    snap_v2["spec_data"]["status"] = "active"

    with pytest.raises(RollbackCooldownError) as exc_info:
        await manager.rollback(
            "strategy-a", "1.0.0", actor="carol", reason="thrashing"
        )

    assert exc_info.value.retry_after_seconds > 0


@pytest.mark.asyncio
async def test_rollback_after_cooldown_succeeds(
    store: dict[str, list[dict[str, Any]]], manager: PromotionManager
) -> None:
    _add_snapshot(
        store,
        _make_spec("strategy-a", "1.0.0", status="deprecated"),
        version_counter=1,
    )
    _add_snapshot(
        store,
        _make_spec("strategy-a", "2.0.0", status="active"),
        version_counter=2,
    )

    # Pretend a rollback happened more than the cooldown window ago.
    capability_id = "cap-billing"
    manager._last_rollback_at[capability_id] = datetime.utcnow() - timedelta(
        days=ROLLBACK_COOLDOWN_DAYS + 1
    )

    spec = await manager.rollback(
        "strategy-a", "1.0.0", actor="dave", reason="planned downgrade"
    )

    assert spec.version == "1.0.0"
    assert spec.status == "active"
    assert store["strategy-a"][1]["status"] == "deprecated"
    assert "last_rollback_at" in store["strategy-a"][0]["spec_data"]


@pytest.mark.asyncio
async def test_rollback_target_must_exist(manager: PromotionManager) -> None:
    with pytest.raises(KeyError):
        await manager.rollback(
            "strategy-a", "9.9.9", actor="dave", reason="nope"
        )


@pytest.mark.asyncio
async def test_rollback_rejects_draft_target(
    store: dict[str, list[dict[str, Any]]], manager: PromotionManager
) -> None:
    _add_snapshot(store, _make_spec("strategy-a", "1.0.0", status="draft"))

    with pytest.raises(InvalidStateTransitionError):
        await manager.rollback(
            "strategy-a", "1.0.0", actor="dave", reason="invalid"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Audit log
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_audit_log_captures_actor_and_reason(
    store: dict[str, list[dict[str, Any]]], manager: PromotionManager
) -> None:
    _add_snapshot(store, _make_spec("strategy-a", "1.0.0", status="draft"))

    await manager.promote(
        "strategy-a", "1.0.0", actor="alice", reason="initial launch"
    )
    await manager.deprecate(
        "strategy-a", "1.0.0", actor="bob", reason="replaced by v2"
    )
    await manager.archive(
        "strategy-a", "1.0.0", actor="carol", reason="end of life"
    )

    log = manager.audit_log
    assert len(log) == 3

    promote_entry, deprecate_entry, archive_entry = log
    assert promote_entry["actor"] == "alice"
    assert promote_entry["from_state"] == "draft"
    assert promote_entry["to_state"] == "active"
    assert promote_entry["reason"] == "initial launch"
    assert promote_entry["spec_id"] == "strategy-a"
    assert promote_entry["version"] == "1.0.0"
    assert isinstance(promote_entry["timestamp"], datetime)

    assert deprecate_entry["actor"] == "bob"
    assert deprecate_entry["to_state"] == "deprecated"
    assert deprecate_entry["reason"] == "replaced by v2"

    assert archive_entry["actor"] == "carol"
    assert archive_entry["to_state"] == "archived"
    assert archive_entry["reason"] == "end of life"


@pytest.mark.asyncio
async def test_audit_log_records_auto_demotion(
    store: dict[str, list[dict[str, Any]]], manager: PromotionManager
) -> None:
    _add_snapshot(store, _make_spec("strategy-a", "1.0.0", status="active"))
    _add_snapshot(store, _make_spec("strategy-b", "1.0.0", status="draft"))

    await manager.promote(
        "strategy-b", "1.0.0", actor="alice", reason="cutover"
    )

    log = manager.audit_log
    auto_demotions = [
        e for e in log if e["spec_id"] == "strategy-a" and e["to_state"] == "deprecated"
    ]
    assert len(auto_demotions) == 1
    assert "Auto-deprecated" in auto_demotions[0]["reason"]
    assert auto_demotions[0]["actor"] == "alice"
