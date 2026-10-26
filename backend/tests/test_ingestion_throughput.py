"""Tests for the ingestion throughput controls.

Covers the shared thread pool of ``DocumentIngestionPipeline`` (its size follows the
performance config and the file concurrency), the Tesseract OpenMP thread limit and
its wrapper command, the Docling thread count and accelerator options (always a real
``AcceleratorOptions``, never a dict), the GPU conversion slots (``INGEST_GPU_SLOTS``:
slot -> device assignment, the VRAM cap, the CPU fallback after a CUDA out-of-memory
error, the once-per-process device probe) and the raised concurrency caps. No converter
runs and no model is loaded.
"""

from __future__ import annotations

import itertools
import logging
import os
import stat
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")
ocr_tesseract = pytest.importorskip("src.ingestion.ocr_tesseract")
_real_cuda_free_memory_bytes = ingest.cuda_free_memory_bytes  # the fixture below stubs it out


@pytest.fixture
def fresh_pool(monkeypatch):
    """A pristine pool, slot numbering and thread-limit environment on a 23-core box, restored afterwards."""
    cls = ingest.DocumentIngestionPipeline
    saved = (cls._executor, cls._executor_workers, ingest._pool_workers_in_force)
    cls._executor, cls._executor_workers, ingest._pool_workers_in_force = None, 0, 0
    for name in (
        "INGEST_THREAD_POOL_WORKERS", "INGEST_DOCLING_THREADS", "OMP_NUM_THREADS", ocr_tesseract.OMP_LIMIT_ENV,
        "INGEST_GPU_SLOTS", "INGEST_GPU_SLOT_VRAM_MB",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ocr_tesseract, "_limit_pinned_at_import", None)
    monkeypatch.setattr(ocr_tesseract, "available_cpu_count", lambda: 23)
    monkeypatch.setattr(ingest, "available_cpu_count", lambda: 23)
    # GPU slots: nothing resolved, no slot claimed by this thread, no probed device, no VRAM cap
    monkeypatch.setattr(ingest, "_gpu_slots_in_force", None)
    monkeypatch.setattr(ingest, "_detected_device", None)
    monkeypatch.setattr(ingest, "_slot_counter", itertools.count())
    monkeypatch.setattr(ingest, "_slot_local", threading.local())
    monkeypatch.setattr(ingest, "cuda_free_memory_bytes", lambda: None)
    yield cls
    created = cls._executor
    cls._executor, cls._executor_workers, ingest._pool_workers_in_force = saved
    if created is not None and created is not saved[0]:
        created.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Pool size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "concurrent", "expected"),
    [
        (None, 1, 4),  # the default pool
        (None, 8, 8),  # never below the file concurrency
        (4, 2, 4),
        (2, 6, 6),
        (0, 3, 4),  # 0 = not configured
        (64, 1, 32),  # capped
        (16, 64, 32),
    ],
)
def test_resolve_thread_pool_workers(fresh_pool, configured, concurrent, expected) -> None:
    assert ingest.resolve_thread_pool_workers(configured, concurrent) == expected


def test_default_pool_size_follows_the_environment(fresh_pool, monkeypatch) -> None:
    monkeypatch.setenv("INGEST_THREAD_POOL_WORKERS", "12")
    assert ingest.default_thread_pool_workers() == 12
    assert ingest.resolve_thread_pool_workers(None, 2) == 12
    monkeypatch.setenv("INGEST_THREAD_POOL_WORKERS", "nope")
    assert ingest.default_thread_pool_workers() == 4


def test_configure_executor_sizes_and_replaces_the_pool(fresh_pool) -> None:
    pool = fresh_pool.configure_executor(6)
    assert pool._max_workers == 6
    assert fresh_pool.get_executor() is pool
    assert fresh_pool.configure_executor(6) is pool  # same size: kept
    bigger = fresh_pool.configure_executor(16)
    assert bigger is not pool
    assert bigger._max_workers == 16
    assert pool._shutdown  # the old pool is retired
    assert fresh_pool.configure_executor(100)._max_workers == ingest.MAX_THREAD_POOL_WORKERS


