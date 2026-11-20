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
| **video (.mp4/.mov/.avi)** | ~340 / ~30 GB | **not ingested (by design)** | Returns `"[Video: … not yet supported]"`. No transcription pipeline. `.wmv` is additionally absent from `SUPPORTED_INGESTION_EXTENSIONS`, so `.wmv` is not even walked. *2026-11-07: the audio track is transcribed (ffmpeg extraction, `media.video`), `.wmv` is walked; see docs/04, "Media transcription".* |
| **audio (.mp3/.m4a/.wav)** | ~66 / ~5 GB | **broken offline** | With no OpenAI key (the live config) the path uses local Whisper via Docling, whose **`turbo` model is not baked into the image** — first use downloads **1.51 GB** (fails in an offline/air-gapped install). Language is **hard-coded `"en"`**, so German audio is mis-transcribed. `faster_whisper` is not importable, but the code uses openai-whisper, so that is moot. *2026-11-07: the checkpoint is baked into the base image (`WHISPER_MODELS`), the language is configurable (`media.language`, default detect), transcriptions have their own threads/device/slots and budget, and a media run (`retry_media`) transcribes the deferred files; see docs/04, "Media transcription".* |

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

## Follow-up 2026-10-23: every content-bearing type on the NAS is now read

A full census of the four shares (416,625 files) before the first complete ingest, and the
readers added for it (`src/ingestion/ingest.py`, tests in
`backend/tests/test_legacy_format_readers.py`):

| extension | files | format found | reader |
|---|---|---|---|
| `.rtf` | 7,652 | RTF (`\ansicpg1252`), a few AppleDouble `._` forks | `striprtf` with the declared code page (`_read_rtf_file`) |
| `.dot` | 2,504 | Word 97 templates (OLE2) | antiword, like `.doc` / `.wbk` |
| `.dotx` | 66 | Word 2007+ templates (`.docx` container) | Docling via a temporary `.docx` copy (`_read_renamed_office_file`) |
| `.xls` | 830 | legacy Excel (OLE2); some exports are really HTML | pandas + `xlrd` → one Markdown table per sheet; HTML exports go to Docling (`_read_xls_file`) |
| `.mht` | 285 | MHTML archives (ADVOKAT Online: Meldeamt, Grundbuch) | HTML part → Docling (`_read_mhtml_file`) |
| `.tif` / `.tiff` | 242 | scans, some multi-page | every page OCRed (`_read_multipage_tiff`); DPI tags (`IFDRational`) no longer force a re-save |
| `.as .ass .ds .eb .do .dos .3` | ~2,200 | 1990s DOS text system (`Pittner Archiv`, `M U S T E R`): cp437 letters inside `\xe1` markers, HP PCL escapes, format bytes | heuristic decoder `legacy_dos_text()` |
| `.dss` | 121 | mostly the same text format; some Olympus DSS dictation audio (magic `\x02dss`) | text decoder; audio stored as a placeholder (no DSS transcription) |
| `.wvb` | 90 | jurXpert "weiteres Vorbringen": cp1252 text or RTF | byte sniffing (`sniff_text_bytes`) → text / RTF |
| `.mtt` | 331 | gzip-packed HTML index pages of MANZ `Muster` | gunzip + sniffing → Docling HTML |

Not indexed, by design (no document content): `.dra` 1,882 (Dragon NaturallySpeaking
dictation containers, OLE stream `Dragon Speech`; the dictated text is in the neighbouring
`.doc`/`.docx`), `.dat` 5,503 (ERV transfer records of `ERVKopien`, quote-delimited protocol
lines), `.db` 2,368 (`Thumbs.db` caches), `.herunterladen` 76 (JavaScript of saved web pages),
`.zip`/`.cab`/`.lnk`/`.tmp`/`.idx`/`.js`/`.css`, and `.mp4` (video, see item 7). macOS
AppleDouble forks (`._name`, present next to Mac-copied files of every type) are skipped in
both discovery paths (`is_sidecar_file`). The API's file-queue glob patterns are now derived
from `SUPPORTED_INGESTION_EXTENSIONS` (`_supported_file_patterns()` in
`backend/routers/ingestion.py`); the hand-written list had already drifted (no `.tif`).

Items 3, 5 and 6 above are therefore done; item 2 is enabled for the diejuristin ingest
(`MSG_EXTRACT_ATTACHMENTS=true` in the live `.env`); items 4 and 7 (audio/video) were addressed on 2026-11-07 (docs/04, "Media transcription").

## Follow-up 2026-10-28: scanned pages whose OCR text Docling keeps inside a picture (F35)

**Finding** (prompt-test log F35, ticks 21-23, after the PR #29 deploy made pure scans visible as
`image_only_pdf` records): 484 of the first 539 pure scans (90 %) were recorded as
`image_only_pdf: OCR found no text`, although OCR had worked. Of 12 random ones re-read with plain
Tesseract 5.5 (deu+eng) on a pypdfium2 render, 4 were readable; the other 8 are photos, IDs, a
sketch and the CCITT fax class of F30, which stays out of scope here. The pipeline's own
converter (`create_document_converter()` in the live API container) on
`HandMa_MileTh/Unterlagen/Aufklärungsbogen Aesthetic.Clinic vom 16.6.2017.pdf` returns
`ConversionStatus.SUCCESS` with items = {`PictureItem`: 1}: Docling 2.142 classifies the scanned
page as one picture, its OCR still runs, and the 1,568 characters it recognised hang on that
picture as its *children*. `export_to_markdown()` renders `<!-- image -->`, the HybridChunker does
not traverse pictures, no chunk is produced, and the file is recorded as a scan without text.
`Beilage I.PDF` carries 736 characters the same way; `Erlagschein.pdf` 18 text items. A second
variant returns no items at all for a page plain Tesseract reads without trouble
(`Direktzustellung_2009_03_24_4.pdf`, 209 words). Multi-page scans show the same loss as
documents whose text is only `<!-- image -->` lines. The rollback image behaves identically, so
this is the Docling of the 2026-10-23 dependency refresh, not PR #29.

