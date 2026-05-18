"""Tests for :mod:`backend.agent.strategy.exploration_modes.BanditMode` (Task 76 / P7).

Covers:

* Warmup phase: round-robin pulls until every arm meets the threshold.
* UCB1 phase: arms with higher mean_reward dominate post-warmup pulls.
* ``record_result`` accumulates ``mean_reward`` correctly.
* ``next_arm`` honours ``max_total_pulls`` by returning ``None``.
* ``materialize_candidates`` delegates to GridMode and seeds arm states.
* Persistence: progress survives a fresh ``BanditMode`` instance bound to
  the same state store.
* MongoBanditStateStore round-trip (using ``_FakeAsyncDB``).
* Empty-pool ``next_arm`` returns ``None`` instead of crashing.
"""
from __future__ import annotations

import math
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest

from backend.agent.strategy.bandit_state_store import (
    BANDIT_STATE_COLLECTION_NAME,
    BanditArmState,
    InMemoryBanditStateStore,
    MongoBanditStateStore,
)
from backend.agent.strategy.exploration_modes import (
    BanditMode,
    GridMode,
    ModeRegistry,
)
from backend.agent.strategy.experiment_job_models import StrategyExperimentJob
from backend.agent.strategy.models import (
    StrategyGraph,
    StrategyNode,
    StrategySpec,
)
from backend.tests.strategy._fakes import _FakeAsyncDB


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_spec(strategy_id: str) -> StrategySpec:
    """Return a minimal :class:`StrategySpec` for bandit tests."""
    return StrategySpec(
        strategy_id=strategy_id,
        version="1.0.0",
        graph=StrategyGraph(
            nodes=[StrategyNode(node_id="n1", node_type="retrieve")],
            edges=[],
            entry_node="n1",
            terminal_nodes=["n1"],
        ),
    )


def _make_job(experiment_id: str = "exp-1") -> StrategyExperimentJob:
    """Return a queued-style experiment job."""
    return StrategyExperimentJob(
        id="job-bandit",
        experiment_id=experiment_id,
        tenant="recallhub",
        mode="bandit",
        datasets=["ds-a"],
        strategy_ids=["base"],
    )


class _StubGridMode(GridMode):
    """``GridMode`` whose :meth:`materialize_candidates` returns canned arms.

    Bypasses :class:`CandidateGenerator` so the bandit can be exercised
    without seeding a real :class:`SpecStore`.
    """

    def __init__(self, arms: list[StrategySpec]) -> None:
        super().__init__()
        self._arms = list(arms)
        self.calls = 0

    async def materialize_candidates(
        self,
        job: StrategyExperimentJob,
        *,
        spec_store: Any,
        candidate_generator_factory: Any = None,
    ) -> list[StrategySpec]:
        self.calls += 1
        return list(self._arms)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_bandit_registered_in_mode_registry() -> None:
    """``ModeRegistry.get('bandit')`` returns a fresh :class:`BanditMode`."""
    instance = ModeRegistry.get("bandit")
    assert isinstance(instance, BanditMode)
    assert "bandit" in ModeRegistry.known_modes()


@pytest.mark.asyncio
async def test_materialize_candidates_delegates_to_grid_and_seeds_state() -> None:
    """``materialize_candidates`` returns Grid arms and writes one row per arm."""
    arms = [_make_spec("a"), _make_spec("b"), _make_spec("c")]
    grid = _StubGridMode(arms)
    state = InMemoryBanditStateStore()
    mode = BanditMode(grid_mode=grid, state_store=state)

    job = _make_job(experiment_id="exp-seed")
    out = await mode.materialize_candidates(job, spec_store=None)

    assert grid.calls == 1
    assert [s.strategy_id for s in out] == ["a", "b", "c"]
    rows = await state.list_arms("exp-seed")
    assert sorted(r.arm_id for r in rows) == ["a", "b", "c"]
    assert all(r.pull_count == 0 and r.total_reward == 0.0 for r in rows)
    assert all(r.mean_reward == 0.0 for r in rows)