def test_get_executor_defaults_to_four_workers(fresh_pool) -> None:
    assert fresh_pool.get_executor()._max_workers == 4


# ---------------------------------------------------------------------------
# CPU threads shared across the pool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pool", "cores", "expected"),
    [(1, 23, 4), (2, 23, 4), (4, 23, 4), (8, 23, 2), (12, 23, 1), (16, 23, 1), (23, 23, 1), (8, 4, 1), (1, 1, 1)],
)
def test_tesseract_thread_limit_shares_the_cores(pool, cores, expected) -> None:
    assert ocr_tesseract.tesseract_thread_limit(pool, cores) == expected


def test_configure_executor_sets_the_tesseract_limit_and_docling_threads(fresh_pool) -> None:
    fresh_pool.configure_executor(16)
    assert os.environ[ocr_tesseract.OMP_LIMIT_ENV] == "1"
    assert ingest.docling_num_threads() == 1
    fresh_pool.configure_executor(2)
    assert os.environ[ocr_tesseract.OMP_LIMIT_ENV] == "4"
    assert ingest.docling_num_threads() == 4
    assert ingest.docling_num_threads(8) == 2


def test_pinned_values_win(fresh_pool, monkeypatch) -> None:
    assert ocr_tesseract.configure_tesseract_threads(16, pinned="0") == 0
    assert os.environ[ocr_tesseract.OMP_LIMIT_ENV] == "0"
    assert ocr_tesseract.configure_tesseract_threads(16, pinned="3") == 3
    assert ocr_tesseract.configure_tesseract_threads(16, pinned="many") == 1  # computed instead
    monkeypatch.setenv("INGEST_DOCLING_THREADS", "3")
    assert ingest.docling_num_threads(16) == 3
    monkeypatch.delenv("INGEST_DOCLING_THREADS")
    monkeypatch.setenv("OMP_NUM_THREADS", "2")
    assert ingest.docling_num_threads(16) == 2


# ---------------------------------------------------------------------------
# Tesseract wrapper command
# ---------------------------------------------------------------------------


def test_tesseract_wrapper_applies_the_limit_to_the_child_only(tmp_path: Path) -> None:
    real = tmp_path / "bin" / "tesseract"
    real.parent.mkdir()
    real.write_text("#!/bin/sh\necho real\n")
    command = Path(ocr_tesseract.tesseract_command(real_command=str(real), directory=str(tmp_path)))
    script = command.read_text()
    assert command.name == "tesseract"
    assert command.parent == tmp_path
    assert f'export OMP_THREAD_LIMIT="${ocr_tesseract.OMP_LIMIT_ENV}"' in script
    assert f'"${ocr_tesseract.OMP_LIMIT_ENV}" != "0"' in script
    assert script.rstrip().endswith(f'exec "{real}" "$@"')
    assert "\r" not in script
    if os.name == "posix":
        assert command.stat().st_mode & stat.S_IXUSR


@pytest.mark.skipif(os.name != "posix", reason="the wrapper runs through sh")
def test_tesseract_wrapper_runs(tmp_path: Path) -> None:
    real = tmp_path / "real-tesseract"
    real.write_text('#!/bin/sh\necho "limit=${OMP_THREAD_LIMIT:-free} args=$*"\n')
    real.chmod(0o755)
    (tmp_path / "w").mkdir()
    command = ocr_tesseract.tesseract_command(real_command=str(real), directory=str(tmp_path / "w"))
    env = {**os.environ, ocr_tesseract.OMP_LIMIT_ENV: "2"}
    out = subprocess.run([command, "--version"], env=env, capture_output=True, text=True, check=True).stdout
    assert out.strip() == "limit=2 args=--version"
    env[ocr_tesseract.OMP_LIMIT_ENV] = "0"
    out = subprocess.run([command], env=env, capture_output=True, text=True, check=True).stdout
    assert out.startswith("limit=free")


