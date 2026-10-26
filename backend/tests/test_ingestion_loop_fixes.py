"""Tests for the 2026-10-24 fixes of the concurrent ingestion loop.

Findings F1, F2 and F4 of ``docs/system-validation/INGESTION_PROMPT_TEST_LOG_2026-10-24.md``:

- F1: a timed-out conversion kept running on its pool thread while its slot went to the next
  file, so more conversions ran than slots and every file slowed down. Now a thread-mode
  timeout keeps its slot until the thread ends, and with process isolation the conversion
  worker process is killed. The per-file budget starts when the conversion starts and scales
  with the CPU share of one conversion at the run's concurrency.
- F2: per-file stats reached ``ingestion_stats`` / ``failed_documents`` only after the last
  file. They are now flushed in batches during the run and the job counters follow them.
- F4: a copy of a file still in flight was converted and stored too (identical files sort next
  to each other and started together). Copies are deferred until the first file's outcome is
  known and then skipped as duplicates.

The loop tests run the real ``_ingest_documents_concurrent`` over stubbed per-document steps.
The process tests spawn real worker processes with a sleeping target.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")
workers = pytest.importorskip("src.ingestion.conversion_workers")
from backend.services.ingestion_counters import apply_stats_counts  # noqa: E402
from backend.tests import conversion_worker_targets as targets  # noqa: E402

@pytest.fixture(autouse=True)
def _no_budget_scaling(monkeypatch: pytest.MonkeyPatch):
    """Budgets in these tests are literal seconds, whatever the box's core count."""
    monkeypatch.setattr(ingest, "conversion_budget_factor", lambda *a, **k: 1.0)
    monkeypatch.setattr(ingest, "stats_flush_config", lambda: (1000, 3600.0))


@pytest.fixture
def copies_hash_last(monkeypatch: pytest.MonkeyPatch):
    """Files named ``copy …`` finish hashing after the others, so the original claims its hash first."""
    real = ingest.compute_file_hash

    def slow_for_copies(file_path: str) -> str:
        if Path(file_path).name.startswith("copy"):
            time.sleep(0.1)
        return real(file_path)

    monkeypatch.setattr(ingest, "compute_file_hash", slow_for_copies)


@pytest.fixture
def retries(monkeypatch: pytest.MonkeyPatch):
    """Set the retry count of the global timeout config for one test."""

    def _set(count: int) -> None:
        monkeypatch.setattr(ingest.get_timeout_config(), "max_retries", count)

    return _set


def _write(tmp_path: Path, **contents: str) -> Dict[str, str]:
    """Create files named by keyword with the given contents; returns name -> path."""
    paths = {}
    for name, text in contents.items():
        file_name = name.replace("_", " ") + ".txt"
        path = tmp_path / file_name
        path.write_text(text, encoding="utf-8")
        paths[file_name] = str(path)
    return paths


class _Pipeline(ingest.DocumentIngestionPipeline):
    """Real concurrent loop over a stubbed per-document step; stats batches are kept in memory."""

    def __init__(self, delays: Optional[Dict[str, float]] = None, failing: Optional[Set[str]] = None) -> None:
        self._initialized = True
        self.db = None
        self.documents_folders: List[str] = []
        self.delays = delays or {}
        self.failing = failing or set()
        self.processed: List[str] = []
        self.flushes: List[List[Any]] = []
        self.stats: List[Any] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def _ingest_single_document(self, file_path: str, content_hash: Optional[str]):
        name = Path(file_path).name
        self.processed.append(name)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delays.get(name, 0.01))
            if name in self.failing:
                raise RuntimeError(f"cannot read {name}")
            return ingest.IngestionResult(
                document_id=f"doc-{name}", title=name, chunks_created=1, processing_time_ms=1.0, errors=[]
            )
        finally:
            self.in_flight -= 1

    async def _save_ingestion_stats(self, batch: List[Any]) -> bool:
        self.flushes.append(list(batch))
        self.stats.extend(batch)
        return True

    def stat(self, name: str) -> Any:
        return next(s for s in self.stats if s.file_name == name)


