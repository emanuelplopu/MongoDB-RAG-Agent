#!/usr/bin/env python
"""Benchmark Docling conversions on the ingestion pool with N GPU slots.

Runs inside the worker image in a throwaway container (plain ``docker run``, nothing of
the Compose stack is touched) and converts every PDF and image under a folder exactly
as ``DocumentIngestionPipeline`` would: on the shared thread pool, with one thread-cached
converter per thread, Tesseract's OpenMP limit and the Docling thread count shared across
the pool, and the first ``INGEST_GPU_SLOTS`` slots on the GPU
(``src/ingestion/ingest.py``, "GPU conversion slots")::

    docker run --rm --gpus all -e INGEST_GPU_SLOTS=4 \\
      -v <repo>/src:/app/src:ro -v <repo>/scripts/bench_gpu_slots.py:/app/bench_gpu_slots.py:ro \\
      -v <sample>:/data/in:ro -v <out>:/data/out \\
      recallhub-ingestion-worker:latest python -I /app/bench_gpu_slots.py /data/in /data/out/slots4.json --workers 16

Every thread first builds its converter and loads the models (warm-up, timed
separately), then the files are converted ``--repeat`` times. The JSON written to the
output path holds the per-file wall times (with slot, device and page count), the
pages/s of the run, the medians per device and per file class (born-digital PDF, scan,
image), the process' CUDA memory (peak reserved, per GPU slot) and every conversion
error. ``--memory-fraction`` caps the process' share of the VRAM
(``torch.cuda.set_per_process_memory_fraction``) to provoke CUDA out-of-memory with a
slot count the card cannot hold, without starving the other processes on the GPU.

``python -I`` (isolated mode) does not put the script's folder on ``sys.path``; the
repository root is added explicitly (``--app-root``, default ``/app``).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import threading
import time
from concurrent.futures import Future, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="Folder with PDFs and images (searched recursively)")
    parser.add_argument("output", help="JSON result file")
    parser.add_argument("--workers", type=int, default=16, help="Thread pool size (default 16)")
    parser.add_argument(
        "--gpu-slots", type=int, default=None,
        help="Sets INGEST_GPU_SLOTS before the pipeline is imported (default: the environment)",
    )
    parser.add_argument("--repeat", type=int, default=1, help="Convert the file list this many times")
    parser.add_argument("--limit", type=int, default=0, help="Use at most this many files (0 = all)")
    parser.add_argument(
        "--memory-fraction", type=float, default=None,
        help="torch.cuda.set_per_process_memory_fraction for this process (OOM experiments)",
    )
    parser.add_argument("--no-warmup", action="store_true", help="Skip the per-thread warm-up phase")
    parser.add_argument("--app-root", default="/app", help="Repository root to import src.ingestion from")
    parser.add_argument("--label", default="", help="Free text stored in the result")
    return parser.parse_args(argv)


def find_files(root: Path, limit: int) -> List[Path]:
    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in PDF_EXTENSIONS | IMAGE_EXTENSIONS
    )
    return files[:limit] if limit > 0 else files


def classify(path: Path) -> Dict[str, Any]:
    """``pdf-text`` (text layer on page 1), ``pdf-scan`` or ``image``, with the page count."""
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        return {"class": "image", "pages": 1}
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(path))
        try:
            pages = len(doc)
            chars = len(doc[0].get_textpage().get_text_range().strip()) if pages else 0
        finally:
            doc.close()
        return {"class": "pdf-text" if chars >= 50 else "pdf-scan", "pages": pages}
    except Exception as exc:  # noqa: BLE001 - classification is informational
        return {"class": "pdf-unknown", "pages": 0, "classify_error": str(exc)[:200]}


class CudaMemorySampler:
    """Samples the CUDA allocator and the card's free memory until stopped."""

    def __init__(self, interval: float = 0.5) -> None:
        self.interval = interval
        self.samples: List[Dict[str, float]] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.available = False
        try:
            import torch

            self.available = bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001
            self.available = False

    def sample(self) -> Optional[Dict[str, float]]:
        if not self.available:
            return None
        import torch

        free, total = torch.cuda.mem_get_info()
        entry = {
            "t": time.time(),
            "card_used_mib": (total - free) / 2**20,
            "card_free_mib": free / 2**20,
            "reserved_mib": torch.cuda.memory_reserved() / 2**20,
            "allocated_mib": torch.cuda.memory_allocated() / 2**20,
        }
        self.samples.append(entry)
        return entry

    def start(self) -> None:
        if not self.available:
            return

        def loop() -> None:
            while not self._stop.wait(self.interval):
                try:
                    self.sample()
                except Exception:  # noqa: BLE001 - a failed sample is skipped
                    pass

        self._thread = threading.Thread(target=loop, name="cuda-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def peak(self, key: str) -> Optional[float]:
        values = [s[key] for s in self.samples if key in s]
        return max(values) if values else None


def median(values: List[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.gpu_slots is not None:
        os.environ["INGEST_GPU_SLOTS"] = str(args.gpu_slots)
    sys.path.insert(0, args.app_root)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    for noisy in ("docling", "docling_core", "docling_ibm_models", "httpx", "urllib3", "PIL", "pymongo"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log = logging.getLogger("bench_gpu_slots")

    import src.ingestion.ingest as ingest  # noqa: E402 - after sys.path and the environment are set

    sampler = CudaMemorySampler()
    cuda_info: Dict[str, Any] = {"available": sampler.available}
    if sampler.available:
        import torch

        cuda_info["device"] = torch.cuda.get_device_name(0)
        cuda_info["torch"] = torch.__version__
        if args.memory_fraction is not None:
            torch.cuda.set_per_process_memory_fraction(float(args.memory_fraction))
            cuda_info["memory_fraction"] = float(args.memory_fraction)
    baseline = sampler.sample()

    files = find_files(Path(args.input), args.limit)
    if not files:
        log.error("No PDF or image files under %s", args.input)
        return 2
    info = {str(p): classify(p) for p in files}
    log.info(
        "%d files: %s", len(files),
        ", ".join(f"{cls}={n}" for cls, n in sorted(
            ((c, sum(1 for i in info.values() if i['class'] == c)) for c in {i['class'] for i in info.values()})
        )),
    )

    pool = ingest.DocumentIngestionPipeline.configure_executor(args.workers)
    workers = ingest.DocumentIngestionPipeline._executor_workers
    gpu_slots = ingest.gpu_slots_in_force()
    log.info("pool %d workers, %d GPU slots, device %s", workers, gpu_slots, ingest.get_effective_device())

    from docling.datamodel.base_models import InputFormat

    # Warm-up: one task per thread, held at a barrier so every thread builds its converter
    warmup_seconds = None
    warmup_devices: Dict[str, int] = {}
    if not args.no_warmup:
        barrier = threading.Barrier(workers)

        def warm() -> Dict[str, Any]:
            t0 = time.perf_counter()
            slot = ingest.current_conversion_slot()
            entry: Dict[str, Any] = {"slot": slot, "device": ingest.conversion_device()}
            try:
                converter = ingest.create_document_converter()
                converter.initialize_pipeline(InputFormat.PDF)
            except Exception as exc:  # noqa: BLE001 - e.g. CUDA OOM while loading: the run goes on
                entry["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
                entry["cuda_oom"] = ingest.is_cuda_out_of_memory(exc)
                log.warning("warm-up of slot %d (%s) failed: %s", slot, entry["device"], entry["error"])
            finally:
                barrier.wait(timeout=600)
            entry["seconds"] = time.perf_counter() - t0
            return entry

        t_warm = time.perf_counter()
        warm_results = [f.result() for f in [pool.submit(warm) for _ in range(workers)]]
        warmup_seconds = time.perf_counter() - t_warm
        for entry in warm_results:
            key = entry["device"] + (" (failed)" if "error" in entry else "")
            warmup_devices[key] = warmup_devices.get(key, 0) + 1
        log.info("warm-up %.1f s; converters: %s", warmup_seconds, warmup_devices)
    after_warmup = sampler.sample()

    sampler.start()

    def convert(path: Path) -> Dict[str, Any]:
        entry: Dict[str, Any] = {"file": path.name, **info[str(path)]}
        entry["slot"] = ingest.current_conversion_slot()
        entry["device_before"] = ingest.conversion_device()
        t0 = time.perf_counter()
        try:
            if path.suffix.lower() in IMAGE_EXTENSIONS:
                result = ingest.convert_with_cpu_fallback(lambda: ingest.create_image_converter(image_path=str(path)), str(path))
            else:
                result = ingest.convert_with_cpu_fallback(ingest.create_document_converter, str(path))
            entry["status"] = str(getattr(result, "status", "?"))
            doc = result.document
            try:
                entry["pages_converted"] = int(doc.num_pages()) if hasattr(doc, "num_pages") else len(result.pages)
            except Exception:  # noqa: BLE001
                entry["pages_converted"] = len(getattr(result, "pages", []) or [])
            entry["chars"] = len(doc.export_to_markdown()) if doc is not None else 0
        except Exception as exc:  # noqa: BLE001 - recorded, the run goes on
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            entry["cuda_oom"] = ingest.is_cuda_out_of_memory(exc)
        entry["seconds"] = time.perf_counter() - t0
        entry["device"] = ingest.conversion_device()
        return entry

    results: List[Dict[str, Any]] = []
    t_run = time.perf_counter()
    futures: List[Future] = []
    for _ in range(max(1, args.repeat)):
        futures.extend(pool.submit(convert, path) for path in files)
    for future in as_completed(futures):
        results.append(future.result())
    wall = time.perf_counter() - t_run
    sampler.stop()
    final = sampler.sample()

    ok = [r for r in results if "error" not in r]
    pages = sum(r.get("pages_converted") or r.get("pages") or 0 for r in ok)
    by_device: Dict[str, Dict[str, Any]] = {}
    for device in sorted({r["device"] for r in results}):
        rows = [r for r in ok if r["device"] == device]
        by_device[device] = {
            "files": len(rows),
            "errors": sum(1 for r in results if r["device"] == device and "error" in r),
            "median_s_per_file": median([r["seconds"] for r in rows]),
            "median_s_per_page": median([r["seconds"] / max(1, r.get("pages_converted") or r.get("pages") or 1) for r in rows]),
            "by_class": {
                cls: {
                    "files": len(sub),
                    "median_s_per_file": median([r["seconds"] for r in sub]),
                    "median_s_per_page": median([r["seconds"] / max(1, r.get("pages_converted") or r.get("pages") or 1) for r in sub]),
                }
                for cls in sorted({r["class"] for r in rows})
                for sub in [[r for r in rows if r["class"] == cls]]
            },
        }
    errors = [r for r in results if "error" in r]
    peak_reserved = sampler.peak("reserved_mib")
    summary = {
        "label": args.label,
        "input": args.input,
        "workers": workers,
        "gpu_slots_requested": os.environ.get("INGEST_GPU_SLOTS"),
        "gpu_slots": gpu_slots,
        "docling_threads": ingest.docling_num_threads(workers),
        "tesseract_omp_limit": os.environ.get("OCR_OMP_THREAD_LIMIT"),
        "ocr_engine": os.environ.get("OCR_ENGINE", "tesseract"),
        "cuda": cuda_info,
        "files": len(files),
        "repeat": args.repeat,
        "conversions": len(results),
        "ok": len(ok),
        "errors": len(errors),
        "cuda_oom_errors": sum(1 for r in errors if r.get("cuda_oom")),
        "demoted_slots": sorted({r["slot"] for r in results if r["device_before"] == "cuda" and r["device"] == "cpu"}),
        "pages": pages,
        "warmup_seconds": warmup_seconds,
        "warmup_converters": warmup_devices,
        "wall_seconds": wall,
        "pages_per_second": pages / wall if wall else None,
        "files_per_second": len(ok) / wall if wall else None,
        "by_device": by_device,
        "vram": {
            "baseline_card_used_mib": baseline["card_used_mib"] if baseline else None,
            "after_warmup_card_used_mib": after_warmup["card_used_mib"] if after_warmup else None,
            "after_warmup_reserved_mib": after_warmup["reserved_mib"] if after_warmup else None,
            "peak_card_used_mib": sampler.peak("card_used_mib"),
            "peak_reserved_mib": peak_reserved,
            "peak_allocated_mib": sampler.peak("allocated_mib"),
            "peak_reserved_per_gpu_slot_mib": (peak_reserved / gpu_slots) if (peak_reserved and gpu_slots) else None,
            "final_card_used_mib": final["card_used_mib"] if final else None,
        },
        "error_list": [
            {k: r[k] for k in ("file", "slot", "device_before", "device", "error", "cuda_oom") if k in r} for r in errors
        ],
        "results": sorted(results, key=lambda r: (r["slot"], r["file"])),
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        f"{args.label or 'run'}: {workers} workers, {gpu_slots} GPU slots: {len(ok)}/{len(results)} conversions, "
        f"{pages} pages in {wall:.1f} s = {pages / wall if wall else 0:.2f} pages/s "
        f"({len(ok) / wall if wall else 0:.2f} files/s); errors {len(errors)} "
        f"(CUDA OOM {summary['cuda_oom_errors']}, demoted slots {summary['demoted_slots']}); "
        f"VRAM peak reserved {peak_reserved or 0:.0f} MiB"
        + (f" = {peak_reserved / gpu_slots:.0f} MiB per GPU slot" if peak_reserved and gpu_slots else "")
        + f"; card used {summary['vram']['baseline_card_used_mib'] or 0:.0f} -> {summary['vram']['peak_card_used_mib'] or 0:.0f} MiB"
    )
    for device, stats in by_device.items():
        print(
            f"  {device}: {stats['files']} files, median {stats['median_s_per_file'] or 0:.2f} s/file, "
            f"{stats['median_s_per_page'] or 0:.2f} s/page; "
            + "; ".join(
                f"{cls} {s['files']}x {s['median_s_per_page'] or 0:.2f} s/page" for cls, s in stats["by_class"].items()
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
