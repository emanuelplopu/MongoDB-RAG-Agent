"""Tests for the ``run-now`` scheduler endpoint.

Exercises :func:`backend.routers.scheduler.run_schedule_now` end-to-end via
FastAPI's :class:`TestClient` against a stub :class:`SchedulerDaemon` so
the test never spawns real exploration tasks. Verifies the four response
shapes documented in the brief: 202 on success, 404 when missing, 409
when paused, and 503 when the circuit breaker is open.
"""

from __future__ import annotations

from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.agent.strategy.scheduler_store import (
    InMemorySchedulerStore,
    SchedulerStore,
)
from backend.routers import scheduler as scheduler_router
from backend.routers.scheduler import router
from backend.scheduler.models import StrategySchedule
from backend.scheduler.scheduler_daemon import (
    CircuitBreakerOpenError,
    ScheduleNotFoundError,
    SchedulePausedError,
    SchedulerDaemon,
)


# ══════════════════════════════════════════════════════════════════════════════
# Stubs
# ══════════════════════════════════════════════════════════════════════════════


class _StubDaemon(SchedulerDaemon):
    """Daemon stub that captures ``run_now`` calls instead of spawning tasks."""

    def __init__(
        self,
        store: SchedulerStore,
        *,
        outcome: str = "ok",
        trace_id: str = "trace-stub",
    ) -> None:
        # Bypass the real ctor: it instantiates Ollama/circuit-breaker
        # subsystems we don't want to touch in unit tests.
        self.store = store
        self.outcome = outcome
        self.trace_id = trace_id
        self.run_now_calls: list[str] = []
        self._running = False
        self._shutdown_requested = False
        self._current_run = None

    async def run_now(self, schedule_id: str) -> str:  # type: ignore[override]
        self.run_now_calls.append(schedule_id)
        if self.outcome == "missing":
            raise ScheduleNotFoundError(schedule_id)
        if self.outcome == "paused":
            raise SchedulePausedError(f"Schedule {schedule_id} is paused")
        if self.outcome == "open":
            raise CircuitBreakerOpenError(
                f"Schedule {schedule_id} circuit breaker is OPEN"
            )
        if self.outcome == "boom":
            raise RuntimeError("explosion")
        return self.trace_id


def _make_app(
    *,
    daemon: Optional[SchedulerDaemon] = None,
    store: Optional[SchedulerStore] = None,
) -> FastAPI:
    """Build a minimal FastAPI app with the scheduler router mounted."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.scheduler_store = store
    app.state.scheduler_daemon = daemon
    # ``app.state.db`` not set on purpose so the dependency falls back to
    # the explicitly-injected store.
    return app


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def store() -> InMemorySchedulerStore:
    return InMemorySchedulerStore()


@pytest.fixture(autouse=True)
def _reset_module_state() -> None:
    """Reset the module-level fallback singletons between tests."""
    scheduler_router._default_in_memory_store = InMemorySchedulerStore()
    scheduler_router._default_daemon = SchedulerDaemon(
        store=scheduler_router._default_in_memory_store
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════════


def test_run_now_returns_202_with_trace_id(
    store: InMemorySchedulerStore,
) -> None:
    daemon = _StubDaemon(store, trace_id="trace-success")
    app = _make_app(daemon=daemon, store=store)

    with TestClient(app) as client:
        response = client.post("/api/v1/schedules/sched-success/run-now")

    assert response.status_code == 202
    body = response.json()
    assert body["trace_id"] == "trace-success"
    assert body["schedule_id"] == "sched-success"
    assert daemon.run_now_calls == ["sched-success"]


def test_run_now_returns_404_for_missing_schedule(
    store: InMemorySchedulerStore,
) -> None:
    daemon = _StubDaemon(store, outcome="missing")
    app = _make_app(daemon=daemon, store=store)

    with TestClient(app) as client:
        response = client.post("/api/v1/schedules/missing/run-now")

    assert response.status_code == 404
    assert "Schedule not found" in response.json()["detail"]


def test_run_now_returns_409_when_paused(
    store: InMemorySchedulerStore,
) -> None:
    daemon = _StubDaemon(store, outcome="paused")
    app = _make_app(daemon=daemon, store=store)

    with TestClient(app) as client:
        response = client.post("/api/v1/schedules/sched-paused/run-now")

    assert response.status_code == 409
    assert "paused" in response.json()["detail"].lower()


def test_run_now_returns_503_when_breaker_open(
    store: InMemorySchedulerStore,
) -> None:
    daemon = _StubDaemon(store, outcome="open")
    app = _make_app(daemon=daemon, store=store)

    with TestClient(app) as client:
        response = client.post("/api/v1/schedules/sched-open/run-now")

    assert response.status_code == 503
    assert "open" in response.json()["detail"].lower()


def test_run_now_unexpected_error_becomes_500(
    store: InMemorySchedulerStore,
) -> None:
    daemon = _StubDaemon(store, outcome="boom")
    app = _make_app(daemon=daemon, store=store)

    with TestClient(app) as client:
        response = client.post("/api/v1/schedules/sched-boom/run-now")

    assert response.status_code == 500


# ══════════════════════════════════════════════════════════════════════════════
# Daemon-level run_now (no router)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_daemon_run_now_rejects_missing_schedule(
    store: InMemorySchedulerStore,
) -> None:
    daemon = SchedulerDaemon(store=store)
    with pytest.raises(ScheduleNotFoundError):
        await daemon.run_now("nope")


@pytest.mark.asyncio
async def test_daemon_run_now_rejects_paused_schedule(
    store: InMemorySchedulerStore,
) -> None:
    paused = StrategySchedule(
        id="p1",
        name="Paused",
        paused=True,
        paused_reason="manual",
        candidate_strategy_ids=[],
    )
    await store.upsert(paused)

    daemon = SchedulerDaemon(store=store)
    with pytest.raises(SchedulePausedError):
        await daemon.run_now("p1")


@pytest.mark.asyncio
async def test_daemon_run_now_rejects_open_breaker(
    store: InMemorySchedulerStore,
) -> None:
    blocked = StrategySchedule(
        id="b1",
        name="Open breaker",
        circuit_breaker_state="open",
        candidate_strategy_ids=[],
    )
    await store.upsert(blocked)

    daemon = SchedulerDaemon(store=store)
    with pytest.raises(CircuitBreakerOpenError):
        await daemon.run_now("b1")


@pytest.mark.asyncio
async def test_daemon_run_now_returns_trace_and_marks_running(
    store: InMemorySchedulerStore,
) -> None:
    schedule = StrategySchedule(
        id="ok-1",
        name="Eligible",
        candidate_strategy_ids=[],  # empty -> background task no-ops fast
    )
    await store.upsert(schedule)

    daemon = SchedulerDaemon(store=store)
    trace_id = await daemon.run_now("ok-1")

    assert isinstance(trace_id, str) and trace_id
    persisted = await store.get("ok-1")
    assert persisted is not None
    assert persisted.last_run_status == "running"
    assert persisted.last_run_at is not None