async def _run(pipe: _Pipeline, files: List[str], existing: Optional[Dict[str, str]] = None, **kwargs: Any):
    return await pipe._ingest_documents_concurrent(
        document_files=files,
        existing_hashes={} if existing is None else existing,
        incremental=True,
        max_concurrent=kwargs.pop("max_concurrent", 3),
        **kwargs,
    )


# --------------------------------------------------------------------------- F4: same-run copies


async def test_copies_first_seen_in_the_run_are_stored_once(tmp_path: Path, copies_hash_last) -> None:
    paths = _write(tmp_path, original="same bytes", copy_of_original="same bytes", other="different bytes")
    pipe = _Pipeline(delays={"original.txt": 0.4})  # still in flight when the copy's hash is known
    existing: Dict[str, str] = {}

    results, duplicates = await _run(pipe, list(paths.values()), existing)

    assert duplicates == 1
    assert sorted(pipe.processed) == ["original.txt", "other.txt"]  # the copy waited, then was skipped
    assert sorted(existing.values()) == ["original.txt", "other.txt"]  # stored hashes were added
    duplicate = next(r for r in results if r.errors and r.errors[0].startswith("Duplicate of:"))
    assert (duplicate.title, duplicate.errors[0]) == ("copy of original.txt", "Duplicate of: original.txt")
    assert len(results) == 3
    skipped = next(s for s in pipe.stats if s.error_type == "duplicate")
    assert (skipped.file_name, skipped.classification, skipped.success) == ("copy of original.txt", "normal", True)


async def test_a_copy_of_a_failed_file_is_processed_itself(tmp_path: Path, copies_hash_last) -> None:
    paths = _write(tmp_path, original="same bytes", copy_of_original="same bytes")
    pipe = _Pipeline(delays={"original.txt": 0.4}, failing={"original.txt"})
    existing: Dict[str, str] = {}

    results, duplicates = await _run(pipe, [paths["original.txt"], paths["copy of original.txt"]], existing)

    assert duplicates == 0
    assert pipe.processed == ["original.txt", "copy of original.txt"]  # deferred, then converted itself
    assert list(existing.values()) == ["copy of original.txt"]
    assert sorted(s.error_type or "ok" for s in pipe.stats) == ["error", "ok"]
    assert len(results) == 2


async def test_pre_existing_hashes_still_skip_without_conversion(tmp_path: Path) -> None:
    paths = _write(tmp_path, known="already stored")
    existing = {ingest.compute_file_hash(paths["known.txt"]): "archive/known.txt"}
    pipe = _Pipeline()

    results, duplicates = await _run(pipe, list(paths.values()), existing)

    assert (duplicates, pipe.processed) == (1, [])
    assert results[0].errors == ["Duplicate of: archive/known.txt"]


# --------------------------------------------------------------------------- F2: incremental stats


async def test_stats_are_flushed_in_batches_while_the_run_is_going(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ingest, "stats_flush_config", lambda: (2, 3600.0))
    paths = _write(tmp_path, **{f"f{i}": f"content {i}" for i in range(5)})
    flushes_seen_at_start: List[int] = []
    snapshots: List[Dict[str, int]] = []

    class _Watching(_Pipeline):
        async def _ingest_single_document(self, file_path: str, content_hash: Optional[str]):
            flushes_seen_at_start.append(len(self.flushes))
            return await super()._ingest_single_document(file_path, content_hash)

    pipe = _Watching(failing={"f3.txt"})
    await _run(pipe, list(paths.values()), max_concurrent=1, stats_callback=snapshots.append)

    assert sorted(s.file_name for s in pipe.stats) == sorted(paths)  # every file once
    assert [len(batch) for batch in pipe.flushes] == [2, 2, 1]
    assert max(flushes_seen_at_start) >= 1, "no batch was written before the last file"
    assert [s["written"] for s in snapshots] == [2, 4, 5]
    assert snapshots[-1]["error"] == 1 and snapshots[-1]["success"] == 4


async def test_stats_are_flushed_on_time_when_files_are_slow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ingest, "stats_flush_config", lambda: (1000, 1.0))
    paths = _write(tmp_path, a="1", b="2", c="3")
    pipe = _Pipeline(delays={name: 0.7 for name in paths})

    await _run(pipe, list(paths.values()), max_concurrent=1)

    assert len(pipe.stats) == 3
    assert len(pipe.flushes) >= 2, "the timer never flushed before the end of the run"