def test_without_tesseract_on_path_the_plain_command_is_used(monkeypatch) -> None:
    monkeypatch.setattr(ocr_tesseract, "_wrapper_path", None)
    monkeypatch.setattr(ocr_tesseract.shutil, "which", lambda name: None)
    assert ocr_tesseract.tesseract_command() == "tesseract"


def test_build_ocr_options_points_docling_at_the_wrapper(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ocr_tesseract, "_wrapper_path", str(tmp_path / "tesseract"))
    options = ingest.build_ocr_options(ingest.OcrConfig(engine="tesseract", languages=("deu", "eng")))
    assert options.tesseract_cmd == str(tmp_path / "tesseract")


# ---------------------------------------------------------------------------
# Docling accelerator options
# ---------------------------------------------------------------------------


def test_accelerator_options_is_a_real_object(fresh_pool, monkeypatch) -> None:
    from docling.datamodel.accelerator_options import AcceleratorOptions

    options = ingest.docling_accelerator_options("cpu", num_threads=2)
    assert isinstance(options, AcceleratorOptions)
    assert options.device == "cpu"
    assert options.num_threads == 2
    assert ingest.docling_accelerator_options("cuda").device == "cuda"
    monkeypatch.setattr(ingest._accelerator_config, "cuda_device_id", 1)
    assert ingest.docling_accelerator_options("cuda").device == "cuda:1"
    fresh_pool.configure_executor(8)
    assert ingest.docling_accelerator_options("cpu").num_threads == 2


@pytest.mark.parametrize("device", ["cuda", "mps", "cpu"])
def test_converters_carry_accelerator_options_for_every_device(fresh_pool, monkeypatch, device) -> None:
    """A dict here made Docling fail every conversion on a GPU ('dict' object has no attribute 'device')."""
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat

    monkeypatch.setattr(ingest, "get_effective_device", lambda: device)
    ingest.assign_conversion_slot(0)  # the first slot: on the GPU when there is one
    config = ingest.OcrConfig(engine="tesseract", languages=("deu", "eng"))
    pdf = ingest._build_document_converter(config).format_to_options[InputFormat.PDF].pipeline_options
    image = ingest._build_image_converter(config, None).format_to_options[InputFormat.IMAGE].pipeline_options
    for options in (pdf, image):
        assert isinstance(options.accelerator_options, AcceleratorOptions)
        assert options.accelerator_options.device == device


# ---------------------------------------------------------------------------
# GPU conversion slots
# ---------------------------------------------------------------------------


@pytest.fixture
def cuda_box(fresh_pool, monkeypatch):
    """The fresh pool on a machine whose effective device is CUDA (no real GPU is touched)."""
    monkeypatch.setattr(ingest, "get_effective_device", lambda: "cuda")
    return fresh_pool


def _slots_of(pool, workers: int) -> list[tuple[int, str]]:
    """(slot, device) of every thread of ``pool``, all held at a barrier so each thread takes one task."""
    barrier = threading.Barrier(workers)

    def claim() -> tuple[int, str]:
        slot = ingest.current_conversion_slot()
        device = ingest.conversion_device()
        barrier.wait(timeout=10)
        return slot, device

    return sorted(f.result(timeout=10) for f in [pool.submit(claim) for _ in range(workers)])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), ("", None), ("  ", None), ("0", 0), ("6", 6), ("nope", None), ("-1", None)],
)
def test_configured_gpu_slots_parses_the_environment(fresh_pool, monkeypatch, raw, expected) -> None:
    if raw is None:
        monkeypatch.delenv("INGEST_GPU_SLOTS", raising=False)
    else:
        monkeypatch.setenv("INGEST_GPU_SLOTS", raw)
    assert ingest.configured_gpu_slots() == expected


def test_gpu_slots_need_a_cuda_device(fresh_pool, monkeypatch) -> None:
    monkeypatch.setattr(ingest, "get_effective_device", lambda: "cpu")
    assert ingest.resolve_gpu_slots(16, configured=8) == 0
    monkeypatch.setenv("INGEST_GPU_SLOTS", "8")
    assert ingest.resolve_gpu_slots(16) == 0
    # MPS (unified memory) keeps the device for every slot; there is nothing to cap
    monkeypatch.setattr(ingest, "get_effective_device", lambda: "mps")
    assert ingest.resolve_gpu_slots(16) == 0
    assert ingest.slot_device(0) == "mps"
    assert ingest.slot_device(15) == "mps"


