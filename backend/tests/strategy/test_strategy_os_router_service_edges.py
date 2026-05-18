"""Strategy OS router and service edge coverage.

These tests focus on defensive branches around dependency resolution,
HTTP error translation, and best-effort persistence. The broad happy
paths live in the feature tests; this file keeps the less common edges
compact and explicit.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from backend.agent.strategies.base import (
    StrategyConfig,
    StrategyDomain,
    StrategyMetadata,
)
from backend.agent.strategy.models import StrategySpec
from backend.agent.strategy.promotion_evaluator import (
    PromotionEvaluator,
    PromotionThresholds,
    _citation_coverage,
    _coerce_float,
    _extract_metrics,
    _is_fatal,
    _percentile,
)
from backend.agent.strategy.promotion_manager import (
    InvalidStateTransitionError,
    RollbackCooldownError,
)
from backend.agent.strategy.spec_store import (
    InMemorySpecStore,
    MongoSpecStore,
    VersionConflictError,
)
from backend.routers import scheduler as scheduler_router
from backend.routers import strategies as strategies_router
from backend.routers import strategy_specs as spec_router
from backend.scheduler.models import StrategySchedule
from backend.scheduler.scheduler_daemon import (
    CircuitBreakerOpenError,
    ScheduleNotFoundError,
    SchedulePausedError,
)


def _spec_payload(
    strategy_id: str = "edge-spec",
    *,
    version: str = "1.0.0",
    status: str = "draft",
    capability_id: str = "qa",
) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "version": version,
        "status": status,
        "capability_id": capability_id,
        "tenant_scope": "default",
        "graph": {
            "nodes": [{"node_id": "retrieve", "node_type": "retrieve"}],
            "edges": [],
            "entry_node": "retrieve",
            "terminal_nodes": ["retrieve"],
        },
    }


def _snapshot(
    strategy_id: str = "edge-spec",
    *,
    status: str = "draft",
    version: str = "1.0.0",
    counter: int = 1,
) -> dict[str, Any]:
    payload = _spec_payload(strategy_id, status=status, version=version)
    return {
        "spec_data": payload,
        "version": version,
        "version_counter": counter,
        "status": status,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "spec_hash": "hash",
    }


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = list(docs)

    def __aiter__(self) -> "_Cursor":
        return self

    async def __anext__(self) -> dict[str, Any]:
        if not self._docs:
            raise StopAsyncIteration
        return self._docs.pop(0)


class _Collection:
    def __init__(
        self,
        docs: list[dict[str, Any]] | None = None,
        *,
        fail_find: bool = False,
    ) -> None:
        self.docs = docs or []
        self.fail_find = fail_find

    def find(self, _query: dict[str, Any]) -> _Cursor:
        if self.fail_find:
            raise RuntimeError("find failed")
        return _Cursor(self.docs)


class _DB(dict[str, _Collection]):
    def __getitem__(self, name: str) -> _Collection:
        return dict.setdefault(self, name, _Collection())


@pytest.mark.asyncio
async def test_strategy_spec_router_defensive_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover validation, conflicts, promotion errors, and DI Mongo paths."""

    class Store:
        def __init__(self, snap: dict[str, Any] | None = None) -> None:
            self.snap = snap
            self.snapshots: dict[str, list[dict[str, Any]]] = {}
            self.raise_upsert: Exception | None = None
            self.raise_flush = False

        async def get(self, _sid: str, _version: str | None = None) -> dict[str, Any] | None:
            return self.snap

        async def upsert(
            self,
            _snapshot: dict[str, Any],
            *,
            expected_version: int | None = None,
        ) -> dict[str, Any]:
            if self.raise_upsert is not None:
                raise self.raise_upsert
            return _snapshot

        async def list_versions(self, _sid: str) -> list[dict[str, Any]]:
            return [] if self.snap is None else [self.snap]

        async def list(self, **_: Any) -> list[dict[str, Any]]:
            return [] if self.snap is None else [self.snap]

        async def flush_snapshot(self, _sid: str, _version: str) -> bool:
            if self.raise_flush:
                raise RuntimeError("flush failed")
            return True

    class Manager:
        def __init__(self, method: str, exc: Exception) -> None:
            self.method = method
            self.exc = exc

        async def promote(self, *_: Any, **__: Any) -> StrategySpec:
            raise self.exc

        async def deprecate(self, *_: Any, **__: Any) -> StrategySpec:
            raise self.exc

        async def archive(self, *_: Any, **__: Any) -> StrategySpec:
            raise self.exc

        async def rollback(self, *_: Any, **__: Any) -> StrategySpec:
            raise self.exc

    request_without_app = SimpleNamespace()
    assert spec_router._resolve_mongo_db(request_without_app) is None

    request_with_db = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db={"db": "handle"})))
    assert isinstance(spec_router.get_spec_store(request_with_db), MongoSpecStore)

    non_default = InMemorySpecStore({})
    assert spec_router.get_promotion_manager(non_default) is not spec_router._promotion_manager

    with pytest.raises(HTTPException) as parse_error:
        spec_router._parse_spec({"strategy_id": 123})
    assert parse_error.value.status_code == 400

    with pytest.raises(HTTPException) as already_exists:
        await spec_router.create_spec(_spec_payload(), store=Store(_snapshot()))
    assert already_exists.value.status_code == 409

    monkeypatch.setattr(spec_router, "validate_spec", lambda _spec: ["invalid"])
    with pytest.raises(HTTPException) as invalid_create:
        await spec_router.create_spec(_spec_payload("invalid-create"), store=Store())
    assert invalid_create.value.status_code == 400
    monkeypatch.setattr(spec_router, "validate_spec", lambda _spec: [])

    conflict_store = Store()
    conflict_store.raise_upsert = VersionConflictError(expected=0, current=1)
    with pytest.raises(HTTPException) as create_conflict:
        await spec_router.create_spec(_spec_payload("create-conflict"), store=conflict_store)
    assert create_conflict.value.status_code == 409

    missing_store = Store()
    for coro in (
        spec_router.get_spec("missing", store=missing_store),
        spec_router.get_spec("missing", version="2.0.0", store=missing_store),
        spec_router.update_spec("missing", _spec_payload("missing"), x_expected_version=1, store=missing_store),
        spec_router.validate_stored_spec("missing", store=missing_store),
        spec_router.get_spec_history("missing", store=missing_store),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await coro
        assert exc_info.value.status_code == 404

    current = Store(_snapshot(counter=4))
    with pytest.raises(HTTPException) as expected_mismatch:
        await spec_router.update_spec("edge-spec", _spec_payload(), x_expected_version=3, store=current)
    assert expected_mismatch.value.status_code == 409

    with pytest.raises(HTTPException) as payload_mismatch:
        await spec_router.update_spec("edge-spec", _spec_payload("other"), x_expected_version=4, store=current)
    assert payload_mismatch.value.status_code == 400

    monkeypatch.setattr(spec_router, "validate_spec", lambda _spec: ["bad update"])
    with pytest.raises(HTTPException) as invalid_update:
        await spec_router.update_spec("edge-spec", _spec_payload(), x_expected_version=4, store=current)
    assert invalid_update.value.status_code == 400
    monkeypatch.setattr(spec_router, "validate_spec", lambda _spec: [])

    current.raise_upsert = VersionConflictError(expected=4, current=5)
    with pytest.raises(HTTPException) as update_conflict:
        await spec_router.update_spec("edge-spec", _spec_payload(), x_expected_version=4, store=current)
    assert update_conflict.value.status_code == 409

    malformed = Store({**_snapshot(), "spec_data": {"strategy_id": 123}})
    validation = await spec_router.validate_stored_spec("edge-spec", store=malformed)
    assert validation.valid is False

    import backend.agent.coordinator as coordinator

    monkeypatch.setattr(coordinator, "get_spec_selector", lambda: (_ for _ in ()).throw(RuntimeError("cache down")))
    spec_router._invalidate_selector_cache(StrategySpec(**_spec_payload()))

    flush_store = Store()
    flush_store.raise_flush = True
    await spec_router._flush_promotion_change(flush_store, StrategySpec(**_spec_payload()), op="promote")

    body = spec_router.PromotionRequest(actor="tester", reason="edge")
    rollback_body = spec_router.RollbackRequest(target_version="0.9.0", actor="tester", reason="edge")
    promotion_cases = [
        (spec_router.promote_spec("s", "1", body, manager=Manager("promote", KeyError("nope")), store=Store()), 404),
        (
            spec_router.promote_spec(
                "s",
                "1",
                body,
                manager=Manager("promote", InvalidStateTransitionError("bad state")),
                store=Store(),
            ),
            409,
        ),
        (
            spec_router.deprecate_spec(
                "s",
                "1",
                body,
                manager=Manager("deprecate", InvalidStateTransitionError("bad state")),
                store=Store(),
            ),
            409,
        ),
        (
            spec_router.archive_spec_version(
                "s",
                "1",
                body,
                manager=Manager("archive", InvalidStateTransitionError("bad state")),
                store=Store(),
            ),
            409,
        ),
        (
            spec_router.rollback_spec(
                "s",
                rollback_body,
                manager=Manager("rollback", RollbackCooldownError("wait", retry_after_seconds=7)),
                store=Store(),
            ),
            409,
        ),
        (
            spec_router.rollback_spec(
                "s",
                rollback_body,
                manager=Manager("rollback", InvalidStateTransitionError("bad state")),
                store=Store(),
            ),
            409,
        ),
    ]
    for coro, status_code in promotion_cases:
        with pytest.raises(HTTPException) as exc_info:
            await coro
        assert exc_info.value.status_code == status_code


@pytest.mark.asyncio
async def test_scheduler_router_error_translation_and_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover scheduler endpoint failure adapters without starting a daemon."""

    class Store:
        def __init__(self, schedule: StrategySchedule | None = None, *, fail: str | None = None) -> None:
            self.schedule = schedule
            self.fail = fail

        async def list(self) -> list[StrategySchedule]:
            if self.fail == "list":
                raise RuntimeError("list failed")
            return [] if self.schedule is None else [self.schedule]

        async def get(self, _schedule_id: str) -> StrategySchedule | None:
            return self.schedule

        async def upsert(self, schedule: StrategySchedule) -> StrategySchedule:
            if self.fail == "upsert":
                raise RuntimeError("write failed")
            return schedule

    class Daemon:
        def __init__(self, exc: Exception | None = None) -> None:
            self.exc = exc
            self._running = False
            self._current_run = None
            self._shutdown_requested = False

        async def run_now(self, _schedule_id: str) -> str:
            if self.exc is not None:
                raise self.exc
            return "trace-edge"

    class BadStatusDaemon:
        @property
        def _running(self) -> bool:
            raise RuntimeError("status failed")

    class BadToggleDaemon:
        def __setattr__(self, name: str, value: Any) -> None:
            if name == "_shutdown_requested":
                raise RuntimeError("toggle failed")
            object.__setattr__(self, name, value)

    assert scheduler_router._resolve_mongo_db(SimpleNamespace()) is None
    request_with_db = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db={"db": "handle"})))
    assert isinstance(scheduler_router.get_scheduler_store(request_with_db), scheduler_router.MongoSchedulerStore)

    with pytest.raises(HTTPException) as list_failed:
        await scheduler_router.list_schedules(store=Store(fail="list"))
    assert list_failed.value.status_code == 500

    with pytest.raises(HTTPException) as create_failed:
        await scheduler_router.create_or_update_schedule(
            scheduler_router.CreateScheduleRequest(), store=Store(fail="upsert")
        )
    assert create_failed.value.status_code == 500

    with pytest.raises(HTTPException) as missing_get:
        await scheduler_router.get_schedule("missing", store=Store())
    assert missing_get.value.status_code == 404

    with pytest.raises(HTTPException) as missing_disable:
        await scheduler_router.disable_schedule("missing", store=Store())
    assert missing_disable.value.status_code == 404

    schedule = StrategySchedule(name="Nightly")
    with pytest.raises(HTTPException) as disable_failed:
        await scheduler_router.disable_schedule("sched", store=Store(schedule, fail="upsert"))
    assert disable_failed.value.status_code == 500

    run_now_cases = [
        (ScheduleNotFoundError("missing"), 404),
        (SchedulePausedError("paused"), 409),
        (CircuitBreakerOpenError("open"), 503),
        (RuntimeError("boom"), 500),
    ]
    for exc, status_code in run_now_cases:
        with pytest.raises(HTTPException) as exc_info:
            await scheduler_router.run_schedule_now("sched", daemon=Daemon(exc))
        assert exc_info.value.status_code == status_code

    with pytest.raises(HTTPException) as status_failed:
        await scheduler_router.get_scheduler_status(daemon=BadStatusDaemon())
    assert status_failed.value.status_code == 500

    for endpoint in (scheduler_router.pause_scheduler, scheduler_router.resume_scheduler):
        with pytest.raises(HTTPException) as toggle_failed:
            await endpoint(daemon=BadToggleDaemon())
        assert toggle_failed.value.status_code == 500

    monkeypatch.setattr(
        scheduler_router,
        "_reports",
        [SimpleNamespace(started_at={}), SimpleNamespace(started_at="2026-01-01")],
    )
    with pytest.raises(HTTPException) as reports_failed:
        await scheduler_router.get_reports()
    assert reports_failed.value.status_code == 500


@pytest.mark.asyncio
async def test_strategies_router_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise strategy-management error branches and LLM failure adapters."""

    metadata = StrategyMetadata(
        id="strategy-a",
        name="Strategy A",
        version="1.0.0",
        description="A",
        domains=[StrategyDomain.GENERAL],
        tags=[],
        is_default=True,
        author="tests",
    )
    strategy = SimpleNamespace(
        metadata=metadata,
        config=StrategyConfig(),
        get_analyze_prompt=lambda: "analyze",
        get_plan_prompt=lambda: "plan",
        get_evaluate_prompt=lambda: "evaluate",
        get_synthesize_prompt=lambda: "synthesize",
    )

    class Registry:
        mode = "ok"

        @classmethod
        def get_default(cls) -> Any:
            if cls.mode == "empty":
                raise ValueError("none")
            if cls.mode == "boom":
                raise RuntimeError("registry failed")
            return strategy

        @classmethod
        def get(cls, strategy_id: str) -> Any:
            if strategy_id == "missing":
                raise KeyError(strategy_id)
            if strategy_id == "boom":
                raise RuntimeError("registry failed")
            return strategy

    class Metrics:
        fail_stats = False
        fail_compare = False

        def get_strategy_stats(self, *_: Any, **__: Any) -> dict[str, Any]:
            if self.fail_stats:
                raise RuntimeError("metrics failed")
            return {"strategy_id": "strategy-a"}

        def compare_strategies(self, *_: Any, **__: Any) -> dict[str, Any]:
            if self.fail_compare:
                raise RuntimeError("compare failed")
            return {
                "strategy_a": "strategy-a",
                "strategy_b": "strategy-b",
                "filters": {},
                "comparison": {},
            }

    monkeypatch.setattr(strategies_router, "StrategyRegistry", Registry)

    Registry.mode = "empty"
    with pytest.raises(HTTPException) as no_default:
        await strategies_router.get_default_strategy()
    assert no_default.value.status_code == 503

    Registry.mode = "boom"
    with pytest.raises(HTTPException) as default_boom:
        await strategies_router.get_default_strategy()
    assert default_boom.value.status_code == 500
    Registry.mode = "ok"

    for strategy_id, status_code in [("", 400), ("missing", 404), ("boom", 500)]:
        with pytest.raises(HTTPException) as exc_info:
            await strategies_router.get_strategy_metrics_endpoint(strategy_id)
        assert exc_info.value.status_code == status_code

    metrics = Metrics()
    metrics.fail_stats = True
    monkeypatch.setattr(strategies_router, "get_strategy_metrics", lambda: metrics)
    with pytest.raises(HTTPException) as metrics_failed:
        await strategies_router.get_strategy_metrics_endpoint("strategy-a")
    assert metrics_failed.value.status_code == 500

    with pytest.raises(HTTPException) as compare_missing:
        await strategies_router.compare_strategies_endpoint(
            strategies_router.CompareRequest(strategy_a="missing", strategy_b="strategy-b")
        )
    assert compare_missing.value.status_code == 404

    with pytest.raises(HTTPException) as compare_verify_failed:
        await strategies_router.compare_strategies_endpoint(
            strategies_router.CompareRequest(strategy_a="boom", strategy_b="strategy-b")
        )
    assert compare_verify_failed.value.status_code == 500

    metrics.fail_stats = False
    metrics.fail_compare = True
    with pytest.raises(HTTPException) as compare_failed:
        await strategies_router.compare_strategies_endpoint(
            strategies_router.CompareRequest(strategy_a="strategy-a", strategy_b="strategy-b")
        )
    assert compare_failed.value.status_code == 500

    base_ab = dict(
        query="query",
        response_a="A",
        response_b="B",
        strategy_a="strategy-a",
        strategy_b="strategy-b",
        latency_a_ms=1.0,
        latency_b_ms=2.0,
    )
    for override in ({"response_a": ""}, {"response_b": ""}):
        with pytest.raises(HTTPException) as bad_ab:
            await strategies_router.ab_compare_responses(
                strategies_router.ABCompareResponsesRequest(**{**base_ab, **override}),
                req=SimpleNamespace(),
            )
        assert bad_ab.value.status_code == 400

    monkeypatch.setattr(
        strategies_router,
        "get_llm_manager",
        lambda: (_ for _ in ()).throw(RuntimeError("llm init failed")),
    )
    with pytest.raises(HTTPException) as llm_init_failed:
        await strategies_router.ab_compare_responses(
            strategies_router.ABCompareResponsesRequest(**base_ab),
            req=SimpleNamespace(),
        )
    assert llm_init_failed.value.status_code == 500

    class BadJSONClient:
        async def complete_json(self, **_: Any) -> dict[str, Any]:
            raise json.JSONDecodeError("bad", "{}", 0)

    class FailingClient:
        async def complete_json(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("provider failed")

    for client, expected_detail in (
        (BadJSONClient(), "invalid JSON"),
        (FailingClient(), "Failed to evaluate"),
    ):
        monkeypatch.setattr(
            strategies_router,
            "get_llm_manager",
            lambda client=client: SimpleNamespace(get_orchestrator_client=lambda: client),
        )
        with pytest.raises(HTTPException) as ab_failed:
            await strategies_router.ab_compare_responses(
                strategies_router.ABCompareResponsesRequest(**base_ab),
                req=SimpleNamespace(),
            )
        assert ab_failed.value.status_code == 500
        assert expected_detail in str(ab_failed.value.detail)


@pytest.mark.asyncio
async def test_promotion_evaluator_reader_helper_edges() -> None:
    """Cover helper fallbacks, Mongo reader failures, and per-candidate isolation."""

    assert _percentile([], 95) == 0.0
    assert _percentile([4.0], 95) == 4.0
    assert _coerce_float(True) is None
    assert _coerce_float("1") is None

    doc_with_dimensions = {
        "result": {
            "dimension_scores": [
                {"dimension_id": "citation_quality", "score": 0.77},
                "bad",
            ]
        }
    }
    assert _extract_metrics(doc_with_dimensions)["dimension.citation_quality"] == 0.77
    assert _citation_coverage(doc_with_dimensions) == 0.77
    assert _is_fatal({"result": {"fatal_gate": 0}}) is True
    assert _is_fatal({"status": "fatal"}) is True

    evaluator = PromotionEvaluator(
        thresholds=PromotionThresholds(
            min_runs=1,
            min_composite_score=0.5,
            beats_current_default_by=0.0,
            min_citation_coverage=0.5,
            latency_p95_below_ms=1000,
            manual_approval_required=False,
        )
    )
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    until = datetime(2026, 1, 2, tzinfo=timezone.utc)

    assert await evaluator._read_eval_docs(None, strategy_id="s", since=since, until=until) == []
    failing_db = _DB({"evaluation_results": _Collection(fail_find=True)})
    assert await evaluator._read_eval_docs(failing_db, strategy_id="s", since=since, until=until) == []
    assert await evaluator._aggregate_default_score(None, capability_id="qa", since=since, until=until, exclude_strategy_id="s") is None

    candidate_doc = {
        "strategy_id": "candidate",
        "result": {
            "composite_score": 0.9,
            "latency_ms": 100,
            "privacy_gate": 1,
            "dimension_scores": [{"dimension_id": "citation_quality", "score": 0.95}],
        },
    }
    default_doc = {"strategy_id": "default", "result": {"composite_score": 0.6, "latency_ms": 100}}
    db = _DB(
        {
            "evaluation_results": _Collection([candidate_doc, default_doc]),
            "strategy_specs": _Collection(
                [
                    {"spec_data": {"strategy_id": "candidate", "capability_id": "qa", "version": 123}},
                    {"strategy_id": 123, "spec_data": {"capability_id": "qa"}},
                    {"strategy_id": "default", "spec_data": {"capability_id": "qa"}, "version": "1.0.0"},
                    {"strategy_id": "default", "spec_data": {"capability_id": "qa"}, "version": "1.0.1"},
                ]
            ),
        }
    )

    summary = await evaluator.evaluate(
        db=db,
        capability_id="qa",
        candidate_strategy_id="candidate",
        candidate_version="1.0.0",
        since=since,
        until=until,
    )
    assert summary.recommendation == "recommended"
    assert summary.score_delta is not None

    candidates = await evaluator._iter_candidate_specs(db)
    assert candidates == [("qa", "candidate", None), ("qa", "default", "1.0.0")]

    broken_specs = _DB({"strategy_specs": _Collection(fail_find=True)})
    assert await evaluator._find_default_strategy_id(broken_specs, capability_id="qa", exclude_strategy_id="s") is None
    assert await evaluator._iter_candidate_specs(broken_specs) == []

    class IsolatingEvaluator(PromotionEvaluator):
        async def _iter_candidate_specs(self, _db: Any) -> list[tuple[str, str, str | None]]:
            return [("qa", "bad", "1"), ("qa", "good", "1")]

        async def evaluate(self, **kwargs: Any) -> Any:
            if kwargs["candidate_strategy_id"] == "bad":
                raise RuntimeError("candidate failed")
            return await super().evaluate(**kwargs)

    isolated = await IsolatingEvaluator(thresholds=evaluator.thresholds).evaluate_all(
        db=db,
        since=since,
        until=until,
    )
    assert [row.strategy_id for row in isolated] == ["good"]