async def test_stats_of_a_cancelled_run_are_flushed(tmp_path: Path) -> None:
    paths = _write(tmp_path, first="1", second="2", third="3")
    calls = 0

    def progress(current: int, total: int, current_file: Optional[str] = None, chunks: int = 0) -> None:
        nonlocal calls
        calls += 1
        if calls >= 3:  # stop requested while the second file starts; every later check raises too
            raise asyncio.CancelledError("stop requested")

    pipe = _Pipeline()
    with pytest.raises(asyncio.CancelledError):
        await _run(pipe, list(paths.values()), max_concurrent=1, progress_callback=progress)

    assert [s.file_name for s in pipe.stats] == ["first.txt"]


def _stat(name: str, error_type: Optional[str] = None) -> Any:
    now = datetime.now()
    return ingest.IngestionFileStats(
        file_path=f"/docs/{name}", file_name=name, file_size_bytes=10, started_at=now, completed_at=now,
        processing_time_ms=1.0, chunks_created=0 if error_type else 1, success=error_type is None,
        error_type=error_type, error_message=error_type, timeout_seconds=120.0, profile_key="p", job_id="job-1",
    )


async def test_recorder_keeps_a_batch_the_database_refused() -> None:
    outcomes = [False]
    written: List[Any] = []
    snapshots: List[Dict[str, int]] = []

    async def save(batch: List[Any]) -> bool:
        ok = outcomes.pop(0) if outcomes else True
        if ok:
            written.extend(batch)
        return ok

    recorder = ingest.IngestionStatsRecorder(save, flush_every_files=100, flush_every_seconds=3600, on_flush=snapshots.append)
    recorder.add(_stat("a.pdf", "timeout"))
    assert await recorder.flush() == 0
    assert (recorder.pending, recorder.failed_flushes, snapshots) == (1, 1, [])
    recorder.add(_stat("b.pdf"))
    assert await recorder.flush() == 2
    assert [s.file_name for s in written] == ["a.pdf", "b.pdf"]
    assert snapshots == [{"timeout": 1, "no_chunks": 0, "error": 0, "image_only_pdf": 0, "duplicate": 0, "success": 1, "written": 2}]


async def test_recorder_schedules_a_flush_when_a_batch_is_full() -> None:
    written: List[Any] = []

    async def save(batch: List[Any]) -> None:  # an override returning None counts as written
        written.extend(batch)

    recorder = ingest.IngestionStatsRecorder(save, flush_every_files=2, flush_every_seconds=3600)
    recorder.add(_stat("a.pdf"))
    recorder.add(_stat("b.pdf"))
    await asyncio.sleep(0.05)
    assert len(written) == 2 and recorder.pending == 0


async def test_save_ingestion_stats_writes_both_collections_and_reports_failures() -> None:
    class _Collection:
        def __init__(self, fail: bool = False) -> None:
            self.docs: List[dict] = []
            self.fail = fail

        async def insert_many(self, docs: List[dict]) -> None:
            if self.fail:
                raise RuntimeError("connection lost")
            self.docs.extend(docs)

    stats, failed = _Collection(), _Collection()
    pipe = _Pipeline()
    pipe.db = {"ingestion_stats": stats, "failed_documents": failed}
    batch = [_stat("ok.pdf"), _stat("stuck.pdf", "timeout")]

    assert await ingest.DocumentIngestionPipeline._save_ingestion_stats(pipe, batch) is True
    assert [d["file_name"] for d in stats.docs] == ["ok.pdf", "stuck.pdf"]
    assert [(d["file_name"], d["job_id"], d["resolved"]) for d in failed.docs] == [("stuck.pdf", "job-1", False)]

    pipe.db = {"ingestion_stats": _Collection(fail=True), "failed_documents": failed}
    assert await ingest.DocumentIngestionPipeline._save_ingestion_stats(pipe, batch) is False


