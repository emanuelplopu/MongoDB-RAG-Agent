"""Edge-coverage tests for Strategy OS profiler and resource services."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pymongo.errors import OperationFailure

from backend.agent.strategy.run_trace_store import (
    InMemoryRunTraceStore,
    MongoRunTraceStore,
    RunTraceDoc,
)
from backend.agent.strategy.runtime_profile_store import (
    InMemoryRuntimeProfileStore,
    MongoRuntimeProfileStore,
    RuntimeModelProfile,
)
from backend.agent.strategy.scheduler_models import ResourceLimits
from backend.services import resource_snapshot as rs_mod
from backend.services import runtime_profiler as profiler_mod
from backend.services.resource_snapshot import ResourceSnapshot, ResourceSnapshotCollector
from backend.services.runtime_profiler import (
    ParallelWorkersConfig,
    RuntimeProfiler,
    _ResourceSampler,
    _extract_provider_cost,
    _query_nvidia_smi,
)


def test_runtime_profiler_cost_nvidia_and_sampler_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _extract_provider_cost(None) is None
    assert _extract_provider_cost({"cost_eur": True}) is None
    assert _extract_provider_cost({"cost_eur": -1}) is None
    assert _extract_provider_cost({"usage": "bad"}) is None
    assert _extract_provider_cost({"usage": {"cost_eur": 0.25}}) == 0.25
    assert _extract_provider_cost({"_hidden_params": {"response_cost": 0.5}}) == 0.5

    monkeypatch.setattr(profiler_mod.shutil, "which", lambda _name: None)
    assert _query_nvidia_smi("memory.used") is None

    monkeypatch.setattr(profiler_mod.shutil, "which", lambda _name: "nvidia-smi")

    class Completed:
        def __init__(self, returncode: int, stdout: str) -> None:
            self.returncode = returncode
            self.stdout = stdout

    monkeypatch.setattr(
        profiler_mod.subprocess,
        "run",
        lambda *args, **kwargs: Completed(1, "10\n"),
    )
    assert _query_nvidia_smi("memory.used") is None

    monkeypatch.setattr(
        profiler_mod.subprocess,
        "run",
        lambda *args, **kwargs: Completed(0, "1024\nnot-a-number\n2048\n"),
    )
    assert _query_nvidia_smi("memory.used") == [1024.0, 2048.0]

    def timeout_run(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired("nvidia-smi", 1)

    monkeypatch.setattr(profiler_mod.subprocess, "run", timeout_run)
    assert _query_nvidia_smi("memory.used") is None

    class Memory:
        rss = 2 * 1024**3

    class Process:
        def __init__(self) -> None:
            self.calls = 0

        def memory_info(self) -> Memory:
            return Memory()

        def cpu_percent(self, interval: Any = None) -> float:
            self.calls += 1
            return 25.0 if self.calls > 1 else 0.0

    monkeypatch.setattr(profiler_mod.shutil, "which", lambda _name: "nvidia-smi")
    samples = iter([[1024.0], [50.0], [1024.0], [50.0]])
    monkeypatch.setattr(profiler_mod, "_query_nvidia_smi", lambda _query: next(samples))
    sampler = _ResourceSampler(interval_s=0.001)
    sampler._psutil_process = Process()
    sampler._gpu_available = True
    sampler._sample_once()
    sampler._sample_once()
    assert sampler.ram_peak_gb == 2.0
    assert sampler.cpu_avg_pct == pytest.approx(12.5)
    assert sampler.vram_peak_gb == 1.0
    assert sampler.gpu_avg_pct == 50.0

    class BadProcess:
        def memory_info(self) -> Any:
            raise RuntimeError("rss failed")

    sampler._psutil_process = BadProcess()
    sampler._gpu_available = False
    sampler._sample_once()


@pytest.mark.asyncio
async def test_runtime_profiler_suite_and_parallel_failure_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryRuntimeProfileStore()
    profiler = RuntimeProfiler(profile_store=store, host_id="host-a", provider_invoker=None)

    async def boom_run_test(**kwargs: Any) -> Any:
        raise RuntimeError("suite boom")

    monkeypatch.setattr(profiler, "run_test", boom_run_test)
    results = await profiler.profile_model(model="m", provider="p", contexts=[500])
    assert [r.test_name for r in results] == ["tiny_classification", "parallel_workers"]
    assert all(r.success is False for r in results)
    assert "suite boom" in (results[0].error or "")
    assert "provider_invoker" in (results[1].error or "")

    async def always_fails(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("worker down")

    failing_parallel = RuntimeProfiler(
        profile_store=InMemoryRuntimeProfileStore(),
        host_id="host-a",
        provider_invoker=always_fails,
        parallel_workers=ParallelWorkersConfig(concurrency=2),
    )
    failed = await failing_parallel._run_parallel_workers(model="m", provider="p")
    assert failed.success is False
    assert "worker down" in (failed.error or "")

    outcomes: list[Any] = [
        {
            "output_tokens": 50,
            "first_token_ms": 10,
            "generation_tokens_per_second": 100.0,
            "prompt_tokens_per_second": 200.0,
            "cost_eur": 0.2,
        },
        RuntimeError("one failed"),
    ]

    async def partial_invoker(**kwargs: Any) -> dict[str, Any]:
        item = outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    partial_parallel = RuntimeProfiler(
        profile_store=InMemoryRuntimeProfileStore(),
        host_id="host-a",
        provider_invoker=partial_invoker,
        parallel_workers=ParallelWorkersConfig(concurrency=2),
    )
    partial = await partial_parallel._run_parallel_workers(model="m", provider="p")
    assert partial.success is True
    assert partial.error == "1/2 workers failed"
    assert partial.cost_eur == pytest.approx(0.2)
    assert partial.cost_source == "provider"

    class BadRegistry:
        def list_roles(self) -> dict[str, Any]:
            raise RuntimeError("registry failed")

    cost_profiler = RuntimeProfiler(
        profile_store=InMemoryRuntimeProfileStore(),
        model_role_registry=BadRegistry(),
    )
    assert cost_profiler._lookup_role_config_for_model("m") is None
    assert cost_profiler._estimate_cost_eur(
        role_config=types.SimpleNamespace(
            cost_per_million_input="bad",
            cost_per_million_output=1.0,
        ),
        prompt_tokens=10,
        completion_tokens=5,
    ) == (None, "unknown")


class _AsyncCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = list(docs)

    def sort(self, *args: Any, **kwargs: Any) -> "_AsyncCursor":
        return self

    def __aiter__(self) -> "_AsyncCursor":
        return self

    async def __anext__(self) -> dict[str, Any]:
        if not self.docs:
            raise StopAsyncIteration
        return self.docs.pop(0)


def _trace_doc(trace_id: str = "trace-a") -> RunTraceDoc:
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    return RunTraceDoc(
        trace_id=trace_id,
        run_id=f"run-{trace_id}",
        strategy_id="strategy-a",
        status="success",
        started_at=now,
        completed_at=now + timedelta(milliseconds=10),
        duration_ms=10.0,
    )


def _profile_doc(profile_id: str = "profile-a") -> RuntimeModelProfile:
    return RuntimeModelProfile(
        id=profile_id,
        host_id="host-a",
        tenant="recallhub",
        model="gemma3",
        provider="ollama",
        test_name="short_rag",
        context_tokens=2000,
        output_tokens=500,
        total_latency_ms=1000,
        tokens_per_second=500.0,
        success=True,
        created_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_resource_snapshot_index_latest_capture_and_gate_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="ttl_hours"):
        ResourceSnapshotCollector(None, ttl_hours=0)
    with pytest.raises(ValueError, match="activity_threshold"):
        ResourceSnapshotCollector(None, activity_threshold_seconds=0)

    no_db = ResourceSnapshotCollector(None, host_id="host-a")
    assert no_db.collection is None
    await no_db.ensure_indexes()
    assert await no_db.latest() is None

    class FailingCollection:
        def __init__(self) -> None:
            self.mode = "raise"

        async def create_index(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("index failed")

        async def insert_one(self, doc: dict[str, Any]) -> None:
            raise RuntimeError("insert failed")

        def find(self, query: dict[str, Any]) -> _AsyncCursor:
            if self.mode == "malformed":
                return _AsyncCursor([{"id": "bad"}])
            raise RuntimeError("find failed")

    class FailingDB:
        def __init__(self) -> None:
            self.collection = FailingCollection()

        def __getitem__(self, name: str) -> FailingCollection:
            return self.collection

    db = FailingDB()
    collector = ResourceSnapshotCollector(db, host_id="host-a", cpu_sample_interval=0.0)
    await collector.ensure_indexes()
    assert collector._indexes_ready is True

    monkeypatch.setattr(collector, "_sample_cpu_percent", AsyncMock(return_value=91.0))
    monkeypatch.setattr(collector, "_sample_memory", lambda: (15.0, 16.0, 94.0))
    monkeypatch.setattr(collector, "_sample_gpu", lambda: (89.0, 7.0, 8.0, 87.5))
    monkeypatch.setattr(collector, "_sample_ollama", AsyncMock(return_value=(["m"], 7.0)))
    monkeypatch.setattr(collector, "_count_worker_processes", lambda: 3)
    monkeypatch.setattr(collector, "_detect_interactive_users", AsyncMock(return_value=True))
    snapshot = await collector.capture()
    assert snapshot.cpu_pct == 91.0
    assert snapshot.parallel_worker_count == 3

    assert await collector.latest() is None
    db.collection.mode = "malformed"
    assert await collector.latest() is None

    safe, reasons = await collector.is_safe_to_run(
        ResourceLimits(
            max_cpu_utilization_pct=50,
            max_ram_usage_pct=50,
            max_gpu_utilization_pct=50,
            pause_if_interactive_users=True,
            pause_if_backend_chat_active=False,
        )
    )
    assert safe is False
    assert any("CPU" in reason for reason in reasons)
    assert any("RAM" in reason for reason in reasons)
    assert any("GPU" in reason for reason in reasons)
    assert any("Interactive users" in reason for reason in reasons)

    _safe, backend_reasons = await collector.is_safe_to_run(
        ResourceLimits(
            max_cpu_utilization_pct=100,
            max_ram_usage_pct=100,
            max_gpu_utilization_pct=100,
            pause_if_interactive_users=False,
            pause_if_backend_chat_active=True,
        )
    )
    assert backend_reasons == ["Recent backend /api/chat activity detected; pausing experiments"]


@pytest.mark.asyncio
async def test_resource_snapshot_sampling_process_and_activity_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BadPsutil:
        @staticmethod
        def cpu_percent(interval: float = 0.0) -> float:
            raise RuntimeError("cpu failed")

        @staticmethod
        def virtual_memory() -> Any:
            raise RuntimeError("memory failed")

        @staticmethod
        def process_iter(attrs: Any = None) -> Any:
            raise RuntimeError("process failed")

    monkeypatch.setitem(sys.modules, "psutil", BadPsutil)
    collector = ResourceSnapshotCollector(None, host_id="host-a", cpu_sample_interval=0.0)
    assert await collector._sample_cpu_percent() == 0.0
    assert collector._sample_memory() == (0.0, 0.0, 0.0)
    assert collector._count_worker_processes() == 0

    class Proc:
        def __init__(self, info: Any, fail: bool = False) -> None:
            self._info = info
            self.fail = fail

        @property
        def info(self) -> Any:
            if self.fail:
                raise RuntimeError("proc unavailable")
            return self._info

    good_psutil = types.SimpleNamespace(
        process_iter=lambda attrs=None: iter(
            [
                Proc({"cmdline": "not a list"}),
                Proc({"cmdline": []}),
                Proc({"cmdline": ["python", "scheduler_daemon.py"]}),
                Proc({}, fail=True),
            ]
        ),
        cpu_percent=lambda interval=0.0: 12.0,
        virtual_memory=lambda: types.SimpleNamespace(
            used=2 * 1024**3,
            total=4 * 1024**3,
            percent=50.0,
        ),
    )
    monkeypatch.setitem(sys.modules, "psutil", good_psutil)
    assert collector._count_worker_processes() == 1
    assert collector._sample_memory() == (2.0, 4.0, 50.0)

    monkeypatch.setattr(rs_mod.shutil, "which", lambda _name: "nvidia-smi")
    monkeypatch.setattr(
        rs_mod.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=1, stdout=""),
    )
    assert collector._sample_gpu() == (None, None, None, None)

    monkeypatch.setattr(
        rs_mod.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stdout="bad\n"),
    )
    assert collector._sample_gpu() == (None, None, None, None)

    monkeypatch.setattr(
        rs_mod.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stdout="125, 512, 0\n"),
    )
    gpu_pct, _used, _total, vram_pct = collector._sample_gpu()
    assert gpu_pct == 100.0
    assert vram_pct is None

    class FailingTracker:
        async def seconds_since_last_active(self) -> float:
            raise RuntimeError("tracker failed")

    collector._activity_tracker = FailingTracker()
    assert await collector._detect_interactive_users() is False


class _StoreCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.create_index_calls = 0
        self.raise_indexes = False
        self.raise_replace = False
        self.raise_find_one = False
        self.raise_find = False
        self.malformed = False
        self.ttl_conflict_once = False
        self.dropped: list[str] = []

    async def create_index(self, *args: Any, **kwargs: Any) -> str:
        self.create_index_calls += 1
        if self.ttl_conflict_once and kwargs.get("name"):
            self.ttl_conflict_once = False
            raise OperationFailure("index conflict", code=85)
        if self.raise_indexes:
            raise RuntimeError("index failed")
        return "idx"

    async def drop_index(self, name: str) -> None:
        self.dropped.append(name)

    async def replace_one(self, query: dict[str, Any], payload: dict[str, Any], upsert: bool = False) -> None:
        if self.raise_replace:
            raise RuntimeError("replace failed")
        key = query.get("trace_id") or query.get("id")
        self.docs[str(key)] = dict(payload)

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        if self.raise_find_one:
            raise RuntimeError("find_one failed")
        key = query.get("trace_id") or query.get("id")
        raw = self.docs.get(str(key))
        return dict(raw) if raw is not None else None

    def find(self, query: dict[str, Any]) -> _AsyncCursor:
        if self.raise_find:
            raise RuntimeError("find failed")
        if self.malformed:
            return _AsyncCursor([{"_id": "mongo", "id": "bad"}])
        return _AsyncCursor([dict(doc, _id="mongo") for doc in self.docs.values()])


class _StoreDB:
    def __init__(self, collection: _StoreCollection) -> None:
        self.collection = collection

    def __getitem__(self, name: str) -> _StoreCollection:
        return self.collection


@pytest.mark.asyncio
async def test_run_trace_store_memory_mongo_and_failure_edges() -> None:
    memory = InMemoryRunTraceStore()
    first = await memory.save(_trace_doc("same"))
    replacement = _trace_doc("same")
    replacement.status = "failed"
    await memory.save(replacement.model_dump(mode="python"))
    assert (await memory.get("same")).status == "failed"  # type: ignore[union-attr]
    recent = await memory.list_recent(
        since=datetime(2026, 5, 17, tzinfo=timezone.utc),
        until=datetime(2026, 5, 19, tzinfo=timezone.utc),
        strategy_id="strategy-a",
        status="failed",
    )
    assert recent == [replacement]

    with pytest.raises(ValueError, match="non-None"):
        MongoRunTraceStore(None)
    with pytest.raises(ValueError, match="ttl_seconds"):
        MongoRunTraceStore(_StoreDB(_StoreCollection()), ttl_seconds=0)

    collection = _StoreCollection()
    collection.raise_indexes = True
    store = MongoRunTraceStore(_StoreDB(collection))
    await store.ensure_indexes()
    await store.ensure_indexes()
    assert collection.create_index_calls == 3

    conflict_collection = _StoreCollection()
    conflict_collection.ttl_conflict_once = True
    conflict_store = MongoRunTraceStore(_StoreDB(conflict_collection))
    await conflict_store._ensure_ttl_index()
    assert conflict_collection.dropped == [conflict_store._TTL_INDEX_NAME]

    ok_collection = _StoreCollection()
    mongo = MongoRunTraceStore(_StoreDB(ok_collection))
    saved = await mongo.save(_trace_doc("mongo-trace").model_dump(mode="python"))
    assert saved.trace_id == "mongo-trace"
    assert (await mongo.get("mongo-trace")).trace_id == "mongo-trace"  # type: ignore[union-attr]
    ok_collection.malformed = True
    assert await mongo.list_recent(
        since=datetime(2026, 5, 17, tzinfo=timezone.utc),
        until=datetime(2026, 5, 19, tzinfo=timezone.utc),
    ) == []
    ok_collection.raise_find = True
    assert await mongo.list_recent(
        since=datetime(2026, 5, 17, tzinfo=timezone.utc),
        until=datetime(2026, 5, 19, tzinfo=timezone.utc),
    ) == []
    ok_collection.raise_find_one = True
    assert await mongo.get("mongo-trace") is None
    ok_collection.raise_replace = True
    with pytest.raises(RuntimeError, match="replace failed"):
        await mongo.save(_trace_doc("boom"))


@pytest.mark.asyncio
async def test_runtime_profile_store_memory_mongo_and_failure_edges() -> None:
    memory = InMemoryRuntimeProfileStore()
    await memory.save(_profile_doc("same"))
    updated = _profile_doc("same")
    updated.tokens_per_second = 999.0
    await memory.save(updated.model_dump(mode="python"))
    assert (await memory.get("same")).tokens_per_second == 999.0  # type: ignore[union-attr]
    assert await memory.list_recent(
        model="gemma3",
        since=datetime(2026, 5, 17, tzinfo=timezone.utc),
        limit=1,
    ) == [updated]
    assert await memory.list_by_test_name("short_rag", limit=1) == [updated]
    await memory.ensure_indexes()
    assert memory.all_docs == [updated]

    with pytest.raises(ValueError, match="non-None"):
        MongoRuntimeProfileStore(None)

    collection = _StoreCollection()
    collection.raise_indexes = True
    mongo = MongoRuntimeProfileStore(_StoreDB(collection))
    await mongo.ensure_indexes()
    await mongo.ensure_indexes()
    assert collection.create_index_calls == 3

    ok_collection = _StoreCollection()
    ok_mongo = MongoRuntimeProfileStore(_StoreDB(ok_collection))
    saved = await ok_mongo.save(_profile_doc("mongo-profile").model_dump(mode="python"))
    assert saved.id == "mongo-profile"
    assert (await ok_mongo.get("mongo-profile")).id == "mongo-profile"  # type: ignore[union-attr]
    ok_collection.malformed = True
    assert await ok_mongo.list_recent(
        model="gemma3",
        since=datetime(2026, 5, 17, tzinfo=timezone.utc),
    ) == []
    ok_collection.raise_find = True
    assert await ok_mongo.list_by_test_name("short_rag") == []
    ok_collection.raise_find_one = True
    assert await ok_mongo.get("mongo-profile") is None
    ok_collection.raise_replace = True
    with pytest.raises(RuntimeError, match="replace failed"):
        await ok_mongo.save(_profile_doc("boom"))