def test_gpu_slot_defaults_and_pool_cap(cuda_box, monkeypatch) -> None:
    assert ingest.resolve_gpu_slots(16) == ingest.DEFAULT_GPU_SLOTS == 4  # unset = automatic
    assert ingest.resolve_gpu_slots(2) == 2  # never more slots than threads
    assert ingest.resolve_gpu_slots(16, configured=8) == 8
    assert ingest.resolve_gpu_slots(16, configured=100) == 16
    assert ingest.resolve_gpu_slots(16, configured=0) == 0  # 0 = CPU only
    monkeypatch.setenv("INGEST_GPU_SLOTS", "6")
    assert ingest.resolve_gpu_slots(16) == 6
    monkeypatch.setenv("INGEST_GPU_SLOTS", "0")
    assert ingest.resolve_gpu_slots(16) == 0


def test_gpu_slots_are_lowered_to_the_free_vram(cuda_box, monkeypatch, caplog) -> None:
    gib = 2**30
    with caplog.at_level(logging.WARNING, logger=ingest.logger.name):
        assert ingest.resolve_gpu_slots(16, configured=8, free_vram_bytes=3 * gib) == 2  # 2 x 1200 MiB fit
    assert "lowering the GPU conversion slots from 8 to 2" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=ingest.logger.name):
        assert ingest.resolve_gpu_slots(16, configured=4, free_vram_bytes=gib // 2) == 0
    assert "every conversion runs on the CPU" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=ingest.logger.name):
        assert ingest.resolve_gpu_slots(16, configured=4, free_vram_bytes=12 * gib) == 4  # enough: no warning
    assert caplog.text == ""
    monkeypatch.setenv("INGEST_GPU_SLOT_VRAM_MB", "500")  # a smaller per-slot estimate lets more fit
    assert ingest.resolve_gpu_slots(16, configured=8, free_vram_bytes=3 * gib) == 6


def test_vram_cap_reads_torch_mem_get_info(cuda_box, monkeypatch, caplog) -> None:
    """The real query path: ``torch.cuda.mem_get_info`` of the configured device, mocked here."""
    monkeypatch.setattr(ingest, "cuda_free_memory_bytes", _real_cuda_free_memory_bytes)  # not the fixture's stub
    queried: list[int] = []

    def mem_get_info(device: int = 0) -> tuple[int, int]:
        queried.append(device)
        return 2_600 * 2**20, 16 * 2**30  # 2.6 GB free: room for two 1200 MiB converters

    fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True, mem_get_info=mem_get_info))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(ingest._accelerator_config, "cuda_device_id", 1)
    with caplog.at_level(logging.WARNING, logger=ingest.logger.name):
        assert ingest.resolve_gpu_slots(16, configured=4) == 2
    assert queried == [1]
    assert "Free GPU memory 2.5 GiB holds 2 converter(s)" in caplog.text
    # CUDA unusable: no cap, the request stands
    fake_torch.cuda.is_available = lambda: False
    assert ingest.resolve_gpu_slots(16, configured=4) == 4


def test_slot_device_splits_the_pool(cuda_box) -> None:
    assert [ingest.slot_device(i, 4) for i in range(8)] == ["cuda"] * 4 + ["cpu"] * 4
    assert ingest.slot_device(0, 0) == "cpu"
    assert ingest.slot_device(5, 6) == "cuda"


def test_pool_threads_take_distinct_slots_and_the_first_ones_the_gpu(cuda_box, monkeypatch) -> None:
    monkeypatch.setenv("INGEST_GPU_SLOTS", "3")
    pool = cuda_box.configure_executor(6)
    assert ingest.gpu_slots_in_force() == 3
    assert _slots_of(pool, 6) == [(0, "cuda"), (1, "cuda"), (2, "cuda"), (3, "cpu"), (4, "cpu"), (5, "cpu")]
    # a new pool numbers its slots from 0 again, with its own GPU slot count
    monkeypatch.setenv("INGEST_GPU_SLOTS", "1")
    smaller = cuda_box.configure_executor(2)
    assert smaller is not pool
    assert ingest.gpu_slots_in_force() == 1
    assert _slots_of(smaller, 2) == [(0, "cuda"), (1, "cpu")]