def test_job_counters_follow_the_written_stats() -> None:
    job_state: Dict[str, Any] = {"processed_files": 10}
    apply_stats_counts(
        job_state,
        {"timeout": 2, "no_chunks": 3, "error": 1, "image_only_pdf": 4, "duplicate": 5, "success": 7, "written": 22},
        baseline={"timeout": 1, "error": 1},  # recorded by an earlier run of a resumed job
    )
    assert job_state == {
        "processed_files": 10, "timeout_files": 3, "no_chunks_files": 3, "error_files": 2,
        "image_only_pdfs": 4, "duplicates_skipped": 5, "failed_files": 8,
    }


# --------------------------------------------------------------------------- F1: thread mode


class _ThreadPipeline(_Pipeline):
    """Converts on the real thread pool with a reader that sleeps in its thread."""

    def __init__(self, sleep_for: Dict[str, float]) -> None:
        super().__init__()
        self.sleep_for = sleep_for
        self.thread_started: List[str] = []
        self.thread_ended: List[str] = []
        self.ended_when_started: Dict[str, List[str]] = {}

    async def _ingest_single_document(self, file_path: str, content_hash: Optional[str]):
        name = Path(file_path).name
        self.processed.append(name)
        await self._convert_document(file_path)
        return ingest.IngestionResult(document_id=f"doc-{name}", title=name, chunks_created=1, processing_time_ms=1.0, errors=[])

    def _read_document(self, file_path: str):
        name = Path(file_path).name
        self.ended_when_started.setdefault(name, []).extend(self.thread_ended)
        self.thread_started.append(name)
        time.sleep(self.sleep_for.get(name, 0.0))
        self.thread_ended.append(name)
        return (f"text of {name}", None)


async def test_timed_out_thread_conversion_keeps_its_slot(tmp_path: Path, monkeypatch, retries) -> None:
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 0.2)
    retries(0)
    paths = _write(tmp_path, slow="s", fast_one="1", fast_two="2")
    pipe = _ThreadPipeline({"slow.txt": 1.0})

    started = time.monotonic()
    results, _ = await _run(pipe, [paths["slow.txt"], paths["fast one.txt"], paths["fast two.txt"]], max_concurrent=1)
    elapsed = time.monotonic() - started

    # The slot of the timed-out file stayed reserved: no other conversion ran while its thread ran
    assert pipe.thread_started == ["slow.txt", "fast one.txt", "fast two.txt"]
    assert "slow.txt" in pipe.ended_when_started["fast one.txt"]
    assert elapsed >= 1.0
    stuck = pipe.stat("slow.txt")
    assert (stuck.error_type, stuck.classification, stuck.success) == ("timeout", "timeout", False)
    assert pipe.stat("fast one.txt").success and pipe.stat("fast two.txt").success
    assert len(results) == 3


async def test_retry_of_a_timed_out_thread_conversion_waits_for_its_thread(tmp_path: Path, monkeypatch, retries) -> None:
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 0.2)
    retries(1)
    paths = _write(tmp_path, slow="s", quick="q")
    pipe = _ThreadPipeline({"slow.txt": 0.6})

    await _run(pipe, [paths["slow.txt"], paths["quick.txt"]], max_concurrent=2)

    assert pipe.thread_started.count("slow.txt") == 2  # first attempt, then the retry round
    # The retry started only after the first thread had ended: never two conversions of one slot
    assert pipe.ended_when_started["slow.txt"].count("slow.txt") == 1
    assert pipe.stat("slow.txt").error_type == "timeout"
    assert pipe.stat("quick.txt").success


async def test_budget_starts_when_the_conversion_starts(tmp_path: Path, monkeypatch, retries) -> None:
    """A file that waits for a slot/thread is not charged for the wait."""
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 0.5)
    retries(0)
    paths = _write(tmp_path, a="a", b="b")

    class _QueuedStart(_ThreadPipeline):
        async def _ingest_single_document(self, file_path: str, content_hash: Optional[str]):
            await asyncio.sleep(0.4)  # the step before the conversion (e.g. waiting for a warm worker)
            return await super()._ingest_single_document(file_path, content_hash)

    pipe = _QueuedStart({"a.txt": 0.3, "b.txt": 0.3})  # 0.4 + 0.3 > 0.5, but 0.3 < 0.5 from the start
    await _run(pipe, list(paths.values()), max_concurrent=2)

    assert all(s.success for s in pipe.stats), [(s.file_name, s.error_type) for s in pipe.stats]


