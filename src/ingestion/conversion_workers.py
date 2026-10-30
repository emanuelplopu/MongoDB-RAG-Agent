"""Killable conversion worker processes for the concurrent ingestion loop.

Why this exists (F1 of ``docs/system-validation/INGESTION_PROMPT_TEST_LOG_2026-10-24.md``):
the per-file timeout of the concurrent loop used to cancel an ``asyncio`` future while
the thread converting the file kept running. The slot was handed to the next file, more
conversions ran than the pool was sized for, every file slowed down, more files timed out,
and their retries redid the work. A thread cannot be interrupted; a process can.

With the performance config's ``process_isolation_enabled`` flag on, every file slot of a
run owns one long-lived worker process (``spawn`` context, so CUDA and the Docling models
start cleanly) that converts files on request. The parent sends a file path, the child
answers with the converted Markdown, the ``DoclingDocument`` and the reader's notes about
the conversion (``note_conversion`` in ``ingest.py``, e.g. which scan-text fallback fired);
on a timeout or a stop the parent kills the child and the next file on that slot starts a
fresh one. Chunking, embedding and the MongoDB writes stay in the parent.

The child applies the parent's CPU-thread sharing (``docling_num_threads``, the Tesseract
OpenMP limit) and the stored accelerator config before its first file, and warms up the
PDF converter so the model loading does not count against the first file's budget: the
parent starts a file's budget only when the child has acknowledged the request.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Seconds a worker may take to import Docling, load its models and report ``ready``.
READY_TIMEOUT_SECONDS = 900.0
#: How often a waiting thread looks at the pipe (and at the abort flag of its worker).
_POLL_SECONDS = 0.2
_REAP_SECONDS = 5.0

#: ``target(file_path, **options)``; targets without options are called as ``target(file_path)``.
#: Returns ``(markdown, document)`` or ``(markdown, document, notes)``; the notes travel back
#: to the parent.
ReaderTarget = Callable[..., Tuple[Any, ...]]


class ConversionWorkerError(RuntimeError):
    """The worker process reported a failure, died, or broke the protocol."""


class _Aborted(Exception):
    """Raised inside a waiting thread whose worker generation was killed."""


@dataclass
class WorkerInit:
    """What a conversion worker process applies before it converts its first file.

    Attributes:
        pool_workers: Number of conversions that run at once across all workers; the basis
            for sharing the CPU threads (``docling_num_threads``, Tesseract's OpenMP limit).
        accelerator: ``dataclasses.asdict`` of the parent's accelerator config (device,
            CUDA device id), stored in the database and not visible through the environment.
        apply_pipeline_config: Import ``src.ingestion.ingest`` and apply the above; off for
            test targets that do not convert documents.
        warm_up: Build the PDF converter (loads the layout/table/OCR models) before ``ready``.
        log_level: Root log level of the worker process.
    """

    pool_workers: int = 1
    accelerator: Optional[Dict[str, Any]] = None
    apply_pipeline_config: bool = True
    warm_up: bool = True
    log_level: int = logging.INFO


# ---------------------------------------------------------------------------
# Child side
# ---------------------------------------------------------------------------

_reader: Any = None


def read_document_in_worker(file_path: str, **options: Any) -> Tuple[str, Any, Dict[str, Any]]:
    """Convert ``file_path`` with a read-only pipeline (the default worker target).

    Args:
        file_path: Document to convert.
        **options: Per-attempt conversion options (``ConversionOptions`` fields, e.g.
            ``table_structure=False`` for the last retry of a timed-out PDF); none for the
            run's defaults.

    Returns:
        ``(markdown_content, docling_document, notes)``; the notes are what the reader recorded
        with ``note_conversion`` (e.g. ``ocr_fallback``) and travel back to the parent.
    """
    global _reader
    from src.ingestion.ingest import conversion_notes

    if _reader is None:
        from src.ingestion.ingest import DocumentIngestionPipeline

        _reader = DocumentIngestionPipeline.reader_only()
    with conversion_notes() as notes:
        if options:
            from src.ingestion.ingest import ConversionOptions

            content, document = _reader._read_document(file_path, ConversionOptions(**options))
        else:
            content, document = _reader._read_document(file_path)
    return content, document, dict(notes)


def _apply_init(init: WorkerInit) -> None:
    """Apply the parent's thread sharing and accelerator config in the worker process."""
    import src.ingestion.ingest as ingest

    ingest._pool_workers_in_force = max(1, int(init.pool_workers))
    if init.accelerator:
        try:
            ingest.update_accelerator_config(**init.accelerator)
        except TypeError as exc:  # a field this version does not know
            logger.warning("Ignoring accelerator config in conversion worker: %s", exc)
    try:
        from src.ingestion.ocr_tesseract import configure_tesseract_threads
    except ImportError:  # Docling not installed: no Tesseract either
        pass
    else:
        configure_tesseract_threads(init.pool_workers)
    if init.warm_up:
        try:
            converter = ingest.create_document_converter()
            # Docling builds a format's pipeline (and loads its models) on the first conversion;
            # do it now so the first file on this worker does not pay for it out of its budget
            from docling.datamodel.base_models import InputFormat

            converter.initialize_pipeline(InputFormat.PDF)
        except Exception as exc:  # noqa: BLE001 - a missing model fails the first file, not the worker
            logger.warning("Conversion worker could not warm up the document converter: %s", exc)


