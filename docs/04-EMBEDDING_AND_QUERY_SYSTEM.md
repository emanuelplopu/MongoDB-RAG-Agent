# Embedding and Query Response System

This document provides a detailed description of how the RecallHub RAG (Retrieval-Augmented Generation) system processes documents for embedding and handles query responses.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Document Ingestion Pipeline](#document-ingestion-pipeline)
3. [Embedding Generation Process](#embedding-generation-process)
4. [Query Processing Pipeline](#query-processing-pipeline)
5. [Search Methods](#search-methods)
6. [Response Generation](#response-generation)
7. [Advanced Agent Workflow](#advanced-agent-workflow)
8. [System Prompts](#system-prompts)

---

## System Overview

RecallHub is an agentic RAG system that combines MongoDB Atlas Vector Search with LLM-powered conversational AI. The system processes documents through an ingestion pipeline, stores them with vector embeddings in MongoDB, and retrieves relevant context to answer user queries intelligently.

**Key Components:**
- **Document Ingestion Pipeline**: Converts documents to searchable chunks with embeddings
- **Embedding Service**: Generates vector representations of text using OpenAI or Ollama
- **Search Service**: Provides semantic, text, and hybrid search capabilities
- **Agent System**: Orchestrates search and response generation with tool-calling LLMs
- **Federated Search**: Enables multi-database search with access control

---

## Document Ingestion Pipeline

### Overview Flow

```
File Discovery
      │
      ▼
Format Detection
      │
      ├── Documents (PDF, DOCX, etc.) ──▶ Docling Converter
      ├── Images (PNG, JPG, etc.) ─────▶ OCR Pipeline
      ├── Audio (MP3, WAV, etc.) ──────▶ Whisper Transcription
      └── Video ───────────────────────▶ Frame Extraction + Audio
      │
      ▼
Markdown Output (unified format)
      │
      ▼
HybridChunker (Docling)
      │
      ├── Token-aware splitting
      ├── Document structure preservation
      ├── Semantic boundary respect
      └── Heading context inclusion
      │
      ▼
Embedding Generation
      │
      ▼
MongoDB Storage (documents + chunks collections)
```

### Phase 1: File Discovery and Format Detection

The ingestion pipeline scans configured document folders and classifies files by extension:
- **Documents**: PDF, DOC, DOCX, TXT, MD, HTML, XLSX, XLS, PPTX, PPT
- **Images**: PNG, JPG, JPEG, GIF, WEBP, SVG, BMP
- **Audio**: MP3, WAV, FLAC, M4A, OGG, WMA
- **Video**: MP4, AVI, MKV, MOV, WMV, WEBM

### Phase 2: Document Conversion

**Docling Converter** handles document processing:
- Extracts text while preserving structure (headings, tables, lists)
- Maintains document hierarchy and formatting
- Outputs normalized Markdown format
- Handles OCR for scanned documents when needed
- Loads its models (layout analysis, TableFormer, RapidOCR) from
  `DOCLING_ARTIFACTS_PATH`, which the backend/worker base image fills at build
  time (`backend/Dockerfile.base`), so conversion never downloads from
  huggingface.co at runtime and works on offline installs; see
  `docs/15-DOCKER_BUILD_GUIDE.md` → "Docling models in the base image"

**OCR engine for scanned pages** (`OCR_ENGINE`, `OCR_LANGUAGES`; applied by
`create_document_converter()` for PDFs and `create_image_converter()` for
image files in `src/ingestion/ingest.py`, read from both `src/settings.py`
and `backend/core/config.py`):

| `OCR_ENGINE` | Docling options | Notes |
|---|---|---|
| `tesseract` (default) | `TesseractCliOcrOptions(lang=[...])` | Tesseract 5 binary + `tesseract-ocr-deu`/`-eng` packs are baked into `backend/Dockerfile.base`. 0.99 word recall on the synthetic law-firm scans. |
| `rapidocr` | `RapidOcrOptions()` | Docling's bundled ONNX engine; no language hint possible. What `auto` picked before: 0.84 word recall, whole legible lines turned into letter soup, tokens glued (`1110Wien`). |
| `easyocr` | `EasyOcrOptions(lang=[...])` | Needs the `easyocr` package (not installed). Language codes are derived from the ISO 639-2 setting (`deu` → `de`). |
| `auto` | Docling default (`OcrAutoOptions`) | Lets Docling choose; languages are ignored. |

`OCR_LANGUAGES` is a comma-separated list of ISO 639-2 codes (`deu,eng` by
default; `de`/`ger` style aliases are normalised). Tesseract needs the matching
`tesseract-ocr-<lang>` package in the image. The resolved engine is logged once
per worker process (`Docling OCR engine: tesseract lang=deu+eng`). Changing the
engine only affects documents ingested afterwards: the stored `content` of a
scan *is* its OCR text, so already ingested scans must be re-ingested.

After conversion, `src/ingestion/ocr_repair.py` restores the section sign
of statute references that Tesseract misreads (`$ 94 ÄrzteG`, `$$ RAO`,
`5 10 AVG`, `8 65 Zi 1 lit c StPO` → `§ …`), on the converted content and
on the Docling document items the chunker reads; only text followed by a
section number and a statute marker (`Abs`/`Z`/`lit`/`ff`, a known law
abbreviation, `…gesetz`/`…ordnung`) is touched, amounts never are. Like the
engine, it reaches only documents ingested after it shipped.
Evidence and the measurement harness: `docs/24-OCR_SCAN_QUALITY_CHECK.md`.

**OCR resolution, orientation and content layers** (all read from both
settings surfaces, passed through by `docker-compose.yml`; evidence in
`docs/25-OCR_SCALE_TEST.md`):

| setting | default | what it does |
|---|---|---|
| `OCR_OSD_MIN_CONFIDENCE` | `10` | Tesseract's per-rectangle orientation detection is applied outright only at this confidence or above; below it the rectangle is OCRed both ways and the better reading kept (`src/ingestion/ocr_tesseract.py`, Docling kind `tesseract_recallhub`). `0` restores Docling's behaviour, which flipped upright Courier pages and tables by 180°. The same model OCRs a whole page when the layout model finds no text region on it. |
| `osd_once_per_page` (`RecallHubTesseractCliOcrOptions`) | `true` | Orientation detection runs once on the whole page image and its result is applied to every OCR rectangle of that page, instead of one `tesseract --psm 0` per rectangle (Docling). Per-rectangle OSD failed with "Too few characters" on 37 % of the rectangles of the diejuristin corpus (logos, stamps, signatures: ~8,000 ERROR lines per 3 h) and cost a Tesseract process each. Pages whose OCR rectangles cover less than `osd_min_area_pt` (20,000 pt², a logo or a signature) skip OSD and are read upright; a failed page OSD reads the page upright with a DEBUG line. The confidence guard applies to the page result and marks every rectangle for the both-ways retry. The OCR itself (`deu+eng`) is unchanged. |
| `OCR_SCAN_DPI` | `216` | Raster resolution of PDF pages for OCR (Docling's `scale=3.0`). 300 dpi measured worse and slower. |
| `OCR_IMAGE_MAX_MPX` | `8` | Megapixel budget of the OCR bitmap for image files. Docling OCRs images at 3× their size in points, i.e. a phone photo without a DPI tag at 3× its pixel size (~140 Mpx, 20-60 s); the scale is reduced per image to stay within the budget, A4 scan images (4.5 Mpx at 3×) are untouched. `0` = no cap. |
| `OCR_PSM`, `OCR_TESSDATA_PATH` | empty | Tesseract page segmentation mode and an alternative traineddata directory (e.g. `tessdata_best`). Both were measured and not adopted; available for experiments. Image files Docling cannot load (MPO containers, JPEGs with a `(0, 0)` DPI tag) are re-saved as PNG before conversion. |
| `INGEST_TABLE_STRUCTURE` | `true` | Docling's table-structure stage (TableFormer). Off keeps table text as plain lines and saves ~40 % of the conversion time; on produces Markdown tables but drops the OCR cells it cannot place. Measured, left on (product decision). |
| `OCR_RECT_DILATION` | `20` | Dilation of the layout clusters before touching ones are merged into one OCR rectangle (Docling's value). Smaller values keep stacked tables apart but measured worse overall. |
| `INGEST_PAGE_FURNITURE` | `false` | Keep Docling's furniture layer (page headers/footers: Geschäftszahl headers, `Hinterlegt am …` ERV stamps, IBAN/UID footers) in the stored Markdown **and** in the HybridChunker's chunks. Docling drops it by default for every format; it never reached the index before. |
| `OCR_IMAGE_NOISE_GATE` | `false` | Store `[Image: … - no readable text]` instead of letter soup for image files whose OCR output has a word-like token ratio < 0.45 and OCR confidence < 0.65 (or ratio < 0.25); photographed letters, forms and screenshots pass. |
| `INGEST_SKIP_IMAGE_ONLY_PDF` | `false` | Image-only PDFs (pure scans without a text layer, ~30 % of the PDFs of a law-firm NAS) are OCRed like every other PDF, in the concurrent worker loop (`max_concurrent_files > 1`, the default of 2) as in the sequential one. `true` restores the old pre-check that skips them before OCR and records them as `image_only_pdf` without content — only for text-only profiles. A blank scan that is OCRed ends as `no_chunks`. See `docs/26-FORMAT_EDGE_AUDIT.md`. |
| `INGEST_THREAD_POOL_WORKERS` | `4` | Size of the pipeline's shared thread pool (hashing, Docling conversion, folder discovery) for a run that passes none (CLI). The Configuration page's `thread_pool_workers` sets it per run, and the pool is never smaller than `max_concurrent_files`. See "Parallel conversions" below. |
| `INGEST_DOCLING_THREADS` | derived | CPU threads per Docling model (layout, TableFormer). Default: the cores shared evenly across the pool, at most Docling's 4 (`OMP_NUM_THREADS` pins it too). |
| `OCR_OMP_THREAD_LIMIT` | derived | OpenMP threads per Tesseract process. Default: the cores shared evenly across the pool, 1-4; `0` leaves OpenMP free. Applied through a generated wrapper command (`tesseract_command()` in `src/ingestion/ocr_tesseract.py`), so it never caps PyTorch in the ingestion process. |
| `INGEST_PROCESS_ISOLATION` | `true` | Convert in worker processes the concurrent loop kills when a file's budget runs out (`src/ingestion/conversion_workers.py`). Runs started by the API or the ingestion worker take the Configuration page's "Process isolation" (`process_isolation_enabled`) instead; this is the fallback for runs without a stored performance config (CLI). See "Timed-out conversions" below. |
| `INGEST_STATS_FLUSH_FILES`, `INGEST_STATS_FLUSH_SECONDS` | `25`, `60` | How often the per-file stats of a run are written to `ingestion_stats` / `failed_documents`: every N recorded files or every M seconds, whichever comes first (and once more when the run ends). See "Per-file stats during the run" below. |

Converters are cached per worker thread and configuration
(`create_document_converter()` / `create_image_converter()`); Docling keeps
its initialised pipeline per converter instance, so the former per-file
construction reloaded the layout model for every scanned file.

**Parallel conversions (throughput).** Every run converts on one shared
thread pool (`DocumentIngestionPipeline.configure_executor()`), sized from the
performance config the Configuration page saves
(`ingestion_config.performance_config`; API
`GET`/`POST /api/v1/system/ingestion-performance`): `thread_pool_workers`
threads, never fewer than `max_concurrent_files`, at most 32. Until 2026-10-23
the pool was fixed at 2, so no setting could run more than two conversions at
a time, and a job started from the UI (it runs inside the API process, not in
the ingestion worker) fell back to one file at a time while no config was
saved; both now follow the settings defaults (2 files, 4 threads). The CPU
threads of the Docling models and of each Tesseract process are shared across
the pool (table above), because Tesseract 5's OpenMP otherwise oversubscribes
the CPU.

Measured 2026-10-23 on an Ultra 9 285HX (23 cores to Docker, synthetic 150-DPI
scan pages, Tesseract deu+eng, tables on): 1 conversion at a time 0.28 pages/s,
2 → 0.50, 4 → 0.85, 8 → 1.36; 12 with free OpenMP threads collapsed to 0.30
(22 cores busy, 39 s per page), while 16 with one OpenMP thread per Tesseract
reached 2.00 pages/s on 13 busy cores, ~0.6 GB RSS per thread. Born-digital
PDFs cost ~0.4 s/page for the layout model on the CPU (4 threads) and
~0.1 s/page on the GPU; OCR time does not change with a GPU. Size the pool to
the cores you can spare and to the memory of the Docker VM (`.wslconfig` on
Windows: Docker Desktop gets a fraction of the host's RAM, and each conversion
thread needs ~0.6 GB on top of MongoDB's cache), then watch `docker stats`
during the first minutes of a run.

**Load profiles (office hours).** A machine that stands in the office is loud
and hot at 16 conversions around the clock (2026-10-27: an Ultra 9 285HX held at
90 W, hottest core 92-94 °C, for four days). The performance config therefore
carries a `load_profile` (Configuration page → Ingestion → "Load Profile";
`backend/services/ingestion_schedule.py`):

- `server_room` (default): `max_concurrent_files` files at once around the
  clock, as before.
- `office`: on the office days (`office_hours.weekdays`, 0 = Monday) between
  `office_hours.start` and `office_hours.end` (wall-clock time in
  `office_hours.timezone`, default `INGESTION_OFFICE_TIMEZONE` =
  `Europe/Vienna`) a run converts at most `office_hours.max_concurrent_files`
  files at once (default 8, never above `max_concurrent_files`); at night, from
  the end of the last office day to the start of the next (Friday evening to
  Monday morning with the default days) it uses the full `max_concurrent_files`.
- `office_lite`: the same hours and days with
  `office_hours.lite_max_concurrent_files` (default 4) files at once, for a
  machine that stands next to the people working.

The limit follows the clock inside a running job: the concurrent loop takes it
from a `SlotLimiter` (`src/ingestion/ingest.py`) whose `limit()` callable is
`ConcurrencyController.current` (`slot_limit` of `ingest_documents`), read
before every file. A lowered limit takes effect as the running files finish
(nothing is interrupted), a raised one admits waiting files at once. The API
runner and the ingestion worker re-read `performance_config` every minute, so
switching the profile or its hours on the Configuration page reaches the
running job without a restart. The thread pool, the per-conversion CPU threads
and the conversion worker processes stay sized for `max_concurrent_files`,
which is why a raised `max_concurrent_files` still needs a new run; idle
worker processes cost memory, not CPU. The job status (`/api/v1/ingestion/status`)
reports `load_period` (`server_room`, `office_hours`, `off_hours`) and
`concurrency_slots`, and the Ingestion page shows "office hours: N files at
once" next to the ETA while the lower limit is in force. Jobs run from the
CLI pass no `slot_limit` and use every slot.

Why a profile and not simply a smaller `max_concurrent_files`: the CPU threads
of each conversion are the cores shared across the pool (`docling_num_threads`,
`OCR_OMP_THREAD_LIMIT`, table above), so a run started with 8 slots gives every
conversion three threads and loads the CPU like 16 slots with one. Measured on
the client's MS-02 Ultra (Ultra 9 285HX, 24 threads, host agent samples,
diejuristin corpus, 2026-10-27/28): 16 slots 96 % CPU, 88 W package, 87 °C
package, hottest core 88 °C average / 96 °C peak; 8 slots (a new run sized for
8) 96 %, 88 W, 87 °C, the same; `office_lite` with 4 of 16 slots 43-51 % CPU,
65-74 W, 81-84 °C package, hottest core 82-86 °C average / 92 °C peak, the API
container down from 13 to 3-5 cores. Power and load halve; the temperature
falls less, because the few busy cores run at their boost clock. Going quieter
than that is a matter of the CPU power limit (BIOS or the Windows power plan's
maximum processor state), not of the ingestion settings. The host agent records
no CPU or case fan speed on this board (LibreHardwareMonitor finds no Super I/O
on it); only the GPU fan is sampled, as a percentage.

**Clock cap (tested 2026-10-28, not recommended).** `scripts/Set-CpuBoostCap.ps1
-Apply` (elevated) sets the active Windows power plan to 99 % maximum processor
state with the boost mode disabled, `-Restore` undoes it. On the MS-02 Ultra the
CPU then sits at 2.24 GHz: 28-35 W instead of 83-88 W, package 54-58 °C instead
of 78-87 °C, hottest core 58-62 °C instead of 92-96 °C, within 15 s. But the
conversions slow down far more than the clock ratio suggests: the big scanned
PDFs that take 250-400 s at full speed no longer fit their budget, and the run
indexed 5 pages/min at 16 slots × 1 thread, 5 pages/min at 8 slots × 2 threads
(3,600 s `max_timeout`), with most files cancelled at their budget, against
91-130 pages/min uncapped. A profile of fewer slots keeps 75-90 % of the pace at
a few degrees less; the cap keeps the machine cool at a tenth of the pace. For a
cool *and* productive machine the lever is a sustained power limit (PL1) in the
BIOS, which keeps the boost for short bursts, not the Windows plan's all-or-
nothing boost switch. Measured rows (pages/min, files finished, CPU, package
power, package temperature, hottest-core peak):

| Run | pages/min | files ok | CPU | W | pkg °C | core peak °C |
|---|---|---|---|---|---|---|
| 16 slots × 1 thread, no cap | 91-130 | 566 of 657 | 87-96 % | 87-88 | 80-87 | 90-96 |
| 8 of 16 slots × 1 thread, no cap | 120 | 25 of 44 | 73 % | 83 | 78 | 92 |
| 4 of 16 slots × 1 thread, no cap (`office_lite`) | 15-46 | 19 of 118 | 51-60 % | 75-78 | 80-82 | 92 |
| 8 of 16 slots × 1 thread, cap | 65 (files begun uncapped) | 5 of 21 | 49 % | 28 | 54 | 58 |
| 16 of 16 slots × 1 thread, cap | 5 | 2 of 39 | 81 % | 35 | 58 | 62 |
| 8 slots × 2 threads, cap, 3,600 s budget | 5 | 4 of 58 | 74 % | 33 | 56 | 58 |

The same test found that `POST /api/v1/system/ingestion-performance` replaced
the whole `performance_config` document and so dropped the `timeout_config`
saved by `/ingestion-timeout-config`; it now merges (`$set`).

**Timed-out conversions (process isolation).** Until 2026-10-24 the per-file
timeout of the concurrent loop cancelled an asyncio future while the thread
converting the file kept running: the slot went to the next file, more
conversions ran than the pool was sized for, every file slowed down, more
files timed out, and their retries redid the work (F1 of
`docs/system-validation/INGESTION_PROMPT_TEST_LOG_2026-10-24.md`: 209 timeouts
in 2 h at concurrency 16, the longest conversion 2,160 s against a 902 s
limit, inserts down 20×). The stored `process_isolation_enabled` flag was
read by nothing. Now:

- With the Configuration page's "Process isolation" on (the default;
  `INGEST_PROCESS_ISOLATION` for runs without a stored config) every file
  slot of a run owns one worker process (`src/ingestion/conversion_workers.py`,
  `spawn` context, started on its first file, warmed up with the PDF pipeline
  before it takes requests). The worker runs `_read_document`; chunking,
  embedding and the MongoDB writes stay in the API or ingestion-worker
  process. On a timeout, stop or shutdown the process is killed and the next
  file on that slot starts a fresh one; the model reload (10-20 s on a CPU)
  is not charged to that file. Each worker applies the run's CPU-thread
  sharing (`docling_num_threads`, `OCR_OMP_THREAD_LIMIT`, computed for the
  number of workers) and the stored accelerator config, and loads the models
  for itself (on a GPU: once per worker, as the threads did). Measured
  2026-10-24 on Windows, CPU: an idle worker with the layout and TableFormer
  models loaded holds ~1.1 GB RSS, plus the working set of a conversion; the
  parent process no longer holds any converter.
- Off, conversions run on the thread pool as before, but a timed-out
  conversion keeps its slot until its thread ends (a thread cannot be
  interrupted), so the pool never runs more conversions than there are
  slots, and the retry of that file waits for its first thread to end.
- The budget (`calculate_file_timeout`) starts when the conversion actually
  starts, not when the file was queued, and is multiplied by
  `conversion_budget_factor`: the formula describes a conversion that has
  Docling's default 4 threads to itself; with 16 conversions on 20 cores each
  has one thread, so the factor is 4 (8 conversions: 2 threads, factor 2;
  pinned thread counts that oversubscribe the cores count too). The
  `max_timeout` cap (30 min by default) stays absolute. A file whose
  conversion has not started within one budget times out all the same, so
  nothing stuck before the conversion can hold a slot forever.

**Per-file stats during the run.** `ingestion_stats` and `failed_documents`
were written only after the last file (F2), so during a multi-week job the
failed-documents page, the analytics endpoints and the retry options
(`retry_timeouts`, `retry_no_chunks`) had nothing to work with, and a restart
lost everything held in memory. They are now written every
`INGEST_STATS_FLUSH_FILES` recorded files or `INGEST_STATS_FLUSH_SECONDS`
seconds (25 / 60; `IngestionStatsRecorder` in `src/ingestion/ingest.py`), once
more when the run ends or is stopped, and a batch the database refused is kept
for the next flush. After every flush the job's `timeout_files`,
`no_chunks_files`, `error_files`, `image_only_pdfs`, `duplicates_skipped` and
`failed_files` are set from what was written
(`backend/services/ingestion_counters.py`; a resumed job adds what its earlier
runs recorded), so `GET /api/v1/ingestion/status` and the runs list show them
while the job runs instead of zeros until its end.

**Resume without redoing failures (F23).** The incremental filter only knew the
`documents` collection, so after a restart every file that had timed out,
errored or produced no chunks looked new and was processed again (553 timeouts
on 451 files in the first 4 h after the 2026-10-25 redeploy). A resumed job now
skips the files its own `failed_documents` records name (`failed_paths_of_job`
in `src/ingestion/ingest.py`, applied by the pipeline's queue build and by the
API's `_build_pending_files_queue`), unless the request's `retry_timeouts` /
`retry_errors` / `retry_no_chunks` / `retry_image_only_pdfs` flag asks for that
error type or the record was marked resolved. The discovery log line counts them
as "failed earlier in this job".

**Reader placeholders are never stored (F26).** A file neither Docling nor the
raw-text fallback could read used to be stored as a document whose whole content
was `[Error: Could not read file <name>]`, chunked and embedded; it counted as
ingested and a question naming it got the error string as its source. The
pipeline now raises `UnreadableFileError` for `[Error: …]` / `[Skipped: …]`
placeholders (recorded as `error` in `failed_documents`, so "retry errors"
re-queues the file) and treats `[Document: … - no text content]` as an empty
file (no chunks, no warning). Existing placeholder documents are removed with
`quellexctl ingest purge-placeholders --profile <key> [--apply]`
(`backend/services/placeholder_purge.py`: deletes the documents and their
chunks, dry run by default, records each file in `failed_documents`).

**Pure scans in the stats (F9).** Every PDF that ends without chunks or as a
stored document is checked for a text layer (`is_image_only_pdf`, pypdfium, a
few ms). A pure scan whose OCR found nothing is recorded as `image_only_pdf`
(with the pdfium reason) instead of `no_chunks`, so it shows up under that type
in `/failed-documents/summary` and in the job's `image_only_pdfs` counter; a
pure scan whose OCR produced text carries `image_only_pdf: true` on its
`ingestion_stats` record and is counted in the job's `image_only_pdfs_ocr`
(`/failed-documents/summary` reports the total as `image_only_pdfs_ocr_ok`).
Files whose content is empty (Outlook `ATT0000x.txt` placeholders, frameset
pages) log "No chunks created" at INFO instead of WARNING.

**UI-started jobs run in the worker (F7).** `POST /api/v1/ingestion/start`
used to run every job inside the API process (1,400 % CPU, 45 GB RSS, `/health`
timeouts, the worker container idle). The job is now queued `PENDING` with
`runner: worker` for the ingestion worker to claim when
`INGESTION_UI_JOB_RUNNER` is `worker`, or `auto` (the default) and a worker has
reported itself alive within three heartbeats (`backend/services/worker_liveness.py`
reads `activity_current`); `api` keeps the in-process run. The status endpoint
shows the queued/claimed job from the job document (phase `queued` until the
worker claims it), and the pause, resume and stop endpoints reach a worker job
through its `control_command` (a job stopped before it was claimed simply ends).

**One ETA.** `GET /api/v1/ingestion/status`, the `/events/stream` metrics and the
machine-stats widget (`/api/v1/telemetry/overview`) share
`backend/services/ingestion_eta.py`: the runners record how many files of each
extension the run queued (`queue_by_extension`) and finished
(`processed_by_extension`); the estimator reads each extension's pace from
`ingestion_stats` (files per minute over the last hour, else over the time that
extension was being processed, else the job's overall pace; cached 60 s per job)
and sums `remaining / pace`. The status payload carries the breakdown as `eta`
(`by_extension`, `unknown_remaining` for extensions without any pace yet) next
to `estimated_remaining_seconds`; the ingestion page lists the largest
remaining types and the widget shows them in the pace row's tooltip. Until
2026-10-26 the status page extrapolated `elapsed / progress` and the widget
divided the remaining files by the last hour's pace; because the queue is
sorted by file type the two disagreed by weeks (2.5 weeks vs. 58-72 days).

**Copies within one run.** The hash of every stored file was added to the
de-duplication map, but identical files sort next to each other (same type and
size) and started in parallel slots before either was stored, so both were
converted, embedded and stored (F4: 17-20 % redundant documents in the
sampled windows). A file whose hash is being processed by another slot is now
deferred and processed after that file's outcome is known: skipped as
`Duplicate of: …` when it was stored, converted itself when it failed.

A GPU in the worker or backend container (`deploy.resources.reservations.devices`
as for `embedder-gpu`) is picked up automatically (`get_effective_device()`).
The Docling accelerator options are always a real `AcceleratorOptions`
(`docling_accelerator_options()`); the former dict assignment made every
conversion fail with `'dict' object has no attribute 'device'` as soon as a
GPU was visible. Each pool thread keeps its own converter, so N threads load
the layout and TableFormer models N times on the GPU; keep that within the
VRAM left by the embedder and the chat model.

**Audio Processing** uses Whisper:
- Transcribes audio content to text
- Preserves speaker identification when available
- Timestamps important segments

### Phase 3: Chunking with HybridChunker

The Docling HybridChunker provides intelligent document splitting:

**Capabilities:**
- **Token-aware**: Uses actual tokenizer (sentence-transformers/all-MiniLM-L6-v2) instead of character estimates
- **Structure preservation**: Respects document structure including headings, sections, and tables
- **Semantic boundaries**: Splits at paragraph, code block, and natural boundaries
- **Context inclusion**: Each chunk includes its heading hierarchy for better understanding

**Configuration Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_tokens` | `CHUNK_MAX_TOKENS` (1024) | Token budget per chunk. 512 until 2026-10-26: with EmbeddingGemma 2's 2,048-token window the literature documents of the diejuristin corpus averaged 23 chunks each; 1024 halves that and keeps a clause or a table row together. The start request's `max_tokens` overrides it per job. |
| `embed_max_tokens` | `EMBEDDING_MAX_TOKENS` (2048) | The embedding model's per-text limit: a contextualised chunk above it (an unsplittable table) is cut into overlapping windows below it before embedding (`split_oversized_chunks`). |
| `chunk_size` | 1000 | Target characters (fallback mode) |
| `chunk_overlap` | 200 | Character overlap between chunks (fallback mode) |
| `merge_peers` | true | Merge small adjacent chunks |

The token counter is the chunker's MiniLM tokenizer, which produces more tokens per
German word than Gemma's SentencePiece vocabulary, so the budgets are conservative with
respect to the embedder.

**Contextualization Process:**
1. Docling parses document structure into a DoclingDocument
2. HybridChunker iterates through document elements
3. Each chunk receives contextualized text including:
   - Parent heading hierarchy (e.g., "Chapter 1 > Section A > Subsection")
   - Original content
   - Relevant metadata

**Fallback Mode:**
When DoclingDocument is not available, a simple sliding window approach is used:
1. Split content at sentence boundaries (., !, ?, \n)
2. Respect minimum chunk size (100 characters)
3. Apply overlap for context continuity

---

## Embedding Generation Process

### Overview

Embeddings convert text chunks into high-dimensional vectors that capture semantic meaning. The system uses OpenAI's embedding models as primary provider with Ollama as fallback.

### Embedding Flow

```
Document Chunks
      │
      ▼
Text Preprocessing
      │
      ├── Truncation (if exceeds max tokens)
      └── Batch grouping (up to 100 texts)
      │
      ▼
Primary Provider (OpenAI)
      │
      ├── Success ──▶ Return embeddings
      └── Failure ──▶ Retry with exponential backoff
              │
              ▼ (after max retries)
Fallback Provider (Ollama)
      │
      ▼
Embedding Vectors (1536-3072 dimensions)
```

### Supported Embedding Models

**OpenAI Models:**
| Model | Dimensions | Max Tokens |
|-------|------------|------------|
| text-embedding-3-large | 3072 | 8191 |
| text-embedding-3-small | 1536 | 8191 |
| text-embedding-ada-002 | 1536 | 8191 |

**Ollama Models (Fallback):**
| Model | Dimensions | Max Tokens |
|-------|------------|------------|
| nomic-embed-text | 768 | 8192 |
| mxbai-embed-large | 1024 | 512 |
| all-minilm | 384 | 256 |

### Resilience Configuration

The embedding service implements robust error handling:

**Retry Logic:**
- Maximum retries: 3
- Initial delay: 1.0 seconds
- Maximum delay: 30.0 seconds
- Exponential backoff multiplier: 2.0

**Retryable Errors:**
- Rate limit errors
- Timeout errors
- Connection errors
- Server errors (5xx status codes)

**Non-retryable Errors:**
- Authentication errors
- Invalid request errors (4xx except rate limits)

### Batch Processing

Embeddings are generated in batches for efficiency:
1. Chunks are grouped into batches (default: 100 per batch)
2. Each batch is sent to the embedding API
3. Progress callbacks report completion status
4. Control is yielded between batches to allow API responsiveness

### Local Embedding Server (llama.cpp, CPU or NVIDIA GPU)

`docker-compose.yml` ships two optional profiles that run a llama.cpp server with
an OpenAI-compatible `/v1/embeddings` API (default model: Google's
EmbeddingGemma 2, `ggml-org/embeddinggemma-2-GGUF:Q8_0`, 768-d, multimodal):

```powershell
docker compose --profile recallhub --profile embedder up -d       # CPU
docker compose --profile recallhub --profile embedder-gpu up -d   # NVIDIA (CUDA build, needs NVIDIA Container Toolkit)
```

Both answer on the network alias `embedder` (host port `11090`), so switching
the app is config-only:

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_API_KEY=none
EMBEDDING_MODEL=embeddinggemma-2
EMBEDDING_BASE_URL=http://embedder:8080/v1
EMBEDDING_DIMENSION=768
```

Tunables: `EMBEDDER_HF_MODEL`, `EMBEDDER_CTX` (total context, split across
`EMBEDDER_SLOTS`), `EMBEDDER_THREADS` (CPU), `EMBEDDER_GPU_LAYERS` (GPU),
`EMBEDDER_OFFLINE` (`1` maps to llama.cpp `--offline`: serve from the cached
GGUF, never contact Hugging Face). The GGUF (310 MB) and its mmproj (555 MB)
live in the `embedder_models` volume in Hugging Face hub-cache layout
(`/root/.cache/huggingface/hub/models--ggml-org--embeddinggemma-2-GGUF/`:
content-addressed `blobs/`, `snapshots/<rev>/*.gguf` symlinks, `refs/main`).

#### Offline packages and the Windows installer

The llama.cpp images are not part of the default export (~1.2 GB each) and the
model is a Docker volume, not an image, so both are opt-in:

```powershell
# Backup package: server image(s) into images\, volume contents into embedder\
.\scripts\full-backup.ps1 -Tenant recallhub -All -IncludeEmbedder                         # CPU image
.\scripts\full-backup.ps1 -Tenant recallhub -All -IncludeEmbedder -EmbedderVariant gpu     # or both

# Standalone pieces (both support -WhatIf)
.\scripts\export-docker-images.ps1 -OutputPath .\images -Tenant recallhub -IncludeEmbedder -EmbedderVariant both -WhatIf
.\scripts\export-embedder-models.ps1 -OutputPath .\embedder

# Hardened Windows installer (or set "embedder.enabled": true in installer\config.json)
.\installer\build.ps1 -IncludeEmbedder -EmbedderVariant cpu
```

- `export-docker-images.ps1 -IncludeEmbedder` writes
  `images/embedder-llamacpp-server.tar` (`ghcr.io/ggml-org/llama.cpp:server`)
  and/or `images/embedder-llamacpp-server-cuda.tar` (`…:server-cuda`).
- `export-embedder-models.ps1` archives the root of `recallhub_embedder_models`
  into `embedder/embedder-models.tar` (~0.8 GB) plus
  `embedder-models-manifest.json` (model reference, file list). It refuses to
  run while the volume holds no model, i.e. before the first successful start
  of the embedder.
- On the target, `INSTALL.ps1` / `full-restore.ps1` load the image with the
  others, seed the volume through `installer/scripts/Import-EmbedderModels.ps1`
  (idempotent: skips when a GGUF is already present), write `EMBEDDER_OFFLINE=1`
  to `.env`, start the stack with `--profile <tenant> --profile embedder` (or
  `embedder-gpu`) and give the tray app `composeProfile: "<tenant>,embedder"`.
  `-Embedder auto|none|cpu|gpu` overrides what the backup manifest says
  (`embedder_included`, `embedder_variant`); `auto` picks the CPU image for a
  "both" package. The restore warns when `.env` and the chosen profile disagree
  about `EMBEDDING_BASE_URL=http://embedder:8080/v1`.
- Hardened installer: `installer/config.json` → `"embedder": { "enabled": true,
  "variant": "cpu" | "gpu", ... }` makes `New-BuildConfig.ps1` emit
  `COMPOSE_PROFILES=embedder` (or `embedder-gpu`), `EMBEDDING_PROVIDER=openai`,
  `EMBEDDING_BASE_URL=http://embedder:8080/v1`, `EMBEDDING_MODEL=embeddinggemma-2`,
  `EMBEDDING_DIMENSION=768` (plus the `EMBEDDER_*` tunables) into `hardened.env`,
  and makes `build.ps1` bundle the image and the cache; `setup.iss` ships
  `embedder\` and runs `Import-EmbedderModels.ps1` before `Start-Services.ps1`.
  `docker-compose.hardened.yml` carries the profile-gated `embedder` /
  `embedder-gpu` services on the internal network only (alias `embedder`, no
  port mapping, `LLAMA_ARG_OFFLINE=1`) and the `embedder_models` volume with the
  fixed name `recallhub_embedder_models`. The checked-in `hardened.env` keeps
  Ollama embeddings and documents the swap as a commented block.
- Stored vectors are model-specific (768-d EmbeddingGemma 2 vs. 1536/3072-d
  OpenAI): switching the provider on an existing tenant requires re-ingesting.

Notes:

- EmbeddingGemma expects task prefixes (`task: search result | query: ` for
  queries, `title: none | text: ` for documents). The query path applies the
  model family's query prefix automatically (`backend/services/query_embedding.py`,
  shared by the search and chat routers and the federated search; override or
  disable it with `EMBEDDING_QUERY_PREFIX`). Documents are embedded without a
  prefix on purpose: on the synthetic gold set the query prefix alone lifted
  semantic Hit@1 from 17 % to 27 %, while prefixing the documents as well gave
  nothing and would cost a re-ingestion
  (`docs/system-validation/SEMANTIC_SEARCH_REGRESSION_2026-10-23.md`). The
  benchmark/checks still apply both prefixes (`detect_task_prefixes`).
- llama.cpp ignores the OpenAI `dimensions` parameter; Matryoshka truncation
  is done client-side (`truncate_renorm`).
- Image embedding works through `/v1/embeddings` with
  `{"input": {"prompt_string": "<media_marker>", "multimodal_data": ["<base64>"]}}`;
  the flat `multimodal_data` form is silently ignored by the server.
- Measured 2026-10-15: embedding scanned pages directly as images retrieves
  *worse* than their OCR text in a mixed text+image index (a text query sits
  ~0.06 closer to text than to an image of the same page, so image vectors sink
  below clean-text noise) and costs ~11× per page; keep OCR as the scan
  representation. See `docs/23-SCAN_IMAGE_EMBEDDING_SPIKE.md`.
- Ollama's `embeddinggemma-2` tags are MLX-only (Apple Silicon) as of
  2026-10-10 and fail on Windows/Linux with "requires MLX support".

#### Query embeddings: retry and a second instance (F29)

On the live Quellex stack the one `embedder-gpu` instance serves the ingestion
batches (100 chunks per request, split into one task per chunk over `-np 4`
slots) and every search query. A query that lands while the slots are busy
waits behind the batches, and a transient failure (connection reset, 429, 5xx)
used to fail the search outright. `backend/services/query_embedding.py` now

- retries query embeddings with exponential backoff (`QUERY_EMBEDDING_MAX_RETRIES`,
  default 3; first delay `QUERY_EMBEDDING_RETRY_BASE_SECONDS` 0.5 s, doubled per
  attempt, capped at 8 s, with jitter; `QUERY_EMBEDDING_TIMEOUT_SECONDS` 30 s per
  request; 4xx answers other than 429 are not retried), and
- embeds queries at `QUERY_EMBEDDING_URL` when it is set, e.g. the
  `embedder-query` compose service: the same image and model on host port
  `11091` (`-np 2`, 8k context, ~0.9 GB VRAM), alias `embedder-query`, started
  with `--profile embedder-query`. Ingestion keeps `EMBEDDING_BASE_URL`.

```powershell
docker compose --profile quellex --profile embedder-gpu --profile embedder-query up -d
# .env: QUERY_EMBEDDING_URL=http://embedder-query:8080/v1
```

**Is the embedder on the GPU?** Checked 2026-10-26 on the live stack after two
days of uptime: `nvidia-smi` showed the GPU at 0-5 % with the embedder at 100 %
of one CPU core, 10.4 GiB RSS and 606 tok/s aggregate (1,344 tasks in 10 min);
a four-text request of 3,468 tokens took 106 s. Restarting the container
(`docker restart rag-embedder-gpu`) brought it to 13-16k tok/s per request
(2,364 tokens in 0.15-0.18 s), 81 % GPU utilisation during the request and
4.6 GiB RSS, with the startup log placing the layers on `CUDA0` ("layer 5 is
assigned to device CUDA0"; llama.cpp disables flash attention for this model,
which is expected). The offload flags (`-ngl 999`, `-b 8192 -ub 8192`) are
therefore correct; the slow state was a degradation of the long-running
server, not CPU compute. Watch the embedder's tokens per minute (its
`stop processing: n_tokens = …` log lines) and restart it when the per-request
rate collapses; the query instance keeps searches responsive either way.
The log also showed `400 … ill-formed UTF-8` answers to some ingestion
batches: chunk text with invalid UTF-8 reaches the server and the whole
batch is rejected (the F27 "HTTP 500" of the prompt-test log); not fixed here.

### Embedding Validation Checks

`backend/services/embedding_checks.py` runs the suite used to qualify an
embedding provider on real chunks from the index: cold/warm query latency,
throughput by batch size (tokens/s from the server's `usage` when available),
vector dimension and L2 normalisation, MRL truncation stability (top-k
neighbour overlap), pseudo-query retrieval sanity, an image-embedding probe
(llama.cpp), ranking agreement between providers and against the vectors
already stored in the index, plus a hardware snapshot (CPU/RAM/NVIDIA GPUs).

Entry points:

- UI: System → Embedding Benchmark → "Validation checks" (admin only).
- API: `POST /api/v1/benchmark/checks` (`EmbeddingChecksRequest`),
  `GET /api/v1/benchmark/checks/results`; results are stored in
  `benchmark_results` with `kind: "checks"`.
- CLI (no API needed, works on a GPU box):
  `uv run python -m backend.cli bench embeddings --provider llamacpp --base-url http://localhost:11090 --database rag_parhelion`
  or `.\scripts\benchmark-embeddings.ps1 -Database rag_parhelion [-Json] [-Output checks.json]`.

### GPU Thermal / Mixed-Placement Stress Test

`backend/services/gpu_stress.py` answers "how does this box behave under
sustained local-model load": it starts llama.cpp servers as Docker containers
(`server-cuda` or CPU image, any GGUF from Hugging Face, cached in the
`recallhub_embedder_models` volume), drives them with concurrent synthetic
chat/embedding requests for a fixed duration and samples `nvidia-smi` every
second (temperature, power vs. limit, SM/memory clocks, utilisation, VRAM,
active throttle reasons) together with host CPU/RAM. Each scenario reports
per-model load time, VRAM growth, layers offloaded to the GPU, tokens/s,
requests/s and p95 latency, plus a thermal summary (start/max/end
temperature, time to max, mean power, throttle seconds, host CPU).

Placement per model: `gpu` (all layers in VRAM), `cpu` (CPU image, model in
system RAM), `split` (`--fit on`, llama.cpp keeps what fits in VRAM and spills
the rest to RAM) or `cpu_moe: true` (attention on the GPU, MoE experts in
RAM). A scenario may run several models at once, which is how "models on the
GPU and in RAM at the same time" is exercised. A model may also point at an
existing OpenAI-compatible `endpoint` (e.g. Ollama) instead of a GGUF.

Suites are YAML (`backend/config/gpu_stress/gemma4.yaml` covers EmbeddingGemma 2
and Gemma 4 E2B/E4B/26B-A4B alone, split and mixed) so the same run can be
repeated on other hardware and the JSON results compared:

```powershell
.\scripts\gpu-stress.ps1 -DryRun                                   # print the docker commands
.\scripts\gpu-stress.ps1 -Output docs/system-validation/gpu-stress-<host>.json
.\scripts\gpu-stress.ps1 -Only gemma4-26b-split,mixed-gpu-and-ram -Duration 180
.\scripts\gpu-stress.ps1 -Cleanup                                  # remove leftover rag-stress-* containers
# equivalent: uv run python -m backend.cli bench gpu-stress [--suite ..] [--only ..] [--duration ..] [--output ..]
```

Requirements on the host: Docker with the NVIDIA Container Toolkit (`docker run
--gpus all`), `nvidia-smi` on PATH (without it only host CPU/RAM is sampled)
and roughly 26 GB of disk for the default suite's GGUFs. The test is CLI-only
on purpose: it orchestrates containers and runs for minutes, so it does not
belong behind the API. Results from this repo's machines live in
`docs/system-validation/`.

### Metadata Attached to Embeddings

Each embedded chunk stores:
- `embedding_model`: Model used for generation
- `embedding_generated_at`: ISO timestamp of generation
- `token_count`: Actual token count from tokenizer

---

## Query Processing Pipeline

### Query Flow Overview

```
User Query
      │
      ▼
Query Embedding Generation
      │
      ▼
Search Execution
      │
      ├── Semantic Search ($vectorSearch)
      ├── Text Search ($search)
      └── Hybrid Search (both + RRF)
      │
      ▼
Document Lookup ($lookup)
      │
      ▼
Result Ranking & Deduplication
      │
      ▼
Context Assembly
      │
      ▼
LLM Response Generation
      │
      ▼
Response with Source Attribution
```

### Step 1: Query Embedding

The user's query text is converted to a vector using the same embedding model used during ingestion. This ensures the query vector is in the same semantic space as the document vectors.

### Step 2: Search Execution

Three search methods are available, each optimized for different query types.

---

## Search Methods

### Semantic Search (Vector Search)

Uses MongoDB's `$vectorSearch` aggregation operator to find conceptually similar content.

**Process:**
1. Query vector is compared against stored embeddings
2. Approximate Nearest Neighbor (ANN) search with HNSW index
3. Returns top-k most similar chunks by cosine similarity

**MongoDB Pipeline:**
```
$vectorSearch (numCandidates: 100, limit: N)
      │
      ▼
$lookup (join with documents collection)
      │
      ▼
$unwind (flatten document info)
      │
      ▼
$project (select fields + vectorSearchScore)
```

**Best For:**
- Conceptual/thematic queries
- Questions where exact keywords may not appear in documents
- Finding semantically related content

### Text Search (Full-Text Search)

Uses MongoDB Atlas Search with fuzzy matching for keyword-based retrieval.

**Process:**
1. Query text is tokenized and analyzed
2. Fuzzy matching allows for typos (max 2 edits, prefix length 3)
3. Returns chunks containing matching terms

**MongoDB Pipeline:**
```
$search (text query with fuzzy matching)
      │
      ▼
$limit (fetch count)
      │
      ▼
$lookup (join with documents collection)
      │
      ▼
$project (select fields + searchScore)
```

**Best For:**
- Exact term searches
- Technical jargon or specific names
- When you know the exact words used in documents

### Hybrid Search (Recommended Default)

Combines semantic and text search using Reciprocal Rank Fusion (RRF).

**Process:**
1. Execute semantic search and text search concurrently
2. Over-fetch results (2x requested count from each)
3. Apply RRF algorithm to merge and rank results
4. Return top-k merged results

**Reciprocal Rank Fusion Algorithm:**
```
For each document d appearing in result lists:
    RRF_score(d) = Σ(1 / (k + rank_i(d)))

Where:
- k = 60 (standard RRF constant)
- rank_i(d) = position of document d in result list i
```

**Enhanced RRF Adjustments:**
- **Cross-search boost**: +15% score for documents found in both searches
- **Content length penalties**:
  - < 50 chars: 50% penalty (likely incomplete)
  - < 100 chars: 30% penalty
  - < 200 chars: 15% penalty
  - > 500 chars: 5% bonus (rich content)
- **Score normalization**: Final scores normalized to 0.5-1.0 range

**Best For:**
- General-purpose queries
- When query type is uncertain
- Balancing semantic understanding with keyword precision

### Identifier Queries (FN, EZ/KG, Geschäftszahl)

Module: `backend/services/identifier_search.py`, used by `hybrid_search` in
`backend/routers/search.py` and by `FederatedSearch._search_database` (the chat
path). Background: findings F25 and F17 in
`docs/system-validation/INGESTION_PROMPT_TEST_LOG_2026-10-24.md`. Entity
questions that name an identifier lost their best document once copies of
related documents accumulated (Finanzamt letters stored 2–6 times under
different paths filled the top 10), and case numbers were tokenisation-sensitive
(`13 U 39/16v` did not find the printed `13 U 39/16 v`, and the fuzzy text
search let `404945h` match unrelated numbers).

**Only queries that contain an identifier take this path.** `detect_identifiers`
returns an empty list for every other query, and nothing below runs: the
ordinary candidate counts (`2 × match_count`, `numCandidates: 100`), the fuzzy
text query and the RRF fusion stay exactly as described above. No re-ingestion
and no index change is needed; the exact query runs on the existing
`text_index` (`lucene.standard` on `content`).

**Detection** (regex, Austrian legal identifiers):

| Kind | Examples | Variants generated |
|------|----------|--------------------|
| Firmenbuchnummer | `FN 404945h`, `FN404945h`, `FN 404945 h` | with/without spaces, lower/upper check letter, bare `404945h` |
| Grundbuch | `EZ 52` **together with** `KG 11029` | `EZ 52`/`EZ52` and `KG 11029`/`KG11029`; an EZ or KG alone is not an identifier |
| Geschäftszahl / Aktenzeichen | `13 U 39/16v`, `13 U 39/16 v`, `17 Cg 114/11a`, `038 C 1199/2017a`, `5 E 1149/20k-12` | check letter with/without the space and in both cases, leading-zero department also without the zeros, ON suffix (`-12`) with and without |

**Retrieval on this path:**

1. The vector and fuzzy text stages run unchanged but fetch `5 × match_count`
   candidates per index, with `numCandidates = max(100, 20 × limit)` for
   `$vectorSearch`, and additionally project `chunk_index` and the parent
   document's `content_hash`.
2. One extra Atlas Search query on `text_index`: `compound.should` of exact
   `phrase` clauses on `content`, one per spelling variant, no fuzziness,
   `$limit 20`, same `$lookup` to the documents collection. A Grundbuch entry
   contributes a nested `compound.must` that requires an EZ and a KG variant in
   the same chunk. In addition, documents whose `source` path or `title`
   carries the identifier are matched with a tolerant case-insensitive regex
   (`FN\s*404945\s*h`; a Geschäftszahl's slash may be `_`, `-`, `.` or a space,
   as in the file name `17Cg114_11a`), up to 20 documents, and up to 8 of each
   one's chunks (lowest `chunk_index` first, fetched by the indexed
   `document_id`) join the exact hits after the phrase hits. This is what
   rescues F25: the Firmenbuchabfrage's text lost the number in conversion
   (no chunk in the corpus contains `404945`), only its file name has it. The
   regex scan over the documents collection costs about 65 ms for 79k
   documents and runs on this path only.
3. Merge (`merge_identifier_results`): the exact hits are **pinned ahead** of
   the RRF-fused list (among them, chunks the normal search also found come
   first, then the rest in search order). Copies are then **collapsed**: the
   candidates are grouped by `(content_hash, chunk_index)` (by chunk id when
   either value is missing), the first representative of each group stays and
   the other copies' source paths are listed in its `metadata.locations`. The
   remaining slots are filled from the fused list; copies beyond the result
   window still contribute their locations.

Pinned results carry `metadata.exact_identifier_match: true` and a
`similarity`/`rrf_score` of `1.0`; every result on this path has
`metadata.locations` (possibly empty). On the federated path the exact hits ride
through `_apply_rrf` as `search_type: "exact"` (they never reach a strategy's
own RRF), and `_deduplicate_and_rank` keeps pinned documents first and collapses
copies across sources by the same key, by chunk id when the hash is absent.

Limits: the identifier must be spelled closely enough for the regexes (a
Geschäftszahl needs its check letter), and copies only collapse when the file
bytes are identical (same `content_hash`); a re-scanned copy of the same letter
is a different file. The live check for F25 is
`backend/tests/integration/test_identifier_search_live.py` (skipped unless
`RUN_INTEGRATION_TESTS=true` and a backend answers at `TEST_BASE_URL`, default
`http://127.0.0.1:11001`, with `TEST_API_KEY` for `X-API-Key`).

---

## Response Generation

### Simple Chat Pipeline

For direct chat interactions without advanced agent workflows:

**Process:**
1. User message received
2. System determines if search is needed (based on query type)
3. If search needed, LLM calls `search_knowledge_base` tool
4. Search results assembled into context
5. LLM generates response using context
6. Response returned with source attribution

### Tool Calling Loop

The chat system uses an iterative tool-calling pattern:

```
User Message
      │
      ▼
LLM Decision (with tools available)
      │
      ├── Tool Call Requested ──▶ Execute Tool
      │         │                      │
      │         └──────────────────────┘
      │                    ▼
      │            Add Result to Messages
      │                    │
      │         ◄──────────┘
      │
      └── Final Response ──▶ Return to User
```

**Maximum Iterations:** Configurable (default prevents infinite loops)

### Context Assembly

Search results are formatted for LLM consumption:
1. Top results extracted (typically top 5)
2. Document title and source included
3. Content truncated if needed (500-3000 characters per result)
4. Formatted as: `[Document Title]: content excerpt`

---

## Advanced Agent Workflow

The Federated Agent system provides sophisticated multi-step reasoning for complex queries.

### Agent Architecture

```
FederatedAgent (Coordinator)
      │
      ├── Orchestrator (Thinking Model - e.g., GPT-5.1, o1)
      │       ├── Analyze Phase
      │       ├── Plan Phase
      │       ├── Evaluate Phase
      │       └── Synthesize Phase
      │
      ├── WorkerPool (Fast Model - e.g., GPT-4o-mini)
      │       ├── Search Execution
      │       ├── Web Browsing
      │       └── Result Processing
      │
      └── FederatedSearch (Multi-Database Search)
              ├── Profile Documents
              ├── Personal Data
              ├── Cloud Storage
              └── Shared Resources
```

### Phase 1: Analysis

The orchestrator analyzes user intent with enhanced context extraction.

**Outputs:**
- Intent summary and classification
- Query type: FACTUAL, EXPLORATORY, COMPARATIVE, PROCEDURAL, AGGREGATE
- Named entity extraction (people, organizations, documents, dates, technical terms)
- Optimized search queries (primary + alternatives)
- Source priority determination
- Complexity assessment (1-5 scale)
- Multi-hop reasoning flag

**Conversation Context Processing:**
- Last 3 messages always included
- Earlier messages with significant term overlap included
- Maximum 10 context entries

### Phase 2: Planning

Creates an execution plan with targeted search tasks.

**Task Types:**
- `search_all`: Search all accessible sources
- `search_profile`: Search profile documents only
- `search_personal`: Search user's personal data
- `search_cloud`: Search cloud storage
- `web_search`: Search the web
- `browse_url`: Fetch specific URL content
- `summarize`: Summarize results

`search_cloud` and `search_personal` are only offered to the planner when a cloud or personal database exists for the user (`FederatedSearch.get_available_sources`); the prompt says so, and planned or follow-up tasks of those types are dropped anyway (`plan_unavailable_task_type` / `evaluate_unavailable_task_type` in the trace's degradations). A `source_priority` of `cloud` or `personal` without such a database falls back to `search_all`.

The orchestrator also appends `anchor_user_question`, a `search_all` task with the user's verbatim question (`AGENT_ANCHOR_RAW_QUERY`), next to the planner's rewritten queries. The live trace labels planned searches by type plus query and shows this task as "Your question as typed, all sources", so two all-source searches stay distinguishable. The `worker_step` stream event carries `documents_count` (all documents found) next to a three-document `documents` preview; the UI shows the count.

**Plan Structure:**
- Intent summary
- Reasoning explanation
- Execution strategy (parallel or sequential)
- Task list with priorities and dependencies
- Success criteria
- Maximum iterations

### Phase 3: Execution (Workers)

Worker pool executes planned tasks in parallel or sequence:
1. Tasks dispatched based on dependencies
2. Each worker executes search or browse operation
3. Results collected with quality assessment
4. Metadata tracked (duration, token usage)

### Phase 4: Evaluation

Orchestrator evaluates collected results:

**Decision Options:**
- `sufficient`: Enough information to answer
- `needs_refinement`: Additional searches needed
- `needs_web`: External information required
- `insufficient`: Cannot answer with available data

**Outputs:**
- Findings summary
- Gaps identified
- Follow-up tasks (if needed)
- Confidence score (0-1)

### Phase 5: Synthesis

Final answer generation from all collected information:
1. All document and web results compiled
2. Results sorted by relevance score
3. Content truncated to fit context window (~40,000 chars max)
4. Orchestrator generates comprehensive answer
5. Fallback generation if LLM fails

### Federated Search Access Control

The system supports multi-tenant search with access control:

**Data Source Types:**
| Type | Description | Access |
|------|-------------|--------|
| Profile | Shared organization documents | Profile members |
| Personal | User's private data (emails, etc.) | Owner only |
| Cloud Private | User's private cloud storage | Owner only |
| Cloud Shared | Shared cloud storage | Profile members |

**Source Priority Logic:**
- Identity/personal questions → Personal first
- Organization questions → Profile first
- File lookups → Cloud first
- External entities → Web search

---

## System Prompts

### Main RAG Agent System Prompt

Used for the CLI agent and basic chat:

```
You are a helpful assistant with access to a knowledge base containing documents, 
invoices, notes, transcripts, and business records.

IMPORTANT: You MUST use the search_knowledge_base tool to find information. 
You have NO built-in knowledge of the user's documents.

## Your Capabilities:
1. **Knowledge Base Search**: Use the `search_knowledge_base` tool to find relevant documents
2. **Information Synthesis**: Combine and summarize search results into helpful answers
3. **Conversation**: Engage naturally with users

## WHEN TO SEARCH (use search_knowledge_base):
- User asks about payments, invoices, amounts, transactions → SEARCH
- User asks about specific people, companies, or names → SEARCH  
- User asks about documents, records, notes, files → SEARCH
- User asks "how much", "when did", "what was" questions → SEARCH
- User asks about any business or work-related information → SEARCH

## WHEN NOT TO SEARCH:
- Greetings (hi, hello) → Just respond conversationally
- Questions about your capabilities → Answer directly
- General knowledge questions unrelated to documents → Answer if you know

## Search Strategy:
- Default to search_type="hybrid" for best results
- Use match_count=10 for comprehensive searches
- If first search returns nothing, try rephrasing the query

## Response Guidelines:
- Always cite the document source when providing information from searches
- If search returns no results, tell the user and suggest alternative search terms
- Be specific about amounts, dates, and names found in documents
- Answer in the same language as the documents when appropriate

Remember: When in doubt, SEARCH. The knowledge base contains the user's actual 
documents and records.
```

### Chat System Prompt with Tools

Used for the web chat interface with full tool access:

```
You are a helpful AI assistant with access to tools. You MUST use these tools 
to answer questions - NEVER respond without using tools first.

## Available Tools:

### 1. search_knowledge_base
Search internal documents using hybrid search (vector + text). The knowledge base 
contains documents about the user's company, projects, and internal information.
- **ALWAYS call this multiple times** with different queries (at least 2-3 searches)
- Start with broad context queries, then get specific
- If a search returns no results, TRY DIFFERENT TERMS - don't give up!
- When user says "my company" - search for company info, organization, business, etc.

### 2. browse_web  
Fetch and read content from a web URL. Use when you have a specific URL to visit.

### 3. web_search
Search the web using Brave Search to find URLs.

## CRITICAL RULES:

### Rule 1: NEVER give advice without searching first
**WRONG**: Explaining to user what they should do or look for
**RIGHT**: Actually searching and finding the information

### Rule 2: When user references "my company" or "our organization"
You MUST search the knowledge base first:
1. search_knowledge_base("company name organization")
2. search_knowledge_base("business overview about us")
3. Then search for the specific topic they asked about

### Rule 3: Don't give up after one search
- If search returns empty, try 2-3 MORE searches with different terms
- Try synonyms: "accounting" → "finance", "invoices", "bookkeeping"
- Try broader terms: "vendor" → "supplier", "partner", "company"

### Rule 4: Step-by-step questions require step-by-step tool usage
When user asks "go step by step":
1. Search for each piece of information separately
2. Use multiple tool calls in sequence
3. Gather all info before responding

Remember: You have access to the user's company documents. Search them! 
Multiple times! With different queries!
```

### Orchestrator Analysis Prompt

Used by the federated agent for intent analysis:

```
You are analyzing a user's question to understand their intent and plan the 
best search strategy.

User message: {user_message}
Conversation context: {conversation_history}

**Available Data Sources:**
- **profile**: Organization/company shared documents (policies, handbooks, reports)
- **cloud**: Cloud storage documents (Google Drive, Dropbox, WebDAV files)
- **personal**: User's private data (emails, personal documents, private files)
- **web**: Internet search via Brave Search + browse specific URLs

**CRITICAL ANALYSIS TASKS:**

1. **Intent Classification** - What type of question is this?
   - FACTUAL: Looking for specific facts ("Who is X?", "What is the address?")
   - EXPLORATORY: Looking for understanding ("How does X work?", "Explain Y")
   - COMPARATIVE: Looking for differences ("Compare A and B")
   - PROCEDURAL: Looking for steps ("How to do X?")
   - AGGREGATE: Looking for summaries ("Summarize all X")

2. **Entity Extraction** - Extract ALL named entities:
   - People: names, roles, titles, relationships
   - Organizations: companies, teams, departments
   - Documents: file names, document types
   - Dates: specific dates, time periods, deadlines
   - Technical terms: jargon, product names, acronyms

3. **Search Query Generation** - Create optimized search queries:
   - Use extracted entities as exact match terms (quotes)
   - Generate synonym variations for key concepts
   - Create both broad and narrow query variants

**Respond with JSON:**
{
    "intent_summary": "Clear statement of what user wants",
    "query_type": "FACTUAL|EXPLORATORY|COMPARATIVE|PROCEDURAL|AGGREGATE",
    "named_entities": {
        "people": ["person1", "person2"],
        "organizations": ["org1", "org2"],
        "documents": ["doc type or name"],
        "dates": ["date or period"],
        "technical_terms": ["term1", "term2"]
    },
    "search_queries": {
        "primary": "Main search query with key entities",
        "alternatives": ["synonym variation", "broader query", "narrower query"]
    },
    "sources_needed": ["profile", "cloud", "personal", "web"],
    "source_priority": "Which source is most likely to have the answer",
    "must_find": ["Critical information required"],
    "nice_to_have": ["Additional helpful context"],
    "complexity": 1-5,
    "requires_multi_hop": true/false,
    "reasoning": "Your strategic analysis"
}
```

### Orchestrator Synthesis Prompt

Used to generate final answers from search results:

```
You are generating a comprehensive answer to the user's question based on 
search results from multiple sources.

User's original question: {user_message}

Search Results (sorted by relevance):
{all_results}

**Instructions:**
1. Synthesize information from all relevant sources
2. Cite specific documents when making claims
3. If information is incomplete, acknowledge gaps
4. Answer in the same language as the user's question
5. Be specific about dates, amounts, and names
6. Structure your response clearly

**Generate your response now:**
```

---

## Performance Characteristics

### Typical Latencies

| Operation | Typical Duration |
|-----------|------------------|
| Query embedding generation | 100-300ms |
| Semantic search | 50-200ms |
| Text search | 50-150ms |
| Hybrid search (total) | 200-500ms |
| LLM response generation | 1-5 seconds |
| Full agent workflow | 5-30 seconds |

### Scaling Considerations

- **Embedding generation**: Batch processing reduces API calls
- **Vector search**: HNSW index provides sub-linear search time
- **Text search**: Atlas Search indexes provide fast full-text queries
- **Parallel execution**: Federated search queries multiple databases concurrently
- **Context limits**: Results truncated to fit LLM context windows

---

## Quality Optimization

### Search Quality Indicators

| Quality Level | Criteria |
|--------------|----------|
| Excellent | 5+ results, avg similarity > 0.8 |
| Good | 3+ results, avg similarity > 0.5 |
| Partial | 1+ results |
| Empty | No results found |

### Best Practices

1. **Chunk Size**: Keep chunks around 512 tokens for optimal embedding quality
2. **Overlap**: 200-character overlap prevents context loss at boundaries
3. **Hybrid Search**: Default to hybrid for best general-purpose results
4. **Multiple Queries**: Use query variations to improve recall
5. **Source Priority**: Match source types to query intent
6. **Iterative Refinement**: Use agent evaluation to identify gaps

---

*Last Updated: March 2026*