@pytest.mark.asyncio
async def test_warmup_round_robin_covers_each_arm_threshold_times() -> None:
    """Warmup phase pulls each arm exactly ``warmup_pulls_per_arm`` times."""
    arms = [_make_spec("a"), _make_spec("b"), _make_spec("c")]
    grid = _StubGridMode(arms)
    state = InMemoryBanditStateStore()
    mode = BanditMode(
        grid_mode=grid,
        state_store=state,
        warmup_pulls_per_arm=2,
    )
    job = _make_job(experiment_id="exp-warm")
    await mode.materialize_candidates(job, spec_store=None)

    pulled: list[str] = []
    for i in range(6):  # 3 arms × 2 warmup pulls
        arm = await mode.next_arm(
            experiment_id="exp-warm", total_pulls_so_far=i
        )
        assert arm is not None
        pulled.append(arm)
        # Simulate the runner recording a constant reward.
        await mode.record_result(
            experiment_id="exp-warm", arm_id=arm, reward=0.5
        )

    counts = {arm_id: pulled.count(arm_id) for arm_id in ("a", "b", "c")}
    assert counts == {"a": 2, "b": 2, "c": 2}
    # First three pulls are deterministic round-robin: a, b, c (lowest
    # arm_id wins ties).
    assert pulled[:3] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_post_warmup_ucb_prefers_higher_mean_reward_arm() -> None:
    """Post-warmup, the higher-reward arm dominates UCB1 pulls."""
    arms = [_make_spec("low"), _make_spec("high")]
    grid = _StubGridMode(arms)
    state = InMemoryBanditStateStore()
    mode = BanditMode(
        grid_mode=grid,
        state_store=state,
        warmup_pulls_per_arm=1,
    )
    job = _make_job(experiment_id="exp-ucb")
    await mode.materialize_candidates(job, spec_store=None)

    # Run warmup manually so each arm has one pull with very different
    # rewards.
    arm0 = await mode.next_arm(experiment_id="exp-ucb", total_pulls_so_far=0)
    await mode.record_result(experiment_id="exp-ucb", arm_id=arm0, reward=0.1)
    arm1 = await mode.next_arm(experiment_id="exp-ucb", total_pulls_so_far=1)
    await mode.record_result(experiment_id="exp-ucb", arm_id=arm1, reward=0.9)
    assert {arm0, arm1} == {"low", "high"}

    # Post-warmup: pull 30 times with rewards proportional to arm name.
    high_pulls = 0
    low_pulls = 0
    total = 2
    for _ in range(30):
        chosen = await mode.next_arm(
            experiment_id="exp-ucb", total_pulls_so_far=total
        )
        assert chosen is not None
        if chosen == "high":
            await mode.record_result(
                experiment_id="exp-ucb", arm_id=chosen, reward=0.9
            )
            high_pulls += 1
        else:
            await mode.record_result(
                experiment_id="exp-ucb", arm_id=chosen, reward=0.1
            )
            low_pulls += 1
        total += 1

    assert high_pulls > low_pulls, (
        f"UCB1 should favour the higher-reward arm; "
        f"got high={high_pulls} low={low_pulls}"
    )


@pytest.mark.asyncio
async def test_record_result_updates_mean_reward() -> None:
    """``record_result`` increments pull_count and accumulates total_reward."""
    state = InMemoryBanditStateStore()
    mode = BanditMode(state_store=state, warmup_pulls_per_arm=0)
    # Seed the arm row directly so we don't need GridMode here.
    await state.upsert_arm(
        BanditArmState(experiment_id="exp-r", arm_id="x")
    )

    await mode.record_result(experiment_id="exp-r", arm_id="x", reward=0.4)
    await mode.record_result(experiment_id="exp-r", arm_id="x", reward=0.8)
    await mode.record_result(experiment_id="exp-r", arm_id="x", reward=0.3)

    row = await state.get_arm("exp-r", "x")
    assert row is not None
    assert row.pull_count == 3
    assert row.total_reward == pytest.approx(1.5)
    assert row.mean_reward == pytest.approx(0.5)
    assert row.last_pulled_at is not None


