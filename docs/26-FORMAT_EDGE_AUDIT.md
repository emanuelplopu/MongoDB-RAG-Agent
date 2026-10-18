# 26 — Non-Scan Format & Edge-Case Ingestion Audit (diejuristin corpus)

Date: 2026-10-18. Scope: validate how the ingestion pipeline (`src/ingestion/ingest.py`)
handles the **non-scan** formats and structural edge cases that dominate the diejuristin
NAS corpus but were never tested — the earlier OCR scale test (`docs/25-OCR_SCALE_TEST.md`,
PR #4) covered only scanned PDFs and photos.

This is a **measurement-only** study. The default pipeline was not changed and the full
ingest was not run (the production DB `rag_diejuristin` currently holds 0 documents — the
full ingest is still pending, so these findings are pre-flight). One low-risk fix (`.msg`/`.eml`
attachment extraction, behind an off-by-default setting) is implemented in the accompanying PR.

## Method

- **Sample**: a time-boxed walk of all four read-only NAS shares (`/app/mounts/{akten,
  documents,literatur,texte}`) saw **416,363 files**; 345 were sampled into an isolated bench
  area (`C:\RecallHub-Verify\bench\format-edge-2026-10-18\`, read-only copies + `manifest.json`),
  balanced across buckets, with `.msg` split 35 with / 35 without attachments.
- **Audit**: each sample was extracted with the **real** production code
  (`DocumentIngestionPipeline._read_document`) running inside a throwaway container from the
  live `recallhub-ingestion-worker:latest` image, with the exact live worker OCR env
  (tesseract deu+eng, `INGEST_PAGE_FURNITURE=false`, `OCR_IMAGE_NOISE_GATE=false`, …), NAS
  mounted read-only, no network, writing only to the bench folder.
- The **embedding + Mongo-write** stage was deliberately skipped: the stored `content` equals
  the extraction output audited here, and running embeddings would hit the shared live GPU
  embedder (prior CUDA-crash incident). Scripts: `bench/.../scripts/{sample_corpus,audit_formats,
  drill,audio_test,count_ext}.py`.

## Corpus census (all four shares, 416,363 files)

| ext | count | GB | ingested? |
|---|--:|--:|---|
| .doc | 129,154 | 6.2 | yes (antiword) |
| .pdf | 102,358 | 81.2 | partly — see below |
| .msg | 85,800 | 65.8 | body only |
| .docx | 32,827 | 4.7 | yes |
| .jpg/.jpeg | 21,458 | 26.6 | yes (OCR) |
| .wbk | 8,562 | 0.2 | yes (as .doc) |
| **.rtf** | **7,652** | 0.3 | **NO (excluded)** |
| .htm/.html | 5,960 | 0.05 | yes |
| .dat | 5,503 | 0.0 | no (noise) |
| **.dot** | **2,504** | 0.1 | **NO (Word templates)** |
| .xlsx | 1,036 | 0.04 | yes |
| **.xls** | **830** | 0.04 | **BROKEN** |
| .gif | 852 | 0.0 | yes (static) |
| .mp4/.mov/.avi | ~340 | ~30 | no (placeholder) |
| .tif/.tiff | 242 | 0.1 | lossy (multi-page) |
| .mp3/.m4a/.wav | ~66 | ~5 | broken offline |
| .eml | 96 | 0.07 | body only |

Supported-extension files: **390,192**; unsupported: **26,171** (mostly noise — `.dat`, `.db`,
`.lnk`, `.tmp`, `.idx`, `.css`, `.js` — plus the real-content `.rtf` and `.dot` buckets).

## Per-format / edge-case verdict

| Format | Corpus | Verdict | Finding |
|---|--:|---|---|
| **.msg body/headers** | 85,800 | **works** | Subject/from/to/date/body extracted cleanly; umlauts, §, forwarded-mail chains preserved. 0/70 empty, 0/70 html-only, 0/70 trivial body. |
| **.msg attachments** | — | **lossy (high impact)** | Attachments are **listed by filename only, never opened**. 50% of sampled emails have ≥1 attachment, **30% carry a substantive attachment** (overwhelmingly PDFs: 14 text + **29 scanned** + 10 images across 70 emails). One email carried **16 scanned-PDF case documents**, all unindexed; another lost **9,901 chars** of attachment text (4.5× its body). |
| **.eml** | 96 | **lossy** | Same body-good / attachments-listed-only behaviour as `.msg`. |
| **.doc / .wbk** | 137,716 | **works** | antiword (`-w 0`) is good: 98% umlaut-correct, § preserved, tables kept as space-aligned text (avg 7.6 table-like lines/doc). ~3% (2/72) are sparse Word **templates** yielding a skeleton — the files genuinely hold little text, not a converter failure. No RTF-renamed-as-.doc, no password-protected, no zip-renamed in the sample. No LibreOffice/soffice in the image (antiword is the only converter). |
| **.docx** | 32,827 | **works** | Docling clean (avg 20k chars). 1/18 invalid file fell back to raw text; embedded DrawingML diagrams are not rasterised (no LibreOffice) — text is unaffected. |
| **.pdf (text layer)** | ~70% of PDFs | **works** | Docling + Tesseract OCR; § and umlauts preserved. |
| **.pdf (image-only / pure scan)** | **~30% (~30k)** | **was BROKEN under the default worker — fixed** | Silently skipped before OCR (no content). The concurrent loop now OCRs them; the skip is opt-in via `INGEST_SKIP_IMAGE_ONLY_PDF`. See "Critical finding" below. |
| **.xls (legacy)** | 830 | **BROKEN** | Docling cannot read legacy `.xls`; the raw-text fallback fails on the binary → stored content is `"[Error: Could not read file …]"`. 6/6 placeholders. |
| **.xlsx** | 1,036 | **works** | Docling clean. |
| **.rtf** | 7,652 | **NOT INGESTED** | `.rtf` is absent from `SUPPORTED_INGESTION_EXTENSIONS` → never walked. If it were, the current code has no `.rtf` branch, so the generic text-read path would store **raw RTF markup** (`{\rtf1\ansi…}` control words), not text. These are real legal letter/`Muster` templates. |
| **.dot** | 2,504 | **NOT INGESTED** | Word templates; OLE2 like `.doc`, so antiword could read them, but `.dot` is not a supported extension. |
| **images (.jpg/.png/.bmp)** | ~21k | **works for document photos; noisy for real photos** | Docling OCR; MPO and 0-dpi files are re-saved correctly (`prepare_image_for_docling`). Live runs with `OCR_IMAGE_NOISE_GATE=false`, so photos that OCR to letter-soup are indexed as noise (the scale-test noise gate exists but is off in the live `.env`). |
| **.tif / .tiff** | 242 | **lossy** | **Multi-page TIFFs are flattened to page 1** (3/10 sampled had 3–8 frames). Separately, **every** TIFF is unnecessarily re-saved because its DPI tag is a PIL `IFDRational`, which fails the `isinstance(v,(int,float))` check in `prepare_image_for_docling` → `bad_dpi=True`. |
| **.gif** | 852 | **works (static)** | Animated GIFs would use frame 0 only (none animated in sample; rare). |
| **video (.mp4/.mov/.avi)** | ~340 / ~30 GB | **not ingested (by design)** | Returns `"[Video: … not yet supported]"`. No transcription pipeline. `.wmv` is additionally absent from `SUPPORTED_INGESTION_EXTENSIONS`, so `.wmv` is not even walked. |
| **audio (.mp3/.m4a/.wav)** | ~66 / ~5 GB | **broken offline** | With no OpenAI key (the live config) the path uses local Whisper via Docling, whose **`turbo` model is not baked into the image** — first use downloads **1.51 GB** (fails in an offline/air-gapped install). Language is **hard-coded `"en"`**, so German audio is mis-transcribed. `faster_whisper` is not importable, but the code uses openai-whisper, so that is moot. |

### Structure / robustness (all pass)

- **Long Windows paths / `\\?\`**: a host-side (Windows tooling) concern only. Ingestion runs
  in the Linux container over CIFS (`/app/mounts/...`), which has no 260-char limit; deep
  jurXpert paths and non-ASCII (umlaut/§) filenames extracted fine (15/15, 0 errors).
- **Dedup**: content SHA-256 dedup (`_get_existing_hashes`) skips identical files across folders.
- **One bad/hung file does not stall a run**: per-file adaptive timeout + `asyncio.wait_for`,
  skip-on-error (each file in its own try/except, recorded in `failed_documents`), and up to
  2 retries with increasing timeout. Incremental mode skips already-ingested `source` paths.
- Zero-byte / corrupt files: handlers return error placeholders rather than raising; no probe
  errors on the 90 sampled PDFs.

## Critical finding — image-only (scanned) PDFs are skipped by the default worker

The worker defaults to `max_concurrent_files: 2` (`backend/workers/ingestion_worker.py:88`; no
`performance_config` override exists in the live DB), so ingestion runs the **concurrent** path
`_ingest_documents_concurrent`. That path has a pre-check (`src/ingestion/ingest.py:2882`) that,
for any PDF where `is_image_only_pdf()` finds < 50 chars of text in the first 3 pages, records it
as `image_only_pdf` and **`return`s without processing** — no Docling, no OCR, no content, only a
stats row. The **sequential** path (`max_concurrent_files == 1`) has no such skip.

**~30% of sampled PDFs (27/90) are image-only** (pure scans with no text layer). Re-OCRing 5 of
them (i.e. what the sequential path would do) showed **3 contain substantial recoverable legal
text** — 6,983 chars (a Vienna administrative penal order, with § recovered), 2,962 chars (an
enforcement/`Exekution` case), 1,383 chars (a medical `Befund`) — and 2 were genuinely blank.

So on the imminent full ingest, with defaults, roughly **30,000 scanned PDFs (~a third of the
102k) would have been silently dropped** — exactly the material the OCR scale test was built to
handle. This was the single highest-impact finding.

**Status: fixed (2026-10-18).** `_ingest_documents_concurrent` no longer pre-checks PDFs by default:
every PDF goes through Docling + OCR, the same as in the sequential path, and the per-file
timeout, timeout retry and stats rows are unchanged. What changes in the results:

- A scan whose OCR recovers text becomes a normal document (classification `normal`).
- A genuinely blank scan is not pre-skipped any more; it ends up as `no_chunks` (stats row with
  `success=False`, so it also appears in `failed_documents`) instead of `image_only_pdf`.
- The hot loop stops opening every PDF with pdfium on the event loop for the pre-check.

`INGEST_SKIP_IMAGE_ONLY_PDF=true` (setting `ingest_skip_image_only_pdf`; `.env.example`, passed
through `docker-compose.yml`) restores the old skip for text-only profiles; those PDFs are then
recorded as `image_only_pdf` without content. The setting only governs the concurrent loop — the
sequential loop never had the pre-check and is untouched. Cost to plan for: each scan now runs
OCR; the OCR scale test measured ~4 s per scanned page (`docs/25-OCR_SCALE_TEST.md`), so ~30k
multi-page scans are days of CPU time on top of the text PDFs.

Files an earlier run already classified `image_only_pdf` keep that classification in the file
registry until they are reprocessed; select them with the `retry_image_only_pdfs` ingestion
filter (Ingestion Management page) instead of re-ingesting everything. Nothing needs to be
re-run on the live `rag_diejuristin` database, which holds 0 documents.
Tests: `backend/tests/test_image_only_pdf_ocr.py`.

## Prioritised fixes

1. **Stop skipping image-only PDFs (highest impact) — DONE.** `_ingest_documents_concurrent` now
   OCRs them like the sequential path; the `is_image_only_pdf` pre-check is opt-in through
   `INGEST_SKIP_IMAGE_ONLY_PDF` (default `false`) for text-only profiles. See the status note in
   the critical finding above.
2. **`.msg` / `.eml` attachment extraction (implemented in the PR, off by default).** Open each
   substantive attachment and append its extracted text (PDF→OCR, doc→antiword, docx→Docling),
   skipping inline logos / `smime.p7s` / `winmail.dat`, with size/count caps. Enable it for the
   diejuristin ingest with `MSG_EXTRACT_ATTACHMENTS=true` in `.env` — the variable (and
   `MSG_ATTACHMENT_MAX_MB` / `MSG_ATTACHMENT_MAX_COUNT`) is forwarded to every ingest/OCR service
   through `docker-compose.yml`. ~30% of 85,800 emails carry real document payloads today reduced
   to a filename.
3. **`.xls` support.** Add a legacy-`.xls` reader (e.g. `xlrd`/`pandas`) or convert via a
   headless LibreOffice; today all 830 store an error string.
4. **Audio: bake the Whisper model into the image and fix the language.** Pre-download the ASR
   model into the image (as Docling models already are) so offline installs work, and pass the
   OCR language config (deu) instead of the hard-coded `"en"`; otherwise document that audio will
   not transcribe. ~5 GB of audio.
5. **Include `.rtf` (7,652 files) and `.dot` (2,504).** Add `.rtf` to supported extensions with a
   real RTF→text reader (not the raw-markup fallback); treat `.dot` like `.doc` via antiword.
6. **Multi-page TIFF + the `IFDRational` resave.** Loop image frames (or convert all pages) for
   multi-page TIFF, and fix the `bad_dpi` check to accept `IFDRational` (coerce with `float()`)
   so TIFFs are not needlessly re-saved.
7. **Video** (~30 GB): decide explicitly — skip is acceptable, but consider at least audio-track
   transcription for `.mp4`/`.mov` once the audio path is fixed.

Raw per-file results, extracted text samples and the manifest live in
`C:\RecallHub-Verify\bench\format-edge-2026-10-18\` (outside the repo; confidential, not committed).
