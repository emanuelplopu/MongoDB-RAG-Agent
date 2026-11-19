# 27 — Post-Ingestion Validation of the diejuristin Index and Improvement Cycle 1

Date: 2026-11-11. Scope: the full ingestion of the law firm's four shares into the Quellex
tenant (`rag_diejuristin`) ran from 2026-10-23 to 2026-11-08. This document summarises the
job, validates what was indexed against the files on the shares, reviews the failures,
skips and slow stages, defines a retrieval and chat benchmark built from the live index,
and records the fixes of the first improvement cycle (PR "post-ingestion cycle 1").

Update 2026-11-12: cycle 1 was merged (main `013dc6b`) and deployed; the benchmark was run
again on the deployed build, including the chat prompts that had no baseline (section 7.7).

All numbers are measurements taken on 2026-11-11 between 20:05 and 22:30 local time, with
read-only queries against MongoDB, a file census of the shares, and read-only re-reads of
sample files inside the worker container. A retry run of failed files was started from the
application at 20:06 and was running during the measurements; where that matters it is said.
Scripts, samples and raw results: `C:\RecallHub-Verify\bench\validation-2026-11-11\`
(`q01`-`q09` analysis scripts, `census.tsv`, `samples.json`, `probe.json`,
`repro_result*.json`, `benchmark/`). The benchmark cases quote client documents and stay
there; the harness is in `scripts/retrieval-benchmark/`.

## 1. The job

| | |
|---|---|
| Job | `b98d150e`, profile `diejuristin`, API runner, 33 run segments (32 resumes) |
| Wall clock | 2026-10-23 20:29 to 2026-11-08 08:20 local, 372 hours (15.5 days) |
| Files on the shares (census 2026-11-11) | 416,900 files, 242.8 GB |
| Files the run discovered | 401,390, of which 394,540 to process |
| Documents indexed | 365,109 |
| Chunks (768-d EmbeddingGemma 2) | 4,313,374; 49.6 GB of chunk data, 37 GB on disk |
| PDF pages converted | 1.16 million |
| Unresolved failure records at the end | 7,355 |
| Hours with at least one finished file | 367 of 372 |
| Host averages over the run | CPU 65 %, package 62 W, GPU 18 W, package 72 °C (max 99 °C); about 30 kWh |

Per file type (documents collection, 2026-11-11):

| type | documents | chunks | words | note |
|---|--:|--:|--:|---|
| .doc | 123,067 | 539,859 | 39.4 M | antiword, fallback chunker |
| .pdf | 96,478 | 2,312,884 | 315.1 M | Docling + OCR, hybrid chunker; about 31 % are scans |
| .msg | 78,618 | 1,209,448 | 100.9 M | body + attachment text, fallback chunker |
| .docx | 26,856 | 105,493 | 20.4 M | Docling, hybrid chunker |
| .jpg/.jpeg | 13,969 | 18,053 | 0.8 M | OCR; 58 % are placeholders (section 4.1) |
| .wbk / .dot | 10,982 | 37,111 | 2.9 M | as .doc |
| .htm/.html/.mht | 5,731 | 21,617 | 2.1 M | |
| .rtf | 4,027 | 29,592 | 2.7 M | |
| .xlsx / .xls | 1,674 | 13,389 | 2.2 M | |
| DOS-era text (.as, .ass, .ds, .eb, .do, .dss, .dos, .3, .mtt, .wvb, .txt) | 3,053 | 21,734 | 2.0 M | |
| other (.tif, .png, .bmp, .gif, .eml, .dotx, video placeholders) | 658 | 4,145 | 0.2 M | |

By share: `documents` 207,564 documents / 1.52 M chunks, `akten` 103,317 / 1.20 M,
`texte` 37,473 / 0.24 M, `literatur` 16,759 / 1.35 M.

Where the time went (per-file telemetry events, sum over the up to 16 parallel files, so
slot-hours rather than wall-clock hours):

| stage | slot-hours | share |
|---|--:|--:|
| convert (Docling, OCR, readers) | 2,620 | 58 % |
| embed (waiting for and running the embedder) | 1,842 | 41 % |
| chunk | 10 | 0.2 % |
| save | 13 | 0.3 % |
| OCR repair | 0.1 | 0 % |

By outcome: 365,121 files stored (3,539 slot-hours), 8,603 without text (16 h), 2,053 errors
(20 h) and **2,092 cancelled files that cost 910 slot-hours** (20 % of all slot time): files
in flight at a restart, a hang or a timeout whose work was thrown away. Timeouts alone were
352 attempts and 355 slot-hours.

By type: PDFs took 2,846 slot-hours (1,966 converting, 869 embedding), e-mails 952, `.doc`
455. A `.doc` converts in milliseconds; **450 of its 455 slot-hours were the embedding step**,
13 seconds per file for 4.4 chunks on average. The embedder processed about 1.4 billion
tokens in 372 hours, roughly 1,000 tokens per second on a GPU that measures 13,000-16,000
(`docs/04`), because the llama.cpp server kept sliding into its degraded state (one pinned
CPU core, idle GPU) between restarts.

## 2. Coverage: every file on the shares accounted for

The census of the four shares was reconciled with `documents`, the duplicate records and
`failed_documents` (`q06_coverage.py`):

| files | GB | state |
|--:|--:|---|
| 364,318 | 151.0 | indexed |
| 29,443 | 29.5 | copy of an indexed file (recorded as duplicate) |
| 12,951 | 30.6 | extension not supported |
| 2,535 | 0.05 | sidecar names (`._` AppleDouble, `~$` Office owner files) |
| 2,495 | 2.2 | failed: no text extracted |
| 1,970 | 2.4 | failed: read error |
| 1,687 | 1.0 | failed: image-only PDF without OCR text |
| 705 + 11 | 0.2 | skipped as hang suspects / recorded hangs |
| 199 | 25.2 | audio/video deferred to a media run |
| 5 | 0.02 | failed: timeout (still unresolved and not indexed) |
| 581 | 0.7 | no record: created after the last scan (active matters) |

Nothing is unexplained. Two drift effects are visible three days after the run: **581 new
files** in active matters are not indexed, and **796 indexed documents no longer exist**
under their path (moved, renamed or deleted). No code reconciles the index with the share
and the schedule check has no caller (section 5, backlog).

Not supported and therefore not indexed: 1,882 `.dra` dictation recordings (22.6 GB), 144
`.zip` archives (5.7 GB, not unpacked), 11 `.pst` Outlook archives (0.3 GB), 37 `.odt`, 20
`.ods`, 13 `.epub`, 12 `.heic`, 12 `.opus`, 20 `.dotm`; the rest is application data
(`.dat`, `.db`, `.lnk`, `.tmp`, `.js`, `.css`).

## 3. Validation of the indexed content

### 3.1 Text quality by file type

400 documents per type were sampled and the share of their words found in a reference
vocabulary (32,000 words from 4,000 born-digital `.docx`) measured (`q05_quality.py`).
Median and tenth percentile of that hit rate:

| type | median | p10 | documents below 0.6 |
|---|--:|--:|--:|
| .docx | 0.96 | 0.90 | 0.3 % |
| .doc | 0.94 | 0.88 | 0 % |
| .msg | 0.93 | 0.84 | 1 % |
| .rtf | 0.93 | 0.85 | 0 % |
| .pdf | 0.91 | 0.75 | 3 % |
| DOS-era text (.as, .ass, .ds, .eb, .do, .dos, .3) | 0.89-0.94 | 0.81-0.90 | 0-0.3 % |
| .tif | 0.88 | 0.65 | 6 % |
| .jpg / .png with OCR text | 0.81-0.84 | 0.38-0.58 | 4-8 % |
| .xlsx / .xls | 0.80-0.84 | 0.59-0.64 | 5-10 % (numbers and codes, not garbage) |

Umlauts are present in 96-99 % of the sampled documents of every text type, including the
DOS-era files (the cp437 decoding works); mojibake appeared in 13 of 5,600 samples. The low
scores are handwriting, plans, forms, foreign-language pages and photographed screens.

### 3.2 Scanned versus born-digital PDFs

300 indexed PDFs were opened with pdfium inside the worker container (`probe_in_container.py`):
194 born-digital, 92 scans without a text layer (31 %), 14 mixed.

| class | indexed words per page, p10 / median / p90 |
|---|---|
| born-digital | 83 / 178 / 363 |
| scan (OCR) | 83 / 196 / 372 |
| mixed | 63 / 125 / 219 |

The OCR path yields as much text per page as the text layer of born-digital files. One of
92 scans is below 30 words per page (a photo exhibit). For born-digital files the indexed
text is 0.86 of the text layer's length at the median (p10 0.74; page furniture is off);
2 of 193 are below 0.5.

### 3.3 E-mails

150 indexed `.msg` files were opened with `extract_msg` and compared with their documents:
the header block (From, To, Date) is present in 150 of 150; the indexed text is 0.91 of the
body's length at the median (1 of 150 below 0.7); 73 carry real attachments (112 files:
70 PDF, 14 signatures, Word, images, Excel) and for 50 of the 73 the document is clearly
longer than the body, so the attachment text is in. The same attachment is usually also a
file of its own in the matter's `Attachements` folder, so its text is indexed twice
(section 4.4). Two of 73 sampled `.eml` files are Outlook containers under the wrong
name; their binary bytes were indexed as text (fixed, section 6).

### 3.4 Structure of the index

- Every sampled chunk (30,000) has a 768-d embedding, a parent document and content; no orphans.
- Chunk size: median 331 tokens, p95 895, 0.4 % above the 1,024 budget, none above the embedder's 2,048.
- **44 % of all chunks come from the fallback chunker** (`simple_fallback`): every `.msg`,
  `.doc`, `.rtf`, `.wbk`, `.txt` and DOS-era file. It cuts 1,000-character windows without
  headings; an e-mail's header is only in its first chunk.
- Chunk and document metadata carry the path, a size, the ingestion date and counts. There
  is **no page number, no heading, no file type, no file date and no e-mail field**, and the
  vector index has no filter field: a search cannot be scoped to a matter, a type or a
  period, and an answer cannot cite a page.
- `documents` had only the `_id` index (section 4.6).

## 4. Edge cases, skipped files and errors

### 4.1 Placeholders stored as documents (8,702)

8,624 documents consist of the line `[Image: <name> - OCR extracted minimal text]`, 35 of
`[Video: <name> - video transcription not yet supported]`, 42 start with `[Error`, one is an
audio placeholder. They were chunked and embedded. 58 % of the indexed `.jpg` documents are
such placeholders (logos, signatures, photos without text: median 4.9 megapixels). The
pipeline knew only `[Error:`, `[Skipped:` and `[Document:` as placeholders, and the purge
command only the first two.

### 4.2 Read errors (1,980) by cause

Sampled 400 failure records and re-read 76 files with the live reader, then with the fixed
readers (`repro_in_container.py`):

| cause | share of the error records | after the fix |
|---|---|---|
| `~$` Office owner files (162-byte lock files) read as documents | 724 (500 `.doc`, 209 `.docx`, 15 templates) | skipped at discovery |
| `.docx` whose zip member names use backslashes (written by the firm's practice-management software: letters, fee notes, cost schedules); Docling: "could not load document" | about 500 of 508 | read (6 of 6 samples) |
| small Word 6/95 `.doc`/`.dot`/`.wbk` (`antiword`: "text stream too small to handle"; 431 of the 570 are under 12 kB) | about 490 of 570 | read by a built-in reader (9 of 10 samples) |
| `.doc` that is a `.docx` container, RTF or plain text | about 80 of 570 | read by content (8 of 8 samples) |
| `.msg` without a property stream (56), not an OLE2 file (14), other | 90 | open (backlog) |
| PDFs Docling cannot load (about three quarters password-protected, the rest damaged) | 30 | cause now recorded |
| conversion worker died (very large PDFs, up to 8,782 pages / 304 MB; 4 e-mails) | 20 | open |
| legacy `.ppt` (needs LibreOffice), `.pptx`, encrypted `.xls`, `.xlsx` | 23 | cause now recorded |
| audio that could not be transcribed | 16 | media run (PR #38) |
| **three law commentaries (12-30 MB of text) rejected by MongoDB after 4-5.5 hours each** ("BSON document too large") | 3 | content capped, chunks kept |

The error message was "Could not read file <name>" or "legacy .doc format requires antiword"
(with `antiword` installed) in 1,845 of the 1,980 cases, without the cause.

### 4.3 No text extracted (2,496) and image-only PDFs (1,687)

- 1,393 images (photos, logos) and 186 empty HTML fragments: correct, there is no text.
- 406 PDFs: 41 of 60 sampled have a text layer (plans, court delivery notices). The live
  build now reads 7 of 8 re-tested ones (2,900-28,000 characters): they failed on an older
  build and the running retry picks them up.
- 229 `.docx`: 53 of 60 sampled contain only pasted pictures (photographed exhibits,
  tickets, screenshots). Fixed: the pictures are OCRed (4 of 5 samples yield text).
- Image-only PDFs: 1,657 of the 1,687 records date from 2026-10-26 to 10-28. All 80 sampled
  are real scans of 1-15 pages. The live build reads 11 of 14 re-tested ones (the other 3
  are photos). The running retry does not include this group (`retry_image_only_pdfs` off).

### 4.4 Copies

11,314 content hashes are stored more than once (16,526 surplus documents, about 200,000
surplus chunks, 4.7 % of the index). The guard against same-run copies arrived on
2026-10-24; non-incremental paths and half-finished saves can still add copies, and there is
no unique index. Search collapsed copies only on the identifier path.

### 4.5 Documents without chunks (50) and a save that was not atomic

While the embedder was degraded on 2026-11-01, 38 e-mails and 12 Word files were stored as
documents with zero chunks: the embedder dropped every chunk it could not embed and the
document was saved anyway, counted as ingested and skipped by later runs. The save also
deleted the previous version of a file before inserting the new one.

### 4.6 Failure records

136 of the 155 open `timeout` records belong to files a later attempt indexed: only a retry
inside the same run resolved a record. Every failed retry added another row. The job
document's final counters describe the last run segment only (failed 332, success rate
0.19) for a job with 394,540 files.

## 5. Inefficient processes and stages

| finding | effect measured | state |
|---|---|---|
| The job document carried the API runner's 50,000 log lines (13.5 MB) and is rewritten on every progress update | During the retry run of 2026-11-11: about 100 queued updates, the oldest waiting 23 minutes, 47,000 write conflicts, MongoDB at about 10 CPU cores, hybrid search 4.6 s server-side (30 s gateway timeouts). After removing the lines: no queue, MongoDB below one core, hybrid 0.6 s, semantic 0.07 s, text 0.04 s | fixed in code (2,000-line cap, trim on claim); live document trimmed (lines archived to `C:\RecallHub-Verify\logs\`) |
| `documents`, `ingestion_stats`, `failed_documents` had no index besides `_id` | every stored file scanned `documents` (0.8 s) for its previous version; identifier queries and the failure pages scanned too | fixed in code (indexes created when a run starts); created on the live database: lookup 7 ms |
| Every search pipeline joined the whole parent document (megabytes of `content`) per hit | 13 ms instead of 7 ms for 40 hits with a warm cache; more under memory pressure | fixed (title, source, hash only) |
| Embedder degradation between restarts | embedding was 41 % of all slot time; average throughput about 7 % of the GPU's capacity | open: the watchdogs restart it, the cause in llama.cpp is not removed (backlog 1) |
| 2,092 cancelled files, 910 slot-hours | restarts, hangs and timeouts threw away in-flight work; 32 resumes | partly addressed earlier (hang fix, 4-hour budget); checkpointing of long conversions is open |
| Three commentaries converted and embedded for 13 hours in total, then rejected at the insert; a retry repeats it | 13 slot-hours per attempt | fixed (content cap) |
| Resume re-reads and re-hashes every copy over the network (218,165 duplicate records for 29,443 copies) | about 15 minutes per resume | open (backlog: record copies as `locations` on the original) |
| Vector stage looked at 100 candidates for any limit | the vector stage alone finds 39 % of the written questions, 54 % with 600 candidates (+160 ms) | fixed (20 x limit) |
| A chat request that exceeds the 300 s limit is not cancelled; chat and ingestion share the GPU | after three timeouts no chat request completed while the retry run was embedding (section 7.5) | open (backlog) |
| Configured `embedding_batch_size` is never passed to the embedder | setting without effect | open |

## 6. Improvement cycle 1 (this PR)

Ingestion:

1. **Placeholders are never stored** (`src/ingestion/placeholders.py`): image, audio, video and
   empty-e-mail placeholders are classified like the error and empty ones; the purge
   (`quellexctl ingest purge-placeholders`) covers them and records `no_chunks` or `error`.
2. **Discovery skips `~$` Office owner files** in the pipeline and in the API's file list.
3. **Readers go by content and report the cause**: `.doc` files that are `.docx` containers,
   RTF, HTML or text go to those readers; `antiword`'s reason is in the error; a built-in
   reader handles the Word 6/95 files `antiword` rejects; `.eml` files that are Outlook
   containers go to the `.msg` reader; packages with backslash member names are converted
   from a repaired copy; picture-only Word files are OCRed
   (`src/ingestion/office_fallbacks.py`); password-protected PDFs are named as such; the
   raw-text fallback applies to text formats only.
4. **A stored document is whole**: `documents.content` is capped at 8 MB
   (`INGEST_MAX_DOCUMENT_CONTENT_MB`) while all chunks are stored; the new version is written
   before the old one is deleted and a half-written one is removed; a document none of whose
   chunks could be embedded is a failed file (`EmbeddingFailedError`), chunks the embedder
   rejects go to the embedding backlog.
5. **Indexes** for the queries of the pipeline and the document pages are created when a run
   starts and when a database is created (`src/ingestion/indexes.py`).
6. **Failure records**: a file that is indexed resolves its open records of every type and
   run; a repeated failure updates its row.
7. **Job document**: at most 2,000 log lines; a claimed job is trimmed first.

Search:

8. The join to `documents` reads title, source and content hash only (search, chat, identifier
   and legacy tool pipelines).
9. Ordinary hybrid queries fetch three times the requested results per index with
   `numCandidates = 20 x limit`, **collapse copies** into one result with
   `metadata.locations`, and **drop stored placeholders** (`backend/services/search_results.py`).
10. **On a tie in the fusion the text hit precedes a vector-only hit** (`fused_order`), in the
    search endpoint and in the chat path's fusions: rank 1 for 59 % instead of 21 % of exact
    sentences and 51 % instead of 28 % of written questions on the benchmark (section 7.4).

Maintenance: `quellexctl ingest tidy-failures` resolves failure records of files that are
indexed and merges repeated records (3,041 of 10,003 open records on this database).

Benchmark harness: `scripts/retrieval-benchmark/retrieval_benchmark.py` and
`chat_benchmark.py` (section 7).

Verified on the real files: the fixed readers were run inside the worker container against
the sampled failures (read-only): 6 of 6 backslash packages, 9 of 10 small Word 6 files,
8 of 8 misnamed `.doc` files and 4 of 5 picture-only Word files now yield text.

Applied to the live system on 2026-11-11 (no deployment, no restart): the job document's
stale log lines were archived and removed, and the ordinary indexes were created.

Not applied, for the operator to decide (each changes stored data):

- purge of the 8,702 placeholder documents (`quellexctl ingest purge-placeholders --profile diejuristin`, dry run first);
- removal of the 50 documents without chunks so the next run ingests them again;
- a retry run of the image-only PDFs, and, after this build is deployed, of the read errors
  (about 1,070 Word files become readable, 724 lock-file records disappear).

## 7. Benchmark: complex prompts, false negatives and false positives

### 7.1 The benchmark

`scripts/retrieval-benchmark/` builds the cases from the live index, so the ground truth is
known without labelling by hand:

- **Known-item probes** (false negatives): for 130 documents of 14 file classes (PDF
  born-digital / scan / mixed, e-mail, `.doc`, `.docx`, `.rtf`, Excel, images, HTML, DOS-era
  text, templates, `.eml`) one sentence of the document is the query. The expected result is
  every document that contains the sentence (copies included; sentences that occur in more
  than eight documents are skipped as boilerplate). Each probe has variants: the exact
  sentence (130), umlauts transliterated and lower-cased (88), four to five keywords in
  shuffled order (128), two typing errors (128), and **a question the local model wrote
  about the passage without reusing its wording** (106).
- **Identifiers**: 12 court file numbers, 12 Firmenbuch numbers and 12 Grundbuch entries
  found in the corpus, and 12 matter codes (folder names).
- **Negatives** (false positives): 16 invented identifiers verified absent, 8 invented
  party names, 8 off-topic questions; 15 generic one-word queries for result-list noise.
- **Chat prompts**: 14 single-document questions, 5 identifier questions, 10 questions that
  need two documents of the same matter (written by the local model from both passages,
  with a reference answer), 4 folder listings ("which fee notes exist in matter X"), and 12
  prompts that must be declined (invented parties, false premises about a real matter,
  off-topic).

678 retrieval cases were sent to the hybrid, semantic and text endpoints of the live
application (1,774 queries, tag `baseline-20261111`), the 45 chat prompts through the
sessions API the UI uses. The deployed build is main at `997c413`, without this PR.

### 7.2 Retrieval baseline

Share of probes whose document is at rank 1 / in the top 10, and the mean reciprocal rank:

| query | hybrid | text only | semantic only |
|---|---|---|---|
| exact sentence | 0.22 / 0.89 / 0.50 | 0.79 / 0.94 / 0.84 | 0.12 / 0.30 / 0.18 |
| transliterated | 0.23 / 0.85 / 0.48 | 0.72 / 0.89 / 0.77 | 0.11 / 0.25 / 0.15 |
| keywords | 0.10 / 0.70 / 0.34 | 0.52 / 0.77 / 0.59 | 0.03 / 0.11 / 0.05 |
| typing errors | 0.16 / 0.89 / 0.47 | not run | not run |
| written question | 0.33 / 0.79 / 0.51 | 0.54 / 0.76 / 0.61 | 0.26 / 0.43 / 0.30 |

By file class (hybrid, document in the top 10; exact sentence / written question):
`.doc` 1.00 / 0.75, `.docx` 0.90 / 0.88, e-mail `.msg` 0.60 / 0.88, `.eml` 1.00 / 0.62, RTF
1.00 / 0.88, Excel 1.00 / 1.00, HTML 1.00 / 0.50, DOS-era text 0.90 / 1.00, templates 0.90 /
0.88, images (OCR) 0.80 / 0.62, PDF (random sample) 0.90 / 0.71, PDF born-digital 0.80 / 0.88, **PDF scans 0.80 / 0.75**, PDF
mixed 0.86 / 0.86. Scanned documents are found as well as born-digital ones. 6 of 130 exact
sentences were found by none of the three endpoints (3 e-mails, 2 PDFs, 1 scan): short,
generic sentences.

Identifiers (hybrid): court file numbers, Firmenbuch numbers and Grundbuch entries are in
the top 10 for 12 of 12 each, at rank 1 for 9, 10 and 11. **Matter codes are found for 4 of
12**: a folder name is searchable only when it also occurs in the text.

Latency (server side, after the job document was trimmed): text 37 ms, semantic 70 ms,
hybrid 0.97 s at the median (p90 2.4 s); identifier queries 1.2 s (p90 4.7 s).

### 7.3 What the baseline shows

False negatives:

- **The fusion hides what the text stage finds.** Text search alone ranks the right document
  first for 79 % of exact sentences and 54 % of written questions; the hybrid result does so
  for 22 % and 33 %. Reciprocal rank fusion scores rank *i* of both stages alike, the fusion
  listed the vector stage first, and every tie went to the vector hit.
- **The vector stage is weak on this index**: 43 % of written questions and 30 % of exact
  sentences have their document in the semantic top 10. `numCandidates` was 100 for any
  limit; with 600 the vector stage alone finds 54 % of the questions instead of 39 % (section 7.4).
- Matter codes (above); transliteration and typing errors cost little (the fuzzy text stage
  absorbs them).

False positives:

- **Nothing is ever declined by the search**: all 32 negative queries return ten results on
  every endpoint. The vector score cannot separate them: negatives score 0.80-0.91, real
  questions 0.87-0.91 (a floor of 0.86 would still answer 20 of 32 negatives and lose 5 of
  106 real questions). The text score separates better: a floor of 12 on the top BM25 score
  rejects 21 of 32 negatives and no real question.
- **Copies crowd the result list**: 448 of 642 hybrid result lists contain the same text more
  than once (1.45 repeats on average). Fixed by the copy collapse of this PR for identical
  files; near-copies (the attachment inside its e-mail) remain.
- **Fragments**: 250 of 642 hybrid result lists contain a chunk under 60 characters; for
  one-word queries ("Klage", "Rechnung", "Protokoll") 4.7 of the ten results are such
  fragments. The chat path demotes short chunks, the search endpoint does not.

### 7.4 Fusion and candidate window: what-if on the same queries

`fusion_lab.py` ran the vector and the text stage directly against MongoDB for 210 of the
probes, cached the ranked lists and scored ranking variants offline (rank 1 / top 10 / MRR):

| ranking | exact sentence | keywords | written question |
|---|---|---|---|
| deployed (100 candidates, vector wins ties) | 0.21 / 0.89 / 0.50 | 0.12 / 0.73 / 0.37 | 0.28 / 0.77 / 0.47 |
| 600 candidates, 30 per stage, placeholders dropped | 0.28 / 0.88 / 0.52 | 0.15 / 0.72 / 0.37 | 0.38 / 0.79 / 0.54 |
| **+ the text hit wins a tie (this PR)** | **0.59 / 0.88 / 0.68** | **0.50 / 0.73 / 0.57** | **0.51 / 0.79 / 0.61** |
| + text weight 1.5 | 0.60 / 0.93 / 0.70 | 0.50 / 0.77 / 0.59 | 0.54 / 0.77 / 0.62 |
| steeper text ranks (k 10 for text, 60 for vector) | 0.79 / 0.95 / 0.84 | 0.54 / 0.78 / 0.62 | 0.49 / 0.79 / 0.59 |
| text only | 0.79 / 0.95 / 0.84 | 0.54 / 0.78 / 0.62 | 0.51 / 0.77 / 0.59 |
| vector only, 100 / 300 / 600 candidates (top 10) | 0.32 / 0.33 / 0.37 | 0.14 / 0.14 / 0.15 | 0.39 / 0.48 / 0.54 |

The deployed row reproduces the live baseline. The tie-break alone doubles to quadruples the
rank-1 rate at an unchanged top-10 rate and keeps equal weights, so half of the window still
belongs to the vector stage. The text-heavier variants gain more on quotes but push
vector-only hits out of the top ten; these probes are lexical by construction (even the
written questions name the parties and amounts), so that step is left to a reranker with a
benchmark that also has conceptual questions (backlog). Median stage time measured directly:
vector 106 ms with 100 candidates, 184 ms with 300, 262 ms with 600; text 71 ms.

Shipped in this PR: `fused_order` (the text hit precedes a vector-only hit on a tie) in the
search endpoint and in the chat path's fusions, and `numCandidates = 20 x limit` for
ordinary queries.

### 7.5 Chat baseline

The chat run was made while the retry ingestion was running and was stopped after 27 of
the 45 prompts, because it stopped producing answers (see the last point):

| prompts | completed | result |
|---|---|---|
| single-document questions | 14 of 14 | answered in 47 s at the median, none declined; the document that holds the answer is among the first six cited sources for 12 |
| identifier questions | 5 of 5 | all five answers deal with the identifier asked for (50 s) |
| two-document questions | 4 of 7 attempted | 3 ended at the application's 300 s limit (HTTP 504); of the 4 answers the local-model judge accepted 2, and one of the two gold documents was among the first six sources for 1 |
| folder listing | 0 of 1 attempted | 300 s limit |
| must be declined (12) | not run | |

- Single-fact questions work: the right document is retrieved and cited.
- Questions that need two documents are the weak class: the second document is rarely
  retrieved, and the multi-step run is slow.
- **Chat and ingestion compete for the GPU.** During the run the GPU was at 99 %, 15.5 of
  16 GB and 90 °C (embedder and language model together); a single model call took about
  50 s, so a request with several steps exceeded the 300 s limit. **A request that times out
  is not cancelled**: its agent run goes on and occupies the model, and the next requests
  queue behind it. After three timeouts no further prompt completed.

The must-decline prompts (false positives in answers) therefore have no baseline yet. To run
when no ingestion is active:
`chat_benchmark.py run --kinds unanswerable,folder_listing,multi_document --tag <tag>`.

### 7.6 Running it again

```
uv run python scripts/retrieval-benchmark/retrieval_benchmark.py generate --out <dir> --db <database> --questions 8
uv run python scripts/retrieval-benchmark/retrieval_benchmark.py run --out <dir> --key-file <api key file> --tag <tag>
uv run python scripts/retrieval-benchmark/chat_benchmark.py generate --out <dir> --db <database>
uv run python scripts/retrieval-benchmark/chat_benchmark.py run --out <dir> --key-file <api key file> --tag <tag>
uv run python scripts/retrieval-benchmark/fusion_lab.py collect --out <dir> --db <database>
uv run python scripts/retrieval-benchmark/fusion_lab.py score --out <dir>
```

The same `cases.jsonl` can be run again after a deployment (`--tag`) and compared with
`baseline-20261111`. The search endpoints allow 30 requests per minute and client; the
runner paces itself. The cases quote the corpus and must stay outside the repository.

### 7.7 After the deployment (2026-11-12)

The retry run finished on 2026-11-12 at 00:08 (29,865 files looked at, 29,476 already
indexed, 341 documents added, 332 failed again). Cycle 1 was deployed at 18:04 with no job
running; the same 678 cases and 45 chat prompts were then run against the live application
(tags `cycle1-20261112`, `cycle1-20261112-a`, `-b`).

Hybrid search, rank 1 / top 10 / mean reciprocal rank:

| query | before | after |
|---|---|---|
| exact sentence (130) | 0.22 / 0.88 / 0.50 | **0.62** / 0.88 / 0.70 |
| transliterated (88) | 0.23 / 0.85 / 0.48 | **0.58** / 0.85 / 0.67 |
| keywords (129) | 0.10 / 0.70 / 0.34 | **0.43** / 0.73 / 0.52 |
| typing errors (130) | 0.15 / 0.88 / 0.46 | **0.64** / 0.90 / 0.71 |
| written question (106) | 0.33 / 0.78 / 0.50 | **0.54** / 0.79 / 0.62 |
| matter codes (12) | 0.00 / 0.33 / 0.08 | 0.17 / 0.33 / 0.20 |
| court file, Firmenbuch, Grundbuch numbers (36) | 0.83 / 1.00 | 0.83 / 1.00 |

- Of the 583 known-item queries 284 rank their document higher than before, 32 lower, 267
  the same; 10 entered the top ten, 2 left it. The live result confirms the offline
  what-if of section 7.4.
- The semantic endpoint finds 47 % of the written questions in its top ten instead of 42 %
  (`numCandidates` 200 instead of 100); the text endpoint is unchanged, as expected.
- Server time at the median / 90th percentile: hybrid 337 ms / 770 ms (baseline 1.3 s / 4.6 s,
  measured while the job document was still being rewritten), semantic 72 / 81 ms, text
  24 / 49 ms, identifier queries 367 / 484 ms.
- Result lists with the same text more than once: 374 of 645 instead of 448 of 642, and
  1.12 repeats per list instead of 1.45. What remains are near-copies with different files
  (the attachment inside its e-mail, the same clause in two contracts). No list contains a
  placeholder.
- **Unchanged, as these were not part of cycle 1**: all 32 negative queries still return ten
  results, 267 of 645 lists contain a fragment under 60 characters, matter codes are in the
  top ten for 4 of 12.
- **One query fails, before and after**: a written question of 26 words around `§ 23 UrhG`
  gets HTTP 400 "Query too long". The statute text stage sent the rest of the question as
  one unbounded fuzzy clause (more than Lucene's 1,024 clauses), and the retry without fuzzy
  matching rebuilt the same stage. Fixed after the deployment (`remainder_clause` in
  `backend/services/identifier_search.py`); against the live index the bounded stage answers
  in 125 ms with the expected document first.

Chat, with no ingestion running (gemma4:12b on the GPU, 43-45 s per answer at the median,
65 s at most, no request near the 300 s limit):

| prompts | result |
|---|---|
| single-document questions (14) | 14 answered; the document that holds the answer is among the cited sources for 13 (baseline 12) |
| identifier questions (5) | all five answers deal with the identifier asked for |
| two-document questions (10) | 10 answered, the local-model judge accepts 4; one of the two documents is cited for 1, both for none |
| folder listings (4) | 4 answered, none complete: of the 22 documents to list, 4 are among the sources |
| must be declined (12) | **9 declined; 3 answered anyway**; all 12 cite sources |

- The three false positives (one invented party, two false premises about a real matter)
  all state that the documents do not contain the answer and then fill the answer with
  unrelated material from the retrieved chunks; the invented party's "report" is described
  as if it existed. A prompt that is declined still lists ten sources.
- Questions that need two documents and folder listings are the weak classes: the second
  document is not retrieved, and a listing needs a query by path, not a similarity search.
- The timeouts of the baseline run were caused by the ingestion running next to the chat.

Failure list after the retry run (dry run of `quellexctl ingest tidy-failures`): 11,489 open
records, of which 465 belong to files that are in the index now and 4,076 repeat a later
attempt; 6,948 remain (2,280 no text, 1,968 read errors, 1,742 image-only PDFs, 731 suspected
hangs, 199 deferred media, 17 timeouts, 11 hangs). The retry ran on the build before cycle 1,
so the read errors that cycle 1 repairs are still in that list.

## 8. Backlog for the next cycles

Ranked by value for the firm. "Re-index" means rebuilding a search index, "re-embed"
computing embeddings again.

Ingestion:

| # | item | evidence | effort | data impact |
|---|---|---|---|---|
| 1 | Remove the cause of the embedder degradation (llama.cpp server pins one core and leaves the GPU idle between restarts): newer server image, slot and batch settings, a 24-hour throughput test | 41 % of slot time, about 7 % of GPU capacity used | M | none |
| 2 | Keep the index in step with the shares: scheduled incremental run (the schedule check has no caller) and a guarded reconciliation that removes documents whose file is gone (only when every share was listed and is not empty; a move is a path update) | 581 new files and 796 stale documents after three days | M | deletes stale documents |
| 3 | Structure for the text formats: e-mails, `.doc`, `.rtf` and legacy text through the hybrid chunker; page, heading, type, file date and e-mail fields on chunks; filter fields in the vector index | 44 % of chunks without structure; no scoped search, no page citations | L | re-chunk and re-embed about 1.9 M chunks from stored content; vector index rebuild |
| 4 | Copies as `locations` on the original, clean-up of the surplus copies, unique index on the content hash | 16,526 surplus documents, 200,000 chunks; 15 minutes of re-hashing per resume | M | deletes copies (dry run first) |
| 5 | Job summary from `ingestion_stats` at completion (counters survive a resume) | final counters describe the last segment only | S | none |
| 6 | Remaining formats: `.msg` without property stream (56), legacy `.ppt` via LibreOffice, `.zip` archives (144, 5.7 GB), `.pst` (11), `.odt`/`.ods`, `.heic`, `.opus`; `.dra` dictations (1,882, 22.6 GB) in the media run | section 2 and 4.2 | S-M each | none |
| 7 | Long conversions survive a restart (page ranges or checkpoints) | 2,092 cancelled files, 910 slot-hours | L | none |
| 8 | Pass the configured embedding batch size; bounded task queue instead of one task per file up front; fallback embedder off during ingestion (its vectors would be stored under the primary model's name) | code review | S | none |

Retrieval and answers:

| # | item | evidence | effort | data impact |
|---|---|---|---|---|
| 1 | No-answer control in chat: gate on raw scores with a calibrated floor (the quality gates run on scores normalised to 0.5-1.0, so nothing is ever filtered), never skip the evaluation step, grounding rule in the synthesis prompt, lower temperature | section 7.3; a BM25 floor of 12 rejects 21 of 32 negatives and no real question | S-M | none |
| 1a | Cancel the agent run when its request times out; keep chat responsive during ingestion (model and embedder share one GPU) | section 7.5 | S-M | none |
| 2 | German analyzer for the text leg (`content.de` with `lucene.german`) and searchable title/path | compounds and inflections beyond two edits miss; matter codes are only found when they are in the body | M | text index rebuild |
| 3 | Reranker over the fused top 50 (its score doubles as the no-answer floor) | hit@10 is far above hit@1 | M | none |
| 4 | Citations with path and page; the UI shows the cited sources | sources are titles; the title is often a file stem | S-M | page numbers need item 3 of the ingestion list |
| 5 | Identifier coverage: `FBN`, administrative file numbers, matter codes as path queries; fewer false triggers (addresses read as court references) | code review | S | none |
| 6 | Scoped search by matter, type and period | no filter today | M-L | vector index rebuild |
| 7 | Wire or remove the Strategy OS retrieve node (it has no search backend and returns nothing) | code review | S | none |
