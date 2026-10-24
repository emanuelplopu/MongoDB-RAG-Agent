"""Tests for the activity event pipeline: src telemetry hooks, the recorder, and the instrumentation.

Covers :mod:`src.ingestion.telemetry_hooks` (no-op without a sink, file/step events, outcomes,
job binding), :class:`backend.services.activity_events.ActivityEventRecorder` (event shape,
live state machine, batching, retries, overflow) and the instrumented per-file method of the
ingestion pipeline and the chat coordinator.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from backend.services import activity_events
from backend.services.activity_events import ActivityEventRecorder, record_event
from src.ingestion import telemetry_hooks


class _Sink:
    """Collects (kind, fields) pairs like the recorder would receive them."""

    def __init__(self):
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, kind: str, fields: dict[str, Any]) -> None:
        self.events.append((kind, dict(fields)))

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events if k != "state"]

    def first(self, kind: str) -> dict[str, Any]:
        return next(f for k, f in self.events if k == kind)


@pytest.fixture
def sink():
    s = _Sink()
    telemetry_hooks.set_event_sink(s)
    yield s
    telemetry_hooks.set_event_sink(None)


class _FakeStore:
    def __init__(self, fail_inserts: int = 0):
        self.inserted: list[dict[str, Any]] = []
        self.current: dict[str, dict[str, Any]] = {}
        self.fail_inserts = fail_inserts

    async def insert_events(self, docs):
        if self.fail_inserts:
            self.fail_inserts -= 1
            raise RuntimeError("mongo down")
        self.inserted.extend(docs)
        return len(docs)

    async def upsert_activity_current(self, key, doc):
        self.current[key] = dict(doc)


# ── telemetry_hooks ──────────────────────────────────────────────────────────


class TestTelemetryHooks:
    def test_noop_without_sink(self):
        telemetry_hooks.set_event_sink(None)
        with telemetry_hooks.file_scope("/tmp/a.pdf") as trace:
            with telemetry_hooks.step("convert"):
                pass
        assert trace.steps == {}  # nothing collected, nothing emitted, no error

    def test_file_scope_emits_start_and_end_with_steps(self, sink, tmp_path):
        path = tmp_path / "Brief.PDF"
        path.write_bytes(b"x" * 1234)
        with telemetry_hooks.file_scope(str(path)):
            with telemetry_hooks.step("convert"):
                pass
            with telemetry_hooks.step("embed"):
                pass
            telemetry_hooks.note(chunks=7, pages=None)

        assert sink.kinds() == ["file.start", "file.end"]
        start = sink.first("file.start")
        assert start["file"] == {"path": str(path), "name": "Brief.PDF", "ext": "pdf", "size_bytes": 1234}
        end = sink.first("file.end")
        assert end["outcome"] == "ok"
        assert set(end["steps"]) == {"convert_ms", "embed_ms"}
        assert end["chunks"] == 7
        assert "pages" not in end  # None counts are dropped
        assert end["duration_ms"] >= 0
        # Live state: the current step was signalled and cleared at the end.
        steps_signalled = [f.get("step") for k, f in sink.events if k == "state" and "step" in f]
        assert "convert" in steps_signalled and steps_signalled[-1] is None

    def test_error_outcome_reraises(self, sink):
        with pytest.raises(ValueError):
            with telemetry_hooks.file_scope("/x/broken.docx"):
                with telemetry_hooks.step("convert"):
                    raise ValueError("corrupt zip")
        end = sink.first("file.end")
        assert end["outcome"] == "error"
        assert end["error_type"] == "ValueError"
        assert end["error"] == "corrupt zip"
        assert "convert_ms" in end["steps"]  # the failing step is still timed

    def test_cancelled_outcome(self, sink):
        async def run():
            with telemetry_hooks.file_scope("/x/slow.pdf"):
                await asyncio.sleep(10)

        async def main():
            task = asyncio.create_task(run())
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(main())
        end = sink.first("file.end")
        assert end["outcome"] == "cancelled"
        assert "error" not in end

    def test_explicit_outcome(self, sink):
        with telemetry_hooks.file_scope("/x/empty.txt") as trace:
            trace.outcome = "empty"
        assert sink.first("file.end")["outcome"] == "empty"

    def test_bound_job_propagates_to_tasks(self, sink):
        async def child():
            telemetry_hooks.emit("probe")

        async def main():
            token = telemetry_hooks.bind_job("job-42")
            try:
                await asyncio.gather(asyncio.create_task(child()), asyncio.create_task(child()))
            finally:
                telemetry_hooks.reset_job(token)
            telemetry_hooks.emit("after")

        asyncio.run(main())
        jobs = [f["job_id"] for k, f in sink.events]
        assert jobs == ["job-42", "job-42", None]

    def test_sink_failure_never_raises(self):
        def broken(kind, fields):
            raise RuntimeError("boom")

        telemetry_hooks.set_event_sink(broken)
        try:
            with telemetry_hooks.file_scope("/x/a.pdf"):
                with telemetry_hooks.step("chunk"):
                    pass
        finally:
            telemetry_hooks.set_event_sink(None)


# ── recorder ─────────────────────────────────────────────────────────────────


class TestActivityEventRecorder:
    def test_event_shape(self):
        store = _FakeStore()
        rec = ActivityEventRecorder(store, tenant="quellex", source="api", host="H")
        rec.emit("file.end", {"job_id": "j1", "outcome": "ok", "duration_ms": 5.0})
        asyncio.run(rec.flush())

        doc = store.inserted[0]
        assert doc["meta"] == {"kind": "file.end", "source": "api", "tenant": "quellex", "job_id": "j1"}
        assert doc["outcome"] == "ok"
        assert "job_id" not in doc
        assert doc["ts"].tzinfo is not None

    def test_state_machine_and_activity_current(self):
        store = _FakeStore()
        rec = ActivityEventRecorder(store, tenant="quellex", source="api", host="H")
        rec.job_status("j1", "RUNNING")
        rec.job_phase("j1", "processing")
        rec.job_progress("j1", 3, 10)
        rec.emit("file.start", {"job_id": "j1", "file": {"path": "/a.pdf"}})
        rec.emit("state", {"step": "embed", "job_id": "ignored"})
        asyncio.run(rec.flush())

        current = store.current["quellex:api"]
        assert current["job_id"] == "j1"
        assert current["job_status"] == "running"  # lower-cased
        assert current["phase"] == "processing"
        assert current["step"] == "embed"
        assert current["current_file"] == "/a.pdf"
        assert (current["processed_files"], current["total_files"]) == (3, 10)
        assert current["updated_at"].tzinfo is not None
        # The state signal is not an event.
        assert [d["meta"]["kind"] for d in store.inserted] == ["job.status", "job.phase", "file.start"]

        rec.job_status("j1", "completed")
        asyncio.run(rec.flush())
        cleared = store.current["quellex:api"]
        assert cleared["job_id"] is None and cleared["phase"] is None and cleared["current_file"] is None

    def test_chat_marks_activity(self):
        store = _FakeStore()
        rec = ActivityEventRecorder(store, tenant="quellex", source="api")
        rec.emit("chat.request", {"session_id": "s1", "duration_ms": 1200.0})
        asyncio.run(rec.flush())
        assert store.current["quellex:api"]["chat_last_active_at"] is not None

    def test_failed_insert_keeps_events_for_next_flush(self):
        store = _FakeStore(fail_inserts=1)
        rec = ActivityEventRecorder(store, tenant="t", source="api")
        rec.emit("job.phase", {"job_id": "j", "phase": "discovering"})
        assert asyncio.run(rec.flush()) == 0
        assert rec.pending() == 1
        assert asyncio.run(rec.flush()) == 1
        assert rec.pending() == 0

    def test_queue_overflow_drops_oldest(self):
        rec = ActivityEventRecorder(_FakeStore(), tenant="t", source="api", max_queue=3)
        for i in range(5):
            rec.emit("file.end", {"i": i})
        assert rec.pending() == 3
        assert rec.dropped == 2

    def test_batches_respect_max_batch(self):
        store = _FakeStore()
        rec = ActivityEventRecorder(store, tenant="t", source="api", max_batch=2)
        for i in range(5):
            rec.emit("file.end", {"i": i})
        assert asyncio.run(rec.flush()) == 5
        assert [d["i"] for d in store.inserted] == [0, 1, 2, 3, 4]

    def test_idle_state_not_rewritten(self):
        store = _FakeStore()
        rec = ActivityEventRecorder(store, tenant="t", source="api", refresh_interval=0.0)
        asyncio.run(rec.flush())  # initial (dirty) write
        store.current.clear()
        asyncio.run(rec.flush())  # idle and clean: nothing to write
        assert store.current == {}

    def test_active_state_refreshed(self):
        store = _FakeStore()
        rec = ActivityEventRecorder(store, tenant="t", source="api", refresh_interval=0.0)
        rec.job_status("j1", "running")
        asyncio.run(rec.flush())
        store.current.clear()
        asyncio.run(rec.flush())  # still running: refreshed so the host agent sees it as fresh
        assert store.current["t:api"]["job_id"] == "j1"

    def test_idle_heartbeat_rewrites_state_after_the_interval(self):
        store = _FakeStore()
        rec = ActivityEventRecorder(store, tenant="shared", source="worker", flush_interval=0.0, heartbeat_interval=0.0)
        asyncio.run(rec.flush())  # initial write
        first = store.current["shared:worker"]["updated_at"]
        store.current.clear()
        asyncio.run(rec.flush())  # idle, clean, but the heartbeat is due: written again with a fresh updated_at
        assert store.current["shared:worker"]["job_id"] is None
        assert store.current["shared:worker"]["updated_at"] >= first

    def test_heartbeat_interval_never_shorter_than_the_flush_interval(self):
        rec = ActivityEventRecorder(_FakeStore(), tenant="t", source="api", flush_interval=2.0, heartbeat_interval=0.5)
        assert rec._heartbeat_interval == 2.0

    def test_start_registers_sink_and_stop_flushes(self):
        store = _FakeStore()

        async def main():
            rec = ActivityEventRecorder(store, tenant="t", source="worker", flush_interval=60)
            await rec.start()
            try:
                telemetry_hooks.emit("file.start", file={"path": "/b.pdf"})
            finally:
                await rec.stop()
            return rec

        asyncio.run(main())
        assert [d["meta"]["kind"] for d in store.inserted] == ["file.start"]
        assert telemetry_hooks._sink is None  # unregistered on stop

    def test_record_event_without_recorder_is_noop(self):
        activity_events.set_activity_recorder(None)
        record_event("chat.request", session_id="x")  # must not raise


class TestJobTenantAttribution:
    """The shared worker labels each job's activity with the job's tenant, not its own."""

    def test_bound_job_events_and_state_carry_the_job_tenant(self):
        store = _FakeStore()
        rec = ActivityEventRecorder(store, tenant="shared", source="worker", host="H")
        rec.bind_job_tenant("j1", "quellex")
        rec.job_status("j1", "running")
        rec.emit("file.start", {"job_id": None, "file": {"path": "/a.pdf"}})  # pipeline event without an id
        asyncio.run(rec.flush())

        assert [d["meta"]["tenant"] for d in store.inserted] == ["quellex", "quellex"]
        assert rec.key == "shared:worker"  # one live-state document per process, whatever the job
        assert store.current["shared:worker"]["tenant"] == "quellex"

    def test_release_restores_the_process_tenant(self):
        store = _FakeStore()
        rec = ActivityEventRecorder(store, tenant="shared", source="worker")
        rec.bind_job_tenant("j1", "recallhub")
        rec.job_status("j1", "completed")
        rec.release_job_tenant("j1")
        rec.emit("job.phase", {"job_id": "j2", "phase": "discovering"})  # an unbound job
        asyncio.run(rec.flush())

        assert [d["meta"]["tenant"] for d in store.inserted] == ["recallhub", "shared"]
        assert store.current["shared:worker"]["tenant"] == "shared"

    def test_empty_tenant_keeps_the_default(self):
        rec = ActivityEventRecorder(_FakeStore(), tenant="quellex", source="api")
        rec.bind_job_tenant("j1", "")
        assert rec.tenant_for("j1") == "quellex"
        assert rec.state()["tenant"] == "quellex"


class TestWorkerTenant:
    def test_default_is_shared_not_the_settings_default(self, monkeypatch):
        from backend.workers.ingestion_worker import worker_default_tenant

        monkeypatch.delenv("TENANT_ID", raising=False)
        assert worker_default_tenant() == "shared"
        monkeypatch.setenv("TENANT_ID", " quellex ")
        assert worker_default_tenant() == "quellex"

    def test_job_tenant_prefers_the_recorded_tenant(self, monkeypatch):
        from backend.workers.ingestion_worker import job_tenant

        monkeypatch.delenv("TENANT_ID", raising=False)
        assert job_tenant({"job_id": "j", "tenant": "quellex"}) == "quellex"
        assert job_tenant({"job_id": "j", "tenant": "  "}) == "shared"
        assert job_tenant({"job_id": "j"}) == "shared"

    def test_api_records_its_tenant_on_saved_jobs(self, monkeypatch):
        from backend.core.config import settings
        from backend.routers import ingestion

        saved: list[dict[str, Any]] = []

        class _Jobs:
            async def replace_one(self, _filter, doc, upsert=False):
                saved.append(doc)

        db = SimpleNamespace(db={ingestion.INGESTION_JOBS_COLLECTION: _Jobs()})
        monkeypatch.setattr(settings, "tenant_id", "quellex")
        asyncio.run(ingestion.save_job_to_db(db, {"job_id": "j1", "status": "RUNNING"}))
        asyncio.run(ingestion.save_job_to_db(db, {"job_id": "j2", "tenant": "recallhub"}))

        assert [(d["_id"], d["tenant"]) for d in saved] == [("j1", "quellex"), ("j2", "recallhub")]


# ── instrumented code paths ──────────────────────────────────────────────────


class TestPipelineInstrumentation:
    """The real _ingest_single_document emits one file.start/file.end with every step timed."""

    def _pipeline(self, monkeypatch, chunks: list):
        from concurrent.futures import ThreadPoolExecutor

        from src.ingestion import ingest as ingest_module

        monkeypatch.setattr(ingest_module, "repair_docling_document", lambda doc: 0)
        monkeypatch.setattr(ingest_module, "repair_ocr_text", lambda text: text)

        pipeline = ingest_module.DocumentIngestionPipeline.__new__(ingest_module.DocumentIngestionPipeline)
        executor = ThreadPoolExecutor(max_workers=1)
        pipeline.get_executor = lambda: executor
        pipeline._read_document = lambda path: ("Title line\nBody text.", SimpleNamespace(pages={1: object(), 2: object()}))
        pipeline.documents_folders = []
        pipeline._extract_title = lambda content, path: "Title"
        pipeline._extract_document_metadata = lambda content, path: {}

        async def chunk_document(**kwargs):
            return chunks

        async def embed_chunks(items):
            return items

        async def save(*args):
            return "doc-1"

        pipeline.chunker = SimpleNamespace(chunk_document=chunk_document)
        pipeline.embedder = SimpleNamespace(embed_chunks=embed_chunks)
        pipeline._save_to_mongodb = save
        return pipeline

    def test_successful_file(self, sink, monkeypatch, tmp_path):
        path = tmp_path / "a.pdf"
        path.write_bytes(b"%PDF")
        pipeline = self._pipeline(monkeypatch, chunks=[object(), object(), object()])

        result = asyncio.run(pipeline._ingest_single_document(str(path), content_hash=None))

        assert result.chunks_created == 3
        end = sink.first("file.end")
        assert end["outcome"] == "ok"
        assert set(end["steps"]) == {"hash_ms", "convert_ms", "ocr_repair_ms", "chunk_ms", "embed_ms", "save_ms"}
        assert end["pages"] == 2
        assert end["chunks"] == 3
        assert end["chars"] > 0

    def test_file_without_chunks_is_empty(self, sink, monkeypatch, tmp_path):
        path = tmp_path / "blank.pdf"
        path.write_bytes(b"%PDF")
        pipeline = self._pipeline(monkeypatch, chunks=[])

        asyncio.run(pipeline._ingest_single_document(str(path), content_hash="precomputed"))

        end = sink.first("file.end")
        assert end["outcome"] == "empty"
        assert "hash_ms" not in end["steps"]  # hash was supplied
        assert "embed_ms" not in end["steps"]  # stopped after chunking


class TestEmitJobHelper:
    def test_api_helper_emits_status_value_and_phase(self):
        from backend.models.schemas import IngestionStatus
        from backend.routers.ingestion import _emit_job

        store = _FakeStore()
        rec = ActivityEventRecorder(store, tenant="t", source="api")
        activity_events.set_activity_recorder(rec)
        try:
            _emit_job("j1", status=IngestionStatus.RUNNING)
            _emit_job("j1", phase="processing", files=10)
            _emit_job(None, phase="ignored")  # no job id: nothing recorded
        finally:
            activity_events.set_activity_recorder(None)
        asyncio.run(rec.flush())

        assert [(d["meta"]["kind"], d.get("status"), d.get("phase")) for d in store.inserted] == [
            ("job.status", "running", None),
            ("job.phase", None, "processing"),
        ]
        assert store.inserted[1]["files"] == 10


class TestChatActivity:
    def test_records_ids_and_timings_only(self):
        from backend.agent.coordinator import FederatedAgent

        store = _FakeStore()
        rec = ActivityEventRecorder(store, tenant="t", source="api")
        activity_events.set_activity_recorder(rec)
        coordinator = FederatedAgent.__new__(FederatedAgent)
        coordinator.trace = SimpleNamespace(
            id="trace-1", orchestrator_duration_ms=800.0, worker_duration_ms=300.0,
            orchestrator_steps=[SimpleNamespace(phase="analyze", duration_ms=120.0)], degradations=[],
        )
        coordinator.strategy = None
        coordinator.config = SimpleNamespace(orchestrator_model="gemma4:12b")
        try:
            import time

            coordinator._record_chat_activity("s1", True, time.perf_counter())
            coordinator._record_chat_activity("s2", False, time.perf_counter(), error=RuntimeError("llm down"))
        finally:
            activity_events.set_activity_recorder(None)
        asyncio.run(rec.flush())

        ok, failed = store.inserted
        assert ok["meta"]["kind"] == "chat.request"
        assert ok["outcome"] == "ok" and ok["mode"] == "thinking" and ok["trace_id"] == "trace-1"
        assert ok["phases"] == [{"phase": "analyze", "ms": 120.0}]
        assert failed["outcome"] == "error" and failed["error_type"] == "RuntimeError"
        assert "user_message" not in ok and "response" not in ok
