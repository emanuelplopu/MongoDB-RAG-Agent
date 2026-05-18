"""Focused integration tests for Phase 5 coverage gaps.

Covers:
1. Checkpoint save/resume lifecycle
2. End-to-end spec lifecycle (load YAML → seed → list → promote → selector → rollback)
3. Legacy fallback when empty spec store
4. Candidate generator → spec store integration
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.agent.strategy.candidate_generator import CandidateGenerator
from backend.agent.strategy.checkpoint_manager import CheckpointManager
from backend.agent.strategy.models import (
    StrategyGraph,
    StrategyNode,
    StrategyRunState,
    StrategySpec,
)
from backend.agent.strategy.promotion_manager import PromotionManager
from backend.agent.strategy.spec_loader import load_all_specs
from backend.agent.strategy.spec_selector import (
    NoActiveSpecError,
    StrategySpecSelector,
)
from backend.agent.strategy.spec_store import InMemorySpecStore, MongoSpecStore
from backend.tests.strategy._fakes import _FakeAsyncDB


# ══════════════════════════════════════════════════════════════════════════════
# Checkpoint Resume Tests
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_checkpoint_save_and_restore() -> None:
    """Test basic checkpoint save → load → restore cycle."""
    db = _FakeAsyncDB()
    manager = CheckpointManager(db=db, ttl_minutes=60)

    # Create a state and save a checkpoint
    state = StrategyRunState(
        run_id="run-123",
        trace_id="trace-456",
        capability_id="qa",
        tenant_id="recallhub",
    )
    completed_level = 2

    success = await manager.save_checkpoint(state, completed_level)
    assert success is True

    # Load the checkpoint back
    checkpoint_doc = await manager.load_checkpoint("run-123")
    assert checkpoint_doc is not None
    assert checkpoint_doc["run_id"] == "run-123"
    assert checkpoint_doc["completed_level"] == 2
    assert checkpoint_doc["state_snapshot"]["run_id"] == "run-123"

    # Restore the state
    restored_state = manager.restore_state_from_checkpoint(checkpoint_doc)
    assert restored_state.run_id == "run-123"
    assert restored_state.trace_id == "trace-456"


@pytest.mark.asyncio
async def test_checkpoint_delete_after_success() -> None:
    """Test that checkpoint is deleted after run completes."""
    db = _FakeAsyncDB()
    manager = CheckpointManager(db=db)

    state = StrategyRunState(
        run_id="run-789",
        trace_id="trace-999",
    )
    await manager.save_checkpoint(state, 1)

    # Verify it exists
    doc = await manager.load_checkpoint("run-789")
    assert doc is not None

    # Delete it
    deleted = await manager.delete_checkpoint("run-789")
    assert deleted is True

    # Verify it's gone
    doc_after = await manager.load_checkpoint("run-789")
    assert doc_after is None


@pytest.mark.asyncio
async def test_checkpoint_ttl_expiry() -> None:
    """Test that expired checkpoints are filtered from list_pending_checkpoints."""
    db = _FakeAsyncDB()
    manager = CheckpointManager(db=db, ttl_minutes=60)

    state = StrategyRunState(run_id="run-expired", trace_id="trace-x")
    await manager.save_checkpoint(state, 1)

    # Manually expire the checkpoint in the collection
    coll = db["strategy_run_checkpoints"]
    stored = await coll.find_one({"run_id": "run-expired"})
    assert stored is not None

    stored["expires_at"] = datetime.utcnow() - timedelta(minutes=1)
    await coll.replace_one({"run_id": "run-expired"}, stored)

    # list_pending should not return it
    pending = await manager.list_pending_checkpoints()
    assert len(pending) == 0


# ══════════════════════════════════════════════════════════════════════════════
# End-to-End Spec Lifecycle (Issue #2 concern)
# ══════════════════════════════════════════════════════════════════════════════


def _make_minimal_spec(
    strategy_id: str = "test-spec-1",
    version: str = "1.0.0",
    status: str = "draft",
    capability_id: str = "qa",
    tenant_scope: str = "default",
) -> StrategySpec:
    """Create a minimal valid spec for testing."""
    graph = StrategyGraph(
        nodes=[StrategyNode(node_id="n1", node_type="retrieve")],
        edges=[],
        entry_node="n1",
        terminal_nodes=["n1"],
    )
    return StrategySpec(
        strategy_id=strategy_id,
        version=version,
        status=status,  # type: ignore[arg-type]
        capability_id=capability_id,
        tenant_scope=tenant_scope,
        graph=graph,
    )


@pytest.mark.asyncio
async def test_spec_lifecycle_load_seed_promote_select() -> None:
    """End-to-end: YAML load → seed → list → promote → selector returns it → rollback."""
    db = _FakeAsyncDB()
    store = MongoSpecStore(db)
    promotion_manager = PromotionManager()

    # 1. Create an initial draft spec
    spec_v1 = _make_minimal_spec(
        strategy_id="lifecycle-spec",
        version="1.0.0",
        status="draft",
        capability_id="qa",
    )
    v1_dict = spec_v1.model_dump(mode="json")
    v1_snapshot = {
        "spec_data": v1_dict,
        "version": spec_v1.version,
        "version_counter": 1,
        "status": "draft",
        "created_at": datetime.utcnow(),
        "spec_hash": f"hash-{spec_v1.strategy_id}-v1",
    }
    await store.upsert(v1_snapshot, expected_version=None)

    # 2. Verify it's listable but not selectable (draft)
    drafts = await store.list(capability_id="qa", status="draft")
    assert len(drafts) == 1
    assert drafts[0]["spec_data"]["strategy_id"] == "lifecycle-spec"

    # 3. Promote to active (simulates operator action or seeding)
    in_memory_store = {
        "lifecycle-spec": [v1_snapshot],
    }
    promotion_manager = PromotionManager(store=in_memory_store)
    promoted = await promotion_manager.promote(
        "lifecycle-spec", "1.0.0", actor="test", reason="lifecycle test"
    )
    assert promoted.status == "active"

    # 4. Sync back to store
    v1_snapshot["status"] = "active"
    v1_snapshot["spec_data"]["status"] = "active"
    await store.upsert(v1_snapshot, expected_version=1)

    # 5. Selector finds the active spec
    selector = StrategySpecSelector(store=store, legacy_adapter=None)
    selected = await selector.select(
        capability_id="qa",
        tenant_id="default",
    )
    assert selected is not None
    assert selected.strategy_id == "lifecycle-spec"
    assert selected.status == "active"

    # 6. Create v2 and promote it (demotes v1)
    spec_v2 = _make_minimal_spec(
        strategy_id="lifecycle-spec-v2",
        version="2.0.0",
        status="draft",
        capability_id="qa",
    )
    v2_dict = spec_v2.model_dump(mode="json")
    v2_snapshot = {
        "spec_data": v2_dict,
        "version": spec_v2.version,
        "version_counter": 1,
        "status": "draft",
        "created_at": datetime.utcnow(),
        "spec_hash": f"hash-{spec_v2.strategy_id}-v2",
    }
    await store.upsert(v2_snapshot, expected_version=None)

    # Promote v2
    in_memory_store["lifecycle-spec-v2"] = [v2_snapshot]
    promotion_manager = PromotionManager(store=in_memory_store)
    await promotion_manager.promote(
        "lifecycle-spec-v2", "2.0.0", actor="test", reason="rollout v2"
    )
    v2_snapshot["status"] = "active"
    v2_snapshot["spec_data"]["status"] = "active"
    await store.upsert(v2_snapshot, expected_version=None)

    # Selector now returns v2 (newer)
    selector.clear_cache()
    selected_v2 = await selector.select(capability_id="qa", tenant_id="default")
    assert selected_v2 is not None
    assert selected_v2.strategy_id == "lifecycle-spec-v2"

    # 7. Rollback to v1: first deprecate v2 in memory, then promote v1
    v2_snapshot["status"] = "deprecated"
    v2_snapshot["spec_data"]["status"] = "deprecated"
    v1_snapshot["status"] = "deprecated"  # Will be promoted back
    v1_snapshot["spec_data"]["status"] = "deprecated"
    in_memory_store["lifecycle-spec"] = [v1_snapshot]
    in_memory_store["lifecycle-spec-v2"] = [v2_snapshot]

    promotion_manager = PromotionManager(store=in_memory_store)
    # Clear cooldown for testing
    promotion_manager._last_rollback_at = {}
    rolled_back = await promotion_manager.rollback(
        "lifecycle-spec", "1.0.0", actor="ops", reason="v2 regression"
    )
    assert rolled_back.status == "active"
    v1_snapshot["status"] = "active"
    v1_snapshot["spec_data"]["status"] = "active"
    await store.upsert(v1_snapshot, expected_version=None)

    # Selector returns v1 again
    selector.clear_cache()
    selected_rolled_back = await selector.select(
        capability_id="qa", tenant_id="default"
    )
    assert selected_rolled_back is not None
    assert selected_rolled_back.strategy_id == "lifecycle-spec"


# ══════════════════════════════════════════════════════════════════════════════
# Legacy Fallback (Issue #3)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_legacy_fallback_when_no_active_spec() -> None:
    """When spec store is empty and legacy adapter also returns None, selector
    raises NoActiveSpecError.

    This simulates the coordinator's special case (line 595) where if neither
    the store nor the legacy adapter provide a spec, legacy_orchestrator_v1 is used.
    """
    empty_store = InMemorySpecStore()
    # A legacy adapter that explicitly returns None
    legacy_adapter = MagicMock()
    legacy_adapter.get_spec.return_value = None

    selector = StrategySpecSelector(
        store=empty_store,
        legacy_adapter=legacy_adapter,
        fallback_ttl_seconds=60,
    )

    # Should raise NoActiveSpecError when both store and adapter return nothing
    with pytest.raises(NoActiveSpecError) as exc_info:
        await selector.select(
            capability_id="qa",
            tenant_id="recallhub",
        )

    assert exc_info.value.capability_id == "qa"
    assert exc_info.value.tenant_id == "recallhub"
    legacy_adapter.get_spec.assert_called_once()


@pytest.mark.asyncio
async def test_legacy_adapter_none_returns_none() -> None:
    """When legacy_adapter=None is explicitly set, selector returns None
    instead of raising (preserved historical behavior)."""
    empty_store = InMemorySpecStore()
    selector = StrategySpecSelector(
        store=empty_store,
        legacy_adapter=None,
    )

    result = await selector.select(capability_id="qa", tenant_id="recallhub")
    assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# Candidate Generator → Spec Store (Issue #4 adjacent concern)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_candidate_generator_output_storable() -> None:
    """Generated candidates can be persisted to spec store as drafts.

    This ensures the loop: candidate_generator.generate() → store.upsert works.
    """
    db = _FakeAsyncDB()
    store = MongoSpecStore(db)

    # Create a source spec with nodes matching DEFAULT_AXES targets
    graph = StrategyGraph(
        nodes=[
            StrategyNode(node_id="n_retrieve", node_type="retrieve", config={"top_k": 10}),
            StrategyNode(node_id="n_evidence", node_type="evidence_cards", config={"max_cards": 5}),
        ],
        edges=[{"from_node": "n_retrieve", "to_node": "n_evidence"}],
        entry_node="n_retrieve",
        terminal_nodes=["n_evidence"],
    )
    source_spec = StrategySpec(
        strategy_id="base-spec",
        version="1.0.0",
        status="active",
        capability_id="qa",
        graph=graph,
    )

    # Generate candidates from the source spec
    generator = CandidateGenerator(base_spec=source_spec, max_candidates=6)
    candidates = generator.generate()
    assert len(candidates) > 0

    # Each candidate should be a valid StrategySpec
    for candidate in candidates:
        assert isinstance(candidate, StrategySpec)
        assert candidate.graph is not None

        # Upsert as draft into the store
        candidate_dict = candidate.model_dump(mode="json")
        candidate_snapshot = {
            "spec_data": candidate_dict,
            "version": candidate.version,
            "version_counter": 1,
            "status": "draft",
            "created_at": datetime.utcnow(),
            "spec_hash": f"hash-{candidate.strategy_id}",
        }
        result = await store.upsert(candidate_snapshot, expected_version=None)
        assert result is not None

    # Verify candidates are in the store
    all_specs = await store.list(status="draft")
    assert len(all_specs) == len(candidates)


# ══════════════════════════════════════════════════════════════════════════════
# Run-trace persistence → nightly report (Task 68 closes Task 62 placeholder)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_nightly_report_consumes_persisted_traces(tmp_path) -> None:
    """Seeding three real traces into ``strategy_runs`` makes the nightly
    report show non-zero counts and latency percentiles — closing the
    "empty placeholder" caveat from Task 62.
    """
    from backend.agent.strategy.run_trace_store import (
        MongoRunTraceStore,
        RunTraceDoc,
    )
    from backend.agent.strategy.report_generator import NightlyReportGenerator

    db = _FakeAsyncDB()
    store = MongoRunTraceStore(db)
    await store.ensure_indexes()

    end = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    base = end - timedelta(hours=12)

    docs = [
        RunTraceDoc(
            trace_id="t1",
            run_id="r1",
            strategy_id="alpha",
            capability_id="qa",
            tenant_id="recallhub",
            status="success",
            started_at=base,
            completed_at=base + timedelta(milliseconds=100),
            duration_ms=100.0,
            node_outputs=[
                {"node_id": "retrieve", "node_type": "retrieve",
                 "status": "success", "duration_ms": 50.0},
                {"node_id": "synthesize", "node_type": "synthesize",
                 "status": "success", "duration_ms": 50.0},
            ],
            halt_reason=None,
        ),
        RunTraceDoc(
            trace_id="t2",
            run_id="r2",
            strategy_id="alpha",
            capability_id="qa",
            tenant_id="recallhub",
            status="success",
            started_at=base + timedelta(hours=1),
            completed_at=base + timedelta(hours=1, milliseconds=200),
            duration_ms=200.0,
            node_outputs=[
                {"node_id": "retrieve", "node_type": "retrieve",
                 "status": "success", "duration_ms": 100.0},
                {"node_id": "synthesize", "node_type": "synthesize",
                 "status": "success", "duration_ms": 100.0},
            ],
            halt_reason=None,
        ),
        RunTraceDoc(
            trace_id="t3",
            run_id="r3",
            strategy_id="alpha",
            capability_id="qa",
            tenant_id="recallhub",
            status="failed",
            started_at=base + timedelta(hours=2),
            completed_at=base + timedelta(hours=2, milliseconds=300),
            duration_ms=300.0,
            node_outputs=[
                {"node_id": "retrieve", "node_type": "retrieve",
                 "status": "success", "duration_ms": 100.0},
                {"node_id": "synthesize", "node_type": "synthesize",
                 "status": "error", "duration_ms": 200.0,
                 "error": "boom"},
            ],
            halt_reason="node_halt: synthesize",
        ),
    ]
    for d in docs:
        await store.save(d)

    generator = NightlyReportGenerator(
        db=db, lookback_hours=24, output_dir=tmp_path
    )
    summary = await generator.generate(run_date=end)

    assert summary.total_runs == 3
    assert summary.success_count == 2
    assert summary.failure_count == 1
    # Percentiles must be non-zero now that real durations are persisted.
    assert summary.p50_latency_ms > 0.0
    assert summary.p95_latency_ms > 0.0
    # Top failing nodes ranks the synthesize error from t3.
    failing_node_ids = {entry["node_id"] for entry in summary.top_failing_nodes}
    assert "synthesize" in failing_node_ids


if __name__ == "__main__":
    pytest.main([__file__, "-v"])