@pytest.mark.asyncio
async def test_next_arm_returns_none_when_max_total_pulls_reached() -> None:
    """``next_arm`` returns ``None`` once the pull budget is exhausted."""
    arms = [_make_spec("a"), _make_spec("b")]
    grid = _StubGridMode(arms)
    state = InMemoryBanditStateStore()
    mode = BanditMode(
        grid_mode=grid,
        state_store=state,
        warmup_pulls_per_arm=1,
        max_total_pulls=4,
    )
    job = _make_job(experiment_id="exp-cap")
    await mode.materialize_candidates(job, spec_store=None)

    for i in range(4):
        chosen = await mode.next_arm(
            experiment_id="exp-cap", total_pulls_so_far=i
        )
        assert chosen is not None
        await mode.record_result(
            experiment_id="exp-cap", arm_id=chosen, reward=0.5
        )

    # At total_pulls_so_far == max_total_pulls the bandit must stop.
    assert (
        await mode.next_arm(
            experiment_id="exp-cap", total_pulls_so_far=4
        )
        is None
    )
    # And it stays None for any value beyond the cap.
    assert (
        await mode.next_arm(
            experiment_id="exp-cap", total_pulls_so_far=999
        )
        is None
    )


@pytest.mark.asyncio
async def test_next_arm_returns_none_with_empty_arm_pool() -> None:
    """An experiment with zero registered arms yields ``None``."""
    state = InMemoryBanditStateStore()
    mode = BanditMode(state_store=state)
    assert (
        await mode.next_arm(
            experiment_id="exp-empty", total_pulls_so_far=0
        )
        is None
    )


@pytest.mark.asyncio
async def test_state_persists_across_bandit_instances() -> None:
    """A fresh ``BanditMode`` reading the same store sees prior progress."""
    arms = [_make_spec("a"), _make_spec("b")]
    state = InMemoryBanditStateStore()

    grid_a = _StubGridMode(arms)
    mode_a = BanditMode(
        grid_mode=grid_a, state_store=state, warmup_pulls_per_arm=1
    )
    job = _make_job(experiment_id="exp-persist")
    await mode_a.materialize_candidates(job, spec_store=None)

    arm0 = await mode_a.next_arm(
        experiment_id="exp-persist", total_pulls_so_far=0
    )
    await mode_a.record_result(
        experiment_id="exp-persist", arm_id=arm0, reward=0.7
    )
    arm1 = await mode_a.next_arm(
        experiment_id="exp-persist", total_pulls_so_far=1
    )
    await mode_a.record_result(
        experiment_id="exp-persist", arm_id=arm1, reward=0.2
    )

    # New mode instance, *same* store. Re-materialize must not zero-out
    # the warm rows.
    grid_b = _StubGridMode(arms)
    mode_b = BanditMode(
        grid_mode=grid_b, state_store=state, warmup_pulls_per_arm=1
    )
    await mode_b.materialize_candidates(job, spec_store=None)

    rows = sorted(
        await state.list_arms("exp-persist"), key=lambda r: r.arm_id
    )
    assert [r.arm_id for r in rows] == ["a", "b"]
    assert sum(r.pull_count for r in rows) == 2
    assert sum(r.total_reward for r in rows) == pytest.approx(0.9)
    # And the warmed-up bandit picks the higher-mean arm next, since
    # both arms now satisfy the warmup threshold.
    next_after = await mode_b.next_arm(
        experiment_id="exp-persist", total_pulls_so_far=2
    )
    # The arm with mean_reward 0.7 must dominate the arm with 0.2 once
    # warmup has lapsed and pull counts are equal.
    expected_winner = arm0
    assert next_after == expected_winner


@pytest.mark.asyncio
async def test_mongo_bandit_state_store_roundtrip() -> None:
    """:class:`MongoBanditStateStore` round-trips a pull through ``_FakeAsyncDB``."""
    db = _FakeAsyncDB()
    store = MongoBanditStateStore(db)
    await store.ensure_indexes()

    # Index keyspec was issued.
    indexes = db[BANDIT_STATE_COLLECTION_NAME].created_indexes
    assert any(
        args and args[0] == [("experiment_id", 1), ("arm_id", 1)]
        for args, _kwargs in indexes
    )

    await store.upsert_arm(
        BanditArmState(experiment_id="exp-m", arm_id="a")
    )
    row = await store.record_pull("exp-m", "a", reward=0.6)
    assert row.pull_count == 1
    assert row.total_reward == pytest.approx(0.6)

    row2 = await store.record_pull("exp-m", "a", reward=0.4)
    assert row2.pull_count == 2
    assert row2.mean_reward == pytest.approx(0.5)

    listed = await store.list_arms("exp-m")
    assert len(listed) == 1
    assert listed[0].arm_id == "a"
    assert listed[0].pull_count == 2

    # ensure_indexes is idempotent (no double-create explosion).
    await store.ensure_indexes()