async def test_a_file_that_never_starts_converting_still_times_out(tmp_path: Path, monkeypatch, retries) -> None:
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 0.2)
    retries(0)
    paths = _write(tmp_path, hung="h")
    pipe = _Pipeline(delays={"hung.txt": 5.0})  # the stub never reaches _convert_document

    started = time.monotonic()
    await _run(pipe, list(paths.values()), max_concurrent=1)

    assert time.monotonic() - started < 3.0
    assert pipe.stat("hung.txt").error_type == "timeout"


# --------------------------------------------------------------------------- F1: process isolation


def _pool(size: int, **kwargs: Any) -> Any:
    return workers.ConversionProcessPool(
        size,
        target=targets.sleepy_reader,
        init=workers.WorkerInit(apply_pipeline_config=False, warm_up=False),
        ready_timeout=kwargs.pop("ready_timeout", 120.0),
        **kwargs,
    )


async def _wait_until_dead(pool: Any, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while pool.live_pids() and time.monotonic() < deadline:
        await asyncio.sleep(0.05)


async def test_process_pool_kills_a_conversion_that_exceeds_its_budget(tmp_path: Path) -> None:
    paths = _write(tmp_path, slow="5", fast="0", bad="boom")
    pool = _pool(1)
    try:
        started: List[float] = []
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(pool.convert(paths["slow.txt"], on_started=lambda: started.append(time.monotonic())), timeout=1.0)
        assert started, "the budget never started: the worker did not take the request"
        assert pool.kills == 1
        await _wait_until_dead(pool)
        assert pool.live_pids() == []

        # The slot is free at once; the next file gets a fresh process
        t0 = time.monotonic()
        content, document = await pool.convert(paths["fast.txt"])
        assert content.startswith("slept 0.0") and document is None
        assert time.monotonic() - t0 < 60
        assert pool.starts == 2
        live = pool.live_pids()
        assert len(live) == 1 and str(live[0]) in content

        # A reader failure is reported for the file; the worker stays
        with pytest.raises(workers.ConversionWorkerError, match="cannot read bad.txt"):
            await pool.convert(paths["bad.txt"])
        assert pool.live_pids() == live and pool.starts == 2
    finally:
        pool.shutdown()
    await _wait_until_dead(pool)
    assert pool.live_pids() == []


class _ProcessPipeline(_Pipeline):
    """Converts in the worker processes (the pool is set on the instance as a run would)."""

    async def _ingest_single_document(self, file_path: str, content_hash: Optional[str]):
        name = Path(file_path).name
        self.processed.append(name)
        content, _ = await self._convert_document(file_path)
        return ingest.IngestionResult(document_id=f"doc-{name}", title=name, chunks_created=1, processing_time_ms=1.0, errors=[])


async def test_loop_with_process_isolation_frees_the_slot_of_a_timed_out_file(tmp_path: Path, monkeypatch, retries) -> None:
    monkeypatch.setattr(ingest, "calculate_file_timeout", lambda *a, **k: 1.0)
    retries(1)
    paths = _write(tmp_path, stuck="30", fast_one="0", fast_two="0.2")
    pipe = _ProcessPipeline()
    pipe._conversion_pool = _pool(2)
    try:
        started = time.monotonic()
        results, _ = await _run(pipe, [paths["stuck.txt"], paths["fast one.txt"], paths["fast two.txt"]], max_concurrent=2)
        elapsed = time.monotonic() - started
    finally:
        await pipe._stop_conversion_pool()

    assert elapsed < 60, "a killed conversion must not be waited for"
    assert pipe._conversion_pool is None
    stuck = pipe.stat("stuck.txt")
    assert (stuck.error_type, stuck.classification) == ("timeout", "timeout")
    assert pipe.processed.count("stuck.txt") == 2  # the retry round ran it once more, in a fresh process
    assert pipe.stat("fast one.txt").success and pipe.stat("fast two.txt").success
    assert len(results) == 3


# --------------------------------------------------------------------------- budgets and switches


@pytest.mark.parametrize(
    ("parallel", "threads", "cores", "expected"),
    [
        (2, 4, 23, 1.0),  # two conversions with 4 threads each: the formula's reference
        (16, 1, 23, 4.0),  # the diejuristin run: one thread per conversion
        (8, 2, 23, 2.0),
        (16, 4, 20, 3.2),  # pinned threads: oversubscribed cores
        (1, 2, 2, 2.0),  # a small box
    ],
)
def test_conversion_budget_factor(monkeypatch, parallel: int, threads: int, cores: int, expected: float) -> None:
    monkeypatch.undo()  # the autouse stub replaced the real function
    import importlib

    real = importlib.import_module("src.ingestion.ingest").conversion_budget_factor
    assert real(parallel, threads, cores) == pytest.approx(expected)


def test_calculate_file_timeout_scales_before_the_cap() -> None:
    cfg = ingest.TimeoutConfig()  # base 120 s, 30 s/MB, cap 1800 s
    plain = ingest.calculate_file_timeout(100 * 1024, file_path="/x/a.txt", config=cfg)
    scaled = ingest.calculate_file_timeout(100 * 1024, file_path="/x/a.txt", config=cfg, concurrency_factor=4.0)
    assert scaled == pytest.approx(plain * 4)
    assert ingest.calculate_file_timeout(100 * 1024, file_path="/x/a.txt", config=cfg, concurrency_factor=100.0) == cfg.max_timeout
    assert ingest.calculate_file_timeout(100 * 1024, config=cfg, concurrency_factor=0.5) == pytest.approx(plain)


def test_process_isolation_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ingest.process_isolation_enabled(False) is False
    assert ingest.process_isolation_enabled(True) is True

    class _Settings:
        ingest_process_isolation = False

    monkeypatch.setattr(ingest, "load_settings", lambda *a, **k: _Settings())
    monkeypatch.delenv("INGEST_PROCESS_ISOLATION", raising=False)
    assert ingest.process_isolation_enabled() is False  # from the settings
    monkeypatch.setenv("INGEST_PROCESS_ISOLATION", "true")
    assert ingest.process_isolation_enabled() is True  # the environment wins

    def broken(*args: Any, **kwargs: Any):
        raise RuntimeError("no MONGODB_URI")

    monkeypatch.setattr(ingest, "load_settings", broken)
    monkeypatch.delenv("INGEST_PROCESS_ISOLATION")
    assert ingest.process_isolation_enabled() is ingest.DEFAULT_INGEST_PROCESS_ISOLATION is True


def test_stats_flush_config_reads_environment_then_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()
    import importlib

    real = importlib.import_module("src.ingestion.ingest").stats_flush_config

    class _Settings:
        ingest_stats_flush_files = 7
        ingest_stats_flush_seconds = 15.0

    monkeypatch.setattr(ingest, "load_settings", lambda *a, **k: _Settings())
    monkeypatch.delenv("INGEST_STATS_FLUSH_FILES", raising=False)
    monkeypatch.delenv("INGEST_STATS_FLUSH_SECONDS", raising=False)
    assert real() == (7, 15.0)
    monkeypatch.setenv("INGEST_STATS_FLUSH_FILES", "50")
    monkeypatch.setenv("INGEST_STATS_FLUSH_SECONDS", "0")  # not positive: falls back
    assert real() == (50, 15.0)


def test_settings_surfaces_carry_the_new_fields() -> None:
    from src.settings import Settings

    fields = Settings.model_fields
    assert fields["ingest_process_isolation"].default is True
    assert fields["ingest_stats_flush_files"].default == ingest.DEFAULT_STATS_FLUSH_FILES
    assert fields["ingest_stats_flush_seconds"].default == ingest.DEFAULT_STATS_FLUSH_SECONDS


def test_run_passes_the_stored_flag_to_the_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run reads process_isolation_enabled from the performance config like the other settings."""
    ingestion = pytest.importorskip("backend.routers.ingestion")
    source = Path(ingestion.__file__).read_text(encoding="utf-8")
    assert 'perf_config.get("process_isolation_enabled"' in source
    assert "process_isolation=process_isolation" in source and "stats_callback=stats_callback" in source
    worker_source = Path(pytest.importorskip("backend.workers.ingestion_worker").__file__).read_text(encoding="utf-8")
    assert 'process_isolation=self.config.get("process_isolation_enabled")' in worker_source
