"""High-yield Strategy OS reporting, runner, and selector edge tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.agent.strategy import report_generator as report_mod
from backend.agent.strategy import spec_loader as spec_loader_mod
from backend.agent.strategy import spec_selector as selector_mod
from backend.agent.strategy.checkpoint_manager import CheckpointManager
from backend.agent.strategy.graph_compiler import compile_graph
from backend.agent.strategy.models import (
    BusinessContext,
    CitationRef,
    NodeOutput,
    RetrievedChunk,
    SourcePolicy,
    StrategyBudgets,
    StrategyEdge,
    StrategyGraph,
    StrategyNode,
    StrategyRunState,
    StrategySpec,
    SynthesisResult,
    ValidationIssue,
    ValidationResult,
)
from backend.agent.strategy.nodes.base import NodeExecutor
from backend.agent.strategy.nodes.registry import NodeRegistry
from backend.agent.strategy.report_generator import (
    RUN_REPORT_COLLECTION_NAME,
    STRATEGY_RUN_COLLECTION_NAME,
    NightlyReportGenerator,
    NightlyReportSummary,
    _ensure_utc,
    _percentile,
)
from backend.agent.strategy.spec_store import InMemorySpecStore, MongoSpecStore
from backend.agent.strategy.spec_loader import SpecLoaderError
from backend.agent.strategy.spec_selector import (
    AdaptiveSelector,
    FastPathRules,
    StrategySpecSelector,
)
from backend.agent.strategy.strategy_runner import (
    StateViolationError,
    StrategyExecutionError,
    StrategyRunner,
)


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
        fail_index: bool = False,
        fail_replace: bool = False,
    ) -> None:
        self.docs = docs or []
        self.fail_find = fail_find
        self.fail_index = fail_index
        self.fail_replace = fail_replace

    def find(self, _query: dict[str, Any] | None = None) -> _Cursor:
        if self.fail_find:
            raise RuntimeError("find failed")
        return _Cursor(self.docs)

    async def find_one(self, _query: dict[str, Any]) -> dict[str, Any] | None:
        if self.fail_find:
            raise RuntimeError("find_one failed")
        return self.docs[0] if self.docs else None

    async def replace_one(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        if self.fail_replace:
            raise RuntimeError("replace failed")
        return SimpleNamespace(upserted_id="id")

    async def delete_one(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        if self.fail_replace:
            raise RuntimeError("delete failed")
        return SimpleNamespace(deleted_count=1)

    async def delete_many(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        if self.fail_replace:
            raise RuntimeError("delete failed")
        return SimpleNamespace(deleted_count=1)

    def create_index(self, *_args: Any, **_kwargs: Any) -> None:
        if self.fail_index:
            raise RuntimeError("index failed")
        return None


class _DB(dict[str, _Collection]):
    def __getitem__(self, name: str) -> _Collection:
        return dict.setdefault(self, name, _Collection())


def _spec(
    strategy_id: str = "spec-a",
    *,
    capability_id: str = "qa",
    latency_target_ms: int = 1000,
    latency_hard_limit_ms: int = 2000,
    local_only: bool = False,
    display_name: str | None = None,
    model_roles: dict[str, str] | None = None,
) -> StrategySpec:
    return StrategySpec(
        strategy_id=strategy_id,
        display_name=display_name or strategy_id,
        capability_id=capability_id,
        tenant_scope="recallhub",
        status="active",
        graph=StrategyGraph(
            nodes=[StrategyNode(node_id="n", node_type="normalize_query")],
            edges=[],
            entry_node="n",
            terminal_nodes=["n"],
        ),
        budgets=StrategyBudgets(
            latency_target_ms=latency_target_ms,
            latency_hard_limit_ms=latency_hard_limit_ms,
            max_output_tokens=100,
            local_only=local_only,
        ),
        model_roles={"other": "model-a"} if model_roles is None else model_roles,
    )


def _snapshot(spec: StrategySpec) -> dict[str, Any]:
    return {
        "spec_data": spec.model_dump(mode="json"),
        "status": spec.status,
        "version": spec.version,
        "version_counter": 1,
        "created_at": datetime.now(timezone.utc),
        "spec_hash": "hash",
    }


@pytest.mark.asyncio
async def test_report_generator_error_and_empty_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover report generator edge branches without depending on Mongo."""
    assert _ensure_utc(None) is None
    assert _ensure_utc(datetime(2026, 1, 1)).tzinfo == timezone.utc
    assert _percentile([], 95.0) == 0.0
    with pytest.raises(ValueError):
        NightlyReportGenerator(None, lookback_hours=0)
    with pytest.raises(ValueError):
        NightlyReportGenerator(None, regression_threshold=-0.1)

    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    start = now - timedelta(hours=24)
    generator = NightlyReportGenerator(None, output_dir=tmp_path)
    assert await generator._aggregate_runs(start, now) == {
        "total": 0,
        "success": 0,
        "failed": 0,
        "p50_ms": 0.0,
        "p95_ms": 0.0,
        "top_failing_nodes": [],
        "docs": [],
        "per_strategy_latencies": {},
        "fatal_failures": [],
    }
    assert await generator._aggregate_evaluation_scores(start, now) == {
        "count": 0,
        "mean_score": 0.0,
        "per_pair": {},
        "per_strategy": {},
        "docs": [],
    }
    assert await generator._aggregate_scheduler_results(start, now) == {"count": 0, "mean_score": 0.0}
    assert await generator._aggregate_baseline(now) == {}
    assert await generator._mean_cost_per_strategy() == {}
    assert await generator._mean_score_window(start, now) == {}
    assert await generator._aggregate_schedule_health() == {
        "paused": 0,
        "open_breakers": 0,
        "at_failure_threshold": 0,
    }

    failing = NightlyReportGenerator(
        _DB(
            {
                STRATEGY_RUN_COLLECTION_NAME: _Collection(fail_find=True),
                "evaluation_results": _Collection(fail_find=True),
                "scheduler_results": _Collection(fail_find=True),
                "scheduler_reports": _Collection(fail_find=True),
                "runtime_model_profiles": _Collection(fail_find=True),
            }
        ),
        output_dir=tmp_path,
    )
    assert (await failing._aggregate_runs(start, now))["total"] == 0
    assert (await failing._aggregate_evaluation_scores(start, now))["count"] == 0
    assert await failing._aggregate_scheduler_results(start, now) == {"count": 0, "mean_score": 0.0}
    assert await failing._aggregate_baseline(now) == {}
    assert await failing._mean_cost_per_strategy() == {}
    assert await failing._mean_score_window(start, now) == {}
    assert await failing._aggregate_schedule_health() == {
        "paused": 0,
        "open_breakers": 0,
        "at_failure_threshold": 0,
    }

    import backend.agent.strategy.scheduler_store as scheduler_store_mod

    class RaisingSchedulerStore:
        def __init__(self, _db: Any) -> None:
            pass

        async def list(self) -> list[Any]:
            raise RuntimeError("schedule store failed")

    monkeypatch.setattr(scheduler_store_mod, "MongoSchedulerStore", RaisingSchedulerStore)
    assert await NightlyReportGenerator(_DB(), output_dir=tmp_path)._aggregate_schedule_health() == {
        "paused": 0,
        "open_breakers": 0,
        "at_failure_threshold": 0,
    }

    db = _DB(
        {
            STRATEGY_RUN_COLLECTION_NAME: _Collection(
                [
                    {
                        "strategy_id": "s1",
                        "capability_id": "qa",
                        "status": "failed",
                        "duration_ms": 12,
                        "halt_reason": "fatal error",
                        "node_outputs": ["bad", {"node_id": "n1", "status": "error"}],
                    },
                    {"strategy_id": "s2", "status": "success", "duration_ms": "bad"},
                ]
            ),
            "evaluation_results": _Collection(
                [
                    {
                        "strategy_id": "s1",
                        "capability_id": "qa",
                        "test_case_id": 123,
                        "result": {
                            "composite_score": "bad",
                            "dimension_scores": [
                                {"dimension_id": "citation_quality", "score": 0.66}
                            ],
                        },
                        "metrics": {"citation_accuracy": 0.8},
                        "judge_variance": 0.3,
                    },
                    {
                        "strategy_id": "s1",
                        "capability_id": "qa",
                        "test_case_id": "case-a",
                        "dataset_id": "data-a",
                        "result": {"composite_score": 0.4},
                        "judge_variance": 0.4,
                    },
                ]
            ),
            "scheduler_results": _Collection([{"composite_score": "bad"}]),
            "scheduler_reports": _Collection(
                [
                    {
                        "capability_id": "qa",
                        "candidate_rankings": [
                            "bad",
                            {"strategy_id": 123, "score": 0.9},
                            {"strategy_id": "s1", "score": "bad"},
                            {"strategy_id": "s1", "score": 0.9},
                        ],
                    }
                ]
            ),
            "runtime_model_profiles": _Collection([{}, {"id": "x"}]),
        }
    )
    populated = NightlyReportGenerator(db, output_dir=tmp_path)
    assert (await populated._aggregate_runs(start, now))["top_failing_nodes"][0]["node_id"] == "n1"
    eval_stats = await populated._aggregate_evaluation_scores(start, now)
    assert eval_stats["count"] == 1
    assert await populated._aggregate_scheduler_results(start, now) == {"count": 0, "mean_score": 0.0}
    assert await populated._aggregate_baseline(now) == {("s1", "qa"): 0.9}
    assert await populated._mean_cost_per_strategy() == {}
    assert (await populated._latency_cost_table({"s1": [], "s2": [100.0]}))[0]["strategy_id"] == "s2"
    citation_rows = populated._citation_quality_table(eval_stats["docs"])
    assert citation_rows[0]["citation_coverage_mean"] == 0.66
    weak_cases = populated._cases_needing_better_gold_labels(eval_stats["docs"])
    assert weak_cases == [
        {
            "dataset_id": "data-a",
            "case_id": "case-a",
            "reason": "all_strategies_below_0.5,high_judge_variance",
        }
    ]

    class RaisingPromotionEvaluator:
        async def evaluate_all(self, **_: Any) -> list[Any]:
            raise RuntimeError("promotion down")

    class JsonFailGenerator(NightlyReportGenerator):
        def _write_json(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("json failed")

    monkeypatch.setattr(report_mod, "PromotionEvaluator", RaisingPromotionEvaluator)
    generated = await JsonFailGenerator(None, output_dir=tmp_path).generate(run_date=now)
    assert generated.json_path is None

    summary = NightlyReportSummary(
        report_date="2026-05-01",
        window_start=start,
        window_end=now,
        markdown_path=tmp_path / "r.md",
        json_path=tmp_path / "r.json",
    )
    persist_db = _DB({RUN_REPORT_COLLECTION_NAME: _Collection(fail_index=True, fail_replace=True)})
    await NightlyReportGenerator(persist_db, output_dir=tmp_path)._persist_summary(summary)
    await NightlyReportGenerator(None, output_dir=tmp_path)._persist_summary(summary)


@pytest.mark.asyncio
async def test_checkpoint_manager_and_spec_loader_edges(tmp_path: Path) -> None:
    """Cover no-op/error checkpoint paths and loader parse failures."""
    state = StrategyRunState(query="q")
    no_db = CheckpointManager(None)
    assert await no_db.save_checkpoint(state, 0) is False
    assert await no_db.load_checkpoint("missing") is None
    assert await no_db.delete_checkpoint("missing") is False
    assert await no_db.list_pending_checkpoints() == []
    assert no_db.restore_state_from_checkpoint({"state_snapshot": state.model_dump(mode="json")}).query == "q"

    failing_db = _DB({"strategy_run_checkpoints": _Collection(fail_find=True, fail_replace=True)})
    manager = CheckpointManager(failing_db)
    assert await manager.save_checkpoint(state, 1) is False
    assert await manager.load_checkpoint(state.run_id) is None
    assert await manager.delete_checkpoint(state.run_id) is False
    assert await manager.list_pending_checkpoints() == []

    class LegacyState:
        run_id = "legacy-run"
        trace_id = "legacy-trace"

        def dict(self) -> dict[str, Any]:
            return {"query": "legacy"}

    assert await CheckpointManager(_DB()).save_checkpoint(LegacyState(), 0) is True

    with pytest.raises(SpecLoaderError):
        spec_loader_mod.load_spec_from_yaml(str(tmp_path / "missing.yaml"))
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("[", encoding="utf-8")
    with pytest.raises(SpecLoaderError):
        spec_loader_mod.load_spec_from_yaml(str(bad_yaml))
    scalar_yaml = tmp_path / "scalar.yaml"
    scalar_yaml.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(SpecLoaderError):
        spec_loader_mod.load_spec_from_yaml(str(scalar_yaml))
    invalid_spec = tmp_path / "invalid.yaml"
    invalid_spec.write_text("strategy_id: x\n", encoding="utf-8")
    with pytest.raises(SpecLoaderError):
        spec_loader_mod.load_spec_from_yaml(str(invalid_spec))
    assert spec_loader_mod.load_all_specs(str(tmp_path / "no-such-dir")) == []
    mixed = tmp_path / "specs"
    mixed.mkdir()
    (mixed / "skip.txt").write_text("ignored", encoding="utf-8")
    (mixed / "bad.yaml").write_text("strategy_id: x\n", encoding="utf-8")
    assert spec_loader_mod.load_all_specs(str(mixed)) == []

    bad_runtime_spec = _spec().model_copy(
        update={
            "graph": StrategyGraph(
                nodes=[StrategyNode(node_id="a", node_type="normalize_query")],
                edges=[StrategyEdge(from_node="a", to_node="missing")],
                entry_node="a",
                terminal_nodes=["a"],
            ),
            "budgets": StrategyBudgets(latency_target_ms=10, latency_hard_limit_ms=1),
        }
    )
    validation_errors = spec_loader_mod.validate_spec(bad_runtime_spec)
    assert any("Graph validation failed" in err for err in validation_errors)
    assert any("latency_target_ms" in err for err in validation_errors)

    memory_store = InMemorySpecStore({"empty": [], "deleteme": [_snapshot(_spec("deleteme"))]})
    listed = await memory_store.list()
    assert len(listed) == 1
    assert listed[0]["spec_data"]["strategy_id"] == "deleteme"
    assert await memory_store.delete("deleteme") is True

    mongo_store = MongoSpecStore(_DB())
    mongo_store._snapshots_hydrated = True
    assert await mongo_store.ensure_loaded() is None
    snap = _snapshot(_spec("mongo-spec"))
    await mongo_store.upsert(snap)
    replacement = {**snap, "version_counter": 2}
    await mongo_store.upsert(replacement)
    assert mongo_store.snapshots["mongo-spec"][-1]["version_counter"] == 2
    assert await mongo_store.delete("mongo-spec", version="1.0.0") is True
    mongo_store.snapshots["mongo-spec"] = [snap]
    assert await mongo_store.delete("mongo-spec") is True
    with pytest.raises(ValueError):
        MongoSpecStore._snapshot_to_doc({"spec_data": {}, "version": "1", "version_counter": 1, "status": "draft"})


class _SuccessExecutor(NodeExecutor):
    async def execute(self, node: StrategyNode, state: StrategyRunState) -> NodeOutput:
        return NodeOutput(node_id=node.node_id, node_type=node.node_type, output_data="ok")


@pytest.mark.asyncio
async def test_strategy_runner_error_checkpoint_and_merge_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover StrategyRunner defensive execution branches."""
    registry = NodeRegistry()
    registry.register("normalize_query", _SuccessExecutor)
    spec = _spec()
    context = BusinessContext(capability_id="qa", tenant_id="tenant")

    class BadMongoTrace:
        def __init__(self, _db: Any) -> None:
            raise RuntimeError("trace unavailable")

    monkeypatch.setattr("backend.agent.strategy.strategy_runner.MongoRunTraceStore", BadMongoTrace)
    assert StrategyRunner(registry, db=_DB()).run_trace_store is None

    class DeleteFailCheckpoint:
        async def delete_checkpoint(self, _run_id: str) -> None:
            raise RuntimeError("delete failed")

        async def save_checkpoint(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    result = await StrategyRunner(
        registry,
        checkpoint_manager=DeleteFailCheckpoint(),
    ).run(spec, context, query="q")
    assert result.halt_reason is None

    class ExecutionInitFailRunner(StrategyRunner):
        def _init_state(self, *_args: Any, **_kwargs: Any) -> StrategyRunState:
            raise StrategyExecutionError("init failed")

    failed = await ExecutionInitFailRunner(registry).run(spec, context, query="q")
    assert failed.success is False
    assert failed.halt_reason.startswith("execution_error")

    class UnexpectedInitFailRunner(StrategyRunner):
        def _init_state(self, *_args: Any, **_kwargs: Any) -> StrategyRunState:
            raise RuntimeError("init exploded")

    unexpected = await UnexpectedInitFailRunner(registry).run(spec, context, query="q")
    assert unexpected.success is False
    assert unexpected.halt_reason.startswith("unexpected_error")

    invalid_graph = StrategyGraph(
        nodes=[StrategyNode(node_id="a", node_type="normalize_query")],
        edges=[StrategyEdge(from_node="a", to_node="missing")],
        entry_node="a",
        terminal_nodes=["a"],
    )
    with pytest.raises(StrategyExecutionError):
        StrategyRunner(registry)._compile_and_validate(invalid_graph)

    compiled = compile_graph(spec.graph)
    duplicate_state = StrategyRunState(query="q")
    duplicate_state.node_outputs["n"] = NodeOutput(node_id="n", node_type="normalize_query")
    halt = await StrategyRunner(registry)._execute_levels(compiled, duplicate_state, StrategyBudgets())
    assert halt.startswith("state_violation")

    class SaveFailCheckpoint:
        async def save_checkpoint(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("save failed")

    save_state = StrategyRunState(query="q")
    assert await StrategyRunner(registry, checkpoint_manager=SaveFailCheckpoint())._execute_levels(
        compiled, save_state, StrategyBudgets()
    ) is None

    conditional_graph = StrategyGraph(
        nodes=[
            StrategyNode(node_id="a", node_type="normalize_query"),
            StrategyNode(node_id="b", node_type="normalize_query"),
        ],
        edges=[StrategyEdge(from_node="a", to_node="b", condition="missing + 1")],
        entry_node="a",
        terminal_nodes=["b"],
    )
    conditional = compile_graph(conditional_graph)
    condition_state = StrategyRunState(query="q")
    condition_state.node_outputs["a"] = NodeOutput(node_id="a", node_type="normalize_query")
    assert StrategyRunner(registry)._is_node_reachable("b", conditional, condition_state) is False

    false_graph = StrategyGraph(
        nodes=[
            StrategyNode(node_id="a", node_type="normalize_query"),
            StrategyNode(node_id="b", node_type="normalize_query"),
        ],
        edges=[StrategyEdge(from_node="a", to_node="b", condition="False")],
        entry_node="a",
        terminal_nodes=["b"],
    )
    false_compiled = compile_graph(false_graph)
    assert StrategyRunner(registry)._is_node_reachable("b", false_compiled, condition_state) is False

    route_state = StrategyRunState(query="q")
    synth = SynthesisResult(
        text="answer",
        citations=[CitationRef(source_id="src", document_title="Doc")],
        language="en",
        model_used="m",
    )
    validation = ValidationResult(
        validator_id="validator",
        passed=True,
        score=0.8,
        issues=[ValidationIssue(issue_type="note", severity="warning", message="ok")],
    )
    runner = StrategyRunner(registry)
    runner._route_output_to_state(route_state, NodeOutput(node_id="s", node_type="synthesize", output_data=synth))
    runner._route_output_to_state(
        route_state,
        NodeOutput(node_id="v", node_type="validate_contract", output_data=validation),
    )
    assert route_state.synthesis_result is synth
    assert route_state.validation_results == [validation]


@pytest.mark.asyncio
async def test_adaptive_selector_storage_scoring_and_cache_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover selector fallback, scoring, telemetry, and helper branches."""

    class Store:
        async def list(self, **_: Any) -> list[dict[str, Any]]:
            return [_snapshot(_spec("slow", latency_target_ms=9000)), {"spec_data": "bad"}]

    selector = StrategySpecSelector(store=Store(), legacy_adapter=SimpleNamespace(get_spec=lambda: None))
    assert await selector._resolve_from_store(
        capability_id="qa",
        tenant_id="tenant",
        agent_mode="fast",
        privacy_mode="standard",
    ) is None

    bad_legacy = StrategySpecSelector(store=None, legacy_adapter=SimpleNamespace(get_spec=lambda: (_ for _ in ()).throw(RuntimeError("legacy"))))
    assert bad_legacy._resolve_from_legacy() is None
    db_selector = StrategySpecSelector(db=SimpleNamespace(db={"db": "handle"}))
    assert db_selector.store is not None
    selector._cache[("a", "t")] = selector_mod._CacheEntry(_spec("a"), 9999999999, False)
    selector._cache[("b", "t")] = selector_mod._CacheEntry(_spec("b"), 9999999999, False)
    assert selector.clear_cache(capability_id="a") == 1
    selector.invalidate_cache()

    context = BusinessContext(capability_id="qa", matter_id="matter")
    assert FastPathRules(eligible_capabilities=frozenset()).evaluate(
        business_context=context,
        source_policy={"allow_web": False},
        capability_id="legal_hearing_questions",
        top_retrieval_score=0.95,
    ) is False
    assert FastPathRules().evaluate(
        business_context=context,
        source_policy={"allow_web": False},
        capability_id="not_allowed_summary",
        top_retrieval_score=0.95,
    ) is False
    assert FastPathRules().evaluate(
        business_context=context,
        source_policy=None,
        capability_id="legal_hearing_questions",
        top_retrieval_score=0.95,
    ) is False
    with pytest.raises(ValueError):
        AdaptiveSelector(base_selector=None)

    class BaseSelector:
        def __init__(self, candidates: list[StrategySpec]) -> None:
            self.candidates = candidates
            self.cache_cleared = 0

        async def list_active_for_capability(self, _capability_id: str | None) -> list[StrategySpec]:
            return self.candidates

        async def select(self, **_: Any) -> StrategySpec:
            return _spec("fallback")

        def rank_candidates(self, candidates: list[StrategySpec], *, tenant_id: str | None) -> StrategySpec:
            return sorted(candidates, key=lambda spec: spec.strategy_id)[0]

        def clear_cache(self, **_: Any) -> int:
            self.cache_cleared += 1
            return 1

    assert (await AdaptiveSelector(base_selector=BaseSelector([])).select(capability_id="qa")).strategy_id == "fallback"

    disqualified = _spec("web", model_roles={"synth": "model-a"}).model_copy(
        update={"source_policy": SourcePolicy(allow_web=True)}
    )
    assert (
        await AdaptiveSelector(base_selector=BaseSelector([disqualified, disqualified])).select(
            capability_id="qa",
            privacy_mode="strict",
        )
    ).strategy_id == "fallback"

    class ResourceCollector:
        async def latest(self) -> Any:
            raise RuntimeError("snapshot failed")

    adaptive = AdaptiveSelector(base_selector=BaseSelector([_spec("fast", display_name="Fast Variant"), _spec("slow")]))
    monkeypatch.setattr(selector_mod, "emit_trace_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("trace failed")))
    winner = await adaptive.select(capability_id="qa", tenant_id="recallhub", top_retrieval_score=0.9)
    assert winner.strategy_id in {"fast", "slow"}
    adaptive._cache[("qa", "recallhub", "standard")] = selector_mod._CacheEntry(winner, 9999999999, False)
    assert adaptive.clear_cache(capability_id="qa", tenant_id="missing") == 1
    adaptive._cache[("other", "recallhub", "standard")] = selector_mod._CacheEntry(winner, 9999999999, False)
    adaptive._cache[("qa", "recallhub", "standard")] = selector_mod._CacheEntry(winner, 9999999999, False)
    assert adaptive.clear_cache(capability_id="qa", tenant_id="recallhub") == 2
    adaptive._cache[("qa", "recallhub", "standard")] = selector_mod._CacheEntry(winner, 9999999999, False)
    adaptive.invalidate_cache()

    await adaptive._persist_decision(
        capability_id="qa",
        tenant_id="recallhub",
        privacy_mode="standard",
        agent_mode="auto",
        winner=winner,
        scored=None,
        fast_path_eligible=False,
        chosen_via_cache=False,
    )

    assert await AdaptiveSelector(base_selector=BaseSelector([]), resource_collector=ResourceCollector())._fetch_resource_snapshot() is None

    quality_db = _DB(
        {
            "evaluation_results": _Collection(
                [
                    {"strategy_id": 123, "result": {"composite_score": 0.1}},
                    {"strategy_id": "s1", "result": {"composite_score": 0.8}},
                    {"strategy_id": "s1", "composite_score": 0.6},
                    {"strategy_id": "s2", "result": {"composite_score": "bad"}},
                ]
            )
        }
    )
    quality = await AdaptiveSelector(base_selector=BaseSelector([]), evaluation_results_db=quality_db)._fetch_quality_scores("qa")
    assert quality == {"s1": 0.7}
    assert await AdaptiveSelector(
        base_selector=BaseSelector([]),
        evaluation_results_db=_DB({"evaluation_results": _Collection(fail_find=True)}),
    )._fetch_quality_scores("qa") == {}

    class ProfileStore:
        def __init__(self, profiles: list[Any] | None = None, fail: bool = False) -> None:
            self.profiles = profiles or []
            self.fail = fail

        async def list_recent(self, **_: Any) -> list[Any]:
            if self.fail:
                raise RuntimeError("profiles failed")
            return self.profiles

    adaptive_profiles = AdaptiveSelector(base_selector=BaseSelector([]), runtime_profile_store=ProfileStore(fail=True))
    assert await adaptive_profiles._fetch_latency_signal(_spec("model", model_roles={"synth": "model-a"})) is None
    assert await AdaptiveSelector(base_selector=BaseSelector([]), runtime_profile_store=ProfileStore())._fetch_latency_signal(
        _spec("model", model_roles={"synth": "model-a"})
    ) is None
    assert await AdaptiveSelector(
        base_selector=BaseSelector([]),
        runtime_profile_store=ProfileStore([SimpleNamespace(tokens_per_second=0)]),
    )._fetch_latency_signal(_spec("model", model_roles={"synth": "model-a"})) is None
    assert await AdaptiveSelector(
        base_selector=BaseSelector([]),
        runtime_profile_store=ProfileStore([SimpleNamespace(tokens_per_second=10)]),
    )._fetch_latency_signal(_spec("no-model", model_roles={})) is None

    scoring_selector = AdaptiveSelector(
        base_selector=BaseSelector([]),
        runtime_profile_store=ProfileStore([SimpleNamespace(tokens_per_second=80)]),
    )
    score, breakdown = await scoring_selector._score_candidate(
        spec=_spec("candidate", model_roles={"synth": "model-a"}, local_only=True),
        privacy_mode="standard",
        snapshot=SimpleNamespace(gpu_pct=90.0, cpu_pct=95.0, ollama_resident_models=["model-a"]),
        quality_by_strategy={"other": 0.4},
        fast_path_eligible=True,
    )
    assert score > 0
    assert breakdown["quality"]["signal"] == "missing"
    assert breakdown["resource"]["fit"] == 0.3
    assert breakdown["fast_path_bias"] == 0.05

    assert selector_mod._resolve_synth_model(_spec(model_roles={"custom": "model-b"})) == "model-b"
    assert selector_mod._looks_fast_variant(_spec("latency-fast", display_name="Plain", latency_target_ms=4000)) is True
