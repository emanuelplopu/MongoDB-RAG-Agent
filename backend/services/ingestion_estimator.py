"""Pre-ingestion estimator.

Predicts the volume (files, bytes, chunks, tokens), the wall-clock time and the
embedding cost of a full or incremental ingest for a profile's document folders
*before* the ingest runs. It is deliberately honest about uncertainty: every
figure is a rough forecast, ranges are returned alongside central values, and
the :class:`~backend.models.schemas.IngestEstimate.assumptions` list records
which inputs were measured and which were assumed.

Design notes
------------
* **File discovery** reuses :data:`src.ingestion.ingest.SUPPORTED_INGESTION_EXTENSIONS`
  and the same per-folder ``os.walk`` so the file count matches what a real run
  would process. Incremental mode skips files already present in the profile's
  documents collection, mirroring ``DocumentIngestionPipeline.ingest_documents``.
* **Tokens** prefer, in order: (1) *history* — tokens-per-byte derived from past
  runs (``ingestion_stats`` chunks + ``chunks`` collection token counts);
  (2) *sampled* — a real tokenizer run over a sample of text-extractable files;
  (3) *heuristic* — the repo's ``chars / 4`` fallback. PDFs/scans cannot be
  tokenised cheaply before Docling conversion, so coarse per-type ratios are
  used for them and the estimate is flagged ``is_rough``.
* **Time** is dominated by Docling conversion, *not* embedding throughput, so it
  is estimated from measured per-file / per-extension processing times
  (``ingestion_stats``) where available and from per-type defaults otherwise,
  then divided by the worker's concurrency. Embedding time is reported
  separately as a component derived from the latest embedding benchmark.
* All heavy filesystem work (the directory walk and the tokenizer sampling) runs
  in a worker thread via :func:`asyncio.to_thread` so the event loop stays free.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.models.schemas import IngestEstimate, IngestEstimateByType

logger = logging.getLogger(__name__)

try:  # The canonical extension filter lives with the ingestion pipeline.
    from src.ingestion.ingest import SUPPORTED_INGESTION_EXTENSIONS
except Exception:  # pragma: no cover - defensive fallback if import path shifts
    SUPPORTED_INGESTION_EXTENSIONS = frozenset({
        ".md", ".markdown", ".txt", ".pdf", ".docx", ".doc", ".wbk",
        ".pptx", ".ppt", ".xlsx", ".xls", ".html", ".htm", ".msg", ".eml",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif",
        ".mp3", ".wav", ".m4a", ".flac", ".mp4", ".avi", ".mkv", ".mov", ".webm",
    })

_MB = 1024 * 1024
_GB = 1024 * 1024 * 1024

#: Extension (lower-cased, no dot) → coarse content category.
EXTENSION_CATEGORY: Dict[str, str] = {
    "md": "text", "markdown": "text", "txt": "text",
    "html": "html", "htm": "html",
    "msg": "email", "eml": "email",
    "pdf": "pdf",
    "docx": "word", "doc": "word", "wbk": "word",
    "pptx": "slides", "ppt": "slides",
    "xlsx": "sheet", "xls": "sheet",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image",
    "webp": "image", "bmp": "image", "tiff": "image", "tif": "image",
    "mp3": "audio", "wav": "audio", "m4a": "audio", "flac": "audio",
    "mp4": "video", "avi": "video", "mkv": "video", "mov": "video", "webm": "video",
}

#: Rough embedding-tokens produced per *file byte*, by category. Used only when
#: no measured history exists. These are coarse, documented guesses — a scanned
#: PDF yields far fewer tokens per byte than a plain-text file, and OCR/transcription
#: output barely correlates with file size — so any estimate relying on these is
#: flagged ``is_rough``.
DEFAULT_TOKENS_PER_BYTE: Dict[str, float] = {
    "text": 0.25,    # ~1 byte/char, ~4 chars/token
    "html": 0.12,    # markup overhead
    "email": 0.15,
    "pdf": 0.08,     # extractable text << file size
    "word": 0.12,
    "slides": 0.03,
    "sheet": 0.05,
    "image": 0.002,  # OCR text only, highly variable
    "audio": 0.02,   # transcription, unrelated to byte size
    "video": 0.005,
}
_DEFAULT_TOKENS_PER_BYTE_FALLBACK = 0.10

#: Per-category default conversion+processing time as ``(base_seconds, seconds_per_MB)``.
#: Used only when ``ingestion_stats`` has no measured time for a type. Docling OCR
#: on PDFs and images, and Whisper transcription on audio, dominate.
DEFAULT_CONVERSION_SECONDS: Dict[str, Tuple[float, float]] = {
    "text": (0.5, 0.2),
    "html": (1.0, 0.5),
    "email": (1.5, 0.5),
    "pdf": (8.0, 6.0),
    "word": (3.0, 2.0),
    "slides": (4.0, 2.0),
    "sheet": (2.0, 1.0),
    "image": (5.0, 3.0),
    "audio": (5.0, 30.0),   # transcription is far slower than real time
    "video": (10.0, 8.0),
}
_DEFAULT_CONVERSION_FALLBACK = (3.0, 2.0)

#: Fallback embedding throughput (tokens/sec) when no benchmark exists, by provider type.
DEFAULT_THROUGHPUT_TOKENS_PER_SEC: Dict[str, float] = {
    "openai": 20000.0,
    "voyage": 15000.0,
    "gemini": 15000.0,
    "google": 15000.0,
    "llamacpp": 4000.0,
    "ollama": 3000.0,
    "vllm": 8000.0,
    "custom": 3000.0,
}
_DEFAULT_THROUGHPUT_FALLBACK = 3000.0

#: Extensions whose bytes can be decoded as text cheaply for tokenizer sampling.
_TEXT_DECODABLE = {"txt", "md", "markdown", "html", "htm", "eml"}

#: Minimal local embedding-cost table (USD per 1M tokens). The authoritative
#: table lives in :mod:`backend.services.embedding_benchmark`; this is a
#: dependency-light fallback used only if that import fails.
_FALLBACK_EMBEDDING_COSTS: Dict[str, float] = {
    "text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
    "text-embedding-ada-002": 0.10,
}


def _category_for(extension: str) -> str:
    """Return the coarse category for an extension (with or without a leading dot)."""
    ext = extension.lower().lstrip(".")
    return EXTENSION_CATEGORY.get(ext, "other")


def _embedding_cost_per_million(model: str) -> Optional[float]:
    """Look up USD-per-1M-tokens for an embedding model (None when unknown)."""
    if not model:
        return None
    try:
        from backend.services.embedding_benchmark import EMBEDDING_COSTS  # noqa: WPS433
        costs = EMBEDDING_COSTS
    except Exception:  # pragma: no cover - fallback if heavy module unavailable
        costs = _FALLBACK_EMBEDDING_COSTS
    if model in costs:
        return costs[model]
    base = model.split(":")[0].lower()
    for key, value in costs.items():
        if base == key.lower():
            return value
    return None


def human_duration(seconds: float) -> str:
    """Render a duration as a short human string (e.g. ``~2 h 15 m``, ``~9 d 6 h``, ``~2 w 3 d``).

    Weeks start at 14 days, so a nine-day run still reads ``~9 d`` rather than ``~1 w``.
    """
    seconds = max(0.0, float(seconds))
    if seconds < 1:
        return "<1 s"
    if seconds < 90:
        return f"~{round(seconds)} s"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"~{round(minutes)} m"
    hours = int(minutes // 60)
    rem_minutes = int(round(minutes - hours * 60))
    if rem_minutes == 60:
        hours += 1
        rem_minutes = 0
    if hours < 24:
        return f"~{hours} h {rem_minutes} m" if rem_minutes else f"~{hours} h"
    days = hours // 24
    rem_hours = hours % 24
    if days < 14:
        return f"~{days} d {rem_hours} h" if rem_hours else f"~{days} d"
    weeks = days // 7
    rem_days = days % 7
    return f"~{weeks} w {rem_days} d" if rem_days else f"~{weeks} w"


@dataclass
class _DiscoveredFile:
    """A single supported file found on disk."""
    path: str
    extension: str  # lower-cased, with leading dot
    size_bytes: int


def _discover_files(folders: Sequence[str]) -> Tuple[List[_DiscoveredFile], List[str]]:
    """Walk ``folders`` and collect supported files plus any missing folders.

    Mirrors ``DocumentIngestionPipeline._find_document_files`` (single ``os.walk``
    pass, same extension filter) so the count matches a real run. Runs synchronously;
    call it via :func:`asyncio.to_thread`.
    """
    found: List[_DiscoveredFile] = []
    missing: List[str] = []
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            missing.append(folder)
            continue
        for root, _dirs, files in os.walk(folder):
            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if ext in SUPPORTED_INGESTION_EXTENSIONS:
                    full = os.path.join(root, filename)
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        size = 0
                    found.append(_DiscoveredFile(path=full, extension=ext, size_bytes=size))
    return found, missing


def _relative_source(file_path: str, folders: Sequence[str]) -> str:
    """Compute the stored ``source`` for a file, matching the ingestion pipeline."""
    source = os.path.basename(file_path)
    abs_path = os.path.abspath(file_path)
    for folder in folders:
        try:
            abs_folder = os.path.abspath(folder)
        except Exception:
            continue
        if abs_path.startswith(abs_folder):
            source = os.path.relpath(file_path, folder)
            break
    return source


def _sample_tokens_per_byte(
    files: Sequence[_DiscoveredFile], sample_files: int, max_read_bytes: int = 256 * 1024
) -> Optional[Tuple[float, int]]:
    """Estimate tokens-per-byte for text-decodable files with a real tokenizer.

    Returns ``(tokens_per_byte, sampled_file_count)`` or ``None`` when no real
    tokenizer is available or no text files could be sampled. Reads at most
    ``max_read_bytes`` per file and scales the token count up by the file size.
    Runs synchronously; call it via :func:`asyncio.to_thread`.
    """
    if sample_files <= 0:
        return None
    tokenizer = _load_tokenizer()
    if tokenizer is None:
        return None

    candidates = [f for f in files if f.extension.lstrip(".") in _TEXT_DECODABLE and f.size_bytes > 0]
    if not candidates:
        return None
    # Spread the sample across the candidates deterministically.
    step = max(1, len(candidates) // sample_files)
    sampled = candidates[::step][:sample_files]

    total_bytes = 0
    total_tokens = 0.0
    counted = 0
    for item in sampled:
        try:
            with open(item.path, "rb") as handle:
                raw = handle.read(max_read_bytes)
        except OSError:
            continue
        if not raw:
            continue
        text = raw.decode("utf-8", errors="ignore")
        try:
            n_tokens = len(tokenizer.encode(text))
        except Exception:  # pragma: no cover - tokenizer edge cases
            continue
        read_bytes = len(raw)
        # Scale the sampled token count up to the whole file by byte ratio.
        scale = item.size_bytes / read_bytes if read_bytes else 1.0
        total_tokens += n_tokens * scale
        total_bytes += item.size_bytes
        counted += 1

    if counted == 0 or total_bytes == 0:
        return None
    return total_tokens / total_bytes, counted


_TOKENIZER_CACHE: List[Any] = []  # [sentinel] once resolved (may hold None)


def _load_tokenizer() -> Optional[Any]:
    """Best-effort load of a real tokenizer (tiktoken, then HF). Cached.

    Returns an object exposing ``.encode(text) -> list`` or ``None`` when neither
    backend is importable / usable offline. Never raises.
    """
    if _TOKENIZER_CACHE:
        return _TOKENIZER_CACHE[0]
    tokenizer: Optional[Any] = None
    try:  # pragma: no cover - depends on local environment
        import tiktoken

        tokenizer = tiktoken.get_encoding("cl100k_base")
    except Exception:
        tokenizer = None
    if tokenizer is None:
        try:  # pragma: no cover - depends on local environment / model cache
            from transformers import AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained("sentence-transformers/all-MiniLM-L6-v2")
        except Exception:
            tokenizer = None
    _TOKENIZER_CACHE.append(tokenizer)
    return tokenizer


class IngestionEstimator:
    """Compute a :class:`IngestEstimate` for a profile's document folders."""

    def __init__(
        self,
        db: Any,
        *,
        benchmark_db: Any = None,
        documents_collection: str = "documents",
        chunks_collection: str = "chunks",
        stats_collection: str = "ingestion_stats",
        config_collection: str = "ingestion_config",
        benchmark_collection: str = "benchmark_results",
        embedding_model: str = "text-embedding-3-small",
        embedding_provider: str = "openai",
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> None:
        """Initialise the estimator.

        Args:
            db: Async MongoDB database (motor / pymongo-async) holding the
                profile's ``documents``, ``chunks``, ``ingestion_stats`` and
                ``ingestion_config`` collections.
            benchmark_db: Async database holding ``benchmark_results``. Defaults
                to ``db``; the profile database is tried first, then this one.
            embedding_model: Active embedding model (profile override or global).
            embedding_provider: Provider type for the fallback throughput.
            chunk_size / chunk_overlap: Active chunking config, for fallbacks.
        """
        self.db = db
        self.benchmark_db = benchmark_db
        self.documents_collection = documents_collection
        self.chunks_collection = chunks_collection
        self.stats_collection = stats_collection
        self.config_collection = config_collection
        self.benchmark_collection = benchmark_collection
        self.embedding_model = embedding_model or "text-embedding-3-small"
        self.embedding_provider = (embedding_provider or "openai").lower()
        self.chunk_size = chunk_size or 1000
        self.chunk_overlap = chunk_overlap or 0

    async def estimate(
        self,
        folders: Sequence[str],
        profile_key: str,
        *,
        incremental: bool = True,
        sample_files: int = 16,
    ) -> IngestEstimate:
        """Produce a pre-ingestion estimate for ``folders``.

        Args:
            folders: Absolute (container) document folders to scan.
            profile_key: Profile these folders belong to (for history scoping).
            incremental: Skip files already present in the documents collection.
            sample_files: Max text files to tokenise for the sampled token method.

        Returns:
            A fully-populated :class:`IngestEstimate`.
        """
        folders = [f for f in (folders or []) if f]

        # 1. Discover files (off the event loop).
        discovered, missing = await asyncio.to_thread(_discover_files, folders)
        scanned_files = len(discovered)

        assumptions: List[str] = []

        # 2. Incremental skip (path-based, matching the pipeline).
        skipped_existing = 0
        if incremental and discovered:
            existing_sources = await self._existing_sources()
            if existing_sources:
                remaining: List[_DiscoveredFile] = []
                for item in discovered:
                    source = _relative_source(item.path, folders)
                    if source in existing_sources:
                        skipped_existing += 1
                    else:
                        remaining.append(item)
                discovered = remaining
                assumptions.append(
                    f"Incremental: skipped {skipped_existing} already-ingested file(s) by path; "
                    "runtime content-hash dedup may skip a few more."
                )
            else:
                assumptions.append("Incremental requested but no documents are ingested yet — treating as a full run.")

        file_count = len(discovered)
        total_bytes = sum(f.size_bytes for f in discovered)

        # Group by extension.
        by_ext: Dict[str, Dict[str, int]] = {}
        for item in discovered:
            ext = item.extension.lstrip(".")
            bucket = by_ext.setdefault(ext, {"count": 0, "bytes": 0})
            bucket["count"] += 1
            bucket["bytes"] += item.size_bytes

        # 3. History, chunk-token stats, concurrency, benchmark (async, defensive).
        history, history_scope = await self._history_by_extension(profile_key)
        avg_tokens_per_chunk = await self._avg_tokens_per_chunk()
        concurrency = await self._concurrency()
        benchmark = await self._latest_benchmark()

        tokens_per_chunk = avg_tokens_per_chunk or max(1.0, self.chunk_size / 4.0)

        # 4. Sampled tokenizer (off the event loop), best-effort.
        sampled: Optional[Tuple[float, int]] = None
        if sample_files > 0 and discovered:
            sampled = await asyncio.to_thread(_sample_tokens_per_byte, discovered, sample_files)

        # Decide whether usable history exists.
        global_cpb = self._global_chunks_per_byte(history)
        has_history_tokens = bool(history) and global_cpb is not None and avg_tokens_per_chunk is not None

        # Determine overall token method by dominant byte source.
        text_bytes = sum(
            b["bytes"] for ext, b in by_ext.items() if ext in _TEXT_DECODABLE
        )
        sampled_dominant = (
            sampled is not None and total_bytes > 0 and text_bytes >= 0.5 * total_bytes
        )
        if has_history_tokens:
            token_method = "history"
        elif sampled_dominant:
            token_method = "sampled"
        else:
            token_method = "heuristic"

        # 5. Per-extension token / chunk / time estimates.
        est_tokens = 0.0
        est_chunks = 0.0
        seq_seconds = 0.0
        seq_seconds_high = 0.0
        history_time_files = 0
        by_extension_models: List[IngestEstimateByType] = []

        for ext in sorted(by_ext, key=lambda e: by_ext[e]["bytes"], reverse=True):
            bucket = by_ext[ext]
            count = bucket["count"]
            ebytes = bucket["bytes"]
            category = _category_for(ext)
            hist = history.get(ext)

            # --- tokens per byte for this extension ---
            tpb, _tpb_src = self._tokens_per_byte_for(
                ext, category, hist, global_cpb, tokens_per_chunk, sampled, has_history_tokens
            )
            ext_tokens = ebytes * tpb
            ext_chunks = ext_tokens / tokens_per_chunk if tokens_per_chunk else 0.0

            # --- time for this extension ---
            avg_ms: Optional[float] = None
            if hist and hist.get("avg_time_ms"):
                avg_ms = float(hist["avg_time_ms"])
                max_ms = float(hist.get("max_time_ms") or avg_ms)
                ext_seconds = count * avg_ms / 1000.0
                ext_seconds_high = count * max_ms / 1000.0
                history_time_files += count
            else:
                base, per_mb = DEFAULT_CONVERSION_SECONDS.get(category, _DEFAULT_CONVERSION_FALLBACK)
                avg_mb = (ebytes / count / _MB) if count else 0.0
                per_file = base + per_mb * avg_mb
                ext_seconds = count * per_file
                ext_seconds_high = ext_seconds * 2.5

            est_tokens += ext_tokens
            est_chunks += ext_chunks
            seq_seconds += ext_seconds
            seq_seconds_high += ext_seconds_high

            by_extension_models.append(
                IngestEstimateByType(
                    extension=ext,
                    file_count=count,
                    total_bytes=ebytes,
                    estimated_chunks=int(round(ext_chunks)),
                    estimated_tokens=int(round(ext_tokens)),
                    avg_processing_time_ms=avg_ms,
                )
            )

        estimated_tokens = int(round(est_tokens))
        estimated_chunks = int(round(est_chunks))

        # 6. Time: divide sequential by concurrency; bracket the central value.
        concurrency = max(1, concurrency)
        central_seconds = seq_seconds / concurrency
        low_seconds = central_seconds * (0.7 if history_time_files else 0.5)
        high_seconds = (seq_seconds_high / concurrency)
        if high_seconds < central_seconds:
            high_seconds = central_seconds * (1.6 if history_time_files else 2.5)
        time_method = "history" if history_time_files >= max(1, file_count) * 0.5 else "defaults"

        # 7. Embedding throughput + cost.
        if benchmark and benchmark.get("tokens_per_sec"):
            tokens_per_sec = float(benchmark["tokens_per_sec"])
            benchmark_date = benchmark.get("date")
        else:
            tokens_per_sec = DEFAULT_THROUGHPUT_TOKENS_PER_SEC.get(
                self.embedding_provider, _DEFAULT_THROUGHPUT_FALLBACK
            )
            benchmark_date = None
        embedding_seconds = (estimated_tokens / tokens_per_sec) if tokens_per_sec else None

        # Token range reflects method uncertainty (wider for non-history estimates).
        low_factor = 0.75 if token_method == "history" else 0.6
        high_factor = 1.25 if token_method == "history" else 1.6
        token_low = int(round(est_tokens * low_factor))
        token_high = int(round(est_tokens * high_factor))

        cost_per_million = _embedding_cost_per_million(self.embedding_model)
        if cost_per_million:
            central_cost = (estimated_tokens / 1_000_000.0) * cost_per_million
            cost_low = (token_low / 1_000_000.0) * cost_per_million
            cost_high = (token_high / 1_000_000.0) * cost_per_million
        else:
            central_cost = cost_low = cost_high = 0.0

        # 8. Assumptions / honesty notes.
        assumptions.extend(
            self._build_assumptions(
                token_method=token_method,
                history_scope=history_scope,
                sampled=sampled,
                avg_tokens_per_chunk=avg_tokens_per_chunk,
                tokens_per_chunk=tokens_per_chunk,
                time_method=time_method,
                concurrency=concurrency,
                benchmark=benchmark,
                benchmark_date=benchmark_date,
                tokens_per_sec=tokens_per_sec,
                cost_per_million=cost_per_million,
                missing=missing,
                embedding_seconds=embedding_seconds,
            )
        )

        is_rough = (
            token_method != "history"
            or time_method != "history"
            or benchmark_date is None
        )

        return IngestEstimate(
            profile_key=profile_key,
            folders=list(folders),
            missing_folders=[m for m in missing if m],
            incremental=incremental,
            scanned_files=scanned_files,
            skipped_existing=skipped_existing,
            file_count=file_count,
            total_bytes=total_bytes,
            total_size_gb=round(total_bytes / _GB, 3),
            estimated_chunks=estimated_chunks,
            estimated_tokens=estimated_tokens,
            estimated_tokens_low=max(0, token_low),
            estimated_tokens_high=max(estimated_tokens, token_high),
            token_method=token_method,
            estimated_time_seconds=round(central_seconds, 1),
            estimated_time_low_seconds=round(low_seconds, 1),
            estimated_time_high_seconds=round(high_seconds, 1),
            estimated_time_human=human_duration(central_seconds),
            estimated_embedding_seconds=round(embedding_seconds, 1) if embedding_seconds is not None else None,
            concurrency=concurrency,
            time_method=time_method,
            estimated_cost_usd=round(central_cost, 4),
            estimated_cost_low_usd=round(cost_low, 4),
            estimated_cost_high_usd=round(cost_high, 4),
            embedding_model=self.embedding_model,
            cost_per_million_tokens=cost_per_million,
            embedding_throughput_tokens_per_sec=round(tokens_per_sec, 1) if tokens_per_sec else None,
            benchmark_date=benchmark_date,
            is_rough=is_rough,
            assumptions=assumptions,
            by_extension=by_extension_models,
        )

    # ------------------------------------------------------------------ helpers

    def _tokens_per_byte_for(
        self,
        ext: str,
        category: str,
        hist: Optional[Dict[str, Any]],
        global_cpb: Optional[float],
        tokens_per_chunk: float,
        sampled: Optional[Tuple[float, int]],
        has_history_tokens: bool,
    ) -> Tuple[float, str]:
        """Pick the best tokens-per-byte source for one extension."""
        # 1. Per-extension history (chunks/byte * tokens/chunk).
        if hist and hist.get("total_bytes") and hist.get("total_chunks") is not None:
            cpb = hist["total_chunks"] / hist["total_bytes"] if hist["total_bytes"] else 0.0
            if cpb > 0:
                return cpb * tokens_per_chunk, "history"
        # 2. Global history ratio.
        if has_history_tokens and global_cpb:
            return global_cpb * tokens_per_chunk, "history-global"
        # 3. Sampled tokenizer, for text-decodable types only.
        if sampled is not None and ext in _TEXT_DECODABLE:
            return sampled[0], "sampled"
        # 4. Per-category default.
        return DEFAULT_TOKENS_PER_BYTE.get(category, _DEFAULT_TOKENS_PER_BYTE_FALLBACK), "default"

    @staticmethod
    async def _aggregate_to_list(collection: Any, pipeline: List[Dict[str, Any]], length: int) -> List[Dict[str, Any]]:
        """Run an aggregation across either Motor or PyMongo-Async drivers.

        PyMongo Async's ``aggregate`` is a coroutine returning the cursor, while
        Motor's returns the cursor directly; this awaits whichever is produced.
        """
        cursor = collection.aggregate(pipeline)
        if inspect.isawaitable(cursor):
            cursor = await cursor
        return await cursor.to_list(length)

    @staticmethod
    def _global_chunks_per_byte(history: Dict[str, Dict[str, Any]]) -> Optional[float]:
        """Aggregate chunks-per-byte across all history extensions."""
        total_chunks = sum((h.get("total_chunks") or 0) for h in history.values())
        total_bytes = sum((h.get("total_bytes") or 0) for h in history.values())
        if total_bytes > 0 and total_chunks > 0:
            return total_chunks / total_bytes
        return None

    async def _existing_sources(self) -> set:
        """Fetch the set of already-ingested ``source`` paths. Never raises."""
        existing: set = set()
        try:
            cursor = self.db[self.documents_collection].find({}, {"source": 1})
            async for doc in cursor:
                source = doc.get("source")
                if source:
                    existing.add(source)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Estimator: could not read existing sources: %s", exc)
        return existing

    async def _history_by_extension(
        self, profile_key: str
    ) -> Tuple[Dict[str, Dict[str, Any]], str]:
        """Aggregate per-extension processing history from ``ingestion_stats``.

        Tries the profile-scoped history first; if empty, falls back to all
        profiles. Returns ``(by_extension, scope)`` where ``scope`` is one of
        ``"profile"``, ``"global"`` or ``"none"``.
        """
        for match, scope in (({"profile_key": profile_key}, "profile"), ({}, "global")):
            match_stage = dict(match)
            match_stage["success"] = True
            pipeline = [
                {"$match": match_stage},
                {
                    "$addFields": {
                        "extension": {
                            "$toLower": {"$arrayElemAt": [{"$split": ["$file_name", "."]}, -1]}
                        }
                    }
                },
                {
                    "$group": {
                        "_id": "$extension",
                        "count": {"$sum": 1},
                        "avg_time_ms": {"$avg": "$processing_time_ms"},
                        "max_time_ms": {"$max": "$processing_time_ms"},
                        "total_chunks": {"$sum": "$chunks_created"},
                        "total_bytes": {"$sum": "$file_size_bytes"},
                    }
                },
            ]
            try:
                rows = await self._aggregate_to_list(self.db[self.stats_collection], pipeline, 200)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Estimator: history aggregation failed (%s): %s", scope, exc)
                rows = []
            if rows:
                out = {
                    r["_id"]: {
                        "count": r.get("count", 0),
                        "avg_time_ms": r.get("avg_time_ms"),
                        "max_time_ms": r.get("max_time_ms"),
                        "total_chunks": r.get("total_chunks", 0),
                        "total_bytes": r.get("total_bytes", 0),
                    }
                    for r in rows
                    if r.get("_id")
                }
                if out:
                    return out, scope
        return {}, "none"

    async def _avg_tokens_per_chunk(self) -> Optional[float]:
        """Average ``token_count`` over the chunks collection. None when empty."""
        pipeline = [
            {"$match": {"token_count": {"$gt": 0}}},
            {"$group": {"_id": None, "avg": {"$avg": "$token_count"}}},
        ]
        try:
            rows = await self._aggregate_to_list(self.db[self.chunks_collection], pipeline, 1)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Estimator: avg tokens/chunk failed: %s", exc)
            return None
        if rows and rows[0].get("avg"):
            return float(rows[0]["avg"])
        return None

    async def _concurrency(self) -> int:
        """Read the worker's ``max_concurrent_files`` (default 2)."""
        try:
            doc = await self.db[self.config_collection].find_one({"_id": "performance_config"})
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Estimator: could not read concurrency config: %s", exc)
            doc = None
        if doc and doc.get("max_concurrent_files"):
            try:
                return max(1, int(doc["max_concurrent_files"]))
            except (TypeError, ValueError):
                pass
        return 2

    async def _latest_benchmark(self) -> Optional[Dict[str, Any]]:
        """Find the most recent benchmark metric for the active embedding model.

        Returns ``{"tokens_per_sec", "cost_per_million", "date"}`` or ``None``.
        Searches the profile database first, then ``benchmark_db``.
        """
        databases = [self.db]
        if self.benchmark_db is not None and self.benchmark_db is not self.db:
            databases.append(self.benchmark_db)
        base_model = self.embedding_model.split(":")[0].lower()
        for database in databases:
            try:
                cursor = (
                    database[self.benchmark_collection]
                    .find({"kind": {"$ne": "checks"}})
                    .sort("timestamp", -1)
                    .limit(25)
                )
                docs = await cursor.to_list(length=25)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Estimator: benchmark lookup failed: %s", exc)
                docs = []
            for doc in docs:
                for metric in doc.get("results", []) or []:
                    model = (metric.get("model") or "")
                    if not metric.get("success", True):
                        continue
                    if model.lower() != self.embedding_model.lower() and model.split(":")[0].lower() != base_model:
                        continue
                    tokens = metric.get("tokens_processed") or 0
                    emb_ms = metric.get("embedding_time_ms") or metric.get("total_time_ms") or 0
                    if tokens > 0 and emb_ms > 0:
                        return {
                            "tokens_per_sec": tokens / (emb_ms / 1000.0),
                            "cost_per_million": metric.get("cost_estimate_usd"),
                            "date": doc.get("timestamp"),
                        }
        return None

    def _build_assumptions(
        self,
        *,
        token_method: str,
        history_scope: str,
        sampled: Optional[Tuple[float, int]],
        avg_tokens_per_chunk: Optional[float],
        tokens_per_chunk: float,
        time_method: str,
        concurrency: int,
        benchmark: Optional[Dict[str, Any]],
        benchmark_date: Optional[str],
        tokens_per_sec: float,
        cost_per_million: Optional[float],
        missing: Sequence[str],
        embedding_seconds: Optional[float],
    ) -> List[str]:
        """Assemble the human-readable assumptions list."""
        notes: List[str] = []

        if token_method == "history":
            notes.append(
                f"Tokens from {history_scope} run history "
                f"(~{tokens_per_chunk:.0f} tokens/chunk measured)."
            )
        elif token_method == "sampled":
            notes.append(
                f"Tokens from a real tokenizer over {sampled[1]} sampled text file(s), "
                "extrapolated by bytes; PDFs/images use coarse per-type ratios."
            )
        else:
            notes.append(
                "Tokens from coarse per-type byte ratios (chars/4 for text); "
                "no run history or tokenizer sample available — treat as very rough."
            )

        if time_method == "history":
            notes.append(
                f"Time from measured per-type processing history, divided by {concurrency} concurrent file(s)."
            )
        else:
            notes.append(
                f"Time from per-type defaults (Docling/OCR/transcription dominate), "
                f"divided by {concurrency} concurrent file(s) — no measured history for these types."
            )
        notes.append("Docling conversion of PDFs/scans dominates wall-clock and varies widely.")

        if benchmark_date:
            notes.append(
                f"Embedding throughput/cost based on the benchmark from {benchmark_date}; "
                "run 'quellexctl bench embeddings' to refresh."
            )
        else:
            notes.append(
                f"No embedding benchmark found for '{self.embedding_model}' — used a default "
                f"~{tokens_per_sec:.0f} tokens/sec; run 'quellexctl bench embeddings' for a real rate."
            )

        if embedding_seconds is not None:
            notes.append(
                f"Embedding alone is {human_duration(embedding_seconds)}, a small part of wall-clock "
                "(it overlaps conversion)."
            )

        if cost_per_million:
            notes.append(
                f"Cost = tokens / 1M x ${cost_per_million:.4f} for '{self.embedding_model}'."
            )
        else:
            notes.append(
                f"No per-token cost for '{self.embedding_model}' (local/free model) — cost shown as $0."
            )

        if missing:
            notes.append(
                "Some configured folders were missing/unreachable and skipped: "
                + ", ".join(m for m in missing if m)
            )

        return notes