**Fix** (`src/ingestion/scan_text_fallback.py`, called from the PDF branch of `_read_document` in
`src/ingestion/ingest.py` through `_recover_scan_text`; the same code runs in the thread pool and
in the conversion worker processes). It runs after the conversion, on the `DoclingDocument`,
and changes nothing for a page that produced body text: the trigger is, per page, *no text item
and no table cell outside pictures* (page headers and footers, Docling's furniture layer, do not
count as text). Two variants, applied in page order:

| variant | trigger | what happens | `ocr_fallback` |
|---|---|---|---|
| picture children | the page has `PictureItem`s whose descendants (captions excluded) carry text | each child's text is inserted into the body as a plain text item right after its picture, in the children's order, with the child's own provenance; the picture and its children stay as they are | `picture_children` |
| full page | the page has no item at all, no native text layer (< 50 characters, the `is_image_only_pdf` threshold) and at least one image object | the page is rendered with pypdfium2 at the pipeline's OCR scale (`OCR_SCAN_DPI`, `OcrConfig.pdf_scale()`) and read with one Tesseract pass (`deu+eng` / the configured languages, `OCR_PSM`, `OCR_TESSDATA_PATH`, through the `OCR_OMP_THREAD_LIMIT` wrapper of `src/ingestion/ocr_tesseract.py`, `ocr_image_file`); Tesseract's paragraphs become text items of that page, inserted where the page belongs in the body. One pass, no orientation detection: Docling's per-page OSD (`RecallHubTesseractCliOcrModel`) does not run here, so a rotated scan on this path is read unrotated | `full_page` |