def test_zero_gpu_slots_keeps_every_conversion_on_the_cpu(cuda_box, monkeypatch) -> None:
    monkeypatch.setenv("INGEST_GPU_SLOTS", "0")
    pool = cuda_box.configure_executor(4)
    assert ingest.gpu_slots_in_force() == 0
    assert {device for _, device in _slots_of(pool, 4)} == {"cpu"}


def test_threads_outside_the_pool_take_the_next_slot(cuda_box, monkeypatch) -> None:
    monkeypatch.setenv("INGEST_GPU_SLOTS", "2")
    cuda_box.configure_executor(4)
    assert ingest.current_conversion_slot() == 0  # nothing claimed yet: this thread is slot 0
    assert ingest.conversion_device() == "cuda"
    ingest.assign_conversion_slot(7)  # a worker process pins its own slot
    assert ingest.current_conversion_slot() == 7
    assert ingest.conversion_device() == "cpu"


def test_accelerator_options_and_converter_keys_follow_the_slot(cuda_box, monkeypatch) -> None:
    monkeypatch.setenv("INGEST_GPU_SLOTS", "1")
    cuda_box.configure_executor(4)
    ingest.assign_conversion_slot(0)
    assert ingest.docling_accelerator_options().device == "cuda"
    ingest.assign_conversion_slot(1)
    assert ingest.docling_accelerator_options().device == "cpu"
    assert ingest.docling_accelerator_options("cuda").device == "cuda"  # an explicit device still wins


def test_changing_the_accelerator_device_resolves_the_slots_anew(cuda_box) -> None:
    assert ingest.gpu_slots_in_force() == 4
    ingest.update_accelerator_config(device="cuda")
    assert ingest._gpu_slots_in_force is None
    ingest.update_accelerator_config(device="auto")  # restore for the other tests
    assert ingest._gpu_slots_in_force is None


def test_the_device_is_probed_once_per_process(fresh_pool, monkeypatch) -> None:
    probes: list[int] = []

    def probe() -> str:
        probes.append(1)
        return "cpu"

    monkeypatch.setattr(ingest, "_probe_available_device", probe)
    assert [ingest.detect_available_device() for _ in range(3)] == ["cpu"] * 3
    assert len(probes) == 1
    ingest.reset_device_detection()
    assert ingest.detect_available_device() == "cpu"
    assert len(probes) == 2


class _OutOfMemoryError(RuntimeError):
    """Stands in for ``torch.OutOfMemoryError`` (matched by name, torch is optional)."""


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_OutOfMemoryError("CUDA out of memory. Tried to allocate 512.00 MiB"), True),
        (RuntimeError("CUDA out of memory. Tried to allocate 20.00 MiB"), True),
        (RuntimeError("CUDA error: out of memory"), True),
        (RuntimeError("cuBLAS failed: CUBLAS_STATUS_ALLOC_FAILED"), True),
        (RuntimeError("Conversion failed for: x.pdf with status: ConversionStatus.FAILURE"), False),
        (ValueError("not a pdf"), False),
    ],
)
def test_cuda_out_of_memory_detection(exc, expected) -> None:
    assert ingest.is_cuda_out_of_memory(exc) is expected
    wrapped = RuntimeError("Conversion failed for: x.pdf")
    wrapped.__cause__ = exc  # Docling re-raises inside an except block: the model's error is chained
    assert ingest.is_cuda_out_of_memory(wrapped) is expected


class _Converter:
    def __init__(self, failures: int, message: str = "CUDA out of memory. Tried to allocate 512.00 MiB") -> None:
        self.failures = failures
        self.message = message
        self.calls = 0

    def convert(self, path: str) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(self.message)
        return f"ok:{path}"