def _worker_main(conn: Connection, target: ReaderTarget, init: WorkerInit) -> None:
    """Entry point of a worker process: apply the config, say ``ready``, convert on request."""
    logging.basicConfig(
        level=init.log_level,
        stream=sys.stderr,
        format=f"%(asctime)s [conversion-worker {os.getpid()}] %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    try:
        if init.apply_pipeline_config:
            _apply_init(init)
        conn.send(("ready", os.getpid()))
        while True:
            message = conn.recv()  # EOFError once the parent is gone
            if not message or message[0] == "stop":
                break
            file_path = message[1]
            options = message[2] if len(message) > 2 else None
            try:
                outcome = target(file_path, **options) if options else target(file_path)
                content, document = outcome[0], outcome[1]
                # a target may add the reader's notes about the conversion as a third item
                notes = dict(outcome[2]) if len(outcome) > 2 and outcome[2] else {}
            except Exception as exc:  # noqa: BLE001 - reported to the parent as a file error
                logger.exception("Conversion failed in worker: %s", file_path)
                conn.send(("error", f"{type(exc).__name__}: {exc}"[:2000]))
                continue
            try:
                conn.send(("ok", content, document, notes))
            except Exception as exc:  # noqa: BLE001 - unpicklable document: send its JSON form
                export = getattr(document, "export_to_dict", None)
                if export is None:
                    conn.send(("error", f"result not transferable: {type(exc).__name__}: {exc}"))
                else:
                    conn.send(("ok_dict", content, export(), notes))
    except (EOFError, OSError, KeyboardInterrupt):
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _document_from_dict(data: Dict[str, Any]) -> Any:
    from docling_core.types.doc import DoclingDocument

    return DoclingDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Parent side
# ---------------------------------------------------------------------------


@dataclass
class _Generation:
    """One process of a worker slot: its pipe and the flag that ends threads waiting on it."""

    process: Any
    conn: Connection
    abort: threading.Event = field(default_factory=threading.Event)
    ready: bool = False

    @property
    def pid(self) -> Optional[int]:
        return getattr(self.process, "pid", None)

    def wait_message(self, deadline: Optional[float]) -> tuple:
        """Block (in a waiter thread) until the child sends a message.

        Args:
            deadline: ``time.monotonic()`` value after which ``TimeoutError`` is raised; None waits
                until the message, the abort flag or the child's death.

        Raises:
            _Aborted: The parent killed this generation.
            TimeoutError: ``deadline`` passed.
            ConversionWorkerError: The child died or closed the pipe.
        """
        while True:
            if self.abort.is_set():
                raise _Aborted()
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError()
            try:
                if self.conn.poll(_POLL_SECONDS):
                    return self.conn.recv()
            except (EOFError, OSError) as exc:
                if self.abort.is_set():
                    raise _Aborted() from exc
                code = getattr(self.process, "exitcode", None)
                raise ConversionWorkerError(
                    f"conversion worker {self.pid} exited (code {code}) before answering"
                ) from exc


class ConversionWorker:
    """One file slot's worker process, restarted after every kill."""

    def __init__(self, index: int, target: ReaderTarget, init: WorkerInit) -> None:
        self.index = index
        self._target = target
        self._init = init
        self.generation: Optional[_Generation] = None
        self.starts = 0
        self.kills = 0

    @property
    def pid(self) -> Optional[int]:
        return self.generation.pid if self.generation else None

    def is_alive(self) -> bool:
        return bool(self.generation and self.generation.process.is_alive())

    def start(self) -> _Generation:
        """Spawn a new process for this slot (the previous generation must be gone)."""
        ctx = multiprocessing.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe(duplex=True)
        process = ctx.Process(
            target=_worker_main,
            args=(child_conn, self._target, self._init),
            name=f"ingest-convert-{self.index}",
            daemon=True,
        )
        process.start()
        child_conn.close()
        self.generation = _Generation(process=process, conn=parent_conn)
        self.starts += 1
        logger.info("Conversion worker %d started (pid %s)", self.index, process.pid)
        return self.generation

    def kill(self, reason: str, *, join: bool = False) -> None:
        """Terminate the current process; threads waiting on it return at their next poll.

        Args:
            reason: Logged with the pid.
            join: Reap synchronously (shutdown); otherwise the caller reaps in the background.
        """
        generation = self.generation
        if generation is None:
            return
        self.generation = None
        generation.abort.set()
        process = generation.process
        if process.is_alive():
            self.kills += 1
            logger.warning("Killing conversion worker %d (pid %s): %s", self.index, process.pid, reason)
            try:
                process.kill()
            except (OSError, AttributeError):
                process.terminate()
        try:
            generation.conn.close()
        except OSError:
            pass
        if join:
            process.join(_REAP_SECONDS)

    def request_stop(self) -> bool:
        """Ask a ready worker to exit; returns whether a request went out."""
        generation = self.generation
        if generation is None or not generation.ready or not generation.process.is_alive():
            return False
        try:
            generation.conn.send(("stop",))
        except (OSError, ValueError):
            return False
        return True

    def finish_stop(self) -> None:
        """Forget a worker that exited after :meth:`request_stop`; kill one that did not."""
        generation = self.generation
        if generation is None:
            return
        if generation.process.is_alive():
            self.kill("did not exit after stop", join=True)
            return
        logger.info("Conversion worker %d stopped (pid %s)", self.index, generation.pid)
        self.generation = None
        generation.abort.set()
        try:
            generation.conn.close()
        except OSError:
            pass


class ConversionProcessPool:
    """Worker processes for the file slots of one concurrent ingestion run.

    Processes start lazily, on a slot's first conversion, so a run that converts nothing
    (or a test) spawns none. ``convert()`` kills the slot's process when it is cancelled
    (per-file timeout, stop, shutdown) and raises :class:`ConversionWorkerError` when the
    process dies; either way the next file on that slot gets a fresh process.

    Args:
        size: Number of slots (``max_concurrent_files``).
        target: The function the child runs per file (module-level, importable by name).
        init: Per-process configuration; see :class:`WorkerInit`.
        ready_timeout: Seconds a process may take to report ``ready``.
    """

    def __init__(
        self,
        size: int,
        *,
        target: ReaderTarget = read_document_in_worker,
        init: Optional[WorkerInit] = None,
        ready_timeout: float = READY_TIMEOUT_SECONDS,
    ) -> None:
        self.size = max(1, int(size))
        self._init = init or WorkerInit(pool_workers=self.size)
        self._target = target
        self._ready_timeout = ready_timeout
        self.workers: List[ConversionWorker] = [
            ConversionWorker(i, self._target, self._init) for i in range(self.size)
        ]
        self._idle: Optional[asyncio.Queue] = None
        # Waiting on a pipe blocks a thread per slot; a dedicated pool keeps these waits
        # from queueing behind the ingestion pool's hashing and conversions (and vice versa)
        self._waiters = ThreadPoolExecutor(max_workers=self.size + 2, thread_name_prefix="convert_wait_")
        self._closed = False

    # -- accounting -----------------------------------------------------------------

    @property
    def kills(self) -> int:
        return sum(w.kills for w in self.workers)

    @property
    def starts(self) -> int:
        return sum(w.starts for w in self.workers)

    def live_pids(self) -> List[int]:
        return [w.pid for w in self.workers if w.is_alive() and w.pid is not None]

    # -- conversions ----------------------------------------------------------------

    def _queue(self) -> asyncio.Queue:
        if self._idle is None:
            self._idle = asyncio.Queue()
            for worker in self.workers:
                self._idle.put_nowait(worker)
        return self._idle

    async def _receive(self, generation: _Generation, deadline: Optional[float]) -> tuple:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._waiters, generation.wait_message, deadline)

    async def _ready_generation(self, worker: ConversionWorker) -> _Generation:
        generation = worker.generation
        if generation is None or not generation.process.is_alive():
            if generation is not None:
                worker.kill("found dead")
            generation = worker.start()
        if not generation.ready:
            try:
                message = await self._receive(generation, time.monotonic() + self._ready_timeout)
            except TimeoutError:
                worker.kill("did not become ready")
                raise ConversionWorkerError(
                    f"conversion worker {worker.index} did not become ready within {self._ready_timeout:.0f}s"
                )
            if not message or message[0] != "ready":
                worker.kill("bad handshake")
                raise ConversionWorkerError(f"conversion worker {worker.index} sent {message!r} instead of ready")
            generation.ready = True
            logger.info("Conversion worker %d ready (pid %s)", worker.index, generation.pid)
        return generation

    async def convert(
        self,
        file_path: str,
        on_started: Optional[Callable[[], None]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Any]:
        """Convert ``file_path`` in the next free worker process.

        Args:
            file_path: Document to convert.
            on_started: Called once the worker has the request (the file's budget starts here).
            options: Per-attempt conversion options passed to the worker target as keyword
                arguments (``ConversionOptions.to_dict()``); ``None`` for the run's defaults.

        Returns:
            ``(markdown_content, docling_document_or_None)`` as ``_read_document`` returns it.

        Raises:
            ConversionWorkerError: The worker died or reported a failure.
            asyncio.CancelledError: The caller was cancelled; the worker process was killed first.
        """
        content, document, _notes = await self.convert_with_notes(
            file_path, on_started=on_started, options=options
        )
        return content, document

    async def convert_with_notes(
        self,
        file_path: str,
        on_started: Optional[Callable[[], None]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Any, Dict[str, Any]]:
        """:meth:`convert`, plus the notes the reader recorded about the conversion.

        Args:
            file_path: Document to convert.
            on_started: Called once the worker has the request (the file's budget starts here).
            options: Per-attempt conversion options for the worker target; see :meth:`convert`.

        Returns:
            ``(markdown_content, docling_document_or_None, notes)``; ``notes`` is empty for a
            target that returns only the content and the document.
        """
        if self._closed:
            raise ConversionWorkerError("conversion pool is closed")
        queue = self._queue()
        worker = await queue.get()
        try:
            try:
                generation = await self._ready_generation(worker)
                try:
                    generation.conn.send(("convert", file_path, options) if options else ("convert", file_path))
                except (OSError, ValueError) as exc:
                    worker.kill("pipe broken")
                    raise ConversionWorkerError(f"conversion worker {worker.index} unreachable: {exc}") from exc
                if on_started is not None:
                    on_started()
                message = await self._receive(generation, None)
            except asyncio.CancelledError:
                # Timeout, stop or shutdown: the process goes, whether it was warming up or
                # converting (a thread may still be waiting on its pipe; the kill ends it too)
                self._kill_async(worker, f"cancelled ({os.path.basename(file_path)})")
                raise
            except _Aborted as exc:
                raise ConversionWorkerError(f"conversion worker {worker.index} was killed while converting") from exc
            kind = message[0] if message else None
            notes = message[3] if message and len(message) > 3 and isinstance(message[3], dict) else {}
            if kind == "ok":
                return message[1], message[2], notes
            if kind == "ok_dict":
                return message[1], _document_from_dict(message[2]), notes
            if kind == "error":
                raise ConversionWorkerError(message[1])
            worker.kill("protocol error")
            raise ConversionWorkerError(f"conversion worker {worker.index} sent {kind!r}")
        finally:
            queue.put_nowait(worker)

    def _kill_async(self, worker: ConversionWorker, reason: str) -> None:
        """Kill without blocking the event loop; the process is reaped in the default executor."""
        generation = worker.generation
        worker.kill(reason)
        if generation is None:
            return
        try:
            asyncio.get_running_loop().run_in_executor(None, generation.process.join, _REAP_SECONDS)
        except RuntimeError:  # no running loop (shutdown from a sync context)
            generation.process.join(_REAP_SECONDS)

    def shutdown(self) -> None:
        """End every worker process: ask the ready ones to exit, then kill whatever is left.

        Blocks for at most about ``_REAP_SECONDS`` in all, so call it off the event loop
        (``run_in_executor``) when a loop is running.
        """
        self._closed = True
        asked = [worker for worker in self.workers if worker.request_stop()]
        deadline = time.monotonic() + _REAP_SECONDS
        while asked and time.monotonic() < deadline:
            asked = [worker for worker in asked if worker.is_alive()]
            if asked:
                time.sleep(0.05)
        for worker in self.workers:
            try:
                worker.finish_stop()
            except Exception as exc:  # noqa: BLE001 - never let a stuck child block the run's end
                logger.warning("Conversion worker %d did not stop cleanly: %s", worker.index, exc)
        self._waiters.shutdown(wait=False)