A document with both variants records `picture_children+full_page`. The Markdown is exported
from the repaired document, so the stored `content`, the title extraction and the chunker all see
the recovered text, and the § repair (`src/ingestion/ocr_repair.py`), which runs after the
conversion on the whole document, covers the recovered items too. pdfium is opened only when
Docling listed a page without any item (or no page at all); a document whose pages all hold
something never pays for it. Without a `tesseract` binary only the first variant runs. A failure
inside the fallback is logged and the conversion result stands as Docling returned it; when the
full-page pass fails after picture children were already copied, those pages are still recorded
(`ocr_fallback` set, a warning with the error, Tesseract's stderr tail in the log).

A side fix found by the review of this change: the furniture-aware chunking serializer
(`_furniture_serializer_provider` in `src/ingestion/chunker.py`) was built from bare
`MarkdownParams`, whose `image_placeholder` is `<!-- image -->`, while `ChunkingDocSerializer`'s
own default is `""`; with `INGEST_PAGE_FURNITURE` on (the default) every picture therefore became
a `<!-- image -->` chunk. The provider now starts from the chunker's own defaults and only
switches the content layers.

What is recorded: one INFO line per file (`OCR fallback for <file> (picture_children: 1,568
characters from 1 picture(s) on page(s) 1)`), the per-file stat `ocr_fallback` in
`ingestion_stats` (`IngestionFileStats.ocr_fallback`, carried from the conversion worker to the
loop as `ConversionHandle.notes` through `note_conversion`, and from `_ingest_single_document` as
`IngestionResult.ocr_fallback`). The `image_only_pdf` semantics of PR #29 are unchanged: a scan
rescued this way is `success=True, image_only_pdf=True`; one whose full-page pass finds nothing
either stays `image_only_pdf: OCR found no text` and retryable.

Not covered on purpose: a page whose only item is a picture *without* text children (Docling's
OCR already ran on that bitmap and found nothing; a second pass with the same engine would only
cost time), the CCITT fax scans of F30, and files whose Docling conversion fails outright.
Tests: `backend/tests/test_scan_text_fallback.py` (synthetic equivalents of the sample files;
Tesseract mocked, rendering and chunking real).

## Follow-up 2026-11-19: the Word files that stayed unreadable, by content

**Finding.** After the full ingest and the retry run of 2026-11-11, `failed_documents` of the
diejuristin index held 1,805 distinct unresolved `error` records with a Word extension
(`.doc` 1,050, `.docx` 718, `.dot` 24, `.wbk` 8, `.dotx` 5; 1,778 written by the retry run,
27 in October). The messages were "legacy .doc format requires antiword" (1,067: the pre-check
of the build that ran the retry, removed since) and "Could not read file <name>" (723) and said
nothing about the files. The retry run used the build of 2026-11-11 08:54, so the readers of
cycle 1 (`docs/27`, PR #39, deployed 2026-11-12) never saw these files.

**Method.** All 1,805 paths were classified by their first bytes and container structure inside
the worker image (`docker run --volumes-from rag-ingestion-worker`, read-only), then every file
that is not an owner file (1,078) was read with the deployed build's `_read_document`, and the
ones that still failed or returned nothing (144) again with this branch's `src/` mounted into
the same image. One pass each, no client file copied.

| content (sniffed) | files | under | deployed build (PR #39) | this branch |
|---|---|---|---|---|
| `~$` Office owner files, 162 bytes (719 present, 8 deleted since) | 727 | `.doc` 500, `.docx` 204, `.dot` 10, `.dotx` 5 | skipped at discovery (`is_sidecar_file`); the records stay | same |
| `.docx` packages whose member names use backslashes | 482 | `.docx` | read, 482 of 482 | same |
| OLE2 Word binaries (398 Word 6/95, 70 Word 97, 1 Word 2003) | 469 | `.doc` 452, `.dot` 14, `.wbk` 3 | 392 read; 66 "no text" (antiword exit 0, empty); 11 `UnicodeDecodeError` (a stray byte in antiword's UTF-8) | 11 decode errors read; 6 of the 66 read from their pictures (three faxes, a Vereinsregisterauszug, a form, a letter); the other 60 are empty (see below) |
| OLE2 Word 97 binaries named `.docx` (Rangordnungserklärungen of the practice software) | 17 | `.docx` | Docling: "could not load document" | `antiword`, 17 of 17 |
| plain text (e-mail notes, "An: … Betreff: …") | 58 | `.doc` 53, `.wbk` 5 | read | same |
| `.docx` packages named `.doc` | 14 | `.doc` | 11 read; 3 are template packages (`wordprocessingml.template`, python-docx: "is not a Word file") | 14 of 14 |
| `.docx` packages Docling rejects: a list definition with an empty `w:start` value, `int('')` in `msword_backend._get_start_value` ("Pipeline SimplePipeline failed"; WEKA and Forum Media templates) | 4 | `.docx` | error | paragraphs from `document.xml`, 4 of 4 |
| RTF | 5 | `.doc` | read | same |
| HTML | 2 | `.doc` | read | same |
| password-protected Word binaries (FIB flag `fEncrypted`) | 7 | `.doc` | error ("Encrypted documents are not supported"), retried by every `retry_errors` run | `encrypted`, never retried |
| password-protected packages (`EncryptedPackage` stream: DRM and strong encryption) | 3 | `.docx` | error (a generic `ConversionError`) | `encrypted`, never retried |
| PDFs (two Grundbuchsauszüge, a scanned letter) | 3 | `.doc` | error ("not a Word document and not text") | PDF reader, 3 of 3 (one is a 59 s OCR) |
| JPEG scans (LEAD Technologies; UVS letters, a payment slip) | 3 | `.doc` | error | image OCR, 3 of 3 |
| Word 2.x for Windows (bare FIB `\xdb\xa5`, Pittner Archiv 1995/96) | 3 | `.doc` | read as text with a prefix of formatting bytes | `word2_binary_text`, clean text |
| Outlook messages (OLE2 `__substg1.0_*` streams) | 2 | `.doc`, `.docx` | error | `.msg` reader, 2 of 2 |
| owner-file content that lost its `~$` prefix | 2 | `.doc`, `.docx` | one stored as the text "eggereggerMingLiUSimSun…", one error | empty document |
| Excel 97 workbook (`Workbook` stream, 2.1 MB) | 1 | `.doc` | error | `.xls` reader |
| truncated OLE2 file (32,768 bytes, "incomplete OLE sector"; two copies of one file) | 2 | `.doc` | error | error naming the truncation |
| Microsoft Works-style container (streams `CONTENTS`, `SPELLING`) | 1 | `.doc` | error | error naming the streams |

Of the 1,078 real files the deployed build therefore already reads 954; what it does not read is
the 17 Word binaries named `.docx`, the 11 antiword outputs with a stray byte, the 3 template
packages, the 4 WEKA packages, the 3 PDFs, the 3 scans, the 2 messages, the workbook, the 10
password-protected files, the 2 truncated copies, the Works file and the 2 owner contents, and it
returns no text for 66 Word binaries and junk for the 3 Word 2 files.

**Fix** (`src/ingestion/ingest.py`, `src/ingestion/office_fallbacks.py`,
`src/ingestion/placeholders.py`, `backend/services/ingestion_counters.py`; tests in
`backend/tests/test_word_content_dispatch.py`, synthetic fixtures only):

- *Content before extension.* `_read_document` sniffs every Word-named file
  (`sniff_container`, `_read_word_file_by_content`): a PDF or a picture goes to its reader
  through a temporary copy with the right suffix (`_read_as_renamed`; the placeholder of a
  failed read names the original); an OLE2, Word 2 or text file named `.docx`/`.dotx` goes
  to the `.doc` reader; a zip named `.doc` went there already. A zero-byte file of any type
  and an owner-file content (`is_office_owner_content`: length byte, ANSI name padded to 54
  bytes, UTF-16 copy) are empty documents, not errors.
- *What the OLE2 container holds* (`ole_container_kind`, before antiword runs): `WordDocument`
  with the encryption flag → password-protected; `EncryptedPackage` → password-protected
  package; `__substg1.0_*` → the `.msg` reader; `Workbook`/`Book` → the `.xls` reader;
  `PowerPoint Document`, anything else → an error naming the streams; a container olefile
  cannot open → an error naming the damage.
- *antiword's output* is captured as bytes and decoded as UTF-8 with replacement: it lets an
  unmapped character through as one raw byte (0xED in the 11 court filings), and `text=True`
  turned that into a failed file.
- *Word binaries without body text* (`_read_doc_without_body_text`): the text-box story of the
  piece table (`word_binary_text`, `_main_or_other_stories`: forms and letters laid out in
  text boxes; the header story is left out, it held "Seite 1 von 1" and letterheads) and the
  OCR of the JPEG/PNG pictures found in the `WordDocument`, `Data` and `ObjectPool` streams
  (`ole_embedded_images`, cut at the signatures and end markers, pictures under 2 kB skipped;
  OCRed like the pictures of a `.docx`, `ocr_fallback: word_binary_pictures`). Both are joined.
- *Word 2.x* (`word2_binary_text`): the text between `fcMin` and `fcMac` of the bare FIB,
  cleaned like Word 6 text (`doc_reader: word2_binary`).
- *Template packages by content type* (`ooxml_main_part_kind`): `_read_renamed_office_file`
  rewrites the main part's content type whenever the package is a template, whatever the file
  is called, and normalises backslash member names first; a `.docx` whose package is a
  template takes the same path.
- *Docling failure on a Word package* (`_docx_text_fallback`, `docx_plain_text`): the
  paragraphs, tabs, breaks and table rows of `word/document.xml` are read directly
  (`doc_reader: docx_xml`, no `DoclingDocument`, so the fallback chunker is used); only when
  that yields nothing does the conversion error stand, with its cause.
- *`encrypted` as a countable outcome*: a placeholder whose cause says `password-protected`
  (Word, Office package, and the PDFs `pdf_open_problem` already named) raises
  `UnreadableFileError(error_type="encrypted")`; the loop records that type in
  `ingestion_stats` and `failed_documents`, the job counts it under `error_files`
  (`apply_stats_counts`), the classification stays `error`, and `failed_paths_of_job` skips
  it under every `retry_*` flag (`RETRY_FLAG_ERROR_TYPES` has no entry for it). Count them with
  `db.failed_documents.countDocuments({error_type: "encrypted", resolved: {$ne: true}})`.

**Verified on the real files** (this branch's `src/` mounted into the worker image, the 144
files the deployed build failed on or returned nothing for): every class above reads as the
table says; the remaining errors are exactly the 10 password-protected files (now `encrypted`),
the 2 truncated copies and the Works file. Of the 66 Word binaries without body text, 15 hold
JPEG/PNG pictures and 6 of them yield OCR text (the other 9 are photos and logos: `fotos
admond.doc`, `ramhofer.doc`, the `Normal.dot` logos); none has a text-box story (`ccpTxbx` is 0
throughout, the text-box reader is kept for forms that have one); the 19-24 kB
`Grundbuchsauszug.doc` / `Firmenbuchauszug.doc` / `Nur Barauslagen.doc` files are empty
documents (one or two characters in the main story), and the rest are `Normal.dot` templates
with their backups and letterheads whose only text is the header.

**Not recovered, by design:** the 10 password-protected files (no password), the truncated
file (its second half is missing), the Works document (no reader), the 8 owner files deleted
since, and the 60 Word binaries that are empty documents, templates or letterheads (they stay
`no_chunks`). The 727 owner-file records and the records of the files that
read fine now are not resolved by this change: a `retry_errors` run on this build resolves the
latter as each file is indexed (`docs/27`, "an indexed file resolves its open failed_documents
rows"); the owner files are no longer discovered, so `quellexctl ingest tidy-failures` now
resolves their records too (`sidecar_files` in `backend/services/failed_documents_tidy.py`:
`~$` owner files and `._` forks, "never a document").

## Follow-up 2026-11-19: PowerPoint decks (`.pptx`, legacy `.ppt`, cut-off packages)

**State before.** The four shares hold 17 `.pptx` and 10 `.ppt` files. After the full ingest
and the 2026-11-11 retry run one `.pptx` was indexed; 2 `.pptx` and all 10 `.ppt` were
`error: Could not read file`; 2 `.pptx` were recorded as `hang` and 13 as `hang_suspect` by
the ingestion watch of 2026-11-02/03, when the API main thread froze after a conversion. The
freeze was later traced to docling-core's table column split on the event loop (`SETO.xlsx`,
PR #37, live since 2026-11-11), so the PowerPoint records were over-cautious; whether Docling's
`.pptx` backend and the HybridChunker handle these decks had never been checked.

**Measured 2026-11-19** with the deployed worker image (`recallhub-ingestion-worker:latest`,
Docling 2.142, `create_document_converter()` + `export_document_markdown` + the pipeline's
`DoclingHybridChunker`, run with `docker run --volumes-from rag-ingestion-worker` against the
live shares, read-only):

| deck (17 `.pptx`) | size | convert | chunk | chunks | finding |
|---|--:|--:|--:|--:|---|
| `StepDa_Obsorge/Unterlagen/Besuchszeitenaufstellung.pptx` (`hang`) | 0.7 MB | 0.3 s | 0.04 s | 2 | text + 5 pictures; the "hang" was the SETO.xlsx chunking, not this deck |
| `StepDa_Obsorge/Unterlagen/Brainstorming.pptx` | 53 KB | 0.1 s | 0.1 s | 6 | indexed already |
| `SMK-CA/Attachements/Lending StructureDFHAT - Kopie*.pptx` (3 copies) | 44 KB | 0.04 s | 0.01 s | 1 | one text slide |
| `literatur/.../Legal Tech Präsentation*.pptx` (2), `Studying in the US.pptx` | 2-5 MB | 1.3-2.0 s | 0.04 s | 6-10 | clean titles, lists |
| `literatur/.../Wien_Blockchain_nov_2017.pptx` (2 copies) | 40 MB | 19 s | 0.04 s | 4 | 28 pictures; the time is Docling loading them |
| `P-4-12/Fotos/2012-11-02 Fotos Gericht.pptx` | 3 MB | 4.5 s | 0.02 s | 2 | captions + 22 photos |
| `GraWe_AvirSt/GraWe - Bilder.pptx` | 0.8 MB | 1.3 s | - | **0** | 14 photos, no text item: `<!-- image -->` only |
| `P-102-10/06-03-2012.pptx`, `P-102-10/Fotos/06-03-2012.pptx` (`hang`) | 0.7 MB | 1.0 s | - | **0** | one photo each |
| `BRUNNER-NÖBAU/Attachements/Besichtigung_UNIQUE_DOCNUMBER%….pptx` | **52 MB** | 11.9 s | - | **0** | 10 photographed letters of the building authority, no text item |
| `Opening Statements/5.5millionopening.pptx`, `…/Safety Rule for Doctors.pptx` (`error`) | 224 KiB each | 0.02 s | - | - | **not a zip**: both are the same package cut off at 229,376 bytes (56 x 4 KiB, an interrupted copy) inside `ppt/media/image2.jpeg`; all 18 slide parts before the cut are complete |

All 15 intact decks convert in 0.04-19 s and chunk in under 0.2 s: nothing in this set hangs
the chunker. Four decks are picture-only (zero chunks: Docling's `.pptx` backend does not OCR
pictures, so they would end as `no_chunks`).

The 10 `.ppt` files (`Präsentation High End Poker.ppt`, `PräsentationÄnderungen.ppt`,
`Sponsoring - Rechtsfragen.ppt`, `Präsentation Jahresabschluß 2002.ppt`, the two IIR
`Praesentation_06102016*.ppt`, four `P-28-16/Fotos/vorlagefotos*.ppt`) all fail in Docling
with `MsPowerpointDocumentBackend could not load document` in 0.02-0.13 s: `.ppt` was in the
pipeline's Docling format list, but Docling reads `.pptx` only and the image has no LibreOffice.
Their OLE2 `PowerPoint Document` streams hold 3-692 text atoms (0-78 K characters) and the
`Pictures` streams 0-24 JPEG/PNG pictures; `Sponsoring - Rechtsfragen.ppt` was saved
incrementally (5 `UserEditAtom`s, i.e. several copies of edited slides in the stream).

**Fix** (`src/ingestion/office_fallbacks.py`, wired in `src/ingestion/ingest.py`; tests
`backend/tests/test_powerpoint_readers.py`, OLE container faked, Docling stubbed):

| case | reader | what it does | recorded as |
|---|---|---|---|
| legacy `.ppt` (OLE2) | `ppt_binary_text` via `_read_ppt_file` | walks the `PowerPoint Document` record stream for `TextCharsAtom` (UTF-16) / `TextBytesAtom` (cp1252) below the document's slide lists and the slide and notes containers; follows `Current User` → `UserEditAtom` chain → persist directories, so only the current copy of each object and only the slides the document's slide lists reference are read (title masters are slide containers too: without this, "Hier klicken, um Master-Titelformat zu bearbeiten" and the `*` slide-number placeholders leaked into 3 of the 10 decks); `\r`/`\v` become line breaks, Wingdings bullets (private-use code points) are dropped; encrypted decks (`Current User` token or `CryptSession10Container`) are an error naming the reason; a `.pptx` package under a `.ppt` name goes to Docling through a renamed copy | `doc_reader: ppt_binary` (conversion note) |
| `.ppt` without any text (photo decks) | `ppt_binary_pictures` + `_ocr_picture_blobs` | the JPEG/PNG/TIFF blips of the `Pictures` stream (OfficeArt record header, 16- or 32-byte UID, tag byte) go through the image reader, like the pictures of a picture-only `.docx` | `ocr_fallback: office_pictures` |
| picture-only `.pptx` | `_ocr_office_pictures` (the `.docx` path, now also for `.pptx`) | the pictures under `ppt/media/` are OCRed when the Markdown has no text | `ocr_fallback: office_pictures` |
| `.pptx` / `.docx` cut off by an interrupted copy | `truncated_ooxml_text` via `_salvage_truncated_package` | when Docling rejects the file and `zipfile.is_zipfile` is false, the local file headers are walked, every member before the cut is inflated on its own, and the paragraphs of the surviving `ppt/slides/slideN.xml` (in slide order, `## Slide N` headings) or `word/document.xml` are returned; a complete package Docling rejects keeps its own cause | `ooxml_repair: truncated` |

**Measured after the fix** (same image, the worktree's `src/` mounted over the image's;
`_read_document` + chunker): every `.ppt` reads in 0.02-0.2 s; `Praesentation_06102016_.ppt`
77,944 characters / 105 chunks, `Praesentation_06102016.ppt` 67,916 / 93,
`Sponsoring - Rechtsfragen.ppt` 20,365 / 29 (§ 153 ff. StGB, no duplicate slides),
`Präsentation High End Poker.ppt` 4,163 / 6, `Jahresabschluß 2002.ppt` 1,951 / 3,
`PräsentationÄnderungen.ppt` 513 / 1 (a table, cell per line), the three captioned
`vorlagefotos*.ppt` 26-78 characters / 1 chunk each (the photos of models carry no text);
`VorlagePortraits Anfang_42178.ppt` has no text at all and one 2 MB PNG that OCR reads as
nothing: `[Document: … - no text content]`, i.e. empty, not an error. The two cut-off decks
yield 2,389 characters / 4 chunks (18 slides of an American opening statement). Of the four
picture-only `.pptx`, the 52 MB `Besichtigung` deck gives 19,191 characters / 26 chunks of
OCR text from the photographed letters in 93 s; `GraWe - Bilder.pptx` (14 photos, 33 s) and
the two `06-03-2012.pptx` (one photo, 5 s) have no text in their pictures and keep their
`<!-- image -->` Markdown, so they still end as `no_chunks`, which is correct for a photo album.

Not covered on purpose: speaker notes of `.pptx` (Docling leaves them out; the `.ppt` reader
includes notes containers), embedded OLE objects and charts in either format, `.pps`/`.pot`
(none on the shares), and a `.ppt` whose `Current User` stream is missing or damaged (every
slide and notes container is read then, so an incrementally saved deck may repeat a slide and
a title master's placeholder text may appear).

**Live retry.** The 13 `hang_suspect` and 2 `hang` `.pptx` records of job
`b98d150e-8516-4ad0-bd72-bd298d14d92d` are to be marked `resolved: true` and the job re-queued
as a resume without retry flags (the worker then re-reads those 15 files with the deployed
image: the 11 text decks index, the 4 picture-only ones end as `no_chunks` until this change is
deployed); the `.ppt` and cut-off `.pptx` `error` records need the new build and a retry run
with `retry_errors`.

## Follow-up 2026-11-19: the `no_chunks` class ("No content extracted from document")

**Population.** After the full ingestion (2026-11-08) and the retry run of 2026-11-11 (job
`b98d150e`, `retry_timeouts` + `retry_errors` + `retry_no_chunks`, which recovered almost nothing
of this class), `failed_documents` of `rag_diejuristin` held 10,866 distinct files with an open
`no_chunks` record on 2026-11-19: 2,280 written by the retry run (1,286 `.jpg`, 229 `.docx`, 188
`.pdf`, 186 `.htm`, 107 `.jpeg`, 81 `.png`, 71 `.gif`, 47 `.html`, 44 `.txt`, 14 `.tiff`, 13 `.bmp`,
8 `.dotx`, 2 `.mp3`, one each `.xlsx`, `.md`, `.rtf`, `.tif`) and 8,586 written by the placeholder
purge of the same morning (`quellexctl ingest purge-placeholders`, `purged_document_id` set:
8,571 `[Image: … - OCR extracted minimal text]` documents of the builds before 2026-11-11, 13
empty `.msg`, one `.dss`, one `.wvb`). None of the 10,866 is in `documents`. For the whole index,
`ingestion_stats` holds 14,436 images with chunks, 26,067 duplicate images and 3,145 image
`no_chunks` rows: with the purged ones, about 45 % of the distinct image files of this corpus
carry no text at all (photos of accident sites, buildings, vehicles, injuries; 4,734 of the
10,866 files sit in a `Fotos`/`Bilder` folder, 1,668 in an e-mail `Attachements` folder).

**Method.** 10 to 20 files per extension (random, from the open records) were converted with
the deployed worker image (`recallhub-ingestion-worker:latest`, build of main 2026-11-19, Docling
2.142) through the pipeline's own `_read_document` and `DoclingHybridChunker`, with the
`DoclingDocument` inspected (text items by label, tables, pictures and their text children,
furniture) next to cheap whole-set signals (size, pixel dimensions, pdfium page count, native
text characters, image/path/form objects, annotations; `w:t` characters, text boxes, pictures of
Word packages; visible text of HTML). Pages of the hard cases were rendered and read with plain
Tesseract at several scales. The same samples were then converted again with the patched
`src/` mounted into the worker image. Scripts and results stay outside the repo (client files).

**Sub-classes** (sample verdicts, counts estimated for the open records of 2026-11-19):

| sub-class | sample | estimated files | what the deployed build does | after this change |
|---|---|---|---|---|
| photo / drawing / logo: no text in the image | 55 of 70 images; a 640 x 480 car photo, 12 Mpx building photos, 16-pixel web buttons, pasted e-mail logos (`PastedGraphic-3.tiff`, 194 x 109, "VERTRIEBS GMBH") | ~9,000 (the 8,571 purged placeholders, ~85 % of the retry run's 1,573 images) | `[Image: … - OCR extracted minimal text]` -> `no_chunks` failure, retried on every `retry_no_chunks` run | `image_no_text`: a `success` stat, counted in `images_without_text`, no `failed_documents` row, open rows of the file resolved; never retried |
| image whose OCR lines hang below the picture (F35 on images) | `IMG_5637.jpeg` (a photographed letter, 2,874 characters of picture children), `Mindmap.png` (282), `Unbenannt2.PNG` (a screenshot, 228), `Rezension … .PNG` (1,112), `image001_V18.jpg` (a letterhead banner, 215 below a heading) | ~800-1,200 (5 of 70 sampled images) | `export_to_markdown()` gives `<!-- image -->` (14 characters), which passed the image reader's `> 10 characters` check and was handed to the chunker as content; 0 chunks -> `no_chunks` | `recover_image_text` copies the children into the body (`ocr_fallback: picture_children`); a document still below 50 characters of body text gets one plain Tesseract pass over the image at its own size, then at 2/3 and 1/2 (`full_page`); `markdown_has_text` decides, so `<!-- image -->` alone is the placeholder |
| scanned PDF page whose only body item is a heading made of the first OCR line, the rest below the picture | `Xerox Scan_24082022125358.pdf` (an RSb return receipt: heading "A330 ER Veeromnen Opec", 506 characters below the picture, plain Tesseract 642), `Direktzustellung_22277 … .pdf` (a building-authority form), `Handlerquelle und Neurissquelle FMM.pdf` (a map with its title), `Beilage._8 Absteckplan.pdf` (a survey plan: heading + 2,229 characters below the picture) | ~75 (8 of 20 PDFs) | F35 skipped the page because a text item (the heading) counted as body text | headings, titles, captions and page furniture no longer count as body text; a page with fewer than `MIN_BODY_TEXT_CHARS` (50) characters of other text gets its picture children copied and, for a scan, the full-page pass |
| scan Docling and Tesseract read as nothing at the OCR scale | `20211117095036738.pdf` (a two-page power of attorney, a 1-bit 3,512 x 2,480 scan): 0 characters at scale 3 (216 dpi) and at 300 dpi, 894 at scale 2, 942 at scale 1.5 | ~20 (2 of 20 PDFs; the same class exists among `image_only_pdf` records) | pure scan, OCR found nothing | the full-page pass tries the OCR scale, then 2/3 and 1/2 of it (`FULL_PAGE_SCALE_FACTORS`), keeps the first result with 50+ characters, else the longest; the stat notes the scale that read the page (`ScanTextRecovery.scales`) |
| photo exhibit whose only text is the page header (the court's filing stamp "Hinterlegt am 07.12.2018 - 15:20" and the exhibit number "./41") | `Beilage 41.pdf`, `Direktzustellung_19886 … .pdf` (3 pages each, AcroForm wrappers around photos) | ~20 | the header is furniture (`INGEST_PAGE_FURNITURE=false`), the body holds pictures without children -> 0 chunks | the scan pages get the full-page pass (the header is native text below the 50-character page threshold, so the page counts as a scan); a PDF whose Markdown has no text otherwise is exported with the furniture layer (`furniture_text` note), and the chunker's Markdown fallback makes the chunk |
| PDF of photos, no text anywhere | `Fotos März 2021 Dornbacher Str_ 4.pdf` (11 pages, 84 s), `E4823-Beilage2.pdf`, `Baumgartner Binder Wantenriss Photo 2.pdf` (a damaged wing) | ~30 (3 of 20) | `image_only_pdf` since PR #29 (the open `no_chunks` rows date from before the 2026-10-30 deploy) | unchanged (`image_only_pdf`) |
| PDFs the deployed build already reads | 9 of 20: CAD plans (`HG-BB-A2_55737.pdf`, 7,480 characters), `Querschnitt Einreichplan`, `Dokument Scan_001.pdf`, `Beilage AY4.pdf`, `Schanzstr 23-Konsens.pdf`, … | ~85 | fixed by F35 (deployed 2026-10-30) and PR #39 (2026-11-12); never retried since | a `retry_no_chunks` run indexes them |
| Word file of photographed exhibits whose pictures carry text | 11 of 20 `.docx` (`Beilage 6.docx`: a bank statement photo, `Schaumann KV 1.docx`: a 4,898-character quote, `Pichowetz Facebook.docx`, print screens) | ~125 | fixed by PR #39's `_ocr_office_pictures` (`ocr_fallback: office_pictures`); never retried since | a `retry_no_chunks` run indexes them |
| Word file with one heading above its pictures | `Lichtbildkonvolut.docx` (142 characters of Markdown: the heading), `2021-11-04-Fotos.docx`, `Zarkulea Bilder Beklagtenfahrzeug.docx` | ~35 (3 of 20) | the heading made `markdown_has_text` true, so the pictures were not OCRed; the HybridChunker makes no chunk of a heading | `markdown_has_prose` (headings do not count) triggers the picture OCR; the heading is kept above the pictures' text; a heading-only file is chunked by the Markdown fallback |
| Word file of pictures without text, or empty | `Dok1.docx` (13 MB, 22 photos, 76 s), `Vivaneo.docx`, `Petrovic Bild 2.docx`, `Dok3.docx`, `Flöck … Anzeigenbestätigung.docx` (one vector picture), `15-04-14 Bf Donauspital.docx` (no text at all) | ~70 (6 of 20) | `_ocr_office_pictures` found nothing -> `(markdown, document)` -> 0 chunks -> `no_chunks` | `[Document: … - only pictures without text]` / `… - no text content` -> `empty_file` |
| HTML without visible text | all 40 sampled `.htm`/`.html`: Outlook's `ATT0000x.htm` and `Unbenannte Anlage 000xx.htm` stubs (an empty `<body>`, 150-500 bytes), frameset pages, saved ad frames of a newspaper article, a `UltraEdit` table of contents of links | ~230 | Docling exports "" -> `no_chunks` | `[Document: … - no text content]` -> `empty_file`; a page Docling exports as nothing but with visible text in the markup (`2.html`, a title) gets that text (`html_fallback: visible_text`) |
| empty text files and templates | 44 `.txt` of 2-6 bytes (Outlook attachment stubs), `Readme.md` of 1 byte, 8 `.dotx` Word building-block templates (Equations, Dactylos, Galathea: no text), `Berechnung.xlsx` (no cell text), a 32,768-byte truncated `.rtf` | ~55 | `no_chunks` | `empty_file` (the empty-content branch of `_ingest_single_document`); the truncated RTF stays a `[Document: … - no text content]` placeholder |
| empty `.msg` | 13 Sixt rental e-mails: no subject, no sender, no body (a 265-byte empty RTF), no attachment | 13 | `[Empty email: …]` -> `no_chunks` | `empty_file` |
| media | 2 `.mp3`, 1 `.dss` | 3 | the media run (`retry_media`) | unchanged; the audio/video placeholders keep the `no_chunks` type so the media run sees them |

Not seen in the sample, although expected: PDFs whose text sits in form fields or annotations
(the sampled `AcroForm` files are photo wrappers: `FPDF_GetFormType` 1, one annotation, no field
text), Word text boxes, headers or footers (`w:txbxContent` 0 and header/footer characters 0 in
every sampled package), chunker edge cases with a huge table or a single over-long paragraph
(the HybridChunker splits those; the only way to 0 chunks is a document of pictures or of
headings).

**Fixes** (`src/ingestion/scan_text_fallback.py`, `src/ingestion/ingest.py`,
`src/ingestion/chunker.py`, `src/ingestion/placeholders.py`):

* Two outcomes for a file that was read but holds nothing to index: `image_no_text` (an image
  whose OCR found no text) and `empty_file` (no text at all: an empty file, a markup-only page, a
  Word file of pictures without text, an empty e-mail). Both are `success` stats
  (`IngestionFileStats.error_type` set, `classification` the same), counted in the job's
  `images_without_text` / `empty_files` (`JOB_COUNTER_FIELDS`, `IngestionStatusResponse`,
  `IngestionRunSummary`, the frontend's job types), not in `failed_files`, and write no
  `failed_documents` row; a stat of either outcome resolves the file's open rows with
  `resolution: "a later attempt found no text in the file"`. `IngestionResult.no_text`
  (`"image"` / `"empty"`) carries the reason from `_ingest_single_document` to the loop
  (`placeholders.no_text_kind` for the placeholders; an empty content, or one of picture
  placeholders only, is `"empty"`). The `[Audio:` / `[Video:` placeholders keep the `no_chunks`
  type. `no_chunks` now means: text was extracted and the chunker still produced nothing.
* The scan-text fallback counts body text without headings (`HEADING_LABELS`: section header,
  title, caption, page header/footer) and treats a page below `MIN_BODY_TEXT_CHARS` (50) as
  one whose text Docling left out: picture children are copied, and the full-page pass runs for a
  scan page that is still short afterwards (before: only for a page without any item). The
  pass tries the OCR scale, 2/3 and 1/2 of it. `recover_image_text` is the same for an image
  conversion (the picture children, then a plain Tesseract pass over the image itself, capped at
  `OCR_IMAGE_MAX_MPX`), wired into the image branch and the multi-page TIFF reader of
  `_read_document` through `_recover_image_text`; the stat's `ocr_fallback` records
  `picture_children` / `full_page` as for PDFs. A `ScanTextRecovery` can now record both
  variants on one page (`picture_children+full_page`) and the lower scale that read it.
* `_read_document`: the image branch decides with `markdown_has_text` (text besides
  `<!-- image -->`) instead of `len > 10`; a PDF whose Markdown has no text is re-exported with
  the furniture layer when that holds text (`note_conversion(furniture_text=True)`); an HTML file
  Docling exports as nothing gets its visible text from the markup (`html_visible_text`:
  scripts, styles, comments and tags dropped, entities decoded) or the `[Document: … - no text
  content]` placeholder; a `.docx` without prose (`markdown_has_prose`: headings and picture
  placeholders do not count) gets its pictures OCRed, the heading kept above their text, and is
  a `[Document: … - only pictures without text]` / `no text content` placeholder when there is
  nothing.
* `DoclingHybridChunker._chunk_document`: when the HybridChunker returns no chunk for a
  document whose Markdown has text besides picture placeholders (headings only, page furniture
  only), the Markdown is chunked by the sliding-window fallback (`chunk_method:
  simple_fallback`) instead of recording `no_chunks`; picture placeholders are no content on
  either path.

**What to run after the deploy.** One run with `retry_no_chunks` (re-queue the finished job as
on 2026-11-11, or a run of the Ingestion page with the "no chunks" filter) over the 10,866 open
rows. Expected: ~1,000-1,300 files indexed (the PDFs and Word files the deployed build already
reads, the images and scans this change recovers), ~9,100 `image_no_text`, ~400 `empty_file`,
~30 `image_only_pdf`, and the open `no_chunks` rows of all of them resolved. Cost: every image
is OCRed again (Docling's pass plus, for the ~9,000 without text, one to three plain Tesseract
passes): about 8-15 s per image on the CPU worker, 25-40 h of worker time; the office load
profile applies. `quellexctl ingest tidy-failures` does not reclassify the old rows without a
retry (the images were last read by builds without the picture-children recovery), so the retry
is the way to close them. The run's `images_without_text` and `empty_files` counters show the
split while it runs.

Tests: `backend/tests/test_no_text_outcomes.py` (the outcomes through `_ingest_single_document`,
the concurrent loop, the stats writer and the counters; the readers with Docling stubbed and
synthetic images, HTML, Word packages and a furniture-only PDF document; the scan-text fallback on
images with its scale ladder; the chunker's Markdown fallback), plus the adjusted cases of
`backend/tests/test_scan_text_fallback.py` (a heading or fewer than 50 characters of body text
no longer protect a page; the full-page pass at a lower scale).