def test_a_gpu_slot_that_runs_out_of_memory_moves_to_the_cpu(cuda_box, monkeypatch, caplog) -> None:
    monkeypatch.setenv("INGEST_GPU_SLOTS", "2")
    cuda_box.configure_executor(4)
    ingest.assign_conversion_slot(0)
    converter = _Converter(failures=1)
    with caplog.at_level(logging.WARNING, logger=ingest.logger.name):
        assert ingest.convert_with_cpu_fallback(lambda: converter, "scan.pdf") == "ok:scan.pdf"
    assert converter.calls == 2
    assert ingest.conversion_device() == "cpu"  # this slot stays on the CPU
    assert "Conversion slot 0 moved to the CPU after a GPU out-of-memory error" in caplog.text
    assert ingest.convert_with_cpu_fallback(lambda: _Converter(failures=0), "next.pdf") == "ok:next.pdf"
    ingest.assign_conversion_slot(0)  # re-pinning the slot lifts the demotion
    assert ingest.conversion_device() == "cuda"


def test_other_conversion_errors_and_cpu_slots_are_not_demoted(cuda_box, monkeypatch) -> None:
    monkeypatch.setenv("INGEST_GPU_SLOTS", "2")
    cuda_box.configure_executor(4)
    ingest.assign_conversion_slot(0)
    with pytest.raises(RuntimeError, match="Conversion failed"):
        ingest.convert_with_cpu_fallback(lambda: _Converter(1, "Conversion failed for: x.pdf"), "x.pdf")
    assert ingest.conversion_device() == "cuda"
    ingest.assign_conversion_slot(3)  # a CPU slot: an OOM-looking error is somebody else's and propagates
    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        ingest.convert_with_cpu_fallback(lambda: _Converter(1), "x.pdf")
    assert ingest.conversion_device() == "cpu"


# ---------------------------------------------------------------------------
# Concurrency caps
# ---------------------------------------------------------------------------


def test_performance_config_caps() -> None:
    from pydantic import ValidationError

    system = pytest.importorskip("backend.routers.system")
    ok = system.IngestionPerformanceConfig(max_concurrent_files=32, thread_pool_workers=32)
    assert (ok.max_concurrent_files, ok.thread_pool_workers) == (32, 32)
    with pytest.raises(ValidationError):
        system.IngestionPerformanceConfig(max_concurrent_files=33)
    with pytest.raises(ValidationError):
        system.IngestionPerformanceConfig(thread_pool_workers=33)
    assert ingest.MAX_THREAD_POOL_WORKERS == 32


def test_settings_caps_match_the_pool_limit() -> None:
    from backend.core.config import BackendSettings

    for name in ("ingestion_max_concurrent_files", "ingestion_thread_pool_workers"):
        limits = [getattr(meta, "le", None) for meta in BackendSettings.model_fields[name].metadata]
        assert ingest.MAX_THREAD_POOL_WORKERS in limits, name


def test_both_settings_surfaces_read_the_gpu_slots(monkeypatch) -> None:
    """``INGEST_GPU_SLOTS``: empty (as docker-compose.yml forwards it) is the automatic default."""
    from backend.core.config import BackendSettings
    from src.settings import Settings

    monkeypatch.delenv("INGEST_GPU_SLOTS", raising=False)
    required = {"mongodb_uri": "mongodb://localhost:27017", "llm_api_key": "k", "embedding_api_key": "k"}
    assert Settings(_env_file=None, **required).ingest_gpu_slots is None
    assert Settings(_env_file=None, ingest_gpu_slots="", **required).ingest_gpu_slots is None
    assert Settings(_env_file=None, ingest_gpu_slots="3", **required).ingest_gpu_slots == 3
    assert BackendSettings(_env_file=None, ingest_gpu_slots="").ingest_gpu_slots is None
    monkeypatch.setenv("INGEST_GPU_SLOTS", "2")
    assert BackendSettings(_env_file=None).ingest_gpu_slots == 2
    assert Settings(_env_file=None, **required).ingest_gpu_slots == 2
