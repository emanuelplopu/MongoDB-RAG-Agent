"""Tests for the ingestion throughput controls.

Covers the shared thread pool of ``DocumentIngestionPipeline`` (its size follows the
performance config and the file concurrency), the Tesseract OpenMP thread limit and
its wrapper command, the Docling thread count and accelerator options (always a real
``AcceleratorOptions``, never a dict) and the raised concurrency caps. No converter
runs and no model is loaded.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ingest = pytest.importorskip("src.ingestion.ingest")
ocr_tesseract = pytest.importorskip("src.ingestion.ocr_tesseract")


@pytest.fixture
def fresh_pool(monkeypatch):
    """A pristine class-level pool and thread-limit environment on a 23-core box, restored afterwards."""
    cls = ingest.DocumentIngestionPipeline
    saved = (cls._executor, cls._executor_workers, ingest._pool_workers_in_force)
    cls._executor, cls._executor_workers, ingest._pool_workers_in_force = None, 0, 0
    for name in ("INGEST_THREAD_POOL_WORKERS", "INGEST_DOCLING_THREADS", "OMP_NUM_THREADS", ocr_tesseract.OMP_LIMIT_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ocr_tesseract, "_limit_pinned_at_import", None)
    monkeypatch.setattr(ocr_tesseract, "available_cpu_count", lambda: 23)
    monkeypatch.setattr(ingest, "available_cpu_count", lambda: 23)
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
    config = ingest.OcrConfig(engine="tesseract", languages=("deu", "eng"))
    pdf = ingest._build_document_converter(config).format_to_options[InputFormat.PDF].pipeline_options
    image = ingest._build_image_converter(config, None).format_to_options[InputFormat.IMAGE].pipeline_options
    for options in (pdf, image):
        assert isinstance(options.accelerator_options, AcceleratorOptions)
        assert options.accelerator_options.device == device


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
